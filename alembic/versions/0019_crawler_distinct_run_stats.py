"""Store distinct daily runs for the crawler statistics read API."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0019_crawler_distinct_run_stats"
down_revision = "0018_crawler_daily_stats"
branch_labels = None
depends_on = None
COUNTERS = (
    "runs_started",
    "runs_running",
    "runs_completed",
    "runs_partial",
    "runs_failed",
    "runs_cancelled",
)


def upgrade():
    op.create_table(
        "crawler_daily_run_stats",
        sa.Column("owner_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("activity_date", sa.Date(), primary_key=True),
        sa.Column("batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in COUNTERS),
        sa.ForeignKeyConstraint(
            ["owner_id", "batch_id"],
            ["crawler_stats_publications.owner_id", "crawler_stats_publications.batch_id"],
            name="fk_crawler_run_stats_publication",
        ),
        *(
            sa.CheckConstraint(f"{name} >= 0", name=f"ck_crawler_run_stats_{name}")
            for name in COUNTERS
        ),
    )


def downgrade():
    raise RuntimeError("Preserve published statistics; roll back application code instead")
