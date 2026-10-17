"""All settings for place_assistant in one place: model, API keys, endpoints,
and where output files go. Values come from the project-root .env file (its
variables are listed in AGENTS.md, section 9).

Two separate Google products are used, with two kinds of credentials:
- Vertex AI (Gemini, Maps grounding, Search grounding) authenticates with
  Application Default Credentials plus GOOGLE_GENAI_USE_VERTEXAI /
  GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION. Every agent uses this.
- Maps Platform (Places API (New) + Geocoding API) needs a real API key with
  billing enabled. Only the bulk collection pipeline uses it, because
  grounding can't enumerate every place in an area.
"""

import os
from pathlib import Path

# --- Gemini models ------------------------------------------------------------
# Gemini 3.1 Pro, a preview model. Vertex AI serves preview models only from
# the "global" location, so set GOOGLE_CLOUD_LOCATION=global. Verified on
# 2026-10-15 with Maps grounding, Search grounding and JSON output.
# Override with GEMINI_MODEL in .env, e.g. gemini-2.5-flash.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

# The bulk steps inside surveys and collections: menu reading, product
# confirmation, PDF transcription and review sentiment. They run several times
# per place, so this is where a faster, cheaper model saves the most.
# Defaults to MODEL; set GEMINI_PIPELINE_MODEL=gemini-2.5-flash to save cost.
PIPELINE_MODEL = os.environ.get("GEMINI_PIPELINE_MODEL") or MODEL

# --- Spending cap ---------------------------------------------------------------
# The most places one survey, outlet check or area collection may check, from
# the UI forms or from chat. Every place is billed, so set it low (e.g. 5) on a
# demo deployment. Defaults to 60, Google's Text Search maximum.
def _places_cap() -> int:
    try:
        return max(1, min(int(os.environ.get("MAX_PLACES_PER_RUN") or 60), 60))
    except ValueError:
        return 60


MAX_PLACES_PER_RUN = _places_cap()


def limit_note(asked: int) -> str | None:
    """A note for a run whose requested size was cut to MAX_PLACES_PER_RUN,
    or None when it wasn't.
    """
    if asked <= MAX_PLACES_PER_RUN:
        return None
    return (
        f"This deployment checks at most {MAX_PLACES_PER_RUN} places per run, "
        f"so {MAX_PLACES_PER_RUN} of the {asked} asked for were checked."
    )


# --- Maps Platform / third-party keys ----------------------------------------
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("MAPS_API_KEY")
FOURSQUARE_API_KEY = os.environ.get("FOURSQUARE_API_KEY")  # optional, extra review source

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FOURSQUARE_PLACES_URL = "https://api.foursquare.com/v3/places"

# Honest, identifying User-Agent for every outbound request to a third party.
# OpenStreetMap's Overpass API requires one; business websites get it too.
USER_AGENT = "PlaceDataCollector/1.0 (research/data-collection tool)"

# --- Output files ---------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Must stay dot-prefixed: `adk web` treats every non-dot folder in the project
# root as an agent app and fails with "No root_agent found" on a plain
# "output" folder. Absolute, so results land in the same place no matter
# which directory a command is run from.
OUTPUT_DIR = PROJECT_ROOT / ".output"


def require_maps_api_key() -> None:
    """Raises a clear setup error if the Maps Platform key is missing."""
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError(
            "No Maps API key found (checked GOOGLE_MAPS_API_KEY and "
            "MAPS_API_KEY). This pipeline needs 'Places API (New)' and "
            "'Geocoding API' enabled on a Maps Platform project, plus an API "
            "key restricted to those two APIs, set in .env."
        )
