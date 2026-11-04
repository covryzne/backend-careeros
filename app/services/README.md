# Backend services

Services are grouped by feature. HTTP routes, agent tools and workers call these
modules; this layer owns business operations and database-backed workflows.

| Package | Modules and responsibility |
| --- | --- |
| `crawler/` | `runs`, `ingestion`, `dates`, `events`, `stats`, `stats_read`: crawl queue/lifecycle, ingestion, date rules, progress and published statistics |
| `agent/` | `facade`, `runs`, `settings`, `approvals`, `privacy`, `career`: tool dispatch, agent execution, policies, approvals and career reports |
| `jobs/` | `workflows`, `summaries`, `content`, `identity`, `matching`, `skills`: job operations, response summaries, descriptions, deduplication and matching |
| `applications/` | `workflows`, `transitions`: application operations, stage rules and activity |
| `profile/` | `service`, `sources`: candidate profile operations and professional evidence |
| `documents/` | `service`, `imports`, `parser`, `matching`: private documents, staged CV import, isolated PDF parsing and CV snapshots |
| `drafts/` | `service`, `composer`: grounded draft lifecycle and composition |
| `dashboard/` | `service`: dashboard aggregates and previews |
| `reminders/` | `service`: reminder lifecycle |
| `providers/` | `settings`: encrypted provider settings and configuration resolution |
| `shared/` | `audit`, `idempotency`: audit records, owner locks and replay-safe commands |

## Imports and maintenance

Import the concrete module that owns an operation:

```python
from app.services.crawler import runs as crawler_runs
from app.services.jobs import workflows as job_workflows
from app.services.profile import service as profile
from app.services.shared.idempotency import command
```

Package `__init__.py` files deliberately avoid eager exports. This keeps importing
one feature from initializing unrelated services and reduces circular-import risk.
Keep cross-feature dependencies explicit; preserve deferred imports where a
workflow needs them to avoid an existing dependency cycle. Shared helpers should
remain independent of feature services.

Add crawler behavior to `crawler/`, job behavior to `jobs/`, and so on. The root
contains package documentation rather than a second set of forwarding modules.
These service paths are internal Python imports, not HTTP API paths.

Process entry points stay unchanged:

```text
python -m app.crawler.worker
python -m app.crawler.stats --coverage-start YYYY-MM-DD
python -m app.agent.worker
```

The CV parser subprocess now runs `python -m app.services.documents.parser`;
`documents/imports.py` invokes it with the same input, timeout and error handling.
No database migration, API payload, feature flag or business rule changes are
required by this directory refactor. Restart existing API/worker processes after
updating the checkout so they load the new module paths; let active work finish
before restarting its worker.
