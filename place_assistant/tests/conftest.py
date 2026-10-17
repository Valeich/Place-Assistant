"""Shared fixtures.

Offline tests can't reach the network: an autouse fixture blocks every DNS
lookup and socket connection except to localhost. A missing mock then fails
loudly instead of calling a paid API. Tests marked live or billed are exempt.
"""

import socket
import types as pytypes

import pytest

# place_assistant.config is imported inside the fixtures, not here. pytest
# loads this file before the root conftest loads .env for live runs, and the
# package reads its settings when first imported.

_LOCAL = ("127.0.0.1", "::1", "localhost", "testserver")
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _guarded_connect(sock, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else None
    if host is None or host in _LOCAL:
        return _real_connect(sock, address, *args, **kwargs)
    raise RuntimeError(f"Offline test tried to reach the network: {address}")


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host in _LOCAL or host is None:
        return _real_getaddrinfo(host, *args, **kwargs)
    raise RuntimeError(f"Offline test tried to look up {host!r}")


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Blocks network access for every test not marked live or billed."""
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("billed"):
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    """A temporary output folder in place of .output/."""
    from place_assistant import config

    path = tmp_path / "out"
    monkeypatch.setattr(config, "OUTPUT_DIR", path)
    return path


@pytest.fixture
def maps_key(monkeypatch):
    """A fake Maps key, so code that requires one runs. It is never sent anywhere."""
    from place_assistant import config

    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "test-maps-key")
    return "test-maps-key"


def gemini_response(text: str, sources=()):
    """A stand-in for a google-genai response: .text plus grounding sources."""
    chunks = [pytypes.SimpleNamespace(web=pytypes.SimpleNamespace(title=t, uri=f"https://example.com/{t}")) for t in sources]
    meta = pytypes.SimpleNamespace(grounding_chunks=chunks)
    return pytypes.SimpleNamespace(text=text, candidates=[pytypes.SimpleNamespace(grounding_metadata=meta)])


class FakeGemini:
    """Replaces get_client() in the given modules. handler(prompt, grounded)
    returns a response from gemini_response(), or a plain string.
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.models = pytypes.SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config=None):
        prompt = contents if isinstance(contents, str) else "\n".join(str(c) for c in contents)
        grounded = bool(getattr(config, "tools", None))
        self.calls.append({"prompt": prompt, "grounded": grounded})
        result = self.handler(prompt, grounded)
        return gemini_response(result) if isinstance(result, str) else result


@pytest.fixture
def fake_gemini(monkeypatch):
    """Installs a FakeGemini: fake_gemini(handler, *modules) -> FakeGemini."""

    def install(handler, *modules):
        fake = FakeGemini(handler)
        for module in modules:
            monkeypatch.setattr(module, "get_client", lambda: fake)
        return fake

    return install
