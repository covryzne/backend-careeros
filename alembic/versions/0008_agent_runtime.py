import sqlalchemy as sa

from alembic import op

revision = "0008_agent_runtime"
down_revision = "0007_profile_grounding"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_runs",
        sa.Column("model_turn_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_unique_constraint(
        "uq_agent_event_sequence", "agent_tool_events", ["agent_run_id", "sequence_number"]
    )
    op.create_index("ix_agent_run_claim", "agent_runs", ["status", "created_at"])


def downgrade():
    raise RuntimeError("Run history must be preserved; use an application rollback")
