"""Best-effort menu and price extraction for one place, in two stages:

1. The place's own public website, through site_reader: the listed page,
   up to two menu sub-pages it links to, and PDF menus.
2. If the site has no menu or prices, a Google Search-grounded lookup checks
   delivery platforms (GoFood, GrabFood, ShopeeFood, DoorDash, Uber Eats)
   and food blogs. This mirrors rule 1b in place_assistant/prompt.py.

No compliant API offers itemized menus with prices: Google Places, Foursquare,
GoFood and GrabFood all lack one. Each stage's LLM call extracts only what is
literally in its input text and never invents items.
"""

import json

from google.genai import types

from .. import config
from ..shared.llm import get_client
from . import site_reader

_MAX_MENU_TEXT_CHARS = 30000  # all pages combined, to bound the extraction prompt

_MENU_ITEMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"item": {"type": "string"}, "price": {"type": "string"}},
        "required": ["item", "price"],
    },
}

_DELIVERY_PLATFORMS = "GoFood, GrabFood, ShopeeFood, DoorDash, Uber Eats"


def grounding_sources(response) -> list[dict]:
    """Returns [{"title", "uri"}, ...] for the web pages a Search-grounded
    response cited. Empty when the answer wasn't grounded in any page.
    """
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
    except (AttributeError, IndexError, TypeError):
        return []
    return [{"title": c.web.title, "uri": c.web.uri} for c in chunks if getattr(c, "web", None)]


def _parse_items_from_text(source_text: str) -> list[dict]:
    """Structures free-text menu mentions into [{"item", "price"}, ...] via a
    no-tools, schema-forced call. Shared by both extraction stages below —
    never invents items beyond what's literally in source_text.
    """
    prompt = f"""\
Extract any menu items and prices that are EXPLICITLY present in the text
below. Do not invent, estimate, or infer items or prices that aren't
literally there. If no menu items or prices appear, return an empty array.

Text:
{source_text}
"""
    response = get_client().models.generate_content(
        model=config.PIPELINE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_MENU_ITEMS_SCHEMA,
        ),
    )
    try:
        items = json.loads(response.text)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _extract_from_website(website_url: str | None) -> tuple[list[dict], str, list[str]]:
    """Returns (items, website_status, page_urls) from the place's own site.

    website_status comes from site_reader.read_business_site, or is
    "no_menu_text" / "error: ..." when the pages were read but extraction
    found nothing or failed, or "menu_not_readable: <links>" when the site
    links its menu on a host this project never fetches, e.g. Google Drive.
    """
    site = site_reader.read_business_site(website_url)
    if site["status"] != "ok":
        return [], site["status"], []
    page_urls = [p["url"] for p in site["pages"]]
    text = "\n\n".join(
        f"[Page: {p['url']}]\n{p['text'][:site_reader.MAX_PAGE_CHARS]}" for p in site["pages"]
    )[:_MAX_MENU_TEXT_CHARS]
    try:
        items = _parse_items_from_text(text)
    except Exception as exc:
        return [], f"error: {type(exc).__name__}: {exc}"[:300], page_urls
    if not items and site.get("menu_links_not_read"):
        return [], "menu_not_readable: " + " ".join(site["menu_links_not_read"]), page_urls
    return items, ("ok" if items else "no_menu_text"), page_urls


def _extract_via_search(place_name: str, area_name: str) -> tuple[list[dict], list[dict], str]:
    """Falls back to a Google Search-grounded lookup for delivery-platform or
    food-blog menu listings. Returns (items, sources, status).

    response_mime_type=application/json isn't reliable combined with tools
    on the same call, so this is two Gemini calls: grounded free text here,
    then _parse_items_from_text structures it with no tools attached. An
    answer without any cited source is discarded, since it may come from
    the model's memory.
    """
    search_prompt = f"""\
Search for published menu items and prices for "{place_name}" in
{area_name}. Look specifically at delivery platforms ({_DELIVERY_PLATFORMS})
and food blogs/review sites — not the business's own website. Only report
items and prices that are explicitly published in a source you find; if you
can't find any published menu or price information for this specific place,
say so plainly rather than guessing.
"""
    try:
        response = get_client().models.generate_content(
            model=config.PIPELINE_MODEL,
            contents=search_prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        grounded_text = (response.text or "").strip()
        sources = grounding_sources(response)
        if not grounded_text or not sources:
            return [], [], "no_sources"
        items = _parse_items_from_text(grounded_text)
    except Exception as exc:
        return [], [], f"error: {type(exc).__name__}: {exc}"[:300]
    return items, sources, ("ok" if items else "nothing_found")


def extract_menu(website_url: str | None, place_name: str, area_name: str) -> dict:
    """Best-effort menu/price lookup for one place: tries the place's own
    website first, then falls back to a Search-grounded delivery-platform/
    food-blog lookup if the site had nothing.

    Returns {"items": [{"item", "price"}, ...], "source": str, "note": str|None,
    "website_status": str, "search_status": str|None, "source_urls": [...],
    "sources": [{"title", "uri"}, ...]}. source is one of "official_website",
    "third_party_search", or "unavailable". A "third_party_search" result
    always carries a note flagging that the prices are second-hand and may
    be outdated — relay that caveat whenever this data is surfaced to a user.
    website_status and search_status say why a stage found nothing, so a
    failed fetch isn't mistaken for "no menu".
    """
    items, website_status, page_urls = _extract_from_website(website_url)
    if items:
        return {
            "items": items,
            "source": "official_website",
            "note": None,
            "website_status": website_status,
            "search_status": None,
            "source_urls": page_urls,
            "sources": [],
        }

    items, sources, search_status = _extract_via_search(place_name, area_name)
    if items:
        return {
            "items": items,
            "source": "third_party_search",
            "note": (
                "Found via delivery-platform/web search, not the business's "
                "own site — prices may be outdated."
            ),
            "website_status": website_status,
            "search_status": search_status,
            "source_urls": [],
            "sources": sources,
        }

    return {
        "items": [],
        "source": "unavailable",
        "note": f"Website: {website_status}. Delivery/blog search: {search_status}.",
        "website_status": website_status,
        "search_status": search_status,
        "source_urls": [],
        "sources": [],
    }
