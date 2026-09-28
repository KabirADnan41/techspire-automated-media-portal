"""End-to-end tests: mocked RSS / AI / Facebook, real SQLite and Pillow."""

import json
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from helpers import NOW, Clock, ScriptedProvider, feed_transport, rejected_answer, rss
from PIL import Image

from techspire import main as cli
from techspire.config import Settings
from techspire.content_filter import AIContentService, extract_article_data
from techspire.enums import ArticleStatus as S
from techspire.enums import ErrorStage
from techspire.exceptions import AIProviderError, AlreadyRunningError, ImageGenerationError
from techspire.fb_publisher import FacebookPublisher, authorize_publishing
from techspire.image_generator import CardRenderer
from techspire.pipeline import Pipeline, RunOptions
from techspire.rss_fetcher import FeedConfig, RSSFetcher, build_http_client
from techspire.storage import Storage

FEED = FeedConfig("Example Feed", "https://news.example.com/feed.xml")
SECOND = FeedConfig("Second Feed", "https://second.example.com/rss")
RENDERER = CardRenderer()


def item(slug: str, title: str, hours_ago: float = 1, summary: str = "Example Corp released a new AI model.") -> dict:
    return {"title": title, "link": f"https://news.example.com/{slug}", "description": summary,
            "pubDate": format_datetime(NOW - timedelta(hours=hours_ago), usegmt=True)}


def relevant(prompt: str) -> dict:
    """A valid 'relevant' answer that echoes the prompt's original_url, as a correct model would."""
    return {**_base_relevant(), "original_url": extract_article_data(prompt)["original_url"]}


def _base_relevant() -> dict:
    return {"is_relevant": True, "category": "TECHNOLOGY",
            "catchy_headline": "Example Corp Launches a Powerful New AI Model for Developers",
            "facebook_caption": "Example Corp has released a new AI model for developers. The release gives teams "
                                "another option for building AI features. #AI #Developers #TechspireOfficial",
            "rejection_reason": None}


class Harness:
    def __init__(self, tmp_path: Path, settings: Settings | None = None) -> None:
        self.clock = Clock()
        self.settings = settings or Settings(
            database_path=tmp_path / "data" / "t.db", output_dir=tmp_path / "output", log_dir=tmp_path / "logs")
        self.storage = Storage(self.settings.database_path, clock=self.clock)
        self.reports: list[str] = []
        self.runs = 0

    def run(self, provider, transport, *, feeds=(FEED,), mode="dry-run", publisher=None, limit=10,
            renderer=RENDERER, max_age=48.0, publish_ids=None):
        self.runs += 1
        fetcher = RSSFetcher(build_http_client(5, "UA", transport), clock=self.clock, retry_wait_seconds=0)
        pipeline = Pipeline(self.settings, self.storage, fetcher, list(feeds),
                            AIContentService(provider, sleep=lambda s: None), renderer,
                            RunOptions(mode=mode, limit=limit, max_age_hours=max_age, publish_ids=publish_ids),
                            publisher=publisher, run_id=f"run-{self.runs}", report=self.reports.append)
        return pipeline.run()

    def only(self):
        articles = self.storage.list_by_status(*S)
        assert len(articles) == 1
        return articles[0]


@pytest.fixture
def h(tmp_path) -> Harness:
    harness = Harness(tmp_path)
    yield harness
    harness.storage.close()


# ------------------------------------------------------------------ dry run
def test_relevant_article_reaches_review_without_any_facebook_request(h):
    transport = feed_transport({FEED.url: rss([item("ai-1", "Example Corp releases new AI model")])})
    summary = h.run(ScriptedProvider(relevant), transport)

    stored = h.only()
    assert stored.status is S.READY_FOR_REVIEW
    assert [e["to_status"] for e in h.storage.events(stored.id)] == [
        "DISCOVERED", "ANALYZING", "APPROVED", "IMAGE_CREATED", "READY_FOR_REVIEW"]
    assert summary.ready_for_review == 1 and summary.facebook_writes == 0
    assert transport.calls == [FEED.url]  # the only network request was the feed

    with Image.open(stored.image_path) as image:
        assert image.size == (1200, 630)
    preview = json.loads(stored.preview_path.read_text(encoding="utf-8"))
    assert preview["state"] == "READY_FOR_REVIEW"
    assert preview["facebook_write_performed"] is False
    assert preview["original_url"] == "https://news.example.com/ai-1"
    assert "Facebook write performed:\nNO" in h.reports[0]
    assert (h.settings.previews_dir / "index.html").is_file()


def test_rejected_article_is_recorded_with_reason(h):
    transport = feed_transport({FEED.url: rss([item("pol", "Senator gives speech about budget")])})
    summary = h.run(ScriptedProvider(rejected_answer("Politics, not technology.")), transport)
    stored = h.only()
    assert stored.status is S.REJECTED and stored.rejection_reason == "Politics, not technology."
    assert summary.rejected == 1 and summary.ready_for_review == 0
    assert stored.image_path is None


def test_review_page_escapes_untrusted_content(h):
    evil = "Evil <script>alert(1)</script> story about new AI tools"
    transport = feed_transport({FEED.url: rss([item("xss", evil.replace("<", "&lt;").replace(">", "&gt;"))])})
    h.run(ScriptedProvider(rejected_answer("Reason <img src=x onerror=alert(1)>")), transport)
    page = (h.settings.previews_dir / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page  # the cleaner already dropped it from the title
    assert "<img src=x" not in page  # AI text is not cleaned, so the page must escape it
    assert "Reason &lt;img src=x onerror=alert(1)&gt;" in page


def test_second_run_skips_duplicates_without_ai_calls(h):
    transport = feed_transport({FEED.url: rss([item("ai-1", "Example Corp releases new AI model")])})
    provider = ScriptedProvider(relevant)
    h.run(provider, transport)
    summary = h.run(provider, transport)
    assert summary.duplicates == 1 and summary.analyzed == 0
    assert len(provider.prompts) == 1


def test_same_story_from_another_feed_is_skipped(h):
    title = "Quantacore raises forty million dollars for photonic chips"
    transport = feed_transport({
        FEED.url: rss([item("q1", title, hours_ago=1)]),
        SECOND.url: rss([{"title": title, "link": "https://second.example.com/q", "description": "copy",
                          "pubDate": format_datetime(NOW - timedelta(hours=2), usegmt=True)}]),
    })
    summary = h.run(ScriptedProvider(relevant), transport, feeds=(FEED, SECOND))
    assert summary.same_story == 1 and summary.analyzed == 1


def test_ai_outage_stops_ai_and_leaves_work_for_next_run(h):
    transport = feed_transport({FEED.url: rss([item("a", "First AI story here", 1), item("b", "Second AI story", 2)])})
    outage = [AIProviderError("503 from provider", transient=True, status_code=503)] * 3
    summary = h.run(ScriptedProvider(*outage), transport)
    assert summary.analyzed == 0 and summary.failed == 0
    [stored] = h.storage.list_by_status(*S)  # the second article was not even registered
    assert stored.status is S.DISCOVERED and stored.retry_count == 0

    summary = h.run(ScriptedProvider(relevant, relevant), transport)
    assert summary.ready_for_review == 2
    assert {a.status for a in h.storage.list_by_status(*S)} == {S.READY_FOR_REVIEW}


def test_invalid_ai_answers_are_retried_on_later_runs_up_to_the_limit(h):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    bad = {**_base_relevant(), "category": "NOT A CATEGORY", "original_url": "https://news.example.com/a"}
    for expected_retries in (1, 2, 3):
        h.run(ScriptedProvider(bad, bad), transport)
        stored = h.only()
        assert stored.status is S.FAILED and stored.error_stage == ErrorStage.ANALYZE
        assert stored.retry_count == expected_retries
    provider = ScriptedProvider()
    h.run(provider, transport)  # limit reached: no more AI calls
    assert provider.prompts == []


def test_image_failure_resumes_without_calling_ai_again(h):
    class BrokenRenderer(CardRenderer):
        def render_to_file(self, *args, **kwargs):
            raise ImageGenerationError("disk full")

    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    h.run(ScriptedProvider(relevant), transport, renderer=BrokenRenderer())
    stored = h.only()
    assert stored.status is S.FAILED and stored.error_stage == ErrorStage.IMAGE

    provider = ScriptedProvider()
    h.run(provider, transport)
    assert h.only().status is S.READY_FOR_REVIEW
    assert provider.prompts == []


def test_unexpected_errors_are_charged_to_the_article_not_left_mid_step(h):
    class BuggyRenderer(CardRenderer):
        def render_to_file(self, *args, **kwargs):
            raise RuntimeError("unexpected bug")

    transport = feed_transport({FEED.url: rss([item("a", "First AI story", 1), item("b", "Second AI story", 2)])})
    summary = h.run(ScriptedProvider(KeyError("bug in provider"), relevant), transport, renderer=BuggyRenderer())
    first, second = sorted(h.storage.list_by_status(*S), key=lambda a: a.id)
    assert (first.status, first.error_stage, first.retry_count) == (S.FAILED, ErrorStage.ANALYZE, 1)
    assert (second.status, second.error_stage) == (S.FAILED, ErrorStage.IMAGE)
    assert summary.failed == 2  # the batch carried on after the first failure


def test_repeated_image_failures_use_up_the_retry_budget(h):
    class BrokenRenderer(CardRenderer):
        def render_to_file(self, *args, **kwargs):
            raise ImageGenerationError("font file is corrupt")

    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    h.run(ScriptedProvider(relevant), transport, renderer=BrokenRenderer())
    for expected in (2, 3):
        summary = h.run(ScriptedProvider(), transport, renderer=BrokenRenderer())
        stored = h.only()
        assert (stored.status, stored.error_stage, stored.retry_count) == (S.FAILED, ErrorStage.IMAGE, expected)
        assert summary.resumed == 1
    summary = h.run(ScriptedProvider(), transport, renderer=BrokenRenderer())
    assert summary.resumed == 0  # budget used up: no more attempts


def test_recovered_and_resumed_are_counted_separately(h):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    h.run(ScriptedProvider(AIProviderError("down", transient=False, status_code=401)), transport)
    h.storage.transition(h.only().id, S.ANALYZING)
    summary = h.run(ScriptedProvider(relevant), transport)
    assert (summary.recovered, summary.resumed) == (1, 1)


def test_crash_during_analysis_is_recovered(h):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    h.run(ScriptedProvider(AIProviderError("down", transient=False, status_code=401)), transport)
    stored = h.only()
    h.storage.transition(stored.id, S.ANALYZING)  # simulate a crash mid-analysis
    summary = h.run(ScriptedProvider(relevant), transport)
    assert summary.recovered >= 1
    assert h.only().status is S.READY_FOR_REVIEW


def test_old_and_undated_articles_are_skipped(h):
    undated = {"title": "Undated AI story", "link": "https://news.example.com/undated", "description": "x"}
    transport = feed_transport({FEED.url: rss([item("old", "Old AI story from days ago", hours_ago=72), undated])})
    provider = ScriptedProvider()
    summary = h.run(provider, transport)
    assert (summary.too_old, summary.undated, summary.analyzed) == (1, 1, 0)
    assert provider.prompts == []


def test_article_limit_is_respected(h):
    items = [item(f"s{i}", f"Example AI story number {i}", hours_ago=i + 1) for i in range(5)]
    provider = ScriptedProvider(*[relevant] * 2)
    summary = h.run(provider, feed_transport({FEED.url: rss(items)}), limit=2)
    assert summary.analyzed == 2
    assert len(h.storage.list_by_status(*S)) == 2
    # newest first
    assert {a.original_url for a in h.storage.list_by_status(*S)} == {
        "https://news.example.com/s0", "https://news.example.com/s1"}


def test_failing_feed_does_not_stop_the_batch(h):
    transport = feed_transport({FEED.url: 500, SECOND.url: rss([item("x", "Second feed AI story")])})
    summary = h.run(ScriptedProvider(relevant), transport, feeds=(FEED, SECOND))
    assert summary.feeds_failed == 1 and summary.feeds_ok == 1 and summary.ready_for_review == 1


def test_publisher_is_refused_outside_live_mode(h):
    with pytest.raises(ValueError):
        Pipeline(h.settings, h.storage, None, [], None, RENDERER, RunOptions(mode="dry-run"),
                 publisher=object(), run_id="x")


def test_parallel_run_is_refused(h):
    h.storage.acquire_run_lock("someone-else")
    with pytest.raises(AlreadyRunningError):
        h.run(ScriptedProvider(), feed_transport({}))


# --------------------------------------------------- future live mode (mocked)
OPEN_FB = {"dry_run": False, "publish_enabled": True, "fb_page_id": "123456789",
           "fb_page_access_token": "EAATESTTOKEN0000000000000000000000"}


class FakeGraph:
    """In-memory stand-in for graph.facebook.com that records every write."""

    def __init__(self, photo_responses, comment_responses, existing_comments=()):
        self.photo_responses = list(photo_responses)
        self.comment_responses = list(comment_responses)
        self.existing_comments = list(existing_comments)
        self.photo_calls = 0
        self.comment_calls = 0
        self.comment_reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/comments"):
            self.comment_reads += 1
            return httpx.Response(200, json={"data": self.existing_comments})
        assert request.method == "POST"
        if request.url.path.endswith("/photos"):
            self.photo_calls += 1
            response = self.photo_responses.pop(0)
        elif request.url.path.endswith("/comments"):
            self.comment_calls += 1
            response = self.comment_responses.pop(0)
        else:
            raise AssertionError(f"unexpected request {request.url}")
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def live(tmp_path):
    harness = Harness(tmp_path, Settings(database_path=tmp_path / "data" / "live.db", output_dir=tmp_path / "out",
                                         log_dir=tmp_path / "logs", **OPEN_FB))
    yield harness
    harness.storage.close()


def publisher_for(settings: Settings, graph: FakeGraph) -> FacebookPublisher:
    return FacebookPublisher(settings, authorize_publishing(settings, publish_flag=True), publish_flag=True,
                             client=httpx.Client(transport=httpx.MockTransport(graph)))


def test_post_succeeds_comment_fails_then_only_the_comment_is_retried(live):
    graph = FakeGraph(
        photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
        comment_responses=[httpx.Response(500, json={"error": {"message": "temporary", "code": 2}}),
                           httpx.Response(200, json={"id": "222_999"})],
    )
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    publisher = publisher_for(live.settings, graph)
    summary = live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher)
    stored = live.only()
    assert stored.status is S.COMMENT_PENDING
    assert (stored.facebook_photo_id, stored.facebook_post_id) == ("111", "123456789_222")  # persisted immediately
    assert summary.facebook_writes == 1

    # next simulated run: the existing post must NOT be recreated
    summary = live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.PUBLISHED and stored.facebook_comment_id == "222_999"
    assert graph.photo_calls == 1 and graph.comment_calls == 2
    assert graph.comment_reads == 1  # the 500 was ambiguous, so the run checked before posting again
    assert summary.facebook_writes == 1


def test_definitive_comment_refusal_is_retried_directly(live):
    graph = FakeGraph(
        photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
        comment_responses=[httpx.Response(400, json={"error": {"message": "limit", "code": 4}}),
                           httpx.Response(200, json={"id": "222_999"})],
    )
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert live.only().error_stage == ErrorStage.PUBLISH_COMMENT
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert live.only().status is S.PUBLISHED
    assert (graph.photo_calls, graph.comment_calls, graph.comment_reads) == (1, 2, 0)


def test_unknown_photo_outcome_is_never_retried_automatically(live):
    graph = FakeGraph(photo_responses=[httpx.ReadTimeout("slow")], comment_responses=[])
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.FAILED and stored.error_stage == ErrorStage.PUBLISH_UNKNOWN
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert graph.photo_calls == 1


def test_crash_during_upload_is_recovered_as_unknown_not_reposted(live):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport)  # dry run: READY_FOR_REVIEW
    stored = live.only()
    live.storage.transition(stored.id, S.PUBLISHING)  # crash right after marking PUBLISHING
    graph = FakeGraph(photo_responses=[], comment_responses=[])
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert live.only().error_stage == ErrorStage.PUBLISH_UNKNOWN
    assert graph.photo_calls == 0


def test_rate_limited_upload_is_retried_on_the_next_run(live):
    graph = FakeGraph(
        photo_responses=[httpx.Response(400, json={"error": {"message": "limit", "code": 80001}}),
                         httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
        comment_responses=[httpx.Response(200, json={"id": "c1"})],
    )
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert live.only().error_stage == ErrorStage.PUBLISH_PHOTO
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert live.only().status is S.PUBLISHED
    assert graph.photo_calls == 2


def test_comment_that_actually_succeeded_is_found_not_posted_twice(live):
    existing = [{"id": "222_555", "message": "Read full story: https://news.example.com/a",
                 "from": {"id": "123456789", "name": "Techspire"}}]
    graph = FakeGraph(photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
                      comment_responses=[httpx.ReadTimeout("slow")], existing_comments=existing)
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.COMMENT_PENDING and stored.error_stage == ErrorStage.COMMENT_UNKNOWN
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.PUBLISHED and stored.facebook_comment_id == "222_555"
    assert (graph.photo_calls, graph.comment_calls, graph.comment_reads) == (1, 1, 1)  # no duplicates


def test_crash_during_the_comment_request_is_reconciled_not_duplicated(live):
    existing = [{"id": "222_777", "message": "Read full story: https://news.example.com/a",
                 "from": {"id": "123456789"}}]

    class Crash(Exception):
        pass

    graph = FakeGraph(photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
                      comment_responses=[Crash("process killed mid-request")], existing_comments=existing)
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    with pytest.raises(Crash):
        live.run(ScriptedProvider(relevant), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.COMMENT_PENDING and stored.error_stage is ErrorStage.COMMENT_UNKNOWN
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    stored = live.only()
    assert stored.status is S.PUBLISHED and stored.facebook_comment_id == "222_777"
    assert (graph.photo_calls, graph.comment_calls, graph.comment_reads) == (1, 1, 1)


def test_review_page_banner_reports_real_posts(live):
    graph = FakeGraph(photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
                      comment_responses=[httpx.Response(200, json={"id": "c1"})])
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport)
    page = live.settings.review_page.read_text(encoding="utf-8")
    assert "NOTHING HAS BEEN POSTED TO FACEBOOK" in page
    live.run(ScriptedProvider(), transport, mode="live", publisher=publisher_for(live.settings, graph))
    assert "1 ARTICLE(S) HAVE BEEN POSTED TO FACEBOOK" in live.settings.review_page.read_text(encoding="utf-8")


def test_lost_run_lock_stops_before_any_facebook_write(live):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport)  # dry run: READY_FOR_REVIEW
    graph = FakeGraph(photo_responses=[], comment_responses=[])
    publisher = publisher_for(live.settings, graph)
    original = live.storage.refresh_run_lock

    def stolen(run_id, ttl=timedelta(minutes=30)):
        live.storage._conn.execute("UPDATE run_lock SET run_id = 'intruder'")
        return original(run_id, ttl)

    live.storage.refresh_run_lock = stolen
    with pytest.raises(AlreadyRunningError):
        live.run(ScriptedProvider(), feed_transport({FEED.url: rss([])}), mode="live", publisher=publisher)
    assert graph.photo_calls == 0


def test_explicit_ids_publish_an_older_reviewed_article(live):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport)
    article_id = live.only().id
    live.clock.advance(hours=72)
    graph = FakeGraph(photo_responses=[httpx.Response(200, json={"id": "111", "post_id": "123456789_222"})],
                      comment_responses=[httpx.Response(200, json={"id": "c1"})])
    live.run(ScriptedProvider(), feed_transport({FEED.url: rss([])}), mode="live",
             publisher=publisher_for(live.settings, graph), publish_ids=frozenset({article_id}))
    assert live.only().status is S.PUBLISHED and graph.photo_calls == 1


def test_stale_reviewed_articles_are_not_published(live):
    transport = feed_transport({FEED.url: rss([item("a", "Example AI story")])})
    live.run(ScriptedProvider(relevant), transport)  # dry run creates READY_FOR_REVIEW
    live.clock.advance(hours=72)
    graph = FakeGraph(photo_responses=[], comment_responses=[])
    live.run(ScriptedProvider(), feed_transport({FEED.url: rss([])}), mode="live",
             publisher=publisher_for(live.settings, graph))
    assert graph.photo_calls == 0 and live.only().status is S.READY_FOR_REVIEW


# -------------------------------------------------------------------- CLI
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    env = tmp_path / "test.env"
    env.write_text("", encoding="utf-8")
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text("feeds:\n  - name: Example Feed\n    url: https://news.example.com/feed.xml\n", encoding="utf-8")
    for key in list(cli.Settings.__dataclass_fields__):
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "cli.db"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FEEDS_FILE", str(feeds))
    return env


def test_cli_publish_is_blocked_by_default(cli_env, tmp_path, capsys):
    assert cli.main(["--publish", "--env-file", str(cli_env)]) == cli.EXIT_PUBLISH_BLOCKED
    err = capsys.readouterr().err
    assert "PUBLISHING BLOCKED" in err and "DRY_RUN is true" in err and "PUBLISH_ENABLED is false" in err
    assert not (tmp_path / "data" / "cli.db").exists()  # nothing was even started


def test_cli_live_publishing_requires_reviewed_ids_by_default(cli_env, monkeypatch, capsys):
    for key, value in {"DRY_RUN": "false", "PUBLISH_ENABLED": "true", "FB_PAGE_ID": "123456789",
                       "FB_PAGE_ACCESS_TOKEN": "EAATESTTOKEN0000000000000000000000",
                       "GEMINI_API_KEY": "AIzaTESTKEY-not-real-000000000000000"}.items():
        monkeypatch.setenv(key, value)
    assert cli.main(["--publish", "--env-file", str(cli_env)]) == cli.EXIT_PUBLISH_BLOCKED
    err = capsys.readouterr().err
    assert "--publish --ids" in err and "EAATEST" not in err


def test_cli_dry_run_without_ai_key_explains_what_to_do(cli_env, capsys):
    assert cli.main(["--dry-run", "--env-file", str(cli_env)]) == cli.EXIT_ERROR
    assert "GEMINI_API_KEY is missing" in capsys.readouterr().err


def test_cli_resolve_unknown_requires_exactly_one_decision(cli_env, capsys):
    assert cli.main(["--resolve-unknown", "1", "--env-file", str(cli_env)]) == cli.EXIT_ERROR
    assert cli.main(["--resolve-unknown", "1", "--post-id", "1_2", "--not-posted",
                     "--env-file", str(cli_env)]) == cli.EXIT_ERROR
    assert "exactly one of" in capsys.readouterr().err


def test_cli_doctor_reports_the_lock(cli_env, capsys):
    assert cli.main(["--doctor", "--env-file", str(cli_env)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "LIVE FACEBOOK PUBLISHING: DISABLED" in out
    assert "[FAIL" not in out


def test_cli_demo_runs_end_to_end_offline(cli_env, tmp_path, monkeypatch, capsys):
    demo_settings = cli._demo_settings

    def redirected(settings):
        return replace(demo_settings(settings), database_path=tmp_path / "demo" / "demo.db",
                       output_dir=tmp_path / "demo-out", log_dir=tmp_path / "logs")

    monkeypatch.setattr(cli, "_demo_settings", redirected)
    assert cli.main(["--demo", "--env-file", str(cli_env)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "LIVE FACEBOOK PUBLISHING: DISABLED" in out and "Facebook writes performed: 0" in out
    images = sorted((tmp_path / "demo-out" / "images").glob("*.jpg"))
    assert len(images) == 7
    with Storage(tmp_path / "demo" / "demo.db") as store:
        counts = store.status_counts()
    assert counts == {"READY_FOR_REVIEW": 7, "REJECTED": 5, "FAILED": 1}
    with Storage(tmp_path / "demo" / "demo.db") as store:
        sectors = {a.category.value for a in store.list_by_status(S.READY_FOR_REVIEW)}
    assert sectors == {"TECHNOLOGY", "BUSINESS", "FINANCE", "CAREER DEVELOPMENT", "ENTREPRENEURSHIP"}
    assert cli.main(["--status", "--env-file", str(cli_env)]) == cli.EXIT_OK


def test_round_robin_keeps_one_busy_feed_from_taking_every_slot(h):
    busy = [item(f"busy{i}", f"Busy feed technology story number {i}", hours_ago=0.1 * (i + 1)) for i in range(6)]
    quiet = [{"title": "Quiet feed careers story about skills", "link": "https://second.example.com/careers",
              "description": "Employers want data skills.",
              "pubDate": format_datetime(NOW - timedelta(hours=5), usegmt=True)}]
    transport = feed_transport({FEED.url: rss(busy), SECOND.url: rss(quiet)})
    h.run(ScriptedProvider(*[relevant] * 3), transport, feeds=(FEED, SECOND), limit=3)
    urls = {a.original_url for a in h.storage.list_by_status(*S)}
    assert "https://second.example.com/careers" in urls  # older, but its feed gets a turn
    assert len(urls) == 3
