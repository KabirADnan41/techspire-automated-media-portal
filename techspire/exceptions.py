"""Error taxonomy. Messages are written for operators, not just developers."""

from __future__ import annotations


class TechspireError(Exception):
    """Base class for all expected application errors."""


class ConfigError(TechspireError):
    """Configuration is missing or invalid. The message says how to fix it."""


class StorageError(TechspireError):
    """The SQLite state database could not be used."""


class InvalidStateTransition(StorageError):
    """A state change was refused (wrong current state or not allowed)."""


class AlreadyRunningError(TechspireError):
    """Another Techspire run holds the run lock."""


class FeedError(TechspireError):
    """A single RSS feed could not be fetched or parsed."""


class AIProviderError(TechspireError):
    """The AI provider call failed. `transient` errors are worth retrying."""

    def __init__(self, message: str, *, transient: bool, status_code: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status_code = status_code
        self.retry_after = retry_after


class AIResponseValidationError(TechspireError):
    """The AI answered, but the answer broke the output contract."""


class ImageGenerationError(TechspireError):
    """The news card could not be rendered or saved."""


class PublishingDisabledError(TechspireError):
    """A Facebook write was attempted while any safety gate is closed."""


class FacebookError(TechspireError):
    """Base class for Facebook Graph API failures."""


class FacebookAPIError(FacebookError):
    """Facebook returned a structured error: the request was definitively refused."""

    def __init__(self, message: str, *, code: int | None = None, subcode: int | None = None,
                 fbtrace_id: str | None = None, transient: bool = False,
                 rate_limited: bool = False, auth_error: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id
        self.transient = transient
        self.rate_limited = rate_limited
        self.auth_error = auth_error


class FacebookOutcomeUnknownError(FacebookError):
    """The request may or may not have been applied (timeout, dropped connection,
    5xx, unreadable success response). Never retry a post automatically after this."""


class FacebookNotSentError(FacebookError):
    """The request never reached Facebook (connection could not be opened). Safe to retry."""
