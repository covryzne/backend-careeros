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
```

The current local PostgreSQL instance is expected on port `5434`. Replace the
username and password in `.env` with the credentials configured on your
machine; never commit that file.

To start the API exactly from the `app` directory:

```powershell
cd D:\Kerja\Gabut\job-tracker\backend-careeros\app
python main.py
```

The equivalent development command from the backend root, with auto-reload, is:

```powershell
cd D:\Kerja\Gabut\job-tracker\backend-careeros
uvicorn app.main:app --reload
```

The direct `python main.py` command runs at `http://127.0.0.1:8001`. The root endpoint returns a startup message, and OpenAPI documentation is available at `http://127.0.0.1:8001/docs`.

After the API and database are available, seed the real candidate profile
through the profile API. The script is idempotent and does not remove
unrelated records:

```powershell
python scripts\seed_real_profile.py
```

The seed stores claims as self-reported (`is_verified=false`) because no
evidence files were provided. The current profile schema does not yet expose
structured project-to-skill relations or document upload persistence; project
technologies are retained in project descriptions until that contract is added.

## Current scope

The initial foundation includes profile persistence, workflow database models, Alembic migrations, health checks, and profile read/update endpoints. Scraper execution, scheduling, RabbitMQ, authentication, and external apply automation remain deferred according to `specs-careeros/API_CONTRACT.md`.
