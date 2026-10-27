"""Store role-specific CV versions independently of optional parsing."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_cv_versions"
down_revision = "0012_cv_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("candidate_documents", sa.Column("owner_id", postgresql.UUID(as_uuid=True)))
    op.add_column("candidate_documents", sa.Column("target_role", sa.String(160)))
    op.add_column(
        "candidate_documents",
        sa.Column("role_key", sa.String(160), nullable=False, server_default=""),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("candidate_documents", sa.Column("matching_snapshot", postgresql.JSONB()))
    op.add_column(
        "candidate_documents",
        sa.Column("snapshot_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE candidate_documents d SET owner_id = p.owner_id FROM candidate_profiles p WHERE p.id = d.profile_id"
    )
    op.execute("""WITH numbered AS (
        SELECT id, row_number() OVER (PARTITION BY owner_id ORDER BY uploaded_at, id) AS n
        FROM candidate_documents
    ) UPDATE candidate_documents d SET version_number = numbered.n FROM numbered WHERE d.id = numbered.id""")
    op.alter_column("candidate_documents", "owner_id", nullable=False)
    op.alter_column("candidate_documents", "profile_id", nullable=True)
    op.create_foreign_key("fk_document_owner", "candidate_documents", "users", ["owner_id"], ["id"])
    op.create_index("ix_candidate_documents_owner_id", "candidate_documents", ["owner_id"])
    op.create_unique_constraint(
        "uq_document_role_version",
        "candidate_documents",
        ["owner_id", "role_key", "version_number"],
    )
    op.create_check_constraint("ck_document_version", "candidate_documents", "version_number >= 1")


def downgrade():
    raise RuntimeError("Preserve CV history; roll back application code instead")
