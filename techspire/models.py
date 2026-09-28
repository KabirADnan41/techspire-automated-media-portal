"""Typed data passed between pipeline stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from techspire.enums import ArticleStatus, Category, ErrorStage

# Facebook object IDs: "<digits>" (photo) or "<page-id>_<post-id>" (post).
FACEBOOK_OBJECT_ID = re.compile(r"\d+(_\d+)?")


def utcnow() -> datetime:
    return datetime.now(UTC)


def format_utc(moment: datetime) -> str:
    """Display format used in reports and on the review page."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True, slots=True)
class Article:
    """A normalized RSS item. `original_url` is kept exactly as the feed gave it."""

    title: str
    summary: str
    original_url: str
    canonical_url: str
    url_hash: str
    source_name: str
    feed_url: str
    published_at: datetime | None
    discovered_at: datetime
    author: str | None = None
    guid: str | None = None
    language: str | None = None
    title_fingerprint: str | None = None


class AIDecision(BaseModel):
    """Raw structured output from the AI provider (both shapes in one flat object).

    Relevant:  is_relevant=true + category, catchy_headline, facebook_caption, original_url
    Rejected:  is_relevant=false + rejection_reason
    Unused fields must be null. Extra fields are refused.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    is_relevant: bool
    category: str | None
    catchy_headline: str | None
    facebook_caption: str | None
    original_url: str | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class ApprovedContent:
    category: Category
    headline: str
    caption: str
    original_url: str


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str


CurationResult = ApprovedContent | Rejection


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Identifiers returned by POST /{page-id}/photos. The photo ID and the post ID differ."""

    photo_id: str
    post_id: str | None


@dataclass(frozen=True, slots=True)
class StoredArticle:
    """One row of the `articles` table."""

    id: int
    url_hash: str
    original_url: str
    canonical_url: str
    title: str
    summary: str
    source_name: str
    feed_url: str | None
    published_at: datetime | None
    discovered_at: datetime
    status: ArticleStatus
    category: Category | None
    catchy_headline: str | None
    facebook_caption: str | None
    rejection_reason: str | None
    image_path: Path | None
    preview_path: Path | None
    facebook_photo_id: str | None
    facebook_post_id: str | None
    facebook_comment_id: str | None
    error_stage: ErrorStage | None
    last_error: str | None
    retry_count: int
    last_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def facebook_target(self) -> str | None:
        """Where the source comment goes: the post, or the photo if no post ID was returned."""
        return self.facebook_post_id or self.facebook_photo_id

    @property
    def approved_content(self) -> ApprovedContent | None:
        if self.category and self.catchy_headline and self.facebook_caption:
            return ApprovedContent(self.category, self.catchy_headline, self.facebook_caption, self.original_url)
        return None


@dataclass(slots=True)
class RunSummary:
    run_id: str
    mode: str
    feeds_ok: int = 0
    feeds_failed: int = 0
    fetched: int = 0
    too_old: int = 0
    undated: int = 0
    duplicates: int = 0
    same_story: int = 0
    analyzed: int = 0
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    ready_for_review: int = 0
    recovered: int = 0  # reset/flagged after an interrupted run
    resumed: int = 0  # unfinished work picked up again
    facebook_writes: int = 0
