"""Preserve structured job content and removable versioned drafts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_job_details_drafts"
down_revision = "0013_cv_versions"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("requirements", "responsibilities"):
        op.add_column("jobs", sa.Column(name, sa.Text()))
    for name, length in (
        ("hr_contact_name", 200),
        ("hr_contact_email", 320),
        ("hr_contact_phone", 50),
    ):
        op.add_column("jobs", sa.Column(name, sa.String(length)))
    op.add_column("communication_drafts", sa.Column("job_snapshot", postgresql.JSONB()))
    op.add_column("communication_drafts", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    raise RuntimeError("Preserve job content and draft history; roll back application code instead")
