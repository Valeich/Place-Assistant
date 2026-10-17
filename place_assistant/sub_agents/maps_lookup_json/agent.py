"""maps_lookup_json: the same Google Maps lookup as maps_lookup, returned as
structured JSON.

Two steps run in sequence, because output_schema combined with tools is
unreliable on Gemini 2.5 models. Keeping the steps apart works on every model,
so GEMINI_MODEL can be switched freely:

1. lookup_step grounds the answer with Google Maps and writes free text into
   session state under "raw_place_answer".
2. format_step has no tools, so output_schema works reliably. It only
   transcribes that text into PlaceDetailsResponse.
"""

from google.adk.agents import Agent, SequentialAgent
from google.adk.tools import google_maps_grounding

from ...config import MODEL
from .prompt import FORMAT_INSTRUCTION, LOOKUP_INSTRUCTION
from .schema import PlaceDetailsResponse

lookup_step = Agent(
    model=MODEL,
    name="maps_lookup_step",
    instruction=LOOKUP_INSTRUCTION,
    tools=[google_maps_grounding],
    output_key="raw_place_answer",
)

format_step = Agent(
    model=MODEL,
    name="json_format_step",
    instruction=FORMAT_INSTRUCTION,
    output_schema=PlaceDetailsResponse,
)

maps_lookup_json_agent = SequentialAgent(
    name="maps_lookup_json",
    description=(
        "Looks up real places on Google Maps and returns structured JSON "
        "(name, address, rating, reviews, price level, facilities, menu "
        "summary, Maps link) instead of Markdown."
    ),
    sub_agents=[lookup_step, format_step],
)
