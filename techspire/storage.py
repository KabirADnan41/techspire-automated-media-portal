"""SQLite state store: duplicate protection, explicit state machine, audit trail, run lock.

Design rules
- Duplicate protection lives in the database: UNIQUE(url_hash) and UNIQUE(canonical_url).
- Every status change is a compare-and-set inside a BEGIN IMMEDIATE transaction, is
  checked against enums.ALLOWED_TRANSITIONS (a FAILED article may only resume at the
  stage that failed), and writes an article_events audit row.
- CHECK constraints make it impossible to reach an approved/rendered/published state
  without the content, image and Facebook IDs that state implies.
- A trigger makes it impossible to move an article that already has a Facebook
  photo/post ID back into PUBLISHING, so a post can never be uploaded twice.
- Facebook IDs are written (and committed) the moment they are known.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from techspire.enums import (
    ALLOWED_TRANSITIONS,
    OPERATOR_TRANSITIONS,
    RESETS_RETRY_BUDGET,
    RESUME_FROM_FAILED,
    ArticleStatus,
    Category,
    ErrorStage,
)
from techspire.exceptions import AlreadyRunningError, InvalidStateTransition, StorageError
from techspire.logging_config import redact_short
from techspire.models import FACEBOOK_OBJECT_ID, Article, StoredArticle, utcnow

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # v2: five editorial sectors (category CHECK list changed)
SAME_STORY_WINDOW = timedelta(days=7)
MAX_ERROR_CHARS = 1000

S = ArticleStatus
_STATUS_SQL = ", ".join(f"'{s.value}'" for s in ArticleStatus)
_CATEGORY_SQL = ", ".join(f"'{c.value}'" for c in Category)
_ORDER_NEWEST = "ORDER BY COALESCE(published_at, discovered_at) DESC, id DESC"

_ARTICLES_DDL = """
CREATE TABLE {exists}{table} (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash            TEXT NOT NULL UNIQUE,
    original_url        TEXT NOT NULL,
    canonical_url       TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    title_fingerprint   TEXT,
    summary             TEXT NOT NULL DEFAULT '',
    source_name         TEXT NOT NULL,
    feed_url            TEXT,
    author              TEXT,
    guid                TEXT,
    language            TEXT,
    published_at        TEXT,
    discovered_at       TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ({statuses})),
    category            TEXT CHECK (category IS NULL OR category IN ({categories})),
    catchy_headline     TEXT,
    facebook_caption    TEXT,
    rejection_reason    TEXT,
    image_path          TEXT,
    preview_path        TEXT,
    facebook_photo_id   TEXT,
    facebook_post_id    TEXT,
    facebook_comment_id TEXT,
    error_stage         TEXT,
    last_error          TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    last_attempt_at     TEXT,
    last_run_id         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (status NOT IN ('APPROVED', 'IMAGE_CREATED', 'READY_FOR_REVIEW', 'PUBLISHING', 'POST_CREATED',
                          'COMMENT_PENDING', 'PUBLISHED')
           OR (category IS NOT NULL AND catchy_headline IS NOT NULL AND facebook_caption IS NOT NULL)),
    CHECK (status NOT IN ('IMAGE_CREATED', 'READY_FOR_REVIEW', 'PUBLISHING', 'POST_CREATED',
                          'COMMENT_PENDING', 'PUBLISHED')
           OR image_path IS NOT NULL),
    CHECK (status NOT IN ('POST_CREATED', 'COMMENT_PENDING', 'PUBLISHED')
           OR facebook_photo_id IS NOT NULL OR facebook_post_id IS NOT NULL),
    CHECK (status <> 'PUBLISHED' OR facebook_comment_id IS NOT NULL)
) STRICT;
"""


def _articles_ddl(table: str = "articles", categories: str = _CATEGORY_SQL, if_not_exists: bool = True) -> str:
    return _ARTICLES_DDL.format(exists="IF NOT EXISTS " if if_not_exists else "", table=table,
                                statuses=_STATUS_SQL, categories=categories)


_SCHEMA_REST = """
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_fingerprint ON articles(title_fingerprint);

CREATE TRIGGER IF NOT EXISTS prevent_second_upload
BEFORE UPDATE OF status ON articles
WHEN NEW.status = 'PUBLISHING'
     AND (OLD.facebook_photo_id IS NOT NULL OR OLD.facebook_post_id IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'article already has a Facebook post; refusing to upload again');
END;

CREATE TABLE IF NOT EXISTS article_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    run_id      TEXT,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_events_article ON article_events(article_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    mode         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    outcome      TEXT,
    summary_json TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS run_lock (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    run_id      TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at  TEXT NOT NULL
) STRICT;
"""

# Columns a transition may set. Column names never come from outside this module.
_UPDATABLE = frozenset({
    "category", "catchy_headline", "facebook_caption", "rejection_reason", "image_path",
    "preview_path", "facebook_photo_id", "facebook_post_id", "facebook_comment_id",
    "error_stage", "last_error", "retry_count", "last_attempt_at",
})


def _to_db(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to storage")
        return value.astimezone(UTC).isoformat(timespec="seconds")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (ArticleStatus, Category, ErrorStage)):
        return value.value
    return value


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True, slots=True)
class Registration:
    article_id: int
    is_new: bool
    status: ArticleStatus
    duplicate_of: int | None = None  # set when rejected as the same story


class Storage:
    def __init__(self, path: Path, clock: Callable[[], datetime] = utcnow) -> None:
        self.path = path
        self._clock = clock
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._init_schema()
        except (sqlite3.Error, OSError) as exc:
            raise StorageError(
                f"Unable to open {path}. Check that the data directory is writable "
                f"and the file is not open in another program. ({exc})"
            ) from exc

    # ------------------------------------------------------------------ basics
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StorageError(
                f"{self.path} was created by a newer Techspire version (schema {version}). "
                "Upgrade the application before using this database."
            )
        has_articles = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'").fetchone()
        if has_articles and version < SCHEMA_VERSION:
            self._rebuild_articles(version)
        self._conn.executescript(_articles_ddl() + _SCHEMA_REST)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _rebuild_articles(self, from_version: int) -> None:
        """Carry every row into a table with the current constraints (e.g. the new category
        list), mapping legacy categories through Category.normalize. Ids and history are kept."""
        log.warning("Upgrading %s from schema %d to %d", self.path, from_version, SCHEMA_VERSION)
        self._conn.execute("PRAGMA foreign_keys = OFF")  # the audit table references articles(id)
        try:
            with self._tx() as conn:
                conn.execute("DROP TABLE IF EXISTS articles_upgrade")
                conn.execute(_articles_ddl("articles_upgrade", if_not_exists=False))
                columns = [r["name"] for r in conn.execute("PRAGMA table_info(articles)")]
                marks = ", ".join("?" for _ in columns)
                for row in conn.execute("SELECT * FROM articles").fetchall():
                    values = dict(row)
                    if values.get("category"):
                        mapped = Category.normalize(values["category"])
                        values["category"] = mapped.value if mapped else None
                    conn.execute(f"INSERT INTO articles_upgrade ({', '.join(columns)}) VALUES ({marks})",
                                 [values[c] for c in columns])
                conn.execute("DROP TABLE articles")
                conn.execute("ALTER TABLE articles_upgrade RENAME TO articles")
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic write transaction (takes the SQLite write lock up front)."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def now(self) -> datetime:
        return self._clock()

    # --------------------------------------------------------------- run lock
    def acquire_run_lock(self, run_id: str, ttl: timedelta = timedelta(minutes=30)) -> None:
        now = self.now()
        with self._tx() as conn:
            row = conn.execute("SELECT run_id, expires_at FROM run_lock WHERE id = 1").fetchone()
            if row is not None and _dt(row["expires_at"]) > now:
                raise AlreadyRunningError(
                    f"Another Techspire run ({row['run_id']}) is in progress (lock expires "
                    f"{row['expires_at']}). Wait for it to finish; a crashed run's lock expires on its own."
                )
            if row is not None:
                log.warning("Taking over expired run lock from %s", row["run_id"])
            conn.execute(
                "INSERT OR REPLACE INTO run_lock (id, run_id, acquired_at, expires_at) VALUES (1, ?, ?, ?)",
                (run_id, _to_db(now), _to_db(now + ttl)),
            )

    def refresh_run_lock(self, run_id: str, ttl: timedelta = timedelta(minutes=30)) -> None:
        """Extend our lock; raises if another run has taken it over (stop before writing anything)."""
        with self._tx() as conn:
            cursor = conn.execute("UPDATE run_lock SET expires_at = ? WHERE id = 1 AND run_id = ?",
                                  (_to_db(self.now() + ttl), run_id))
            if cursor.rowcount != 1:
                raise AlreadyRunningError(f"Run {run_id} lost its run lock to another run; stopping safely.")

    def release_run_lock(self, run_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM run_lock WHERE id = 1 AND run_id = ?", (run_id,))

    def start_run(self, run_id: str, mode: str) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO runs (run_id, mode, started_at) VALUES (?, ?, ?)",
                         (run_id, mode, _to_db(self.now())))

    def finish_run(self, run_id: str, outcome: str, summary: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, outcome = ?, summary_json = ? WHERE run_id = ?",
                (_to_db(self.now()), outcome, json.dumps(summary, default=str), run_id),
            )

    # ------------------------------------------------------------ registration
    def register(self, article: Article, run_id: str | None = None) -> Registration:
        """Insert a newly discovered article unless its canonical URL is already known.

        Safe under restarts and concurrent runs: the UNIQUE constraint decides.
        An exact title-fingerprint match with a recent article is recorded as
        REJECTED (same story) so it never reaches the AI or the page.
        """
        now = self.now()
        values = {
            "url_hash": article.url_hash, "original_url": article.original_url,
            "canonical_url": article.canonical_url, "title": article.title,
            "title_fingerprint": article.title_fingerprint, "summary": article.summary,
            "source_name": article.source_name, "feed_url": article.feed_url,
            "author": article.author, "guid": article.guid, "language": article.language,
            "published_at": _to_db(article.published_at), "discovered_at": _to_db(article.discovered_at),
            "status": S.DISCOVERED.value, "last_run_id": run_id, "created_at": _to_db(now),
            "updated_at": _to_db(now),
        }
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self._tx() as conn:
            cursor = conn.execute(
                f"INSERT INTO articles ({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                tuple(values.values()),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT id, status FROM articles WHERE url_hash = ? OR canonical_url = ?",
                    (article.url_hash, article.canonical_url),
                ).fetchone()
                return Registration(row["id"], False, ArticleStatus(row["status"]))

            article_id = conn.execute("SELECT id FROM articles WHERE url_hash = ?",
                                      (article.url_hash,)).fetchone()["id"]
            self._event(conn, article_id, run_id, None, S.DISCOVERED, "discovered")

            if article.title_fingerprint:
                twin = conn.execute(
                    "SELECT id, source_name FROM articles WHERE title_fingerprint = ? AND id <> ? "
                    "AND discovered_at >= ? ORDER BY id LIMIT 1",
                    (article.title_fingerprint, article_id, _to_db(now - SAME_STORY_WINDOW)),
                ).fetchone()
                if twin is not None:
                    reason = f"Duplicate story: same headline as article #{twin['id']} ({twin['source_name']})."
                    self._check_transition(article_id, S.DISCOVERED, None, S.REJECTED)
                    self._update(conn, article_id, S.DISCOVERED, S.REJECTED, run_id, False,
                                 {"rejection_reason": reason})
                    self._event(conn, article_id, run_id, S.DISCOVERED, S.REJECTED, reason)
                    return Registration(article_id, True, S.REJECTED, duplicate_of=twin["id"])
        return Registration(article_id, True, S.DISCOVERED)

    # ----------------------------------------------------------------- reading
    def get(self, article_id: int) -> StoredArticle:
        row = self._conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            raise StorageError(f"Article #{article_id} does not exist.")
        return _row_to_article(row)

    def known_url_hashes(self, hashes: list[str]) -> set[str]:
        """Which of these URL hashes are already in the database (one query per 500)."""
        known: set[str] = set()
        for start in range(0, len(hashes), 500):
            chunk = hashes[start:start + 500]
            marks = ", ".join("?" for _ in chunk)
            rows = self._conn.execute(f"SELECT url_hash FROM articles WHERE url_hash IN ({marks})", chunk)
            known.update(r["url_hash"] for r in rows)
        return known

    def list_by_status(self, *statuses: ArticleStatus, limit: int | None = None) -> list[StoredArticle]:
        marks = ", ".join("?" for _ in statuses)
        sql = f"SELECT * FROM articles WHERE status IN ({marks}) {_ORDER_NEWEST}"
        params: list[Any] = [s.value for s in statuses]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_article(r) for r in self._conn.execute(sql, params)]

    def resumable_articles(self, max_retries: int) -> list[StoredArticle]:
        """Unfinished local work: interrupted steps and retryable analysis/image failures."""
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE status IN (?, ?, ?) "
            f"OR (status = ? AND error_stage IN (?, ?) AND retry_count < ?) {_ORDER_NEWEST}",
            (S.DISCOVERED.value, S.APPROVED.value, S.IMAGE_CREATED.value, S.FAILED.value,
             ErrorStage.ANALYZE.value, ErrorStage.IMAGE.value, max_retries),
        )
        return [_row_to_article(r) for r in rows]

    def publishable_articles(self, max_retries: int) -> list[StoredArticle]:
        """READY_FOR_REVIEW articles plus definitive (not unknown-outcome) photo failures."""
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE facebook_photo_id IS NULL AND facebook_post_id IS NULL AND "
            f"(status = ? OR (status = ? AND error_stage = ? AND retry_count < ?)) {_ORDER_NEWEST}",
            (S.READY_FOR_REVIEW.value, S.FAILED.value, ErrorStage.PUBLISH_PHOTO.value, max_retries),
        )
        return [_row_to_article(r) for r in rows]

    def comment_pending_articles(self, max_retries: int) -> list[StoredArticle]:
        """Posts that exist on Facebook but still lack the source-link comment."""
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE status IN (?, ?) AND facebook_comment_id IS NULL "
            "AND retry_count < ? ORDER BY id",
            (S.POST_CREATED.value, S.COMMENT_PENDING.value, max_retries),
        )
        return [_row_to_article(r) for r in rows]

    def unknown_outcome_articles(self) -> list[StoredArticle]:
        """Photo uploads whose outcome is unknown: they wait for a human (see --resolve-unknown)."""
        rows = self._conn.execute("SELECT * FROM articles WHERE status = ? AND error_stage = ? ORDER BY id",
                                  (S.FAILED.value, ErrorStage.PUBLISH_UNKNOWN.value))
        return [_row_to_article(r) for r in rows]

    def status_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM articles GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def events(self, article_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT run_id, from_status, to_status, note, created_at FROM article_events "
            "WHERE article_id = ? ORDER BY id", (article_id,))
        return [dict(r) for r in rows]

    def recent_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- transitions
    def transition(self, article_id: int, to_status: ArticleStatus, *, run_id: str | None = None,
                   note: str | None = None, increment_retry: bool = False,
                   **fields: Any) -> StoredArticle:
        """Atomically move an article to `to_status`, setting `fields` in the same write."""
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        with self._tx() as conn:
            current, stage = self._current_state(conn, article_id)
            self._check_transition(article_id, current, stage, to_status)
            self._update(conn, article_id, current, to_status, run_id, increment_retry, fields)
            self._event(conn, article_id, run_id, current, to_status, note)
        return self.get(article_id)

    def mark_failed(self, article_id: int, stage: ErrorStage, error: str, run_id: str | None = None) -> StoredArticle:
        message = redact_short(error, MAX_ERROR_CHARS)
        return self.transition(
            article_id, S.FAILED, run_id=run_id, note=f"{stage.value}: {message}", increment_retry=True,
            error_stage=stage, last_error=message, last_attempt_at=self.now(),
        )

    def save_facebook_ids(self, article_id: int, photo_id: str, post_id: str | None,
                          run_id: str | None = None) -> None:
        """Commit Facebook IDs on their own, before any status change: the IDs alone are
        enough to block a second upload (see prevent_second_upload)."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE articles SET facebook_photo_id = ?, facebook_post_id = ?, updated_at = ? "
                "WHERE id = ? AND facebook_photo_id IS NULL AND facebook_post_id IS NULL",
                (photo_id, post_id, _to_db(self.now()), article_id),
            )
            current, _ = self._current_state(conn, article_id)
            self._event(conn, article_id, run_id, current, current,
                        f"Facebook IDs recorded: photo_id={photo_id} post_id={post_id}")

    def record_post_created(self, article_id: int, photo_id: str, post_id: str | None,
                            run_id: str | None = None) -> StoredArticle:
        """Persist Facebook identifiers immediately after a successful upload. The IDs are
        committed first, so they survive even if the status change is refused."""
        self.save_facebook_ids(article_id, photo_id, post_id, run_id)
        return self.transition(
            article_id, S.POST_CREATED, run_id=run_id, note=f"photo_id={photo_id} post_id={post_id}",
            error_stage=None, last_error=None, last_attempt_at=self.now(),
        )

    def mark_comment_in_flight(self, article_id: int, run_id: str | None = None) -> StoredArticle:
        """Saved right before the comment request: if the run dies mid-request, the next run
        checks for the comment before posting it again."""
        return self.transition(
            article_id, S.COMMENT_PENDING, run_id=run_id, note="comment request sent",
            error_stage=ErrorStage.COMMENT_UNKNOWN, last_error="Comment request in progress; outcome not yet known.",
            last_attempt_at=self.now(),
        )

    def record_comment_created(self, article_id: int, comment_id: str, run_id: str | None = None) -> StoredArticle:
        return self.transition(
            article_id, S.PUBLISHED, run_id=run_id, note=f"comment_id={comment_id}",
            facebook_comment_id=comment_id, error_stage=None, last_error=None, last_attempt_at=self.now(),
        )

    def record_comment_failure(self, article_id: int, error: str, run_id: str | None = None,
                               stage: ErrorStage = ErrorStage.PUBLISH_COMMENT) -> StoredArticle:
        """Keep the post and remember the failure for the next run. COMMENT_UNKNOWN means the
        comment may exist: the next run checks the post's comments before posting again."""
        message = redact_short(error, MAX_ERROR_CHARS)
        return self.transition(
            article_id, S.COMMENT_PENDING, run_id=run_id, note=f"comment failed: {message}",
            increment_retry=True, error_stage=stage, last_error=message, last_attempt_at=self.now(),
        )

    def resolve_unknown_publish(self, article_id: int, *, post_id: str | None,
                                run_id: str | None = None) -> StoredArticle:
        """Record an operator's manual check of an upload whose outcome was unknown.

        post_id given -> the post exists: POST_CREATED (the next --publish run adds only the comment).
        post_id None  -> nothing was posted: eligible for publishing again (still needs --publish).
        """
        if post_id is not None and not FACEBOOK_OBJECT_ID.fullmatch(post_id):
            raise StorageError("The post ID must look like 123456789_987654321 (digits and one underscore).")
        with self._tx() as conn:
            current, stage = self._current_state(conn, article_id)
            if current is not S.FAILED or stage is not ErrorStage.PUBLISH_UNKNOWN:
                raise InvalidStateTransition(f"Article #{article_id} is not waiting for an unknown-outcome decision.")
            if post_id is not None:
                to_status, note = S.POST_CREATED, f"operator confirmed the post exists: post_id={post_id}"
                fields: dict[str, Any] = {"facebook_post_id": post_id, "error_stage": None, "last_error": None}
            else:
                ids = conn.execute("SELECT facebook_photo_id, facebook_post_id FROM articles WHERE id = ?",
                                   (article_id,)).fetchone()
                if ids["facebook_photo_id"] or ids["facebook_post_id"]:
                    raise InvalidStateTransition(
                        f"Article #{article_id} has Facebook IDs recorded, so it was posted; use --post-id.")
                to_status, note = S.FAILED, "operator confirmed nothing was posted"
                fields = {"error_stage": ErrorStage.PUBLISH_PHOTO,
                          "last_error": "Operator confirmed nothing was posted."}
            assert to_status in OPERATOR_TRANSITIONS[current]
            self._update(conn, article_id, current, to_status, run_id, False, {**fields, "retry_count": 0})
            self._event(conn, article_id, run_id, current, to_status, note)
        return self.get(article_id)

    def recover_interrupted(self, run_id: str) -> int:
        """Called at run start (while holding the run lock): no other run can be mid-step.

        ANALYZING  -> DISCOVERED        (no external side effect happened; safe to redo)
        PUBLISHING -> FAILED(publish_unknown)  (the upload may have happened; needs a human)
        """
        recovered = 0
        for stored in self.list_by_status(S.ANALYZING):
            self.transition(stored.id, S.DISCOVERED, run_id=run_id, note="recovered after interrupted run")
            recovered += 1
        for stored in self.list_by_status(S.PUBLISHING):
            self.mark_failed(
                stored.id, ErrorStage.PUBLISH_UNKNOWN,
                "A previous run stopped during the Facebook photo upload, so the outcome is unknown. "
                "Check the Page manually before re-queuing this article.", run_id,
            )
            recovered += 1
        return recovered

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _current_state(conn: sqlite3.Connection, article_id: int) -> tuple[ArticleStatus, ErrorStage | None]:
        row = conn.execute("SELECT status, error_stage FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            raise StorageError(f"Article #{article_id} does not exist.")
        return ArticleStatus(row["status"]), ErrorStage(row["error_stage"]) if row["error_stage"] else None

    @staticmethod
    def _check_transition(article_id: int, current: ArticleStatus, stage: ErrorStage | None,
                          to_status: ArticleStatus) -> None:
        if to_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidStateTransition(
                f"Article #{article_id}: {current.value} -> {to_status.value} is not an allowed transition."
            )
        if current is S.FAILED and RESUME_FROM_FAILED.get(stage) is not to_status:
            raise InvalidStateTransition(
                f"Article #{article_id} failed at '{stage}' and cannot resume at {to_status.value}."
            )

    def _update(self, conn: sqlite3.Connection, article_id: int, current: ArticleStatus,
                to_status: ArticleStatus, run_id: str | None, increment_retry: bool,
                fields: dict[str, Any]) -> None:
        assignments = ["status = ?", "last_run_id = ?", "updated_at = ?"]
        params: list[Any] = [to_status.value, run_id, _to_db(self.now())]
        for name, value in fields.items():
            assignments.append(f"{name} = ?")
            params.append(_to_db(value))
        if increment_retry:
            assignments.append("retry_count = retry_count + 1")
        elif to_status in RESETS_RETRY_BUDGET and current is not S.FAILED and "retry_count" not in fields:
            # Reached by completing the previous stage (not by retrying a failed one): fresh budget.
            assignments.append("retry_count = 0")
        params += [article_id, current.value]
        try:
            cursor = conn.execute(
                f"UPDATE articles SET {', '.join(assignments)} WHERE id = ? AND status = ?", params
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidStateTransition(f"Article #{article_id}: {exc}") from exc
        if cursor.rowcount != 1:
            raise InvalidStateTransition(f"Article #{article_id} changed state concurrently; update refused.")

    def _event(self, conn: sqlite3.Connection, article_id: int, run_id: str | None,
               from_status: ArticleStatus | None, to_status: ArticleStatus, note: str | None) -> None:
        conn.execute(
            "INSERT INTO article_events (article_id, run_id, from_status, to_status, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (article_id, run_id, from_status.value if from_status else None, to_status.value,
             note, _to_db(self.now())),
        )


def _row_to_article(row: sqlite3.Row) -> StoredArticle:
    return StoredArticle(
        id=row["id"], url_hash=row["url_hash"], original_url=row["original_url"],
        canonical_url=row["canonical_url"], title=row["title"], summary=row["summary"],
        source_name=row["source_name"], feed_url=row["feed_url"],
        published_at=_dt(row["published_at"]), discovered_at=_dt(row["discovered_at"]),
        status=ArticleStatus(row["status"]),
        category=Category(row["category"]) if row["category"] else None,
        catchy_headline=row["catchy_headline"], facebook_caption=row["facebook_caption"],
        rejection_reason=row["rejection_reason"],
        image_path=Path(row["image_path"]) if row["image_path"] else None,
        preview_path=Path(row["preview_path"]) if row["preview_path"] else None,
        facebook_photo_id=row["facebook_photo_id"], facebook_post_id=row["facebook_post_id"],
        facebook_comment_id=row["facebook_comment_id"],
        error_stage=ErrorStage(row["error_stage"]) if row["error_stage"] else None,
        last_error=row["last_error"], retry_count=row["retry_count"],
        last_attempt_at=_dt(row["last_attempt_at"]), created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )
