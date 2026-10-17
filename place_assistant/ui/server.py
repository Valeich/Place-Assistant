"""Web UI backend for Place Assistant.

Serves the single-page frontend in static/ and a small JSON API:

    GET  /api/health          settings the UI shows (model, project, Maps key set?)
    POST /api/chat            one chat turn with the root agent, streamed
    POST /api/survey          area product survey, streamed with progress
    POST /api/check           named places: product check or full menus, streamed
    GET  /api/files           reports in the output folder
    GET  /api/files/{name}    download or view one report file
    GET  /api/reports/{name}  a saved .json report, shaped like a live result

Streams are newline-delimited JSON (one object per line). Long jobs send a
{"type": "ping"} line every 15 seconds so proxies keep the connection open.
When the browser disconnects or presses Stop, a survey stops starting new
places; places already running finish and the partial result is saved.

Settings come from environment variables only, like the rest of the
package, so the same server runs locally (run_ui.py loads .env) or in a
container. It has no login: run it locally, or put it behind your cloud's
authentication when you deploy it.
"""

import asyncio
import json
import mimetypes
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from .. import config
from ..agent import root_agent
from ..pipeline import check_named_places, prices, product_survey, survey_product, verdict
from ..sub_agents.web_search.agent import SEARCH_SUGGESTIONS_KEY

STATIC_DIR = Path(__file__).parent / "static"
APP_NAME = "place_assistant"
USER_ID = "ui_user"
_DOWNLOADABLE = (".json", ".csv", ".docx")
_HEARTBEAT_S = 15
_DISCONNECT_POLL_S = 1.0
_END = object()

app = FastAPI(title="Place Assistant", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One in-memory runner for the whole server. Chat history lives only as long
# as the process, which is what local testing needs.
_sessions = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=_sessions)


# --- Request bodies -------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class SurveyRequest(BaseModel):
    area: str = Field(min_length=2, max_length=200)
    product: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=200)
    place_types: list[str] = Field(default_factory=lambda: ["restaurant"], min_length=1, max_length=3)
    limit: int = Field(default=10, ge=1, le=60)
    search_third_party: bool = True


class CheckRequest(BaseModel):
    area: str = Field(min_length=2, max_length=200)
    place_names: list[str] = Field(min_length=1, max_length=product_survey.MAX_NAMED_PLACES)
    product: str = Field(default="", max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=200)
    search_third_party: bool = True


# --- Streaming helpers --------------------------------------------------------------

def _line(item: dict) -> bytes:
    """One NDJSON line: item as JSON plus a newline, UTF-8 encoded."""
    return (json.dumps(item, ensure_ascii=False, default=str) + "\n").encode("utf-8")


async def _with_heartbeat(source, request: Request):
    """Re-yields source's items as NDJSON lines, adding a ping line whenever
    nothing was sent for _HEARTBEAT_S seconds.

    Checks every second whether the browser has gone away (closed the tab or
    pressed Stop) and then closes source, which stops a running survey.
    Writing to a closed connection doesn't fail reliably, so this can't wait
    for the next write to notice.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        """Copies source's items into the queue, then an end marker."""
        try:
            async for item in source:
                await queue.put(item)
        except Exception as exc:  # report, don't break the stream
            await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(_END)

    task = asyncio.create_task(pump())
    getter = asyncio.ensure_future(queue.get())
    last_sent = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({getter}, timeout=_DISCONNECT_POLL_S)
            if not done:
                if await request.is_disconnected():
                    break
                if time.monotonic() - last_sent >= _HEARTBEAT_S:
                    last_sent = time.monotonic()
                    yield _line({"type": "ping"})
                continue
            item = getter.result()
            if item is _END:
                break
            last_sent = time.monotonic()
            yield _line(item)
            getter = asyncio.ensure_future(queue.get())
    finally:
        getter.cancel()
        task.cancel()


def _stream(source, request: Request) -> StreamingResponse:
    """Wraps source in a streaming NDJSON response with heartbeats and disconnect checks."""
    return StreamingResponse(
        _with_heartbeat(source, request),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --- Chat ---------------------------------------------------------------------------

def _chat_items(event) -> list[dict]:
    """Turns one ADK event into UI items: tool calls, tool results, the
    agent's text, and errors.
    """
    items = []
    if event.error_message:
        items.append({"type": "error", "message": f"{event.error_code}: {event.error_message}"})
    for call in event.get_function_calls():
        items.append({"type": "tool_call", "name": call.name, "args": call.args or {}})
    responses = event.get_function_responses()
    for response in responses:
        items.append({"type": "tool_result", "name": response.name})
    suggestions = (event.actions.state_delta or {}).get(SEARCH_SUGGESTIONS_KEY) if event.actions else None
    if suggestions:
        # Google's Search Suggestions for a web_search answer, which its
        # grounding terms require showing with the answer.
        items.append({"type": "search_suggestions", "html": suggestions})
    if event.is_final_response() and event.content and event.content.parts:
        text = "\n".join(p.text for p in event.content.parts if p.text and not p.thought)
        if not text and responses:
            # AgentTool(skip_summarization=True) ends the turn with the raw
            # tool output (maps_lookup_json's JSON) instead of model text.
            text = "\n".join(
                json.dumps((r.response or {}).get("result", r.response), indent=2, ensure_ascii=False)
                for r in responses
            )
            text = f"```json\n{text}\n```"
        if text:
            items.append({"type": "message", "author": event.author, "text": text})
    return items


_STOPPED = {"error": "Stopped by the user before this tool finished. Nothing from it is available."}


async def _answer_stopped_calls(session) -> None:
    """Answers tool calls that a stopped turn left without a response.

    Stop cancels a turn after the model's tool call was saved but before the
    tool's result was, and Gemini rejects a history with an unanswered call.
    A short "stopped by the user" result lets the conversation carry on.
    """
    answered = {r.id for event in session.events for r in event.get_function_responses()}
    for event in list(session.events):
        pending = [c for c in event.get_function_calls() if c.id and c.id not in answered]
        if not pending:
            continue
        parts = [
            types.Part(function_response=types.FunctionResponse(id=c.id, name=c.name, response=_STOPPED))
            for c in pending
        ]
        await _sessions.append_event(session, Event(
            invocation_id=event.invocation_id,
            author=event.author,
            branch=event.branch,
            content=types.Content(role="user", parts=parts),
        ))


async def _chat_turn(req: ChatRequest):
    """Runs one chat turn with the root agent, yielding the session id, then
    tool calls, tool results, answers and errors as they happen.
    """
    session = None
    if req.session_id:
        session = await _sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=req.session_id)
    if session is None:
        session = await _sessions.create_session(app_name=APP_NAME, user_id=USER_ID)
    else:
        await _answer_stopped_calls(session)
    yield {"type": "session", "session_id": session.id}

    message = types.Content(role="user", parts=[types.Part(text=req.message)])
    async for event in _runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        for item in _chat_items(event):
            yield item
    yield {"type": "done"}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    """Streams one chat turn. Pass session_id to continue a conversation."""
    return _stream(_chat_turn(req), request)


# --- Surveys and checks -----------------------------------------------------------------

def _for_ui(result: dict) -> dict:
    """The pipeline result with file names instead of paths, and every price
    also given as read in full ("60" -> "IDR 60,000") when its currency is
    known. Prices are re-read here, so reports saved before the local
    currency was inferred display the same way as new ones.
    """
    summary = dict(result.get("summary") or {})
    is_product = summary.get("kind") == "product_survey"
    area_currency = prices.currency_for_address(summary.get("area"))

    def read(price: str | None, currency: str | None) -> dict:
        value, cur = prices.parse_price(price or "", currency)
        return {"price_value": value, "currency": cur, "price_read_as": prices.format_read_as(value, cur) if cur else None}

    places = []
    for record in result.get("places", []):
        record = dict(record)
        currency = prices.currency_for_address(record.get("address")) or area_currency
        if record.get("evidence"):
            # The place's country decides; a currency saved with the price is the fallback.
            record["evidence"] = [{**e, **read(e.get("price"), currency or e.get("currency"))} for e in record["evidence"]]
        if is_product:
            record.update(verdict.explain(record))  # current wording, also for older reports
        menu = record.get("menu")
        if menu and menu.get("items"):
            record["menu"] = {**menu, "items": [{**i, "price_read_as": read(i.get("price"), currency)["price_read_as"]} for i in menu["items"]]}
        places.append(record)

    if is_product:
        summary["price_summary"] = prices.summarize_prices(prices.survey_price_entries(places))

    files = {
        kind: os.path.basename(result[f"{kind}_path"])
        for kind in ("json", "csv", "docx")
        if result.get(f"{kind}_path")
    }
    return {"summary": summary, "places": places, "files": files}


async def _job(func, **kwargs):
    """Runs a pipeline function in a worker thread, yielding its progress
    events and then its result. Sets the cancel event when the stream ends
    early, so the pipeline stops starting new places.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()

    def progress(event: dict) -> None:
        """Hands a pipeline progress event from the worker thread to the stream."""
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "progress", **event})

    future = asyncio.ensure_future(asyncio.to_thread(func, progress=progress, cancel=cancel, **kwargs))
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, future}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            break
        while not queue.empty():
            yield queue.get_nowait()
        try:
            result = future.result()
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return
        yield {"type": "result", "data": _for_ui(result)}
    finally:
        cancel.set()


@app.post("/api/survey")
async def survey(req: SurveyRequest, request: Request) -> StreamingResponse:
    """Streams an area product survey: progress events, then the result."""
    return _stream(
        _job(
            survey_product,
            area_name=req.area,
            product=req.product,
            place_types=req.place_types,
            aliases=req.aliases,
            product_description=req.description,
            limit=req.limit,
            search_third_party=req.search_third_party,
        ),
        request,
    )


@app.post("/api/check")
async def check(req: CheckRequest, request: Request) -> StreamingResponse:
    """Streams a named-place check, or full menus when no product is given."""
    return _stream(
        _job(
            check_named_places,
            place_names=req.place_names,
            area_name=req.area,
            product=req.product,
            aliases=req.aliases,
            product_description=req.description,
            search_third_party=req.search_third_party,
        ),
        request,
    )


# --- Reports ------------------------------------------------------------------------

def _output_dir() -> Path:
    """The output folder, resolved to an absolute path."""
    return Path(config.OUTPUT_DIR).resolve()


def _report_info(path: Path) -> tuple[str, str | None]:
    """(kind, title) for a report. kind is "product_survey", "menu_check",
    "collection" or "file"; title is a readable name from the report itself.
    """
    if path.suffix != ".json":
        return "file", None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return "file", None
    if isinstance(data, list):
        return "collection", f"Collection: {path.stem.replace('_', ' ')}"
    if not isinstance(data, dict):
        return "file", None
    summary = data.get("summary") or {}
    kind = summary.get("kind", "file")
    names = summary.get("place_names") or []
    if kind == "product_survey":
        if not names:
            where = summary.get("area")
        elif len(names) <= 2:
            where = " and ".join(names)
        else:
            where = f"{len(names)} named places"
        return kind, f"{summary.get('product')} {'at' if names else 'in'} {where}"
    if kind == "menu_check":
        return kind, f"Menus: {', '.join(names[:3])}{' and more' if len(names) > 3 else ''}"
    return kind, None


@app.get("/api/files")
def list_files() -> list[dict]:
    """Reports in the output folder, newest first, with kind and title."""
    out = _output_dir()
    if not out.is_dir():
        return []
    files = [p for p in out.iterdir() if p.is_file() and p.suffix in _DOWNLOADABLE]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    listing = []
    for p in files:
        kind, title = _report_info(p)
        listing.append(
            {
                "name": p.name,
                "stem": p.stem,
                "format": p.suffix[1:],
                "size": p.stat().st_size,
                "modified": p.stat().st_mtime,
                "kind": kind,
                "title": title,
            }
        )
    return listing


def _report_path(name: str, suffixes: tuple[str, ...] = _DOWNLOADABLE) -> Path:
    """Resolves a report name inside the output folder, or raises 404. Any
    folder part of the name is dropped, so paths can't escape the folder.
    """
    base = os.path.basename(name)
    path = (_output_dir() / base).resolve()
    if not base.endswith(suffixes) or path.parent != _output_dir() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"No report named {base!r}.")
    return path


@app.get("/api/files/{name}")
def get_file(name: str, download: bool = True) -> FileResponse:
    """Downloads a report, or shows it in the browser with download=false."""
    path = _report_path(name)
    base = path.name
    media_type = mimetypes.guess_type(base)[0] or "application/octet-stream"
    if base.endswith(".csv"):
        media_type = "text/csv; charset=utf-8"
    return FileResponse(
        path,
        media_type=media_type,
        filename=base if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/reports/{name}")
def get_report(name: str) -> dict:
    """A saved survey or menu check in the same shape as a live result, or
    {"collection": [...]} for an area_collector run.
    """
    path = _report_path(name, (".json",))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{path.name} isn't valid JSON: {exc}") from None
    if isinstance(data, list):
        return {"collection": data}
    if isinstance(data, dict) and "summary" in data:
        return _for_ui({**data, "json_path": str(path)})
    raise HTTPException(status_code=422, detail=f"{path.name} isn't a report this UI can show.")


# --- Settings and page --------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    """Settings the UI shows in its header. Never includes key values."""
    return {
        "model": config.MODEL,
        "pipeline_model": config.PIPELINE_MODEL,
        "vertex_ai": os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE",
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        "location": os.environ.get("GOOGLE_CLOUD_LOCATION"),
        "maps_key": bool(config.GOOGLE_MAPS_API_KEY),
        "output_dir": str(_output_dir()),
        "max_places": min(product_survey.MAX_NAMED_PLACES, config.MAX_PLACES_PER_RUN),
        "max_places_per_run": config.MAX_PLACES_PER_RUN,
    }


@app.get("/")
def index() -> FileResponse:
    """The single-page frontend."""
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
