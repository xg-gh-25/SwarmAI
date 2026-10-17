"""Tests for the Bedrock thinking-display gateway (core/bedrock_thinking_gateway.py).

What is tested:
- verify_sigv4: accepts a request signed by botocore with the right credentials;
  rejects every tampering (body, path, signed header, key, token, scope, age,
  missing auth). This IS the route's authentication, so each reject is pinned.
- rewrite_thinking_display: only adaptive/enabled thinking gets display=summarized;
  everything else is byte-identical.
- The route (real FastAPI app + TestClient, upstream = httpx MockTransport):
  unsigned/forged → 403 and nothing forwarded; a signed request is forwarded to
  the FIXED bedrock-runtime.<config region> host, re-signed, with the rewritten
  body, and the response streams back.
- gateway_env_for_session: only desktop, SigV4-Bedrock, summarized, non-hive.

Methodology: botocore signs exactly like the CLI does (SigV4); the CLI-compat of
the canonical form is covered by the real-CLI E2E run recorded in the commit.
"""
from __future__ import annotations

import json

import httpx
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import bedrock_thinking_gateway as gw

AK, SK, TOK, REGION = "ASIATESTKEY0001", "secret/abc+123", "session-token-xyz", "us-east-1"
HOST = "127.0.0.1:18321"
PATH = "/bedrock-gw/model/us.anthropic.claude-opus-5-5/invoke-with-response-stream"
BODY = json.dumps({"thinking": {"type": "adaptive"}, "messages": [{"role": "user", "content": "hi"}]}).encode()


def _sign(body=BODY, path=PATH, ak=AK, sk=SK, tok=TOK, region=REGION):
    req = AWSRequest(method="POST", url=f"http://{HOST}{path}", data=body,
                     headers={"content-type": "application/json"})
    SigV4Auth(Credentials(ak, sk, tok), "bedrock", region).add_auth(req)
    headers = {k.lower(): v for k, v in req.headers.items()}
    headers["host"] = HOST
    return headers


def _verify(headers, body=BODY, path=PATH, **kw):
    args = dict(method="POST", raw_path=path, raw_query="", headers=headers, body=body,
                access_key=AK, secret_key=SK, token=TOK, region=REGION)
    args.update(kw)
    return gw.verify_sigv4(**args)


class TestVerifySigV4:
    def test_valid_signature_accepted(self):
        assert _verify(_sign()) == (True, "ok")

    def test_tampered_body_rejected(self):
        assert _verify(_sign(), body=BODY + b" ")[0] is False

    def test_tampered_path_rejected(self):
        other = PATH.replace("opus-5-5", "haiku-4-5")
        assert _verify(_sign(), path=other) == (False, "bad_signature")

    def test_tampered_signed_header_rejected(self):
        h = _sign()
        h["content-type"] = "text/plain"
        assert _verify(h) == (False, "bad_signature")

    def test_wrong_secret_rejected(self):
        assert _verify(_sign(sk="other-secret")) == (False, "bad_signature")

    def test_other_access_key_rejected(self):
        assert _verify(_sign(ak="ASIAOTHERKEY0002")) == (False, "unknown_access_key")

    def test_session_token_mismatch_rejected(self):
        assert _verify(_sign(tok="stolen-token")) == (False, "session_token_mismatch")

    def test_wrong_region_rejected(self):
        assert _verify(_sign(region="us-west-2")) == (False, "wrong_scope")

    def test_stale_signature_rejected(self):
        h = _sign()
        assert _verify(h, now=__import__("time").time() + 3600) == (False, "stale_signature")

    def test_missing_authorization_rejected(self):
        h = _sign()
        del h["authorization"]
        assert _verify(h) == (False, "missing_or_malformed_authorization")


class TestRewrite:
    @pytest.mark.parametrize("ttype", ["adaptive", "enabled"])
    def test_thinking_on_gets_summarized(self, ttype):
        out, changed = gw.rewrite_thinking_display(json.dumps({"thinking": {"type": ttype}}).encode())
        assert changed and json.loads(out)["thinking"] == {"type": ttype, "display": "summarized"}

    @pytest.mark.parametrize("body", [
        json.dumps({"messages": []}).encode(),                          # aux call, no thinking
        json.dumps({"thinking": {"type": "disabled"}}).encode(),
        json.dumps({"thinking": {"type": "adaptive", "display": "summarized"}}).encode(),
        b"not json",
    ])
    def test_everything_else_byte_identical(self, body):
        assert gw.rewrite_thinking_display(body) == (body, False)


class _Chunks(httpx.AsyncByteStream):
    """An unread, chunked upstream body (like Bedrock's eventstream)."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for c in self._chunks:
            yield c


@pytest.fixture
def app_client(monkeypatch):
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return httpx.Response(200, stream=_Chunks([b"\x00frame-1", b"\x00frame-2"]),
                              headers={"content-type": "application/vnd.amazon.eventstream",
                                       "x-amzn-requestid": "rid-1"})

    monkeypatch.setattr(gw, "_http_client", httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    monkeypatch.setattr(gw, "_resolve_credentials", lambda akid: Credentials(AK, SK, TOK).get_frozen_credentials())
    monkeypatch.setattr(gw, "_config_region", lambda: REGION)
    app = FastAPI()
    app.include_router(gw.router, prefix=gw.GATEWAY_PREFIX)
    return TestClient(app, base_url=f"http://{HOST}"), seen


class TestRoute:
    def test_unsigned_request_denied_and_not_forwarded(self, app_client):
        client, seen = app_client
        r = client.post(PATH, content=BODY)
        assert r.status_code == 403 and seen == []

    def test_forged_signature_denied_and_not_forwarded(self, app_client):
        client, seen = app_client
        h = _sign(sk="attacker-guess")
        r = client.post(PATH, content=BODY, headers=h)
        assert r.status_code == 403 and seen == []

    def test_disallowed_action_denied(self, app_client):
        client, seen = app_client
        path = "/bedrock-gw/model/us.anthropic.claude-opus-5-5/converse"
        r = client.post(path, content=BODY, headers=_sign(path=path))
        assert r.status_code == 403 and seen == []

    def test_non_post_not_routed(self, app_client):
        client, seen = app_client
        assert client.get(PATH).status_code == 405 and seen == []

    def test_signed_request_forwarded_rewritten_resigned_to_fixed_host(self, app_client):
        client, seen = app_client
        r = client.post(PATH, content=BODY, headers=_sign())
        assert r.status_code == 200
        assert r.content == b"\x00frame-1\x00frame-2"
        assert r.headers["x-amzn-requestid"] == "rid-1"
        up = seen[0]
        assert up.url.host == f"bedrock-runtime.{REGION}.amazonaws.com"
        assert up.url.path == "/model/us.anthropic.claude-opus-5-5/invoke-with-response-stream"
        assert json.loads(up.content)["thinking"]["display"] == "summarized"
        assert up.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={AK}/")
        assert up.headers["authorization"] != _sign()["authorization"]  # re-signed for the new body


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class TestSessionEnv:
    @pytest.fixture(autouse=True)
    def _port(self, monkeypatch):
        monkeypatch.setattr(gw, "_listen_port", 18321)
        monkeypatch.delenv("SWARMAI_MODE", raising=False)

    def _env(self, cfg=None, channel=None, display="summarized"):
        return gw.gateway_env_for_session(_Cfg(cfg or {}), channel_context=channel,
                                          thinking_display=display)

    def test_desktop_bedrock_session_routed(self):
        assert self._env() == {"ANTHROPIC_BEDROCK_BASE_URL": "http://127.0.0.1:18321/bedrock-gw"}

    @pytest.mark.parametrize("kw", [
        {"cfg": {"bedrock_thinking_gateway": False}},
        {"cfg": {"use_bedrock": False}},
        {"cfg": {"auth_method": "bedrock_api_key"}},
        {"channel": {"channel_id": "c1"}},
        {"display": None},
    ])
    def test_not_routed(self, kw):
        assert self._env(**kw) == {}

    def test_hive_not_routed(self, monkeypatch):
        monkeypatch.setenv("SWARMAI_MODE", "hive")
        assert self._env() == {}

    def test_unknown_port_not_routed(self, monkeypatch):
        monkeypatch.setattr(gw, "_listen_port", None)
        assert self._env() == {}


async def _options(channel_context=None):
    from unittest.mock import MagicMock
    from core.prompt_builder import PromptBuilder

    cfg = MagicMock()
    values = {"thinking_mode": "adaptive", "thinking_effort": "high", "use_bedrock": True}
    cfg.get = MagicMock(side_effect=lambda k, d=None: values.get(k, d))
    cfg.get_model = MagicMock(return_value="claude-opus-5-5")
    return await PromptBuilder(cfg).build_options(
        agent_config={"model": "claude-opus-5-5"}, enable_skills=False, enable_mcp=False,
        resume_session_id=None, session_context={"sdk_session_id": "sid"},
        channel_context=channel_context, editor_context=None, terminal_context=None,
        cached_system_prompt="CACHED",
    )


class TestBuildOptionsWiring:
    @pytest.fixture(autouse=True)
    def _port(self, monkeypatch):
        monkeypatch.setattr(gw, "_listen_port", 18321)
        monkeypatch.delenv("SWARMAI_MODE", raising=False)

    @pytest.mark.asyncio
    async def test_desktop_session_env_points_cli_at_gateway(self):
        opts = await _options()
        assert opts.env["ANTHROPIC_BEDROCK_BASE_URL"] == "http://127.0.0.1:18321/bedrock-gw"
        assert "CLAUDE_CODE_SKIP_BEDROCK_AUTH" not in opts.env  # the CLI must keep signing

    @pytest.mark.asyncio
    async def test_channel_session_env_untouched(self):
        opts = await _options(channel_context={"channel_id": "c1", "channel_type": "slack"})
        assert "ANTHROPIC_BEDROCK_BASE_URL" not in opts.env
