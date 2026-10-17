"""fetch_rendered_page tool: loads a business's own website in headless
Chromium so JavaScript-rendered menus and prices are visible, which a plain
HTTP GET would miss.

Uses Playwright's async API, because the sync API refuses to run inside ADK's
asyncio event loop. See shared/web.py for the compliance rules.
"""

import asyncio
import urllib.parse

from playwright.async_api import async_playwright

from ..config import USER_AGENT
from ..shared import web

_MAX_TEXT_CHARS = 15000  # keep the extracted text within a sane token budget
_MAX_LINKS = 40
_PAGE_TIMEOUT_MS = 20000
_RENDER_SETTLE_MS = 2500  # give client-side rendering a moment to fill the page in


def _out_of_scope(host: str) -> str | None:
    """Why this tool may not read host, or None when it may."""
    if web.host_in(host, web.SOCIAL_HOSTS):
        return f"{host} is a login-walled social network page, which this tool can't read."
    if web.host_in(host, web.NEVER_FETCH_HOSTS):
        return (
            f"Fetching {host} is not allowed — search engines, Google Maps pages "
            "and delivery apps are out of scope for this tool. Use it only on a "
            "business's own website."
        )
    return None


async def _robots_refusal(url: str) -> str | None:
    """Why robots.txt stops this fetch, or None when it allows it. The
    check does blocking network I/O, so it runs off the event loop.
    """
    status = await asyncio.to_thread(web.robots_status, url)
    host = web.host_of(url)
    if status == web.DISALLOWED:
        return f"robots.txt for {host} disallows fetching {url}."
    if status == web.UNREACHABLE:
        return f"Couldn't reach {host} to read its robots.txt, so {url} wasn't fetched."
    return None


async def fetch_rendered_page(url: str) -> dict:
    """Fetches a business's own public web page in a headless browser and
    returns its visible text, including content that only appears after
    JavaScript renders (menus, prices, opening promos, etc.).

    Use this when a place's website URL is already known (e.g. it appeared
    in a specialist agent's answer) and the user wants details that live on
    that site — like actual menu items and prices — which Google Maps and
    Search summaries don't include. Only use it on the business's own
    website; never on google.com/maps or search-engine pages.

    The result also includes the page's links. If the text you need lives on
    a sub-page (e.g. the homepage shows a "Menu" link), call this tool again
    with that link's href — follow at most one or two such hops, and only
    within the same website.

    Args:
        url: Full http(s) URL of the page to fetch, e.g.
            "https://anomalicoffee.com/menu".

    Returns:
        dict: {"status": "success", "url": str, "text": str,
            "links": [{"text": str, "href": str}, ...]} with the rendered
            page's visible text (truncated to a sane length) and its
            same-site links. url is the page actually read, after any
            redirect. On failure, {"status": "error", "error_message": str}
            if the URL is disallowed (robots.txt, blocked host, bad scheme),
            redirects somewhere disallowed, or the fetch failed. Relay errors
            honestly rather than inventing content.
    """
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return {"status": "error", "error_message": f"Unsupported URL scheme: {url}"}
    host = web.host_of(url)
    refusal = _out_of_scope(host) or await _robots_refusal(url)
    if refusal:
        return {"status": "error", "error_message": refusal}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=USER_AGENT)
                await page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                await page.wait_for_timeout(_RENDER_SETTLE_MS)
                text = await page.inner_text("body")
                raw_links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({text: (e.innerText || '').trim(), href: e.href}))",
                )
                final_url = page.url or url
            finally:
                await browser.close()
    except Exception as exc:
        return {"status": "error", "error_message": f"Failed to fetch {url}: {exc}"}

    # A redirect can land somewhere this tool may not read, e.g. a
    # maps.app.goo.gl short link or a site that forwards to Instagram.
    final_host = web.host_of(final_url)
    if final_host != host:
        refusal = _out_of_scope(final_host) or await _robots_refusal(final_url)
        if refusal:
            return {"status": "error", "error_message": f"{url} redirected to {final_url}. {refusal}"}

    text = (text or "").strip()
    if not text:
        return {"status": "error", "error_message": f"Page at {final_url} rendered no visible text."}

    # Same-site links only (sub-page hops like "Menu"), deduped and capped —
    # off-site links invite exactly the drift the blocklist exists to stop.
    # "Same site" means the page actually read, so a redirect from
    # alpha.com to www.alpha.com keeps its links.
    links = []
    seen = set()
    for link in raw_links:
        href = (link.get("href") or "").split("#")[0]
        if not href.startswith(("http://", "https://")) or web.host_of(href) != final_host:
            continue
        if href in seen or href.rstrip("/") in (url.rstrip("/"), final_url.rstrip("/")):
            continue
        seen.add(href)
        links.append({"text": link.get("text", "")[:80], "href": href})
        if len(links) >= _MAX_LINKS:
            break

    return {"status": "success", "url": final_url, "text": text[:_MAX_TEXT_CHARS], "links": links}
