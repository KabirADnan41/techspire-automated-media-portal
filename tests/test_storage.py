import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from helpers import Clock, make_article

from techspire.enums import ArticleStatus as S
from techspire.enums import Category, ErrorStage
from techspire.exceptions import AlreadyRunningError, InvalidStateTransition, StorageError
from techspire.storage import Storage
from techspire.text_cleaner import title_fingerprint


def _advance_to_ready(storage: Storage, article_id: int) -> None:
    storage.transition(article_id, S.ANALYZING)
    storage.transition(article_id, S.APPROVED, category=Category.TECHNOLOGY, catchy_headline="Headline here now",
                       facebook_caption="Caption text #A #B")
    storage.transition(article_id, S.IMAGE_CREATED, image_path=Path("output/images/x.jpg"))
    storage.transition(article_id, S.READY_FOR_REVIEW)


def test_insert_and_read_back(storage):
    article = make_article()
    reg = storage.register(article, "run-1")
    assert reg.is_new and reg.status is S.DISCOVERED
    stored = storage.get(reg.article_id)
    assert stored.original_url == article.original_url
    assert stored.canonical_url == article.canonical_url
    assert stored.published_at == article.published_at
    assert stored.published_at.tzinfo is not None
    assert storage.events(reg.article_id)[0]["to_status"] == "DISCOVERED"


def test_duplicate_registration_is_refused(storage):
    first = storage.register(make_article("https://news.example.com/a?utm_source=x"))
    second = storage.register(make_article("https://news.example.com/a?fbclid=y"))
    assert first.is_new and not second.is_new
    assert second.article_id == first.article_id
    assert sum(storage.status_counts().values()) == 1


def test_uniqueness_is_enforced_by_the_database(storage):
    article = make_article()
    storage.register(article)
    with pytest.raises(sqlite3.IntegrityError):
        storage._conn.execute(
            "INSERT INTO articles (url_hash, original_url, canonical_url, title, source_name, discovered_at, status, "
            "created_at, updated_at) VALUES (?, 'u', 'c2', 't', 's', 'd', 'DISCOVERED', 'c', 'u')",
            (article.url_hash,),
        )


def test_duplicate_survives_restart(tmp_path, clock):
    path = tmp_path / "restart.db"
    with Storage(path, clock=clock) as store:
        store.register(make_article())
    with Storage(path, clock=clock) as store:
        assert not store.register(make_article()).is_new


def test_valid_transitions_are_recorded(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    assert storage.get(reg.article_id).status is S.READY_FOR_REVIEW
    path = [e["to_status"] for e in storage.events(reg.article_id)]
    assert path == ["DISCOVERED", "ANALYZING", "APPROVED", "IMAGE_CREATED", "READY_FOR_REVIEW"]


@pytest.mark.parametrize("target", [S.PUBLISHED, S.READY_FOR_REVIEW, S.POST_CREATED, S.APPROVED])
def test_invalid_transitions_are_refused(storage, target):
    reg = storage.register(make_article())
    with pytest.raises(InvalidStateTransition):
        storage.transition(reg.article_id, target)
    assert storage.get(reg.article_id).status is S.DISCOVERED


def test_failure_is_persisted_with_retry_counter_and_redaction(storage):
    reg = storage.register(make_article())
    storage.transition(reg.article_id, S.ANALYZING)
    failed = storage.mark_failed(reg.article_id, ErrorStage.ANALYZE, "boom access_token=EAAsecret123456789012345")
    assert failed.status is S.FAILED
    assert failed.error_stage == "analyze"
    assert failed.retry_count == 1
    assert "EAAsecret" not in failed.last_error
    assert failed.last_attempt_at is not None
    storage.transition(reg.article_id, S.ANALYZING)
    assert storage.mark_failed(reg.article_id, ErrorStage.ANALYZE, "again").retry_count == 2


def test_resumable_articles_respect_retry_limit(storage):
    ids = []
    for i in range(3):
        reg = storage.register(make_article(f"https://news.example.com/r{i}"))
        ids.append(reg.article_id)
    storage.transition(ids[1], S.ANALYZING)
    storage.mark_failed(ids[1], ErrorStage.ANALYZE, "x")
    storage.transition(ids[2], S.ANALYZING)
    for _ in range(3):
        storage.mark_failed(ids[2], ErrorStage.ANALYZE, "x")
        if storage.get(ids[2]).retry_count < 3:
            storage.transition(ids[2], S.ANALYZING)
    resumable = {a.id for a in storage.resumable_articles(max_retries=3)}
    assert resumable == {ids[0], ids[1]}


def test_post_id_persisted_and_comment_pending_recovery(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    stored = storage.record_post_created(reg.article_id, "111", "222_333")
    assert (stored.status, stored.facebook_photo_id, stored.facebook_post_id) == (S.POST_CREATED, "111", "222_333")

    pending = storage.record_comment_failure(reg.article_id, "comment endpoint down")
    assert pending.status is S.COMMENT_PENDING and pending.retry_count == 1
    again = storage.record_comment_failure(reg.article_id, "still down")
    assert again.status is S.COMMENT_PENDING and again.retry_count == 2
    assert [a.id for a in storage.comment_pending_articles(max_retries=3)] == [reg.article_id]

    done = storage.record_comment_created(reg.article_id, "999")
    assert done.status is S.PUBLISHED and done.facebook_comment_id == "999"
    assert storage.comment_pending_articles(max_retries=3) == []


def test_article_with_a_post_can_never_enter_publishing_again(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.record_post_created(reg.article_id, "111", "222_333")
    # Force it into FAILED(publish_photo) behind the state machine's back, as a bug might.
    storage._conn.execute("UPDATE articles SET status='FAILED', error_stage='publish_photo' WHERE id=?",
                          (reg.article_id,))
    assert storage.publishable_articles(max_retries=3) == []
    with pytest.raises(InvalidStateTransition, match="already has a Facebook post"):
        storage.transition(reg.article_id, S.PUBLISHING)


def test_published_requires_a_comment_id(storage):
    reg = storage.register(make_article())
    with pytest.raises(sqlite3.IntegrityError):
        storage._conn.execute("UPDATE articles SET status='PUBLISHED', facebook_post_id='1' WHERE id=?",
                              (reg.article_id,))


def test_failed_write_rolls_back_completely(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    events_before = len(storage.events(reg.article_id))
    # POST_CREATED without any Facebook id violates a CHECK constraint mid-transaction.
    with pytest.raises(InvalidStateTransition):
        storage.transition(reg.article_id, S.POST_CREATED, last_error="partial")
    stored = storage.get(reg.article_id)
    assert stored.status is S.PUBLISHING and stored.last_error is None
    assert len(storage.events(reg.article_id)) == events_before


def test_recover_interrupted_run(storage):
    analyzing = storage.register(make_article("https://news.example.com/1")).article_id
    storage.transition(analyzing, S.ANALYZING)
    publishing = storage.register(make_article("https://news.example.com/2")).article_id
    _advance_to_ready(storage, publishing)
    storage.transition(publishing, S.PUBLISHING)

    assert storage.recover_interrupted("run-2") == 2
    assert storage.get(analyzing).status is S.DISCOVERED
    unknown = storage.get(publishing)
    assert unknown.status is S.FAILED and unknown.error_stage == ErrorStage.PUBLISH_UNKNOWN
    assert storage.publishable_articles(max_retries=3) == []  # never retried automatically


def test_same_story_is_detected_conservatively(storage):
    title = "Quantacore raises forty million dollars for photonic chips"
    first = storage.register(make_article("https://a.example.com/1", title=title, fingerprint=title_fingerprint(title)))
    twin = storage.register(make_article("https://b.example.com/9", title=title, fingerprint=title_fingerprint(title)))
    other = storage.register(make_article("https://c.example.com/2", title="Different story entirely here today",
                                          fingerprint=title_fingerprint("Different story entirely here today")))
    assert first.status is S.DISCOVERED
    assert twin.status is S.REJECTED and twin.duplicate_of == first.article_id
    assert "Duplicate story" in storage.get(twin.article_id).rejection_reason
    assert other.status is S.DISCOVERED


def test_same_story_window_expires(tmp_path):
    clock = Clock()
    title = "Quantacore raises forty million dollars for photonic chips"
    with Storage(tmp_path / "w.db", clock=clock) as store:
        store.register(make_article("https://a.example.com/1", title=title, fingerprint=title_fingerprint(title)))
        clock.advance(days=8)
        fingerprint = title_fingerprint(title)
        later = store.register(make_article("https://b.example.com/1", title=title, fingerprint=fingerprint))
        assert later.status is S.DISCOVERED


def test_run_lock(tmp_path):
    clock = Clock()
    with Storage(tmp_path / "lock.db", clock=clock) as store:
        store.acquire_run_lock("run-a")
        with pytest.raises(AlreadyRunningError):
            store.acquire_run_lock("run-b")
        store.release_run_lock("run-a")
        store.acquire_run_lock("run-b")
        clock.advance(minutes=31)  # run-b crashed without releasing
        store.acquire_run_lock("run-c")


def test_refreshing_a_lost_lock_fails_loudly(tmp_path):
    clock = Clock()
    with Storage(tmp_path / "lost.db", clock=clock) as store:
        store.acquire_run_lock("run-a")
        clock.advance(minutes=31)
        store.acquire_run_lock("run-b")  # run-a looked dead and was taken over
        with pytest.raises(AlreadyRunningError, match="lost its run lock"):
            store.refresh_run_lock("run-a")


def test_known_url_hashes_batches(storage):
    articles = [make_article(f"https://news.example.com/k{i}") for i in range(3)]
    for article in articles[:2]:
        storage.register(article)
    hashes = [a.url_hash for a in articles] + ["f" * 64] * 600  # crosses the 500-per-query chunking
    assert storage.known_url_hashes(hashes) == {articles[0].url_hash, articles[1].url_hash}


def test_saved_facebook_ids_block_any_second_upload(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.recover_interrupted("run-x")  # -> FAILED(publish_unknown), e.g. another run took over
    storage.save_facebook_ids(reg.article_id, "111", "222_333")
    stored = storage.get(reg.article_id)
    assert (stored.facebook_photo_id, stored.facebook_post_id) == ("111", "222_333")
    assert storage.publishable_articles(max_retries=3) == []
    with pytest.raises(InvalidStateTransition):
        storage.transition(reg.article_id, S.PUBLISHING)


def test_unknown_comment_outcome_stays_pending_for_reconciliation(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.record_post_created(reg.article_id, "111", "222_333")
    storage.record_comment_failure(reg.article_id, "timed out", stage=ErrorStage.COMMENT_UNKNOWN)
    [pending] = storage.comment_pending_articles(max_retries=3)
    assert pending.error_stage == ErrorStage.COMMENT_UNKNOWN and pending.status is S.COMMENT_PENDING


def _unknown_outcome(storage: Storage) -> int:
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.recover_interrupted("run-x")
    return reg.article_id


def test_operator_confirms_the_post_exists(storage):
    article_id = _unknown_outcome(storage)
    stored = storage.resolve_unknown_publish(article_id, post_id="123456789_222")
    assert stored.status is S.POST_CREATED and stored.facebook_post_id == "123456789_222"
    assert [a.id for a in storage.comment_pending_articles(max_retries=3)] == [article_id]
    assert storage.publishable_articles(max_retries=3) == []


def test_operator_confirms_nothing_was_posted(storage):
    article_id = _unknown_outcome(storage)
    stored = storage.resolve_unknown_publish(article_id, post_id=None)
    assert (stored.status, stored.error_stage, stored.retry_count) == (S.FAILED, ErrorStage.PUBLISH_PHOTO, 0)
    assert [a.id for a in storage.publishable_articles(max_retries=3)] == [article_id]


def test_resolution_is_guarded(storage):
    ready = storage.register(make_article("https://news.example.com/other")).article_id
    with pytest.raises(InvalidStateTransition):
        storage.resolve_unknown_publish(ready, post_id=None)
    article_id = _unknown_outcome(storage)
    with pytest.raises(StorageError, match="post ID must look like"):
        storage.resolve_unknown_publish(article_id, post_id="../../me/feed")
    storage.save_facebook_ids(article_id, "111", "123_456")
    with pytest.raises(InvalidStateTransition, match="use --post-id"):
        storage.resolve_unknown_publish(article_id, post_id=None)


def test_failed_articles_resume_only_at_the_stage_that_failed(storage):
    article_id = storage.register(make_article()).article_id
    storage.transition(article_id, S.ANALYZING)
    storage.mark_failed(article_id, ErrorStage.ANALYZE, "bad answer")
    for wrong in (S.PUBLISHING, S.APPROVED):
        with pytest.raises(InvalidStateTransition, match="cannot resume"):
            storage.transition(article_id, wrong)
    assert storage.transition(article_id, S.ANALYZING).status is S.ANALYZING


def test_schema_refuses_states_without_their_content(storage):
    article_id = storage.register(make_article()).article_id
    storage.transition(article_id, S.ANALYZING)
    with pytest.raises(InvalidStateTransition):
        storage.transition(article_id, S.APPROVED)  # no category/headline/caption
    storage.transition(article_id, S.APPROVED, category=Category.TECHNOLOGY, catchy_headline="h",
                       facebook_caption="c")
    with pytest.raises(InvalidStateTransition):
        storage.transition(article_id, S.IMAGE_CREATED)  # no image
    assert storage.get(article_id).status is S.APPROVED


def test_each_stage_gets_its_own_retry_budget(storage):
    article_id = storage.register(make_article()).article_id
    for _ in range(2):
        storage.transition(article_id, S.ANALYZING)
        storage.mark_failed(article_id, ErrorStage.ANALYZE, "flaky")
    storage.transition(article_id, S.ANALYZING)
    approved = storage.transition(article_id, S.APPROVED, category=Category.TECHNOLOGY, catchy_headline="h",
                                  facebook_caption="c")
    assert approved.retry_count == 0  # analysis finished: the image stage starts fresh
    storage.mark_failed(article_id, ErrorStage.IMAGE, "disk full")
    retried = storage.transition(article_id, S.APPROVED)  # resuming a failed image keeps counting
    assert retried.retry_count == 1


def test_facebook_ids_survive_a_refused_status_change(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.recover_interrupted("other-run")  # someone moved it on meanwhile
    with pytest.raises(InvalidStateTransition):
        storage.record_post_created(reg.article_id, "111", "222_333")
    stored = storage.get(reg.article_id)
    assert (stored.facebook_photo_id, stored.facebook_post_id) == ("111", "222_333")
    assert storage.publishable_articles(max_retries=3) == []


def test_comment_in_flight_marker_is_saved_before_the_request(storage):
    reg = storage.register(make_article())
    _advance_to_ready(storage, reg.article_id)
    storage.transition(reg.article_id, S.PUBLISHING)
    storage.record_post_created(reg.article_id, "111", "222_333")
    marked = storage.mark_comment_in_flight(reg.article_id)
    assert (marked.status, marked.error_stage, marked.retry_count) == (S.COMMENT_PENDING, ErrorStage.COMMENT_UNKNOWN, 0)
    assert storage.record_comment_created(reg.article_id, "c1").status is S.PUBLISHED


def test_run_lock_is_shared_between_connections(tmp_path):
    clock = Clock()
    path = tmp_path / "shared.db"
    with Storage(path, clock=clock) as one, Storage(path, clock=clock) as two:
        one.acquire_run_lock("run-a", ttl=timedelta(minutes=5))
        with pytest.raises(AlreadyRunningError):
            two.acquire_run_lock("run-b")


def test_concurrent_claims_only_one_wins(tmp_path):
    clock = Clock()
    path = tmp_path / "claim.db"
    with Storage(path, clock=clock) as one, Storage(path, clock=clock) as two:
        article_id = one.register(make_article()).article_id
        one.transition(article_id, S.ANALYZING)
        with pytest.raises(InvalidStateTransition):
            two.transition(article_id, S.ANALYZING)


def test_v1_database_is_upgraded_to_the_five_sectors(tmp_path):
    """A database created by the tech-only version keeps its rows, ids and history."""
    import techspire.storage as storage_module

    path = tmp_path / "v1.db"
    old_categories = "'ARTIFICIAL INTELLIGENCE', 'CYBERSECURITY', 'STARTUPS & FUNDING'"
    conn = sqlite3.connect(path)
    conn.executescript(storage_module._articles_ddl("articles", old_categories) + storage_module._SCHEMA_REST)
    columns = ("url_hash, original_url, canonical_url, title, source_name, discovered_at, status, category, "
               "catchy_headline, facebook_caption, image_path, created_at, updated_at")
    for n, category in enumerate(("CYBERSECURITY", "STARTUPS & FUNDING")):
        ts = "2026-09-27T10:00:00+00:00"
        conn.execute(f"INSERT INTO articles ({columns}) VALUES (?, 'u', ?, 't', 's', ?, 'READY_FOR_REVIEW', ?, "
                     "'h', 'c', 'x.jpg', ?, ?)", (f"hash{n}", f"https://a.example/{n}", ts, category, ts, ts))
    conn.execute("INSERT INTO article_events (article_id, to_status, created_at) VALUES (1, 'DISCOVERED', ?)",
                 ("2026-09-27T10:00:00+00:00",))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    with Storage(path) as store:
        assert store.get(1).category is Category.TECHNOLOGY
        assert store.get(2).category is Category.ENTREPRENEURSHIP
        assert store.get(2).status is S.READY_FOR_REVIEW
        assert len(store.events(1)) == 1
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == storage_module.SCHEMA_VERSION
        new_id = store.register(make_article("https://a.example/new")).article_id
        assert new_id == 3  # ids continue after the migrated rows
        store.transition(new_id, S.ANALYZING)
        approved = store.transition(new_id, S.APPROVED, category=Category.CAREER_DEVELOPMENT,
                                    catchy_headline="h", facebook_caption="c")
        assert approved.category is Category.CAREER_DEVELOPMENT
        with pytest.raises(InvalidStateTransition):  # the second-upload guard came back with the table
            store._conn.execute("UPDATE articles SET facebook_post_id = '1_2' WHERE id = 1")
            store.transition(1, S.PUBLISHING)
