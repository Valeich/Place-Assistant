"""area_collector: collects every place of one type in an area, such as
"all cafes in Jakarta Pusat", and writes the results to files.

The agent makes a single collect_places call that runs the whole batch
pipeline (see pipeline/). Enumerating an area is deterministic ETL work, so
the agent's only job is to start it and report the outcome faithfully.
Needs a Maps Platform API key with billing enabled.
"""

from google.adk.agents import Agent

from ...config import MODEL
from .prompt import INSTRUCTION
from .tools import collect_places

area_collector_agent = Agent(
    model=MODEL,
    name="area_collector",
    description=(
        "Runs a batch pipeline to collect structured place data (address, "
        "rating, reviews, sentiment, facilities, alcohol service, "
        "best-effort menu/price — from the place's own website, falling "
        "back to a delivery-platform/food-blog search) for a named area and "
        "place type (cafe, restaurant, bar, museum, etc.), optionally "
        "filtered to only alcohol-serving places, using Google Places API, "
        "Foursquare, OpenStreetMap, and Google Search — writes full results "
        "to JSON/CSV and returns per-place menu highlights inline."
    ),
    instruction=INSTRUCTION,
    tools=[collect_places],
)
