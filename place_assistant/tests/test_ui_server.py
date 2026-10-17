"""The web UI backend (place_assistant/ui/server.py), with the agent and the
pipelines replaced by fakes."""

import json

import pytest
from fastapi.testclient import TestClient
from google.adk.events import Event
from google.genai import types

from place_assistant.ui import server


@pytest.fixture
def client(out_dir, monkeypatch, maps_key):
    out_dir.mkdir()
    return TestClient(server.app)


def _lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_page_and_static_files(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_health_reports_settings_but_never_the_key(client, maps_key):
    response = client.get("/api/health")
    assert response.json()["maps_key"] is True
    assert maps_key not in response.text


def test_reports_list_download_and_reopen(client, out_dir):
    survey = {"summary": {"kind": "product_survey", "product": "Bintang", "area": "Kemang", "status_counts": {}},
              "places": [{"name": "A", "status": "found_with_price",
                          "evidence": [{"source": "official_website", "item": "Bintang", "price": "60",
                                        "price_value": 60000.0, "currency": "IDR"}]},
                         {"name": "B", "status": "no_evidence", "verdict": "Not confirmed", "reviews_checked": 2,
                          "proof": "Not mentioned in its 2 Google reviews. This doesn't mean it isn't sold."}]}
    (out_dir / "kemang_product_bintang_bar.json").write_text(json.dumps(survey))
    (out_dir / "kemang_product_bintang_bar.csv").write_text("name\nA\n")

    files = client.get("/api/files").json()
    json_entry = next(f for f in files if f["format"] == "json")
    assert json_entry["kind"] == "product_survey" and json_entry["title"] == "Bintang in Kemang"

    download = client.get("/api/files/kemang_product_bintang_bar.csv")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]

    report = client.get("/api/reports/kemang_product_bintang_bar.json").json()
    assert report["places"][0]["evidence"][0]["price_read_as"] == "IDR 60,000"
    assert report["places"][0]["verdict"] == "Available"  # older reports get a proof when reopened
    assert report["places"][0]["proof"].startswith('Listed as "Bintang" at 60')
    assert report["places"][1]["verdict"] == "Needs field check"  # saved wording is replaced by the current one
    assert report["places"][1]["proof"].startswith("Not yet mentioned in its 2 Google reviews.")


@pytest.mark.parametrize("name", ["..%2F.env", "%2E%2E%2F%2E%2E%2Fetc%2Fpasswd", ".env", "x.json", "..%2Frun.py", "notes.txt"])
def test_downloads_stay_in_the_output_folder(client, name):
    assert client.get(f"/api/files/{name}").status_code == 404


def test_bad_form_input_is_rejected(client):
    response = client.post("/api/survey", json={"area": "K", "product": "", "limit": 99, "place_types": []})
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert response.status_code == 422 and {"area", "product", "limit", "place_types"} <= fields


def test_survey_stream(client, monkeypatch, out_dir):
    received = {}

    def fake_survey(progress, cancel, **kwargs):
        received.update(kwargs)
        progress({"stage": "checking", "total": 1, "names": ["Bar A"]})
        progress({"stage": "place_done", "index": 0, "done": 1, "total": 1, "name": "Bar A", "status": "no_evidence"})
        return {"summary": {"kind": "product_survey"}, "places": [{"name": "Bar A", "status": "no_evidence"}],
                "json_path": str(out_dir / "s.json"), "csv_path": None, "docx_path": None}

    monkeypatch.setattr(server, "survey_product", fake_survey)
    body = {"area": "Kemang, Jakarta", "product": "Bintang", "aliases": ["Bir Bintang"], "place_types": ["bar"], "limit": 3}
    items = _lines(client.post("/api/survey", json=body))
    assert [i["type"] for i in items] == ["progress", "progress", "result"]
    assert items[-1]["data"]["files"] == {"json": "s.json"}
    assert received["limit"] == 3 and received["aliases"] == ["Bir Bintang"]


def test_survey_errors_are_streamed(client, monkeypatch):
    def failing(progress, cancel, **kwargs):
        raise RuntimeError("No Maps API key found")

    monkeypatch.setattr(server, "survey_product", failing)
    items = _lines(client.post("/api/survey", json={"area": "Kemang", "product": "Bintang"}))
    assert items == [{"type": "error", "message": "No Maps API key found"}]


def test_check_menu_mode_passes_an_empty_product(client, monkeypatch, out_dir):
    received = {}

    def fake_check(progress, cancel, **kwargs):
        received.update(kwargs)
        return {"summary": {"kind": "menu_check"}, "places": [], "json_path": str(out_dir / "m.json")}

    monkeypatch.setattr(server, "check_named_places", fake_check)
    items = _lines(client.post("/api/check", json={"area": "Jakarta", "place_names": ["Eastern Promise"]}))
    assert items[-1]["type"] == "result" and received["product"] == "" and received["place_names"] == ["Eastern Promise"]


class _FakeRunner:
    """Yields a tool call, its result and a final answer, like the root agent."""

    async def run_async(self, user_id, session_id, new_message):
        call = types.Part(function_call=types.FunctionCall(name="maps_lookup", args={"request": "Anomali"}))
        yield Event(author="place_assistant", content=types.Content(role="model", parts=[call]))
        result = types.Part(function_response=types.FunctionResponse(name="maps_lookup", response={"result": "ok"}))
        yield Event(author="place_assistant", content=types.Content(role="user", parts=[result]))
        yield Event(author="place_assistant", content=types.Content(role="model", parts=[types.Part(text="**Anomali** is in Menteng.")]))


def test_chat_stream_and_session(client, monkeypatch):
    monkeypatch.setattr(server, "_runner", _FakeRunner())
    items = _lines(client.post("/api/chat", json={"message": "Where is Anomali Coffee?"}))
    assert [i["type"] for i in items] == ["session", "tool_call", "tool_result", "message", "done"]
    assert items[1]["name"] == "maps_lookup" and "Menteng" in items[3]["text"]

    session_id = items[0]["session_id"]
    again = _lines(client.post("/api/chat", json={"message": "And its rating?", "session_id": session_id}))
    assert again[0]["session_id"] == session_id  # the conversation continues


def test_chat_shows_raw_json_from_the_json_specialist():
    response = types.Part(function_response=types.FunctionResponse(
        name="maps_lookup_json", response={"result": {"places": [{"name": "Blue Bottle", "found": True}]}}))
    event = Event(author="place_assistant", content=types.Content(role="user", parts=[response]),
                  actions={"skip_summarization": True})
    items = server._chat_items(event)
    message = next(i for i in items if i["type"] == "message")
    assert message["text"].startswith("```json") and '"found": true' in message["text"]


def test_a_stopped_tool_call_is_answered_before_the_next_message():
    # Stop can cancel a turn between the model's tool call and the tool's
    # result. Gemini rejects that history, so the next turn answers the call.
    import asyncio

    async def scenario():
        session = await server._sessions.create_session(app_name=server.APP_NAME, user_id=server.USER_ID)
        call = types.Part(function_call=types.FunctionCall(id="call-1", name="product_survey", args={"request": "Aqua"}))
        await server._sessions.append_event(session, Event(
            invocation_id="inv-1", author="place_assistant", content=types.Content(role="model", parts=[call])))
        await server._answer_stopped_calls(session)
        await server._answer_stopped_calls(session)  # a second turn adds nothing more
        saved = await server._sessions.get_session(app_name=server.APP_NAME, user_id=server.USER_ID, session_id=session.id)
        return [r for e in saved.events for r in e.get_function_responses()]

    responses = asyncio.run(scenario())
    assert [(r.id, r.name) for r in responses] == [("call-1", "product_survey")]
    assert "Stopped by the user" in responses[0].response["error"]


def test_health_reports_the_place_cap(client, monkeypatch):
    from place_assistant import config

    monkeypatch.setattr(config, "MAX_PLACES_PER_RUN", 5)
    health = client.get("/api/health").json()
    assert health["max_places_per_run"] == 5 and health["max_places"] == 5


def test_search_suggestions_reach_the_chat():
    # Google's grounding terms require showing them with a web_search answer.
    from types import SimpleNamespace

    from place_assistant.sub_agents.web_search.agent import SEARCH_SUGGESTIONS_KEY, keep_search_suggestions

    context = SimpleNamespace(state={})
    entry = types.SearchEntryPoint(rendered_content="<div class='chip'>best cafes menteng</div>")
    response = SimpleNamespace(grounding_metadata=types.GroundingMetadata(search_entry_point=entry))
    assert keep_search_suggestions(context, response) is None  # the answer itself is untouched
    assert context.state == {SEARCH_SUGGESTIONS_KEY: entry.rendered_content}
    assert keep_search_suggestions(SimpleNamespace(state={}), SimpleNamespace(grounding_metadata=None)) is None

    result = types.Part(function_response=types.FunctionResponse(name="web_search", response={"result": "ok"}))
    event = Event(author="place_assistant", content=types.Content(role="user", parts=[result]),
                  actions={"state_delta": dict(context.state)})
    assert {"type": "search_suggestions", "html": entry.rendered_content} in server._chat_items(event)
