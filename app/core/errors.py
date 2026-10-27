import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException


def error_response(request, status, detail, code=None):
    codes = {
        400: "INVALID_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        428: "VERSION_REQUIRED",
        429: "RATE_LIMITED",
        503: "DEPENDENCY_UNAVAILABLE",
    }
    message = (
        detail.get("message", str(detail))
        if isinstance(detail, dict)
        else detail
        if isinstance(detail, str)
        else "Validation failed"
    )
    payload = {
        "detail": detail,
        "error": {
            "code": code
            or (detail.get("code") if isinstance(detail, dict) else None)
            or codes.get(status, "INTERNAL_ERROR"),
            "message": message,
            "details": detail if isinstance(detail, list) else [],
            "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
        },
    }
    if isinstance(detail, dict) and "current_version" in detail:
        payload["error"]["current_version"] = detail["current_version"]
    return JSONResponse(status_code=status, content=payload)


def install(app):
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        detail = [
            {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return error_response(request, 422, detail)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return error_response(request, 409, "Data conflicts with an existing record")
