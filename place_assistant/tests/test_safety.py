"""Safety rules: robots.txt, secrets in errors, file names, sandboxed file
tools, blocked hosts, and how the Maps key is sent."""

import asyncio
import json
import traceback
import types as pytypes

import pytest
import requests

from place_assistant import config
from place_assistant.pipeline import export, geocode, places_client
from place_assistant.shared import web
from place_assistant.tools import convert_json_to_docx, fetch_rendered_page, read_output_file


def test_offline_tests_cannot_reach_the_network():
    import socket

    with pytest.raises(RuntimeError, match="Offline test"):
        socket.create_connection(("places.googleapis.com", 443), timeout=1)
    with pytest.raises(RuntimeError, match="Offline test"):
        requests.get("https://places.googleapis.com/v1/places:searchText", timeout=1)


# --- robots.txt -------------------------------------------------------------------

@pytest.fixture
def robots(monkeypatch):
    """robots(status, body) makes every robots.txt request return that."""
    web._robots_parser.cache_clear()

    def install(status=200, body="", error=None):
        def get(url, **kwargs):
            assert kwargs.get("timeout"), "robots.txt must be fetched with a timeout"
            assert kwargs["headers"]["User-Agent"] == config.USER_AGENT
            if error:
                raise error
            return pytypes.SimpleNamespace(status_code=status, text=body, raise_for_status=lambda: _raise(status))

        monkeypatch.setattr(web.requests, "get", get)

    yield install
    web._robots_parser.cache_clear()


def _raise(status):
    if status >= 400:
        raise requests.HTTPError(f"{status}")


def test_robots_rules_are_followed(robots):
    robots(body="User-agent: *\nDisallow: /private\n")
    assert web.robots_allows("https://a.com/menu")
    assert not web.robots_allows("https://a.com/private/menu")


@pytest.mark.parametrize(
    "status, error, allowed",
    [
        (404, None, True),  # no robots.txt: everything allowed
        (403, None, False),  # forbidden: nothing allowed
        (500, None, False),  # server error: err on the side of not fetching
        (200, requests.Timeout("slow"), False),  # timeout: not fetched
    ],
)
def test_robots_failures(robots, status, error, allowed):
    robots(status=status, error=error)
    assert web.robots_allows("https://b.com/") is allowed


@pytest.mark.parametrize(
    "status, error, expected",
    [
        (404, None, web.ALLOWED),
        (403, None, web.DISALLOWED),
        (500, None, web.UNREACHABLE),  # the site is down, not refusing robots
        (200, requests.ConnectionError("DNS"), web.UNREACHABLE),
        (200, requests.exceptions.SSLError("expired"), web.UNREACHABLE),
    ],
)
def test_robots_status_tells_a_dead_site_from_a_refusal(robots, status, error, expected):
    robots(status=status, error=error)
    assert web.robots_status("https://b.com/") == expected


@pytest.mark.parametrize(
    "url, host",
    [
        ("https://WWW.Alpha.com.:443/menu", "alpha.com"),
        ("http://alpha.com", "alpha.com"),
        ("https://maps.app.goo.gl/x", "maps.app.goo.gl"),
        ("not a url", ""),
    ],
)
def test_host_of(url, host):
    assert web.host_of(url) == host


# --- The Maps key never appears in errors ---------------------------------------------------

def test_geocode_error_hides_the_key(monkeypatch, maps_key):
    leak = requests.ConnectionError(f"Max retries exceeded with url: /geocode/json?address=x&key={maps_key}")
    monkeypatch.setattr(geocode.requests, "get", lambda *a, **k: (_ for _ in ()).throw(leak))
    with pytest.raises(RuntimeError) as info:
        geocode.geocode_area_bounds("Menteng")
    shown = str(info.value) + "".join(traceback.format_exception(info.value))
    assert maps_key not in shown


def test_geocode_reports_googles_reason(monkeypatch, maps_key):
    body = {"status": "REQUEST_DENIED", "error_message": "This API is not activated."}
    monkeypatch.setattr(geocode.requests, "get", lambda *a, **k: pytypes.SimpleNamespace(raise_for_status=lambda: None, json=lambda: body))
    with pytest.raises(ValueError, match="REQUEST_DENIED.*not activated"):
        geocode.geocode_area_bounds("Menteng")


def test_places_key_goes_in_a_header_not_the_url(monkeypatch, maps_key):
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return pytypes.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"places": []})

    monkeypatch.setattr(places_client.requests, "post", post)
    places_client.find_place("Eastern Promise", "Jakarta")
    assert maps_key not in seen["url"] and maps_key not in str(seen["body"])
    assert seen["headers"]["X-Goog-Api-Key"] == maps_key


def test_missing_key_gives_a_setup_error(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", None)
    with pytest.raises(RuntimeError, match="No Maps API key"):
        places_client.find_place("x", "y")


# --- Small areas are widened -------------------------------------------------------

def test_tiny_geocoded_area_is_widened(monkeypatch, maps_key):
    tiny = {"northeast": {"lat": -6.2764, "lng": 106.8128}, "southwest": {"lat": -6.2791, "lng": 106.8101}}
    body = {"status": "OK", "results": [{"geometry": {"viewport": tiny}}]}
    monkeypatch.setattr(geocode.requests, "get", lambda *a, **k: pytypes.SimpleNamespace(raise_for_status=lambda: None, json=lambda: body))
    box = geocode.geocode_area_bounds("Kemang, Jakarta")
    assert box["widened"] is True
    assert round((box["northeast"]["lat"] - box["southwest"]["lat"]) * 111.32, 1) == geocode.MIN_BOX_KM


def test_large_area_is_kept():
    big = {"northeast": {"lat": -6.1, "lng": 106.9}, "southwest": {"lat": -6.2, "lng": 106.8}}
    assert geocode._widen(big) is big


# --- File names and the output folder ------------------------------------------------------

@pytest.mark.parametrize(
    "parts, stem",
    [
        (("Jakarta Pusat, Indonesia", "cafe", "alcohol"), "jakarta_pusat_indonesia_cafe_alcohol"),
        (("Menteng / Jakarta", "cafe"), "menteng___jakarta_cafe"),  # no folder in the name
        (("../../etc", "x"), "etc_x"),  # leading dots and underscores are stripped
        (("", ""), "output"),
    ],
)
def test_safe_file_stem(parts, stem):
    assert export.safe_file_stem(*parts) == stem
    assert "/" not in stem and "\\" not in stem


@pytest.mark.parametrize("name", ["../../etc/passwd", "../.env", ".env", "/etc/hosts.csv", "missing.json"])
def test_file_tools_stay_in_the_output_folder(out_dir, monkeypatch, name):
    import place_assistant.tools.output_files as output_files

    monkeypatch.setattr(output_files, "_OUTPUT_DIR", str(out_dir))
    out_dir.mkdir()
    assert read_output_file(name)["status"] == "error"
    assert convert_json_to_docx(name)["status"] == "error"


def test_read_output_file_returns_pretty_json(out_dir, monkeypatch):
    import place_assistant.tools.output_files as output_files

    monkeypatch.setattr(output_files, "_OUTPUT_DIR", str(out_dir.resolve()))
    out_dir.mkdir()
    (out_dir / "x.json").write_text(json.dumps([{"name": "A"}]))
    result = read_output_file("x.json")
    assert result["status"] == "success" and '"name": "A"' in result["content"]


# --- The website reader tool refuses search engines and odd schemes --------------------------

@pytest.mark.parametrize(
    "url, message",
    [
        ("https://www.google.com/maps/place/x", "not allowed"),
        ("https://maps.google.co.id/x", "not allowed"),
        ("https://www.bing.com/search?q=x", "not allowed"),
        ("https://www.google.com:443/maps", "not allowed"),  # a port doesn't hide the host
        ("https://www.google.com./maps", "not allowed"),  # nor does a trailing dot
        ("https://maps.app.goo.gl/abc", "not allowed"),  # Maps short links
        ("https://gofood.co.id/jakarta/alpha", "not allowed"),
        ("https://www.instagram.com/alpha", "login-walled"),
        ("ftp://example.com/menu", "Unsupported URL scheme"),
        ("file:///etc/passwd", "Unsupported URL scheme"),
    ],
)
def test_fetch_rendered_page_refuses(url, message):
    result = asyncio.run(fetch_rendered_page(url))
    assert result["status"] == "error" and message in result["error_message"]


class _FakeBrowserPage:
    """Just enough of a Playwright page for fetch_rendered_page."""

    def __init__(self, final_url, text, links):
        self.url, self._text, self._links = final_url, text, links

    async def goto(self, url, **kwargs):
        pass

    async def wait_for_timeout(self, ms):
        pass

    async def inner_text(self, selector):
        return self._text

    async def eval_on_selector_all(self, selector, script):
        return self._links


@pytest.fixture
def browser(monkeypatch):
    """browser(final_url, text, links) makes the headless browser land on
    final_url with that content, and allows every robots.txt.
    """
    from place_assistant.tools import web_fetch

    def install(final_url, text="Menu " * 50, links=()):
        page = _FakeBrowserPage(final_url, text, list(links))

        class Browser:
            closed = False

            async def new_page(self, **kwargs):
                return page

            async def close(self):
                Browser.closed = True

        class Playwright:
            chromium = pytypes.SimpleNamespace(launch=lambda **kw: _done(Browser()))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(web_fetch, "async_playwright", Playwright)
        monkeypatch.setattr(web, "robots_status", lambda url: web.ALLOWED)
        return Browser

    return install


async def _done(value):
    return value


def test_fetch_keeps_links_of_the_site_it_was_redirected_to(browser):
    Browser = browser("https://www.alpha.com/", links=[
        {"text": "Menu", "href": "https://www.alpha.com/menu"},
        {"text": "Elsewhere", "href": "https://other.com/menu"},
    ])
    result = asyncio.run(fetch_rendered_page("https://alpha.com"))
    assert result["status"] == "success" and result["url"] == "https://www.alpha.com/"
    assert [link["href"] for link in result["links"]] == ["https://www.alpha.com/menu"]
    assert Browser.closed


def test_fetch_refuses_a_redirect_to_a_blocked_site(browser):
    browser("https://www.google.com/maps/place/alpha")
    result = asyncio.run(fetch_rendered_page("https://alpha.example/"))
    assert result["status"] == "error" and "redirected to" in result["error_message"]
    assert "not allowed" in result["error_message"]
