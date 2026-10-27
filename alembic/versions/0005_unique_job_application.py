"""Prevent duplicate applications for the same owner and job."""

from alembic import op

revision = "0005_unique_job_application"
down_revision = "0004_new_job_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_applications_owner_job", "applications", ["owner_id", "job_id"])


def downgrade() -> None:
    op.drop_constraint("uq_applications_owner_job", "applications", type_="unique")
