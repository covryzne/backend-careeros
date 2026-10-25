"""Keep newly ingested jobs separate from explicitly saved jobs."""

from alembic import op

revision = "0004_new_job_default"
down_revision = "0003_profile_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("jobs", "status", server_default="new")


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'interested' WHERE status = 'new'")
    op.alter_column("jobs", "status", server_default="saved")
