"""Shared fixtures. Real network access is blocked for every test."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from helpers import Clock

from techspire.config import Settings
from techspire.storage import Storage


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to open a real connection fails the test. Mocked transports still work."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Real network access is disabled in tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def _detach_app_log_handlers():
    """CLI tests configure logging against pytest's captured streams; detach them afterwards."""
    yield
    import logging

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_techspire", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "data" / "test.db",
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
        feeds_file=tmp_path / "feeds.yaml",
    )


@pytest.fixture
def storage(tmp_path: Path, clock: Clock) -> Storage:
    store = Storage(tmp_path / "state.db", clock=clock)
    yield store
    store.close()
