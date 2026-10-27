from alembic import op

revision = "0009_documents_drafts"
down_revision = "0008_agent_runtime"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE candidate_documents (\n\tid UUID NOT NULL, \n\tprofile_id UUID NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\toriginal_file_name VARCHAR(255) NOT NULL, \n\tstorage_key VARCHAR(80) NOT NULL, \n\tmime_type VARCHAR(100) NOT NULL, \n\tfile_size_bytes INTEGER NOT NULL, \n\tchecksum_sha256 VARCHAR(64) NOT NULL, \n\tuploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(profile_id) REFERENCES candidate_profiles (id), \n\tUNIQUE (storage_key)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE candidate_fact_evidence (\n\tid UUID NOT NULL, \n\tfact_id UUID NOT NULL, \n\tdocument_id UUID NOT NULL, \n\texcerpt TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_fact_document UNIQUE (fact_id, document_id), \n\tFOREIGN KEY(fact_id) REFERENCES candidate_facts (id) ON DELETE CASCADE, \n\tFOREIGN KEY(document_id) REFERENCES candidate_documents (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE communication_drafts (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tsource_profile_version INTEGER NOT NULL, \n\tdraft_type VARCHAR(40) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tcitations JSONB NOT NULL, \n\treview_flags JSONB NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tFOREIGN KEY(job_id) REFERENCES jobs (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE draft_versions (\n\tid UUID NOT NULL, \n\tdraft_id UUID NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcontent TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_draft_version UNIQUE (draft_id, version), \n\tFOREIGN KEY(draft_id) REFERENCES communication_drafts (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE profile_proposals (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tsource_profile_version INTEGER NOT NULL, \n\tchanges JSONB NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n"
    )
    op.create_foreign_key(
        "fk_application_selected_document",
        "applications",
        "candidate_documents",
        ["selected_document_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    raise RuntimeError("Preserve user artifacts; roll back application code instead")
