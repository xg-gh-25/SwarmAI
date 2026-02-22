"""Global error handler middleware for structured error responses."""
import logging
import traceback
from uuid import uuid4
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from core.exceptions import (
    AppException,
    ValidationException,
    RateLimitException,
)
from schemas.error import ErrorResponse, ValidationErrorResponse, ValidationErrorDetail

logger = logging.getLogger(__name__)


def create_error_response(
    code: str,
    message: str,
    detail: str | None = None,
    suggested_action: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Create a standardized error response dictionary."""
    response = ErrorResponse(
        code=code,
        message=message,
        detail=detail,
        suggested_action=suggested_action,
        request_id=request_id,
    )
    return response.model_dump(exclude_none=True)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle custom application exceptions."""
    request_id = str(uuid4())

    # Log the error
    logger.warning(
        f"AppException: {exc.code} - {exc.message}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "error_code": exc.code,
        }
    )

    # Handle validation exceptions with field details
    if isinstance(exc, ValidationException) and exc.fields:
        response = ValidationErrorResponse(
            code=exc.code,
            message=exc.message,
            detail=exc.detail,
            suggested_action=exc.suggested_action,
            request_id=request_id,
            fields=[ValidationErrorDetail(**f) for f in exc.fields],
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump(exclude_none=True),
        )

    # Handle rate limit exceptions with retry_after header
    if isinstance(exc, RateLimitException):
        return JSONResponse(
            status_code=exc.status_code,
            content=create_error_response(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
                suggested_action=exc.suggested_action,
                request_id=request_id,
            ),
            headers={"Retry-After": str(exc.retry_after)},
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=create_error_response(
            code=exc.code,
            message=exc.message,
            detail=exc.detail,
            suggested_action=exc.suggested_action,
            request_id=request_id,
        ),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI/Pydantic validation errors."""
    request_id = str(uuid4())

    # Extract field errors
    fields = []
    for error in exc.errors():
        field_path = ".".join(str(loc) for loc in error.get("loc", []))
        fields.append(
            ValidationErrorDetail(
                field=field_path,
                error=error.get("msg", "Validation error"),
            )
        )

    logger.warning(
        f"Validation error on {request.url.path}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "errors": [f.model_dump() for f in fields],
        }
    )

    response = ValidationErrorResponse(
        code="VALIDATION_FAILED",
        message="Invalid input data",
        detail=f"Validation failed for {len(fields)} field(s)",
        suggested_action="Please check your input and try again",
        request_id=request_id,
        fields=fields,
    )

    return JSONResponse(
        status_code=400,
        content=response.model_dump(exclude_none=True),
    )


async def pydantic_validation_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    """Handle Pydantic ValidationError (not from FastAPI)."""
    request_id = str(uuid4())

    fields = []
    for error in exc.errors():
        field_path = ".".join(str(loc) for loc in error.get("loc", []))
        fields.append(
            ValidationErrorDetail(
                field=field_path,
                error=error.get("msg", "Validation error"),
            )
        )

    logger.warning(
        f"Pydantic validation error on {request.url.path}",
        extra={"request_id": request_id, "errors": [f.model_dump() for f in fields]}
    )

    response = ValidationErrorResponse(
        code="VALIDATION_FAILED",
        message="Invalid input data",
        suggested_action="Please check your input and try again",
        request_id=request_id,
        fields=fields,
    )

    return JSONResponse(
        status_code=400,
        content=response.model_dump(exclude_none=True),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    request_id = str(uuid4())

    # Log the full traceback for debugging
    logger.error(
        f"Unexpected error on {request.url.path}: {str(exc)}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "traceback": traceback.format_exc(),
        }
    )

    return JSONResponse(
        status_code=500,
        content=create_error_response(
            code="SERVER_ERROR",
            message="An unexpected error occurred",
            detail=str(exc) if logger.isEnabledFor(logging.DEBUG) else None,
            suggested_action="Please try again later. If the problem persists, contact support.",
            request_id=request_id,
        ),
    )


def setup_error_handlers(app: FastAPI) -> None:
    """Register all error handlers with the FastAPI app."""
    # Custom application exceptions
    app.add_exception_handler(AppException, app_exception_handler)

    # FastAPI validation errors
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Pydantic validation errors
    app.add_exception_handler(ValidationError, pydantic_validation_handler)

    # Generic fallback handler
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("Error handlers registered")
