from fastapi import APIRouter

from app.api.routes.applications import router as applications_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.profile import router as profile_router

api_router = APIRouter()
api_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(applications_router, prefix="/applications", tags=["applications"])
api_router.include_router(profile_router, prefix="/profile", tags=["profile"])
api_router.include_router(jobs_router, prefix="/jobs", tags=["jobs"])

from app.api.routes import agent_runs, content, reminders

api_router.include_router(reminders.router, prefix="/reminders", tags=["reminders"])
api_router.include_router(content.router, tags=["documents-and-drafts"])
api_router.include_router(agent_runs.router, tags=["agent"])

from app.api.routes.agent_settings import router as agent_settings_router

api_router.include_router(agent_settings_router, tags=["agent-settings"])

from app.api.routes.cv_imports import router as cv_imports_router

api_router.include_router(cv_imports_router, tags=["cv-imports"])

from app.api.routes.crawler import router as crawler_router

api_router.include_router(crawler_router, prefix="/crawler", tags=["crawler"])

from app.api.routes.local_auth import router as local_auth_router

api_router.include_router(local_auth_router, prefix="/local-auth", tags=["local-auth"])

from app.api.routes.auth_sessions import router as auth_sessions_router

api_router.include_router(auth_sessions_router, prefix="/auth-sessions", tags=["auth-sessions"])
