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


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "backend-careeros",
        "version": app.version,
        "database": database_health(),
    }

