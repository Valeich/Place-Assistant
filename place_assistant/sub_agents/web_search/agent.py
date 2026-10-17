"""web_search: place questions answered from the live web through Google
Search grounding, with cited sources.

Good for specific named places and "best X in Y" questions where someone has
already published a list. It is not an area census: it only finds what has
been written and indexed online.

Google's Search grounding terms require showing the returned Search
Suggestions (searchEntryPoint HTML) with grounded answers in an end-user
product. The root agent only receives this agent's text, so
keep_search_suggestions saves the HTML in session state; AgentTool passes
state changes up to the root, and the web UI shows it under the answer. The
CLI and `adk web` don't render it, which is fine for local testing.
"""

from google.adk.agents import Agent
from google.adk.tools import google_search

from ...config import MODEL
from .prompt import INSTRUCTION

SEARCH_SUGGESTIONS_KEY = "search_suggestions_html"


def keep_search_suggestions(callback_context, llm_response):
    """Saves a grounded response's Search Suggestions HTML in session state
    under SEARCH_SUGGESTIONS_KEY. Leaves the response unchanged.
    """
    meta = llm_response.grounding_metadata
    entry = meta.search_entry_point if meta else None
    if entry and entry.rendered_content:
        callback_context.state[SEARCH_SUGGESTIONS_KEY] = entry.rendered_content
    return None

web_search_agent = Agent(
    model=MODEL,
    name="web_search",
    description=(
        "Answers place-related questions (cafes, restaurants, landmarks) by "
        "grounding responses in live Google Search results, with citations. "
        "Good for specific/named places or 'best of' roundups already "
        "published online — not an exhaustive area search."
    ),
    instruction=INSTRUCTION,
    # Like google_maps_grounding, google_search must be the agent's only tool.
    tools=[google_search],
    after_model_callback=keep_search_suggestions,
)
