import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import DomainError, TransientDbError

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str, details: list | None = None, headers=None) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": [part for part in err["loc"] if part != "body"], "msg": err["msg"]} for err in exc.errors()]
        return error_response(422, "validation_error", "Request validation failed.", details)

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        return error_response(422, exc.code, exc.message, exc.details)

    @app.exception_handler(TransientDbError)
    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeoutError)
    async def _db_unavailable(request: Request, exc: Exception):
        logger.warning("database unavailable: %r", exc)
        return error_response(
            503, "service_unavailable", "Temporarily unable to process the request.", headers={"Retry-After": "1"}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
        return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "Internal server error.")
