import uuid

from starlette.requests import Request

from app.core.config import settings
from app.core.errors import error_response


class RequestBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        maximum = (
            settings.document_max_bytes + 65536
            if scope.get("path", "").endswith(("/profile/documents", "/profile/cv-imports"))
            else 1024 * 1024
        )
        buffered = []
        consumed = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            consumed += len(message.get("body", b""))
            if consumed > maximum:
                request = Request(scope)
                request.state.request_id = str(uuid.uuid4())
                response = error_response(request, 413, "Request body is too large")
                response.headers["X-Request-ID"] = request.state.request_id
                return await response(scope, receive, send)
            buffered.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay():
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)
