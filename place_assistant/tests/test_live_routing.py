"""Does the model pick the right tool, and fill in the right arguments?

Every tool is stubbed, so only Gemini is called: no Maps Platform billing,
no surveys run. Run with: pytest --live -k routing

These test model behaviour, so a failure can be a wording problem in a prompt
rather than a code bug. Read the recorded calls in the failure message.
"""

import pytest

from place_assistant.agent import root_agent
from place_assistant.sub_agents import product_survey_agent
from place_assistant.tests.live_helpers import run_agent

pytestmark = pytest.mark.live


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("What's the address, rating and opening hours of Anomali Coffee Menteng?", "maps_lookup"),
        ("Compare Fentons Creamery and Tara's Organic Ice Cream in Oakland", "maps_lookup"),
        ("Give me Blue Bottle Coffee in Oakland as JSON", "maps_lookup_json"),
        ("What are the best rooftop bars in Jakarta?", "web_search"),
        ("Collect all cafes in Menteng, Jakarta, limit 5", "area_collector"),
        ("Which restaurants in Menteng, Jakarta sell Aqua mineral water, and at what price? Check 5 places.", "product_survey"),
        ("Does Sate Khas Senayan in Menteng, Jakarta sell Aqua, and how much is it?", "product_survey"),
        ("Is Teh Botol Sosro available at Solaria Grand Indonesia, and how much is it?", "product_survey"),
        ("Show me the menu with prices at Anomali Coffee Menteng, Jakarta", "product_survey"),
        ("Show me what's in kemang_jakarta_indonesia_product_bintang_bar.json", "read_output_file"),
        ("Convert menteng_jakarta_indonesia_cafe.json into a Word document", "convert_json_to_docx"),
    ],
)
def test_router_picks_the_right_tool(prompt, expected):
    result = run_agent(root_agent, prompt, stub_tools=True, first_tool_only=True)
    tools = [name for name, _ in result["calls"]]
    assert tools and tools[0] == expected, f"called {result['calls']}"


def test_router_refuses_opening_date_filters():
    result = run_agent(root_agent, "Find cafes in Menteng that opened in the last 6 months", stub_tools=True)
    collectors = [c for c in result["calls"] if c[0] in ("area_collector", "product_survey")]
    explained = any(word in result["text"].lower() for word in ("opening date", "opened", "no google api", "not possible", "isn't possible", "cannot", "can't"))
    assert explained, f"answer didn't explain the limit: {result['text'][:300]!r}; calls {collectors}"


def test_survey_specialist_fills_in_arguments():
    result = run_agent(
        product_survey_agent,
        "Which restaurants in Menteng, Jakarta sell Aqua mineral water, and at what price? Check 5 places.",
        stub_tools=True, first_tool_only=True,
    )
    name, args = result["calls"][0]
    assert name == "survey_product_in_area", result["calls"]
    assert args["product"].strip().lower() == "aqua", "category words like 'mineral water' belong in the description"
    assert args.get("product_description"), "the description is required by the prompt"
    assert "restaurant" in [t.lower() for t in args.get("place_types") or []]
    assert int(args.get("limit", 0)) == 5
    assert "menteng" in args["area_name"].lower()
    generic = {"air mineral", "mineral water", "bottled water"}
    assert not generic & {a.strip().lower() for a in args.get("aliases") or []}, "generic words match every brand"


def test_survey_specialist_uses_named_places_for_one_outlet():
    result = run_agent(product_survey_agent, "Does Sate Khas Senayan in Menteng, Jakarta sell Aqua?", stub_tools=True, first_tool_only=True)
    name, args = result["calls"][0]
    assert name == "check_named_places", result["calls"]
    assert any("sate khas senayan" in p.lower() for p in args["place_names"])
    assert args.get("product", "").strip().lower() == "aqua"


def test_survey_specialist_reads_menus_without_a_product():
    result = run_agent(product_survey_agent, "Show me the menu and prices at Anomali Coffee Menteng, Jakarta", stub_tools=True, first_tool_only=True)
    name, args = result["calls"][0]
    assert name == "check_named_places", result["calls"]
    assert not (args.get("product") or "").strip()
