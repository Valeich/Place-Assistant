"""Root agent: reads the user's request and routes it to the right
specialist or tool. It has no place knowledge of its own.

    maps_lookup            facts about specific named places (Google Maps grounding)
    maps_lookup_json       the same lookup, returned as JSON
    web_search             "best X in Y" and web-cited answers (Google Search grounding)
    area_collector         bulk "all X in an area" collection (Places API pipeline)
    product_survey         is a product sold at an area's or named places, at what price;
                           full menus with prices for named places
    fetch_rendered_page    reads a business's own website
    read_output_file       shows a collected JSON/CSV file in chat
    convert_json_to_docx   turns an older JSON run into a .docx report

Why specialists are wrapped as AgentTools: the built-in grounding tools must
be the only tool on their agent, so they can't sit next to anything else.
Wrapping each specialist as an AgentTool is ADK's documented workaround.
An AgentTool returns only the specialist's final text, so each specialist puts
everything the user needs, like Maps links and citations, in that text.
"""

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from .config import MODEL
from .prompt import INSTRUCTION
from .sub_agents import (
    area_collector_agent,
    maps_lookup_agent,
    maps_lookup_json_agent,
    product_survey_agent,
    web_search_agent,
)
from .tools import convert_json_to_docx, fetch_rendered_page, read_output_file

root_agent = Agent(
    model=MODEL,
    name="place_assistant",
    description=(
        "Single entry point for all place-related requests: routes to "
        "Google Maps grounding (specific places), Google Search grounding "
        "('best of' roundups), JSON-formatted Maps lookups, bulk area "
        "collection with alcohol-service filtering, product availability and "
        "price surveys, menu retrieval, and website fetching — and relays "
        "whichever answer applies."
    ),
    instruction=INSTRUCTION,
    tools=[
        AgentTool(agent=maps_lookup_agent),
        AgentTool(agent=web_search_agent),
        # skip_summarization=True returns the JSON verbatim. Without it, the
        # root model re-describes the output and was seen adding a wrapper key
        # and writing Python True/False instead of JSON true/false.
        AgentTool(agent=maps_lookup_json_agent, skip_summarization=True),
        AgentTool(agent=area_collector_agent),
        AgentTool(agent=product_survey_agent),
        # Plain function tools can sit alongside AgentTools. The sole-tool
        # rule only applies to the grounding tools inside the specialists.
        fetch_rendered_page,
        read_output_file,
        convert_json_to_docx,
    ],
)
