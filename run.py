"""Single local test runner for every agent in this project.

Usage:
    python run.py <agent> "your question"

Agents:
    assistant  place_assistant   — root router: picks the right specialist or tool (what `adk web` runs)
    maps       maps_lookup       — Google Maps grounding, Markdown answer
    search     web_search        — Google Search grounding, web-cited answer
    json       maps_lookup_json  — Maps grounding, structured JSON answer
    collector  area_collector    — bulk area collection via Places API (needs Maps API key with billing)
    survey     product_survey    — product availability/price survey, menus of named places (needs Maps API key)

Every agent except "assistant" is a specialist under place_assistant/sub_agents/,
run here on its own for testing.

Examples:
    python run.py assistant "Recommend a work-friendly cafe in Jakarta Pusat and show its menu"
    python run.py maps "Compare Fentons Creamery and Tara's Organic Ice Cream in Oakland"
    python run.py search "Best cafes in Jakarta Pusat?"
    python run.py json "Tell me about Blue Bottle Coffee in Oakland"
    python run.py collector "Collect bars that serve alcohol in Jakarta Pusat, limit 10"
    python run.py survey "Which restaurants in Menteng, Jakarta sell Aqua and at what price? Limit 5"
"""

import argparse
import asyncio
import importlib
import json

from dotenv import load_dotenv

load_dotenv()  # picks up GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION from .env

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# CLI name -> (module, agent variable, warn if grounding citations are missing).
# The warning only matters for web_search, whose whole value is citations.
AGENTS = {
    "assistant": ("place_assistant.agent", "root_agent", False),
    "maps": ("place_assistant.sub_agents.maps_lookup.agent", "maps_lookup_agent", False),
    "search": ("place_assistant.sub_agents.web_search.agent", "web_search_agent", True),
    "json": ("place_assistant.sub_agents.maps_lookup_json.agent", "maps_lookup_json_agent", False),
    "collector": ("place_assistant.sub_agents.area_collector.agent", "area_collector_agent", False),
    "survey": ("place_assistant.sub_agents.product_survey.agent", "product_survey_agent", False),
}

USER_ID = "local_user"


async def run(agent_key: str, user_query: str) -> None:
    """Runs one agent on one question and prints its events as they arrive:
    which tool the router called, errors, and each final answer.
    """
    module_path, attr, warn_if_uncited = AGENTS[agent_key]
    root_agent = getattr(importlib.import_module(module_path), attr)

    app_name = f"{agent_key}_app"
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=app_name, user_id=USER_ID)
    runner = Runner(agent=root_agent, app_name=app_name, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=user_query)])

    async for event in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=message
    ):
        if event.error_message:
            print(f"[agent error] {event.error_code}: {event.error_message}")
        elif event.get_function_calls():
            for call in event.get_function_calls():
                print(f"[routed to: {call.name}]")
        elif event.is_final_response() and event.content and event.content.parts:
            # Concatenate all non-thought text parts — some responses split
            # across multiple parts (e.g. a thinking part plus the answer),
            # and printing only parts[0] can silently show nothing.
            text = "\n".join(
                p.text for p in event.content.parts if p.text and not p.thought
            )
            if not text:
                # AgentTool(skip_summarization=True) ends the turn with the
                # raw function_response as the final event instead of a
                # model-authored text part — print that verbatim as JSON.
                responses = event.get_function_responses()
                if responses:
                    text = "\n".join(json.dumps(r.response, indent=2) for r in responses)
            # SequentialAgent pipelines emit one final response per step;
            # prefix with the author so multi-step output stays readable.
            print(f"[{event.author}] {text or '(empty response)'}")

            if warn_if_uncited:
                chunks = (
                    event.grounding_metadata and event.grounding_metadata.grounding_chunks
                ) or []
                if not chunks:
                    print(
                        "\n[warning] No grounding citations were returned — "
                        "treat this answer with caution."
                    )


def main() -> None:
    """Reads the agent name and question from the command line and runs them."""
    parser = argparse.ArgumentParser(
        description="Run any of this project's agents locally.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("agent", choices=sorted(AGENTS), help="which agent to run")
    parser.add_argument("query", nargs="+", help="the question to ask")
    args = parser.parse_args()

    asyncio.run(run(args.agent, " ".join(args.query)))


if __name__ == "__main__":
    main()
