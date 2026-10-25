"""create candidate profile tables

Revision ID: 0001_initial_candidate_profile
Revises:
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_candidate_profile"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "candidate_profiles",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(50)),
        sa.Column("place_of_birth", sa.String(120)),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("headline", sa.String(240)),
        sa.Column("professional_summary", sa.Text()),
        sa.Column("preferred_work_arrangement", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("preferred_salary_min", sa.Numeric(14, 2)),
        sa.Column("preferred_salary_max", sa.Numeric(14, 2)),
        sa.Column("salary_currency", sa.String(3)),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for table, columns in {
        "candidate_skills": [
            sa.Column("profile_id", uuid, sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("proficiency", sa.String(40)),
            sa.Column("years_experience", sa.Numeric(4, 1)),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        ],
        "candidate_experiences": [
            sa.Column("profile_id", uuid, sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(200), nullable=False),
            sa.Column("company", sa.String(200), nullable=False),
            sa.Column("start_date", sa.Date()),
            sa.Column("end_date", sa.Date()),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("description", sa.Text()),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("evidence_reference", sa.Text()),
        ],
        "candidate_projects": [
            sa.Column("profile_id", uuid, sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("role", sa.String(160)),
            sa.Column("description", sa.Text()),
            sa.Column("url", sa.Text()),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        ],
        "candidate_facts": [
            sa.Column("profile_id", uuid, sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category", sa.String(60), nullable=False),
            sa.Column("claim", sa.Text(), nullable=False),
            sa.Column("evidence_reference", sa.Text()),
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
        ],
    }.items():
        op.create_table(
            table,
            sa.Column("id", uuid, primary_key=True),
            *columns,
        )


def downgrade() -> None:
    for table in ("candidate_facts", "candidate_projects", "candidate_experiences", "candidate_skills", "candidate_profiles", "users"):
        op.drop_table(table)
