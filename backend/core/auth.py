"""JWT authentication utilities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext

from config import settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenPayload:
    """Token payload data."""

    def __init__(
        self,
        sub: str,
        exp: datetime,
        token_type: str = "access",
        iat: Optional[datetime] = None,
    ):
        self.sub = sub  # Subject (user_id)
        self.exp = exp  # Expiration time
        self.token_type = token_type  # "access" or "refresh"
        self.iat = iat or datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token.

    Args:
        user_id: The user's unique identifier
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT access token
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }

    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT refresh token.

    Args:
        user_id: The user's unique identifier
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT refresh token
    """
    if expires_delta is None:
        expires_delta = timedelta(days=settings.refresh_token_expire_days)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }

    return jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def verify_token(token: str, token_type: str = "access") -> Optional[str]:
    """Verify a JWT token and return the user_id.

    Args:
        token: The JWT token to verify
        token_type: Expected token type ("access" or "refresh")

    Returns:
        User ID if token is valid, None otherwise
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )

        # Check token type
        if payload.get("type") != token_type:
            return None

        # Check expiration
        exp = payload.get("exp")
        if exp is None:
            return None
        if datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(timezone.utc):
            return None

        return payload.get("sub")

    except JWTError:
        return None


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token without verification.

    Useful for extracting payload info before full verification.

    Args:
        token: The JWT token to decode

    Returns:
        Token payload dict or None if invalid
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
    except JWTError:
        return None


def create_token_pair(user_id: str) -> tuple[str, str, int]:
    """Create both access and refresh tokens.

    Args:
        user_id: The user's unique identifier

    Returns:
        Tuple of (access_token, refresh_token, expires_in_seconds)
    """
    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id)
    expires_in = settings.access_token_expire_minutes * 60

    return access_token, refresh_token, expires_in
