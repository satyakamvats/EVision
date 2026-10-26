from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def api_error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=api_error("validation_error", "Request validation failed.", {"errors": exc.errors()}))


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    status = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", "Request failed")
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=status, content=detail)
    return JSONResponse(status_code=status, content=api_error("request_error", str(detail)))
