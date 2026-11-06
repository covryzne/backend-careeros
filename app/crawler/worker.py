"""Run separately: python -m app.crawler.worker. PostgreSQL is the queue."""

# ruff: noqa: BLE001 - contain process/DB failures without logging credentials or portal payloads
import argparse
import asyncio
import logging
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.security import ActorContext
from app.crawler.diagnostics import log_failure
from app.crawler.engine import Engine, probe
from app.db.session import SessionLocal
from app.models.crawler import CrawlRun, CrawlRunItem, CrawlRunSource
from app.services.crawler import runs as service
from app.services.crawler.ingestion import apply_event
from app.services.shared.idempotency import lock_owner

log = logging.getLogger(__name__)


def transaction(factory, action):
    with factory() as db:
        result = action(db)
        db.commit()
        return result


async def execute(run_id, token, worker_id, *, factory=SessionLocal, engine_factory=Engine):
    with factory() as db:
        run = db.get(CrawlRun, run_id)
        config, owner_id = dict(run.config), run.owner_id
    failure = None
    clean = True
    started = time.monotonic()

    async def heartbeat():
        while True:
            await asyncio.sleep(4)
            transaction(
                factory,
                lambda db: service.heartbeat(
                    db, run_id, worker_id, token, settings.crawler_lease_seconds
                ),
            )

    async def crawl():
        for platform in config["platforms"]:
            for index, keyword in enumerate(config["keywords"]):
                with factory() as db:
                    source = db.scalar(
                        select(CrawlRunSource).where(
                            CrawlRunSource.run_id == run_id, CrawlRunSource.platform == platform
                        )
                    )
                    remaining = config["max_jobs_per_platform"] - source.jobs_found
                    seen = [
                        f"{platform}:{r.external_id}"
                        for r in db.scalars(
                            select(CrawlRunItem).where(
                                CrawlRunItem.run_id == run_id, CrawlRunItem.platform == platform
                            )
                        )
                    ]
                    if remaining <= 0 or source.error:
                        break
                browser = {"auto": None, "headed": False, "headless": True}[
                    settings.crawler_browser_mode
                ]
                request = {
                    "keyword": keyword,
                    "portals": [platform],
                    "location": config["location"],
                    "linkedin_geo_id": config["linkedin_geo_id"],
                    "days": config["days"],
                    "max_pages": config["max_pages"],
                    "max_jobs": remaining,
                    "timeout": config["timeout_seconds"],
                    "delay": config["delay_seconds"],
                    "headless": browser,
                }
                engine = engine_factory()
                log.info("Run %s: starting %s keyword %d", run_id, platform, index + 1)
                try:
                    pid = await engine.start()

                    def record_pid(db, process_id=pid):
                        run, _ = service.guarded(db, run_id, worker_id, token)
                        run.process_id = process_id

                    transaction(factory, record_pid)
                    await engine.send(
                        {
                            "request": request,
                            "seen_ids": seen,
                            "output_dir": str(
                                settings.crawler_root
                                / "output"
                                / "service-runs"
                                / str(run_id)
                                / platform
                                / str(index)
                            ),
                        }
                    )
                    async for event in engine.events():

                        def ingest(
                            db,
                            current_platform=platform,
                            current_keyword=keyword,
                            current_event=event,
                        ):
                            # Same owner -> slot -> run lock order as HTTP enqueue.
                            lock_owner(db, ActorContext(owner_id, "system"))
                            run, _ = service.guarded(db, run_id, worker_id, token)
                            apply_event(db, run, current_platform, current_keyword, current_event)

                        transaction(factory, ingest)
                finally:
                    nonlocal clean
                    try:
                        await engine.stop()

                        def clear_pid(db):
                            run, _ = service.guarded(db, run_id, worker_id, token, lease=False)
                            run.process_id = None

                        transaction(factory, clear_pid)
                    except Exception:
                        clean = False
                        raise RuntimeError(
                            "Browser cleanup could not be confirmed; slot retained"
                        ) from None

    pulse = asyncio.create_task(heartbeat())
    work = asyncio.create_task(crawl())
    try:
        done, _ = await asyncio.wait(
            {pulse, work},
            timeout=settings.crawler_run_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            failure = "CRAWLER_TIMEOUT"
        elif pulse in done:
            failure = "WORKER_LEASE_LOST"
            pulse.result()
        else:
            work.result()
    except asyncio.CancelledError:
        failure = "WORKER_INTERRUPTED"
        raise
    except Exception as exc:
        failure = failure or "CRAWLER_EXECUTION_FAILED"
        log_failure(log, "execute", exc, run_id)
    finally:
        work.cancel()
        try:
            await work
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_failure(log, "crawl_cleanup", exc, run_id)
        pulse.cancel()
        try:
            await pulse
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_failure(log, "heartbeat", exc, run_id)
        if clean:
            try:
                transaction(
                    factory, lambda db: service.finish(db, run_id, worker_id, token, failure)
                )
            except Exception as exc:
                log_failure(log, "finish_retained_slot", exc, run_id)
        log.info("Crawler run %s ended after %.1fs", run_id, time.monotonic() - started)


def process_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.windll.kernel32.GetLastError() != 87  # Fail closed on access denied.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def recover(run_id):
    """Only after supervisor/operator cleanup; never timeout-steal a browser slot."""

    def action(db):
        state = service.slot(db)
        run = db.get(CrawlRun, run_id)
        if not run or state.active_run_id != run_id or run.status != "running":
            raise RuntimeError("Run is not an interrupted active execution")
        host, pid, _ = (run.worker_id or "").split("|")
        if (
            host != socket.gethostname()[:60]
            or process_alive(int(pid))
            or process_alive(run.process_id)
        ):
            raise RuntimeError(
                "Original worker/engine may still be alive; stop its browser tree first"
            )
        service.finish(db, run_id, run.worker_id, run.fencing_token, "WORKER_INTERRUPTED")

    transaction(SessionLocal, action)


def validate_worker_settings():
    if settings.app_env not in {"development", "test", "staging"}:
        raise RuntimeError("Crawler worker supports development/test/staging only")
    if settings.app_env == "staging" and settings.allow_local_identity:
        raise RuntimeError("Staging worker requires ALLOW_LOCAL_IDENTITY=false")
    if settings.agent_worker_embedded:
        raise RuntimeError("Separate crawler worker requires AGENT_WORKER_EMBEDDED=false")


async def main(once=False):
    validate_worker_settings()
    log.info(
        "Crawler worker config: environment=%s browser_mode=%s timeout_seconds=%s lease_seconds=%s",
        settings.app_env,
        settings.crawler_browser_mode,
        settings.crawler_run_timeout_seconds,
        settings.crawler_lease_seconds,
    )
    log.info("Starting crawler worker; checking installed Selenium adapters...")
    capabilities = probe()  # Discover the installed Selenium engine, not UI constants.
    worker_id = f"{socket.gethostname()[:60]}|{os.getpid()}|{uuid.uuid4().hex[:12]}"
    log.info("Worker %s ready. Start a manual run from the Crawler dashboard.", worker_id)
    last_notice = None
    while True:
        try:

            def register(db):
                state = service.slot(db)
                state.capabilities = capabilities
                state.heartbeat_at = datetime.now(UTC)

            transaction(SessionLocal, register)
            claimed = transaction(
                SessionLocal,
                lambda db: service.claim(db, worker_id, settings.crawler_lease_seconds),
            )
            if claimed:
                log.info("Claimed crawler run %s", claimed[0])
                await execute(*claimed, worker_id)
                last_notice = None
            else:
                notice = transaction(SessionLocal, waiting_notice)
                if notice != last_notice:
                    log.info("%s", notice)
                    last_notice = notice
            if once:
                return
        except Exception as exc:
            log_failure(log, "polling", exc)
            if once:
                raise
        await asyncio.sleep(2)


def waiting_notice(db):
    state = service.slot(db)
    if state.active_run_id is None:
        return "Idle: waiting for a queued run from Start Crawl Now (poll interval: 2s)."
    run = db.get(CrawlRun, state.active_run_id)
    if run is not None and run.status == "running":
        if run.lease_until is None or service.utc(run.lease_until) <= datetime.now(UTC):
            return (
                f"Run {run.id} has an expired lease; slot retained for safe operator recovery. "
                "Confirm the original browser has stopped before recovery."
            )
        return f"Run {run.id} is owned by another execution; waiting without starting a browser."
    return f"Slot reserved for run {state.active_run_id}; waiting for its state to change."


async def serve(once=False):
    """Docker SIGTERM cancels work through the existing browser/lease cleanup path."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(main(once))
    registered = False
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            registered = True
        except NotImplementedError:  # Windows host development uses asyncio's Ctrl+C handling.
            pass
        await task
    except asyncio.CancelledError:
        log.info("Crawler worker stopped after cancellation cleanup")
    finally:
        if registered:
            loop.remove_signal_handler(signal.SIGTERM)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Poll once (also registers installed capabilities)"
    )
    parser.add_argument("--recover", type=uuid.UUID)
    parser.add_argument("--confirm-browser-stopped", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.recover:
        if not args.confirm_browser_stopped:
            parser.error(
                "Recovery requires --confirm-browser-stopped after verifying Chrome cleanup"
            )
        recover(args.recover)
    else:
        asyncio.run(serve(args.once))
