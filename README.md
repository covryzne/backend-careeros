# CareerOS Backend

For Google login with a local application database, use `.env.google.example` and
the [Google Auth setup guide](../careeros-frontend/GOOGLE_AUTH_SETUP.md). Configure
the same Supabase Auth project on both services; do not migrate local data or copy
Google secrets into backend settings.

Staging API now uses Supabase Auth user JWT verification. See [STAGING.md](STAGING.md) for setup, ownership, seeding and server deployment. Historical local-only rollout notes below do not supersede that staging authentication contract.

Manual Selenium crawling (Phase A) uses the **local PostgreSQL** database and a separate worker, with page/day/job limits configurable from Job Crawler. Follow [the Phase A contract and local runbook](../specs-careeros/CRAWLER_PHASE_A.md). Apply Alembic head, start the existing API, and run `.venv/Scripts/python.exe -m app.crawler.worker` in another terminal. Scheduling and CSV import are deferred. No second crawler HTTP service is needed.

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

### Manual development with an environment-specific file

Activate the existing conda/venv environment and install this repo's requirements.
Fill the ignored `.env.development` at the backend repo root using
`.env.development.example` as a template; keep PostgreSQL on the laptop. Native
Python uses `localhost` for that database, not `host.docker.internal`.

From the backend repo:

```powershell
python app/main.py --env-file .env.development
```

From the `app` directory, `python main.py --env-file .env.development` also works:
relative env paths resolve against the backend repo, not the terminal directory.
With no flag or `APP_ENV_FILE`, the existing root `.env` remains the default.
The application does not guess an environment from the Git branch or merge files
from multiple environments. Process environment values override file values.

For Alembic, Uvicorn or the standalone worker, select the same file through the
process environment (commands below run from the backend repo):

```powershell
$env:APP_ENV_FILE = '.env.development'
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

For Windows CMD, use `set "APP_ENV_FILE=.env.development"` instead of the
PowerShell assignment. Run `python -m app.crawler.worker` in a separate terminal
with the same file selected and `AGENT_WORKER_EMBEDDED=false` for that worker.

Docker staging still starts `uvicorn app.main:app --host 0.0.0.0 --port 8001` and
receives configuration through Compose `env_file`/`environment`. Compose's host
variable `APP_ENV_FILE` selects that file; it is not automatically injected into
the container. Do not add `APP_ENV_FILE` inside the private env file unless that
path also exists inside the container. No development file is auto-loaded in Docker.

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

Business services are grouped by feature under `app/services/`. See the
[service structure and import guide](app/services/README.md) for responsibilities,
module paths and worker entry points.

Crawler activity summaries have a separate backfill/refresh process after migration
`0018_crawler_daily_stats`. It publishes daily owner/platform counters every five
minutes when run, without changing crawl execution. See the
[analytics runbook](../specs/ANALYTICS_TRENDS_IMPLEMENTATION.md) for coverage selection,
commands and verification. Migration `0019_crawler_distinct_run_stats` adds distinct
run counts for `GET /api/v1/crawler/stats` and the Statistik Hasil Crawl submenu
under Job Scraper. Refresh publications after upgrading to definition version 2.

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
