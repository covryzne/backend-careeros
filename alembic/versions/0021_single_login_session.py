"""Six-hour login leases and one active session per account."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0021_single_login_session"
down_revision = "0020_local_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_sessions",
        sa.Column("session_key", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.String(30)),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    # Preserve local sessions, shorten the original eight-hour lease to six hours,
    # and keep only the most recently issued session active for each account.
    op.execute("""
        INSERT INTO auth_sessions
            (session_key, user_id, created_at, expires_at, revoked_at, revocation_reason)
        SELECT token_hash, user_id, expires_at - INTERVAL '8 hours',
            expires_at - INTERVAL '2 hours',
            CASE WHEN position > 1 OR expires_at - INTERVAL '2 hours' <= CURRENT_TIMESTAMP
                 THEN CURRENT_TIMESTAMP ELSE NULL END,
            CASE WHEN expires_at - INTERVAL '2 hours' <= CURRENT_TIMESTAMP THEN 'expired'
                 WHEN position > 1 THEN 'replaced' ELSE NULL END
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY user_id ORDER BY expires_at DESC, token_hash DESC
            ) AS position FROM local_sessions
        ) AS previous_sessions
    """)
    op.create_index(
        "uq_auth_sessions_active_user",
        "auth_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade():
    # Do not restore legacy tokens: that would revive sessions revoked since upgrade.
    op.execute("DELETE FROM local_sessions")
    op.drop_table("auth_sessions")
