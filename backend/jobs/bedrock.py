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

A model's answer goes through ``structured()`` (an object shaped by a JSON schema,
the format guaranteed by the API) or ``text()`` (plain text); both report a reply cut
at max_tokens instead of returning it (design
docs/design/2026-10-28-structured-model-output.md)::

    from jobs.bedrock import get_model_id, structured

    answer = structured(prompt, schema=SCHEMA, model_id=get_model_id(), max_tokens=1000)
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger("swarm.jobs.bedrock")

CONNECT_TIMEOUT = 10
# Added to the configured timeouts before a fail-fast call is abandoned (_wall_bound).
WALL_GRACE = 30


def _wall_bound(read_timeout: float, attempts: int) -> float:
    """The longest a fail-fast call can take with the timeouts it was built with."""
    return (CONNECT_TIMEOUT + read_timeout) * max(1, attempts) + WALL_GRACE


def _bounded(call: Any, bound: float) -> Any:
    """Run *call* and give up after *bound* seconds. A CDF judge call sat in an SSL read
    for 90 minutes with read_timeout=25 configured, and the worker thread with it
    (2026-10-29): the socket timeout is not the whole guarantee. The abandoned call's
    thread is a daemon and ends when its socket does."""
    import threading

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    worker = threading.Thread(target=run, name="bedrock-call", daemon=True)
    worker.start()
    worker.join(bound)
    if worker.is_alive():
        logger.error("Bedrock call still blocked after %.0fs, past its own timeouts; "
                     "abandoned (its thread ends with its socket)", bound)
        raise TimeoutError(f"Bedrock call exceeded {bound:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]

# Module-level cached client — reused across calls within the same process.
# TTL prevents stale credentials from causing persistent auth failures.
_client: Any | None = None
_client_region: str | None = None
_client_created_at: float = 0.0  # monotonic timestamp
_CLIENT_TTL: float = 1800.0  # 30 minutes — STS temporary creds have 1-12h TTL

# Substrings (lowercased) that mark a bedrock error as a transient credential/auth
# failure worth ONE evict-and-retry. Single source of truth for converse_with_retry()'s
# two paths (cached client and fail-fast client), so the predicate cannot drift.
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
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=read_timeout,
    )
    creds = _resolve_credentials()
    # its own session: boto3's default one is not thread-safe, and the CDF worker, the
    # session-job thread and the run lane build these clients at the same time
    session = boto3.session.Session()
    if creds:
        return session.client("bedrock-runtime", region_name=region, config=boto_config, **creds)
    return session.client("bedrock-runtime", region_name=region, config=boto_config)


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


def _converse_kwargs(model_id: str, messages: list, system: list,
                     inference_config: dict, tool_config: dict | None) -> dict:
    kw: dict = {"modelId": model_id, "messages": messages, "inferenceConfig": inference_config}
    if system:
        kw["system"] = system
    if tool_config is not None:
        kw["toolConfig"] = tool_config
    return kw


def converse_with_retry(
    *,
    messages: list,
    system: list,
    inference_config: dict,
    model_id: str,
    region: str | None = None,
    read_timeout: int | None = None,
    max_attempts: int | None = None,
    tool_config: dict | None = None,
) -> dict:
    """client.converse() with credential eviction + a single auth-error retry.

    The transport under structured() and text(), so every model answer gets it: a
    transient stale-credential moment self-heals instead of zeroing out every
    LLM-judged golden case (90/147 errored in the 2026-06-28 nightly, all the
    same "unable to assume credentials" failure with no recovery).

    Evict-and-retry ONCE: retry only on the first
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
        tool_config: passed as toolConfig= when given (``structured()`` forces one
            tool this way); omitted otherwise. An empty *system* list is omitted too.

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
                kwargs = _converse_kwargs(model_id, messages, system, inference_config, tool_config)
                return _bounded(lambda: client.converse(**kwargs), _wall_bound(read_timeout, ffa))
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
            return client.converse(**_converse_kwargs(
                model_id, messages, system, inference_config, tool_config))
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


# ── A model's answer: the one entry (design docs/design/2026-10-28-structured-model-output.md)
#
# Every backend call that wants a structured answer from a model goes through
# structured(); every call that wants plain text goes through text(). The format of
# a structured answer is guaranteed by the API (Converse with one forced tool whose
# input schema is the answer's schema), not asked for in the prompt, and a reply cut
# by max_tokens is reported as such instead of reaching a parser as broken text.

_RAW_KEEP = 2000  # characters of the reply an error carries, for diagnosis

# The subset of JSON Schema the answers use. A schema with any other keyword is
# refused when the call is made, so a constraint is never silently left unchecked.
_SCHEMA_KEYS = {"type", "properties", "required", "enum", "items", "description"}
_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean"}


class StructuredOutputError(Exception):
    """A model reply that is not a usable answer.

    ``kind`` is one of:
      - ``truncated``: the reply reached max_tokens (the limit is too low for it);
      - ``missing``: no answer in the reply (no tool call, or empty text);
      - ``invalid``: the answer does not match its schema.
    ``raw`` is the start of the reply, kept for diagnosis.
    """

    KINDS = ("truncated", "missing", "invalid")

    def __init__(self, kind: str, raw: str = "", detail: str = ""):
        if kind not in self.KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        self.kind = kind
        self.raw = (raw or "")[:_RAW_KEEP]
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


def _check_schema(schema: dict, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: a schema is an object")
    extra = set(schema) - _SCHEMA_KEYS
    if extra:
        raise ValueError(f"{path}: unsupported schema keywords {sorted(extra)}")
    t = schema.get("type")
    if not isinstance(t, str) or t not in _SCHEMA_TYPES:
        raise ValueError(f"{path}: type must be one of {sorted(_SCHEMA_TYPES)}")
    for key, kind in (("properties", dict), ("required", list), ("enum", list)):
        if key in schema and not isinstance(schema[key], kind):
            raise ValueError(f"{path}: {key} must be a {kind.__name__}")
    for name, sub in (schema.get("properties") or {}).items():
        _check_schema(sub, f"{path}.{name}")
    if "items" in schema:
        _check_schema(schema["items"], f"{path}[]")


_INT_TEXT = re.compile(r"[+-]?\d+")
_NUMBER_TEXT = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


_STRUCTURAL_AFTER = frozenset(',:}]')


def _repair_inner_quotes(text: str) -> str:
    """*text* with every double quote inside a string value escaped, when the writer
    left it raw (``"why": "遵循"不碰历史数据"的规则"``). A quote closes a string only when
    the next non-space character is structural (``, : } ]``) or the text ends there."""
    out: list[str] = []
    in_string = escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                rest = text[i + 1:].lstrip()
                if rest and rest[0] not in _STRUCTURAL_AFTER:
                    out.append('\\"')
                    continue
                in_string = False
        elif ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def _decode_text_json(value: str, kind: str) -> Any:
    """A list or object the model sent as JSON text instead of a value (it does this
    when a string field holds a straight double quote, measured 2026-10-29: 5/5
    extractions with such text). Decoded as written, else with its inner quotes
    escaped; anything that is still not a *kind* is returned unchanged for the type
    check to refuse."""
    import json as _json
    for candidate in (value, _repair_inner_quotes(value)):
        try:
            decoded = _json.loads(candidate)
        except ValueError:
            continue
        if isinstance(decoded, list if kind == "array" else dict):
            return decoded
    return value


def _validate(value: Any, schema: dict, path: str = "$") -> Any:
    """*value* checked against *schema* (the subset above). A list or object sent as
    JSON text is decoded (``_decode_text_json``); a number written as
    decimal text ("0.95") is converted, and an integer may be written "3" or 3.0; a
    number must be finite, so NaN, Infinity and an overflowing "1e400" are refused
    (a NaN score would read as a perfect one). Raises ValueError naming the path."""
    t = schema["type"]
    if t in ("number", "integer") and isinstance(value, str):
        text = value.strip()
        if not (_INT_TEXT if t == "integer" else _NUMBER_TEXT).fullmatch(text):
            raise ValueError(f"{path}: expected {t}, got {value[:40]!r}")
        value = int(text) if t == "integer" else float(text)
    if t in ("array", "object") and isinstance(value, str):
        value = _decode_text_json(value, t)
    if t == "integer" and isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }[t]
    if not ok:
        raise ValueError(f"{path}: expected a finite {t}, got {value!r:.40}"
                         if t == "number" else f"{path}: expected {t}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: {value!r} is not one of {schema['enum']}")
    if t == "object":
        missing = [k for k in schema.get("required", []) if k not in value]
        if missing:
            raise ValueError(f"{path}: missing {missing}")
        props = schema.get("properties") or {}
        return {k: (_validate(v, props[k], f"{path}.{k}") if k in props else v)
                for k, v in value.items()}
    if t == "array" and "items" in schema:
        return [_validate(v, schema["items"], f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def _reply_parts(resp: dict) -> tuple[str, list]:
    blocks = (((resp or {}).get("output") or {}).get("message") or {}).get("content") or []
    return (resp or {}).get("stopReason") or "", blocks


def _record(resp: dict, model_id: str) -> None:
    usage = (resp or {}).get("usage") or {}
    try:
        _record_usage_sync(input_tokens=int(usage.get("inputTokens") or 0),
                           output_tokens=int(usage.get("outputTokens") or 0), model=model_id)
    except Exception:  # noqa: BLE001 — accounting never breaks the call
        logger.debug("token usage not recorded", exc_info=True)


def _call(prompt: str, *, model_id: str, max_tokens: int, system: str, temperature: float,
          region: str | None, read_timeout: int | None, max_attempts: int | None,
          tool_config: dict | None, record_usage: bool,
          usage: dict | None = None) -> tuple[str, list]:
    from model_registry import supports_custom_temperature

    inference: dict = {"maxTokens": max_tokens}
    if supports_custom_temperature(model_id):  # opus 4.7+ reject a custom temperature
        inference["temperature"] = temperature
    resp = converse_with_retry(
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": system}] if system else [],
        inference_config=inference,
        model_id=model_id, region=region, read_timeout=read_timeout,
        max_attempts=max_attempts, tool_config=tool_config,
    )
    if record_usage:
        _record(resp, model_id)
    if usage is not None:
        u = (resp or {}).get("usage") or {}
        usage["input_tokens"] = int(u.get("inputTokens") or 0)
        usage["output_tokens"] = int(u.get("outputTokens") or 0)
    return _reply_parts(resp)


def structured(
    prompt: str,
    *,
    schema: dict,
    model_id: str,
    max_tokens: int,
    system: str = "",
    name: str = "answer",
    description: str = "Record the answer.",
    temperature: float = 0.0,
    region: str | None = None,
    read_timeout: int | None = None,
    max_attempts: int | None = None,
    record_usage: bool = False,
    usage: dict | None = None,
) -> dict:
    """A model's answer to *prompt* as a dict matching *schema* (an object schema).

    The model is made to call one tool, *name*, whose input schema is *schema*, so
    the reply is an object, never text to parse (forced where the model allows it,
    model_registry.supports_forced_tool_choice; otherwise the model is told to).
    *temperature* is sent only to a model that accepts one
    (model_registry.supports_custom_temperature). Put a free-text field such as
    ``reasoning`` first in a verdict schema: the model writes its analysis there
    instead of before the answer. Raises StructuredOutputError (truncated / missing /
    invalid); a Bedrock error propagates as from converse_with_retry. *record_usage*
    records token usage the way the background jobs always have; a dict passed as
    *usage* is filled with this call's input_tokens and output_tokens.
    """
    from model_registry import supports_forced_tool_choice

    _check_schema(schema)
    if schema["type"] != "object":
        raise ValueError("an answer schema is an object")
    tool = {"toolSpec": {"name": name, "description": description,
                         "inputSchema": {"json": schema}}}
    if supports_forced_tool_choice(model_id):
        choice: dict = {"tool": {"name": name}}
    else:
        # A model that rejects a forced choice picks the tool itself; it is told to,
        # and a reply without the call is "missing" as on any other model.
        choice = {"auto": {}}
        system = (system + "\n\n" if system else "") + f"Answer only by calling the {name} tool."
    stop, blocks = _call(prompt, model_id=model_id, max_tokens=max_tokens, system=system,
                         temperature=temperature, region=region, read_timeout=read_timeout,
                         max_attempts=max_attempts, record_usage=record_usage, usage=usage,
                         tool_config={"tools": [tool], "toolChoice": choice})
    import json as _json
    raw = _json.dumps(blocks, ensure_ascii=False, default=str)
    if stop == "max_tokens":
        raise StructuredOutputError("truncated", raw, f"reply reached max_tokens={max_tokens}")
    use = next((b["toolUse"] for b in blocks
                if isinstance(b, dict) and isinstance(b.get("toolUse"), dict)
                and b["toolUse"].get("name") == name), None)
    if use is None or not isinstance(use.get("input"), dict):
        raise StructuredOutputError("missing", raw, f"no call to {name}")
    try:
        return _validate(use["input"], schema)
    except ValueError as e:
        raise StructuredOutputError("invalid", raw, str(e)) from None


def text(
    prompt: str,
    *,
    model_id: str,
    max_tokens: int,
    system: str = "",
    temperature: float = 0.0,
    region: str | None = None,
    read_timeout: int | None = None,
    max_attempts: int | None = None,
    record_usage: bool = False,
    usage: dict | None = None,
) -> str:
    """A model's plain-text answer to *prompt*. A reply cut by max_tokens raises
    StructuredOutputError("truncated") instead of being returned as if complete; an
    empty reply raises ("missing"). *record_usage* and *usage* as in structured()."""
    stop, blocks = _call(prompt, model_id=model_id, max_tokens=max_tokens, system=system,
                         temperature=temperature, region=region, read_timeout=read_timeout,
                         max_attempts=max_attempts, tool_config=None, record_usage=record_usage,
                         usage=usage)
    out = "".join(b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
    if stop == "max_tokens":
        raise StructuredOutputError("truncated", out, f"reply reached max_tokens={max_tokens}")
    if not out.strip():
        raise StructuredOutputError("missing", out, "empty reply")
    return out
