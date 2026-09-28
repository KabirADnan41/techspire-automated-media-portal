"""Test helpers: builders for articles, AI answers, feeds and scripted providers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from techspire.content_filter import BaseAIProvider
from techspire.models import Article
from techspire.url_utils import canonicalize_url, url_hash

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def make_article(url: str = "https://news.example.com/ai/story-1",
                 title: str = "Example AI model launches for developers",
                 summary: str = "Example Corp released a new AI model for developers.",
                 published: datetime | None = NOW - timedelta(hours=1), source: str = "Example Feed",
                 fingerprint: str | None = None) -> Article:
    canonical = canonicalize_url(url)
    return Article(
        title=title, summary=summary, original_url=url, canonical_url=canonical, url_hash=url_hash(canonical),
        source_name=source, feed_url="https://news.example.com/feed.xml", published_at=published,
        discovered_at=NOW, title_fingerprint=fingerprint,
    )


def relevant_answer(article: Article, **overrides: Any) -> dict[str, Any]:
    answer = {
        "is_relevant": True,
        "category": "TECHNOLOGY",
        "catchy_headline": "Example Corp Launches a Powerful New AI Model for Developers",
        "facebook_caption": ("Example Corp has released a new AI model for developers. The release gives teams "
                             "another option for building AI features. #AI #Developers #TechNews #TechspireOfficial"),
        "original_url": article.original_url,
        "rejection_reason": None,
    }
    answer.update(overrides)
    return answer


def rejected_answer(reason: str = "The article is about politics, not technology.") -> dict[str, Any]:
    return {"is_relevant": False, "category": None, "catchy_headline": None, "facebook_caption": None,
            "original_url": None, "rejection_reason": reason}


class ScriptedProvider(BaseAIProvider):
    """Returns queued answers (dicts are JSON-encoded, exceptions are raised, callables get the prompt)."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[tuple[str, str]] = []

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> str:
        self.prompts.append((system_prompt, user_prompt))
        if not self.answers:
            raise AssertionError("ScriptedProvider ran out of answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(user_prompt)
        return answer if isinstance(answer, str) else json.dumps(answer)


def rss(items: list[dict[str, str]], title: str = "Example Feed") -> bytes:
    body = []
    for item in items:
        parts = [f"<{k}>{v}</{k}>" for k, v in item.items()]
        body.append("<item>" + "".join(parts) + "</item>")
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>'
            f"<link>https://news.example.com/</link><description>d</description>{''.join(body)}</channel></rss>"
            ).encode()


def feed_transport(routes: dict[str, Callable[[httpx.Request], httpx.Response] | bytes | int]) -> httpx.MockTransport:
    """Mock transport serving bytes (200), a status code, or a handler per URL."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        route = routes.get(str(request.url))
        if route is None:
            return httpx.Response(404)
        if isinstance(route, bytes):
            return httpx.Response(200, content=route, headers={"content-type": "application/rss+xml"})
        if isinstance(route, int):
            return httpx.Response(route)
        return route(request)

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport
