"""Authentication middleware and dependencies."""
from __future__ import annotations

from typing import Optional
from fastapi import Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from database import db
from core.auth import verify_token
from core.exceptions import (
    TokenMissingException,
    TokenInvalidException,
    TokenExpiredException,
)

# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Get the current authenticated user from JWT token.

    This is a FastAPI dependency that extracts and validates the JWT token
    from the Authorization header, then returns the user data.

    Args:
        request: The FastAPI request object
        credentials: The HTTP Authorization credentials

    Returns:
        The user data dictionary

    Raises:
        TokenMissingException: If no token is provided
        TokenInvalidException: If the token is invalid
        TokenExpiredException: If the token has expired
    """
    if credentials is None:
        raise TokenMissingException(
            message="Authentication required",
            suggested_action="Please include a valid Bearer token in the Authorization header",
        )

    token = credentials.credentials

    # Verify token and get user_id
    user_id = verify_token(token, token_type="access")
    if user_id is None:
        raise TokenExpiredException(
            message="Token expired or invalid",
            suggested_action="Please refresh your token or log in again",
        )

    # Get user from database
    user = await db.users.get(user_id)
    if user is None:
        raise TokenInvalidException(
            message="User not found",
            suggested_action="Please log in again",
        )

    return user


async def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """Get the current user if authenticated, otherwise return None.

    This is useful for endpoints that can work both with and without
    authentication, potentially returning different data based on auth state.

    Args:
        request: The FastAPI request object
        credentials: The HTTP Authorization credentials

    Returns:
        The user data dictionary if authenticated, None otherwise
    """
    if credentials is None:
        return None

    token = credentials.credentials

    # Verify token and get user_id
    user_id = verify_token(token, token_type="access")
    if user_id is None:
        return None

    # Get user from database
    return await db.users.get(user_id)


def require_user_ownership(resource_user_id: Optional[str], current_user: dict) -> bool:
    """Check if the current user owns the resource.

    Args:
        resource_user_id: The user_id of the resource owner
        current_user: The current authenticated user

    Returns:
        True if the user owns the resource or if resource has no owner

    Note:
        Resources without a user_id are considered public/shared.
    """
    if resource_user_id is None:
        return True
    return resource_user_id == current_user.get("id")
