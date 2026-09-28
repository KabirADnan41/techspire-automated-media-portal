"""RSS/Atom ingestion: download with httpx (timeout, user agent, retries), parse with feedparser.

One broken feed never stops the others: every feed is fetched and parsed inside
its own error boundary and failures are reported, not raised.
"""

from __future__ import annotations

import calendar
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from techspire.exceptions import ConfigError, FeedError
from techspire.models import Article, utcnow
from techspire.text_cleaner import clean_summary, clean_title, title_fingerprint
from techspire.url_utils import InvalidURLError, canonicalize_url, is_http_url, url_hash

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 10 * 1024 * 1024  # after decompression
MAX_DOWNLOAD_SECONDS = 60.0  # total per download, however slowly the server trickles bytes
MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5
MAX_PARALLEL_FEEDS = 8  # feeds download concurrently; results keep the configured order
FETCH_ATTEMPTS = 3
_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Internal DTD entities enable "billion laughs" expansion in feedparser's fallback parser.
_ENTITY_DECLARATION = re.compile(rb"<!ENTITY", re.IGNORECASE)
# Hosts like 127.1, 2130706433 or 0x7f000001 that the OS resolves as IP addresses.
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}", re.IGNORECASE)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

Resolver = Callable[[str], list[str]]


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    url: str
    enabled: bool = True


@dataclass(slots=True)
class FeedResult:
    feed: FeedConfig
    articles: list[Article] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def load_feeds(path: Path) -> list[FeedConfig]:
    """Read and validate config/feeds.yaml."""
    if not path.is_file():
        raise ConfigError(f"Feed list not found: {path}. Create it (see config/feeds.yaml in the README).")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    entries = data.get("feeds") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} must contain a 'feeds:' list with at least one feed.")
    feeds: list[FeedConfig] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: feed #{index} must be a mapping with name and url.")
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        enabled = entry.get("enabled", True)
        if not name or not url:
            raise ConfigError(f"{path}: feed #{index} needs both 'name' and 'url'.")
        if not is_http_url(url):
            raise ConfigError(f"{path}: feed '{name}' has an invalid URL ({url}). Use http:// or https://.")
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}: feed '{name}' has enabled: {enabled!r}; use true or false.")
        if name.lower() in seen:
            raise ConfigError(f"{path}: feed name '{name}' is used twice.")
        seen.add(name.lower())
        feeds.append(FeedConfig(name=name, url=url, enabled=enabled))
    return feeds


def system_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def build_http_client(timeout: float, user_agent: str, transport: httpx.BaseTransport | None = None,
                      resolver: Resolver | None = None) -> httpx.Client:
    """HTTP client for feeds: TLS verification on, explicit timeout, bounded redirects and an
    SSRF guard on every request. Real network clients also check what host names resolve to;
    a mock transport (tests, demo) needs no resolver."""
    if resolver is None and transport is None:
        resolver = system_resolver
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": user_agent, "Accept": _ACCEPT, "Accept-Encoding": "identity"},
        transport=transport,
        event_hooks={"request": [_TargetGuard(resolver)], "response": [_refuse_downgrade]},
    )


class _TargetGuard:
    """SSRF guard for every request, including redirects: http(s) only, no localhost, no numeric
    host tricks, and no host that is (or resolves to) a private, loopback or link-local address.
    DNS is checked before the connection, so a DNS-rebinding attacker could still race it; feed
    URLs come from the operator's own list, which keeps that residual risk small."""

    def __init__(self, resolver: Resolver | None) -> None:
        self._resolver = resolver

    def __call__(self, request: httpx.Request) -> None:
        if request.url.scheme not in ("http", "https"):
            raise FeedError(f"Refusing non-http(s) URL: {request.url}")
        host = request.url.host.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            raise FeedError(f"Refusing to fetch internal host: {host}")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            if _NUMERIC_HOST.fullmatch(host):
                raise FeedError(f"Refusing numeric host name: {host}") from None
            addresses = self._resolve(host)
        for address in addresses:
            if not address.is_global:
                raise FeedError(f"Refusing to fetch non-public address {address} (host {host})")

    def _resolve(self, host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        if self._resolver is None:
            return []
        try:
            return [ipaddress.ip_address(a.split("%", 1)[0]) for a in self._resolver(host)]
        except (OSError, ValueError):
            return []  # unresolvable: the connection attempt itself will fail


def _refuse_downgrade(response: httpx.Response) -> None:
    """Never follow a redirect from https to plain http."""
    if response.is_redirect and response.request.url.scheme == "https":
        target = response.request.url.join(response.headers.get("location", ""))
        if target.scheme == "http":
            raise FeedError("Refusing a redirect from https to http")


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRYABLE_STATUS


class RSSFetcher:
    def __init__(self, client: httpx.Client, clock: Callable[[], datetime] | None = None,
                 retry_wait_seconds: float = 2.0, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._clock = clock or utcnow
        self._monotonic = monotonic
        self._download = retry(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(FETCH_ATTEMPTS),
            wait=wait_exponential(multiplier=retry_wait_seconds, max=30),
            reraise=True,
        )(self._download_once)

    def fetch_all(self, feeds: Sequence[FeedConfig]) -> list[FeedResult]:
        """Fetch every enabled feed concurrently (network waits overlap); results keep feed order."""
        enabled = [feed for feed in feeds if feed.enabled]
        if len(enabled) <= 1:
            return [self.fetch_feed(feed) for feed in enabled]
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_FEEDS, len(enabled))) as pool:
            return list(pool.map(self.fetch_feed, enabled))

    def fetch_feed(self, feed: FeedConfig) -> FeedResult:
        try:
            content, content_type = self._download(feed.url)
            articles = self.parse_feed(feed, content, content_type)
        except httpx.HTTPStatusError as exc:
            return self._failed(feed, f"HTTP {exc.response.status_code} from feed server")
        except httpx.TimeoutException:
            return self._failed(feed, "timed out")
        except httpx.HTTPError as exc:
            return self._failed(feed, f"network error: {type(exc).__name__}")
        except httpx.InvalidURL:
            return self._failed(feed, "invalid URL")
        except FeedError as exc:
            return self._failed(feed, str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad feed must never stop the run
            log.exception("Unexpected error while processing feed '%s'", feed.name)
            return self._failed(feed, f"unexpected error: {type(exc).__name__}")
        log.info("RSS fetch complete: %s (%d articles)", feed.name, len(articles))
        return FeedResult(feed, articles)

    @staticmethod
    def _failed(feed: FeedConfig, reason: str) -> FeedResult:
        log.warning("Feed '%s' skipped: %s", feed.name, reason)
        return FeedResult(feed, error=reason)

    def _download_once(self, url: str) -> tuple[bytes, str]:
        started = self._monotonic()
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            # Compressed bodies are refused (identity was requested): a tiny gzip "bomb" could
            # otherwise expand to gigabytes before any size check runs.
            encoding = response.headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise FeedError(f"server sent a compressed feed ({encoding}) although none was requested")
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    raise FeedError(f"feed is larger than {MAX_FEED_BYTES // (1024 * 1024)} MB")
                if self._monotonic() - started > MAX_DOWNLOAD_SECONDS:
                    raise FeedError(f"download took longer than {MAX_DOWNLOAD_SECONDS:g} seconds")
            return bytes(body), response.headers.get("content-type", "")

    def parse_feed(self, feed: FeedConfig, content: bytes, content_type: str = "") -> list[Article]:
        """Parse feed bytes into normalized Articles. Raises FeedError if nothing usable."""
        if _ENTITY_DECLARATION.search(content):
            raise FeedError("feed declares XML entities; refused for safety")
        headers = {"content-type": content_type} if content_type else {}
        parsed = feedparser.parse(content, response_headers=headers)
        if parsed.bozo and not parsed.entries:
            reason = type(parsed.get("bozo_exception")).__name__ if parsed.get("bozo_exception") else "unknown"
            raise FeedError(f"malformed feed ({reason})")
        if parsed.bozo:
            log.debug("Feed '%s' is not well-formed but has usable entries", feed.name)
        language = (parsed.feed.get("language") or None) if parsed.get("feed") else None
        now = self._clock()
        articles: list[Article] = []
        seen: set[str] = set()
        for entry in parsed.entries:
            article = self._to_article(feed, entry, language, now)
            if article is None or article.url_hash in seen:
                continue
            seen.add(article.url_hash)
            articles.append(article)
        articles.sort(key=lambda a: a.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return articles

    @staticmethod
    def _to_article(feed: FeedConfig, entry: Any, language: str | None, now: datetime) -> Article | None:
        title = clean_title(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link or len(link) > MAX_URL_LENGTH:
            log.debug("Feed '%s': entry without a usable title or link skipped", feed.name)
            return None
        try:
            canonical = canonicalize_url(link)
        except InvalidURLError:
            log.debug("Feed '%s': entry with non-http link skipped", feed.name)
            return None
        raw_summary = entry.get("summary") or entry.get("description") or ""
        if not raw_summary and entry.get("content"):
            raw_summary = entry["content"][0].get("value", "")
        published = _entry_datetime(entry)
        if published is not None and published > now:
            published = now  # future dates must not jump the queue or dodge the age limit
        return Article(
            title=title,
            summary=clean_summary(raw_summary),
            original_url=link,
            canonical_url=canonical,
            url_hash=url_hash(canonical),
            source_name=feed.name,
            feed_url=feed.url,
            published_at=published,
            discovered_at=now,
            author=(clean_title(entry.get("author")) or None) if entry.get("author") else None,
            guid=(str(entry.get("id"))[:500] or None) if entry.get("id") else None,
            language=language,
            title_fingerprint=title_fingerprint(title),
        )


def _entry_datetime(entry: Any) -> datetime | None:
    """feedparser gives UTC struct_time values; convert to an aware datetime."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                # Epoch arithmetic instead of fromtimestamp(): works for pre-1970 dates on Windows too.
                return _EPOCH + timedelta(seconds=calendar.timegm(value))
            except (OverflowError, ValueError, TypeError):
                continue
    return None
