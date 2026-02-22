"""Rate limiting middleware using slowapi."""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings


def get_user_identifier(request: Request) -> str:
    """Get unique identifier for rate limiting.

    Uses authenticated user_id if available, otherwise falls back to IP address.
    This allows rate limiting per user when authenticated, or per IP when not.
    """
    # Check for authenticated user in request state
    if hasattr(request.state, "user") and request.state.user:
        return f"user:{request.state.user.get('id', '')}"

    # Try to get user from Authorization header (for JWT)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # Use a hash of the token for rate limiting (not the full token)
        token = auth_header[7:]
        if len(token) > 10:
            return f"token:{token[:10]}"

    # Fall back to IP address
    return get_remote_address(request)


# Create limiter with default rate limit
limiter = Limiter(
    key_func=get_user_identifier,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)


def get_limiter() -> Limiter:
    """Get the rate limiter instance."""
    return limiter


# Decorator for rate-limited endpoints
def rate_limit(limit: str):
    """Rate limit decorator for specific endpoints.

    Usage:
        @router.get("/endpoint")
        @rate_limit("10/minute")
        async def endpoint():
            ...
    """
    return limiter.limit(limit)


# Exemptions for specific paths (like health checks)
EXEMPT_PATHS = {
    "/health",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def should_exempt(request: Request) -> bool:
    """Check if request path should be exempt from rate limiting."""
    return request.url.path in EXEMPT_PATHS
