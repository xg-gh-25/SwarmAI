"""Authentication API endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, Depends

from schemas.auth import (
    RegisterRequest,
    LoginRequest,
    RefreshTokenRequest,
    AuthResponse,
    UserResponse,
    PasswordChangeRequest,
)
from database import db
from core.auth import (
    hash_password,
    verify_password,
    verify_token,
    create_token_pair,
)
from core.exceptions import (
    ValidationException,
    DuplicateException,
    TokenInvalidException,
    TokenExpiredException,
)
from middleware.auth import get_current_user

router = APIRouter()


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(request: RegisterRequest):
    """Register a new user account."""
    # Check if email already exists
    existing_users = await db.users.list()
    for user in existing_users:
        if user.get("email") == request.email:
            raise DuplicateException(
                message="Email already registered",
                detail="An account with this email address already exists",
            )

    # Create user
    user_data = {
        "id": str(uuid4()),
        "email": request.email,
        "name": request.name or request.email.split("@")[0],
        "password_hash": hash_password(request.password),
        "created_at": datetime.now().isoformat(),
        "last_login": datetime.now().isoformat(),
    }

    await db.users.put(user_data)

    # Generate tokens
    access_token, refresh_token, expires_in = create_token_pair(user_data["id"])

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """Authenticate user and return tokens."""
    # Find user by email
    users = await db.users.list()
    user = None
    for u in users:
        if u.get("email") == request.email:
            user = u
            break

    if not user:
        raise ValidationException(
            message="Invalid credentials",
            detail="Email or password is incorrect",
        )

    # Verify password
    if not verify_password(request.password, user.get("password_hash", "")):
        raise ValidationException(
            message="Invalid credentials",
            detail="Email or password is incorrect",
        )

    # Update last login
    await db.users.update(user["id"], {"last_login": datetime.now().isoformat()})

    # Generate tokens
    access_token, refresh_token, expires_in = create_token_pair(user["id"])

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token using refresh token."""
    # Verify refresh token
    user_id = verify_token(request.refresh_token, token_type="refresh")
    if not user_id:
        raise TokenExpiredException(
            message="Refresh token expired or invalid",
            suggested_action="Please log in again",
        )

    # Verify user still exists
    user = await db.users.get(user_id)
    if not user:
        raise TokenInvalidException(
            message="User not found",
            suggested_action="Please log in again",
        )

    # Generate new tokens
    access_token, refresh_token, expires_in = create_token_pair(user_id)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/logout", status_code=204)
async def logout(current_user: dict = Depends(get_current_user)):
    """Log out current user.

    Note: With JWT tokens, logout is handled client-side by discarding the tokens.
    This endpoint exists for API completeness and could be extended to implement
    token blacklisting if needed.
    """
    # JWT tokens are stateless, so we just return success
    # In production, you might want to add the token to a blacklist
    return None


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user's information."""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user.get("name"),
        created_at=current_user.get("created_at", ""),
        last_login=current_user.get("last_login"),
    )


@router.put("/password", status_code=204)
async def change_password(
    request: PasswordChangeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Change current user's password."""
    # Verify current password
    if not verify_password(request.current_password, current_user.get("password_hash", "")):
        raise ValidationException(
            message="Invalid current password",
            detail="The current password you entered is incorrect",
        )

    # Update password
    await db.users.update(
        current_user["id"],
        {"password_hash": hash_password(request.new_password)},
    )

    return None
