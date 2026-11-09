"""Development-only accounts and revocable local sessions; preserve existing users."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0020_local_auth"
down_revision = "0019_crawler_distinct_run_stats"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("local_accounts",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)))
    op.create_table("local_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("local_accounts.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_local_sessions_user_id", "local_sessions", ["user_id"])
    op.create_index("ix_local_sessions_expires_at", "local_sessions", ["expires_at"])


def downgrade():
    op.drop_table("local_sessions")
    op.drop_table("local_accounts")
