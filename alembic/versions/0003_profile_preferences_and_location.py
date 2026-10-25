"""add profile location and matching preferences

Revision ID: 0003_profile_prefs
Revises: 0002_initial_workflow_tables
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_profile_prefs"
down_revision = "0002_initial_workflow_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column("candidate_profiles", sa.Column("location", sa.String(240), nullable=True))
    op.create_table(
        "candidate_preferences",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "profile_id",
            uuid,
            sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preference_type", sa.String(40), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.UniqueConstraint("profile_id", "preference_type", "value"),
    )


def downgrade() -> None:
    op.drop_table("candidate_preferences")
    op.drop_column("candidate_profiles", "location")
