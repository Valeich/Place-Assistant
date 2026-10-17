"""Places API (New) REST calls: Text Search to enumerate places in an area,
and Place Details to enrich each one.

Uses `requests` against the New API directly. The `googlemaps` PyPI client
targets the legacy API and its old field names.
"""

import time

import requests

from .. import config

# Google's hard cap for Text Search (New): 3 pages x 20 results per query.
MAX_TEXT_SEARCH_RESULTS = 60

# Reviews and the amenity booleans fall under the priciest "Enterprise +
# Atmosphere" Places SKU, so keep `limit` modest on test runs. Food-and-drink
# fields (servesCoffee, menuForChildren, ...) are simply absent for other
# place types, so requesting them is harmless.
DETAILS_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "rating",
        "userRatingCount",
        "priceLevel",
        "currentOpeningHours",
        "websiteUri",
        "googleMapsUri",
        "reviews",
        "servesCoffee",
        "servesBreakfast",
        "servesVegetarianFood",
        "servesBeer",
        "servesWine",
        "servesCocktails",
        "outdoorSeating",
        "delivery",
        "takeout",
        "dineIn",
        "reservable",
        "restroom",
        "goodForChildren",
        "menuForChildren",
        "accessibilityOptions",
        "parkingOptions",
    ]
)

# For product surveys: identity, links and reviews only, without the amenity
# fields the collector needs. Reviews keep it in the same top SKU.
SURVEY_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "googleMapsUri",
        "websiteUri",
        "businessStatus",
        "rating",
        "userRatingCount",
        "priceLevel",
        "nationalPhoneNumber",
        "reviews",
    ]
)

_SEARCH_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.businessStatus"


def _headers(field_mask: str) -> dict:
    """Builds Places API (New) request headers: API key, field mask and JSON
    content type. Raises a setup error if the Maps API key is missing.
    """
    config.require_maps_api_key()
    return {
        "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": field_mask,
        "Content-Type": "application/json",
    }


def search_places_text(
    query: str, bounds: dict, included_type: str | None, max_results: int = MAX_TEXT_SEARCH_RESULTS
) -> list[dict]:
    """Text Search (New) for places of included_type inside a bounding box,
    following pagination.

    included_type is a single Google Place type such as "cafe", "restaurant",
    "bar", "museum", or "tourist_attraction". Full list:
    https://developers.google.com/maps/documentation/places/web-service/place-types

    None searches every type.

    Returns up to max_results {"id", "displayName", "formattedAddress",
    "location", "businessStatus"} dicts, never more than
    MAX_TEXT_SEARCH_RESULTS. A result of exactly that length means the cap
    was probably hit.
    """
    headers = _headers(_SEARCH_FIELD_MASK)
    body = {
        "textQuery": query,
        "locationRestriction": {
            "rectangle": {
                "low": {
                    "latitude": bounds["southwest"]["lat"],
                    "longitude": bounds["southwest"]["lng"],
                },
                "high": {
                    "latitude": bounds["northeast"]["lat"],
                    "longitude": bounds["northeast"]["lng"],
                },
            }
        },
        "pageSize": 20,
    }
    if included_type:
        body["includedType"] = included_type

    results: list[dict] = []
    page_token = None
    while len(results) < min(max_results, MAX_TEXT_SEARCH_RESULTS):
        if page_token:
            # A next-page token needs a moment before Google accepts it;
            # asking too fast returns INVALID_ARGUMENT.
            time.sleep(2)
            body["pageToken"] = page_token
        resp = requests.post(config.PLACES_TEXT_SEARCH_URL, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        places = data.get("places", [])
        results.extend(places)
        page_token = data.get("nextPageToken")
        if not page_token or not places:
            break

    return results[:max_results]


def get_place_details(place_id: str, field_mask: str = DETAILS_FIELD_MASK) -> dict:
    """Place Details (New) for one place, limited to field_mask."""
    resp = requests.get(
        config.PLACES_DETAILS_URL.format(place_id=place_id),
        headers=_headers(field_mask),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def find_place(name: str, area_name: str) -> dict | None:
    """Returns the top Text Search match for a named place in an area, as
    {"id", "displayName", "formattedAddress", "location", "businessStatus"},
    or None when Google finds nothing. The match can be a different branch
    or a similarly named place, so callers should show its name and address.
    """
    resp = requests.post(
        config.PLACES_TEXT_SEARCH_URL,
        headers=_headers(_SEARCH_FIELD_MASK),
        json={"textQuery": f"{name}, {area_name}", "pageSize": 1},
        timeout=15,
    )
    resp.raise_for_status()
    places = resp.json().get("places", [])
    return places[0] if places else None
