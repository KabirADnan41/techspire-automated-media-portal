"""Console + rotating-file logging with secret redaction and UTC timestamps.

Every formatted record (including tracebacks) passes through `redact()`, which
removes the configured secret values and anything shaped like a token or key.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

REDACTED = "[REDACTED]"

# (pattern, replacement) pairs, applied in order.
_TOKEN_PATTERNS = (
    # key=value, key: value, "key": "value" and URL-encoded key%3Dvalue
    (re.compile(r"(?i)\b(access_token|api_key|apikey|key|token|client_secret|password)"
                r"(\"?'?\s*(?:=|:|%3D)\s*\"?'?)([^&\s\"',;}]+)"), rf"\1\2{REDACTED}"),
    # Authorization header values
    (re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), REDACTED),  # Facebook access tokens
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),  # Google API keys (classic format)
    (re.compile(r"\bAQ\.[0-9A-Za-z_-]{30,}"), REDACTED),  # Google AI Studio keys (newer format)
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), REDACTED),  # OpenAI API keys
)

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NOISY_LOGGERS = ("httpx", "httpcore", "google_genai", "google.genai", "openai", "urllib3", "PIL")


class Redactor:
    def __init__(self) -> None:
        self._secrets: list[str] = []

    def set_secrets(self, secrets: Iterable[str]) -> None:
        # Longest first so a secret that contains another is fully removed.
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern, replacement in _TOKEN_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


_redactor = Redactor()


def redact(text: str) -> str:
    """Remove secrets from any text that may be logged, stored or displayed."""
    return _redactor(text)


def redact_short(text: str, limit: int = 300) -> str:
    """Redacted and length-capped text for error messages and stored errors."""
    return redact(text)[:limit]


def register_secrets(secrets: Iterable[str]) -> None:
    _redactor.set_secrets(secrets)


class RedactingFormatter(logging.Formatter):
    converter = time.gmtime  # UTC timestamps everywhere

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str, log_dir: Path, secrets: Iterable[str] = (), verbose: bool = False) -> Path:
    """Configure root logging. Safe to call more than once. Returns the log file path."""
    register_secrets(secrets)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "techspire.log"
    formatter = RedactingFormatter(_LOG_FORMAT, _DATE_FORMAT)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_techspire", False):
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    # Records logged with extra={"console": False} go to the file only (the CLI prints its own message).
    console.addFilter(lambda record: getattr(record, "console", True))
    console.setLevel(logging.DEBUG if verbose else getattr(logging, level))
    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG if verbose else getattr(logging, level))
    for handler in (console, file_handler):
        handler._techspire = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else getattr(logging, level))

    # Third-party HTTP/SDK loggers can echo request details; keep them quiet.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return log_file
