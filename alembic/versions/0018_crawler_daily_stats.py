"""Add published crawler activity summaries; do not rewrite crawler or job history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0018_crawler_daily_stats"
down_revision = "0017_crawler_date_filter_events"
branch_labels = None
depends_on = None

COUNTERS = (
    "runs_started",
    "runs_running",
    "runs_completed",
    "runs_partial",
    "runs_failed",
    "runs_cancelled",
    "jobs_found",
    "jobs_new",
    "jobs_duplicate",
    "jobs_rejected",
    "jobs_filtered",
    "jobs_date_unknown",
    "detail_attempts",
    "pages_completed",
)


def upgrade():
    op.create_table(
        "crawler_stats_publications",
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(40), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "batch_id", name="uq_crawler_stats_owner_batch"),
    )
    op.create_table(
        "crawler_daily_stats",
        sa.Column("owner_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("activity_date", sa.Date(), primary_key=True),
        sa.Column("platform", sa.String(40), primary_key=True),
        sa.Column("batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in COUNTERS),
        sa.ForeignKeyConstraint(
            ["owner_id", "batch_id"],
            ["crawler_stats_publications.owner_id", "crawler_stats_publications.batch_id"],
            name="fk_crawler_stats_publication",
        ),
        *(sa.CheckConstraint(f"{name} >= 0", name=f"ck_crawler_stats_{name}") for name in COUNTERS),
    )


def downgrade():
    raise RuntimeError("Preserve published statistics; roll back application code instead")
