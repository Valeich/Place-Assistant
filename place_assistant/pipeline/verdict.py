"""A plain verdict and a short proof for each place in a product survey.

The proof is built by code from the evidence and checks already collected,
never by a model, so it can only repeat what the survey actually saw:

    Available              'Listed as "AQUA 600ml" at Rp 8.000 on the place's own menu (satekhas.com).'
    Likely available       'A Google review mentions it: "...aqua dingin...".'
    Needs field check      'Not yet mentioned on its website, in its 5 Google reviews or in online listings ...'
    Couldn't check fully   'Its menu is a linked file that can't be opened automatically ...'

"Needs field check" replaces a bare "not found": it says what was checked
and that a call or visit settles it, without calling the result negative.
"""

import urllib.parse

from . import prices, product_check

VERDICTS = {
    product_check.FOUND_WITH_PRICE: "Available",
    product_check.FOUND_NO_PRICE: "Available",
    product_check.MENTIONED_IN_REVIEWS: "Likely available",
    product_check.NO_EVIDENCE: "Needs field check",
    product_check.INCONCLUSIVE: "Couldn't check fully",
    "place_not_found": "Place not found",
    "not_checked": "Skipped",
}

_CONFIRM = "Confirm by phone or visit."


def _site(url: str | None) -> str:
    """The host of url without "www.", e.g. "satekhas.com"."""
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _short(text: str, limit: int = 110) -> str:
    """text on one line, cut at a word boundary to about limit characters."""
    flat = " ".join((text or "").split()).strip("…").strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0] + "…"


def _join(parts: list[str]) -> str:
    """["a", "b", "c"] -> "a, b or c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]


def _sources(record: dict) -> str:
    """Up to two source sites cited by the Google Search check."""
    titles = []
    for source in record.get("third_party_sources") or []:
        title = (source.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return ", ".join(titles[:2])


def best_menu_evidence(record: dict) -> dict | None:
    """The menu or listing evidence that best shows the product: a priced
    item before an unpriced one, the product alone before a bundle, and the
    place's own menu before an online listing. None when there is none.
    """
    menu = [e for e in record.get("evidence") or [] if e["source"] in ("official_website", "third_party_search")]
    ranked = sorted(menu, key=lambda e: (not e.get("price"), prices.is_bundle(e["item"]), e["source"] != "official_website"))
    return ranked[0] if ranked else None


def _available(record: dict) -> str:
    menu = [e for e in record.get("evidence") or [] if e["source"] in ("official_website", "third_party_search")]
    best = best_menu_evidence(record)
    if best["source"] == "official_website":
        site = _site(best.get("url") or record.get("website"))
        where = f"on the place's own menu ({site})" if site else "on the place's own menu"
    else:
        sources = _sources(record)
        where = f"in an online listing ({sources})" if sources else "in an online listing found through Google Search"
    if best.get("price"):
        proof = f'Listed as "{best["item"]}" at {best["price"]} {where}.'
    else:
        proof = f'Listed as "{best["item"]}" {where}, without a price.'
    more = len(menu) - 1
    if more:
        proof += f" {more} more matching item{'s' if more > 1 else ''} in the report."
    if best["source"] == "third_party_search":
        proof += " Online listings can be out of date."
    if not best.get("price") and "menu not read" in str((record.get("checks") or {}).get("website", "")):
        proof += " Its full menu couldn't be opened and may show the price."
    return proof


def _needs_field_check(record: dict) -> str:
    checks = record.get("checks") or {}
    checked, notes = [], []

    website = str(checks.get("website", ""))
    if website.startswith("no_match"):
        checked.append("on its website")
    elif website == "no_website":
        notes.append("It lists no website.")
    elif website == "social_media_only":
        notes.append("It uses a social media page as its website.")
    elif website == "blocked_by_robots":
        notes.append("Its website restricts automated access.")
    elif website == "broken_link":
        notes.append("Its website link is broken.")
    elif website == "not_allowed":
        notes.append("Its website link leads to a delivery app or Google page.")

    reviews = record.get("reviews_checked") or 0
    if reviews:
        checked.append(f"in its {reviews} Google review{'s' if reviews > 1 else ''}")

    if str(checks.get("third_party_search", "")) in ("no_match", "no_sources"):
        checked.append("in online listings such as delivery apps")

    first = f"Not yet mentioned {_join(checked)}." if checked else "It has no online menu, listing or review to check yet."
    return " ".join([first, *notes, "A call or visit will confirm whether it's stocked."])


def _inconclusive(record: dict) -> str:
    checks = {k: str(v) for k, v in (record.get("checks") or {}).items()}
    if any(v.startswith("menu_not_readable") for v in checks.values()):
        reason = "Its menu is a linked file or page that can't be opened automatically; the link is in the report."
    elif checks.get("website") == "fetch_failed":
        reason = "Its website couldn't be loaded."
    elif checks.get("place_details", "").startswith("error") or checks.get("find_place", "").startswith("error"):
        reason = "Google Maps details for this place couldn't be loaded."
    else:
        failed = [name.replace("_", " ") for name, value in checks.items() if value.startswith("error")]
        reason = f"The {_join(failed)} check failed." if failed else "One of the checks couldn't finish."
    return f"{reason} {_CONFIRM}"


def explain(record: dict) -> dict:
    """{"verdict", "proof"} for one product-survey record, from its own
    status, evidence and checks only.
    """
    status = record.get("status")
    if status in (product_check.FOUND_WITH_PRICE, product_check.FOUND_NO_PRICE):
        proof = _available(record)
    elif status == product_check.MENTIONED_IN_REVIEWS:
        quote = _short(next((e.get("quote") for e in record.get("evidence") or [] if e.get("quote")), ""))
        proof = f'A Google review mentions it: "{quote}". Reviews can be old. {_CONFIRM}'
    elif status == product_check.NO_EVIDENCE:
        proof = _needs_field_check(record)
    elif status == product_check.INCONCLUSIVE:
        proof = _inconclusive(record)
    elif status == "place_not_found":
        proof = f'Google Maps has no match for "{record.get("searched_name", "")}" in this area. Try adding a street or mall name.'
    elif status == "not_checked":
        proof = "The survey was stopped before this place was checked."
    else:
        proof = ""
    return {"verdict": VERDICTS.get(status, status), "proof": proof}
