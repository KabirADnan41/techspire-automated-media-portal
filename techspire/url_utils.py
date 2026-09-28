"""Conservative URL canonicalization for duplicate detection.

Only well-known tracking parameters are removed; every other query parameter is
kept (it may identify the article). The original URL is always stored separately.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import unquote_plus, urlsplit, urlunsplit

# Exact names of click/campaign trackers. Any `utm_*` parameter is also removed.
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mkt_tok",
})

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Characters that must be percent-encoded in a valid URL; raw ones signal a malformed or hostile link.
_UNSAFE_URL_CHARS = re.compile(r"[\s<>\"{}|\\^`\x00-\x1f\x7f]")


class InvalidURLError(ValueError):
    pass


def is_http_url(url: str | None) -> bool:
    if not url or _UNSAFE_URL_CHARS.search(url.strip()):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    """Return the canonical form of an http(s) URL. Raises InvalidURLError otherwise.

    - lower-cases scheme and host, drops default ports and the #fragment
    - removes utm_* and known click-ID parameters, keeping the order of the rest
    - leaves the path, its case, and trailing slashes untouched
    """
    if not is_http_url(url):
        raise InvalidURLError(f"Not an http(s) URL: {url!r}")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError(f"Invalid port in URL: {url!r}") from exc
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    # Filter the raw "&"-separated segments so kept parameters keep their exact encoding.
    kept = [
        segment for segment in parts.query.split("&")
        if segment and not _is_tracking_param(unquote_plus(segment.split("=", 1)[0]))
    ]
    query = "&".join(kept)
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(canonical_url: str) -> str:
    """Stable SHA-256 identifier (64 hex chars) of a canonical URL."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
