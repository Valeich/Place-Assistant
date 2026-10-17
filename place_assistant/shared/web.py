"""Rules shared by every website fetcher (tools/web_fetch.py and
pipeline/site_reader.py).

Compliance boundary: this project only fetches a business's OWN public
website. It never fetches search engines, Google pages, social networks or
delivery apps, it checks robots.txt before every fetch, and it identifies
itself honestly.
"""

import functools
import urllib.parse
import urllib.robotparser

import requests

from ..config import USER_AGENT

_ROBOTS_TIMEOUT_S = 10

# Login-walled social profiles. Many small businesses list one as their website.
SOCIAL_HOSTS = (
    "instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com", "x.com",
    "threads.net", "youtube.com", "wa.me", "whatsapp.com",
)
# Hosts no fetcher ever reads. Delivery-app prices are looked up through
# Google Search grounding instead (pipeline/product_check.py).
NEVER_FETCH_HOSTS = SOCIAL_HOSTS + (
    "google.com", "google.co.id", "goo.gl", "bing.com",
    "gofood.co.id", "gojek.com", "grab.com", "shopee.co.id", "shopeefood.co.id",
    "doordash.com", "ubereats.com", "tokopedia.com",
)

ALLOWED, DISALLOWED, UNREACHABLE = "allowed", "disallowed", "unreachable"


def host_of(url: str) -> str:
    """The lowercase host of url without "www.", a port or a trailing dot,
    e.g. "https://WWW.Alpha.com.:443/menu" -> "alpha.com".
    """
    host = (urllib.parse.urlparse(url).hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host


def host_in(host: str, domains: tuple[str, ...]) -> bool:
    """True if host is one of domains or a subdomain of one."""
    return any(host == d or host.endswith("." + d) for d in domains)


@functools.lru_cache(maxsize=512)
def _robots_parser(origin: str) -> urllib.robotparser.RobotFileParser:
    """Reads and caches one site's robots.txt. A failed read raises and is
    not cached, so a passing network error doesn't block the site for good.

    Uses requests with a timeout instead of RobotFileParser.read(), which
    opens the URL with no timeout and can hang forever on a server that
    accepts the connection but never answers. Status codes are handled the
    same way read() does: 401/403 disallow everything, other 4xx allow
    everything, and 5xx counts as a failed read.
    """
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{origin}/robots.txt")
    resp = requests.get(
        f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=_ROBOTS_TIMEOUT_S
    )
    if resp.status_code in (401, 403):
        parser.disallow_all = True
    elif 400 <= resp.status_code < 500:
        parser.allow_all = True
    else:
        resp.raise_for_status()
        parser.parse(resp.text.splitlines())
    return parser


def robots_status(url: str) -> str:
    """ALLOWED or DISALLOWED by the site's robots.txt for USER_AGENT, or
    UNREACHABLE when robots.txt couldn't be read at all (DNS, SSL, timeout
    or a server error), which usually means the site itself is down.
    Nothing may be fetched unless this returns ALLOWED.

    Blocking network I/O on the first call per site.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        parser = _robots_parser(f"{parsed.scheme}://{parsed.netloc}")
    except Exception:
        return UNREACHABLE
    return ALLOWED if parser.can_fetch(USER_AGENT, url) else DISALLOWED


def robots_allows(url: str) -> bool:
    """True if robots.txt was read and lets USER_AGENT fetch url."""
    return robots_status(url) == ALLOWED
