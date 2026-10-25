from fastapi import APIRouter

from app.api.routes.jobs import router as jobs_router
from app.api.routes.profile import router as profile_router

api_router = APIRouter()
api_router.include_router(profile_router, prefix="/profile", tags=["profile"])
api_router.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
