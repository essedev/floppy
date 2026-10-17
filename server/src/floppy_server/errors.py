"""
API errors as `{code, message, details}`, the shape the admin web's client reads.

Services raise `ApiError` with one of the contract's codes; the handlers here turn it, a
validation failure, an unknown route or an unexpected exception into that shape. Validation
errors never echo the submitted values: a rejected secret key must not come back in the
answer.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    """An error answer with a contract code, an HTTP status and safe details."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def unauthorized(message: str = "Not signed in") -> ApiError:
    """401: no valid session or device token."""
    return ApiError(401, "unauthorized", message)


def device_unauthorized(reason: str, message: str) -> ApiError:
    """
    401 for a device token, with `details.reason` for the app to word it: `token_unknown`,
    `device_revoked`, `device_signed_out` or `device_not_registered`.
    """
    return ApiError(401, "unauthorized", message, {"reason": reason})


def forbidden(message: str = "Admins only") -> ApiError:
    """403: signed in, but the role does not allow it."""
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Not found") -> ApiError:
    """404: no such row in the caller's organization (or not visible to the caller)."""
    return ApiError(404, "not_found", message)


def invalid_state(message: str) -> ApiError:
    """409: a state machine transition that is not allowed from the current state."""
    return ApiError(409, "invalid_state", message)


def validation(message: str, details: dict[str, Any] | None = None) -> ApiError:
    """422: a request that is well formed but not acceptable."""
    return ApiError(422, "validation", message, details)


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


_STATUS_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "not_found"}


def install_error_handlers(app: FastAPI) -> None:
    """Registers the handlers that give every error the contract's shape."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(error.code, error.message, error.details),
            status_code=error.status,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, error: RequestValidationError) -> JSONResponse:
        # Only where and what, never the input: it may be a secret key or a code.
        fields = [
            {"loc": [str(part) for part in item.get("loc", ())], "type": item.get("type", "")}
            for item in error.errors()
        ]
        return JSONResponse(
            _body("validation", "Invalid request", {"fields": fields}), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, error: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(
            error.status_code, "server" if error.status_code >= 500 else "validation"
        )
        return JSONResponse(_body(code, str(error.detail)), status_code=error.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=error)
        return JSONResponse(_body("server", "Internal error"), status_code=500)
