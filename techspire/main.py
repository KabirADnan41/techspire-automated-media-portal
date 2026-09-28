"""Command-line entry point.

    python -m techspire.main                 dry run (the default; nothing is posted)
    python -m techspire.main --dry-run       same, explicitly
    python -m techspire.main --demo          offline demo: sample feed + recorded AI answers
    python -m techspire.main --doctor        read-only preflight checks
    python -m techspire.main --check-feeds   test every enabled feed (no AI, no database)
    python -m techspire.main --status        article counts by state and recent runs
    python -m techspire.main --publish       FUTURE USE ONLY; blocked unless every safety gate is open

Exit codes: 0 ok, 1 error, 2 another run is active, 3 publishing blocked.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import secrets
import subprocess
import sys
import tempfile
from dataclasses import astuple, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from techspire import __version__
from techspire.config import PROJECT_ROOT, Settings
from techspire.content_filter import AIContentService, BaseAIProvider, FixtureAIProvider, create_provider
from techspire.enums import ArticleStatus
from techspire.exceptions import AlreadyRunningError, ConfigError, PublishingDisabledError, TechspireError
from techspire.fb_publisher import FacebookPublisher, authorize_publishing, closed_gates
from techspire.image_generator import CardRenderer, FontSet
from techspire.logging_config import configure_logging
from techspire.models import format_utc, utcnow
from techspire.pipeline import Pipeline, RunOptions, is_fresh
from techspire.preview import RULE, format_summary
from techspire.rss_fetcher import FeedConfig, RSSFetcher, build_http_client, load_feeds
from techspire.storage import Storage

log = logging.getLogger("techspire")

DEMO_DIR = PROJECT_ROOT / "demo"
# The demo runs as if it were this moment, so its fixed sample dates are always "fresh".
DEMO_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DEMO_LIMIT = 50  # the offline demo processes every sample article
EXIT_OK, EXIT_ERROR, EXIT_ALREADY_RUNNING, EXIT_PUBLISH_BLOCKED = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m techspire.main",
        description="Techspire Official news automation. Default mode is a safe dry run: "
                    "nothing is ever posted to Facebook unless every safety gate is deliberately opened.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Fetch feeds, curate with AI and create local previews only (default).")
    mode.add_argument("--demo", action="store_true",
                      help="Offline demo with a bundled sample feed and recorded AI answers (AI is mocked).")
    mode.add_argument("--doctor", action="store_true",
                      help="Check configuration, folders, fonts and the publishing lock. Posts nothing.")
    mode.add_argument("--check-feeds", action="store_true",
                      help="Download every enabled feed and report what it contains (no AI, no database).")
    mode.add_argument("--status", action="store_true", help="Show article counts by state and recent runs.")
    mode.add_argument("--publish", action="store_true",
                      help="FUTURE USE: live Facebook publishing. Refused unless DRY_RUN=false and "
                           "PUBLISH_ENABLED=true are also set and Facebook credentials exist.")
    mode.add_argument("--resolve-unknown", type=int, metavar="ID",
                      help="After checking the Page by hand, record the outcome of an upload whose result was "
                           "unknown: add --post-id POST_ID if it was posted, or --not-posted.")
    parser.add_argument("--post-id", metavar="POST_ID", help="With --resolve-unknown: the post that exists.")
    parser.add_argument("--not-posted", action="store_true", help="With --resolve-unknown: nothing was posted.")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Process at most N articles this run (default: MAX_ARTICLES_PER_RUN).")
    parser.add_argument("--feed", action="append", metavar="NAME",
                        help="Only use this feed (name from config/feeds.yaml). Repeatable.")
    parser.add_argument("--ids", metavar="ID,ID", help="With --publish: only publish these reviewed article IDs.")
    parser.add_argument("--env-file", type=Path, metavar="PATH", help="Use this .env file instead of ./.env.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    if args.limit is not None and not 1 <= args.limit <= 200:
        print("--limit must be between 1 and 200.", file=sys.stderr)
        return EXIT_ERROR
    try:
        settings = Settings.load(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.demo:
        settings = _demo_settings(settings)
    try:
        log_file = configure_logging(settings.log_level, settings.log_dir, settings.secret_values(),
                                     verbose=args.verbose)
    except OSError as exc:
        print(f"Cannot write logs to {settings.log_dir}: {exc}. Check the folder is writable.", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.doctor:
            return run_doctor(settings)
        if args.status:
            return show_status(settings)
        if args.check_feeds:
            return check_feeds(settings, args.feed)
        if args.resolve_unknown is not None:
            return resolve_unknown(settings, args.resolve_unknown, args.post_id, args.not_posted)
        return run_pipeline(settings, args)
    except AlreadyRunningError as exc:
        print(f"Not started: {exc}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    except PublishingDisabledError as exc:
        print(_publish_blocked_message(str(exc)), file=sys.stderr)
        return EXIT_PUBLISH_BLOCKED
    except TechspireError as exc:
        log.error("%s", exc, extra={"console": False})
        print(f"\nError: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted. The next run recovers any unfinished article safely.", file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 - last line of defence; details go to the log
        log.exception("Unexpected failure")
        print(f"\nUnexpected error. Details were written to {log_file}.", file=sys.stderr)
        return EXIT_ERROR


# ----------------------------------------------------------------- pipeline
def run_pipeline(settings: Settings, args: argparse.Namespace) -> int:
    live = bool(args.publish)
    if live:
        authorize_publishing(settings, publish_flag=True)  # raises PublishingDisabledError with reasons
    mode = "demo" if args.demo else ("live" if live else "dry-run")
    if mode == "dry-run" and not settings.dry_run:
        log.warning("DRY_RUN=false in the configuration, but --publish was not given: running as a dry run")
    publish_ids = _parse_ids(args.ids) if args.ids else None
    if publish_ids is not None and not live:
        raise ConfigError("--ids only applies together with --publish.")
    if live and publish_ids is None and settings.publish_require_ids:
        raise PublishingDisabledError(
            "live runs must name the reviewed articles to post, e.g. --publish --ids 12,15 "
            "(set PUBLISH_REQUIRE_IDS=false only if you want every fresh reviewed card posted automatically)"
        )

    feeds = _feeds(settings, args.feed)
    limit = args.limit or (DEMO_LIMIT if args.demo else settings.max_articles_per_run)
    run_id = f"{utcnow():%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    provider: BaseAIProvider = _demo_provider() if args.demo else create_provider(settings)  # validates the key
    transport = _demo_transport() if args.demo else None
    clock = (lambda: DEMO_NOW) if args.demo else utcnow
    if args.demo:
        _reset_demo_database(settings.database_path)
    print(_banner(mode), flush=True)
    with Storage(settings.database_path, clock=clock) as storage, _feed_client(settings, transport) as http:
        publisher = None
        if live:
            permit = authorize_publishing(settings, publish_flag=True)
            publisher = FacebookPublisher(settings, permit, publish_flag=True)
        try:
            pipeline = Pipeline(
                settings, storage, RSSFetcher(http, clock=clock), feeds, AIContentService(provider),
                CardRenderer(FontSet.from_settings(settings), settings.brand_logo_path, settings.card_tagline),
                RunOptions(mode=mode, limit=limit, max_age_hours=settings.max_article_age_hours,
                           publish_ids=publish_ids),
                publisher=publisher, run_id=run_id,
            )
            summary = pipeline.run()
        finally:
            if publisher is not None:
                publisher.close()
    print(format_summary(summary, settings.review_page, _safety_line(settings, live)))
    return EXIT_OK


def _feeds(settings: Settings, names: list[str] | None) -> list[FeedConfig]:
    return _select_feeds(load_feeds(settings.feeds_file), names)


def _feed_client(settings: Settings, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return build_http_client(settings.rss_request_timeout, settings.rss_user_agent, transport)


def _select_feeds(feeds: list[FeedConfig], names: list[str] | None) -> list[FeedConfig]:
    if not names:
        return feeds
    wanted = {n.lower() for n in names}
    selected = [replace(f, enabled=True) for f in feeds if f.name.lower() in wanted]
    missing = wanted - {f.name.lower() for f in selected}
    if missing:
        available = ", ".join(f.name for f in feeds)
        raise ConfigError(f"Unknown feed name(s): {', '.join(sorted(missing))}. Available: {available}")
    return selected


def _parse_ids(raw: str) -> frozenset[int]:
    try:
        return frozenset(int(part) for part in raw.split(",") if part.strip())
    except ValueError:
        raise ConfigError("--ids must be a comma-separated list of article numbers, e.g. --ids 12,15") from None


def _banner(mode: str) -> str:
    title = {"dry-run": "TECHSPIRE OFFICIAL - DRY RUN", "demo": "TECHSPIRE OFFICIAL - OFFLINE DEMO",
             "live": "TECHSPIRE OFFICIAL - LIVE PUBLISHING RUN"}[mode]
    lines = [RULE, title]
    if mode == "demo":
        lines.append("Sample feed + recorded AI answers (AI is MOCKED, no network calls).")
    lines.append("LIVE FACEBOOK PUBLISHING: ENABLED FOR THIS RUN" if mode == "live"
                 else "LIVE FACEBOOK PUBLISHING: DISABLED")
    lines.append(RULE)
    return "\n".join(lines)


def _lock_flags(settings: Settings) -> str:
    return f"DRY_RUN={str(settings.dry_run).lower()}, PUBLISH_ENABLED={str(settings.publish_enabled).lower()}"


def _safety_line(settings: Settings, live: bool) -> str:
    if live:
        return "LIVE FACEBOOK PUBLISHING: ENABLED (all gates were open for this run)"
    return f"LIVE FACEBOOK PUBLISHING: DISABLED ({_lock_flags(settings)}, --publish not given)"


def _publish_blocked_message(reason: str) -> str:
    return (f"\nPUBLISHING BLOCKED. {reason}\n"
            "Nothing was fetched, analyzed or posted. Live publishing needs a deliberate decision: see\n"
            "'Future live publishing' in README.md. For a safe run use: python -m techspire.main --dry-run")


# --------------------------------------------------------------------- demo
def _demo_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        database_path=PROJECT_ROOT / "data" / "demo.db",
        output_dir=PROJECT_ROOT / "output" / "demo",
        feeds_file=DEMO_DIR / "feeds.yaml",
        dry_run=True,
        publish_enabled=False,
    )


def _reset_demo_database(path: Path) -> None:
    """The demo always starts from an empty database of its own (never the real one)."""
    if path.name != "demo.db":
        raise ConfigError("Refusing to reset a database that is not the demo database.")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _demo_transport() -> httpx.MockTransport:
    """Serve demo/feeds/<name> for any https://demo-feeds.techspire.invalid/<name> URL."""
    feeds_dir = DEMO_DIR / "feeds"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "demo-feeds.techspire.invalid":
            return httpx.Response(404)
        path = feeds_dir / Path(request.url.path).name
        if not path.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "application/rss+xml"})

    return httpx.MockTransport(handler)


def _demo_provider() -> FixtureAIProvider:
    return FixtureAIProvider(json.loads((DEMO_DIR / "ai_responses.json").read_text(encoding="utf-8")))


# ------------------------------------------------------------------- doctor
def run_doctor(settings: Settings) -> int:
    rows: list[tuple[str, str, str]] = []

    def add(status: str, label: str, detail: str) -> None:
        rows.append((status, label, detail))

    py = sys.version_info
    add("OK" if py >= (3, 11) else "FAIL", "Python", f"{platform.python_version()} (3.11+ required)")
    add("OK" if settings.env_file else "WARN", ".env file",
        str(settings.env_file) if settings.env_file else "not found; safe defaults in use (copy .env.example to .env)")
    try:
        with Storage(settings.database_path) as storage:
            total = sum(storage.status_counts().values())
        add("OK", "Database", f"{settings.database_path} (opened; {total} articles)")
    except TechspireError as exc:
        add("FAIL", "Database", str(exc))
    for label, folder in (("Images folder", settings.images_dir), ("Previews folder", settings.previews_dir),
                          ("Logs folder", settings.log_dir)):
        add(*_check_writable(label, folder))
    try:
        feeds = load_feeds(settings.feeds_file)
        enabled = sum(f.enabled for f in feeds)
        add("OK" if enabled else "WARN", "Feeds", f"{enabled} enabled of {len(feeds)} in {settings.feeds_file}")
    except ConfigError as exc:
        add("FAIL", "Feeds", str(exc))
    key_ok = bool(settings.ai_api_key)
    add("OK" if key_ok else "WARN", f"AI provider ({settings.ai_provider})",
        f"model {settings.ai_model}, reasoning {settings.ai_reasoning_effort or 'provider default'}, "
        f"{settings.ai_key_name} {'is set' if key_ok else 'is MISSING (dry runs need it; --demo does not)'}")
    if settings.temperature_ignored:
        add("WARN", "AI temperature", "AI_TEMPERATURE is ignored: current Gemini models do not accept it")
    fonts = FontSet.from_settings(settings)
    fallback = any(path is None for path in astuple(fonts))
    add("WARN" if fallback else "OK", "Fonts", "; ".join(f"{k}={Path(v).name}" for k, v in fonts.describe().items()))
    if settings.brand_logo_path:
        add("OK" if settings.brand_logo_path.is_file() else "WARN", "Brand logo", str(settings.brand_logo_path))
    else:
        add("OK", "Brand logo", "not configured (text wordmark is used)")
    permissions = _check_folder_permissions()
    if permissions:
        add(*permissions)
    add("OK", "Graph API version", settings.fb_graph_api_version)
    add("OK", "Facebook credentials",
        f"FB_PAGE_ID {'set' if settings.fb_page_id else 'missing'}, FB_PAGE_ACCESS_TOKEN "
        f"{'set' if settings.fb_page_access_token else 'missing'} (only needed for future live publishing)")
    gates = closed_gates(settings, publish_flag=False)
    config_locked = settings.dry_run or not settings.publish_enabled
    add("LOCKED" if config_locked else "WARN", "Publishing safety lock",
        f"{_lock_flags(settings)} -> "
        + ("LIVE FACEBOOK PUBLISHING: DISABLED" if config_locked
           else "config allows publishing; only the --publish flag now stands in the way"))

    print(f"{RULE}\nTECHSPIRE DOCTOR v{__version__}  (read-only checks; nothing is posted)\n{RULE}")
    for status, label, detail in rows:
        print(f"[{status:<6}] {label:<24} {detail}")
    print(RULE)
    log.debug("Doctor finished; closed gates: %s", gates)
    return EXIT_ERROR if any(r[0] == "FAIL" for r in rows) else EXIT_OK


def _check_folder_permissions() -> tuple[str, str, str] | None:
    """Best effort: warn when other local accounts can change the project folder (they could
    read a future .env or alter the code the scheduled task runs)."""
    if os.name != "nt":
        env = PROJECT_ROOT / ".env"
        if env.is_file() and env.stat().st_mode & 0o077:
            return ("WARN", "File permissions", f"{env} is readable by other users; run: chmod 600 .env")
        return None
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    try:
        listing = subprocess.run(  # noqa: S603 - fixed program path and arguments, no shell
            [str(icacls), str(PROJECT_ROOT)], capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    broad = sorted({group for line in listing.splitlines()
                    for group in ("Authenticated Users", "Everyone", "BUILTIN\\Users")
                    if group in line and any(right in line for right in ("(F)", "(M)", "(W)"))})
    if broad:
        return ("WARN", "Folder permissions", f"{', '.join(broad)} can modify this folder; "
                "see 'Secure the project folder' in README.md before adding real keys")
    return ("OK", "Folder permissions", "no broad write access found")


def _check_writable(label: str, folder: Path) -> tuple[str, str, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".doctor-", delete=True):
            pass
        return ("OK", label, f"{folder} (writable)")
    except OSError as exc:
        return ("FAIL", label, f"{folder} is not writable: {exc}")


# ------------------------------------------------------------------- status
def show_status(settings: Settings) -> int:
    with Storage(settings.database_path) as storage:
        counts = storage.status_counts()
        runs = storage.recent_runs(5)
        unknown = storage.unknown_outcome_articles()
    print(f"{RULE}\nTECHSPIRE STATUS  ({settings.database_path})\n{RULE}")
    for status in ArticleStatus:
        if counts.get(status.value):
            print(f"  {status.value:<18} {counts[status.value]}")
    if not counts:
        print("  No articles yet. Run: python -m techspire.main --dry-run")
    if unknown:
        print(f"\n  ATTENTION: {len(unknown)} article(s) have an unknown Facebook outcome and need a manual check: "
              + ", ".join(f"#{a.id}" for a in unknown))
    print("\nRecent runs:")
    for run in runs:
        summary = json.loads(run["summary_json"]) if run["summary_json"] else {}
        print(f"  {run['started_at']}  {run['mode']:<8} {run['outcome'] or 'running/interrupted':<20} "
              f"ready={summary.get('ready_for_review', '-')} writes={summary.get('facebook_writes', '-')}")
    print(f"\nReview page: {settings.review_page}\n{RULE}")
    return EXIT_OK


# ---------------------------------------------------------- resolve unknown
def resolve_unknown(settings: Settings, article_id: int, post_id: str | None, not_posted: bool) -> int:
    """Record a human decision about an upload whose outcome was unknown. Posts nothing."""
    if (post_id is None) == (not not_posted):
        raise ConfigError("Use exactly one of --post-id POST_ID (it was posted) or --not-posted.")
    with Storage(settings.database_path) as storage:
        stored = storage.resolve_unknown_publish(article_id, post_id=post_id)
    if post_id:
        print(f"Article #{article_id} recorded as posted ({post_id}); state {stored.status.value}. "
              "The next --publish run adds only the source comment.")
    else:
        print(f"Article #{article_id} recorded as not posted; it can be published again with --publish.")
    return EXIT_OK


# -------------------------------------------------------------- check feeds
def check_feeds(settings: Settings, names: list[str] | None) -> int:
    feeds = [f for f in _feeds(settings, names) if f.enabled]
    cutoff = utcnow() - timedelta(hours=settings.max_article_age_hours)
    print(f"{RULE}\nTECHSPIRE FEED CHECK  (read-only: no AI, no database, nothing posted)\n{RULE}")
    failures = 0
    with _feed_client(settings) as http:
        results = RSSFetcher(http).fetch_all(feeds)
        for feed, result in zip(feeds, results, strict=True):
            if not result.ok:
                failures += 1
                print(f"[FAIL] {feed.name:<24} {result.error}")
                continue
            dated = [a.published_at for a in result.articles if a.published_at]
            fresh = sum(1 for a in result.articles if is_fresh(a.published_at, cutoff))
            newest = format_utc(max(dated)) if dated else "no dates"
            print(f"[OK]   {feed.name:<24} {len(result.articles):>3} items, {fresh:>3} within "
                  f"{settings.max_article_age_hours:g}h, newest {newest}")
    print(RULE)
    return EXIT_ERROR if failures == len(feeds) and feeds else EXIT_OK


def _configure_stdio() -> None:
    """Never crash on a headline character the console cannot display (e.g. Task Scheduler logs)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
