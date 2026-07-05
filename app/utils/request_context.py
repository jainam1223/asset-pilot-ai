"""Per-request request-id tracking, shared by the logging middleware and
the response envelope in `app.utils.response`.

A contextvar (not request.state) is used so `get_request_id()` is
callable from anywhere in the call stack — including
`utils/response.py`, which has no access to the `Request` object.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id (reusing one supplied by the caller, if any),
    makes it available to `get_request_id()`, and echoes it back on the
    response header so a client can correlate logs to a specific call."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = _request_id.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
