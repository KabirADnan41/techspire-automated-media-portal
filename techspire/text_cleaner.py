"""Turn untrusted RSS HTML into bounded, readable plain text."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from bs4 import BeautifulSoup

SUMMARY_MAX_CHARS = 1200
RAW_HTML_LIMIT = 20_000  # untrusted input is cut before any parsing or regex work
TITLE_MAX_CHARS = 300
FINGERPRINT_MIN_WORDS = 6  # shorter titles are too generic to compare safely

_DROP_TAGS = ("script", "style", "noscript", "iframe", "template", "svg", "object", "embed", "form", "head")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_ESCAPED_TAG = re.compile(r"&lt;\s*/?\s*[a-zA-Z]")
# Feed boilerplate that only ever appears at the end of a summary.
_TRAILING_BOILERPLATE = (
    re.compile(r"(?is)\s*The post .{1,300}? appeared first on .{1,200}?\.?\s*$"),
    re.compile(r"(?i)\s*(continue reading|read more|read the full story)\s*(»|›|→|\.\.\.|…)?\s*$"),
    re.compile(r"\s*\[(…|\.\.\.)\]\s*$"),
)


def html_to_text(raw: str | None) -> str:
    """Strip tags, scripts and styles; decode entities; collapse whitespace."""
    if not raw:
        return ""
    text = raw[:RAW_HTML_LIMIT]
    if "<" not in text and _ESCAPED_TAG.search(text):
        text = html.unescape(text)  # markup that was escaped once: "&lt;p&gt;Hi&lt;/p&gt;"
    if "<" in text:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(_DROP_TAGS):
            tag.decompose()
        text = soup.get_text(" ")
    # Feeds often double-encode entities ("&amp;#8217;"): decode until stable (bounded).
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    # NFC (not NFKC): keeps "™", "…" and accented letters intact for display.
    text = unicodedata.normalize("NFC", text)
    # Drop invisible format characters (zero-width, bidi overrides such as U+202E/U+2066-2069).
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _CONTROL.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def truncate(text: str, max_chars: int) -> str:
    """Shorten at a word boundary and mark the cut with an ellipsis."""
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def clean_title(raw: str | None) -> str:
    return truncate(html_to_text(raw), TITLE_MAX_CHARS)


def clean_summary(raw: str | None) -> str:
    text = html_to_text(raw)
    for pattern in _TRAILING_BOILERPLATE:
        text = pattern.sub("", text).strip()
    return truncate(text, SUMMARY_MAX_CHARS)


def title_fingerprint(title: str) -> str | None:
    """Fingerprint of the normalized title words, for obvious syndicated copies.

    Deliberately strict: exact word sequence only, and only for titles with at
    least FINGERPRINT_MIN_WORDS words, so different stories are not merged.
    """
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", title).casefold())
    if len(words) < FINGERPRINT_MIN_WORDS:
        return None
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()
