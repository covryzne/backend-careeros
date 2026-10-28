# CareerOS Backend

FastAPI modular monolith with PostgreSQL persistence, shared business services and a disabled-by-default bounded agent foundation. Local/private rollout; production authentication is not yet configured and production startup fails closed.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Configure `DATABASE_URL` locally. Before upgrading existing data, follow the backup/restore verification procedure in [the foundation runbook](../specs-careeros/FOUNDATION_IMPLEMENTATION.md). No reseed is needed.

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Health: `/health`; interactive schema: `/docs`; generated contract: `/openapi.json`.

## Local seed data

From `backend-careeros`, with the virtual environment active and `DATABASE_URL`
configured in `.env`, run:

```powershell
python -m alembic upgrade head
python seed_real_profile.py
python seed_jobs.py
```

The API server does not need to be running. Both scripts use the same local owner
and shared services as the application. The original entry points
`python scripts/seed_real_profile.py` and `python scripts/seed_jobs.py` also work.

The jobs seeder creates 15 complete demo jobs: 6 new, 6 saved, and 3 archived on a
fresh database. It includes description, requirements, responsibilities/job desk,
experience requirements, preferred qualifications, HR name/email/phone, skills,
salary, work arrangement, employment type, source, URLs, posting/deadline dates,
discovery time and notes. Example URLs/emails and reserved example phone numbers
are demonstration contacts; these are not live vacancies or actual employer pay.
The original three seed identities are retained for compatibility.

Rerunning does not create duplicate jobs/skills/profile children. It fills missing
seed job fields, restores soft-deleted seed jobs with their original IDs, and
preserves active job statuses, edited non-empty content, unrelated jobs and
application history. Existing jobs beyond these 15 are retained. If run before a
profile exists, jobs are still seeded. Matching now updates automatically from the
latest saved professional profile when jobs/Home/pipeline are read. The seeder and
application share `professional-overlap-v1`: a transparent skill-alignment
indicator based on professional-source keyword overlap. It is not a hiring
probability or proof that every qualification is met. See
[CURRENT_PROFILE_MATCHING.md](../specs-careeros/CURRENT_PROFILE_MATCHING.md).

The profile seeder fills missing profile/preferences and merges the existing real
seed skills, experiences and projects. It creates no Verified Facts or artificial
PDFs. It preserves populated personal/professional fields by default. To explicitly
replace seed-related populated fields with this file's values, use
`python seed_real_profile.py --overwrite`; unrelated child records remain intact.
Unknown birth/salary fields remain unset. Scripts run transactionally against the
configured development/test database and never print private profile/contact data.

```powershell
python -m pytest -q
python -m ruff check app tests scripts/verify_foundation.py scripts/export_openapi.py
python scripts/verify_foundation.py
python scripts/export_openapi.py openapi.local.json
```

The PostgreSQL verification script needs a protected backup and database creation permissions; it operates on guarded disposable databases. Normal unit tests use isolated test storage and skip the PostgreSQL race test unless a dedicated test URL is supplied.

## Features

Owner-scoped Jobs/Applications/Dashboard, partial profile/child editing and versioning, idempotent commands, reminders, private PDF documents, owner-reviewed fact grounding, versioned cited drafts, durable agent runs and immutable approvals. HTTP and typed agent tools call the same services. Existing tracker behavior remains compatible.

The agent and both write flags default to false. Configuration alone does not start a worker. After the runbook's read-only activation checks, run `python -m app.agent.worker` separately. No external sending/submission/destructive tools are registered. Keep server credentials out of frontend configuration.

Private PDFs are stored under ignored `.private-documents/` by default; back this directory up with the database once documents exist. Legacy `is_verified` is retained for compatibility but never authorizes grounded draft claims.

## On-demand career assistant

The Copilot workspace supports prompted job analysis/comparison, pipeline review, and career next steps through the `career_assistant` task. It uses bounded shared-service context and strict cited reports, with source selection and searchable history. See [the assistant contract and verification](../specs-careeros/CAREER_ASSISTANT.md). No additional database migration is needed. Crawler scheduling remains separate, and no real provider configuration or feature flag is enabled automatically.

## Agent switches in Settings

Provider base URL, model and API key can now be configured from Pengaturan →
Provider AI after migration `0015_provider_settings`. The key field stays masked;
leave it empty to keep a saved key. Keys are encrypted in the owner's database row
and never returned by the API. Local development creates the master key privately
at `DOCUMENT_STORAGE_DIR/.provider-key`; back up this file separately with the
private storage directory. Production uses backend-only `PROVIDER_ENCRYPTION_KEY`
(a Fernet key). Keep this master key stable across API/worker processes and restarts.
Existing LLM environment settings remain a fallback. See
[PROVIDER_SETTINGS.md](../specs-careeros/PROVIDER_SETTINGS.md) for the contract,
privacy boundary and three Copilot prompt templates.

Apply additive migration `0011_agent_preferences` before starting this version. Owners can then save agent enable/write/approval preferences and execution limits from Pengaturan; no backend restart is needed. Local development starts an idle embedded worker by default. Set `AGENT_WORKER_EMBEDDED=false` for a separate worker, or `AGENT_RUNTIME_ALLOWED=false` to enforce a server-level shutdown. Provider secrets still belong in backend configuration. Matching reads professional profile data directly, while personal fields are omitted/filtered. See [AGENT_SETTINGS.md](../specs-careeros/AGENT_SETTINGS.md).

## CV import

Install the updated requirements (pypdf is required), then apply additive migration 0012_cv_imports. The profile CV (PDF) menu parses text PDFs locally into editable fields and merges them on save. No provider is needed for parsing. Scans/OCR and password-protected files are not supported. Profile data feeds matching/drafts directly; the Verified Facts user feature has been retired. See [CV_IMPORT.md](../specs-careeros/CV_IMPORT.md).
