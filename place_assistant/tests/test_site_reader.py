"""Reading a business's website (pipeline/site_reader.py). Network calls are faked."""

import pytest
import requests

from place_assistant.pipeline import site_reader
from place_assistant.shared import web


def _links(*pairs):
    return [{"text": text, "href": href} for text, href in pairs]


def test_menu_links_from_a_normal_site():
    follow, unreadable = site_reader._menu_links(
        _links(
            ("Home", "https://www.alpha.com/"),
            ("Our Menu", "https://alpha.com/menu"),
            ("Food menu (PDF)", "https://static.wixstatic.com/ugd/menu.pdf"),  # PDF on a CDN: allowed
            ("Partner menu", "https://other.com/menu"),  # other site, not a PDF: skipped
            ("Order on GoFood", "https://gofood.co.id/jakarta/alpha-menu"),  # delivery app: reported
            ("KITA BAR Menu", "https://drive.google.com/file/d/1/view"),  # Google Drive: reported
            ("Instagram menu", "https://instagram.com/alpha"),  # social: neither
            ("About us", "https://alpha.com/about"),  # not a menu
        ),
        "https://www.alpha.com/",
    )
    assert follow == ["https://static.wixstatic.com/ugd/menu.pdf", "https://alpha.com/menu"]
    assert unreadable == ["https://gofood.co.id/jakarta/alpha-menu", "https://drive.google.com/file/d/1/view"]


def test_menu_links_from_a_link_in_bio_page_may_leave_the_site():
    follow, _ = site_reader._menu_links(
        _links(("MENU", "https://alpha-cafe.com/menu"), ("WhatsApp", "https://wa.me/62")),
        "https://linktr.ee/alpha",
    )
    assert follow == ["https://alpha-cafe.com/menu"]


@pytest.mark.parametrize(
    "text, is_error",
    [
        ("404\nLost?\nThe page you are looking for seems to have drifted away.", True),
        ("Error 404. Page not found.", True),
        ("Menu\nNasi Goreng 404 Rp 25.000\nOur story", False),  # "404" alone isn't enough
        ("Page not found" + " menu" * 1000, False),  # long pages aren't error pages
    ],
)
def test_error_page_detection(text, is_error):
    assert site_reader._looks_like_error_page(text) is is_error


@pytest.mark.parametrize(
    "url, status",
    [
        (None, "no_website"),
        ("https://www.instagram.com/alpha", "social_media_only"),
        ("http://facebook.com/alpha", "social_media_only"),
        ("https://gofood.co.id/alpha", "not_allowed"),
        ("https://maps.google.com/x", "not_allowed"),
    ],
)
def test_sites_that_are_never_fetched(url, status):
    assert site_reader.read_business_site(url)["status"] == status


class _Resp:
    """A fake streamed requests response; a redirect when location is set."""

    def __init__(self, body=b"", url="https://alpha.com/", status=200, content_type="text/html",
                 location=None, declared=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.url, self.status_code, self.is_redirect = url, status, location is not None
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        if declared:
            self.headers["Content-Length"] = str(declared)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def iter_content(self, chunk):
        for i in range(0, len(self.body), chunk):
            yield self.body[i : i + chunk]


@pytest.fixture
def site(monkeypatch):
    """site(pages, robots=..., render=...) serves pages {url: _Resp, str or
    exception} to site_reader and returns the list of URLs fetched.
    """
    fetched = []

    def install(pages, robots=lambda url: web.ALLOWED, render=lambda url: ("", [], url)):
        def get(url, **kwargs):
            assert kwargs.get("timeout") and kwargs.get("allow_redirects") is False and kwargs.get("stream")
            fetched.append(url)
            page = pages[url]
            if isinstance(page, Exception):
                raise page
            return page if isinstance(page, _Resp) else _Resp(page, url=url)

        monkeypatch.setattr(site_reader.requests, "get", get)
        monkeypatch.setattr(web, "robots_status", robots)
        monkeypatch.setattr(site_reader, "render_text", render)
        return fetched

    return install


WELCOME = "<p>" + "Welcome to Alpha. Great coffee. " * 10 + "</p>"


def test_reads_the_site_and_follows_a_menu_link(site):
    site({
        "https://alpha.com/": WELCOME + '<a href="/menu">Menu</a>',
        "https://alpha.com/menu": "<h1>Menu</h1><p>" + "Kopi Susu 25k. " * 20 + "</p>",
    })
    result = site_reader.read_business_site("https://alpha.com/")
    assert result["status"] == "ok"
    assert [p["url"] for p in result["pages"]] == ["https://alpha.com/", "https://alpha.com/menu"]
    assert "Kopi Susu" in result["pages"][1]["text"]


def test_the_whole_page_is_kept_for_the_product_search(site):
    # Drinks usually come last on a long one-page menu.
    site({"https://alpha.com/": "<p>" + "Nasi Goreng Rp 25.000. " * 1000 + "AQUA 600ml Rp 8.000</p>"})
    text = site_reader.read_business_site("https://alpha.com/")["pages"][0]["text"]
    assert len(text) > site_reader.MAX_PAGE_CHARS and text.endswith("AQUA 600ml Rp 8.000")


def test_tiny_pages_are_rendered_in_a_browser(site):
    site({"https://alpha.com/": "<div id=app></div>"}, render=lambda url: ("Rendered menu " * 30, [], url))
    result = site_reader.read_business_site("https://alpha.com/")
    assert result["pages"][0]["text"].startswith("Rendered menu")


def test_robots_refusal_is_respected(site):
    fetched = site({}, robots=lambda url: web.DISALLOWED)
    assert site_reader.read_business_site("https://alpha.com/")["status"] == "blocked_by_robots"
    assert fetched == []


def test_an_unreachable_site_failed_to_load_rather_than_refused(site):
    # A dead site (DNS, SSL, timeout) isn't a robots.txt refusal.
    fetched = site({}, robots=lambda url: web.UNREACHABLE)
    result = site_reader.read_business_site("https://alpha.com/")
    assert result["status"] == "fetch_failed" and "couldn't reach alpha.com" in result["error"]
    assert fetched == []


def test_a_bot_check_page_is_not_a_read_website(site):
    challenge = "Just a moment...\nChecking if the site connection is secure\n" + "alpha.com needs to review the security of your connection before proceeding. " * 2
    site({"https://alpha.com/": _Resp(status=403)}, render=lambda url: (challenge, [], url))
    result = site_reader.read_business_site("https://alpha.com/")
    assert result["status"] == "fetch_failed" and result["pages"] == []


def test_a_rendered_page_with_real_content_replaces_a_refused_plain_fetch(site):
    site({"https://alpha.com/": _Resp(status=403)}, render=lambda url: ("Our menu: Kopi Susu 25k. " * 20, [], url))
    assert site_reader.read_business_site("https://alpha.com/")["status"] == "ok"


def test_soft_404_is_a_broken_link(site):
    page = "<p>404</p><p>The page you are looking for seems to have drifted away. " + "x " * 120 + "</p>"
    site({"https://alpha.com/bar": page})
    assert site_reader.read_business_site("https://alpha.com/bar")["status"] == "broken_link"


def test_failed_menu_page_is_reported_not_dropped(site):
    site({"https://alpha.com/": WELCOME + '<a href="/menu">Menu</a>', "https://alpha.com/menu": TimeoutError("slow server")})
    result = site_reader.read_business_site("https://alpha.com/")
    assert result["menu_links_not_read"] == ["https://alpha.com/menu"]


def test_menu_links_are_read_from_the_redirected_site(site):
    fetched = site({
        "http://alpha.com": _Resp(location="https://www.alpha.com/", status=301),
        "https://www.alpha.com/": _Resp(WELCOME + '<a href="/menu">Menu</a>', url="https://www.alpha.com/"),
        "https://www.alpha.com/menu": "<p>" + "Kopi Susu 25k. " * 20 + "</p>",
    })
    result = site_reader.read_business_site("alpha.com")
    assert [p["url"] for p in result["pages"]] == ["http://alpha.com", "https://www.alpha.com/menu"]
    assert fetched == ["http://alpha.com", "https://www.alpha.com/", "https://www.alpha.com/menu"]


def test_a_menu_link_redirecting_to_google_drive_is_reported_not_fetched(site):
    fetched = site({
        "https://alpha.com/": WELCOME + '<a href="/menu">Menu</a>',
        "https://alpha.com/menu": _Resp(location="https://drive.google.com/file/d/1/view", status=302),
    })
    result = site_reader.read_business_site("https://alpha.com/")
    assert "https://drive.google.com/file/d/1/view" not in fetched
    assert result["menu_links_not_read"] == ["https://alpha.com/menu"]


def test_a_website_redirecting_to_instagram_is_social_media(site):
    fetched = site({"https://alpha.com/": _Resp(location="https://www.instagram.com/alpha/", status=301)})
    assert site_reader.read_business_site("https://alpha.com/")["status"] == "social_media_only"
    assert fetched == ["https://alpha.com/"]


def test_endless_redirects_fail_to_load(site):
    site({"https://alpha.com/": _Resp(location="https://alpha.com/", status=302)})
    assert site_reader.read_business_site("https://alpha.com/")["status"] == "fetch_failed"


def test_a_pdf_behind_a_plain_link_is_downloaded_once(site, monkeypatch):
    monkeypatch.setattr(site_reader, "_transcribe_pdf", lambda data: f"PDF MENU ({len(data)} bytes) " * 20)
    fetched = site({"https://alpha.com/menu": _Resp(b"%PDF-1.4 menu", content_type="application/pdf")})
    page = site_reader._read_page("https://alpha.com/menu")
    assert page["kind"] == "pdf" and page["text"].startswith("PDF MENU (13 bytes)")
    assert fetched == ["https://alpha.com/menu"]


def test_pdf_download_refuses_declared_large_files(site):
    site({"https://alpha.com/menu.pdf": _Resp(b"x" * 10, declared=50 * 1024 * 1024)})
    with pytest.raises(ValueError, match="larger than"):
        site_reader._download_pdf("https://alpha.com/menu.pdf")


def test_pdf_download_stops_when_it_grows_too_large(site):
    site({"https://alpha.com/menu.pdf": _Resp(b"x" * (11 * 1024 * 1024))})
    with pytest.raises(ValueError, match="larger than"):
        site_reader._download_pdf("https://alpha.com/menu.pdf")
