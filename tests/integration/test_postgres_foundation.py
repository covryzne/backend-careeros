import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.agent.context import ToolContext
from app.core.config import settings
from app.core.security import ActorContext
from app.models.candidate import CandidateSkill
from app.models.workflow import Application, AuditEvent
from app.schemas.agent import MoveArguments, RunCreate
from app.schemas.profile import CandidateSkillCreate, ProfileUpdate
from app.schemas.workflow import ApplicationCreate, JobCreate
from app.services import agent_runs, application_workflows, approvals, job_workflows, profile

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Dedicated PostgreSQL test database required"
)


@pytest.fixture
def pg():
    url = os.environ["TEST_DATABASE_URL"]
    from sqlalchemy.engine import make_url

    assert make_url(url).database.startswith("careeros_test_")
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def test_concurrent_profile_writes_and_approval_exactly_once(pg, monkeypatch):
    actor = ActorContext(uuid.uuid4())
    with pg() as db:
        profile.update_profile(db, actor, ProfileUpdate(full_name="Integration Candidate"))
        job = job_workflows.create_job(
            db, actor, payload=JobCreate(title="Test", company="Example")
        )
        application = application_workflows.create_application(
            db,
            actor,
            payload=ApplicationCreate(
                job_id=job.id,
                status="applied",
                application_date="2026-10-27",
                application_channel="manual",
            ),
        )
        db.commit()
        application_id = application.id
    barrier = Barrier(2)

    def write(name):
        with pg() as db:
            barrier.wait()
            try:
                profile.mutate_child(
                    db,
                    actor,
                    CandidateSkill,
                    "create",
                    payload=CandidateSkillCreate(name=name),
                    expected=1,
                )
                db.commit()
                return "ok"
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["Skill A", "Skill B"]))
    assert sorted(map(str, results)) == ["409", "ok"]
    with pg() as db:
        assert profile.load_profile(db, actor).profile_version == 2
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "agent_approvals_enabled", True)
    with pg() as db:
        run = agent_runs.create_run(
            db,
            actor,
            RunCreate(
                task_type="tracking_assistant",
                instruction="Propose interview",
                allowed_tools=["update_application_status"],
            ),
        )
        run.status = "completed"
        db.flush()
        context = ToolContext(
            ActorContext(actor.owner_id, "agent"), run.id, frozenset(run.allowed_tools), run.scope
        )
        approval = approvals.prepare_action(
            db,
            context,
            "update_application_status",
            MoveArguments(application_id=application_id, status="interview", expected_version=1),
        )
        db.commit()
        approval_id = approval.id
        digest = approval.content_hash
    barrier = Barrier(2)

    def approve(_):
        with pg() as db:
            barrier.wait()
            result = approvals.decide(db, actor, approval_id, digest, True)
            db.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(approve, [1, 2]))
    assert results[0] == results[1]
    with pg() as db:
        assert db.get(Application, application_id).version == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.entity_id == approval_id, AuditEvent.action == "approval_executed"
                )
            )
            == 1
        )


def test_concurrent_cv_versions_are_monotonic_per_role(pg, tmp_path, monkeypatch):
    from app.models.foundation import CandidateDocument
    from app.services.documents import upload_document

    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    actor = ActorContext(uuid.uuid4())
    barrier = Barrier(2)

    def upload(_):
        with pg() as db:
            barrier.wait()
            item, _ = upload_document(
                db, actor, "cv.pdf", "application/pdf", b"%PDF-1.4", target_role="AI Engineer"
            )
            db.commit()
            return item.version_number

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(upload, [1, 2])) == [1, 2]
    with pg() as db:
        docs = db.scalars(
            select(CandidateDocument).where(CandidateDocument.owner_id == actor.owner_id)
        ).all()
        assert len(docs) == 2 and {item.role_key for item in docs} == {"ai engineer"}


def test_concurrent_matching_refresh_uses_one_current_cache_row(pg):
    from app.models.workflow import JobMatch
    from app.services.matching import ALGORITHM, refresh

    actor = ActorContext(uuid.uuid4())
    with pg() as db:
        owner = profile.update_profile(
            db,
            actor,
            ProfileUpdate(full_name="Matching Candidate", professional_summary="Build Python APIs"),
        )
        target = job_workflows.create_job(
            db,
            actor,
            payload=JobCreate(title="Engineer", company="Example", skills=["Python", "Docker"]),
        )
        profile.update_profile(
            db,
            actor,
            ProfileUpdate(professional_summary="Build Python APIs using Docker"),
            expected=owner.profile_version,
        )
        current = profile.load_profile(db, actor).profile_version
        identity = target.id
        db.commit()
    barrier = Barrier(2)

    def read(_):
        with pg() as db:
            barrier.wait()
            result = refresh(db, actor.owner_id)
            db.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(read, [1, 2])) == [0, 1]
    with pg() as db:
        values = db.scalars(
            select(JobMatch).where(
                JobMatch.job_id == identity,
                JobMatch.profile_version == current,
                JobMatch.algorithm_version == ALGORITHM,
            )
        ).all()
        assert len(values) == 1 and values[0].score == 100
