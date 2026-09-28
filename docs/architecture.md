# Architecture

Techspire is a single-process Python worker: **fetch -> process -> exit**. The operating system scheduler
(Task Scheduler, cron, systemd) starts it; SQLite remembers everything between runs. There are no services,
queues or brokers.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Load `.env` + environment into a frozen `Settings`; fail-closed validation; secrets hidden from `repr()` |
| `enums.py` | `ArticleStatus`, allowed state transitions, `ErrorStage`, the five-sector `Category` enum and its alias mapping (legacy labels included) |
| `exceptions.py` | Error taxonomy (config, storage, feed, AI, image, Facebook: refused / not sent / outcome unknown) |
| `logging_config.py` | Console + rotating file logs in UTC; every record passes through `redact()` |
| `url_utils.py` | Conservative canonical URL (drops `utm_*`, `fbclid`, `gclid` ...), SHA-256 URL hash |
| `text_cleaner.py` | Untrusted HTML -> bounded plain text; conservative title fingerprint |
| `rss_fetcher.py` | Feed list validation; httpx download (timeout, User-Agent, retries, size cap, SSRF guard); feedparser |
| `storage.py` | SQLite schema (`SCHEMA_VERSION`, automatic table rebuild on upgrade), duplicate protection, state machine, audit events, run lock, recovery |
| `content_filter.py` | Editorial prompt, JSON schema, Gemini/OpenAI/fixture providers, retries, output validation |
| `image_generator.py` | 1200x630 card; `textbbox` measurement, balanced wrapping, font fitting, overflow protection |
| `preview.py` | Per-article JSON preview, HTML review page, console dry-run report |
| `fb_publisher.py` | Graph API client (photo post, source comment, comment lookup) behind the publishing gates |
| `pipeline.py` | Orchestration, per-article error boundaries, resume/retry logic, future publish flow |
| `main.py` | CLI: `--dry-run` (default), `--demo`, `--doctor`, `--check-feeds`, `--status`, `--publish` |

## Data flow

```text
config/feeds.yaml ──► rss_fetcher ──► text_cleaner / url_utils ──► storage.register (UNIQUE url_hash)
                                                                          │ new, fresh (<= 48 h)
                                                                          ▼
                     content_filter (AI + validation) ──► REJECTED (reason stored)
                                   │ approved
                                   ▼
                     image_generator ──► output/images/<date>_<hash>.jpg
                                   │
                                   ▼
                     preview ──► output/previews/<date>_<hash>.json + index.html ──► READY_FOR_REVIEW
                                   │  (only with DRY_RUN=false + PUBLISH_ENABLED=true + --publish)
                                   ▼
                     fb_publisher.publish_photo ──► POST_CREATED (IDs saved at once)
                                   ▼
                     fb_publisher.create_comment ──► PUBLISHED
```

## State machine

```text
DISCOVERED ─► ANALYZING ─┬─► REJECTED
                         ├─► APPROVED ─► IMAGE_CREATED ─► READY_FOR_REVIEW ─► PUBLISHING ─► POST_CREATED ─┬─► PUBLISHED
                         └─► FAILED                                                                        └─► COMMENT_PENDING ─► PUBLISHED
FAILED(analyze) ─► ANALYZING        FAILED(image) ─► APPROVED        FAILED(publish_photo) ─► PUBLISHING
ANALYZING ─► DISCOVERED  (only when recovering a crashed run)
```

Every transition is a compare-and-set `UPDATE ... WHERE id = ? AND status = ?` inside `BEGIN IMMEDIATE`, is checked
against `ALLOWED_TRANSITIONS` (a `FAILED` article may only resume at the stage that failed, per `RESUME_FROM_FAILED`),
and writes an `article_events` row (the audit trail). Database `CHECK` constraints make the implied content mandatory:
no approved state without category/headline/caption, no rendered state without an image, no post state without a
Facebook ID, and no `PUBLISHED` without a real comment ID. A normal dry run ends at `READY_FOR_REVIEW`. Each stage has
its own retry budget (reset when the previous stage completes).

## Duplicate protection

1. Canonical URL -> SHA-256 `url_hash`, `UNIQUE` in SQLite (plus `UNIQUE canonical_url`). Registration is
   `INSERT ... ON CONFLICT DO NOTHING`, so restarts, crashes, two runs or the same item in two feeds can never create
   a second record.
2. Conservative same-story check: an exact normalised title (at least 6 words) seen in the last 7 days is recorded as
   `REJECTED` ("Duplicate story ...") and never reaches the AI.
3. Facebook side: the `prevent_second_upload` trigger refuses to move any article that already has a photo or post ID
   into `PUBLISHING`, and `publishable_articles()` only returns rows without Facebook IDs.

## Failure handling

| Failure | What happens | Next run |
|---|---|---|
| One feed broken, slow or 5xx | Bounded retries (3 attempts, backoff), then that feed is skipped | Fetched again |
| AI provider down, rate limited after retries, bad key, unknown model | Article returns to `DISCOVERED`; AI calls stop for this run | Resumed automatically |
| AI answer breaks the contract | One correction round-trip, then `FAILED(analyze)` | Retried up to `MAX_RETRIES_PER_ARTICLE` |
| Image rendering fails | `FAILED(image)`; AI result kept | Re-rendered (no new AI call), bounded |
| Crash mid-analysis | Left in `ANALYZING` | Reset to `DISCOVERED` and redone (no side effects happened) |
| Unexpected bug in one article | Charged to that article's retry budget; batch continues | Retried, bounded |
| *Future:* photo upload refused (4xx, rate limit, never sent) | `FAILED(publish_photo)`; no post exists | Retried, bounded |
| *Future:* photo upload outcome unknown (timeout, 5xx, crash) | `FAILED(publish_unknown)`; **never retried automatically** | Human checks the Page (see runbook) |
| *Future:* post created, comment refused | `COMMENT_PENDING`; post ID already saved | **Only the comment** is retried |
| *Future:* comment outcome unknown, or the run died mid-request | `COMMENT_PENDING(comment_unknown)` (saved before every comment request) | Read-only lookup of the Page's own comments first; posts only if none exists |

## Safety interlock

`fb_publisher.closed_gates()` lists every closed gate: `DRY_RUN`, `PUBLISH_ENABLED`, the `--publish` flag and the
Facebook credentials. `authorize_publishing()` is the only way to obtain a `PublishPermit`; `FacebookPublisher`
requires one, re-checks the gates in its constructor and again before every request. The pipeline refuses a publisher
outside live mode, `main.py` never builds one in dry-run or demo mode, and the test suite blocks all real network
access. Before every Facebook write the run also re-verifies that it still owns the run lock.

## Concurrency

A single-row `run_lock` table (30-minute lease, refreshed during the run, verified before each Facebook write) stops
two runs on the same computer from working at once. On top of that every state change is a compare-and-set, so two
processes can never both claim the same article. Run Techspire on one machine only and keep `data/techspire.db` on a
local disk: SQLite locking is not reliable on network shares.

## Security measures

- Secrets only from `.env`/environment; excluded from `repr()`; redacted from every log line, stored error and message.
- Page token only in the `Authorization` header (RFC 6750), TLS verification on (httpx default), explicit timeouts.
- Feed content is untrusted: HTML parsed (never executed), scripts dropped, sizes bounded; article text only in the AI
  user turn; AI output validated before use; review page HTML-escaped with a restrictive Content-Security-Policy.
- File names come only from the date and the URL hash (validated hex), never from titles; writes are atomic.
- Feed fetches: only http(s); no `localhost`, numeric host tricks (`127.1`, `0x7f000001`) or hosts that resolve to
  private/loopback/link-local addresses (checked on every redirect; a DNS-rebinding race remains possible, which is why
  only operator-chosen feeds are fetched); no https-to-http redirects; uncompressed bodies only (no gzip bombs); at most
  10 MB and 60 seconds per feed; XML entity declarations refused; raw text cut to 20,000 characters before parsing.
- AI copy: bare web addresses must appear in the article, invisible/bidi control characters are refused, and live
  runs must name the reviewed cards to post (`--publish --ids`, `PUBLISH_REQUIRE_IDS=true`).
- All SQL uses `?` parameters; the only dynamic column names come from a fixed allow-list.
