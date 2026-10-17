"""collect_places tool for the area_collector agent.

A thin async wrapper around pipeline.collect_places. It runs the pipeline in
a worker thread, because ADK calls synchronous tools on its event loop and a
collection takes minutes, which would freeze the web UI for everyone. It
turns exceptions into an error dict the agent can relay, and replaces the
full records with a short per-place menu summary so a large run doesn't
flood the chat.

The collect_places docstring is the tool description the model sees, so edit
it with the same care as a prompt.
"""

import asyncio

from ...pipeline import collect_places as run_pipeline

# Cap per-place menu items in the chat summary so a 60-place run doesn't
# blow up the tool response — full, untruncated records still go to the
# JSON/CSV files regardless of this cap.
_MAX_MENU_ITEMS_IN_SUMMARY = 5


def _menu_highlights(records: list[dict]) -> list[dict]:
    """Shrinks full place records to a short menu summary for the chat reply:
    name, menu source and note, and at most _MAX_MENU_ITEMS_IN_SUMMARY items.
    """
    highlights = []
    for record in records:
        menu = record.get("menu") or {}
        items = menu.get("items") or []
        highlights.append(
            {
                "name": record.get("name"),
                "menu_source": menu.get("source", "unavailable"),
                "menu_note": menu.get("note"),
                "items": items[:_MAX_MENU_ITEMS_IN_SUMMARY],
                "items_truncated": len(items) > _MAX_MENU_ITEMS_IN_SUMMARY,
            }
        )
    return highlights


async def collect_places(
    area_name: str, place_type: str, limit: int = 20, require_alcohol: bool = False
) -> dict:
    """Collects structured place data for a named area and place type, and
    writes it to JSON/CSV.

    Pulls from Google Places API (address, rating, reviews, hours, price
    level, facilities, alcohol service), Foursquare tips (supplementary
    reviews, if configured), OpenStreetMap (a free facilities cross-check —
    cuisine, hours, wifi, wheelchair access, website, phone), and
    best-effort menu extraction from each place's own website — then
    summarizes review sentiment per place with an LLM. Use this tool
    whenever the user asks to collect, gather, scrape, or export place data
    for a geographic area (e.g. "collect all cafes in Jakarta Pusat", "bars
    that serve alcohol around Menteng").

    Args:
        area_name: A geographic area name Google can geocode, e.g. "Jakarta
            Pusat, Indonesia". Include the country/city if it could be
            ambiguous.
        place_type: A single Google Place type matching what the user asked
            for, e.g. "cafe", "restaurant", "bar", "bakery", "museum",
            "tourist_attraction". Infer this from the user's wording (e.g.
            "coffee shops" -> "cafe", "tourist spots" -> "tourist_attraction")
            — see
            https://developers.google.com/maps/documentation/places/web-service/place-types
            for the full type list. If genuinely unclear, ask the user rather
            than guessing.
        limit: Maximum number of places to fully enrich, 1 to 60 (each one
            costs a Place Details call plus an LLM call for sentiment). A
            deployment may cap it lower; the result's limit_note then says so.
            Google's Text Search itself returns at most 60 candidates per
            area; keep this modest for a first run given per-place API costs.
        require_alcohol: Set True when the user specifically wants only
            places that serve alcohol (beer/wine/cocktails). This is a
            POST-hoc filter applied after fetching each candidate's details
            — `limit` candidates are still checked and billed for even if
            few pass, so the result can come back smaller than `limit`. Tell
            the user "checked" vs "collected" from the tool's result rather
            than assuming they're equal.

    Returns:
        dict: on success, {"area", "place_type", "require_alcohol",
        "checked", "collected", "capped_at_60", "json_path", "csv_path",
        "docx_path", "menu_highlights"}. capped_at_60 means Google's search
        ceiling was hit and the area may have more matching places than were
        collected. docx_path is a Word document with a readable per-place
        report (address, rating, facilities, sentiment, and a menu/price
        table) — mention it when the user wants something shareable/printable
        or asks for "Google Docs": this is a local .docx file, not a live
        Google Docs API integration, but it opens directly in Google Docs via
        File > Open, or by dragging it into Google Drive. menu_highlights is
        a per-place list of {"name", "menu_source", "menu_note", "items",
        "items_truncated"} — menu_source is "official_website" (found on the
        place's own site), "third_party_search" (found via a delivery-platform/
        food-blog search fallback — relay menu_note's staleness caveat
        whenever you surface these), or "unavailable" (neither source had
        anything). items is capped at 5 per place for this summary;
        items_truncated=True means the full list is longer — point the user
        to json_path/csv_path/docx_path for the rest. Full, untruncated
        records (reviews, sentiment, facilities, etc.) are written to the
        output files rather than returned inline, since a run can be large.
        On failure (bad area name, API key/billing not set up, invalid place
        type), {"status": "error", "error_message":
        str} — relay this message to the user rather than guessing why it
        failed.

    Note: there is no way to filter by when a place opened (e.g. "opened in
    the last 6 months") — no Google API exposes an opened/established date
    for existing businesses. If asked for this, say so plainly rather than
    guessing or silently ignoring the request.
    """
    try:
        result = await asyncio.to_thread(
            run_pipeline, area_name, place_type=place_type, limit=limit, require_alcohol=require_alcohol
        )
    except Exception as exc:
        return {"status": "error", "error_message": str(exc)}
    records = result.pop("records", None) or []
    result["menu_highlights"] = _menu_highlights(records)
    return result
