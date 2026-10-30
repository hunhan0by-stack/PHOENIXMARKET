"""
The one check every itemsatis payment link must pass (site-wide link, listing
and package links, the ITEMSATIS_REDIRECT_URL setting).

Customers are sent to these links to pay, so a wrong one sends money
elsewhere. Only https links on itemsatis.com / www.itemsatis.com are accepted.
The check is strict on purpose: browsers and Python's URL parser disagree on
unusual addresses (backslashes, "@", encoded host names), so anything unusual
is refused instead of interpreted.
"""
from urllib.parse import unquote, urlsplit

# Exact host names: no other subdomain, no look-alike, no "itemsatis.com.example"
ALLOWED_HOSTS = frozenset({"itemsatis.com", "www.itemsatis.com"})
MAX_LENGTH = 500


def link_problem(url) -> str | None:
    """Why url is not an acceptable itemsatis payment link (a short reason), or None."""
    if not isinstance(url, str) or not url:
        return "empty"
    if len(url) > MAX_LENGTH:
        return "longer than 500 characters"
    # Also rules out Unicode look-alike host names (only plain ASCII is accepted)
    if not url.isascii() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return "spaces, control or non-ASCII characters"
    # "https://itemsatis.com@evil.example" goes to evil.example; "\" is read as "/" by browsers
    if "@" in url or "\\" in url:
        return "contains @ or a backslash"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "not a valid web address"
    if parts.scheme != "https":
        return "not https"
    if port is not None or ":" in parts.netloc:
        return "has a port"
    if parts.netloc.lower() not in ALLOWED_HOSTS:
        return "not itemsatis.com or www.itemsatis.com"
    # A second address hidden in the path or query (e.g. ?url=https://evil.example),
    # also when percent-encoded once or twice
    rest = unquote(unquote(f"{parts.path}?{parts.query}#{parts.fragment}"))
    if "//" in rest or "@" in rest or "\\" in rest or any(ord(ch) < 32 or ord(ch) == 127 for ch in rest):
        return "contains another address or encoded characters"
    return None


def is_valid_link(url) -> bool:
    return link_problem(url) is None
