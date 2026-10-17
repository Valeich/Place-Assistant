"""product_survey: finds out whether a product is sold at the restaurants,
cafes and bars of an area, or at named places, and at what price. Also reads
named restaurants' full menus with prices.

Like area_collector, it runs a deterministic pipeline (pipeline/
product_survey.py) and only fills in arguments and reports the result.
Needs a Maps Platform API key with billing enabled.
"""

from google.adk.agents import Agent

from ...config import MODEL
from .prompt import INSTRUCTION
from .tools import check_named_places, survey_product_in_area

product_survey_agent = Agent(
    model=MODEL,
    name="product_survey",
    description=(
        "Product availability and price survey: checks whether a specific "
        "product or brand is sold at an area's restaurants, cafes or bars, "
        "or at named places, using Google Maps, Google reviews, the places' "
        "own websites and menus, and delivery-app/food-blog listings found "
        "through Google Search. Also returns full menus with prices for "
        "named places. Writes JSON/CSV/DOCX reports."
    ),
    instruction=INSTRUCTION,
    tools=[survey_product_in_area, check_named_places],
)
