"""Real PostgreSQL row locking and unique-index enforcement across workers."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.auth_session import AuthSession
from app.services import auth_sessions

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Dedicated PostgreSQL test database required"
)


@pytest.mark.parametrize("takeover", [False, True])
def test_concurrent_logins_keep_one_active_session(takeover):
    url = os.environ["TEST_DATABASE_URL"]
    assert make_url(url).database.startswith("careeros_test_")
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner = uuid.uuid4()
    if takeover:
        with sessions() as db:
            auth_sessions.claim(db, owner, "original_" + uuid.uuid4().hex)
    barrier = Barrier(2)

    def login():
        key = uuid.uuid4().hex
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                auth_sessions.claim(db, owner, key, takeover=takeover)
                return 200, key
            except HTTPException as exc:
                db.rollback()
                return exc.status_code, key

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: login(), range(2)))
        assert sorted(status for status, _ in results) == ([200, 200] if takeover else [200, 409])
        with sessions() as db:
            active = db.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == owner, AuthSession.revoked_at.is_(None)
                )
            ).all()
            assert len(active) == 1
            assert active[0].session_key in [key for status, key in results if status == 200]
            if takeover:
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(AuthSession)
                        .where(
                            AuthSession.user_id == owner,
                            AuthSession.revocation_reason == "replaced",
                        )
                    )
                    == 2
                )
    finally:
        engine.dispose()
