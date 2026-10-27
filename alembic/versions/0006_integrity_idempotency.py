import sqlalchemy as sa

from alembic import op

revision = "0006_integrity_idempotency"
down_revision = "0005_unique_job_application"
branch_labels = None
depends_on = None


def upgrade():
    # Refuse destructive cleanup: conflicting existing data must be reviewed.
    for sql in [
        "SELECT 1 FROM candidate_skills GROUP BY profile_id,lower(trim(name)) HAVING count(*)>1",
        "SELECT 1 FROM job_sources GROUP BY owner_id,name HAVING count(*)>1",
        "SELECT 1 FROM job_skills GROUP BY job_id,name HAVING count(*)>1",
        "SELECT 1 FROM job_matches GROUP BY owner_id,job_id,profile_version,algorithm_version HAVING count(*)>1",
        "SELECT 1 FROM jobs WHERE source_id IS NOT NULL AND external_job_id IS NOT NULL GROUP BY owner_id,source_id,external_job_id HAVING count(*)>1",
    ]:
        if op.get_bind().execute(sa.text(sql)).first():
            raise RuntimeError(
                "Integrity preflight failed: duplicate records require manual review"
            )
    op.create_index(
        "uq_candidate_skill_normalized",
        "candidate_skills",
        ["profile_id", sa.text("lower(trim(name))")],
        unique=True,
    )
    op.create_unique_constraint("uq_job_source_owner_name", "job_sources", ["owner_id", "name"])
    op.create_unique_constraint("uq_job_skill_name", "job_skills", ["job_id", "name"])
    op.create_unique_constraint(
        "uq_job_external_identity", "jobs", ["owner_id", "source_id", "external_job_id"]
    )
    op.create_unique_constraint(
        "uq_match_identity",
        "job_matches",
        ["owner_id", "job_id", "profile_version", "algorithm_version"],
    )
    op.create_check_constraint("ck_match_score", "job_matches", "score >= 0 AND score <= 100")
    for table in ("jobs", "job_matches", "agent_runs", "audit_events", "reminders"):
        op.create_index("ix_" + table + "_owner_id", table, ["owner_id"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index(
        "ix_job_match_latest", "job_matches", ["owner_id", "job_id", "calculated_at", "id"]
    )
    op.execute(
        "\nCREATE TABLE idempotency_records (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\taction VARCHAR(160) NOT NULL, \n\tkey VARCHAR(200) NOT NULL, \n\tpayload_hash VARCHAR(64) NOT NULL, \n\tresult JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_idempotency_action UNIQUE (owner_id, action, key), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n"
    )


def downgrade():
    raise RuntimeError(
        "Use an application rollback; this data-preserving migration is forward-only"
    )
