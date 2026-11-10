# CareerOS Backend

For Google login with a local application database, use `.env.google.example` and
the [Google Auth setup guide](../careeros-frontend/GOOGLE_AUTH_SETUP.md). Configure
the same Supabase Auth project on both services; do not migrate local data or copy
Google secrets into backend settings.

Staging API now uses Supabase Auth user JWT verification. See [STAGING.md](STAGING.md) for setup, ownership, seeding and server deployment. Historical local-only rollout notes below do not supersede that staging authentication contract.

Manual Selenium crawling (Phase A) uses the **local PostgreSQL** database and a separate worker, with page/day/job limits configurable from Job Crawler. Follow [the Phase A contract and local runbook](../specs-careeros/CRAWLER_PHASE_A.md). Phase B adds the shared dispatcher and owner-scoped queue described below; CSV import remains separate. No second crawler HTTP service is needed.

FastAPI modular monolith with PostgreSQL persistence, shared business services and a disabled-by-default bounded agent foundation. Local/private rollout; production authentication is not yet configured and production startup fails closed.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.development.example .env.development
$env:APP_ENV_FILE = '.env.development'
```

Versioned `.env*.example` files contain placeholders only. Development templates
use local PostgreSQL and local accounts; `.env.google.example` supplies the
alternative Supabase Auth mode while keeping that database local. Set the same
auth mode on the frontend. Staging/production templates use their own HTTPS origin
and Auth/database placeholders. Fill private env files yourself; they remain
ignored. Never copy production credentials into an example. Production runtime
prerequisites are called out in its template; editing a template does not enable
unsupported worker/Compose behavior.

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

### Crawler portal availability

Set `CRAWLER_ENABLED_PORTALS=linkedin,jobstreet,glints,dealls,kalibrr,techinasia`
in the backend environment (`.env.development` locally, `.env.staging` on staging).
Restart/recreate the backend API after changing it. Names are normalized and deduplicated;
unknown names fail configuration validation. An explicitly empty value disables all new
API crawls. If omitted, the six portals above are enabled; Indeed is excluded by default.

The capabilities endpoint exposes the intersection of this policy and installed worker
adapters. New requests for disabled portals return 422. Existing runs, idempotent replays,
results and portal history remain accessible. Enabling `indeed` later does not configure
Chrome profiles or guarantee portal access. This policy does not remove CLI adapters and
does not require a frontend env variable or database migration.

### Staging crawler statistics producer

Crawler statistics are published by `app.crawler.stats`, separately from Selenium.
A running crawler worker does not publish analytics. Users created after the last
publication receive `CRAWLER_STATS_NOT_READY` until the next successful refresh;
existing job rows are unaffected. Statistics measure run outcomes, not current
saved/new/archived job collection size.

Staging Compose provides an opt-in `analytics` profile using the existing backend
image. In the backend `.env.staging`, set `CRAWLER_STATS_COVERAGE_START` to the
**existing publication coverage_start**. Do not change this boundary for an
existing publication. If there is no publication, explicitly select the start of
retained source history before initializing. Inspect these read-only queries on
the intended staging database first:

```sql
SELECT DISTINCT coverage_start, definition_version FROM crawler_stats_publications;
SELECT MIN((started_at AT TIME ZONE 'Asia/Jakarta')::date) AS first_retained_day
FROM crawl_runs WHERE started_at IS NOT NULL;
```

From the backend repository on the VM, after setting the coverage date:

```bash
docker compose -f compose.staging.yml --profile analytics up -d --build backend crawler-stats
docker compose -f compose.staging.yml logs --tail=50 crawler-stats
```

The service publishes immediately and every 300 seconds. Expect a `Published batch`
log with owner/bucket counts; then reload dashboard statistics. Browser refresh
alone cannot create a publication. Missing/invalid coverage prevents startup;
database errors retain the last publication and retry. A Compose profile does not
start with plain `up` unless explicitly enabled. Keep using `--profile analytics`
(or Compose's environment profile selection) when managing that service. Rollback:
stop `crawler-stats`; published data remains and becomes marked stale. No schema
migration or crawler scheduling change is introduced by this deployment wiring.

### Account consistency across tabs

`X-CareerOS-Account` is an optional UUID consistency header. The backend derives the
owner exclusively from the verified token; a different expected account returns
409 `ACCOUNT_SESSION_CHANGED` before reading or mutating user resources. The
header cannot select another owner or bypass authentication. Clients without it
retain the existing contract. The dashboard sends it after verifying its session
so an old tab cannot silently submit an account-A form using account-B cookies.

### Phase B: owner queue, fixed schedules and retry

Implemented on dev on 2026-11-08. Schedule choices are **05:00 and/or 17:00 WIB**,
one configuration per owner. Manual never pauses or advances a schedule. One
queued/running run per owner is enforced by a partial unique index. Other owners
can enqueue even while slots are occupied. Different-owner requests are FIFO;
an owner's additional manual submission returns its existing active run with a
durable idempotency receipt, keeping that run's original configuration.

Migration `0022_crawler_scheduling_queue` follows `0021_single_login_session`.
It adds schedules/occurrence receipts, request receipts and run attempts, replaces
the global queued/running index with per-owner and running-slot indexes, and
supports 1..8 execution slots. Default capacity remains 1. Each worker handles one
run at a time with an independent browser process. More replicas alone do not
increase the configured global capacity. Worker startup sets the shared capacity;
use the same `CRAWLER_MAX_CONCURRENCY` on all worker replicas. Shrinking below an
occupied slot is refused. Expired leases retain their browser slots until verified
cleanup/recovery; they are never automatically stolen. Tokens increase across
retry attempts even when a different execution slot is selected.

Before migration, drain queued/running work, verify browser cleanup, and stop old
workers plus API writers during the schema change. Migration refuses active runs
or retained slots. Back up the intended database using your normal procedure,
then run Alembic on that environment. Do not run old workers against the new queue.
The implementation was verified only on disposable local PostgreSQL; no staging
migration or deployment is performed by editing these files.

Native development, in the backend repo after applying the migration:

```powershell
$env:APP_ENV_FILE='.env.development'
python -m alembic upgrade head
$env:AGENT_WORKER_EMBEDDED='false'
$env:CRAWLER_MAX_CONCURRENCY='2'
python -m app.crawler.worker
# Run the same worker command in a second terminal with the same environment.
# Run dispatcher separately, continuously or once:
python -m app.crawler.dispatcher --once
python -m app.crawler.dispatcher
```

Staging after migration: the backend Compose profile `scheduler` starts a
lightweight dispatcher. It checks immediately and every 60 seconds; no Chrome or
LLM executes in it. Enable it explicitly:

```bash
docker compose -f compose.staging.yml --profile scheduler up -d --build backend crawler-dispatcher
docker compose -f compose.staging.yml logs --tail=30 crawler-dispatcher
```

Alternatively invoke `python -m app.crawler.dispatcher --once` from one Linux cron
every minute, inside the backend container or its configured virtual environment.
Do not create per-user crontabs. The dispatcher persists its heartbeat; capabilities
reports `scheduler_online` for a heartbeat within 150 seconds, and the UI warns
when an enabled schedule has no active dispatcher. Atomic owner/schedule locks and
unique occurrence receipts protect against competing dispatchers. Each occurrence
is enqueued, skipped_overlap, missed, or unavailable. If an occurrence is over two
minutes late at dispatcher invocation it is audited as missed, advancing directly
to the next future time. All due owners are drained in short batches within one
invocation. No historical backlog is replayed. Editing a schedule uses an expected
version and affects only future occurrences; existing queued work is unchanged.

To test two workers via crawler Compose, set BOTH values in the private backend
`.env.staging` passed to crawler Compose (not in a committed env file):

```dotenv
CRAWLER_MAX_CONCURRENCY=2
CRAWLER_WORKER_REPLICAS=2
```

The first is shared database execution capacity; the second starts independent
worker containers. Keep both at 1 for the initial VM rollout. All replicas must use
the same capacity. Each worker has its own 2 GiB memory / 1 CPU limit; these limits
apply per replica, not to the group. Then from the crawler repo on the VM:

```bash
docker compose --env-file ../backend-careeros/.env.staging -f compose.staging.yml up -d --build --force-recreate crawler-worker
docker compose --env-file ../backend-careeros/.env.staging -f compose.staging.yml ps
```

Use two independent browser profiles/accounts. A/B should run concurrently; C
queues until one finishes. Watch RAM/CPU, browser errors, API latency and portal
challenges before adopting capacity 2 operationally on a 2-vCPU VM. Keep default
1 until that live test passes. Indeed remains subject to enabled-portal policy;
shared Chrome profiles and new adapters are not introduced by this change.

### Production worker settings

The updated worker accepts `APP_ENV=production`. Deployed workers require
`ALLOW_LOCAL_IDENTITY=false`, `LOCAL_AUTH_ENABLED=false` and
`AGENT_WORKER_EMBEDDED=false`. It uses internal queue ownership rather than a
browser login; API Supabase JWT checks remain unchanged. Promote this backend
source through staging to main, then rebuild the crawler image, which embeds the
backend worker. Pulling source or rebuilding only the API does not update an
existing crawler image. Production dispatcher/stats and replica/browser-mode
Compose alignment remain separate deployment prerequisites.

### Staging promotion and startup order

Promote the matching backend, crawler and frontend changes from `dev` through
their PRs before rebuilding. Do not deploy the new queue API with an old worker
image: that worker can report a heartbeat while leaving new runs queued.

1. Enter maintenance: prevent new writes, stop the dispatcher, let queued/running
   work finish, verify browser cleanup, then stop workers, API and stats publisher.
   Do not force-reset slots to pass a migration. The migration refuses active work.
2. In the backend repo, build the new image and explicitly migrate the selected
   staging database. Startup never applies migrations automatically:

   ```bash
   docker compose -f compose.staging.yml build backend
   docker compose -f compose.staging.yml run --rm --no-deps backend python -m alembic upgrade head
   docker compose -f compose.staging.yml --profile scheduler up -d --force-recreate backend crawler-dispatcher
   ```

3. In the crawler repo, rebuild/recreate the workers using the command above. The
   image includes the backend worker source, so rebuilding the backend image alone
   does NOT update the crawler containers. Commands above assume the server sibling
   directory is `backend-careeros`; adjust the env path and `BACKEND_CONTEXT` if
   your checkout names differ. For this workspace it is `careeros-backend`.
4. If analytics was enabled, recreate its publisher with the same retained coverage
   date and database connection. Do not change coverage to today's date:

   ```bash
   docker compose -f compose.staging.yml --profile analytics up -d --force-recreate crawler-stats
   ```

5. In the frontend repo, rebuild/recreate the UI. No new frontend Compose variables
   or ports are needed for the schedule toggle/editor:

   ```bash
   docker compose -f compose.staging.yml up -d --build --force-recreate frontend
   ```

6. Verify dispatcher logs (`Dispatch checked`, even with zero due occurrences),
   worker readiness, then a manual crawl with scheduling Off. Save an automatic
   configuration and toggle On to test the next 05:00/17:00 WIB occurrence. With
   capacity/replicas 2, two independent accounts should run and a third should
   queue. Check both worker containers, run events, jobs ownership and VM resources.

The API, dispatcher and workers must point to the SAME environment database. Use
the configured Supabase direct/session connection; do not switch the statistics
publisher back to the transaction pooler. `APP_ENV_FILE` inside a host terminal is
not required inside these containers: Compose injects the selected env file. Its
default for staging is `.env.staging`. The `scheduler` profile is an explicit opt-in;
a plain `up -d backend` does not create a dispatcher on a fresh installation. Keep
one shared dispatcher; using this profile does not require a host cron entry.

API additions (existing auth and owner checks):

- GET/PUT `/api/v1/crawler/schedule`: owned schedule or null; PUT accepts `config`,
  `hours` (5 and/or 17), `enabled`, `expected_version` (0 to create). Stale writes 409.
- GET `/api/v1/crawler/schedule/occurrences`: latest 20 owned occurrence outcomes.
- POST `/api/v1/crawler/runs/{id}/retry`: required Idempotency-Key; 202 same run,
  new attempt only for failed/partial portals. Successful portals are not repeated.
- GET `/api/v1/crawler/runs/{id}/attempts`: attempt targets, times, errors and
  before/after source counters. Other owners receive 404 for run resources.

Retry keeps original config and date window, run ID and successful item receipts.
Failed/discovered/rejected items can be attempted again even when the discovery
cap was reached. Current portal availability is checked; disabled targets return
422. A different active run for the owner blocks retry with 409. New work uses
per-owner/global limits and FIFO order; scheduled times are never changed by retry.
Statistics retain one historical run, distinct item outcomes and saved job identity;
retry does not count old successes as new/duplicate again. Actual page visits can
increase with retries. Before/after snapshots preserve previous source errors.

Rollback: first stop dispatcher, drain/stop workers and confirm all browsers ended.
Export schedule/attempt metadata before an intentional downgrade: the downgrade
removes those new tables but preserves jobs, run history, items and events. New
queued/running work prevents downgrade. Rebuild/deploy all three repos together;
the private engine bridge now accepts known-ID and remaining-new-card budgets.

Verification for this increment: full isolated backend suite 308 passed (20 optional
integration cases skipped), plus 9 real local PostgreSQL queue/worker/dispatcher/retry
integration tests run separately. Migration upgrade/downgrade and active-queue refusal
were exercised on disposable databases. Frontend suite 49 passed (one opt-in Auth
simulation skipped), scoped type/lint checks and production build passed. Crawler
regressions: 85 passed. Parallel tests use fixture engine events, not live two-browser
Azure resource measurements. The original local/staging application databases were
not migrated by these checks. Live portal access, VM capacity 2 and deployment of the
cron/dispatcher remain operator verification steps.
