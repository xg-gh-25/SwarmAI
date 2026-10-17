"""Bedrock thinking-display gateway — a loopback shim that makes Opus thinking visible.

WHY: Opus 4.7+ / 4.8 / 5.5 default ``thinking.display`` to "omitted" (empty thinking
blocks). The bundled Claude Code CLI strips ``display`` from Bedrock requests, so
``--thinking-display summarized`` is a no-op on our Bedrock path (upstream
anthropics/claude-code#63358 / #67729 / #79060, unfixed). Bedrock itself honors
``display="summarized"`` (research run_6a41fc99: 0 → 112-171 chars of thinking).

HOW: desktop sessions point the CLI at this route via ``ANTHROPIC_BEDROCK_BASE_URL``
(per-session env, see :func:`gateway_env_for_session`). The CLI still SigV4-signs
every request with the user's own AWS credentials. The route:

1. **Authenticates the caller by verifying that SigV4 signature** against the same
   credentials the daemon resolves (same credential chain; measured equal). A caller
   without the user's AWS secret key gets 403 — and one that HAS it could call
   Bedrock directly, so the route grants nothing new. No secret is minted here.
2. Rewrites ``thinking.display`` to "summarized" when thinking is on (other fields
   untouched; calls without thinking pass through byte-identical).
3. Re-signs for the fixed upstream ``bedrock-runtime.<config region>`` (the request
   cannot choose the host → no SSRF) and streams the response back unbuffered.

Never logs request/response bodies or credential material.

EXIT: temporary shim for an upstream CLI bug. When the CLI sends ``display`` on
Bedrock, set ``bedrock_thinking_gateway: false`` and delete this module + route.

Key symbols:
- ``router`` — FastAPI router (POST ``/bedrock-gw/model/{id}/invoke[-with-response-stream]``)
- ``gateway_env_for_session`` — the env a desktop session's CLI gets (or {})
- ``set_listen_port`` — called at startup with the daemon's port
- ``verify_sigv4`` — pure SigV4 verification (unit-tested)
- ``rewrite_thinking_display`` — pure body rewrite (unit-tested)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

GATEWAY_PREFIX = "/bedrock-gw"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:%\-]{1,256}$")
_ACTIONS = ("invoke", "invoke-with-response-stream")
_MAX_BODY_BYTES = 64 * 1024 * 1024
_MAX_CLOCK_SKEW_S = 300
_AUTH_RE = re.compile(
    r"^AWS4-HMAC-SHA256 Credential=(?P<akid>[^/,\s]+)/(?P<date>\d{8})/(?P<region>[a-z0-9-]+)/"
    r"(?P<service>[a-z0-9-]+)/aws4_request,\s*SignedHeaders=(?P<signed>[a-z0-9;\-]+),"
    r"\s*Signature=(?P<sig>[0-9a-f]{64})$"
)

_listen_port: Optional[int] = None


# ── Session wiring ──────────────────────────────────────────────────────────

def set_listen_port(port: int) -> None:
    """Record the daemon's loopback port (main.py startup). Process-global config,
    not per-session state: every session's CLI talks to the same daemon."""
    global _listen_port
    _listen_port = int(port)


def gateway_env_for_session(
    config: Any, *, channel_context: Optional[dict], thinking_display: Optional[str],
) -> dict[str, str]:
    """Env that routes ONE session's CLI through the gateway, or ``{}``.

    Only for: switch on, Bedrock SigV4 auth (not bearer-token mode, which has no
    signature to verify), desktop sessions that want the summary
    (``thinking_display == "summarized"`` — channel sessions never render it), not
    hive mode (HiveAuthMiddleware would 401 the CLI), and a known listen port.
    Injected via ``ClaudeAgentOptions.env`` so channel / headless spawns never
    inherit it (claude_environment writes process-global os.environ).
    """
    if config is None or _listen_port is None:
        return {}
    if not config.get("bedrock_thinking_gateway", True):
        return {}
    if not config.get("use_bedrock", True) or config.get("auth_method") == "bedrock_api_key":
        return {}
    if channel_context is not None or thinking_display != "summarized":
        return {}
    if os.environ.get("SWARMAI_MODE", "daemon") == "hive":
        return {}
    return {"ANTHROPIC_BEDROCK_BASE_URL": f"http://127.0.0.1:{_listen_port}{GATEWAY_PREFIX}"}


# ── Body rewrite ────────────────────────────────────────────────────────────

def rewrite_thinking_display(body: bytes) -> tuple[bytes, bool]:
    """Set ``thinking.display = "summarized"`` when thinking is on.

    Returns ``(body, changed)``. Anything that is not a JSON object with
    ``thinking.type`` adaptive/enabled is returned byte-identical.
    """
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, False
    thinking = doc.get("thinking") if isinstance(doc, dict) else None
    if not isinstance(thinking, dict) or thinking.get("type") not in ("adaptive", "enabled"):
        return body, False
    if thinking.get("display") == "summarized":
        return body, False
    thinking["display"] = "summarized"
    # ensure_ascii: a lone surrogate (tool output cut mid-emoji, "\ud83d") is legal
    # in JSON but not encodable as UTF-8 — with ensure_ascii=False it raised and
    # every request of that session got a 500. Escaped output is valid JSON.
    try:
        return json.dumps(doc, ensure_ascii=True, separators=(",", ":")).encode("ascii"), True
    except (TypeError, ValueError):
        return body, False


# ── SigV4 verification (standard AWS Signature Version 4, no new secret) ──────

def _canonical_query(raw_query: str) -> str:
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    enc = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in enc)


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k = hmac.new(f"AWS4{secret}".encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def verify_sigv4(
    *, method: str, raw_path: str, raw_query: str, headers: dict[str, str], body: bytes,
    access_key: str, secret_key: str, token: Optional[str], region: str,
    now: Optional[float] = None,
) -> tuple[bool, str]:
    """Verify an incoming SigV4 request was signed with these credentials.

    ``headers`` keys must be lower-case; ``raw_path`` is the path as received on
    the wire. Returns ``(ok, reason)``; ``reason`` is safe to log (no secrets).
    Fail-closed: any parse problem is a rejection.
    """
    m = _AUTH_RE.match(headers.get("authorization", "").strip())
    if not m:
        return False, "missing_or_malformed_authorization"
    if m["service"] != "bedrock" or m["region"] != region:
        return False, "wrong_scope"
    if not hmac.compare_digest(m["akid"], access_key):
        return False, "unknown_access_key"
    signed = m["signed"].split(";")
    if "host" not in signed or "x-amz-date" not in signed:
        return False, "required_headers_unsigned"
    if token and ("x-amz-security-token" not in signed
                  or not hmac.compare_digest(headers.get("x-amz-security-token", ""), token)):
        return False, "session_token_mismatch"
    amz_date = headers.get("x-amz-date", "")
    try:
        ts = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False, "bad_date"
    if amz_date[:8] != m["date"]:
        return False, "date_scope_mismatch"
    if abs((now if now is not None else time.time()) - ts.timestamp()) > _MAX_CLOCK_SKEW_S:
        return False, "stale_signature"
    payload_hash = hashlib.sha256(body).hexdigest()
    declared = headers.get("x-amz-content-sha256")
    if declared is not None and declared != payload_hash:
        return False, "payload_hash_mismatch"
    if any(h not in headers for h in signed):
        return False, "signed_header_absent"
    canon_headers = "".join(f"{h}:{' '.join(headers[h].split())}\n" for h in signed)
    canonical = "\n".join([
        method.upper(), quote(raw_path, safe="/~"), _canonical_query(raw_query),
        canon_headers, m["signed"], payload_hash,
    ])
    scope = f"{m['date']}/{region}/bedrock/aws4_request"
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest(),
    ])
    key = _signing_key(secret_key, m["date"], region, "bedrock")
    expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, m["sig"]):
        return False, "bad_signature"
    return True, "ok"


# ── Credentials + upstream ──────────────────────────────────────────────────

_boto_session: Any = None
_http_client: Any = None
_REFRESH_MIN_S = 30.0
_last_refresh = float("-inf")


def _resolve_credentials(expected_akid: str, refresh: bool = False) -> Any:
    """Frozen credentials from the daemon's boto3 chain (the CLI uses the same
    chain + credential_process cache). Re-resolves once if the CLI's key differs
    (a rotation between the two processes)."""
    global _boto_session
    import boto3

    global _last_refresh
    if _boto_session is None or refresh:
        _boto_session = boto3.Session()
    creds = _boto_session.get_credentials()
    frozen = creds.get_frozen_credentials() if creds else None
    # Re-resolve on an unknown key at most once per _REFRESH_MIN_S: the key comes
    # from the UNAUTHENTICATED request, so without the limit any local process
    # could make every request re-run the credential chain (credential_process).
    if (frozen is not None and frozen.access_key != expected_akid and not refresh
            and time.monotonic() - _last_refresh >= _REFRESH_MIN_S):
        _last_refresh = time.monotonic()
        return _resolve_credentials(expected_akid, refresh=True)
    return frozen


def _config_region() -> str:
    from core.app_config_manager import AppConfigManager

    return AppConfigManager.instance().get("aws_region", "us-east-1") or "us-east-1"


def _client() -> Any:
    global _http_client
    import httpx

    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=10.0),
            follow_redirects=False,
        )
    return _http_client


def _signed_upstream_request(region: str, path: str, body: bytes, in_headers: dict, creds: Any):
    """Build + SigV4-sign the forward request. Host is fixed by config region."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    # identity: bytes are relayed raw (aiter_raw) and Content-Encoding is not
    # passed back, so a gzip reply would reach the CLI as undecodable bytes.
    headers = {"content-type": in_headers.get("content-type", "application/json"),
               "accept-encoding": "identity"}
    for k, v in in_headers.items():
        if k == "accept" or k.startswith("x-amzn-bedrock-"):
            headers[k] = v
    req = AWSRequest(method="POST", url=f"https://bedrock-runtime.{region}.amazonaws.com{path}",
                     data=body, headers=headers)
    SigV4Auth(creds, "bedrock", region).add_auth(req)
    return req.url, dict(req.headers.items())


def _deny(status: int, reason: str) -> JSONResponse:
    return JSONResponse({"message": f"bedrock thinking gateway: {reason}"}, status_code=status)


# ── Route ───────────────────────────────────────────────────────────────────

router = APIRouter()
_PASS_RESPONSE_HEADERS = ("content-type", "x-amzn-requestid", "x-amzn-errortype")


@router.post("/model/{model_id}/{action}", include_in_schema=False)
async def bedrock_gateway(model_id: str, action: str, request: Request):
    """Verify the CLI's SigV4, rewrite thinking.display, re-sign, stream back."""
    import asyncio

    t0 = time.time()
    if action not in _ACTIONS or not _MODEL_ID_RE.match(model_id):
        return _deny(403, "path not allowed")
    raw_path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    raw_query = request.scope.get("query_string", b"").decode("latin-1")
    if not raw_path.startswith(GATEWAY_PREFIX + "/model/"):
        return _deny(403, "path not allowed")
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        return _deny(413, "request too large")
    headers = {k.lower(): v for k, v in request.headers.items()}
    region = _config_region()
    akid_m = _AUTH_RE.match(headers.get("authorization", "").strip())
    if not akid_m:
        return _deny(403, "missing_or_malformed_authorization")
    try:
        creds = await asyncio.to_thread(_resolve_credentials, akid_m["akid"])
    except Exception as exc:  # noqa: BLE001 — credential chain failure → generic deny
        logger.warning("bedrock_gw.credentials_unavailable: %s", type(exc).__name__)
        return _deny(503, "credentials unavailable")
    if creds is None:
        return _deny(503, "credentials unavailable")
    ok, reason = verify_sigv4(
        method=request.method, raw_path=raw_path, raw_query=raw_query, headers=headers,
        body=body, access_key=creds.access_key, secret_key=creds.secret_key,
        token=creds.token, region=region,
    )
    if not ok:
        logger.warning("bedrock_gw.denied model=%s reason=%s", model_id, reason)
        return _deny(403, reason)

    new_body, injected = rewrite_thinking_display(body)
    upstream_path = raw_path[len(GATEWAY_PREFIX):]
    url, up_headers = _signed_upstream_request(region, upstream_path, new_body, headers, creds)
    client = _client()
    try:
        resp = await client.send(
            client.build_request("POST", url, content=new_body, headers=up_headers), stream=True)
    except Exception as exc:  # noqa: BLE001 — upstream transport error → 502, no detail
        logger.warning("bedrock_gw.upstream_error model=%s err=%s", model_id, type(exc).__name__)
        return _deny(502, "upstream unreachable")

    async def _relay():
        total = 0
        try:
            async for chunk in resp.aiter_raw():
                total += len(chunk)
                yield chunk
        finally:
            await resp.aclose()
            logger.info("bedrock_gw.forwarded model=%s action=%s status=%d injected=%s "
                        "bytes=%d dur=%.1fs", model_id, action, resp.status_code, injected,
                        total, time.time() - t0)

    out_headers = {k: v for k, v in resp.headers.items()
                   if k.lower() in _PASS_RESPONSE_HEADERS or k.lower().startswith("x-amzn-bedrock-")}
    return StreamingResponse(_relay(), status_code=resp.status_code, headers=out_headers,
                             media_type=resp.headers.get("content-type"))
