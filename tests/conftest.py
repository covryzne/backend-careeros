
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models.workflow import (
    Application,
    ApplicationActivity,
    AuditEvent,
    Job,
    JobMatch,
    JobSkill,
    JobSource,
    Reminder,
)


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(UUID, "sqlite")
def compile_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(32)"


@pytest.fixture
def jobs_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (JobSource, Job, JobSkill, JobMatch, Application, ApplicationActivity, AuditEvent, Reminder):
        model.__table__.create(engine)
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield client, db
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


