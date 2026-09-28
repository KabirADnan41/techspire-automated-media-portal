import logging

import pytest

from techspire.logging_config import REDACTED, configure_logging, redact, register_secrets


@pytest.mark.parametrize("text,secret", [
    ("GET https://graph.facebook.com/v26.0/me?access_token=abcdef123456&x=1", "abcdef123456"),
    ('{"access_token": "abcdef123456", "id": "1"}', "abcdef123456"),
    ("token = abcdef123456", "abcdef123456"),
    ("callback?access_token%3Dabcdef123456", "abcdef123456"),
    ("Authorization: Bearer abcdef123456789", "abcdef123456789"),
    ("fb token EAAB1234567890abcdefghijklmnop leaked", "EAAB1234567890abcdefghijklmnop"),
    ("key AIzaSyA1234567890abcdefghijklmnopqrstu", "AIzaSyA1234567890abcdefghijklmnopqrstu"),
    ("gemini AQ.Ab1234567890abcdefghijklmnopqrstuvwx", "AQ.Ab1234567890abcdefghijklmnopqrstuvwx"),
    ("openai sk-proj1234567890abcdefghij", "sk-proj1234567890abcdefghij"),
])
def test_token_shapes_are_redacted(text, secret):
    out = redact(text)
    assert secret not in out and REDACTED in out


def test_registered_secrets_are_redacted_anywhere():
    register_secrets(["my-very-secret-value"])
    try:
        assert redact("error: my-very-secret-value was rejected") == f"error: {REDACTED} was rejected"
    finally:
        register_secrets([])


def test_log_file_never_contains_secrets(tmp_path):
    log_file = configure_logging("INFO", tmp_path, secrets=["TOPSECRET-123456"])
    try:
        logging.getLogger("techspire.test").error("request failed with TOPSECRET-123456 in the body")
        try:
            raise ValueError("TOPSECRET-123456")
        except ValueError:
            logging.getLogger("techspire.test").exception("traceback check")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "TOPSECRET-123456" not in content and content.count(REDACTED) >= 2
    finally:
        register_secrets([])
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_techspire", False):
                root.removeHandler(handler)
                handler.close()
