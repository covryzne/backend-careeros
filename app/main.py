import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.limits import RequestBodyLimit
from app.db.session import database_health


@asynccontextmanager
async def lifespan(_app):
    if settings.app_env not in {"development", "test"}:
        raise RuntimeError(
            "Production authentication is not configured; local identity cannot be used for deployment"
        )
    task = None
    if (
        settings.app_env == "development"
        and settings.agent_runtime_allowed
        and settings.agent_worker_embedded
    ):
        from app.agent.worker import main as run_worker

        task = asyncio.create_task(run_worker())
    _app.state.agent_worker = task
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    lifespan=lifespan,
    title="CareerOS API",
    version="0.1.0",
    description="Career Workflow Manager backend",
)

from app.core.errors import install

install(app)

app.add_middleware(RequestBodyLimit)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Profile-Version", "X-Request-ID"],
)


app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return {
        "service": "backend-careeros",
        "message": "CareerOS API is running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "backend-careeros",
        "version": app.version,
        "database": database_health(),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
