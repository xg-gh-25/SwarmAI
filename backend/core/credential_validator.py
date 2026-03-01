"""Pre-flight AWS credential validation with time-based caching.

This module provides the ``CredentialValidator`` class, which calls
STS ``GetCallerIdentity`` to verify that AWS credentials are valid before
the Claude Agent SDK attempts a Bedrock API call.  This catches expired or
missing credentials early with a clear, actionable error message instead of
relying on fragile string-pattern matching against SDK error text.

Key design decisions:

- **5-minute cache TTL**: A successful validation is cached for 300 seconds
  so that back-to-back chat requests do not each incur ~200 ms of STS
  latency.
- **Immediate invalidation on failure**: When a validation check fails the
  cache is cleared so the very next request re-checks immediately.
- **Async-compatible**: The synchronous ``boto3`` STS call is offloaded to
  a thread via ``asyncio.to_thread`` so it never blocks the event loop.
- **Graceful error handling**: ``ClientError``, ``NoCredentialsError``,
  ``BotoCoreError``, and any unexpected exceptions are caught and treated
  as "credentials invalid" rather than propagated.

Public API:

- ``CredentialValidator`` — Main class with ``is_valid()``,
  ``get_identity()``, and ``invalidate()`` methods.

Usage::

    validator = CredentialValidator()
    if not await validator.is_valid("us-east-1"):
        # yield CREDENTIALS_EXPIRED SSE error
        ...
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CredentialValidator:
    """Cached STS-based credential validation.

    Calls ``sts:GetCallerIdentity`` to verify AWS credentials are valid.
    Results are cached for 5 minutes to avoid adding latency to every
    chat request.  Cache is invalidated on validation failure so the
    next request re-checks immediately.

    Typical usage::

        validator = CredentialValidator()
        if not await validator.is_valid("us-east-1"):
            # emit CREDENTIALS_EXPIRED SSE error
            ...
        identity = await validator.get_identity("us-east-1")
        # identity == {"Account": "123456789012", "Arn": "...", "UserId": "..."}
    """

    CACHE_TTL: int = 1800  # 30 minutes (ADA credentials typically last 1 hour)

    def __init__(self) -> None:
        self._last_check: float = 0
        self._last_result: bool = False
        self._last_identity: dict[str, Any] | None = None

    def _is_cache_valid(self) -> bool:
        """Return True if the cached result is still within the TTL window."""
        if self._last_check == 0:
            return False
        return (time.monotonic() - self._last_check) < self.CACHE_TTL

    def _call_sts(self, region: str) -> dict[str, Any]:
        """Synchronous STS GetCallerIdentity call.

        This runs on a thread via ``asyncio.to_thread`` so it never blocks
        the async event loop.  All boto3/botocore exceptions are caught and
        re-raised as ``RuntimeError`` to keep the async layer simple.
        """
        import boto3
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
        )

        try:
            # Create a fresh Session each time to pick up credentials that
            # were written after the backend started (e.g. ada credentials
            # update).  The default boto3.client() reuses a module-level
            # session that may have cached "no credentials" from startup.
            session = boto3.Session()
            sts = session.client("sts", region_name=region)
            response = sts.get_caller_identity()
            return {
                "Account": response["Account"],
                "Arn": response["Arn"],
                "UserId": response["UserId"],
            }
        except NoCredentialsError:
            raise RuntimeError("No AWS credentials found")
        except ClientError as exc:
            raise RuntimeError(str(exc))
        except BotoCoreError as exc:
            raise RuntimeError(str(exc))

    async def is_valid(self, region: str) -> bool:
        """Check if AWS credentials are valid (cached).

        Returns ``True`` if credentials resolve and the STS call succeeds.
        Results are cached for :attr:`CACHE_TTL` seconds.  On failure the
        cache is invalidated so the next call re-checks immediately.
        """
        if self._is_cache_valid():
            logger.debug("Credential cache hit (valid=%s)", self._last_result)
            return self._last_result

        logger.debug("Credential cache miss — calling STS in region %s", region)
        try:
            identity = await asyncio.to_thread(self._call_sts, region)
            self._last_result = True
            self._last_identity = identity
            self._last_check = time.monotonic()
            logger.info(
                "AWS credentials valid (account=%s)",
                identity.get("Account", "unknown"),
            )
            return True
        except RuntimeError as exc:
            logger.warning("AWS credential validation failed: %s", exc)
            self._invalidate_cache()
            return False
        except Exception as exc:
            logger.warning("Unexpected error during credential validation: %s", exc)
            self._invalidate_cache()
            return False

    async def get_identity(self, region: str) -> dict[str, Any] | None:
        """Return the STS caller identity if valid, ``None`` otherwise.

        Calls :meth:`is_valid` internally so the cache is shared.
        """
        if await self.is_valid(region):
            return self._last_identity
        return None

    def invalidate(self) -> None:
        """Force re-check on next call.

        Call this after detecting an auth error from the SDK so the next
        chat request re-validates immediately instead of trusting a stale
        cached ``True``.
        """
        logger.debug("Credential cache explicitly invalidated")
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Clear all cached state."""
        self._last_check = 0
        self._last_result = False
        self._last_identity = None
