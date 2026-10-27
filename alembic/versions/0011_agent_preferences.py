"""Persist owner-managed agent preferences without modifying existing records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_agent_preferences"
down_revision = "0010_agent_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_preferences",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("internal_writes_enabled", sa.Boolean(), nullable=False),
        sa.Column("approvals_enabled", sa.Boolean(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("max_tool_calls BETWEEN 1 AND 12", name="ck_agent_pref_calls"),
        sa.CheckConstraint("max_turns BETWEEN 1 AND 8", name="ck_agent_pref_turns"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 90", name="ck_agent_pref_timeout"),
    )


def downgrade():
    raise RuntimeError("Preserve owner preferences; roll back application code instead")
