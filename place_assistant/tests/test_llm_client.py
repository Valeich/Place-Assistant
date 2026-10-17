"""The shared Gemini client (shared/llm.py)."""

import threading
import time

from place_assistant.shared import llm


def test_parallel_first_calls_share_one_client(monkeypatch):
    # Survey workers start together. A second client built in a race is
    # garbage-collected mid-request, which closes its connection.
    built = []

    class SlowClient:
        def __init__(self, **kwargs):
            time.sleep(0.05)
            built.append(self)

    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm.genai, "Client", SlowClient)
    start, got = threading.Barrier(8), []

    def worker():
        start.wait()
        got.append(llm.get_client())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1 and all(client is built[0] for client in got)
