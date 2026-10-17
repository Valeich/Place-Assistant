"""Optional secondary review source: Foursquare's Places API "tips" (their
review-equivalent), used to supplement Google's 5-review-per-place cap.

Foursquare's API surface has shifted over the years (v3 Places -> FSQ Places)
— verify the endpoints below against https://docs.foursquare.com before
relying on this in production. This integration is strictly optional: it's
skipped entirely if FOURSQUARE_API_KEY isn't set, and any request failure
degrades to an empty list rather than breaking the pipeline.
"""

import requests

from .. import config


def get_tips(name: str, lat: float | None, lng: float | None, limit: int = 10) -> list[str]:
    """Best-effort: returns tip text strings for the venue matching name/lat/lng.

    Returns [] if no API key is configured, the venue isn't found, or the
    request fails for any reason.
    """
    if not config.FOURSQUARE_API_KEY or lat is None or lng is None:
        return []

    headers = {"Authorization": config.FOURSQUARE_API_KEY, "Accept": "application/json"}
    try:
        search_resp = requests.get(
            f"{config.FOURSQUARE_PLACES_URL}/search",
            headers=headers,
            params={"query": name, "ll": f"{lat},{lng}", "limit": 1},
            timeout=10,
        )
        search_resp.raise_for_status()
        venues = search_resp.json().get("results", [])
        if not venues:
            return []

        fsq_id = venues[0]["fsq_id"]
        tips_resp = requests.get(
            f"{config.FOURSQUARE_PLACES_URL}/{fsq_id}/tips",
            headers=headers,
            params={"limit": limit},
            timeout=10,
        )
        tips_resp.raise_for_status()
        return [tip["text"] for tip in tips_resp.json() if tip.get("text")]
    except Exception:
        return []
