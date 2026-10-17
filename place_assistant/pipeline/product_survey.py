"""Product distribution surveys: is a given product sold at the restaurants,
cafes or bars of an area, or at a list of named places, and at what price?

Area survey:
    geocode area
    -> Google Maps Text Search for "<product> <type> in <area>". These hits
       come first, because they are the places Google Maps itself associates
       with the product, for example through menus or reviews.
    -> if fewer than `limit`, fill up with a plain "<type> in <area>" search
    -> skip permanently closed places
    -> per place, in parallel: Place Details, then product_check.check_product
    -> summary, then JSON / CSV / DOCX in .output/

Named places: each name is matched with one Text Search call, then checked
the same way. Without a product, the place's full menu is extracted instead
(menu_extractor), which is the reliable way to get one restaurant's prices.

A failure on one place is recorded on that place and never stops the run,
so places already checked and billed are always saved.

Both entry points accept two optional hooks, used by the web UI:
- progress(event): called with {"stage": ...} dicts as the run advances,
  including one "place_done" event per finished place.
- cancel: a threading.Event. Once set, places not yet started are skipped
  and saved with status "not_checked"; places already running finish.
"""

import concurrent.futures
import datetime
import threading
from typing import Callable

from .. import config
from . import export, geocode, menu_extractor, places_client, prices, product_check, verdict

MAX_PLACE_TYPES = 3
MAX_NAMED_PLACES = 30
_WORKERS = 4  # places checked at once

NOT_CHECKED = "not_checked"

Progress = Callable[[dict], None] | None


def _report(progress: Progress, **event) -> None:
    """Sends one progress event, never letting a broken listener stop the run."""
    if progress:
        try:
            progress(event)
        except Exception:
            pass


def _spec(product: str, aliases, description: str) -> product_check.ProductSpec:
    """Builds a ProductSpec, raising ValueError when the product name is
    empty or too short to search for.
    """
    product = " ".join((product or "").split())
    if not product:
        raise ValueError("A product name is required.")
    spec = product_check.ProductSpec(
        name=product,
        aliases=tuple(a for a in (aliases or []) if a and a.strip()),
        description=(description or "").strip(),
    )
    if not spec.terms:
        raise ValueError("The product name must be at least 2 characters long.")
    return spec


def _place_info(details: dict) -> dict:
    """The fields of a Place Details response that surveys keep."""
    return {
        "place_id": details.get("id"),
        "name": details.get("displayName", {}).get("text"),
        "address": details.get("formattedAddress"),
        "phone": details.get("nationalPhoneNumber"),
        "google_maps_url": details.get("googleMapsUri"),
        "website": details.get("websiteUri"),
        "rating": details.get("rating"),
        "total_reviews": details.get("userRatingCount"),
        "business_status": details.get("businessStatus"),
    }


def _reviews(details: dict) -> list[str]:
    """The review texts of a Place Details response."""
    return [r["text"]["text"] for r in details.get("reviews", []) if r.get("text", {}).get("text")]


def _check_one(place_id: str, spec: product_check.ProductSpec, area_name: str, search_third_party: bool) -> dict:
    """Details plus product check for one place. Never raises."""
    try:
        details = places_client.get_place_details(place_id, places_client.SURVEY_FIELD_MASK)
    except Exception as exc:
        return {
            "place_id": place_id,
            "status": product_check.INCONCLUSIVE,
            "evidence": [],
            "checks": {"place_details": f"error: {type(exc).__name__}: {exc}"[:300]},
            "third_party_sources": [],
        }
    record = _place_info(details)
    try:
        record.update(
            product_check.check_product(
                {**record, "reviews": _reviews(details)}, spec, area_name, search_third_party
            )
        )
    except Exception as exc:
        record.update(
            status=product_check.INCONCLUSIVE,
            evidence=[],
            checks={"product_check": f"error: {type(exc).__name__}: {exc}"[:300]},
            third_party_sources=[],
        )
    return record


def _menu_one(place_id: str, area_name: str) -> dict:
    """Details plus full menu extraction for one place. Never raises."""
    try:
        details = places_client.get_place_details(place_id, places_client.SURVEY_FIELD_MASK)
        record = _place_info(details)
        record["menu"] = menu_extractor.extract_menu(record["website"], record["name"] or "", area_name)
    except Exception as exc:
        return {
            "place_id": place_id,
            "status": "inconclusive",
            "menu": {"items": [], "source": "unavailable", "note": f"error: {type(exc).__name__}: {exc}"[:300]},
        }
    record["status"] = "menu_found" if record["menu"]["items"] else "menu_unavailable"
    return record


def _parallel(
    func, items: list, *args, progress: Progress = None, cancel: threading.Event | None = None
) -> list[dict]:
    """Runs func(item, *args) for every item on a small thread pool and
    returns the results in the input order.

    Reports a "place_done" event as each item finishes. Items that haven't
    started when cancel is set return {"status": NOT_CHECKED, "_item": item}.
    """

    def run(item):
        """Checks one item, or skips it when the run was stopped."""
        if cancel is not None and cancel.is_set():
            return {
                "status": NOT_CHECKED,
                "checks": {"run": "stopped before this place was checked"},
                "_item": item,
            }
        return func(item, *args)

    results: list[dict] = [{} for _ in items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="survey") as pool:
        futures = {pool.submit(run, item): i for i, item in enumerate(items)}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            i = futures[future]
            results[i] = future.result()
            _report(
                progress,
                stage="place_done",
                index=i,
                done=done,
                total=len(items),
                name=results[i].get("name") or results[i].get("searched_name") or results[i].get("_item"),
                status=results[i].get("status"),
            )
    return results


def _product_summary(records: list[dict], spec: product_check.ProductSpec) -> dict:
    """Counts places per status and summarizes the menu and listing prices found."""
    counts = {status: 0 for status in product_check.STATUSES}
    counts["place_not_found"] = 0
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return {
        "kind": "product_survey",
        "product": spec.name,
        "aliases": [t for t in spec.terms if t.casefold() != spec.name.casefold()],
        "product_description": spec.description,
        "checked": sum(1 for r in records if r["status"] not in ("place_not_found", NOT_CHECKED)),
        "status_counts": counts,
        "found": counts[product_check.FOUND_WITH_PRICE] + counts[product_check.FOUND_NO_PRICE],
        "price_summary": prices.summarize_prices(prices.survey_price_entries(records)),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def _save(survey: dict, *name_parts: str, title: str) -> dict:
    """Writes the survey's JSON, CSV and DOCX to .output/. Returns the paths."""
    return export.save_survey(survey, config.OUTPUT_DIR, export.safe_file_stem(*name_parts), title)


def survey_product(
    area_name: str,
    product: str,
    place_types: list[str] | None = None,
    aliases: list[str] | None = None,
    product_description: str = "",
    limit: int = 20,
    search_third_party: bool = True,
    progress: Progress = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Surveys up to `limit` places of the given types in an area for one
    product. Returns {"summary": {...}, "places": [...], "json_path",
    "csv_path", "docx_path"}. See the module docstring for the steps and
    for progress and cancel.
    """
    spec = _spec(product, aliases, product_description)
    place_types = [t.strip() for t in (place_types or ["restaurant"]) if t and t.strip()][:MAX_PLACE_TYPES]
    asked = int(limit)
    limit = max(1, min(asked, places_client.MAX_TEXT_SEARCH_RESULTS, config.MAX_PLACES_PER_RUN))
    config.require_maps_api_key()
    _report(progress, stage="geocoding", area=area_name)
    bounds = geocode.geocode_area_bounds(area_name)
    _report(progress, stage="searching", widened=bool(bounds.get("widened")))

    candidates: dict[str, dict] = {}
    closed: set[str] = set()  # a closed place can come back from several searches

    def add(results: list[dict], maps_product_match: bool) -> None:
        """Adds search results as candidates, up to limit, skipping duplicates
        and permanently closed places.
        """
        for place in results:
            if place["id"] in candidates or len(candidates) >= limit:
                continue
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                closed.add(place["id"])
                continue
            candidates[place["id"]] = {
                "maps_product_match": maps_product_match,
                "name": place.get("displayName", {}).get("text"),
                "address": place.get("formattedAddress"),
            }

    for place_type in place_types:
        words = place_type.replace("_", " ")
        add(places_client.search_places_text(f"{spec.name} {words} in {area_name}", bounds, place_type, limit), True)
    maps_product_hits = len(candidates)
    for place_type in place_types:
        if len(candidates) < limit:
            words = place_type.replace("_", " ")
            add(places_client.search_places_text(f"{words} in {area_name}", bounds, place_type, limit), False)

    ids = list(candidates)
    _report(progress, stage="checking", total=len(ids), names=[candidates[i]["name"] for i in ids])
    records = _parallel(_check_one, ids, spec, area_name, search_third_party, progress=progress, cancel=cancel)
    for place_id, record in zip(ids, records):
        record.pop("_item", None)
        record.setdefault("place_id", place_id)
        record["maps_product_match"] = candidates[place_id]["maps_product_match"]
        # If Place Details failed, keep the name and address from the search.
        record.setdefault("name", candidates[place_id]["name"])
        record.setdefault("address", candidates[place_id]["address"])

    for record in records:
        record.update(verdict.explain(record))
    summary = _product_summary(records, spec)
    summary.update(
        area=area_name,
        place_types=place_types,
        maps_product_hits=maps_product_hits,
        skipped_closed=len(closed),
    )
    if config.limit_note(asked):
        summary["limit_note"] = config.limit_note(asked)
    if bounds.get("widened"):
        summary["area_note"] = (
            f"Google Maps gave '{area_name}' only a very small area, so the search "
            f"was widened to about {geocode.MIN_BOX_KM:g} km around its centre and "
            "may include places just outside it."
        )
    if cancel is not None and cancel.is_set():
        summary["stopped_early"] = True
    survey = {"summary": summary, "places": records}
    _report(progress, stage="saving")
    paths = _save(
        survey, area_name, "product", spec.name, *place_types,
        title=f"{spec.name}: availability in {area_name}",
    )
    return {**survey, **paths}


def check_named_places(
    place_names: list[str],
    area_name: str,
    product: str = "",
    aliases: list[str] | None = None,
    product_description: str = "",
    search_third_party: bool = True,
    progress: Progress = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Checks a list of named places. With a product, collects evidence that
    each place sells it; without one, extracts each place's full menu with
    prices. Each name is matched to its top Google Maps result, whose name
    and address are returned so a wrong match is visible. Returns the same
    shape as survey_product, and takes the same progress and cancel hooks.
    """
    names = [n.strip() for n in place_names or [] if n and n.strip()]
    if not names:
        raise ValueError("At least one place name is required.")
    if len(names) > MAX_NAMED_PLACES:
        raise ValueError(f"At most {MAX_NAMED_PLACES} places per call; split the list.")
    if len(names) > config.MAX_PLACES_PER_RUN:
        raise ValueError(
            f"This deployment checks at most {config.MAX_PLACES_PER_RUN} places per run; "
            f"{len(names)} were given. Shorten the list."
        )
    config.require_maps_api_key()
    spec = _spec(product, aliases, product_description) if (product or "").strip() else None

    def lookup(name: str) -> dict:
        """Matches one name on Google Maps, then checks it. Never raises."""
        try:
            match = places_client.find_place(name, area_name)
        except Exception as exc:
            return {"searched_name": name, "status": "inconclusive", "checks": {"find_place": f"error: {exc}"[:300]}}
        if not match:
            return {"searched_name": name, "status": "place_not_found"}
        if spec:
            record = _check_one(match["id"], spec, area_name, search_third_party)
        else:
            record = _menu_one(match["id"], area_name)
        return {"searched_name": name, **record}

    _report(progress, stage="checking", total=len(names), names=names)
    records = _parallel(lookup, names, progress=progress, cancel=cancel)
    for name, record in zip(names, records):
        if record.pop("_item", None) is not None:
            record["searched_name"] = name
    if spec:
        for record in records:
            record.update(verdict.explain(record))
        summary = _product_summary(records, spec)
    else:
        counts = {"menu_found": 0, "menu_unavailable": 0, "place_not_found": 0, "inconclusive": 0}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        summary = {
            "kind": "menu_check",
            "checked": sum(1 for r in records if r["status"] not in ("place_not_found", NOT_CHECKED)),
            "status_counts": counts,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    summary.update(area=area_name, place_names=names)
    if cancel is not None and cancel.is_set():
        summary["stopped_early"] = True
    survey = {"summary": summary, "places": records}
    _report(progress, stage="saving")
    label = spec.name if spec else "menus"
    paths = _save(
        survey, area_name, "places", label, names[0], f"{len(names)}" if len(names) > 1 else "",
        title=f"{spec.name} at selected places" if spec else f"Menus: {', '.join(names[:3])}{'…' if len(names) > 3 else ''}",
    )
    return {**survey, **paths}
