"""Controlled vocabularies: article states, the state machine, and editorial categories."""

from __future__ import annotations

import re
from enum import StrEnum


class ArticleStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    ANALYZING = "ANALYZING"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    IMAGE_CREATED = "IMAGE_CREATED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PUBLISHING = "PUBLISHING"
    POST_CREATED = "POST_CREATED"
    COMMENT_PENDING = "COMMENT_PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class ErrorStage(StrEnum):
    ANALYZE = "analyze"
    IMAGE = "image"
    PUBLISH_PHOTO = "publish_photo"  # Facebook definitively refused: no post exists
    PUBLISH_UNKNOWN = "publish_unknown"  # outcome unknown: never retried automatically
    PUBLISH_COMMENT = "publish_comment"  # definitive refusal: retried on the next run
    COMMENT_UNKNOWN = "comment_unknown"  # outcome unknown: next run checks for it before posting again


S = ArticleStatus

# Where a FAILED article resumes, by the stage that failed. Stages not listed wait for a human.
RESUME_FROM_FAILED: dict[ErrorStage, ArticleStatus] = {
    ErrorStage.ANALYZE: S.ANALYZING,
    ErrorStage.IMAGE: S.APPROVED,
    ErrorStage.PUBLISH_PHOTO: S.PUBLISHING,
}

# Every automatic state change. Anything not listed here is refused by storage.
ALLOWED_TRANSITIONS: dict[ArticleStatus, frozenset[ArticleStatus]] = {
    # DISCOVERED -> REJECTED: the same story was already registered (title fingerprint).
    S.DISCOVERED: frozenset({S.ANALYZING, S.REJECTED}),
    # ANALYZING -> DISCOVERED: crash recovery or an AI provider outage (nothing was decided).
    S.ANALYZING: frozenset({S.APPROVED, S.REJECTED, S.FAILED, S.DISCOVERED}),
    S.APPROVED: frozenset({S.IMAGE_CREATED, S.FAILED}),
    S.IMAGE_CREATED: frozenset({S.READY_FOR_REVIEW, S.FAILED}),
    S.READY_FOR_REVIEW: frozenset({S.PUBLISHING}),
    S.PUBLISHING: frozenset({S.POST_CREATED, S.FAILED}),
    S.POST_CREATED: frozenset({S.COMMENT_PENDING, S.PUBLISHED}),
    # COMMENT_PENDING -> COMMENT_PENDING: another comment attempt is recorded.
    S.COMMENT_PENDING: frozenset({S.COMMENT_PENDING, S.PUBLISHED}),
    S.FAILED: frozenset(RESUME_FROM_FAILED.values()),  # further restricted by the failed stage
    S.REJECTED: frozenset(),
    S.PUBLISHED: frozenset(),
}

# Manual decisions after an unknown upload outcome (python -m techspire.main --resolve-unknown).
OPERATOR_TRANSITIONS: dict[ArticleStatus, frozenset[ArticleStatus]] = {
    S.FAILED: frozenset({S.POST_CREATED, S.FAILED}),
}

# Reaching these states by completing a stage (not by resuming from FAILED) gives the next
# stage a fresh retry budget.
RESETS_RETRY_BUDGET = frozenset({S.APPROVED, S.READY_FOR_REVIEW, S.POST_CREATED})


class Category(StrEnum):
    """The five editorial sectors (owner decision, 2026-09-28)."""

    TECHNOLOGY = "TECHNOLOGY"
    BUSINESS = "BUSINESS"
    FINANCE = "FINANCE"
    CAREER_DEVELOPMENT = "CAREER DEVELOPMENT"
    ENTREPRENEURSHIP = "ENTREPRENEURSHIP"

    @classmethod
    def normalize(cls, value: str | None) -> Category | None:
        """Map an AI-produced (or legacy) label onto the fixed enum, or None if unsupported."""
        if not value:
            return None
        key = _category_key(value)
        return _CATEGORY_LOOKUP.get(key)


def _category_key(value: str) -> str:
    text = value.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_CATEGORY_ALIASES: dict[Category, tuple[str, ...]] = {
    Category.TECHNOLOGY: (
        "TECH", "AI", "A I", "ARTIFICIAL INTELLIGENCE", "GENERATIVE AI", "MACHINE LEARNING",
        "CYBERSECURITY", "CYBER SECURITY", "SECURITY", "SOFTWARE", "SOFTWARE AND DEVELOPMENT",
        "SOFTWARE DEVELOPMENT", "HARDWARE", "HARDWARE AND GADGETS", "GADGETS", "TECH INDUSTRY",
        "EMERGING TECHNOLOGY", "CLOUD", "CLOUD AND INFRASTRUCTURE", "SCIENCE AND TECHNOLOGY",
    ),
    Category.BUSINESS: ("BUSINESS NEWS", "COMPANIES", "CORPORATE", "CORPORATES", "INDUSTRY", "TRADE"),
    Category.FINANCE: (
        "FINANCIAL", "MARKETS", "STOCKS", "ECONOMY", "ECONOMICS", "PERSONAL FINANCE", "INVESTING",
        "BANKING", "FINTECH", "CRYPTO", "CRYPTOCURRENCY", "MONEY",
    ),
    Category.CAREER_DEVELOPMENT: (
        "CAREER DEV", "CAREERS", "CAREER", "JOBS", "WORK", "WORKPLACE", "EMPLOYMENT",
        "PROFESSIONAL DEVELOPMENT", "SKILLS",
    ),
    Category.ENTREPRENEURSHIP: (
        "ENTREPRENEURS", "ENTREPRENEURSHIP AND STARTUPS", "STARTUPS", "STARTUP", "STARTUPS AND FUNDING",
        "FUNDING", "VENTURE CAPITAL", "SMALL BUSINESS", "FOUNDERS",
    ),
}

_CATEGORY_LOOKUP: dict[str, Category] = {_category_key(c.value): c for c in Category}
for _category, _aliases in _CATEGORY_ALIASES.items():
    for _alias in _aliases:
        _CATEGORY_LOOKUP[_category_key(_alias)] = _category
