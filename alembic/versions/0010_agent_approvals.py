from alembic import op

revision = "0010_agent_approvals"
down_revision = "0009_documents_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE agent_approvals (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\ttool_name VARCHAR(80) NOT NULL, \n\targuments JSONB NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\tresource_version INTEGER NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tresult JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_run_approval_hash UNIQUE (run_id, content_hash), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tFOREIGN KEY(run_id) REFERENCES agent_runs (id)\n)\n\n"
    )


def downgrade():
    raise RuntimeError("Preserve user artifacts; roll back application code instead")
