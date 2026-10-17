"""survey_product_in_area and check_named_places tools for the
product_survey agent.

Thin async wrappers around pipeline/product_survey.py. They run the pipeline
in a worker thread, because ADK calls synchronous tools on its event loop
and a survey can take minutes. They turn exceptions into an error dict, and
shrink each place to a short summary so a large run doesn't flood the chat.
Full records always go to the JSON/CSV/DOCX files.

The docstrings below are the tool descriptions the model sees, so edit them
with the same care as a prompt.
"""

import asyncio

from ...pipeline import prices, product_survey

_MAX_FOUND_PER_PLACE = 3
_MAX_MENU_ITEMS_PER_PLACE = 25
_THIRD_PARTY_NOTE = "From a delivery app or food blog, not the place's own site. The price may be outdated."


def _read_as(entry: dict) -> str | None:
    """How a printed price was read, e.g. "60" -> "IDR 60,000", or None
    when it couldn't be read.
    """
    return prices.format_read_as(entry.get("price_value"), entry.get("currency"))


def _compact_product_place(record: dict) -> dict:
    """Shrinks one product-check record to what the chat reply needs: status,
    up to three found items, and checks when nothing was found.
    """
    evidence = record.get("evidence") or []
    menu_evidence = [e for e in evidence if e["source"] != "google_reviews"]
    shown = sorted(menu_evidence, key=lambda e: not e.get("price"))[:_MAX_FOUND_PER_PLACE]
    if not shown:
        shown = evidence[:2]
    place = {
        "name": record.get("name") or record.get("searched_name"),
        "address": record.get("address"),
        "status": record["status"],
        "verdict": record.get("verdict"),
        "proof": record.get("proof"),
        "found": [
            {
                "item": e["item"],
                "price": e.get("price"),
                **({"price_read_as": _read_as(e)} if _read_as(e) else {}),
                "source": e["source"],
                **({"quote": e["quote"]} if e["source"] == "google_reviews" else {}),
                **({"note": _THIRD_PARTY_NOTE} if e["source"] == "third_party_search" else {}),
            }
            for e in shown
        ],
        "more_found_in_files": max(0, len(evidence) - len(shown)),
        "google_maps_url": record.get("google_maps_url"),
    }
    if record.get("searched_name") and record.get("searched_name") != record.get("name"):
        place["searched_name"] = record["searched_name"]
    if "maps_product_match" in record:
        place["maps_product_match"] = record["maps_product_match"]
    if record["status"] in ("found_no_price", "no_evidence", "inconclusive"):
        place["checks"] = record.get("checks")
    if record.get("third_party_sources"):
        place["search_sources"] = [s.get("title") for s in record["third_party_sources"]][:5]
    return place


def _compact_menu_place(record: dict) -> dict:
    """Shrinks one menu-check record to the chat reply's needs: menu source,
    note, and up to _MAX_MENU_ITEMS_PER_PLACE items with read-as prices.
    """
    menu = record.get("menu") or {}
    currency = prices.currency_for_address(record.get("address"))
    items = []
    for entry in menu.get("items") or []:
        value, cur = prices.parse_price(entry.get("price") or "", currency)
        read_as = _read_as({"price_value": value, "currency": cur})
        items.append({**entry, **({"price_read_as": read_as} if read_as else {})})
    place = {
        "name": record.get("name") or record.get("searched_name"),
        "address": record.get("address"),
        "status": record["status"],
        "website": record.get("website"),
        "google_maps_url": record.get("google_maps_url"),
        "menu_source": menu.get("source"),
        "menu_note": menu.get("note"),
        "items": items[:_MAX_MENU_ITEMS_PER_PLACE],
        "items_truncated": len(items) > _MAX_MENU_ITEMS_PER_PLACE,
    }
    if record.get("searched_name") and record.get("searched_name") != record.get("name"):
        place["searched_name"] = record["searched_name"]
    if menu.get("sources"):
        place["search_sources"] = [s.get("title") for s in menu["sources"]][:5]
    return place


def _compact(result: dict) -> dict:
    """Turns a pipeline result into the tool's success response."""
    summary = result["summary"]
    compact = _compact_product_place if summary.get("kind") == "product_survey" else _compact_menu_place
    return {
        "status": "success",
        "summary": summary,
        "places": [compact(r) for r in result["places"]],
        "json_path": result.get("json_path"),
        "csv_path": result.get("csv_path"),
        "docx_path": result.get("docx_path"),
    }


async def survey_product_in_area(
    area_name: str,
    product: str,
    place_types: list[str] | None = None,
    aliases: list[str] | None = None,
    product_description: str = "",
    limit: int = 20,
) -> dict:
    """Surveys the restaurants, cafes or bars of an area for one product: is
    it sold there, and at what price? Use it for area-wide product or
    distribution questions, e.g. "which restaurants in Menteng sell Aqua and
    at what price?" or "is our product available in Kemang cafes?".

    Google Maps is searched for the product in the area first, and those
    places are checked first. Each place is then checked in its Google
    reviews, on its own website (including menu pages and PDF menus) and in
    delivery-app and food-blog listings found through Google Search.

    Args:
        area_name: Area Google can geocode, with city or country when
            ambiguous, e.g. "Menteng, Jakarta, Indonesia".
        product: The brand name as printed on menus, without category
            words, e.g. "Aqua", not "Aqua mineral water".
        place_types: One to three Google place types, e.g. ["bar"],
            ["restaurant", "cafe"]. Defaults to ["restaurant"].
        aliases: Other names the same product appears under on menus, e.g.
            ["Aqua Reflections", "Aqua Botol"]. Never a competitor, and never
            a generic word like "air mineral", which matches every brand.
        product_description: A few words saying what the product is, e.g.
            "Danone bottled mineral water brand", so unrelated uses of the same word are
            ignored.
        limit: Number of places to check, 1 to 60. Every place is billed.
            A deployment may cap it lower; summary.limit_note then says so.

    Returns:
        dict: {"status": "success", "summary", "places", "json_path",
        "csv_path", "docx_path"}. summary has checked, status_counts, found,
        price_summary (currency, count, min, median, max), maps_product_hits
        and skipped_closed. Each place has name, address, status, found
        (item, price as printed, price_read_as such as "IDR 60,000" when
        the printed price is shorthand, source), google_maps_url,
        maps_product_match, and
        checks unless a price was found. status is found_with_price,
        found_no_price, mentioned_in_reviews, no_evidence or inconclusive.
        Each place also has verdict ("Available", "Likely available", "Needs
        field check", "Couldn't check fully") and proof, a sentence built
        from the evidence; give both as written. "Needs field check" never
        means the place doesn't sell the product. On
        failure: {"status": "error", "error_message": str}; relay it as is.
    """
    try:
        result = await asyncio.to_thread(
            product_survey.survey_product,
            area_name, product, place_types, aliases, product_description, limit,
        )
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    return _compact(result)


async def check_named_places(
    place_names: list[str],
    area_name: str,
    product: str = "",
    aliases: list[str] | None = None,
    product_description: str = "",
) -> dict:
    """Checks specific places the user names. With a product, finds out
    whether each place sells it and at what price, e.g. "does Sate Khas
    Senayan Menteng sell Aqua?" or "check these outlets for our product". Without a product,
    returns each place's menu items with prices, e.g. "show me the menu and
    prices at Anomali Coffee Menteng".

    Each name is matched to its top Google Maps result. The matched name and
    address are returned, so say which place was actually checked.

    Args:
        place_names: The places as the user named them, at most 30, or
            fewer when the deployment caps places per run (the error says so).
        area_name: Area or city they are in, e.g. "Jakarta, Indonesia".
        product: The product or brand to check. Leave empty for full menus.
        aliases: Other names the same product appears under on menus.
        product_description: A few words saying what the product is.

    Returns:
        dict: the same shape as survey_product_in_area. Without a product,
        each place instead has status menu_found or menu_unavailable,
        menu_source (official_website, third_party_search or unavailable),
        menu_note, items (item, price as printed, price_read_as) and
        items_truncated. A place Google
        can't find has status place_not_found. On failure:
        {"status": "error", "error_message": str}; relay it as is.
    """
    try:
        result = await asyncio.to_thread(
            product_survey.check_named_places,
            place_names, area_name, product, aliases, product_description,
        )
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    return _compact(result)
