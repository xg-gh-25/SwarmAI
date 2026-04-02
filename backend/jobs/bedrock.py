"""
Shared Bedrock client for job handlers.

All background job handlers that call Bedrock LLMs MUST use this module
instead of creating raw ``boto3.client("bedrock-runtime")`` inline.

Why: the raw pattern fails in launchd context because credential_process
(ada → Isengard) DNS resolution fails. This module uses the same credential
path as the SwarmAI app — reading region from AppConfigManager and applying
proper BotoConfig (timeouts, retries, credential eviction on auth errors).

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
_client: Any | None = None
_client_region: str | None = None


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


def get_client(*, force_new: bool = False) -> Any:
    """Return a cached bedrock-runtime client with proper config.

    Uses the same credential chain as the SwarmAI app (AppConfigManager
    region, boto3 default chain with SSO token support).

    Args:
        force_new: Bypass cache and create a fresh client (useful after
            credential eviction on auth errors).
    """
    global _client, _client_region

    region, _ = _load_config()

    if not force_new and _client is not None and _client_region == region:
        return _client

    import boto3
    from botocore.config import Config as BotoConfig

    boto_config = BotoConfig(
        retries={"max_attempts": 2, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=60,  # Job prompts can be 20K+ chars; 30s too tight
    )
    _client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=boto_config,
    )
    _client_region = region
    logger.debug("Created Bedrock client for region=%s", region)
    return _client


def evict_client() -> None:
    """Drop the cached client, forcing re-creation on next call.

    Call this when you get a credential/auth error so the next attempt
    picks up refreshed credentials.
    """
    global _client, _client_region
    _client = None
    _client_region = None


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
