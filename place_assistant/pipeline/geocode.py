"""Resolves a named area (e.g. "Jakarta Pusat, Indonesia") to a lat/lng bounding
box via the Geocoding API, so Places Text Search can be restricted to it
instead of guessed/hardcoded coordinates.
"""

import math

import requests

from .. import config

# Neighbourhood names such as "Kemang, Jakarta" often geocode to a point with
# a viewport only a few hundred metres wide, which holds two or three places.
# Smaller boxes are widened to this size around their centre.
MIN_BOX_KM = 2.0
_KM_PER_DEGREE_LAT = 111.32


def _widen(box: dict, min_km: float = MIN_BOX_KM) -> dict:
    """Returns box grown to at least min_km per side around its centre,
    with "widened": True when it had to grow.
    """
    ne, sw = box["northeast"], box["southwest"]
    lat_c, lng_c = (ne["lat"] + sw["lat"]) / 2, (ne["lng"] + sw["lng"]) / 2
    min_half_lat = min_km / 2 / _KM_PER_DEGREE_LAT
    min_half_lng = min_km / 2 / (_KM_PER_DEGREE_LAT * max(math.cos(math.radians(lat_c)), 0.01))
    half_lat, half_lng = (ne["lat"] - sw["lat"]) / 2, (ne["lng"] - sw["lng"]) / 2
    if half_lat >= min_half_lat and half_lng >= min_half_lng:
        return box
    half_lat, half_lng = max(half_lat, min_half_lat), max(half_lng, min_half_lng)
    return {
        "northeast": {"lat": lat_c + half_lat, "lng": lng_c + half_lng},
        "southwest": {"lat": lat_c - half_lat, "lng": lng_c - half_lng},
        "widened": True,
    }


def geocode_area_bounds(area_name: str) -> dict:
    """Returns {"northeast": {"lat", "lng"}, "southwest": {"lat", "lng"}} for area_name.

    A box smaller than MIN_BOX_KM per side is widened around its centre and
    carries "widened": True. Raises ValueError if the area can't be
    geocoded — never silently guesses.
    """
    config.require_maps_api_key()
    try:
        resp = requests.get(
            config.GEOCODE_URL,
            params={"address": area_name, "key": config.GOOGLE_MAPS_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        # The Geocoding API only accepts the key in the URL, and requests puts
        # the URL into its error text. Never pass that text on: agents relay
        # errors word for word.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise RuntimeError(
            f"Geocoding request for '{area_name}' failed ({type(exc).__name__}"
            f"{f', HTTP {status}' if status else ''})."
        ) from None
    data = resp.json()

    if data.get("status") != "OK" or not data.get("results"):
        detail = f" ({data['error_message']})" if data.get("error_message") else ""
        raise ValueError(
            f"Could not geocode '{area_name}': {data.get('status', 'UNKNOWN_ERROR')}{detail}"
        )

    geometry = data["results"][0]["geometry"]
    # `bounds` (full administrative extent) is preferred; `viewport` is a
    # display-oriented fallback for areas without a defined boundary.
    return _widen(geometry.get("bounds") or geometry["viewport"])
