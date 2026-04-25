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


def get_client(*, force_new: bool = False) -> Any:
    """Return a cached bedrock-runtime client with pre-resolved credentials.

    Pre-resolves credentials in-process (where PATH is correct), then
    injects them explicitly into the boto3 client. This avoids the
    credential_process resolution at call time which fails in launchd.

    Args:
        force_new: Bypass cache and create a fresh client (useful after
            credential eviction on auth errors).
    """
    global _client, _client_region, _client_created_at
    import time

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
        read_timeout=60,  # Job prompts can be 20K+ chars; 30s too tight
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


def get_model_id(model_key: str = "claude-sonnet-4-6") -> str:
    """Resolve a model key to its Bedrock model ID via AppConfigManager.

    Falls back to the cross-region inference ID if no mapping is found.
    """
    _, model_map = _load_config()
    return model_map.get(model_key, f"us.anthropic.{model_key}")


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

            # Fire-and-forget token recording for background jobs
            try:
                import asyncio
                import database
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(
                        database.db.record_token_usage(
                            session_id=None,
                            source="background_job",
                            input_tokens=input_tok,
                            output_tokens=output_tok,
                            cost_usd=None,
                            model=model_id,
                        )
                    )
            except Exception:
                pass  # fire-and-forget — never break the job

            return text, input_tok, output_tok

        except Exception as e:
            err_str = str(e).lower()
            retriable = any(kw in err_str for kw in (
                "credential", "expired", "token", "unauthorized",
                "accessdenied", "security",
            ))
            if retriable and attempt == 0:
                logger.warning(
                    "Bedrock auth error (attempt %d), evicting client: %s",
                    attempt + 1, e,
                )
                evict_client()
                continue
            raise
