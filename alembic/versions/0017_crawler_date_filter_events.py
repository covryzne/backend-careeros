"""Persist crawler progress logs and date-filter counters; preserve historical runs/jobs."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0017_crawler_date_filter_events"
down_revision = "0016_crawler_manual_runs"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("jobs_filtered", "jobs_date_unknown"):
        op.add_column(
            "crawl_run_sources", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "crawl_run_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("platform", sa.String(40)),
        sa.Column("message", sa.String(600), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_crawl_event_sequence"),
    )
    op.create_index("ix_crawl_run_events_run_id", "crawl_run_events", ["run_id"])


def downgrade():
    raise RuntimeError("Preserve crawler history; roll back application code instead")
