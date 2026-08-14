"""
Shared Bedrock client for job handlers.

All background job handlers that call Bedrock LLMs MUST use this module
instead of creating raw ``boto3.client("bedrock-runtime")`` inline.

Credential strategy (same as the main SwarmAI app):
  1. Try boto3 default chain (credential_process → ada → Isengard)
  2. If that fails (launchd context, VPN off, mwinit expired), fall back
     to AWS SSO IdC tokens from ``~/.aws/sso/cache/``
  3. Pre-resolve credentials and inject them explicitly into the boto3
     client — avoids credential_process resolution at call time.

Usage::

    from jobs.bedrock import get_client, get_model_id

    client = get_client()
    response = client.invoke_model(modelId=get_model_id(), body=...)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("swarm.jobs.bedrock")

# Module-level cached client — reused across calls within the same process.
# TTL prevents stale credentials from causing persistent auth failures.
_client: Any | None = None
_client_region: str | None = None
_client_created_at: float = 0.0  # monotonic timestamp
_CLIENT_TTL: float = 1800.0  # 30 minutes — STS temporary creds have 1-12h TTL

# Substrings (lowercased) that mark a bedrock error as a transient credential/auth
# failure worth ONE evict-and-retry. Single source of truth — used by both invoke()
# and converse_with_retry() so the retry predicate can never drift between them.
_RETRIABLE_AUTH_KEYWORDS: tuple[str, ...] = (
    "credential", "expired", "token", "unauthorized",
    "accessdenied", "security",
)


def _load_config() -> tuple[str, dict]:
    """Read region and model map from AppConfigManager (same as app).

    Falls back to sane defaults if AppConfigManager is unavailable
    (e.g. during tests or standalone scheduler execution).
    """
    try:
        from core.app_config_manager import AppConfigManager
        cfg = AppConfigManager.instance()
        region = cfg.get("aws_region") or "us-east-1"
        model_map = cfg.get("bedrock_model_map") or {}
    except Exception:
        region = "us-east-1"
        model_map = {}
    return region, model_map


def _resolve_credentials() -> dict[str, str]:
    """Pre-resolve AWS credentials using the same strategy as executor.py.

    Tries boto3 default chain first (credential_process → ada → Isengard).
    If that fails (launchd context, VPN off, mwinit expired), tries to
    find SSO IdC cached credentials from ``~/.aws/sso/cache/``.

    Returns dict with AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and
    optionally AWS_SESSION_TOKEN. Returns empty dict if all methods fail.
    """
    # Method 1: boto3 default chain (same as executor._get_aws_credentials)
    try:
        import boto3

        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is not None:
            frozen = credentials.get_frozen_credentials()
            if frozen.access_key:
                creds = {
                    "aws_access_key_id": frozen.access_key,
                    "aws_secret_access_key": frozen.secret_key,
                }
                if frozen.token:
                    creds["aws_session_token"] = frozen.token
                logger.info("Credentials resolved via boto3 default chain")
                return creds
    except Exception as e:
        logger.debug("boto3 credential resolution failed: %s", e)

    # Method 2: SSO IdC cached credentials (same tokens Claude CLI uses)
    try:
        import json
        from pathlib import Path

        sso_cache_dir = Path.home() / ".aws" / "sso" / "cache"
        if sso_cache_dir.is_dir():
            # Find the newest non-expired SSO token
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            best_token = None
            best_expiry = None

            for f in sso_cache_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    # SSO token files have accessToken + expiresAt
                    if "accessToken" not in data:
                        continue
                    expires = datetime.fromisoformat(
                        data["expiresAt"].replace("Z", "+00:00")
                    )
                    if expires > now and (best_expiry is None or expires > best_expiry):
                        best_token = data
                        best_expiry = expires
                except Exception:
                    continue

            if best_token:
                # Use STS to exchange SSO token for temporary credentials
                import boto3
                sso_client = boto3.client(
                    "sso",
                    region_name=best_token.get("region", "us-east-1"),
                )
                # We need accountId and roleName from the SSO config
                # Read from ~/.aws/config
                import configparser
                aws_config = configparser.ConfigParser()
                aws_config.read(str(Path.home() / ".aws" / "config"))

                # Prefer [default] profile, then first match with sso_account_id.
                # Without this, multi-profile configs could pick the wrong account.
                sections = aws_config.sections()
                # Sort so 'default' (or 'profile default') comes first
                sections.sort(key=lambda s: (0 if 'default' in s.lower() else 1, s))
                for section in sections:
                    acct = aws_config.get(section, "sso_account_id", fallback=None)
                    role = aws_config.get(section, "sso_role_name", fallback=None)
                    if acct and role:
                        resp = sso_client.get_role_credentials(
                            roleName=role,
                            accountId=acct,
                            accessToken=best_token["accessToken"],
                        )
                        role_creds = resp["roleCredentials"]
                        logger.info("Credentials resolved via SSO IdC cache")
                        return {
                            "aws_access_key_id": role_creds["accessKeyId"],
                            "aws_secret_access_key": role_creds["secretAccessKey"],
                            "aws_session_token": role_creds["sessionToken"],
                        }
    except Exception as e:
        logger.debug("SSO IdC credential resolution failed: %s", e)

    logger.warning("All credential resolution methods failed")
    return {}


def get_client(*, force_new: bool = False, region: str | None = None) -> Any:
    """Return a cached bedrock-runtime client with pre-resolved credentials.

    Pre-resolves credentials in-process (where PATH is correct), then
    injects them explicitly into the boto3 client. This avoids the
    credential_process resolution at call time which fails in launchd.

    Args:
        force_new: Bypass cache and create a fresh client (useful after
            credential eviction on auth errors).
        region: Optional explicit region override. When None, resolves region
            from AppConfigManager (the job default). Callers that have their own
            region precedence (e.g. the eval judge honors AWS_REGION env first)
            pass it explicitly so this client uses the SAME region the caller
            would have used — no silent region-source switch. The region is part
            of the cache key, so an override participates in cache invalidation.
    """
    global _client, _client_region, _client_created_at
    import time

    if region is None:
        region, _ = _load_config()

    # TTL check — recreate client periodically to pick up refreshed credentials.
    # STS temporary credentials have 1-12h TTL; 30min refresh is conservative.
    expired = (time.monotonic() - _client_created_at) > _CLIENT_TTL
    if not force_new and not expired and _client is not None and _client_region == region:
        return _client

    import boto3
    from botocore.config import Config as BotoConfig

    boto_config = BotoConfig(
        retries={"max_attempts": 2, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=120,  # Opus 4.6 skill proposals need 60-90s; 60s too tight
    )

    # Pre-resolve credentials (same strategy as executor._get_aws_credentials)
    creds = _resolve_credentials()

    if creds:
        # Inject explicit credentials — bypasses credential_process entirely
        _client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=boto_config,
            **creds,
        )
        logger.debug("Created Bedrock client with pre-resolved creds for region=%s", region)
    else:
        # Fallback to default chain (may work if running interactively)
        _client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=boto_config,
        )
        logger.warning("Created Bedrock client with default chain (no pre-resolved creds) for region=%s", region)

    _client_region = region
    _client_created_at = time.monotonic()
    return _client


def evict_client() -> None:
    """Drop the cached client, forcing re-creation on next call.

    Call this when you get a credential/auth error so the next attempt
    picks up refreshed credentials.
    """
    global _client, _client_region, _client_created_at
    _client = None
    _client_region = None
    _client_created_at = 0.0


def build_timeout_client(*, read_timeout: int, max_attempts: int, region: str | None = None) -> Any:
    """Build a THROWAWAY (uncached) bedrock-runtime client with tight timeouts.

    For callers that need fail-fast behavior WITHOUT mutating the shared cached
    client (which uses read_timeout=120/max_attempts=2 because skill proposals
    need 60-90s). The OS-Eval judge uses this so one hung Bedrock call fails in
    ~30s instead of stalling on the shared 120s client and blowing the eval wall
    — a serial sweep of 89 judges cannot afford a 120s×N tail (run_9fdb8ad5).

    Reuses the same pre-resolved credentials as get_client (the default chain
    fails under launchd), but does NOT touch the module cache (`_client`) or
    participate in evict_client — it is created, used, and discarded per call
    (a fresh per-call client with shared resolved creds).

    Args:
        read_timeout: socket read timeout in seconds (the anti-hang lever).
        max_attempts: boto retry attempts. Use 1 for strict fail-fast; note this
            disables adaptive throttle-retry, acceptable for the serial judge.
        region: explicit region (caller preserves its own precedence). None →
            resolved from AppConfigManager, same as get_client.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    if region is None:
        region, _ = _load_config()

    boto_config = BotoConfig(
        retries={"max_attempts": max_attempts, "mode": "standard"},
        connect_timeout=10,
        read_timeout=read_timeout,
    )
    creds = _resolve_credentials()
    if creds:
        return boto3.client("bedrock-runtime", region_name=region, config=boto_config, **creds)
    return boto3.client("bedrock-runtime", region_name=region, config=boto_config)


def get_model_id(model_key: str = "claude-sonnet-4-6") -> str:
    """Resolve a model key to its Bedrock model ID via AppConfigManager.

    Falls back to the cross-region inference ID if no mapping is found.
    """
    _, model_map = _load_config()
    return model_map.get(model_key, f"us.anthropic.{model_key}")


def _record_usage_sync(
    *,
    input_tokens: int,
    output_tokens: int,
    model: str | None = None,
) -> None:
    """Record token usage from sync context. Fire-and-forget."""
    try:
        import asyncio
        import database

        async def _do_record():
            await database.db.record_token_usage(
                session_id=None,
                source="background_job",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
            )

        # If there's a running loop (e.g. FastAPI), schedule task.
        # Otherwise (standalone job), use asyncio.run().
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_record())
        except RuntimeError:
            asyncio.run(_do_record())
    except Exception:
        logger.debug("Failed to record background job token usage", exc_info=True)


def invoke(
    prompt: str,
    *,
    model_key: str = "claude-sonnet-4-6",
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> tuple[str, int, int]:
    """High-level invoke: prompt in, text + token counts out.

    Handles credential eviction + single retry on auth errors.

    Returns:
        (response_text, input_tokens, output_tokens)

    Raises:
        Exception: If both attempts fail.
    """
    import json

    model_id = get_model_id(model_key)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    })

    for attempt in range(2):
        try:
            client = get_client(force_new=(attempt > 0))
            response = client.invoke_model(modelId=model_id, body=body)
            result = json.loads(response["body"].read())

            text = result["content"][0]["text"]
            input_tok = result.get("usage", {}).get("input_tokens", 0)
            output_tok = result.get("usage", {}).get("output_tokens", 0)

            logger.info(
                "Bedrock invoke: model=%s, %d in / %d out tokens",
                model_id, input_tok, output_tok,
            )

            # Fire-and-forget token recording for background jobs.
            # bedrock.invoke() is sync, so we record via a sync helper
            # (spawns a short-lived asyncio.run). Never breaks the job.
            try:
                _record_usage_sync(
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                    model=model_id,
                )
            except Exception:
                pass  # fire-and-forget

            return text, input_tok, output_tok

        except Exception as e:
            err_str = str(e).lower()
            retriable = any(kw in err_str for kw in _RETRIABLE_AUTH_KEYWORDS)
            if retriable and attempt == 0:
                logger.warning(
                    "Bedrock auth error (attempt %d), evicting client: %s",
                    attempt + 1, e,
                )
                evict_client()
                continue
            raise


def converse_with_retry(
    *,
    messages: list,
    system: list,
    inference_config: dict,
    model_id: str,
    region: str | None = None,
    read_timeout: int | None = None,
    max_attempts: int | None = None,
) -> dict:
    """client.converse() with credential eviction + a single auth-error retry.

    The converse-API sibling of invoke() (which uses the older invoke_model API
    and cannot carry a separate system prompt). Used by the OS-Eval LLM judge so
    a transient stale-credential moment self-heals instead of zeroing out every
    LLM-judged golden case (90/147 errored in the 2026-06-28 nightly, all the
    same "unable to assume credentials" failure with no recovery).

    Same evict-and-retry-ONCE discipline as invoke(): retry only on the first
    attempt, only for a credential/auth error (`_RETRIABLE_AUTH_KEYWORDS` — the
    single shared predicate), with a freshly-resolved client. A non-auth error
    raises immediately (never masks a real bug, never loops). Bounded at 2 total
    attempts — never a loop (STEERING #1 + PIT03: looping on a poisoned/stale
    client is harmful; the only safe additional retry crosses a process boundary).

    Args:
        messages / system / inference_config: passed verbatim to converse() as
            messages= / system= / inferenceConfig= (boto3 camelCase mapped here).
        model_id: the resolved Bedrock model id (caller pins the judge model).
        region: optional region override forwarded to get_client so the caller's
            own region precedence is preserved (the judge honors AWS_REGION env
            first). None → get_client resolves region from AppConfigManager.
        read_timeout: when set, FAIL-FAST mode — use a THROWAWAY tight-timeout
            client (build_timeout_client) instead of the shared cached one, so a
            hung call fails in read_timeout seconds without stalling on the shared
            120s client. Still does the SAME one auth-error retry (self-heals a
            transient stale credential — the 2026-06-28 incident), but "retry"
            means build a FRESH throwaway client; the shared cache is never
            touched or evicted. Used by the OS-Eval judge (serial ~89-case sweep
            can't afford a 120s×N tail). None (default) → the standard
            cached-client path below, byte-identical to before.
        max_attempts: boto Config retry attempts for the throwaway fail-fast
            client (default 1 when read_timeout is set). Ignored when read_timeout
            is None. This is the boto-internal retry, distinct from the one
            auth-evict retry above.

    Returns:
        The raw converse() response dict (caller extracts output/message/content).

    Raises:
        Exception: if both attempts fail, or on the first non-auth error.
    """
    # FAIL-FAST path: throwaway tight-timeout client (bounds the hang window
    # without touching the shared 120s client skill-proposals depend on). It
    # KEEPS the same one auth-evict-retry discipline as the cached path — but
    # "evict" here just means build a FRESH throwaway client (the shared cache is
    # never involved), so a transient stale-credential moment still self-heals
    # (the 2026-06-28 incident: 90/147 judges errored on one bad cred) while a
    # hung read still fails in read_timeout seconds. Bounded at 2 attempts, never
    # a loop.
    if read_timeout is not None:
        ffa = max_attempts if max_attempts is not None else 1
        for attempt in range(2):
            try:
                client = build_timeout_client(
                    read_timeout=read_timeout, max_attempts=ffa, region=region
                )
                return client.converse(
                    modelId=model_id,
                    messages=messages,
                    system=system,
                    inferenceConfig=inference_config,
                )
            except Exception as e:
                err_str = str(e).lower()
                retriable = any(kw in err_str for kw in _RETRIABLE_AUTH_KEYWORDS)
                if retriable and attempt == 0:
                    logger.warning(
                        "Bedrock judge (fail-fast) auth error, rebuilding throwaway client: %s", e
                    )
                    continue  # fresh throwaway client on next loop; shared cache untouched
                raise
        raise RuntimeError("converse_with_retry (fail-fast) exhausted retries without returning")

    for attempt in range(2):
        try:
            client = get_client(force_new=(attempt > 0), region=region)
            return client.converse(
                modelId=model_id,
                messages=messages,
                system=system,
                inferenceConfig=inference_config,
            )
        except Exception as e:
            err_str = str(e).lower()
            retriable = any(kw in err_str for kw in _RETRIABLE_AUTH_KEYWORDS)
            if retriable and attempt == 0:
                logger.warning(
                    "Bedrock judge auth error (attempt %d), evicting client: %s",
                    attempt + 1, e,
                )
                evict_client()
                continue
            raise
    # Unreachable: range(2) either returns or raises on attempt 1. Guards against
    # a future loop-bound edit silently returning None.
    raise RuntimeError("converse_with_retry exhausted retries without returning")
