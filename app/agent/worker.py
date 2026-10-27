import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.runtime import process_run
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.workflow import AgentRun
from app.services.agent_settings import policy_for


def claim_next(session_factory=SessionLocal):
    now = datetime.now(UTC)
    with session_factory() as db:
        expired = db.scalars(
            select(AgentRun)
            .where(AgentRun.status == "running", AgentRun.lease_until < now)
            .with_for_update(skip_locked=True)
        ).all()
        for item in expired:
            item.status = "failed"
            item.error_code = "WORKER_INTERRUPTED"
            item.completed_at = now
        item = db.scalar(
            select(AgentRun)
            .where(AgentRun.status == "pending")
            .order_by(AgentRun.created_at, AgentRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item and not policy_for(db, item.owner_id).enabled:
            item.status = "cancelled"
            item.cancel_requested = True
            item.completed_at = now
            db.commit()
            return None
        if item:
            item.status = "running"
            item.started_at = now
            item.lease_until = now + timedelta(
                seconds=min(item.timeout_seconds, settings.agent_timeout_seconds)
            )
            result = item.id
        else:
            result = None
        db.commit()
        return result


async def main():
    if not settings.agent_runtime_allowed or settings.app_env not in {"development", "test"}:
        raise RuntimeError("Agent worker requires an allowed private development/test runtime")
    while True:
        try:
            run_id = claim_next()
            if run_id:
                await process_run(run_id)
            else:
                await asyncio.sleep(1)
        except Exception:  # noqa: BLE001 - contain polling failures without logging private payloads
            import logging

            logging.getLogger(__name__).error(
                "Agent worker polling failed; retrying without exposing request data"
            )
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
