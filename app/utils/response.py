"""The ONE response envelope used by every endpoint, success or error.

Routers and global exception handlers both build their JSON body by
calling `success_response()` / `error_response()` from here — nothing
else should construct a response dict by hand. That is what guarantees
every failure path (deliberate domain exception, validation error,
generic HTTPException, or an unhandled 500) funnels into the same shape.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.responses import JSONResponse

from app.utils.pagination import PaginationMeta
from app.utils.request_context import REQUEST_ID_HEADER, get_request_id


def _base_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "request_id": get_request_id(),
    }
    if extra:
        meta.update(extra)
    return meta


def _request_id_headers() -> dict[str, str]:
    # Set here (not left to RequestContextMiddleware) so it's present even
    # for the one path the middleware can't reach: an exception that
    # propagates out of call_next() and is handled by ServerErrorMiddleware,
    # which sits OUTSIDE the middleware and never gets a response object
    # back from it to attach headers to.
    request_id = get_request_id()
    return {REQUEST_ID_HEADER: request_id} if request_id else {}


def success_response(
    data: Any = None,
    *,
    status_code: int = 200,
    message: str | None = None,
    pagination: PaginationMeta | None = None,
    meta_extra: dict[str, Any] | None = None,
) -> JSONResponse:
    meta = _base_meta(meta_extra)
    if pagination is not None:
        meta["pagination"] = pagination.model_dump()

    body = {
        "status_code": status_code,
        "data": data,
        "message": message,
        "meta": meta,
        "success": True,
    }
    return JSONResponse(status_code=status_code, content=body, headers=_request_id_headers())


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body = {
        "status_code": status_code,
        "message": message,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
        "meta": _base_meta(),
        "success": False,
    }
    return JSONResponse(status_code=status_code, content=body, headers=_request_id_headers())
