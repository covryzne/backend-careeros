# CareerOS Backend

FastAPI modular monolith for the Career Workflow Manager.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `.env`, then apply migrations:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`. OpenAPI documentation is available at `/docs`.

## Current scope

The initial foundation includes profile persistence, workflow database models, Alembic migrations, health checks, and profile read/update endpoints. Scraper execution, scheduling, RabbitMQ, authentication, and external apply automation remain deferred according to `specs-careeros/API_CONTRACT.md`.

