"""Worker configuration and graceful stop without database or browser calls."""

import asyncio
import signal

import pytest

from app.core.config import settings
from app.crawler import worker


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_worker_allows_internal_execution_without_user_login(monkeypatch, environment):
    monkeypatch.setattr(settings, "app_env", environment)
    monkeypatch.setattr(settings, "allow_local_identity", False)
    monkeypatch.setattr(settings, "local_auth_enabled", False)
    monkeypatch.setattr(settings, "agent_worker_embedded", False)
    worker.validate_worker_settings()


@pytest.mark.parametrize(
    "environment,local,local_auth,embedded",
    [
        ("unsupported", False, False, False),
        ("staging", True, False, False),
        ("staging", False, True, False),
        ("staging", False, False, True),
        ("production", True, False, False),
        ("production", False, True, False),
        ("production", False, False, True),
    ],
)
def test_unsupported_or_misconfigured_worker_fails(
    monkeypatch, environment, local, local_auth, embedded
):
    monkeypatch.setattr(settings, "app_env", environment)
    monkeypatch.setattr(settings, "allow_local_identity", local)
    monkeypatch.setattr(settings, "local_auth_enabled", local_auth)
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
