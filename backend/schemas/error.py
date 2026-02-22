"""Error response schemas for structured error handling."""
from typing import Optional
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error response format for all API errors."""

    code: str = Field(
        ...,
        description="Machine-readable error code (e.g., 'AGENT_NOT_FOUND')",
        pattern=r"^[A-Z][A-Z0-9_]*$",
        max_length=50,
        examples=["AGENT_NOT_FOUND", "VALIDATION_FAILED"]
    )
    message: str = Field(
        ...,
        description="User-friendly error message",
        max_length=500,
        examples=["The requested agent could not be found."]
    )
    detail: Optional[str] = Field(
        None,
        description="Technical detail for debugging (optional)",
        max_length=2000,
        examples=["Agent with ID 'abc-123' does not exist in database"]
    )
    suggested_action: Optional[str] = Field(
        None,
        description="Actionable guidance for user (optional)",
        max_length=200,
        examples=["Please check the agent ID and try again."]
    )
    request_id: Optional[str] = Field(
        None,
        description="Request correlation ID for support",
        examples=["req_abc123xyz"]
    )


class ValidationErrorDetail(BaseModel):
    """Detail for a single validation error."""

    field: str = Field(..., description="The field that failed validation")
    error: str = Field(..., description="Description of the validation error")


class ValidationErrorResponse(ErrorResponse):
    """Extended error response for validation errors."""

    code: str = "VALIDATION_FAILED"
    fields: list[ValidationErrorDetail] = Field(
        default_factory=list,
        description="List of field-specific validation errors"
    )


class RateLimitErrorResponse(ErrorResponse):
    """Extended error response for rate limit errors."""

    code: str = "RATE_LIMIT_EXCEEDED"
    retry_after: int = Field(
        ...,
        description="Seconds until rate limit resets",
        examples=[60]
    )
