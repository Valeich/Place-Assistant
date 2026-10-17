"""Helpers for live tests: run an agent for real, optionally with its tools
stubbed so only the model's choices are tested (and nothing is billed)."""

import asyncio

from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types

STUB_RESULT = {
    "status": "success",
    "result": "Done. (Test stub: the tool was not run.) Reply to the user in one short sentence; call no more tools.",
}

# A runaway tool loop costs money and never ends; real answers need far fewer calls.
_MAX_LLM_CALLS = 12


def run_agent(agent, prompt: str, stub_tools: bool = False, first_tool_only: bool = False, timeout: float = 240):
    """Runs agent on prompt. Returns {"calls": [(tool, args)], "text": final text}.

    stub_tools: every tool call is recorded and answered with STUB_RESULT
        instead of running, through ADK's before_tool_callback.
    first_tool_only: stop as soon as the model calls its first tool. Enough
        for routing tests, and much faster.
    """
    calls = []

    def record(tool, args, tool_context):
        calls.append((tool.name, dict(args)))
        return STUB_RESULT if stub_tools else None

    test_agent = agent.model_copy(update={"before_tool_callback": record})

    async def go():
        runner = InMemoryRunner(agent=test_agent, app_name="live_test")
        session = await runner.session_service.create_session(app_name="live_test", user_id="tester")
        texts = []
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        events = runner.run_async(
            user_id="tester", session_id=session.id, new_message=message,
            run_config=RunConfig(max_llm_calls=_MAX_LLM_CALLS),
        )
        async for event in events:
            if first_tool_only and calls:
                break
            if event.is_final_response() and event.content and event.content.parts:
                texts += [p.text for p in event.content.parts if p.text and not p.thought]
        await events.aclose()
        return "\n".join(texts)

    text = asyncio.run(asyncio.wait_for(go(), timeout))
    return {"calls": calls, "text": text}
