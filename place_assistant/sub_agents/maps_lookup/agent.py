"""maps_lookup: facts about specific named places from Google Maps, answered
in Markdown.

Uses ADK's built-in google_maps_grounding tool, which queries Google Maps'
place database through Vertex AI. No Maps Platform API key is needed.
"""

from google.adk.agents import Agent
from google.adk.tools import google_maps_grounding

from ...config import MODEL
from .prompt import INSTRUCTION

maps_lookup_agent = Agent(
    model=MODEL,
    name="maps_lookup",
    description=(
        "Looks up real places on Google Maps (restaurants, cafes, landmarks) "
        "and reports their address, rating, reviews, open status, price "
        "level, facilities/amenities, and general menu info."
    ),
    instruction=INSTRUCTION,
    # Vertex AI rejects google_maps_grounding when it shares an agent with any
    # other tool, so it must stay the only one here.
    tools=[google_maps_grounding],
)
