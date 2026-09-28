"""Facebook publisher tests. Every request goes to an in-memory mock; nothing reaches Facebook."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from techspire.config import Settings
from techspire.exceptions import (
    FacebookAPIError,
    FacebookNotSentError,
    FacebookOutcomeUnknownError,
    PublishingDisabledError,
)
from techspire.fb_publisher import FacebookPublisher, PublishPermit, authorize_publishing, closed_gates

TOKEN = "EAATESTTOKEN0000000000000000000000"
OPEN = Settings(dry_run=False, publish_enabled=True, fb_page_id="123456789", fb_page_access_token=TOKEN)


def publisher_with(handler) -> tuple[FacebookPublisher, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording))
    permit = authorize_publishing(OPEN, publish_flag=True)
    return FacebookPublisher(OPEN, permit, publish_flag=True, client=client), requests


@pytest.fixture
def image(tmp_path: Path) -> Path:
    path = tmp_path / "2026-09-28_0123456789abcdef.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return path


def _error(status: int, code: int, message: str = "error", **extra) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "type": "OAuthException", "code": code,
                                                  "fbtrace_id": "TRACE123", **extra}})


# ------------------------------------------------------------------ gates
def test_default_configuration_keeps_publishing_disabled():
    reasons = closed_gates(Settings(), publish_flag=False)
    assert "DRY_RUN is true" in reasons
    assert "PUBLISH_ENABLED is false" in reasons
    assert "the --publish flag was not given" in reasons
    with pytest.raises(PublishingDisabledError):
        authorize_publishing(Settings(), publish_flag=True)


@pytest.mark.parametrize("change,flag", [
    ({"dry_run": True}, True),
    ({"publish_enabled": False}, True),
    ({}, False),
    ({"fb_page_access_token": ""}, True),
    ({"fb_page_id": ""}, True),
])
def test_every_gate_is_required(change, flag):
    with pytest.raises(PublishingDisabledError):
        authorize_publishing(replace(OPEN, **change), publish_flag=flag)


def test_permit_cannot_be_forged():
    with pytest.raises(PublishingDisabledError):
        PublishPermit(page_id="123456789")


def test_publisher_rechecks_gates_itself():
    permit = authorize_publishing(OPEN, publish_flag=True)
    with pytest.raises(PublishingDisabledError):
        FacebookPublisher(OPEN, permit, publish_flag=False, client=httpx.Client())
    with pytest.raises(PublishingDisabledError):
        FacebookPublisher(replace(OPEN, fb_page_id="999"), permit, publish_flag=True, client=httpx.Client())


# ---------------------------------------------------------------- success
def test_successful_image_upload(image):
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json={"id": "111", "post_id": "123456789_222"}))
    result = publisher.publish_photo(image, "Caption text #AI")
    assert (result.photo_id, result.post_id) == ("111", "123456789_222")
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://graph.facebook.com/v26.0/123456789/photos"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    body = request.content
    assert TOKEN.encode() not in body and TOKEN not in str(request.url)  # token only in the header
    assert b'name="caption"' in body and b"Caption text #AI" in body
    assert b'name="source"; filename="2026-09-28_0123456789abcdef.jpg"' in body


def test_photo_and_post_ids_are_kept_separately(image):
    publisher, _ = publisher_with(lambda r: httpx.Response(200, json={"id": "111"}))
    result = publisher.publish_photo(image, "c")
    assert result.photo_id == "111" and result.post_id is None


def test_successful_source_comment():
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json={"id": "222_333"}))
    comment_id = publisher.create_comment("123456789_222", "Read full story: https://example.com/a")
    assert comment_id == "222_333"
    assert str(requests[0].url) == "https://graph.facebook.com/v26.0/123456789_222/comments"
    assert requests[0].content == b"message=Read+full+story%3A+https%3A%2F%2Fexample.com%2Fa"


# ---------------------------------------------------------------- failures
def test_image_upload_refused(image):
    publisher, _ = publisher_with(lambda r: _error(400, 100, "Invalid parameter"))
    with pytest.raises(FacebookAPIError) as info:
        publisher.publish_photo(image, "c")
    assert info.value.code == 100 and not info.value.rate_limited and not info.value.auth_error
    assert info.value.fbtrace_id == "TRACE123"


@pytest.mark.parametrize("response", [
    _error(400, 4), _error(400, 17), _error(400, 32), _error(400, 613), _error(400, 80001),
    httpx.Response(429, json={"error": {"message": "slow", "code": 9999}}),
])
def test_rate_limiting_is_recognized(image, response):
    publisher, _ = publisher_with(lambda r: response)
    with pytest.raises(FacebookAPIError) as info:
        publisher.publish_photo(image, "c")
    assert info.value.rate_limited and info.value.transient


def test_auth_error_is_recognized_and_token_is_redacted(image):
    publisher, _ = publisher_with(lambda r: _error(401, 190, f"Error validating access token {TOKEN}",
                                                   error_subcode=463))
    with pytest.raises(FacebookAPIError) as info:
        publisher.publish_photo(image, "c")
    assert info.value.auth_error and info.value.subcode == 463
    assert TOKEN not in str(info.value)
    assert "Check the Page access token" in str(info.value)


def test_missing_permission_is_an_auth_error():
    publisher, _ = publisher_with(lambda r: _error(403, 200, "Permissions error"))
    with pytest.raises(FacebookAPIError) as info:
        publisher.create_comment("123_456", "Read full story: https://example.com")
    assert info.value.auth_error


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"<html>not json</html>"),
    httpx.Response(200, json={"success": True}),
    httpx.Response(200, json=["unexpected"]),
    httpx.Response(500, json={"error": {"message": "An unknown error occurred", "code": 1, "is_transient": True}}),
    httpx.Response(503, content=b""),
])
def test_ambiguous_photo_responses_are_outcome_unknown(image, response):
    publisher, _ = publisher_with(lambda r: response)
    with pytest.raises(FacebookOutcomeUnknownError):
        publisher.publish_photo(image, "c")


def test_connection_failure_means_nothing_was_sent(image):
    def refuse(request):
        raise httpx.ConnectError("no route", request=request)

    publisher, _ = publisher_with(refuse)
    with pytest.raises(FacebookNotSentError):
        publisher.publish_photo(image, "c")


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": {"message": "Please retry", "code": 1, "is_transient": True}}),
    httpx.Response(400, json={"error": {"message": "Service unavailable", "code": 2}}),
    httpx.Response(403, content=b"<html>proxy said no</html>"),
])
def test_ambiguous_photo_refusals_are_outcome_unknown(image, response):
    publisher, _ = publisher_with(lambda r: response)
    with pytest.raises(FacebookOutcomeUnknownError):
        publisher.publish_photo(image, "c")


def test_transient_comment_error_stays_a_retryable_refusal():
    publisher, _ = publisher_with(lambda r: httpx.Response(400, json={"error": {"message": "x", "code": 2}}))
    with pytest.raises(FacebookAPIError) as info:
        publisher.create_comment("123_456", "Read full story: https://example.com")
    assert info.value.transient


def test_find_comment_only_trusts_the_pages_own_comment_and_follows_cursors():
    pages = [
        {"data": [{"id": "1", "message": "Read full story: https://example.com/a"},  # author hidden
                  {"id": "2", "message": "Read full story: https://example.com/a", "from": {"id": "42"}}],
         "paging": {"cursors": {"after": "CURSOR2"}, "next": "https://graph.facebook.com/next?access_token=x"}},
        {"data": [{"id": "3", "message": "Read full story: https://example.com/a", "from": {"id": "123456789"}}]},
    ]
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json=pages.pop(0)))
    assert publisher.find_comment("123456789_222", "Read full story: https://example.com/a") == "3"
    assert requests[1].url.params["after"] == "CURSOR2"
    assert all("access_token" not in str(r.url) for r in requests)


def test_find_comment_is_a_read_only_lookup():
    data = {"data": [
        {"id": "9", "message": "Read full story: https://example.com/a", "from": {"id": "999999"}},  # someone else
        {"id": "7", "message": "Nice post", "from": {"id": "123456789"}},
        {"id": "5", "message": "Read full story: https://example.com/a", "from": {"id": "123456789"}},
    ]}
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json=data))
    assert publisher.find_comment("123456789_222", "Read full story: https://example.com/a") == "5"
    assert publisher.find_comment("123456789_222", "Read full story: https://example.com/other") is None
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v26.0/123456789_222/comments"
    assert request.headers["authorization"] == f"Bearer {TOKEN}" and TOKEN not in str(request.url)


def test_unreadable_card_means_nothing_was_sent(tmp_path):
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json={"id": "1"}))
    with pytest.raises(FacebookNotSentError, match="could not be read"):
        publisher.publish_photo(tmp_path / "missing.jpg", "c")
    assert requests == []


def test_read_timeout_after_sending_is_outcome_unknown(image):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    publisher, _ = publisher_with(slow)
    with pytest.raises(FacebookOutcomeUnknownError):
        publisher.publish_photo(image, "c")


def test_comment_failure_after_image_success(image):
    responses = [httpx.Response(200, json={"id": "111", "post_id": "123456789_222"}), _error(400, 368, "Blocked")]
    publisher, requests = publisher_with(lambda r: responses.pop(0))
    result = publisher.publish_photo(image, "c")
    with pytest.raises(FacebookAPIError):
        publisher.create_comment(result.post_id, "Read full story: https://example.com")
    assert len(requests) == 2


@pytest.mark.parametrize("object_id", ["", "../me/feed", "123/../../x", "abc"])
def test_malformed_comment_target_is_refused_before_any_request(object_id):
    publisher, requests = publisher_with(lambda r: httpx.Response(200, json={"id": "1"}))
    with pytest.raises(FacebookAPIError):
        publisher.create_comment(object_id, "m")
    assert requests == []


def test_review_page_survives_images_on_another_drive(tmp_path, monkeypatch):
    from helpers import make_article

    from techspire import preview
    from techspire.enums import ArticleStatus, Category
    from techspire.storage import Storage

    def cross_drive(*args, **kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'E:'")

    with Storage(tmp_path / "p.db") as store:
        stored = replace(store.get(store.register(make_article()).article_id), status=ArticleStatus.READY_FOR_REVIEW,
                         category=Category.TECHNOLOGY, catchy_headline="h", facebook_caption="c",
                         image_path=tmp_path / "img.jpg")
    monkeypatch.setattr(preview.os.path, "relpath", cross_drive)
    page = preview.write_review_page(tmp_path / "index.html", [stored], [], [], "DRY-RUN").read_text(encoding="utf-8")
    assert "file:///" in page


def test_payload_preview_contains_no_token(tmp_path):
    from helpers import make_article

    from techspire.enums import ArticleStatus
    from techspire.preview import future_payload
    from techspire.storage import Storage

    with Storage(tmp_path / "p.db") as store:
        article_id = store.register(make_article()).article_id
        stored = replace(store.get(article_id), status=ArticleStatus.READY_FOR_REVIEW,
                         image_path=tmp_path / "x.jpg", facebook_caption="c")
    payload = json.dumps(future_payload(stored, "v26.0"))
    assert "Read full story: https://news.example.com/ai/story-1" in payload
    assert "/v26.0/{page-id}/photos" in payload
    assert "EAA" not in payload
