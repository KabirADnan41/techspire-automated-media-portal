import pytest

from techspire.url_utils import InvalidURLError, canonicalize_url, is_http_url, url_hash


def test_removes_utm_and_click_ids():
    url = ("https://Example.com/News/Story?utm_source=rss&utm_medium=feed&utm_campaign=x&utm_term=t&utm_content=c"
           "&fbclid=abc&gclid=def&id=42")
    assert canonicalize_url(url) == "https://example.com/News/Story?id=42"


def test_preserves_functional_parameters_and_their_order_and_encoding():
    url = "https://example.com/article.php?p=7&q=a%20b&ref=home&utm_source=x&page=2"
    assert canonicalize_url(url) == "https://example.com/article.php?p=7&q=a%20b&ref=home&page=2"


def test_drops_fragment_default_port_and_lowercases_host_only():
    assert canonicalize_url("HTTPS://NEWS.Example.COM:443/Path/To?a=1#section") == "https://news.example.com/Path/To?a=1"
    assert canonicalize_url("http://example.com:8080/x") == "http://example.com:8080/x"
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_trailing_slash_is_left_alone():
    assert canonicalize_url("https://example.com/a/") != canonicalize_url("https://example.com/a")


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://example.com/x", "mailto:a@b.c", "/relative/path",
                                 "", "https://", "data:text/html,hi"])
def test_rejects_non_http_urls(bad):
    assert not is_http_url(bad)
    with pytest.raises(InvalidURLError):
        canonicalize_url(bad)


def test_same_story_with_different_tracking_has_same_hash():
    a = canonicalize_url("https://example.com/s?id=1&utm_source=a")
    b = canonicalize_url("https://example.com/s?fbclid=zz&id=1")
    assert a == b
    assert url_hash(a) == url_hash(b)


def test_hash_is_stable_sha256_hex():
    h = url_hash("https://example.com/s")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert h == url_hash("https://example.com/s")
    assert h != url_hash("https://example.com/t")
