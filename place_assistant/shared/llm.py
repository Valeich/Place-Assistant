"""One shared Gemini client for the pipeline's direct (non-agent) LLM calls."""

import threading

from google import genai
from google.genai import types

# The SDK's default is no timeout, so one stalled request could hang a
# whole collection or survey run.
_TIMEOUT_MS = 120_000


def low_thinking(model: str) -> types.ThinkingConfig:
    """Minimal thinking for simple copying tasks such as PDF transcription.
    Gemini 3 models take a thinking level; Gemini 2.5 models take a budget.
    """
    if model.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="low")
    return types.ThinkingConfig(thinking_budget=0)


_client: genai.Client | None = None
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    """Returns the shared Gemini client, configured from the environment,
    with a two-minute timeout per request.

    Created on first use rather than at import time, so importing the package
    never fails just because .env hasn't been loaded yet. The lock matters:
    survey workers start together, and a second client built in a race is
    garbage-collected mid-request, which closes its connection ("Cannot send
    a request, as the client has been closed").
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = genai.Client(http_options=types.HttpOptions(timeout=_TIMEOUT_MS))
    return _client
