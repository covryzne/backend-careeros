"""Restore a protected backup, verify both migration paths and PostgreSQL races.

Only generated careeros_test_* databases are created/dropped. The configured user
database is read-only here; migrations against it remain an explicit separate step.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings


def main():
    root = Path(__file__).resolve().parents[1]
    url = make_url(settings.database_url)
    backup_dir = Path(os.environ["LOCALAPPDATA"]) / "CareerOS" / "backups"
    backup = max(backup_dir.glob("before-agent-*.dump"), key=lambda path: path.stat().st_mtime)
    pg_bin = Path("C:/Program Files/PostgreSQL/17/bin")
    env = os.environ.copy()
    env.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
    )
    connection_args = {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
    }
    names = []
    with psycopg.connect(**connection_args, autocommit=True) as admin:
        try:
            for restored in (True, False):
                legacy_owner = None
                name = "careeros_test_" + uuid.uuid4().hex[:16]
                names.append(name)
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                child_env = {
                    **env,
                    "PGDATABASE": name,
                    "DATABASE_URL": url.set(database=name).render_as_string(hide_password=False),
                    "APP_ENV": "test",
                }
                if restored:
                    subprocess.run(
                        [
                            str(pg_bin / "pg_restore.exe"),
                            "--dbname",
                            name,
                            "--no-owner",
                            "--no-privileges",
                            "--exit-on-error",
                            str(backup),
                        ],
                        env=child_env,
                        check=True,
                        capture_output=True,
                    )
                    probe = create_engine(child_env["DATABASE_URL"])
                    with probe.begin() as conn:
                        if not conn.scalar(
                            text(
                                "SELECT count(*) FROM information_schema.columns WHERE table_name='candidate_documents' AND column_name='owner_id'"
                            )
                        ):
                            legacy_owner = uuid.uuid4()
                            legacy_profile = uuid.uuid4()
                            conn.execute(
                                text("INSERT INTO users (id) VALUES (:id)"), {"id": legacy_owner}
                            )
                            conn.execute(
                                text(
                                    "INSERT INTO candidate_profiles (id, owner_id, full_name, preferred_work_arrangement, profile_version) VALUES (:id, :owner, 'Legacy CV fixture', 'unknown', 1)"
                                ),
                                {"id": legacy_profile, "owner": legacy_owner},
                            )
                            for index in range(3):
                                conn.execute(
                                    text(
                                        "INSERT INTO candidate_documents (id,profile_id,name,original_file_name,storage_key,mime_type,file_size_bytes,checksum_sha256,deleted_at) VALUES (:id,:profile,'Legacy','legacy.pdf',:key,'application/pdf',8,:hash,:deleted)"
                                    ),
                                    {
                                        "id": uuid.uuid4(),
                                        "profile": legacy_profile,
                                        "key": uuid.uuid4().hex + ".pdf",
                                        "hash": str(index) * 64,
                                        "deleted": None if index < 2 else "2026-10-28",
                                    },
                                )
                    probe.dispose()
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    cwd=root,
                    env=child_env,
                    check=True,
                )
                engine = create_engine(child_env["DATABASE_URL"])
                with engine.connect() as conn:
                    if legacy_owner:
                        rows = conn.execute(
                            text(
                                "SELECT version_number, deleted_at IS NOT NULL FROM candidate_documents WHERE owner_id=:owner ORDER BY version_number"
                            ),
                            {"owner": legacy_owner},
                        ).all()
                        assert [row[0] for row in rows] == [1, 2, 3] and sum(
                            row[1] for row in rows
                        ) == 1
                    assert (
                        conn.scalar(text("SELECT version_num FROM alembic_version"))
                        == "0015_provider_settings"
                    )
                    assert (
                        conn.scalar(
                            text(
                                "SELECT count(*) FROM pg_constraint WHERE conname='uq_match_identity'"
                            )
                        )
                        == 1
                    )
                engine.dispose()
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/integration/test_postgres_foundation.py",
                        "-q",
                        "--tb=short",
                    ],
                    cwd=root,
                    env={**child_env, "TEST_DATABASE_URL": child_env["DATABASE_URL"]},
                    check=True,
                )
                print(
                    "PASS:",
                    "backup restore + upgrade" if restored else "fresh migrations through 0015",
                )
        finally:
            for name in names:
                assert (
                    name.startswith("careeros_test_") and name != url.database and len(name) == 30
                )
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


if __name__ == "__main__":
    main()
