"""Orchestrator: fetch -> dedupe -> AI curator -> card -> review (-> future publish).

One run is fetch -> process -> exit. Each article is handled inside its own error
boundary, so one failure never stops the batch. Every step is a persisted state
transition, so a crash at any point is recovered on the next run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from techspire.config import Settings
from techspire.content_filter import AIContentService, is_provider_outage
from techspire.enums import ArticleStatus, ErrorStage
from techspire.exceptions import (
    AIProviderError,
    AIResponseValidationError,
    FacebookError,
    FacebookOutcomeUnknownError,
    ImageGenerationError,
)
from techspire.fb_publisher import FacebookPublisher, stops_publishing
from techspire.image_generator import CardRenderer, image_path_for
from techspire.models import Article, Rejection, RunSummary, StoredArticle
from techspire.preview import comment_text, format_dry_run_block, write_preview, write_review_page
from techspire.rss_fetcher import FeedConfig, RSSFetcher
from techspire.storage import Storage

log = logging.getLogger(__name__)

S = ArticleStatus
RUN_TIME_BUDGET = timedelta(minutes=20)  # stop starting new work after this (run lock lasts 30 min)
# A crash at one of these steps is charged to this stage's retry budget.
_STAGE_OF_STATUS = {S.ANALYZING: ErrorStage.ANALYZE, S.APPROVED: ErrorStage.IMAGE, S.IMAGE_CREATED: ErrorStage.IMAGE}


@dataclass(frozen=True)
class RunOptions:
    mode: str = "dry-run"  # "dry-run", "demo" or "live"
    limit: int = 10
    max_age_hours: float = 48.0
    publish_ids: frozenset[int] | None = None  # live mode: only publish these reviewed articles


class Pipeline:
    def __init__(self, settings: Settings, storage: Storage, fetcher: RSSFetcher, feeds: Sequence[FeedConfig],
                 ai: AIContentService, renderer: CardRenderer, options: RunOptions, *,
                 publisher: FacebookPublisher | None = None, run_id: str,
                 report: Callable[[str], None] = print, monotonic: Callable[[], float] = time.monotonic) -> None:
        if publisher is not None and options.mode != "live":
            raise ValueError("A publisher may only be supplied in live mode.")
        self.settings = settings
        self.storage = storage
        self.fetcher = fetcher
        self.feeds = list(feeds)
        self.ai = ai
        self.renderer = renderer
        self.options = options
        self.publisher = publisher
        self.run_id = run_id
        self.report = report
        self._monotonic = monotonic
        self._started = monotonic()
        self._ai_stopped = False
        self._fb_stopped = False

    # ------------------------------------------------------------------ run
    def run(self) -> RunSummary:
        summary = RunSummary(run_id=self.run_id, mode=self.options.mode)
        self.storage.acquire_run_lock(self.run_id)
        outcome = "error"
        try:
            self.storage.start_run(self.run_id, self.options.mode)
            log.info("Run %s started (mode=%s)", self.run_id, self.options.mode)
            summary.recovered = self.storage.recover_interrupted(self.run_id)
            if self.publisher is not None:
                self._retry_pending_comments(summary)
            self._process_articles(summary)
            if self.publisher is not None:
                self._publish_ready(summary)
            else:
                pending = len(self.storage.comment_pending_articles(self.settings.max_retries_per_article))
                if pending:
                    log.warning("Publishing disabled: %d post(s) still need their source comment", pending)
            self._write_review_page()
            outcome = "ok"
        finally:
            self.storage.finish_run(self.run_id, outcome, asdict(summary))
            self.storage.release_run_lock(self.run_id)
            log.info("Run %s finished (%s): %d ready for review, %d Facebook writes",
                     self.run_id, outcome, summary.ready_for_review, summary.facebook_writes)
        return summary

    # ---------------------------------------------------------- ingestion
    def _process_articles(self, summary: RunSummary) -> None:
        cutoff = self._cutoff()
        resumable = [s for s in self.storage.resumable_articles(self.settings.max_retries_per_article)
                     if is_fresh(s.published_at, cutoff)]
        new_articles = self._fetch_new(summary, cutoff)
        processed = 0
        for stored in resumable:
            if self._ai_stopped and _needs_ai(stored):
                continue
            if not self._may_continue(processed):
                return
            log.info("Resuming article #%d (%s)", stored.id, stored.status.value)
            summary.resumed += 1
            self._process(stored, summary)
            processed += 1
        for article in new_articles:
            if self._ai_stopped or not self._may_continue(processed):
                return
            registration = self.storage.register(article, self.run_id)
            if not registration.is_new:
                summary.duplicates += 1
                continue
            if registration.status is S.REJECTED:
                summary.same_story += 1
                log.info("Duplicate story skipped: #%d '%s' (same as #%s)",
                         registration.article_id, article.title, registration.duplicate_of)
                continue
            log.info("New article discovered: #%d '%s' (%s)", registration.article_id, article.title,
                     article.source_name)
            self._process(self.storage.get(registration.article_id), summary)
            processed += 1

    def _fetch_new(self, summary: RunSummary, cutoff: datetime) -> list[Article]:
        results = self.fetcher.fetch_all(self.feeds)
        summary.feeds_ok = sum(r.ok for r in results)
        summary.feeds_failed = sum(not r.ok for r in results)
        unique: dict[str, Article] = {}
        for article in (a for r in results for a in r.articles):
            unique.setdefault(article.url_hash, article)
        known = self.storage.known_url_hashes(list(unique))
        fresh: list[Article] = []
        for article in unique.values():
            summary.fetched += 1
            if article.published_at is None:
                summary.undated += 1
            elif not is_fresh(article.published_at, cutoff):
                summary.too_old += 1
            elif article.url_hash in known:
                summary.duplicates += 1
            else:
                fresh.append(article)
        fresh = interleave_by_source(fresh)
        log.info("RSS fetch complete: %d feeds ok, %d failed; %d articles (%d new, %d already known, "
                 "%d too old, %d undated)", summary.feeds_ok, summary.feeds_failed, summary.fetched, len(fresh),
                 summary.duplicates, summary.too_old, summary.undated)
        return fresh

    def _may_continue(self, processed: int) -> bool:
        if processed >= self.options.limit:
            log.info("Article limit reached (%d); remaining articles wait for the next run", self.options.limit)
            return False
        return self._within_budget("remaining articles wait for the next run")

    def _may_write(self) -> bool:
        """Checked right before every Facebook write: not stopped, time left, run lock still ours."""
        return not self._fb_stopped and self._within_budget("remaining Facebook work waits for the next run")

    def _within_budget(self, what_waits: str) -> bool:
        if self._monotonic() - self._started > RUN_TIME_BUDGET.total_seconds():
            log.warning("Run time budget used up; %s", what_waits)
            return False
        self.storage.refresh_run_lock(self.run_id)  # raises if another run took the lock over
        return True

    def _cutoff(self) -> datetime:
        return self.storage.now() - timedelta(hours=self.options.max_age_hours)

    # ---------------------------------------------------------- per article
    def _process(self, stored: StoredArticle, summary: RunSummary) -> None:
        article_id = stored.id
        try:
            if _needs_ai(stored):
                stored = self._analyze(stored, summary)
            elif stored.status is S.FAILED and stored.error_stage is ErrorStage.IMAGE:
                stored = self.storage.transition(stored.id, S.APPROVED, run_id=self.run_id,
                                                 note="retrying image generation")
            if stored and stored.status is S.APPROVED:
                stored = self._render(stored, summary)
            if stored and stored.status is S.IMAGE_CREATED:
                self._finish_preview(stored, summary)
        except Exception as exc:  # noqa: BLE001 - never let one article stop the batch
            log.exception("Unexpected error while processing article #%d", article_id)
            stage = _STAGE_OF_STATUS.get(self.storage.get(article_id).status)
            if stage is not None:  # charge it to the article's retry budget instead of leaving it mid-step
                self._fail(article_id, stage, f"Unexpected error: {type(exc).__name__}: {exc}", summary)

    def _analyze(self, stored: StoredArticle, summary: RunSummary) -> StoredArticle | None:
        if self._ai_stopped:
            return None
        stored = self.storage.transition(stored.id, S.ANALYZING, run_id=self.run_id, last_attempt_at=self.storage.now())
        try:
            result = self.ai.curate(_to_article(stored))
        except AIProviderError as exc:
            if is_provider_outage(exc):
                self.storage.transition(stored.id, S.DISCOVERED, run_id=self.run_id,
                                        note=f"AI provider unavailable: {exc}")
                self._ai_stopped = True
                log.error("AI request failed: %s. Stopping AI analysis for this run; unprocessed articles "
                          "will be retried next run.", exc)
                return None
            return self._fail(stored.id, ErrorStage.ANALYZE, f"AI request failed: {exc}", summary)
        except AIResponseValidationError as exc:
            return self._fail(stored.id, ErrorStage.ANALYZE, f"AI answer failed validation: {exc}", summary)
        summary.analyzed += 1
        if isinstance(result, Rejection):
            self.storage.transition(stored.id, S.REJECTED, run_id=self.run_id, note=result.reason,
                                    rejection_reason=result.reason, error_stage=None, last_error=None)
            summary.rejected += 1
            log.info("AI classification rejected: #%d '%s' - %s", stored.id, stored.title, result.reason)
            return None
        stored = self.storage.transition(
            stored.id, S.APPROVED, run_id=self.run_id, category=result.category,
            catchy_headline=result.headline, facebook_caption=result.caption, error_stage=None, last_error=None,
        )
        summary.approved += 1
        log.info("AI classification approved: #%d [%s] %s", stored.id, result.category.value, result.headline)
        return stored

    def _render(self, stored: StoredArticle, summary: RunSummary) -> StoredArticle | None:
        content = stored.approved_content
        if content is None:
            return self._fail(stored.id, ErrorStage.IMAGE, "approved content is missing", summary)
        path = image_path_for(self.settings.images_dir, stored.url_hash, stored.published_at or stored.discovered_at)
        try:
            layout = self.renderer.render_to_file(content.category, content.headline, path)
        except (ImageGenerationError, OSError, ValueError) as exc:
            return self._fail(stored.id, ErrorStage.IMAGE, f"Image generation failed: {exc}", summary)
        if layout.truncated:
            log.warning("Headline for #%d was truncated on the card; review it carefully", stored.id)
        log.info("Image generated: #%d %s", stored.id, path)
        return self.storage.transition(stored.id, S.IMAGE_CREATED, run_id=self.run_id, image_path=path,
                                       error_stage=None, last_error=None)

    def _finish_preview(self, stored: StoredArticle, summary: RunSummary) -> None:
        preview = write_preview(stored, self.settings.previews_dir, self.settings.fb_graph_api_version)
        stored = self.storage.transition(stored.id, S.READY_FOR_REVIEW, run_id=self.run_id, preview_path=preview)
        summary.ready_for_review += 1
        log.info("Preview ready: #%d %s", stored.id, preview)
        if self.publisher is None:
            self.report(format_dry_run_block(stored))

    def _fail(self, article_id: int, stage: ErrorStage, message: str, summary: RunSummary) -> None:
        failed = self.storage.mark_failed(article_id, stage, message, self.run_id)
        summary.failed += 1
        log.error("Article #%d failed at %s (attempt %d of %d): %s", article_id, stage.value, failed.retry_count,
                  self.settings.max_retries_per_article, message)

    # ------------------------------------------------ future live publishing
    def _publish_ready(self, summary: RunSummary) -> None:
        candidates = self.storage.publishable_articles(self.settings.max_retries_per_article)
        if self.options.publish_ids is not None:
            # An explicit human choice: publish exactly these, whatever their age.
            chosen = [a for a in candidates if a.id in self.options.publish_ids]
            missing = sorted(self.options.publish_ids - {a.id for a in chosen})
            if missing:
                log.warning("Not publishable (not READY_FOR_REVIEW, already posted, or unknown): %s", missing)
        else:
            cutoff = self._cutoff()
            chosen = [a for a in candidates if is_fresh(a.published_at, cutoff)]
            if len(chosen) < len(candidates):
                log.info("%d reviewed article(s) are older than %sh and were not published automatically; "
                         "publish them deliberately with --publish --ids", len(candidates) - len(chosen),
                         self.options.max_age_hours)
        for stored in chosen[: self.options.limit]:
            if not self._may_write():
                break
            self._publish_one(stored, summary)

    def _publish_one(self, stored: StoredArticle, summary: RunSummary) -> None:
        assert self.publisher is not None and stored.image_path is not None  # guaranteed by schema CHECKs
        # Persisted BEFORE the external call: a crash from here on is recovered as "outcome unknown".
        self.storage.transition(stored.id, S.PUBLISHING, run_id=self.run_id, last_attempt_at=self.storage.now())
        try:
            result = self.publisher.publish_photo(stored.image_path, stored.facebook_caption or "")
        except FacebookError as exc:
            unknown = isinstance(exc, FacebookOutcomeUnknownError)
            stage = ErrorStage.PUBLISH_UNKNOWN if unknown else ErrorStage.PUBLISH_PHOTO
            self.storage.mark_failed(stored.id, stage, str(exc), self.run_id)
            self._fb_stopped = self._fb_stopped or stops_publishing(exc)
            log.error("Photo upload for #%d %s: %s", stored.id,
                      "has an unknown outcome; not retrying automatically (see --resolve-unknown)" if unknown
                      else "failed; no post exists", exc)
            return
        except Exception as exc:
            self.storage.mark_failed(stored.id, ErrorStage.PUBLISH_UNKNOWN, f"Unexpected error: {exc}", self.run_id)
            raise
        summary.facebook_writes += 1
        try:
            stored = self.storage.record_post_created(stored.id, result.photo_id, result.post_id, self.run_id)
        except Exception:
            log.critical("Post created for #%d (photo_id=%s, post_id=%s) but its state could not be updated; "
                         "the IDs were saved, so it is never uploaded again", stored.id, result.photo_id,
                         result.post_id)
            raise
        if self._may_write():
            self._comment(stored, summary)

    def _retry_pending_comments(self, summary: RunSummary) -> None:
        for stored in self.storage.comment_pending_articles(self.settings.max_retries_per_article):
            if not self._may_write():
                return
            log.info("Existing post %s detected for #%d; retrying the missing comment only",
                     stored.facebook_target, stored.id)
            self._comment(stored, summary)

    def _comment(self, stored: StoredArticle, summary: RunSummary) -> None:
        assert self.publisher is not None
        target = stored.facebook_target or ""
        message = comment_text(stored.original_url)
        if stored.error_stage is ErrorStage.COMMENT_UNKNOWN:
            # The previous attempt may have succeeded: reconcile (read-only) before posting again.
            try:
                existing = self.publisher.find_comment(target, message)
            except FacebookError as exc:
                self._fb_stopped = self._fb_stopped or stops_publishing(exc)
                log.warning("Could not check #%d for an existing source comment (%s); will try next run",
                            stored.id, exc)
                return
            if existing:
                self.storage.record_comment_created(stored.id, existing, self.run_id)
                log.info("Found the earlier source comment %s for #%d; not posting it again", existing, stored.id)
                return
        # Persisted BEFORE the request: a crash mid-request is reconciled by the next run.
        self.storage.mark_comment_in_flight(stored.id, self.run_id)
        try:
            comment_id = self.publisher.create_comment(target, message)
        except FacebookError as exc:
            unknown = isinstance(exc, FacebookOutcomeUnknownError)
            stage = ErrorStage.COMMENT_UNKNOWN if unknown else ErrorStage.PUBLISH_COMMENT
            self.storage.record_comment_failure(stored.id, str(exc), self.run_id, stage=stage)
            self._fb_stopped = self._fb_stopped or stops_publishing(exc)
            log.warning("Source comment for #%d failed (%s); the post is kept and the next run will %s: %s",
                        stored.id, stage.value, "check for the comment before retrying" if unknown else "retry it",
                        exc)
            return
        self.storage.record_comment_created(stored.id, comment_id, self.run_id)
        summary.facebook_writes += 1

    # ------------------------------------------------------------ review page
    def _write_review_page(self) -> None:
        counts = self.storage.status_counts()
        posted = sum(counts.get(s.value, 0) for s in (S.POST_CREATED, S.COMMENT_PENDING, S.PUBLISHED))
        try:
            write_review_page(
                self.settings.review_page,
                ready=self.storage.list_by_status(S.READY_FOR_REVIEW, limit=100),
                rejected=self.storage.list_by_status(S.REJECTED, limit=30),
                failed=self.storage.list_by_status(S.FAILED, limit=30),
                mode_label="DEMO (MOCKED AI)" if self.options.mode == "demo" else self.options.mode.upper(),
                posted=posted,
            )
        except (OSError, ValueError) as exc:
            log.error("Could not write the review page: %s", exc)


def interleave_by_source(articles: list[Article]) -> list[Article]:
    """Round-robin across feeds (newest first within each feed, and within each round), so one
    busy feed or sector cannot use up the whole per-run limit."""
    by_source: dict[str, list[Article]] = {}
    for article in sorted(articles, key=lambda a: a.published_at or a.discovered_at, reverse=True):
        by_source.setdefault(article.source_name, []).append(article)
    ordered: list[Article] = []
    for rank in range(max((len(group) for group in by_source.values()), default=0)):
        round_ = [group[rank] for group in by_source.values() if rank < len(group)]
        ordered += sorted(round_, key=lambda a: a.published_at or a.discovered_at, reverse=True)
    return ordered


def is_fresh(published_at: datetime | None, cutoff: datetime) -> bool:
    """The age rule used everywhere (pipeline and --check-feeds): dated and not older than the cutoff."""
    return published_at is not None and published_at >= cutoff


def _needs_ai(stored: StoredArticle) -> bool:
    return stored.status is S.DISCOVERED or (stored.status is S.FAILED and stored.error_stage is ErrorStage.ANALYZE)


def _to_article(stored: StoredArticle) -> Article:
    return Article(
        title=stored.title, summary=stored.summary, original_url=stored.original_url,
        canonical_url=stored.canonical_url, url_hash=stored.url_hash, source_name=stored.source_name,
        feed_url=stored.feed_url or "", published_at=stored.published_at, discovered_at=stored.discovered_at,
    )
