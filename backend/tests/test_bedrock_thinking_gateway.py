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
- Key rotation (_CredentialHistory + route over the real _resolve_credentials
  with a fake boto3 chain): the previous key verifies within its 1h TTL and is
  rejected after; unknown keys are rejected; forwarding always uses the CURRENT
  key; the denied log carries only the last 4 AKID chars.

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

    def test_lone_surrogate_does_not_raise(self):
        """Tool output cut mid-emoji leaves "\\ud83d" in the JSON; the rewrite used
        to raise UnicodeEncodeError → a 500 on every request of that session."""
        body = b'{"thinking":{"type":"adaptive"},"messages":[{"content":"x \\ud83d y"}]}'
        out, changed = gw.rewrite_thinking_display(body)
        doc = json.loads(out)
        assert changed and doc["thinking"]["display"] == "summarized"
        assert doc["messages"][0]["content"] == "x \ud83d y"


@pytest.fixture(autouse=True)
def _fresh_credential_state(monkeypatch):
    """Module-global credential caches must not leak between tests."""
    monkeypatch.setattr(gw, "_cred_history", gw._CredentialHistory())
    monkeypatch.setattr(gw, "_boto_session", None)
    monkeypatch.setattr(gw, "_last_refresh", float("-inf"))


OLD_AK, OLD_SK, OLD_TOK = "ASIAOLDKEY000OLD", "old-secret/xyz", "old-token"


def _frozen(ak, sk, tok):
    return Credentials(ak, sk, tok).get_frozen_credentials()


class TestCredentialHistory:
    def test_previous_key_valid_within_ttl_then_expires(self):
        h = gw._CredentialHistory()
        far = 1e12  # previous key's own expiry: far away → the 1h TTL governs
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0, expiry=far)
        h.note(_frozen(AK, SK, TOK), now=100.0)  # rotation: OLD superseded at t=100
        ttl = gw._PREVIOUS_CREDS_TTL_S
        assert h.for_access_key(OLD_AK, now=100.0 + ttl - 1, wall=0.0).secret_key == OLD_SK
        assert h.for_access_key(OLD_AK, now=100.0 + ttl + 1, wall=0.0) is None
        # Expired entry is dropped for good, current unaffected.
        assert h.for_access_key(OLD_AK, now=100.0, wall=0.0) is None
        assert h.for_access_key(AK, now=1e9).secret_key == SK

    def test_previous_key_never_outlives_its_own_expiry(self):
        h = gw._CredentialHistory()
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0, expiry=5_000.0)
        h.note(_frozen(AK, SK, TOK), now=100.0)
        assert h.for_access_key(OLD_AK, now=200.0, wall=4_999.0) is not None
        assert h.for_access_key(OLD_AK, now=200.0, wall=5_000.0) is None  # expired

    def test_unknown_expiry_uses_short_window(self):
        h = gw._CredentialHistory()
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0)  # expiry unknown
        h.note(_frozen(AK, SK, TOK), now=100.0)
        short = gw._PREVIOUS_CREDS_TTL_UNKNOWN_EXPIRY_S
        assert short < gw._PREVIOUS_CREDS_TTL_S
        assert h.for_access_key(OLD_AK, now=100.0 + short - 1) is not None
        assert h.for_access_key(OLD_AK, now=100.0 + short + 1) is None

    def test_renoted_previous_key_does_not_flip_back(self):
        """A resolution returning the PREVIOUS key must not re-stamp it as
        superseded-now (which would keep a superseded key alive forever)."""
        h = gw._CredentialHistory()
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0, expiry=1e12)
        h.note(_frozen(AK, SK, TOK), now=100.0)
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=3000.0)  # stale source re-reports OLD
        ttl = gw._PREVIOUS_CREDS_TTL_S
        assert h.for_access_key(AK, now=3001.0).access_key == AK  # still current
        assert h.for_access_key(OLD_AK, now=100.0 + ttl + 1, wall=0.0) is None

    def test_credential_expiry_reads_refreshable_expiry(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        exp = datetime(2030, 1, 1, tzinfo=timezone.utc)
        assert gw._credential_expiry(SimpleNamespace(_expiry_time=exp)) == exp.timestamp()
        assert gw._credential_expiry(Credentials(AK, SK, TOK)) is None
        assert gw._credential_expiry(None) is None

    def test_non_ascii_token_header_rejected_without_raising(self):
        """verify_sigv4 compared the caller-controlled token header with a str
        compare_digest → TypeError (500) on non-ASCII; now a clean mismatch."""
        ok, reason = gw.verify_sigv4(
            method="POST", raw_path="/model/m/invoke", raw_query="",
            headers={"authorization": (
                f"AWS4-HMAC-SHA256 Credential={AK}/20260101/us-east-1/bedrock/aws4_request, "
                "SignedHeaders=host;x-amz-date;x-amz-security-token, Signature=" + "0" * 64),
                "x-amz-security-token": "t\u00e9", "x-amz-date": "20260101T000000Z",
                "host": "127.0.0.1"},
            body=b"", access_key=AK, secret_key=SK, token=TOK, region="us-east-1",
        )
        assert ok is False and reason == "session_token_mismatch"

    def test_bounded_to_current_and_previous(self):
        h = gw._CredentialHistory()
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0)
        h.note(_frozen(AK, SK, TOK), now=1.0)
        h.note(_frozen("ASIATHIRDKEY0003", "s3", "t3"), now=2.0)
        assert h.for_access_key(OLD_AK, now=3.0) is None   # two rotations ago
        assert h.for_access_key(AK, now=3.0).access_key == AK
        assert h.for_access_key("ASIATHIRDKEY0003", now=3.0) is not None

    def test_same_key_refresh_does_not_supersede(self):
        h = gw._CredentialHistory()
        h.note(_frozen(OLD_AK, OLD_SK, OLD_TOK), now=0.0)
        h.note(_frozen(AK, SK, TOK), now=1.0)
        h.note(_frozen(AK, SK, TOK), now=2.0)  # re-resolve, no rotation
        assert h.for_access_key(OLD_AK, now=3.0).access_key == OLD_AK

    def test_unknown_and_non_ascii_keys_rejected_without_raising(self):
        h = gw._CredentialHistory()
        h.note(_frozen(AK, SK, TOK), now=0.0)
        assert h.for_access_key("ASIANEVERSEEN001", now=1.0) is None
        assert h.for_access_key("ASIA\u00e9\u00e9", now=1.0) is None


class TestCredentialRefreshLimit:
    def test_unknown_key_refreshes_at_most_once_per_window(self, monkeypatch):
        """The key in the (unauthenticated) request must not drive the credential
        chain on every request."""
        calls = []

        class _Sess:
            def get_credentials(self):
                calls.append(1)
                return Credentials(AK, SK, TOK)

        import boto3
        monkeypatch.setattr(boto3, "Session", _Sess)
        monkeypatch.setattr(gw, "_boto_session", None)
        monkeypatch.setattr(gw, "_last_refresh", float("-inf"))
        for _ in range(5):
            gw._resolve_credentials("ASIAFORGED000001")
        assert len(calls) == 6  # first resolve + ONE refresh, then cached


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
        # raw relay: never ask Bedrock for a compressed body
        assert up.headers["accept-encoding"] == "identity"


@pytest.fixture
def rotating_client(monkeypatch):
    """Route with the REAL _resolve_credentials over a fake boto3 chain whose key
    the test rotates (``chain["creds"]``). Daemon has seen OLD; then rotates to
    the current AK — the 2026-10-18 403 unknown_access_key scenario."""
    import boto3

    chain = {"creds": Credentials(OLD_AK, OLD_SK, OLD_TOK), "sessions": 0}

    class _Sess:
        def __init__(self):
            chain["sessions"] += 1

        def get_credentials(self):
            return chain["creds"]

    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return httpx.Response(200, stream=_Chunks([b"ok"]),
                              headers={"content-type": "application/json"})

    monkeypatch.setattr(boto3, "Session", _Sess)
    monkeypatch.setattr(gw, "_http_client", httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    monkeypatch.setattr(gw, "_config_region", lambda: REGION)
    gw._resolve_credentials(OLD_AK)                 # daemon resolves OLD
    chain["creds"] = Credentials(AK, SK, TOK)       # ...then the chain rotates
    app = FastAPI()
    app.include_router(gw.router, prefix=gw.GATEWAY_PREFIX)
    return TestClient(app, base_url=f"http://{HOST}"), seen, chain


def _old_signed():
    return _sign(ak=OLD_AK, sk=OLD_SK, tok=OLD_TOK)


class TestKeyRotation:
    def test_previous_key_accepted_and_forwarded_with_current(self, rotating_client):
        client, seen, chain = rotating_client
        r = client.post(PATH, content=BODY, headers=_old_signed())
        assert r.status_code == 200, r.text
        up = seen[0]
        # Verified with OLD, but forwarded upstream with the CURRENT key + token.
        assert up.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={AK}/")
        assert up.headers["x-amz-security-token"] == TOK
        # Matching the previous key must not re-run the credential chain.
        assert chain["sessions"] == 1

    def test_current_key_still_accepted(self, rotating_client):
        client, seen, _ = rotating_client
        assert client.post(PATH, content=BODY, headers=_sign()).status_code == 200
        assert len(seen) == 1

    def test_previous_key_rejected_after_window(self, rotating_client, caplog):
        client, seen, _ = rotating_client
        assert client.post(PATH, content=BODY, headers=_sign()).status_code == 200  # rotate in
        gw._cred_history._superseded_at -= gw._PREVIOUS_CREDS_TTL_S + 1
        with caplog.at_level("WARNING", logger=gw.logger.name):
            r = client.post(PATH, content=BODY, headers=_old_signed())
        assert r.status_code == 403 and "unknown_access_key" in r.text
        assert len(seen) == 1  # the expired-key request was NOT forwarded
        line = "\n".join(rec.getMessage() for rec in caplog.records)
        assert f"akid=...{OLD_AK[-4:]}" in line
        for secret in (OLD_SK, OLD_TOK, SK, TOK, OLD_AK[:-4]):
            assert secret not in line

    def test_old_key_with_wrong_secret_still_rejected(self, rotating_client):
        client, seen, _ = rotating_client
        h = _sign(ak=OLD_AK, sk="attacker-guess", tok=OLD_TOK)
        r = client.post(PATH, content=BODY, headers=h)
        assert r.status_code == 403 and "bad_signature" in r.text and seen == []

    def test_old_key_with_current_token_rejected(self, rotating_client):
        """Each recorded entry verifies with ITS OWN token, never a mix."""
        client, seen, _ = rotating_client
        h = _sign(ak=OLD_AK, sk=OLD_SK, tok=TOK)
        r = client.post(PATH, content=BODY, headers=h)
        assert r.status_code == 403 and "session_token_mismatch" in r.text and seen == []

    def test_unknown_key_rejected(self, rotating_client):
        client, seen, _ = rotating_client
        h = _sign(ak="ASIANEVERSEEN001", sk="whatever", tok="t")
        r = client.post(PATH, content=BODY, headers=h)
        assert r.status_code == 403 and "unknown_access_key" in r.text and seen == []


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

    def test_an_old_anthropic_flag_is_routed_too(self):
        # chat always runs on Bedrock now (Anthropic-direct is not supported)
        assert self._env({"use_bedrock": False}) == self._env()

    @pytest.mark.parametrize("kw", [
        {"cfg": {"bedrock_thinking_gateway": False}},
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
