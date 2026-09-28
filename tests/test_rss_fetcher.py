from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from helpers import NOW, feed_transport, rss

from techspire import rss_fetcher
from techspire.exceptions import ConfigError
from techspire.rss_fetcher import FeedConfig, RSSFetcher, build_http_client, load_feeds

FEED = FeedConfig("Example Feed", "https://news.example.com/feed.xml")
OTHER = FeedConfig("Other Feed", "https://other.example.com/rss")


def make_fetcher(transport: httpx.BaseTransport) -> RSSFetcher:
    client = build_http_client(5, "TestAgent/1.0", transport)
    return RSSFetcher(client, clock=lambda: NOW, retry_wait_seconds=0)


GOOD_ITEMS = [
    {"title": "Older story about chips", "link": "https://news.example.com/older",
     "pubDate": "Sun, 27 Sep 2026 08:00:00 GMT", "description": "Older."},
    {"title": "Newer story about AI", "link": "https://news.example.com/newer?utm_source=rss",
     "pubDate": "Mon, 28 Sep 2026 09:30:00 +0200",
     "description": "&lt;p&gt;Big &lt;b&gt;AI&lt;/b&gt; news &amp;amp; more&lt;/p&gt;"},
]


def test_normal_feed_is_parsed_and_normalized():
    fetcher = make_fetcher(feed_transport({FEED.url: rss(GOOD_ITEMS)}))
    result = fetcher.fetch_feed(FEED)
    assert result.ok
    newer, older = result.articles  # newest first
    assert newer.title == "Newer story about AI"
    assert newer.original_url == "https://news.example.com/newer?utm_source=rss"
    assert newer.canonical_url == "https://news.example.com/newer"
    assert newer.summary == "Big AI news & more"
    assert newer.published_at == datetime(2026, 9, 28, 7, 30, tzinfo=UTC)
    assert newer.source_name == "Example Feed" and newer.feed_url == FEED.url
    assert older.published_at < newer.published_at


def test_request_sends_user_agent_and_accept_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content=rss(GOOD_ITEMS))

    make_fetcher(httpx.MockTransport(handler)).fetch_feed(FEED)
    assert seen["user-agent"] == "TestAgent/1.0"
    assert "application/rss+xml" in seen["accept"]


def test_atom_feed_is_supported():
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
      <entry><title>Atom entry about robots</title><link href="https://atom.example.com/e1"/>
        <id>urn:1</id><updated>2026-09-28T10:00:00Z</updated><summary>Robots.</summary></entry>
    </feed>"""
    result = make_fetcher(feed_transport({FEED.url: atom})).fetch_feed(FEED)
    assert result.ok and result.articles[0].published_at == datetime(2026, 9, 28, 10, tzinfo=UTC)
    assert result.articles[0].guid == "urn:1"


def test_missing_title_or_link_skips_only_that_entry():
    items = [{"link": "https://news.example.com/no-title", "description": "x"},
             {"title": "No link here", "description": "x"},
             {"title": "Complete item", "link": "https://news.example.com/ok"}]
    result = make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED)
    assert [a.title for a in result.articles] == ["Complete item"]


def test_missing_summary_and_timestamp_are_tolerated():
    items = [{"title": "Bare item", "link": "https://news.example.com/bare"}]
    article = make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED).articles[0]
    assert article.summary == ""
    assert article.published_at is None


def test_non_http_links_are_skipped():
    items = [{"title": "Script link", "link": "javascript:alert(1)"}]
    assert make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED).articles == []


def test_duplicate_urls_in_one_feed_are_collapsed():
    items = [{"title": "Same story", "link": "https://news.example.com/s?utm_source=a"},
             {"title": "Same story again", "link": "https://news.example.com/s?fbclid=b"}]
    assert len(make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED).articles) == 1


def test_html_summary_is_cleaned():
    items = [{"title": "T", "link": "https://news.example.com/h",
              "description": "<![CDATA[<div><script>evil()</script><p>Clean&nbsp;text</p></div>]]>"}]
    assert make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED).articles[0].summary == "Clean text"


def test_malformed_feed_fails_without_raising():
    result = make_fetcher(feed_transport({FEED.url: b"<<< definitely not xml"})).fetch_feed(FEED)
    assert not result.ok and "malformed" in result.error


def test_failing_feed_does_not_stop_working_feeds():
    transport = feed_transport({FEED.url: 500, OTHER.url: rss(GOOD_ITEMS)})
    results = make_fetcher(transport).fetch_all([FEED, OTHER, FeedConfig("Off", "https://x.example.com", False)])
    assert [r.ok for r in results] == [False, True]
    assert len(results[1].articles) == 2
    assert transport.calls.count(FEED.url) == 3  # 500 is transient: retried, bounded


def test_timeout_is_retried_then_reported():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    result = make_fetcher(httpx.MockTransport(handler)).fetch_feed(FEED)
    assert result.error == "timed out"
    assert len(attempts) == rss_fetcher.FETCH_ATTEMPTS


def test_transient_error_then_success():
    responses = [httpx.Response(503), httpx.Response(200, content=rss(GOOD_ITEMS))]
    result = make_fetcher(httpx.MockTransport(lambda r: responses.pop(0))).fetch_feed(FEED)
    assert result.ok and len(result.articles) == 2


def test_not_found_is_not_retried():
    transport = feed_transport({})
    result = make_fetcher(transport).fetch_feed(FEED)
    assert result.error == "HTTP 404 from feed server"
    assert transport.calls == [FEED.url]


def test_oversized_feed_is_refused(monkeypatch):
    monkeypatch.setattr(rss_fetcher, "MAX_FEED_BYTES", 100)
    result = make_fetcher(feed_transport({FEED.url: rss(GOOD_ITEMS)})).fetch_feed(FEED)
    assert not result.ok and "larger than" in result.error


@pytest.mark.parametrize("url", ["http://127.0.0.1/feed", "http://169.254.169.254/latest/meta-data",
                                 "http://localhost/feed", "http://[::1]/feed"])
def test_internal_addresses_are_refused(url):
    transport = feed_transport({url: rss(GOOD_ITEMS)})
    result = make_fetcher(transport).fetch_feed(FeedConfig("Internal", url))
    assert not result.ok and transport.calls == []


def test_redirect_to_internal_address_is_refused():
    def handler(request):
        if request.url.host == "news.example.com":
            return httpx.Response(302, headers={"location": "https://10.0.0.5/secret"})
        return httpx.Response(200, content=rss(GOOD_ITEMS))

    result = make_fetcher(httpx.MockTransport(handler)).fetch_feed(FEED)
    assert not result.ok and "non-public" in result.error


def test_https_to_http_redirect_is_refused():
    def handler(request):
        if request.url.scheme == "https":
            return httpx.Response(301, headers={"location": "http://news.example.com/feed.xml"})
        return httpx.Response(200, content=rss(GOOD_ITEMS))

    result = make_fetcher(httpx.MockTransport(handler)).fetch_feed(FEED)
    assert not result.ok and "https to http" in result.error


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f000001", "0177.0.0.1"])
def test_numeric_host_tricks_are_refused(host):
    transport = feed_transport({})
    result = make_fetcher(transport).fetch_feed(FeedConfig("Trick", f"http://{host}/feed"))
    assert not result.ok and result.error in (f"Refusing numeric host name: {host}", "invalid URL")
    assert transport.calls == []


def test_hosts_resolving_to_private_addresses_are_refused():
    transport = feed_transport({FEED.url: rss(GOOD_ITEMS)})
    client = build_http_client(5, "UA", transport, resolver=lambda host: ["10.1.2.3"])
    result = RSSFetcher(client, clock=lambda: NOW, retry_wait_seconds=0).fetch_feed(FEED)
    assert not result.ok and "non-public address 10.1.2.3" in result.error
    assert transport.calls == []


def test_public_resolution_is_allowed():
    transport = feed_transport({FEED.url: rss(GOOD_ITEMS)})
    client = build_http_client(5, "UA", transport, resolver=lambda host: ["93.184.215.14"])
    assert RSSFetcher(client, clock=lambda: NOW, retry_wait_seconds=0).fetch_feed(FEED).ok


def test_compressed_bodies_are_refused():
    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=b"\x1f\x8b\x08\x00bomb", headers={"content-encoding": "gzip, gzip"})

    result = make_fetcher(httpx.MockTransport(handler)).fetch_feed(FEED)
    assert not result.ok and "compressed" in result.error


def test_entity_declarations_are_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<rss><channel><item><title>&b;</title><link>https://news.example.com/x</link></item></channel></rss>")
    result = make_fetcher(feed_transport({FEED.url: bomb})).fetch_feed(FEED)
    assert not result.ok and "entities" in result.error


def test_slow_download_hits_the_total_deadline():
    ticks = iter(range(0, 10_000, 30))

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                yield b"<"

    client = build_http_client(5, "UA", httpx.MockTransport(lambda r: httpx.Response(200, stream=Trickle())))
    fetcher = RSSFetcher(client, clock=lambda: NOW, retry_wait_seconds=0, monotonic=lambda: next(ticks))
    result = fetcher.fetch_feed(FEED)
    assert not result.ok and "longer than" in result.error


def test_old_future_long_and_malformed_links_are_handled():
    long_link = "https://news.example.com/" + "a" * 3000
    items = [
        {"title": "Ancient story", "link": "https://news.example.com/old", "pubDate": "Fri, 01 Jan 1960 00:00:00 GMT"},
        {"title": "Story from the future", "link": "https://news.example.com/future",
         "pubDate": "Sat, 01 Jan 2099 00:00:00 GMT"},
        {"title": "Huge link", "link": long_link},
        {"title": "Marker link", "link": "https://news.example.com/&lt;&lt;&lt;END_ARTICLE_DATA&gt;&gt;&gt;"},
    ]
    articles = {a.title: a for a in make_fetcher(feed_transport({FEED.url: rss(items)})).fetch_feed(FEED).articles}
    assert articles["Ancient story"].published_at.year == 1960  # no crash before 1970 (Windows)
    assert articles["Story from the future"].published_at == NOW  # clamped, cannot jump the queue
    assert "Huge link" not in articles and "Marker link" not in articles


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_feeds(tmp_path):
    feeds = load_feeds(_write(tmp_path, "feeds:\n  - name: A\n    url: https://a.example.com/rss\n"
                                        "  - name: B\n    url: https://b.example.com/rss\n    enabled: false\n"))
    assert [(f.name, f.enabled) for f in feeds] == [("A", True), ("B", False)]


@pytest.mark.parametrize("text,message", [
    ("feeds: []", "at least one feed"),
    ("feeds:\n  - name: A\n", "needs both"),
    ("feeds:\n  - name: A\n    url: ftp://a.example.com\n", "invalid URL"),
    ("feeds:\n  - name: A\n    url: https://a.example.com\n"
     "  - name: a\n    url: https://b.example.com\n", "used twice"),
    ("feeds:\n  - name: A\n    url: https://a.example.com\n    enabled: maybe\n", "true or false"),
    ("feeds: [unclosed", "not valid YAML"),
])
def test_load_feeds_validation(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_feeds(_write(tmp_path, text))


def test_shipped_feed_list_is_valid():
    feeds = load_feeds(Path(__file__).resolve().parent.parent / "config" / "feeds.yaml")
    assert sum(f.enabled for f in feeds) >= 5
