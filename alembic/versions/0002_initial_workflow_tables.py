"""create core workflow tables

Revision ID: 0002_initial_workflow_tables
Revises: 0001_initial_candidate_profile
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_initial_workflow_tables"
down_revision = "0001_initial_candidate_profile"
branch_labels = None
depends_on = None


def _uuid():
    return postgresql.UUID(as_uuid=True)


def _table(name: str, *columns: sa.Column) -> None:
    op.create_table(name, sa.Column("id", _uuid(), primary_key=True), *columns)


def upgrade() -> None:
    uuid = _uuid()
    op.create_table(
        "job_sources",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("base_url", sa.Text()),
    )
    op.create_table(
        "jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", uuid, sa.ForeignKey("job_sources.id", ondelete="SET NULL")),
        sa.Column("external_job_id", sa.String(255)),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("company", sa.String(240), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("original_url", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.String(240)),
        sa.Column("work_arrangement", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("employment_type", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("salary_text", sa.String(200)),
        sa.Column("salary_min", sa.Numeric(14, 2)),
        sa.Column("salary_max", sa.Numeric(14, 2)),
        sa.Column("salary_currency", sa.String(3)),
        sa.Column("experience_requirements", sa.Text()),
        sa.Column("preferred_qualifications", sa.Text()),
        sa.Column("posting_date", sa.Date()),
        sa.Column("application_deadline", sa.Date()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="saved"),
        sa.Column("notes", sa.Text()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "job_skills",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "job_matches",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", uuid, sa.ForeignKey("candidate_profiles.id"), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("matching_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("missing_requirements", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("blockers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "applications",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="preparing"),
        sa.Column("application_date", sa.Date()),
        sa.Column("application_channel", sa.String(30)),
        sa.Column("selected_document_id", uuid),
        sa.Column("notes", sa.Text()),
        sa.Column("follow_up_date", sa.Date()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "application_activities",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", uuid, sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="user"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "reminders",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", uuid, sa.ForeignKey("applications.id", ondelete="CASCADE")),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(60), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("scope", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_tool_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("agent_run_id", uuid, sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output", postgresql.JSONB()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column("actor_id", uuid),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", uuid),
        sa.Column("before_data", postgresql.JSONB()),
        sa.Column("after_data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in (
        "audit_events",
        "agent_tool_events",
        "agent_runs",
        "reminders",
        "application_activities",
        "applications",
        "job_matches",
        "job_skills",
        "jobs",
        "job_sources",
    ):
        op.drop_table(table)

