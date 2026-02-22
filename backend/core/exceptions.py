"""Custom exception classes for structured error handling."""
from typing import Optional


class AppException(Exception):
    """Base exception for all application errors."""

    code: str = "SERVER_ERROR"
    message: str = "An unexpected error occurred"
    status_code: int = 500
    suggested_action: Optional[str] = "Please try again later"

    def __init__(
        self,
        message: Optional[str] = None,
        detail: Optional[str] = None,
        suggested_action: Optional[str] = None,
        code: Optional[str] = None,
    ):
        self.message = message or self.message
        self.detail = detail
        if suggested_action:
            self.suggested_action = suggested_action
        if code:
            self.code = code
        super().__init__(self.message)


# Validation Errors (400)
class ValidationException(AppException):
    """Raised when input validation fails."""

    code = "VALIDATION_FAILED"
    message = "Invalid input data"
    status_code = 400
    suggested_action = "Please check your input and try again"

    def __init__(
        self,
        message: Optional[str] = None,
        detail: Optional[str] = None,
        fields: Optional[list[dict]] = None,
        suggested_action: Optional[str] = None,
    ):
        super().__init__(message=message, detail=detail, suggested_action=suggested_action)
        self.fields = fields or []


# Authentication Errors (401)
class AuthException(AppException):
    """Base class for authentication errors."""

    code = "AUTH_ERROR"
    message = "Authentication required"
    status_code = 401
    suggested_action = "Please log in and try again"


class TokenMissingException(AuthException):
    """Raised when no authentication token is provided."""

    code = "AUTH_TOKEN_MISSING"
    message = "Authentication token is required"


class TokenInvalidException(AuthException):
    """Raised when the token is invalid."""

    code = "AUTH_TOKEN_INVALID"
    message = "Invalid authentication token"
    suggested_action = "Please log in again"


class TokenExpiredException(AuthException):
    """Raised when the token has expired."""

    code = "AUTH_TOKEN_EXPIRED"
    message = "Authentication token has expired"
    suggested_action = "Please refresh your token or log in again"


# Authorization Errors (403)
class ForbiddenException(AppException):
    """Raised when user lacks permission."""

    code = "FORBIDDEN"
    message = "You do not have permission to perform this action"
    status_code = 403
    suggested_action = "Contact an administrator if you believe this is an error"


# Not Found Errors (404)
class NotFoundException(AppException):
    """Base class for not found errors."""

    code = "NOT_FOUND"
    message = "Resource not found"
    status_code = 404
    suggested_action = "Please check the ID and try again"


class AgentNotFoundException(NotFoundException):
    """Raised when an agent is not found."""

    code = "AGENT_NOT_FOUND"
    message = "The requested agent could not be found"


class SkillNotFoundException(NotFoundException):
    """Raised when a skill is not found."""

    code = "SKILL_NOT_FOUND"
    message = "The requested skill could not be found"


class MCPServerNotFoundException(NotFoundException):
    """Raised when an MCP server is not found."""

    code = "MCP_SERVER_NOT_FOUND"
    message = "The requested MCP server could not be found"


class SessionNotFoundException(NotFoundException):
    """Raised when a session is not found."""

    code = "SESSION_NOT_FOUND"
    message = "The requested session could not be found"


# Conflict Errors (409)
class ConflictException(AppException):
    """Raised when there's a state conflict."""

    code = "CONFLICT"
    message = "The request conflicts with the current state"
    status_code = 409
    suggested_action = "Please refresh and try again"


class DuplicateException(ConflictException):
    """Raised when trying to create a duplicate resource."""

    code = "DUPLICATE_RESOURCE"
    message = "A resource with this identifier already exists"


# Rate Limit Errors (429)
class RateLimitException(AppException):
    """Raised when rate limit is exceeded."""

    code = "RATE_LIMIT_EXCEEDED"
    message = "Too many requests. Please slow down."
    status_code = 429
    suggested_action = "Please wait before making more requests"

    def __init__(self, retry_after: int = 60, **kwargs):
        super().__init__(**kwargs)
        self.retry_after = retry_after


# Service Errors (503)
class ServiceUnavailableException(AppException):
    """Raised when an external service is unavailable."""

    code = "SERVICE_UNAVAILABLE"
    message = "Service temporarily unavailable. Please try again later."
    status_code = 503
    suggested_action = "Please wait a moment and retry"


class DatabaseUnavailableException(ServiceUnavailableException):
    """Raised when the database is unavailable."""

    code = "DATABASE_UNAVAILABLE"
    message = "Database service is temporarily unavailable"


# Agent Execution Errors (500)
class AgentExecutionException(AppException):
    """Raised when agent execution fails."""

    code = "AGENT_EXECUTION_ERROR"
    message = "Agent execution failed"
    status_code = 500
    suggested_action = "Please try again or contact support"


class AgentTimeoutException(AgentExecutionException):
    """Raised when agent execution times out."""

    code = "AGENT_TIMEOUT"
    message = "Agent response timed out. Your conversation has been saved."
    suggested_action = "Please try again"
