import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.session import database_health

app = FastAPI(
    title="CareerOS API",
    version="0.1.0",
    description="Career Workflow Manager backend",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
