"""Owner queue, execution slots, fixed-WIB schedules and retry attempts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0022_crawler_scheduling_queue"
down_revision = "0021_single_login_session"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("LOCK TABLE crawl_runs, crawl_worker_slots IN ACCESS EXCLUSIVE MODE"))
    if conn.scalar(sa.text("SELECT count(*) FROM crawl_runs WHERE status IN ('queued','running')")):
        raise RuntimeError(
            "Drain queued/running crawls and stop old workers before Phase B migration"
        )
    if conn.scalar(
        sa.text("SELECT count(*) FROM crawl_worker_slots WHERE active_run_id IS NOT NULL")
    ):
        raise RuntimeError("Recover retained browser slots before Phase B migration")
    op.create_table(
        "crawl_schedules",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("config", pg.JSONB(), nullable=False),
        sa.Column("hours", pg.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_crawl_schedules_next_run_at", "crawl_schedules", ["next_run_at"])
    op.drop_index("uq_crawl_active_slot", "crawl_runs")
    op.drop_constraint("ck_crawl_execution_slot", "crawl_runs", type_="check")
    op.create_check_constraint(
        "ck_crawl_execution_slot", "crawl_runs", "execution_slot BETWEEN 1 AND 8"
    )
    op.create_index(
        "uq_crawl_running_slot",
        "crawl_runs",
        ["execution_slot"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_crawl_owner_active",
        "crawl_runs",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_foreign_key(
        "fk_crawl_schedule", "crawl_runs", "crawl_schedules", ["schedule_id"], ["id"]
    )
    op.add_column(
        "crawl_runs", sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "crawl_runs",
        sa.Column(
            "enqueued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    conn.execute(sa.text("UPDATE crawl_runs SET enqueued_at=queued_at"))
    op.drop_constraint("ck_crawl_singleton", "crawl_worker_slots", type_="check")
    op.create_check_constraint("ck_crawl_slot_range", "crawl_worker_slots", "id BETWEEN 1 AND 8")
    op.add_column(
        "crawl_worker_slots",
        sa.Column("capacity", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_crawl_capacity", "crawl_worker_slots", "capacity BETWEEN 1 AND 8"
    )
    op.create_index("uq_crawl_slot_run", "crawl_worker_slots", ["active_run_id"], unique=True)
    op.add_column(
        "crawl_worker_slots", sa.Column("dispatcher_heartbeat_at", sa.DateTime(timezone=True))
    )
    op.create_table(
        "crawl_run_attempts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("platforms", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("sources_before", pg.JSONB(), nullable=False),
        sa.Column("sources_after", pg.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "number", name="uq_crawl_attempt"),
    )
    op.create_index("ix_crawl_run_attempts_run_id", "crawl_run_attempts", ["run_id"])
    conn.execute(
        sa.text("""INSERT INTO crawl_run_attempts
        (id,run_id,number,kind,platforms,status,queued_at,started_at,finished_at,error,sources_before,sources_after)
        SELECT id,id,1,'initial',COALESCE(config->'platforms','[]'::jsonb),status,queued_at,started_at,finished_at,error,'{}'::jsonb,'{}'::jsonb FROM crawl_runs""")
    )
    op.create_table(
        "crawl_request_receipts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id"), nullable=False),
        sa.UniqueConstraint("owner_id", "action", "key", name="uq_crawl_receipt"),
    )
    op.create_table(
        "crawl_schedule_occurrences",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schedule_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("crawl_schedules.id"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("crawl_runs.id")),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_crawl_occurrence"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM crawl_runs WHERE status IN ('queued','running')")
    ):
        raise RuntimeError("Drain executions before downgrading crawler queue")
    op.drop_table("crawl_schedule_occurrences")
    op.drop_table("crawl_request_receipts")
    op.drop_table("crawl_run_attempts")
    op.drop_constraint("fk_crawl_schedule", "crawl_runs", type_="foreignkey")
    op.execute("UPDATE crawl_runs SET schedule_id=NULL, execution_slot=1")
    op.drop_column("crawl_runs", "attempt_no")
    op.drop_column("crawl_runs", "enqueued_at")
    op.drop_index("uq_crawl_owner_active", "crawl_runs")
    op.drop_index("uq_crawl_running_slot", "crawl_runs")
    op.drop_constraint("ck_crawl_execution_slot", "crawl_runs", type_="check")
    op.create_check_constraint("ck_crawl_execution_slot", "crawl_runs", "execution_slot=1")
    op.create_index(
        "uq_crawl_active_slot",
        "crawl_runs",
        ["execution_slot"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.execute("DELETE FROM crawl_worker_slots WHERE id<>1")
    op.drop_index("uq_crawl_slot_run", "crawl_worker_slots")
    op.drop_constraint("ck_crawl_capacity", "crawl_worker_slots", type_="check")
    op.drop_column("crawl_worker_slots", "capacity")
    op.drop_column("crawl_worker_slots", "dispatcher_heartbeat_at")
    op.drop_constraint("ck_crawl_slot_range", "crawl_worker_slots", type_="check")
    op.create_check_constraint("ck_crawl_singleton", "crawl_worker_slots", "id=1")
    op.drop_table("crawl_schedules")
