"""Facebook Graph API publisher: implemented and tested with mocks, LOCKED by default.

A live write needs ALL of these at the same time:
  1. DRY_RUN=false                (config)
  2. PUBLISH_ENABLED=true         (config)
  3. --publish on the command line (explicit operator action for that run)
  plus FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN.
The gates are checked when the publisher is built (authorize_publishing) and again
before every single request. Any closed gate raises PublishingDisabledError.

Future flow (documented Graph API behaviour, v26.0):
  POST /{page-id}/photos   multipart: source=<jpeg>, caption, published=true
      -> {"id": <photo id>, "post_id": <post id>}   (two different identifiers)
  POST /{post-id}/comments form: message="Read full story: <url>"
      -> {"id": <comment id>}
The token is sent only in the Authorization header (RFC 6750 section 2.1), never in
the URL or the request body, and never logged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from techspire.config import GRAPH_HOST, Settings
from techspire.exceptions import (
    FacebookAPIError,
    FacebookError,
    FacebookNotSentError,
    FacebookOutcomeUnknownError,
    PublishingDisabledError,
)
from techspire.logging_config import redact_short
from techspire.models import FACEBOOK_OBJECT_ID, PublishResult

log = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # Graph API limit for photo uploads
MAX_COMMENT_PAGES = 5  # 5 x 100 comments scanned when reconciling
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, 80001})
TRANSIENT_CODES = frozenset({1, 2})
AUTH_CODES = frozenset({102, 190})
PERMISSION_CODES = frozenset({10, *range(200, 300)})

_PERMIT_KEY = object()


def closed_gates(settings: Settings, publish_flag: bool) -> list[str]:
    """Human-readable reasons why live publishing is blocked (empty list = all gates open)."""
    reasons = []
    if settings.dry_run:
        reasons.append("DRY_RUN is true")
    if not settings.publish_enabled:
        reasons.append("PUBLISH_ENABLED is false")
    if not publish_flag:
        reasons.append("the --publish flag was not given")
    if not settings.fb_page_id:
        reasons.append("FB_PAGE_ID is missing")
    if not settings.fb_page_access_token:
        reasons.append("FB_PAGE_ACCESS_TOKEN is missing")
    return reasons


@dataclass(frozen=True)
class PublishPermit:
    """Proof that every gate was open. Only authorize_publishing() can create one."""

    page_id: str
    _key: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._key is not _PERMIT_KEY:
            raise PublishingDisabledError("PublishPermit can only be created by authorize_publishing().")


def authorize_publishing(settings: Settings, publish_flag: bool) -> PublishPermit:
    reasons = closed_gates(settings, publish_flag)
    if reasons:
        raise PublishingDisabledError("Live Facebook publishing is disabled: " + "; ".join(reasons) + ".")
    return PublishPermit(page_id=settings.fb_page_id, _key=_PERMIT_KEY)


def token_fingerprint(token: str) -> str:
    """Short non-secret identifier of a token, for audit logs (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def stops_publishing(exc: FacebookError) -> bool:
    """Failures that make further Facebook calls in this run pointless or risky: an unknown
    outcome, Facebook unreachable, a token/permission problem, or rate limiting."""
    if isinstance(exc, (FacebookOutcomeUnknownError, FacebookNotSentError)):
        return True
    return isinstance(exc, FacebookAPIError) and (exc.auth_error or exc.rate_limited)


class FacebookPublisher:
    def __init__(self, settings: Settings, permit: PublishPermit, *, publish_flag: bool,
                 client: httpx.Client | None = None) -> None:
        if not isinstance(permit, PublishPermit) or permit.page_id != settings.fb_page_id:
            raise PublishingDisabledError("A valid PublishPermit for this page is required.")
        self._settings = settings
        self._publish_flag = publish_flag
        self._base = f"{GRAPH_HOST}/{settings.fb_graph_api_version}"
        # TLS verification stays on (httpx default); explicit timeout on every request.
        self._client = client or httpx.Client(timeout=httpx.Timeout(settings.facebook_request_timeout))
        self.token_fingerprint = token_fingerprint(settings.fb_page_access_token)
        self._check_gates()

    def close(self) -> None:
        self._client.close()

    def _check_gates(self) -> None:
        reasons = closed_gates(self._settings, self._publish_flag)
        if reasons:
            raise PublishingDisabledError("Live Facebook publishing is disabled: " + "; ".join(reasons) + ".")

    # ------------------------------------------------------------------ writes
    def publish_photo(self, image_path: Path, caption: str) -> PublishResult:
        """Upload the card as a published Page photo post."""
        self._check_gates()
        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise FacebookNotSentError(f"Card {image_path.name} could not be read ({exc}); nothing was sent.") from None
        if len(data) > MAX_PHOTO_BYTES:
            raise FacebookAPIError(f"{image_path.name} is larger than 10 MB.")
        body = self._post(
            f"{self._base}/{self._settings.fb_page_id}/photos",
            data={"caption": caption, "published": "true"},
            files={"source": (image_path.name, data, "image/jpeg")},
            ambiguous_is_unknown=True,
        )
        photo_id = _graph_id(body, "Facebook answered the photo upload without a photo id; the post may exist.")
        post_id = body.get("post_id")
        result = PublishResult(photo_id=photo_id, post_id=str(post_id) if post_id else None)
        log.info("Facebook photo post created (photo_id=%s, post_id=%s, token=%s)",
                 result.photo_id, result.post_id, self.token_fingerprint)
        return result

    def create_comment(self, object_id: str, message: str) -> str:
        """Add the source-link comment to an existing post."""
        self._check_gates()
        _require_object_id(object_id)
        body = self._post(f"{self._base}/{object_id}/comments", data={"message": message})
        comment_id = _graph_id(body, "Facebook answered the comment request without a comment id.")
        log.info("Facebook source comment created (comment_id=%s, token=%s)", comment_id, self.token_fingerprint)
        return comment_id

    # ------------------------------------------------------------------- reads
    def find_comment(self, object_id: str, message: str) -> str | None:
        """Read-only reconciliation: return the ID of an existing Page comment with exactly
        this message, so a comment whose outcome was unknown is never posted twice."""
        self._check_gates()
        _require_object_id(object_id)
        params = {"fields": "id,message,from", "filter": "stream", "order": "reverse_chronological",
                  "limit": "100"}
        for _ in range(MAX_COMMENT_PAGES):
            try:
                response = self._client.get(f"{self._base}/{object_id}/comments", params=params,
                                            headers=self._auth_header())
            except httpx.HTTPError as exc:
                raise FacebookNotSentError(f"Could not read comments from Facebook ({type(exc).__name__}).") from None
            body = parse_graph_response(response)
            comments = body.get("data")
            for comment in comments if isinstance(comments, list) else []:
                author = comment.get("from") if isinstance(comment, dict) else None
                # Only a comment written by the Page itself counts; anyone else could post the same text.
                if (comment.get("message") == message and isinstance(author, dict)
                        and str(author.get("id")) == self._settings.fb_page_id):
                    return str(comment.get("id"))
            paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            if not paging.get("next") or not cursors.get("after"):
                return None
            # Follow the cursor ourselves: paging.next URLs embed the token in the query string.
            params = {**params, "after": str(cursors["after"])}
        return None

    # ----------------------------------------------------------------- transport
    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.fb_page_access_token}"}

    def _post(self, url: str, *, data: dict[str, str], files: dict[str, Any] | None = None,
              ambiguous_is_unknown: bool = False) -> dict[str, Any]:
        try:
            response = self._client.post(url, data=data, files=files, headers=self._auth_header())
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise FacebookNotSentError(f"Could not reach Facebook ({type(exc).__name__}); nothing was sent.") from None
        except httpx.HTTPError as exc:
            raise FacebookOutcomeUnknownError(
                f"Facebook request interrupted ({type(exc).__name__}); it may or may not have been applied."
            ) from None
        usage = response.headers.get("x-business-use-case-usage") or response.headers.get("x-app-usage")
        if usage:
            log.debug("Graph API usage header: %s", usage)
        return parse_graph_response(response, ambiguous_is_unknown=ambiguous_is_unknown)


def _require_object_id(object_id: str) -> None:
    if not FACEBOOK_OBJECT_ID.fullmatch(object_id or ""):
        raise FacebookAPIError(f"Refusing to use malformed Facebook object id {object_id!r}.")


def _graph_id(body: dict[str, Any], missing_message: str) -> str:
    value = body.get("id")
    if not isinstance(value, (str, int)) or not str(value):
        raise FacebookOutcomeUnknownError(missing_message)
    return str(value)


def parse_graph_response(response: httpx.Response, *, ambiguous_is_unknown: bool = False) -> dict[str, Any]:
    """Return the JSON body of a successful response, or raise a classified error.

    With ambiguous_is_unknown (photo uploads), errors Facebook marks as transient and
    non-Graph error replies count as "outcome unknown", because a retry could post twice."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None

    if response.is_success and isinstance(body, dict) and error is None:
        return body
    if response.is_success:
        raise FacebookOutcomeUnknownError(
            f"Facebook returned HTTP {response.status_code} with an unreadable body; the outcome is unknown."
        )
    if response.status_code >= 500:
        detail = redact_short(str(error.get("message", ""))) if isinstance(error, dict) else ""
        raise FacebookOutcomeUnknownError(
            f"Facebook returned HTTP {response.status_code} {detail}".strip() + "; the outcome is unknown."
        )
    if not isinstance(error, dict):
        if ambiguous_is_unknown and response.status_code != 429:
            raise FacebookOutcomeUnknownError(
                f"Facebook returned HTTP {response.status_code} without a Graph error; the outcome is unknown."
            )
        raise FacebookAPIError(f"Facebook refused the request (HTTP {response.status_code}).",
                               rate_limited=response.status_code == 429)
    code = error.get("code") if isinstance(error.get("code"), int) else None
    subcode = error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
    message = redact_short(str(error.get("message") or "no message"))
    rate_limited = response.status_code == 429 or code in RATE_LIMIT_CODES
    auth_error = code in AUTH_CODES or code in PERMISSION_CODES
    transient = bool(error.get("is_transient")) or code in TRANSIENT_CODES or rate_limited
    if ambiguous_is_unknown and transient and not rate_limited:
        raise FacebookOutcomeUnknownError(f"Facebook reported a temporary error {code}: {message}; "
                                          "the outcome is unknown.")
    hint = ""
    if auth_error:
        hint = " Check the Page access token and its permissions (see README: Future live publishing)."
    elif rate_limited:
        hint = " Facebook is rate limiting this app/page; the next scheduled run will try again."
    raise FacebookAPIError(
        f"Facebook error {code}{'/' + str(subcode) if subcode else ''}: {message}.{hint}",
        code=code, subcode=subcode,
        fbtrace_id=str(error.get("fbtrace_id")) if error.get("fbtrace_id") else None,
        transient=transient, rate_limited=rate_limited, auth_error=auth_error,
    )
