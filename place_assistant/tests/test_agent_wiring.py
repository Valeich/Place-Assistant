"""Structural rules from AGENTS.md section 11, checked without calling any model.

A broken rule here usually fails silently at runtime: the router can't name
a tool, Vertex AI rejects a grounding tool, or JSON gets rewritten.
"""

import importlib
import inspect

import pytest
from google.adk.tools import FunctionTool, google_maps_grounding, google_search
from google.adk.tools.agent_tool import AgentTool

from place_assistant import config, prompt
from place_assistant.agent import root_agent
from place_assistant.tools import fetch_rendered_page
from place_assistant.sub_agents import (
    area_collector_agent,
    maps_lookup_agent,
    maps_lookup_json_agent,
    product_survey_agent,
    web_search_agent,
)

EXPECTED_TOOLS = {
    "maps_lookup", "maps_lookup_json", "web_search", "area_collector", "product_survey",
    "fetch_rendered_page", "read_output_file", "convert_json_to_docx",
}


def _tool_name(tool):
    return tool.name if hasattr(tool, "name") else tool.__name__


def test_root_has_the_expected_tools():
    assert {_tool_name(t) for t in root_agent.tools} == EXPECTED_TOOLS


def test_root_prompt_names_every_tool_and_counts_them():
    for name in EXPECTED_TOOLS:
        assert name in prompt.INSTRUCTION, f"root prompt never mentions {name}"
    assert "You have eight tools" in prompt.INSTRUCTION and len(EXPECTED_TOOLS) == 8


def test_run_py_lists_every_specialist():
    run = importlib.import_module("run")
    specialists = {t.agent.name for t in root_agent.tools if isinstance(t, AgentTool)}
    listed = {getattr(importlib.import_module(m), attr).name for m, attr, _ in run.AGENTS.values()}
    assert specialists <= listed


@pytest.mark.parametrize(
    "agent, grounding",
    [
        (maps_lookup_agent, google_maps_grounding),
        (web_search_agent, google_search),
        (maps_lookup_json_agent.sub_agents[0], google_maps_grounding),
    ],
    ids=["maps_lookup", "web_search", "maps_lookup_step"],
)
def test_grounding_tools_are_alone(agent, grounding):
    assert agent.tools == [grounding], "Vertex AI rejects a grounding tool that shares an agent"


def test_json_format_step_has_a_schema_and_no_tools():
    step = maps_lookup_json_agent.sub_agents[1]
    assert step.output_schema is not None and not step.tools
    assert maps_lookup_json_agent.sub_agents[0].output_key == "raw_place_answer"


def test_json_specialist_output_is_not_rewritten():
    tool = next(t for t in root_agent.tools if isinstance(t, AgentTool) and t.agent is maps_lookup_json_agent)
    assert tool.skip_summarization is True


def test_every_agent_uses_the_pinned_model():
    agents = [root_agent, maps_lookup_agent, web_search_agent, area_collector_agent, product_survey_agent,
              *maps_lookup_json_agent.sub_agents]
    assert {a.model for a in agents} == {config.MODEL}


def _function_tools():
    tools = [t for t in root_agent.tools if not isinstance(t, AgentTool)]
    tools += area_collector_agent.tools + product_survey_agent.tools
    return tools


@pytest.mark.parametrize("func", _function_tools(), ids=lambda f: f.__name__)
def test_function_tools_have_usable_declarations(func):
    declaration = FunctionTool(func)._get_declaration()
    assert declaration.name == func.__name__
    assert declaration.description and len(declaration.description) > 80, "the docstring is the tool's prompt"


@pytest.mark.parametrize(
    "func", [*product_survey_agent.tools, *area_collector_agent.tools, fetch_rendered_page], ids=lambda f: f.__name__
)
def test_slow_tools_dont_block_the_event_loop(func):
    # ADK runs sync tools on its event loop; long jobs must be async.
    assert inspect.iscoroutinefunction(func)


def test_specialists_have_descriptions_for_the_router():
    for tool in root_agent.tools:
        if isinstance(tool, AgentTool):
            assert len(tool.agent.description) > 40, f"{tool.agent.name} needs a description"


def test_web_search_keeps_its_search_suggestions():
    from place_assistant.sub_agents.web_search.agent import keep_search_suggestions

    assert web_search_agent.after_model_callback is keep_search_suggestions
