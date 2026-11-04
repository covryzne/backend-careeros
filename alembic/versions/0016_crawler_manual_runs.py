"""Manual crawler queue. No scheduler or destructive data backfill."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0016_crawler_manual_runs"
down_revision = "0015_provider_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("normalized_url_hash", sa.String(64), nullable=True))
    # Legacy rows remain intact. They are reconciled under owner locks on ingestion.
    op.create_unique_constraint(
        "uq_job_normalized_url", "jobs", ["owner_id", "normalized_url_hash"]
    )
    op.create_table(
        "crawl_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("trigger_type", sa.String(20), nullable=False),
        sa.Column("schedule_id", pg.UUID(as_uuid=True)),
        sa.Column("execution_slot", sa.Integer(), nullable=False),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *[
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("started_at", "finished_at", "heartbeat_at", "lease_until")
        ],
        sa.Column("worker_id", sa.String(100)),
        sa.Column("process_id", sa.Integer()),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_crawl_request"),
        sa.CheckConstraint("execution_slot = 1", name="ck_crawl_execution_slot"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','partial','failed','cancelled')",
            name="ck_crawl_status",
        ),
        sa.CheckConstraint("trigger_type IN ('manual','scheduled')", name="ck_crawl_trigger"),
    )
    op.create_index("ix_crawl_runs_owner_id", "crawl_runs", ["owner_id"])
    op.create_index(
        "uq_crawl_active_slot",
        "crawl_runs",
        ["execution_slot"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "crawl_run_sources",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *[
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "jobs_found",
                "jobs_new",
                "jobs_duplicate",
                "jobs_rejected",
                "detail_attempts",
                "pages_completed",
            )
        ],
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "platform", name="uq_crawl_source"),
    )
    op.create_index("ix_crawl_run_sources_run_id", "crawl_run_sources", ["run_id"])
    op.create_table(
        "crawl_run_items",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("identity_key", sa.String(64), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("error", sa.Text()),
        sa.Column("provenance", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("run_id", "platform", "identity_key", name="uq_crawl_item"),
    )
    op.create_index("ix_crawl_run_items_run_id", "crawl_run_items", ["run_id"])
    op.create_table(
        "crawl_worker_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_run_id", pg.UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id")),
        sa.Column("worker_id", sa.String(100)),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("capabilities", pg.JSONB(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_crawl_singleton"),
    )
    op.execute(
        "INSERT INTO crawl_worker_slots (id, fencing_token, capabilities) VALUES (1, 0, '[]'::jsonb)"
    )


def downgrade():
    raise RuntimeError("Preserve crawler history and jobs; roll back application code instead")
