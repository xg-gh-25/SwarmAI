"""Pre-flight AWS credential validation with time-based caching.

This module provides the ``CredentialValidator`` class, which calls
STS ``GetCallerIdentity`` to verify that AWS credentials are valid before
the Claude Agent SDK attempts a Bedrock API call.  This catches expired or
missing credentials early with a clear, actionable error message instead of
relying on fragile string-pattern matching against SDK error text.

Key design decisions:

- **Cache TTL**: ``is_valid`` caches a successful result for ``CACHE_TTL``
  (30 min — ADA creds typically last ~1h). The newer tri-state ``check``
  uses its OWN short cache (``_CHECK_CACHE_TTL`` = 90s) and caches ONLY
  "valid" so a mid-session expiry surfaces fast and post-mwinit recovery is
  immediate. Both avoid paying ~200 ms of STS latency on every request.
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

import logging
import time
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Auth-class STS error codes that mean "credentials are definitively expired /
# invalid — the user must re-authenticate (mwinit -f / ada credentials update)".
# Anything NOT in this set (throttling, 5xx, network) is treated as "unknown"
# so a transient blip never falsely blocks a valid session (Gate-1 BLOCKER 1).
_STS_EXPIRED_CODES: frozenset[str] = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
        "InvalidToken",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
    }
)
# Deliberately EXCLUDED: AccessDenied / AccessDeniedException. STS
# GetCallerIdentity can return AccessDenied for reasons unrelated to token
# expiry (an SCP / IAM boundary / VPC-endpoint policy denying sts:* while the
# principal still has valid Bedrock access). Mapping it to "expired" would
# FALSELY block a user whose credentials are fine for inference (Gate-2 H1).
# It falls through to "unknown" → fail-open → spawn proceeds.

# Tri-state credential status. "expired" is the ONLY value that blocks a spawn.
AuthStatus = Literal["valid", "expired", "unknown"]

# check() caches ONLY "valid" results, and for a SHORT window — so a mid-session
# expiry surfaces quickly (Gate-1 BLOCKER 2) and a post-mwinit recovery is
# immediate (expired/unknown are never cached → re-checked every call).
# Set ≥ the frontend /health poll interval (30s) so /health does at most ONE
# STS GetCallerIdentity per poll, not one every other poll (Gate-2 L1).
_CHECK_CACHE_TTL: int = 90


def _sts_timeout_config():
    """botocore Config bounding an STS GetCallerIdentity — a HANG-GUARD (STEERING #2).

    STS runs on the dedicated cap=2 'spawn' pool (run_e76b3ea5). Without a timeout,
    botocore defaults to ~60s connect + ~60s read × retries — so a hung STS (network
    black-hole / dead endpoint) can pin BOTH spawn workers for ~2 min and block every
    cold-session TTFT preflight. This bounds a call that can otherwise NEVER finish;
    on timeout it raises a BotoCoreError → check() classifies it 'unknown' (fail-open),
    NOT a truncation of real work. 5s read is ample for a healthy STS (<300ms).

    ⚠️ ``max_attempts`` in botocore's default LEGACY retry mode is the number of
    RETRIES, so ``{"max_attempts": 1}`` = 1 retry = 2 TOTAL attempts. Worst-case hang
    is therefore ~2×(3+5)+backoff ≈ ~16s per call (still ≪ the ~120s default). The one
    retry is DELIBERATE: it absorbs a single transient blip so a momentary network hiccup
    doesn't flip auth to 'unknown' — cheap given check() is 90s-cached and the readiness
    sampler re-checks every 10s. If a strict single-attempt is ever wanted, use
    ``retries={"mode": "standard", "max_attempts": 1}`` (standard mode counts TOTAL tries).
    """
    from botocore.config import Config
    return Config(
        connect_timeout=3,
        read_timeout=5,
        retries={"max_attempts": 1},  # legacy mode: 1 RETRY → 2 total attempts (~16s cap)
    )


class CredentialValidator:
    """Cached STS-based credential validation.

    Calls ``sts:GetCallerIdentity`` to verify AWS credentials are valid.
    ``is_valid`` results are cached for ``CACHE_TTL`` (30 min); the tri-state
    ``check`` uses its own ``_CHECK_CACHE_TTL`` (90s) and caches only "valid".
    Cache is invalidated on validation failure so the next request re-checks
    immediately.

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
        # Separate short-lived cache for check() — independent of the is_valid
        # 30min cache. Only a "valid" status is ever stored here.
        self._check_cache_time: float = 0
        self._check_cache_status: AuthStatus | None = None

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
            sts = session.client("sts", region_name=region, config=_sts_timeout_config())
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
            # Dedicated 'spawn' pool (run_e76b3ea5): this STS call is BOTH the
            # cold-spawn preflight (blocks _ensure_spawned → TTFT) and the
            # readiness-sampler auth leg. It must NEVER queue behind slow git
            # writers (a ~100s context_health index rebuild, a formerly-unbounded
            # plugin clone) — which is exactly what the shared 'subprocess' pool
            # allowed. 'spawn' hosts latency-sensitive READERS only, so a bulk-work
            # saturation on 'subprocess' can no longer stall this preflight/sampler.
            from core import executors
            identity = await executors.run_in("spawn", self._call_sts, region)
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

    def _call_sts_typed(self, region: str) -> dict[str, Any]:
        """STS GetCallerIdentity that PRESERVES the botocore exception type.

        Unlike :meth:`_call_sts` (which flattens every botocore error into a
        generic ``RuntimeError`` and thus loses the error code), this raises
        the original ``NoCredentialsError`` / ``ClientError`` / ``BotoCoreError``
        so :meth:`check` can classify expired-vs-unknown. Runs on a thread via
        ``asyncio.to_thread``.
        """
        import boto3

        # Fresh Session each call to pick up credentials written after startup.
        session = boto3.Session()
        sts = session.client("sts", region_name=region, config=_sts_timeout_config())
        response = sts.get_caller_identity()
        return {
            "Account": response["Account"],
            "Arn": response["Arn"],
            "UserId": response["UserId"],
        }

    async def check(self, region: str) -> AuthStatus:
        """Tri-state credential check: ``valid`` | ``expired`` | ``unknown``.

        This is the credential signal the spawn pre-flight and ``/health``
        consume. The crucial distinction over :meth:`is_valid` (which collapses
        every failure to ``False``):

        - ``valid``   — STS GetCallerIdentity succeeded. Cached for
          :data:`_CHECK_CACHE_TTL` seconds.
        - ``expired`` — DEFINITIVE: ``NoCredentialsError`` or an auth-class
          ``ClientError`` code (:data:`_STS_EXPIRED_CODES`). The ONLY status
          that should block a spawn. NOT cached → re-checked every call so a
          post-``mwinit`` recovery is picked up immediately.
        - ``unknown`` — non-definitive (throttling, 5xx, network/endpoint
          error, or any unexpected exception). Callers FAIL OPEN on this —
          spawn proceeds exactly as today. NOT cached.

        Catch order matters: ``NoCredentialsError`` is a ``BotoCoreError``
        subclass, so it MUST be caught before the generic ``BotoCoreError``
        branch or "no credentials" would wrongly read as ``unknown``.
        """
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
        )

        # Cache hit — only ever stores "valid".
        if (
            self._check_cache_status is not None
            and (time.monotonic() - self._check_cache_time) < _CHECK_CACHE_TTL
        ):
            return self._check_cache_status

        try:
            # Dedicated 'spawn' pool (run_e76b3ea5): check() is the readiness
            # sampler's auth leg AND the cold-spawn preflight — it must not queue
            # behind slow git writers on the 'subprocess' pool (a ~100s index
            # rebuild would stall the sampler → stale readiness → false offline).
            from core import executors
            await executors.run_in("spawn", self._call_sts_typed, region)
            self._check_cache_status = "valid"
            self._check_cache_time = time.monotonic()
            return "valid"
        except NoCredentialsError:
            # Subclass of BotoCoreError — MUST precede the BotoCoreError branch.
            logger.warning("Credential check: no AWS credentials found → expired")
            self._invalidate_check_cache()
            return "expired"
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            self._invalidate_check_cache()
            if code in _STS_EXPIRED_CODES:
                logger.warning(
                    "Credential check: auth-class STS error %s → expired", code
                )
                return "expired"
            logger.warning(
                "Credential check: non-auth STS error %s → unknown", code
            )
            return "unknown"
        except BotoCoreError as exc:
            # Network / endpoint / config error — NOT definitive. Fail open.
            logger.warning(
                "Credential check: botocore error %s → unknown",
                type(exc).__name__,
            )
            self._invalidate_check_cache()
            return "unknown"
        except Exception as exc:  # noqa: BLE001 — log type, never swallow silently
            logger.warning(
                "Credential check: unexpected %s → unknown",
                type(exc).__name__,
            )
            self._invalidate_check_cache()
            return "unknown"

    def _invalidate_check_cache(self) -> None:
        """Clear the short check() cache so the next call re-checks."""
        self._check_cache_time = 0
        self._check_cache_status = None

    def invalidate(self) -> None:
        """Force re-check on next call.

        Call this after detecting an auth error from the SDK so the next
        chat request re-validates immediately instead of trusting a stale
        cached ``True``.
        """
        logger.debug("Credential cache explicitly invalidated")
        self._invalidate_cache()
        self._invalidate_check_cache()

    def _invalidate_cache(self) -> None:
        """Clear all cached state."""
        self._last_check = 0
        self._last_result = False
        self._last_identity = None
