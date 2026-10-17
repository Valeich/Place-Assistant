"""One full collection run for an area and place type:

    geocode area -> Text Search for candidates -> enrich each place
    (details, reviews, menu, sentiment, OSM cross-check) -> filter -> export

Not supported, by design: filtering by when a place opened. No Google API
(Places, Places Insights, Business Profile) exposes an opened or established
date for existing businesses. `openingDate` only exists on not-yet-open
FUTURE_OPENING listings.
"""

import time

from .. import config
from . import export, foursquare_client, geocode, menu_extractor, osm_client, places_client, sentiment


def collect_places(
    area_name: str,
    place_type: str = "cafe",
    limit: int = 20,
    require_alcohol: bool = False,
) -> dict:
    """Runs the full pipeline and returns a run summary.

    place_type: a single Google Place type, e.g. "cafe", "restaurant", "bar",
        "bakery", "museum", "tourist_attraction". Full list:
        https://developers.google.com/maps/documentation/places/web-service/place-types
    limit: how many candidates to enrich, clamped to 1-60 and to
        config.MAX_PLACES_PER_RUN. Each costs a Place Details call plus LLM
        calls.
    require_alcohol: keep only places Google marks with servesBeer,
        servesWine or servesCocktails. Text Search can't filter on these, so
        the filter runs after enrichment: `limit` places are still fetched and
        billed, and "collected" can be smaller than "checked".

    Returns {"area", "place_type", "require_alcohol", "checked", "collected",
    "capped_at_60", "records", "json_path", "csv_path", "docx_path"}, plus
    "limit_note" when the deployment's place cap cut the run short.
    capped_at_60 means Text Search hit Google's 60-result ceiling, so the area
    probably has more matching places. Full coverage would need grid-based
    sub-area tiling, which is not implemented.
    """
    asked = int(limit)
    limit = max(1, min(asked, places_client.MAX_TEXT_SEARCH_RESULTS, config.MAX_PLACES_PER_RUN))
    bounds = geocode.geocode_area_bounds(area_name)

    # Always search up to the full 60-result ceiling, not just `limit`, so
    # capped_at_60 is accurate on runs with limit < 60. Only enrichment is
    # bounded by `limit`.
    candidates = places_client.search_places_text(
        f"{place_type.replace('_', ' ')} in {area_name}",
        bounds,
        included_type=place_type,
        max_results=places_client.MAX_TEXT_SEARCH_RESULTS,
    )
    capped_at_60 = len(candidates) >= places_client.MAX_TEXT_SEARCH_RESULTS

    records = []
    for candidate in candidates[:limit]:
        records.append(_enrich_place(candidate["id"], area_name))
        time.sleep(0.2)  # light pacing so we don't burst API quotas

    checked = len(records)
    if require_alcohol:
        records = [r for r in records if r["alcohol"]["serves_alcohol"]]

    base_name = _output_base_name(area_name, place_type, require_alcohol)
    paths = export.save_records(records, output_dir=config.OUTPUT_DIR, base_name=base_name)
    paths["docx_path"] = export.save_docx(
        records,
        output_dir=config.OUTPUT_DIR,
        base_name=base_name,
        title=f"{place_type.title()}s in {area_name}",
    )

    _print_warnings(area_name, place_type, capped_at_60, require_alcohol, checked, len(records))

    return {
        "area": area_name,
        "place_type": place_type,
        "require_alcohol": require_alcohol,
        "checked": checked,
        "collected": len(records),
        "capped_at_60": capped_at_60,
        "records": records,
        **({"limit_note": config.limit_note(asked)} if config.limit_note(asked) else {}),
        **paths,
    }


def _enrich_place(place_id: str, area_name: str) -> dict:
    """Builds one full record from every data source for a single place."""
    details = places_client.get_place_details(place_id)
    name = details.get("displayName", {}).get("text", "Unknown")
    lat = details.get("location", {}).get("latitude")
    lng = details.get("location", {}).get("longitude")

    google_reviews = [
        review["text"]["text"]
        for review in details.get("reviews", [])
        if review.get("text", {}).get("text")
    ]
    foursquare_tips = foursquare_client.get_tips(name, lat, lng)

    # Menus mostly exist for food and drink types. Other types just come
    # back as "unavailable".
    menu = menu_extractor.extract_menu(details.get("websiteUri"), name, area_name)
    review_sentiment = sentiment.summarize_sentiment(name, google_reviews + foursquare_tips)
    osm_tags = osm_client.get_osm_tags(name, lat, lng)

    serves_beer = details.get("servesBeer")
    serves_wine = details.get("servesWine")
    serves_cocktails = details.get("servesCocktails")

    return {
        "place_id": place_id,
        "name": name,
        "address": details.get("formattedAddress"),
        "rating": details.get("rating"),
        "total_reviews": details.get("userRatingCount"),
        "price_level": details.get("priceLevel"),
        "open_now": details.get("currentOpeningHours", {}).get("openNow"),
        "google_maps_url": details.get("googleMapsUri"),
        "website": details.get("websiteUri"),
        "facilities": {
            "outdoor_seating": details.get("outdoorSeating"),
            "delivery": details.get("delivery"),
            "takeout": details.get("takeout"),
            "dine_in": details.get("dineIn"),
            "reservable": details.get("reservable"),
            "good_for_children": details.get("goodForChildren"),
            "accessibility": details.get("accessibilityOptions", {}),
            "parking": details.get("parkingOptions", {}),
        },
        "alcohol": {
            "serves_beer": serves_beer,
            "serves_wine": serves_wine,
            "serves_cocktails": serves_cocktails,
            "serves_alcohol": bool(serves_beer or serves_wine or serves_cocktails),
        },
        "reviews_sample": {
            "google_reviews": google_reviews,
            "foursquare_tips": foursquare_tips,
        },
        "sentiment": review_sentiment,
        "menu": menu,
        "osm_cross_check": osm_tags or "not found in OpenStreetMap",
    }


def _output_base_name(area_name: str, place_type: str, require_alcohol: bool) -> str:
    """e.g. ("Jakarta Pusat, Indonesia", "cafe", True) -> "jakarta_pusat_indonesia_cafe_alcohol"."""
    return export.safe_file_stem(area_name, place_type, "alcohol" if require_alcohol else "")


def _print_warnings(
    area_name: str, place_type: str, capped_at_60: bool, require_alcohol: bool, checked: int, collected: int
) -> None:
    """Prints console warnings when the 60-result cap was hit, or when the
    alcohol filter kept none of the checked places.
    """
    if capped_at_60:
        print(
            f"[warning] Text Search hit Google's 60-result cap for '{place_type}' in "
            f"'{area_name}' — there may be more matching places than were collected. "
            "Exhaustive coverage of a whole district would need grid-based sub-area "
            "tiling (not implemented)."
        )
    if require_alcohol and checked and not collected:
        print(
            f"[warning] None of the {checked} '{place_type}' places checked in "
            f"'{area_name}' had alcohol service marked in Google's data — this may "
            "under-report real alcohol-serving places whose Google listing simply "
            "doesn't have these fields filled in."
        )
