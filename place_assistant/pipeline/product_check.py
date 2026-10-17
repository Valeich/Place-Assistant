"""Checks one place for evidence that it sells a given product, and at what
price. Used by product_survey.py for every surveyed place.

Evidence sources, cheapest first:
1. Google reviews from Place Details (Google returns at most 5).
2. The place's own website, its menu sub-pages and PDF menus (site_reader).
3. A Google Search-grounded lookup of delivery platforms and food blogs.
   It runs only when the website didn't already show the product with a
   price, because each grounded search is billed.

Each source is first searched for the product's names with plain text
matching, so no LLM call is made when no name appears. When one does,
Gemini decides whether each mention really is the product, for example
Aqua the bottled water versus an "aqua blue" cocktail, and copies the item name
and price. The code then keeps a price only if it literally appears in the
source text, and an item only if its name contains one of the product's
names and it appears in the source, or comes with a verbatim price.

"No evidence" is not proof that a place doesn't stock the product. It means
nothing published online that this check could read mentions it.
"""

import json
import re
from dataclasses import dataclass

from google.genai import types

from .. import config
from ..shared.llm import get_client
from . import prices, site_reader
from .menu_extractor import grounding_sources

_SNIPPET_RADIUS = 250  # characters kept on each side of a name match
_MAX_SNIPPETS = 15  # per source, to bound the confirmation prompt
_QUOTE_RADIUS = 120

# Statuses, strongest evidence first.
FOUND_WITH_PRICE = "found_with_price"
FOUND_NO_PRICE = "found_no_price"
MENTIONED_IN_REVIEWS = "mentioned_in_reviews"
NO_EVIDENCE = "no_evidence"
INCONCLUSIVE = "inconclusive"
STATUSES = (FOUND_WITH_PRICE, FOUND_NO_PRICE, MENTIONED_IN_REVIEWS, NO_EVIDENCE, INCONCLUSIVE)

_MENU_SOURCES = ("official_website", "third_party_search")


# Category words people add to a brand name that menus usually leave out:
# "Aqua mineral water" is listed as "Aqua". Only stripped from the ends of a
# multi-word name, so "Teh Botol Sosro" stays whole.
_GENERIC_SUFFIXES = (
    "beer", "beers", "lager", "pilsner", "wine", "coffee", "tea", "water",
    "juice", "soda", "drink", "drinks", "brand", "mineral",
)
_GENERIC_PREFIXES = ("bir", "air")

# Words that name a category, not a brand. An alias made only of these, such
# as "air mineral" or "mineral water", would match every brand on a menu, so
# it is dropped. The product name itself is always kept.
_GENERIC_WORDS = frozenset(_GENERIC_SUFFIXES + _GENERIC_PREFIXES + (
    "bottled", "kopi", "jus", "minuman", "beverage", "beverages", "soft",
))


def _is_generic(term: str) -> bool:
    """True if every word of term is a category word, e.g. "Air Mineral"."""
    return all(word.casefold() in _GENERIC_WORDS for word in term.split())


def _core_name(term: str) -> str:
    """term without leading/trailing generic category words, e.g.
    "Aqua mineral water" -> "Aqua", "Bir Bintang" -> "Bintang".
    """
    words = term.split()
    while len(words) > 1 and words[-1].casefold() in _GENERIC_SUFFIXES:
        words.pop()
    while len(words) > 1 and words[0].casefold() in _GENERIC_PREFIXES:
        words.pop(0)
    return " ".join(words)


@dataclass(frozen=True)
class ProductSpec:
    """The product being surveyed and the names it may appear under."""

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""

    @property
    def terms(self) -> tuple[str, ...]:
        """The name and aliases plus their core names without category
        words, de-duplicated, longest first. Aliases made only of category
        words ("air mineral") are dropped, and so are aliases and core names
        under 3 characters. The product name is kept from 2 characters up.
        """
        seen = {}
        for i, term in enumerate((self.name, *self.aliases)):
            term = " ".join((term or "").split())
            for variant in (term, _core_name(term)):
                is_name = i == 0 and variant == term
                if len(variant) < (2 if is_name else 3) or variant.casefold() in seen:
                    continue
                if _is_generic(variant) and not (i == 0 and variant == term):
                    continue
                seen[variant.casefold()] = variant
        return tuple(sorted(seen.values(), key=len, reverse=True))

    def pattern(self) -> re.Pattern:
        """Case-insensitive whole-word pattern matching any of the terms, or
        nothing when there are no terms.
        """
        parts = [r"\s+".join(map(re.escape, t.split())) for t in self.terms]
        if not parts:
            return re.compile(r"(?!)")
        return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I)


def _norm(text: str) -> str:
    """Collapses whitespace and case, for comparing quoted text with its source."""
    return " ".join((text or "").split()).casefold()


def find_snippets(text: str, spec: ProductSpec) -> list[str]:
    """Returns text windows around every match of the product's names,
    with overlapping windows merged. Empty when no name appears.
    """
    spans = []
    for m in spec.pattern().finditer(text or ""):
        start, end = max(0, m.start() - _SNIPPET_RADIUS), min(len(text), m.end() + _SNIPPET_RADIUS)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return [text[s:e].strip() for s, e in spans[:_MAX_SNIPPETS]]


_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "snippet_id": {"type": "integer"},
                    "item": {"type": "string"},
                    "price": {"type": "string"},
                },
                "required": ["snippet_id", "item", "price"],
            },
        }
    },
    "required": ["matches"],
}

_CONTEXT_RULES = {
    "menu": (
        "a restaurant's own website or menu",
        "Return one entry for every menu item in a snippet that IS this "
        "product, or a size or variant of it. item is the item name as "
        "written. price is the price printed for that item, copied exactly, "
        'or "" if the snippet shows no price for it.',
    ),
    "listing": (
        "a web search summary of delivery-app listings and food blogs for one restaurant",
        "Return one entry for every item the text says this restaurant lists "
        "or sells that IS this product. Ignore sentences saying it was not "
        "found or is sold elsewhere. item is the item name as written. price "
        'is the price given for it, copied exactly, or "".',
    ),
    "review": (
        "customer reviews of one restaurant",
        "Return one entry for a snippet only if the reviewer says this place "
        "sells or served this product. item is the words naming the product "
        'as written. price is a price the review gives for it, copied exactly, or "".',
    ),
}


def _confirm(spec: ProductSpec, snippets: list[str], context: str) -> list[dict]:
    """Asks Gemini which snippets really refer to the product, then keeps
    only grounded matches: the item must name a product term and appear in
    the snippet (or carry a verbatim price), and a price is kept only when
    it appears verbatim. Returns [{"snippet", "item", "price"}, ...].
    """
    where, rule = _CONTEXT_RULES[context]
    numbered = "\n\n".join(f"[{i}]\n{s}" for i, s in enumerate(snippets))
    prompt = f"""\
Product: {spec.name}
What it is: {spec.description or "not specified"}
Names it may appear under: {", ".join(spec.terms)}

The numbered snippets below come from {where}. Each contains one of those
names, but the word may mean something else, such as another dish, a place
name, a rating or an ordinary word.

{rule}
Return nothing for snippets that don't refer to this product. Never invent
items or prices.

{numbered}
"""
    response = get_client().models.generate_content(
        model=config.PIPELINE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_CONFIRM_SCHEMA,
        ),
    )
    try:
        raw = json.loads(response.text).get("matches", [])
    except Exception:
        return []

    term_pattern = spec.pattern()
    results, seen = [], set()
    for match in raw:
        idx = match.get("snippet_id")
        if not isinstance(idx, int) or not 0 <= idx < len(snippets):
            continue
        snippet = snippets[idx]
        item = " ".join((match.get("item") or "").split()).strip(" ,;:|-–·")
        price = " ".join((match.get("price") or "").split())
        if not term_pattern.search(item):
            continue  # the item must name the product
        if price and _norm(price) not in _norm(snippet):
            price = ""  # only keep prices that literally appear in the source
        if _norm(item) not in _norm(snippet) and not price:
            # A reworded item is kept only when its price is verbatim in the
            # source; otherwise it may be invented.
            continue
        key = (_norm(item), _norm(price))
        if key in seen:
            continue
        seen.add(key)
        results.append({"snippet": snippet, "item": item, "price": price})
    return results


def _quote(snippet: str, item: str) -> str:
    """A short excerpt of snippet centred on item, for the report."""
    flat = " ".join(snippet.split())
    pos = _norm(flat).find(_norm(item))
    if pos < 0:
        return flat[: 2 * _QUOTE_RADIUS]
    start = max(0, pos - _QUOTE_RADIUS)
    excerpt = flat[start : pos + len(item) + _QUOTE_RADIUS]
    return ("…" if start else "") + excerpt + ("…" if pos + len(item) + _QUOTE_RADIUS < len(flat) else "")


def _evidence(match: dict, source: str, url: str | None, default_currency: str | None) -> dict:
    """Builds one evidence record from a confirmed match, with the price parsed
    into a number and currency.
    """
    value, currency = prices.parse_price(match["price"], default_currency)
    return {
        "source": source,
        "item": match["item"],
        "price": match["price"] or None,
        "price_value": value,
        "currency": currency,
        "quote": _quote(match["snippet"], match["item"]),
        "url": url,
    }


def _check_reviews(spec: ProductSpec, reviews: list[str], cur: str | None) -> tuple[list[dict], str]:
    """Checks the place's Google reviews. Returns (evidence, status)."""
    if not reviews:
        return [], "no_reviews"
    snippets = [s for review in reviews for s in find_snippets(review, spec)][:_MAX_SNIPPETS]
    if not snippets:
        return [], "no_match"
    found = [_evidence(m, "google_reviews", None, cur) for m in _confirm(spec, snippets, "review")]
    return found, ("match" if found else "name_found_but_not_the_product")


def _check_website(spec: ProductSpec, website: str | None, cur: str | None) -> tuple[list[dict], str]:
    """Checks the place's own website, menu pages and PDF menus. Returns
    (evidence, status).
    """
    site = site_reader.read_business_site(website)
    if site["status"] != "ok":
        return [], site["status"]
    found = []
    for page in site["pages"]:
        snippets = find_snippets(page["text"], spec)
        if snippets:
            found += [_evidence(m, "official_website", page["url"], cur) for m in _confirm(spec, snippets, "menu")]
    unread = site.get("menu_links_not_read") or []
    if found:
        if unread and not any(e["price"] for e in found):
            # Listed without a price, and a menu page that may hold the price
            # couldn't be read: say so, so a person can check it.
            return found, "match (menu not read: " + " ".join(unread) + ")"
        return found, "match"
    if unread:
        # The site links a menu this project can't read, e.g. a Google Drive PDF.
        return [], "menu_not_readable: " + " ".join(site["menu_links_not_read"])
    return [], f"no_match ({len(site['pages'])} page(s) read)"


_SUMMARY_CHARS = 800


def _check_third_party(
    spec: ProductSpec, place_name: str, address: str, area: str, cur: str | None
) -> tuple[list[dict], str, dict]:
    """Returns (evidence, status, {"sources": [...], "summary": str}) from a
    Search-grounded lookup of delivery apps and food blogs. An answer that
    cites no source is discarded, since it may come from model memory.
    """
    what = f"{spec.name} ({spec.description})" if spec.description else spec.name
    prompt = f"""\
Find out whether the restaurant "{place_name}" at {address or area} sells {what}.
Check its menu listings on food delivery platforms (GoFood, GrabFood,
ShopeeFood, DoorDash, Uber Eats) and on food blogs or review sites, not its
own website. Names it may be listed under: {", ".join(spec.terms)}.

If you find it, list every matching menu item exactly as published, with its
price exactly as published and the source of each. Only report listings for
this specific restaurant and branch. If no published listing shows
{spec.name} at this restaurant, say "NOT FOUND".
"""
    response = get_client().models.generate_content(
        model=config.PIPELINE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    text = (response.text or "").strip()
    sources = grounding_sources(response)
    if not text or not sources:
        return [], "no_sources", {"sources": [], "summary": text[:_SUMMARY_CHARS]}
    search = {"sources": sources, "summary": text[:_SUMMARY_CHARS]}
    snippets = find_snippets(text, spec)
    if not snippets:
        return [], "no_match", search
    found = [_evidence(m, "third_party_search", None, cur) for m in _confirm(spec, snippets, "listing")]
    return found, ("match" if found else "no_match"), search


def _dedupe(evidence: list[dict]) -> list[dict]:
    """Drops repeats of the same item and price from the same source, such
    as "Aqua 7.000" and "Aqua 7.000 IDR", or one item read on two pages,
    and an unpriced mention of an item the same source lists with a price.
    """
    priced = {(e["source"], _norm(e["item"])) for e in evidence if e.get("price")}
    kept, seen = [], set()
    for e in evidence:
        if not e.get("price") and (e["source"], _norm(e["item"])) in priced:
            continue
        price = e["price_value"] if e.get("price_value") is not None else _norm(e.get("price") or "")
        key = (e["source"], _norm(e["item"]), price)
        if key not in seen:
            seen.add(key)
            kept.append(e)
    return kept


def _run(check, *args):
    """Runs one evidence check, turning an exception into an "error: ..."
    status so one failing source never hides the others.
    """
    try:
        return check(*args)
    except Exception as exc:
        status = f"error: {type(exc).__name__}: {exc}"[:300]
        return ([], status, {"sources": [], "summary": ""}) if check is _check_third_party else ([], status)


def classify(evidence: list[dict], checks: dict) -> str:
    """Turns the evidence and per-source check results into one status."""
    menu = [e for e in evidence if e["source"] in _MENU_SOURCES]
    if any(e["price"] for e in menu):
        return FOUND_WITH_PRICE
    if menu:
        return FOUND_NO_PRICE
    if evidence:
        return MENTIONED_IN_REVIEWS
    # A source that exists but couldn't be read leaves the answer open.
    # Permanent gaps such as "no_website" or "social_media_only" don't.
    unread = ("error", "fetch_failed", "menu_not_readable")
    if any(str(v).startswith(unread) for v in checks.values()):
        return INCONCLUSIVE
    return NO_EVIDENCE


def check_product(place: dict, spec: ProductSpec, area_name: str, search_third_party: bool = True) -> dict:
    """Collects product evidence for one place.

    place needs "name", "address", "website" and "reviews" (list of review
    texts). Returns {"status", "evidence", "checks", "reviews_checked",
    "third_party_sources", "third_party_summary"}. The summary is the search model's own wording,
    kept so a person can see what the search found.
    checks says, per source, "match", "no_match", why it couldn't be read
    ("no_website", "social_media_only", ...) or "error: ...".
    """
    evidence, checks = [], {}
    search = {"sources": [], "summary": ""}
    # Menus often print bare numbers ("47"), so prices default to the
    # currency of the place's country.
    cur = prices.currency_for_address(place.get("address")) or prices.currency_for_address(area_name)

    found, checks["google_reviews"] = _run(_check_reviews, spec, place.get("reviews") or [], cur)
    evidence += found

    found, checks["website"] = _run(_check_website, spec, place.get("website"), cur)
    evidence += found

    website_has_price = any(e["source"] == "official_website" and e["price"] for e in evidence)
    if website_has_price:
        checks["third_party_search"] = "skipped: website already lists it with a price"
    elif not search_third_party:
        checks["third_party_search"] = "skipped"
    else:
        found, checks["third_party_search"], search = _run(
            _check_third_party, spec, place.get("name", ""), place.get("address", ""), area_name, cur
        )
        evidence += found

    evidence = _dedupe(evidence)
    return {
        "status": classify(evidence, checks),
        "evidence": evidence,
        "checks": checks,
        "reviews_checked": len(place.get("reviews") or []),
        "third_party_sources": search["sources"],
        "third_party_summary": search["summary"],
    }
