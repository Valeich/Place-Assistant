"""Free, optional facilities cross-check via OpenStreetMap's public Overpass
API. OSM has no ratings, reviews, or menu/pricing data — this only
supplements the facilities signal already pulled from Google (cuisine,
hours, wifi, outdoor seating, wheelchair access, website, phone), as a
free second opinion in case OSM has something Google's listing lacks.

Best-effort by design: matches by name within a small radius (fuzzy, since
Google's and OSM's spelling of a name can differ slightly), and degrades to
{} on no match, a request failure, or a slow/unavailable Overpass instance —
this must never break the main pipeline.

Respects overpass-api.de's public-instance usage policy: an identifying
User-Agent (required — a spoofed one gets blocked) and no attempt to exceed
its documented ~10,000 requests/day fair-use ceiling for a single client.
"""

import re

import requests

from .. import config

_SEARCH_RADIUS_M = 80


def get_osm_tags(name: str, lat: float | None, lng: float | None) -> dict:
    """Returns a dict of matched OSM tags, or {} if nothing usable is found."""
    if not name or lat is None or lng is None:
        return {}

    escaped_name = re.escape(name)
    query = (
        f'[out:json][timeout:20];\n'
        f'nwr["name"~"{escaped_name}",i](around:{_SEARCH_RADIUS_M},{lat},{lng});\n'
        f"out tags center;"
    )

    try:
        resp = requests.post(
            config.OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": config.USER_AGENT},
            timeout=25,  # Overpass may queue requests up to ~15s under load
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception:
        return {}

    if not elements:
        return {}

    tags = elements[0].get("tags", {})
    if not tags:
        return {}

    return {
        "cuisine": tags.get("cuisine"),
        "opening_hours": tags.get("opening_hours"),
        "internet_access": tags.get("internet_access"),
        "outdoor_seating": tags.get("outdoor_seating"),
        "wheelchair": tags.get("wheelchair"),
        "website": tags.get("website") or tags.get("contact:website"),
        "phone": tags.get("phone") or tags.get("contact:phone"),
    }
