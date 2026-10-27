import sqlalchemy as sa

from alembic import op

revision = "0007_profile_grounding"
down_revision = "0006_integrity_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    for table in (
        "candidate_skills",
        "candidate_experiences",
        "candidate_projects",
        "candidate_facts",
    ):
        op.add_column(table, sa.Column("source_type", sa.String(40), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "confirmation_status", sa.String(30), nullable=False, server_default="unconfirmed"
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "evidence_status", sa.String(30), nullable=False, server_default="unassessed"
            ),
        )
        op.add_column(table, sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
        op.create_check_constraint(
            "ck_" + table + "_confirmation",
            table,
            "confirmation_status IN ('unconfirmed','user_confirmed','rejected')",
        )
        op.create_check_constraint(
            "ck_" + table + "_evidence",
            table,
            "evidence_status IN ('unassessed','supported','unsupported')",
        )


def downgrade():
    raise RuntimeError("Grounding and legacy values must be preserved; use an application rollback")
