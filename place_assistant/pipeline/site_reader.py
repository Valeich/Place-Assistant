"""Reads a business's own website for menu content: the page Google lists as
its website, plus up to two menu-looking pages it links to, including PDF
menus.

Each page is first fetched with a plain HTTP GET. If that returns almost no
text, the page is probably a JavaScript app shell (Wix, Squarespace, React),
so it is rendered in headless Chromium instead. PDF menus are transcribed by
Gemini, which also reads scanned menus.

Compliance boundary, shared with shared/web.py: robots.txt is checked for
every URL and every redirect hop, requests carry an honest User-Agent, and
this module never fetches search engines, Google pages, social networks or
delivery apps, even when a link redirects there. Social profiles such as
Instagram are login-walled, and delivery-app prices are looked up through
Search grounding instead (see product_check.py).
"""

import concurrent.futures
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup
from google.genai import types
from playwright.sync_api import sync_playwright

from .. import config
from ..shared import web
from ..shared.llm import get_client, low_thinking
from ..shared.web import host_in, host_of

MAX_PAGE_CHARS = 15000  # per page in menu_extractor's prompt; products are searched in the full text
MAX_EXTRA_PAGES = 2  # menu sub-pages followed from the first page

_MIN_STATIC_TEXT_CHARS = 200  # below this, a plain GET probably hit a JS app shell
_RENDER_TIMEOUT_S = 45  # whole render, browser launch and queueing included
_PAGE_TIMEOUT_MS = 20000
_RENDER_SETTLE_MS = 2500  # let client-side rendering fill the page in
_GET_TIMEOUT_S = 10  # per connection and per read
_MAX_REDIRECTS = 5
_MAX_HTML_BYTES = 5 * 1024 * 1024
_HTML_DEADLINE_S = 30
_MAX_PDF_BYTES = 10 * 1024 * 1024
_PDF_DEADLINE_S = 90  # whole download; requests' timeout only bounds each read

_SOCIAL_HOSTS = web.SOCIAL_HOSTS
_NEVER_FETCH_HOSTS = web.NEVER_FETCH_HOSTS
# Link-in-bio pages stand in for a website. Their menu links usually point
# to other hosts, so those are followed too.
_LINK_IN_BIO_HOSTS = (
    "linktr.ee", "msha.ke", "bio.link", "lynk.id", "beacons.ai", "taplink.cc",
    "linkin.bio", "campsite.bio", "s.id", "heylink.me",
)
_MENU_WORDS = (
    "menu", "food", "drink", "beverage", "minuman", "makanan", "harga", "price",
    "carte", "wine", "cocktail",
)

# Why a page wasn't read on purpose (_read_page's "refused").
SOCIAL, NOT_ALLOWED, ROBOTS, UNREACHABLE = "social", "not_allowed", "robots", "unreachable"

# Playwright's sync API refuses to run on a thread that has a running asyncio
# event loop, which is the thread ADK calls synchronous tools on. Worker
# threads have no loop, so every render runs here.
_render_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="site-render")


class _Refused(Exception):
    """A URL, or a redirect hop, that this module must not fetch."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _host(url: str) -> str:
    """The lowercase host of url without "www.", a port or a trailing dot."""
    return host_of(url)


def _host_in(host: str, domains: tuple[str, ...]) -> bool:
    """True if host is one of domains or a subdomain of one."""
    return host_in(host, domains)


def _host_refusal(url: str) -> _Refused | None:
    """A _Refused for a social network or never-fetched host, else None.
    Needs no network.
    """
    host = host_of(url)
    if host_in(host, _SOCIAL_HOSTS):
        return _Refused(SOCIAL, f"{host} is login-walled")
    if host_in(host, _NEVER_FETCH_HOSTS):
        return _Refused(NOT_ALLOWED, f"{host} is never fetched")
    return None


def _refusal(url: str) -> _Refused | None:
    """Why url must not be fetched, or None: a blocked host, a robots.txt
    refusal, or a site whose robots.txt can't be reached at all.
    """
    refused = _host_refusal(url)
    if refused:
        return refused
    robots = web.robots_status(url)
    if robots == web.DISALLOWED:
        return _Refused(ROBOTS, "blocked by robots.txt")
    if robots == web.UNREACHABLE:
        return _Refused(UNREACHABLE, f"couldn't reach {host_of(url)}")
    return None


_ERROR_PAGE = re.compile(
    r"page (you are|you're) looking for|page not found|not be found|doesn'?t exist|does not exist|"
    r"no longer available|halaman tidak ditemukan",
    re.I,
)


def _looks_like_error_page(text: str) -> bool:
    """True for a short page that says it doesn't exist. Many sites answer a
    dead link with HTTP 200 and a "404" message instead of a real 404.
    """
    head = text[:2000]
    return len(text) < 3000 and bool(re.search(r"\b404\b", head)) and bool(_ERROR_PAGE.search(head))


_BOT_CHECK = re.compile(
    r"just a moment|checking (your browser|if the site connection is secure)|"
    r"verify(ing)? (that )?you are (a )?human|review the security of your connection|"
    r"enable javascript and cookies to continue|attention required|ddos protection by|"
    r"access denied|captcha",
    re.I,
)


def _looks_like_bot_check(text: str) -> bool:
    """True for a short bot-check or access-denied page, such as Cloudflare's
    "Just a moment..." screen. It says nothing about the business, so it must
    never count as a website that was read.
    """
    return len(text) < 3000 and bool(_BOT_CHECK.search(text))


def _is_pdf_url(url: str) -> bool:
    """True if the URL path ends in ".pdf"."""
    return urllib.parse.urlparse(url).path.lower().endswith(".pdf")


def _parse_html(body: bytes, charset: str | None, base_url: str) -> tuple[str, list[dict]]:
    """Returns an HTML page's visible text, one block per line, with
    <script> and <style> content removed, and every <a href> as
    {text, href} with href made absolute.
    """
    soup = BeautifulSoup(body, "html.parser", from_encoding=charset)
    links = [
        {"text": a.get_text(" ", strip=True), "href": urllib.parse.urljoin(base_url, a["href"])}
        for a in soup.find_all("a", href=True)
    ]
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True), links


def _charset(resp) -> str | None:
    """The charset named in the Content-Type header, or None. requests'
    own guess of ISO-8859-1 for text/html is often wrong, so it isn't used.
    """
    match = re.search(r"charset=([\w.:-]+)", resp.headers.get("Content-Type", ""), re.I)
    return match.group(1) if match else None


def _render_with_browser(url: str) -> tuple[str, list[dict], str]:
    """Renders url in headless Chromium and returns (visible text, links,
    the URL the page ended on). Only call it through _render_executor, never
    from a thread with a running asyncio event loop.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.USER_AGENT)
            page.set_default_timeout(_PAGE_TIMEOUT_MS)
            page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(_RENDER_SETTLE_MS)
            text = page.inner_text("body")
            links = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => ({text: (e.innerText || '').trim(), href: e.href}))"
            )
            final_url = page.url
        finally:
            browser.close()
    return (text or "").strip(), links or [], final_url or url


def render_text(url: str) -> tuple[str, list[dict], str]:
    """Renders url off-thread with a hard timeout. Returns (text, links,
    final URL), or ("", [], url) on any failure, because rendering is only a
    fallback for the plain fetch.
    """
    future = _render_executor.submit(_render_with_browser, url)
    try:
        return future.result(timeout=_RENDER_TIMEOUT_S)
    except Exception:
        future.cancel()  # a render still queued for a browser never starts
        return "", [], url


def _transcribe_pdf(data: bytes) -> str:
    """Has Gemini copy a PDF menu's text, item names and prices exactly as
    printed. Works for scanned menus too.
    """
    response = get_client().models.generate_content(
        model=config.PIPELINE_MODEL,
        contents=[
            types.Part.from_bytes(data=data, mime_type="application/pdf"),
            "Transcribe the text of this document exactly as printed, keeping "
            "each menu item on the same line as its price. Do not add, translate, "
            "summarize or correct anything.",
        ],
        config=types.GenerateContentConfig(thinking_config=low_thinking(config.PIPELINE_MODEL)),
    )
    return (response.text or "").strip()


def _get(url: str) -> requests.Response:
    """Starts a streamed GET of url and returns the response once it isn't a
    redirect. Redirects are followed one hop at a time, and every hop must
    pass _refusal, so a menu link that redirects to Google Drive or a
    delivery app is never fetched. Raises _Refused, or a requests error for
    a failed request or an error status. The caller closes the response.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        resp = requests.get(
            url, headers={"User-Agent": config.USER_AGENT}, timeout=_GET_TIMEOUT_S,
            stream=True, allow_redirects=False,
        )
        if not resp.is_redirect:
            try:
                resp.raise_for_status()
            except Exception:
                resp.close()
                raise
            return resp
        resp.close()
        url = urllib.parse.urljoin(url, resp.headers.get("Location", ""))
        refused = _refusal(url)
        if refused:
            raise refused
    raise requests.TooManyRedirects(f"more than {_MAX_REDIRECTS} redirects")


def _read_body(resp, max_bytes: int, deadline_s: float) -> bytes:
    """Reads a streamed response, giving up past max_bytes or deadline_s.
    Slow hosts can otherwise trickle a large file for minutes.
    """
    limit = f"larger than {max_bytes // (1024 * 1024)} MB"
    if int(resp.headers.get("Content-Length") or 0) > max_bytes:
        raise ValueError(f"File {limit}")
    started = time.monotonic()
    chunks, size = [], 0
    for chunk in resp.iter_content(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"File {limit}")
        if time.monotonic() - started > deadline_s:
            raise TimeoutError(f"Download took over {deadline_s}s")
    return b"".join(chunks)


def _download_pdf(url: str) -> bytes:
    """Downloads a PDF, giving up past _MAX_PDF_BYTES or _PDF_DEADLINE_S."""
    with _get(url) as resp:
        return _read_body(resp, _MAX_PDF_BYTES, _PDF_DEADLINE_S)


def _read_page(url: str) -> dict:
    """Fetches one URL. Returns {"url", "final_url", "kind", "text", "links",
    "error", "refused"}. final_url is where redirects ended. kind is "html" or
    "pdf". error is None on success. refused says why the page wasn't
    fetched on purpose: SOCIAL, NOT_ALLOWED, ROBOTS or UNREACHABLE.
    """
    page = {
        "url": url, "final_url": url, "kind": "pdf" if _is_pdf_url(url) else "html",
        "text": "", "links": [], "error": None, "refused": None,
    }
    refused = _refusal(url)
    if refused:
        page["refused"], page["error"] = refused.kind, str(refused)
        return page

    try:
        with _get(url) as resp:
            page["final_url"] = resp.url or url
            if page["kind"] == "pdf" or "application/pdf" in resp.headers.get("Content-Type", ""):
                # Also catches a PDF behind a URL without ".pdf".
                page["kind"] = "pdf"
                page["text"] = _transcribe_pdf(_read_body(resp, _MAX_PDF_BYTES, _PDF_DEADLINE_S))
                return page
            body = _read_body(resp, _MAX_HTML_BYTES, _HTML_DEADLINE_S)
            page["text"], page["links"] = _parse_html(body, _charset(resp), page["final_url"])
    except _Refused as exc:
        page["refused"], page["error"] = exc.kind, str(exc)
        return page
    except Exception as exc:
        page["error"] = f"{type(exc).__name__}: {exc}"[:300]

    if page["kind"] == "html" and len(page["text"]) < _MIN_STATIC_TEXT_CHARS:
        text, links, final_url = render_text(page["final_url"])
        # After a failed fetch (often a 403 from a bot filter), only a page
        # with real content may replace it.
        usable = len(text) >= _MIN_STATIC_TEXT_CHARS or (not page["error"] and len(text) > len(page["text"]))
        if usable and not _looks_like_bot_check(text) and not _host_refusal(final_url):
            page.update(text=text, links=links, final_url=final_url, error=None)
    if page["text"] and _looks_like_bot_check(page["text"]):
        page.update(text="", links=[], error="the site showed a bot check instead of its content")
    return page


def _menu_links(links: list[dict], start_url: str, requested_url: str | None = None) -> tuple[list[str], list[str]]:
    """Picks menu-looking links from the page at start_url, the URL the page
    actually ended on after redirects. Returns (to_follow, not_readable).

    to_follow holds up to MAX_EXTRA_PAGES links. From a normal website only
    same-site links are followed, plus PDF menus on any host, since sites
    often keep PDFs on a CDN. From a link-in-bio page any host is allowed.

    not_readable holds up to 3 menu links on hosts this project never
    fetches, such as a Google Drive PDF or a GoFood page. They are reported
    so a person can open them; social profiles are left out.
    """
    start_host = _host(start_url)
    from_link_in_bio = _host_in(start_host, _LINK_IN_BIO_HOSTS)
    skip = {u.rstrip("/") for u in (start_url, requested_url) if u}
    scored, not_readable = {}, []
    for link in links:
        href = (link.get("href") or "").split("#")[0]
        if not href.startswith(("http://", "https://")) or href.rstrip("/") in skip:
            continue
        host = _host(href)
        haystack = f"{link.get('text', '')} {urllib.parse.urlparse(href).path}".lower()
        score = (3 if "menu" in haystack else 0) + sum(1 for w in _MENU_WORDS[1:] if w in haystack)
        if not score:
            continue
        if _host_in(host, _NEVER_FETCH_HOSTS):
            if not _host_in(host, _SOCIAL_HOSTS) and href not in not_readable:
                not_readable.append(href)
            continue
        is_pdf = _is_pdf_url(href)
        if host != start_host and not (is_pdf or from_link_in_bio):
            continue
        scored[href] = max(scored.get(href, 0), score + (1 if is_pdf else 0))
    return sorted(scored, key=lambda h: -scored[h])[:MAX_EXTRA_PAGES], not_readable[:3]


def read_business_site(url: str | None) -> dict:
    """Reads a place's website and its menu sub-pages.

    Returns {"status", "pages", "error", "menu_links_not_read"}. pages is a
    list of {"url", "kind", "text"} with each page's full visible text.
    menu_links_not_read lists menu links that couldn't be read: menu pages
    that failed to load, and links to hosts this project never fetches,
    such as Google Drive or a delivery app, including links that redirect
    there.
    status is one of:
      "ok"                 at least one page had text
      "no_website"         Google lists no website
      "social_media_only"  the website is, or redirects to, a social profile such as Instagram
      "not_allowed"        the website is, or redirects to, a host this project never fetches
      "blocked_by_robots"  robots.txt disallows it
      "broken_link"        the listed page says it doesn't exist (404)
      "fetch_failed"       the site couldn't be reached or loaded, showed a
                           bot check, or had no text
    """
    if not url:
        return {"status": "no_website", "pages": [], "error": None, "menu_links_not_read": []}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    first = _read_page(url)
    refused_status = {SOCIAL: "social_media_only", NOT_ALLOWED: "not_allowed", ROBOTS: "blocked_by_robots"}
    if first["refused"] in refused_status:
        return {"status": refused_status[first["refused"]], "pages": [], "error": first["error"], "menu_links_not_read": []}
    if not first["text"]:
        return {"status": "fetch_failed", "pages": [], "error": first["error"] or "page had no visible text", "menu_links_not_read": []}

    if _looks_like_error_page(first["text"]):
        return {"status": "broken_link", "pages": [], "error": "the listed page shows a 404 error", "menu_links_not_read": []}

    pages = [first]
    to_follow, not_readable = _menu_links(first["links"], first["final_url"], url)
    if to_follow:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(to_follow)) as pool:
            for sub in pool.map(_read_page, to_follow):
                if sub["text"] and not _looks_like_error_page(sub["text"]):
                    pages.append(sub)
                elif sub["error"] and sub["refused"] not in (ROBOTS, SOCIAL):
                    # A menu page that failed to load, or that leads to a host
                    # this project never fetches: report it rather than letting
                    # the place look like it has no menu.
                    not_readable.insert(0, sub["url"])
    return {
        "status": "ok",
        "pages": [{"url": p["url"], "kind": p["kind"], "text": p["text"]} for p in pages],
        "error": None,
        "menu_links_not_read": not_readable,
    }
