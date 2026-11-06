"""Staging worker configuration and graceful stop without database or browser calls."""

import asyncio
import signal

import pytest

from app.core.config import settings
from app.crawler import worker


def test_staging_worker_allows_internal_execution_without_user_login(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "allow_local_identity", False)
    monkeypatch.setattr(settings, "agent_worker_embedded", False)
    worker.validate_worker_settings()


@pytest.mark.parametrize(
    "environment,local,embedded",
    [
        ("production", False, False),
        ("staging", True, False),
        ("staging", False, True),
    ],
)
def test_unsupported_or_misconfigured_worker_fails(monkeypatch, environment, local, embedded):
    monkeypatch.setattr(settings, "app_env", environment)
    monkeypatch.setattr(settings, "allow_local_identity", local)
    monkeypatch.setattr(settings, "agent_worker_embedded", embedded)
    with pytest.raises(RuntimeError):
        worker.validate_worker_settings()


def test_sigterm_waits_for_worker_cancellation_cleanup(monkeypatch):
    events = []

    async def main(once):
        assert once is False
        try:
            events.append("started")
            await asyncio.Event().wait()
        finally:
            events.append("cleanup")

    async def run():
        loop = asyncio.get_running_loop()

        def register(sig, callback):
            assert sig == signal.SIGTERM
            loop.call_soon(callback)

        monkeypatch.setattr(loop, "add_signal_handler", register)
        monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: events.append("removed"))
        await worker.serve()

    monkeypatch.setattr(worker, "main", main)
    asyncio.run(run())
    assert events == ["started", "cleanup", "removed"]
