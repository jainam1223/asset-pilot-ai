"""Global exception handlers, registered onto the FastAPI app in
`app.main`. Every failure path that isn't a router-level domain result
(HTTPException, request validation, or a bare unhandled exception)
funnels through here into the same `error_response()` envelope.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_service.errors import user_facing_message
from app.utils.response import error_response


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return error_response(
        status_code=exc.status_code,
        code="http_error",
        message=str(exc.detail),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed.",
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    # Anything reaching here is a bug or an outage the router didn't
    # already turn into a domain result — log the real exception, but
    # only ever hand the client the fixed, human-facing message.
    logger.error(f"unhandled_exception: {exc}")
    return error_response(
        status_code=500,
        code="internal_error",
        message=user_facing_message(exc),
    )


def register_exception_handlers(app) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
