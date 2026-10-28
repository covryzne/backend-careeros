"""Store owner-scoped encrypted provider credentials."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_provider_settings"
down_revision = "0014_job_details_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_settings",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("base_url", sa.String(2000), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    raise RuntimeError(
        "Preserve encrypted provider configuration; roll back application code instead"
    )
