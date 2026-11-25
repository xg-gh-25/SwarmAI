"""_verify_bedrock against a local stub Bedrock endpoint: which credentials really go out.

botocore reads AWS_BEARER_TOKEN_BEDROCK, and chooses bearer over sigv4, when it signs
each request, not when the client is built. So these tests look at the request that
reaches the wire (the Authorization header the stub receives) and at os.environ before,
during and after the call, instead of at what the environment held at construction.

The stub serves HTTPS with a certificate made for the test (trusted through
AWS_CA_BUNDLE): botocore sends a bearer token only to an HTTPS endpoint (since 1.44).
"""
from __future__ import annotations

import asyncio
import datetime
import http.server
import ipaddress
import json
import os
import ssl
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

_KEY = "AWS_BEARER_TOKEN_BEDROCK"
_BODY = json.dumps({
    "content": [{"type": "text", "text": "hi"}],
    "usage": {"input_tokens": 8, "output_tokens": 1},
}).encode()


def _self_signed(tmp: Path) -> tuple[Path, Path]:
    """A certificate and key for 127.0.0.1, written under *tmp*."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp / "stub-cert.pem", tmp / "stub-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class _Stub:
    """A one-route HTTPS server standing in for bedrock-runtime InvokeModel."""

    def __init__(self, tmp: Path) -> None:
        self.requests: list[dict] = []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server API
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                stub.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    # what a concurrent reader of the process env would see mid-call
                    "env_token": os.environ.get(_KEY),
                })
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_BODY)))
                self.end_headers()
                self.wfile.write(_BODY)

            def do_GET(self):  # noqa: N802 — bedrock control plane (ListFoundationModels)
                stub.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "env_token": os.environ.get(_KEY),
                })
                body = json.dumps({"modelSummaries": [{
                    "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/m",
                    "modelId": "anthropic.claude-stub-v1", "providerName": "Anthropic"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.cert, key = _self_signed(tmp)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, key)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.url = f"https://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "_Stub":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """Point every bedrock-runtime client at the stub; no real AWS config or IMDS."""
    with _Stub(tmp_path) as s:
        monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", s.url)
        monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK", s.url)
        monkeypatch.setenv("AWS_CA_BUNDLE", str(s.cert))   # trust the stub's certificate
        monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
        for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"):
            monkeypatch.delenv(var, raising=False)
        yield s


def _config(auth_method: str, token: str | None) -> dict:
    return {
        "use_bedrock": True, "aws_region": "us-east-1",
        "default_model": "claude-opus-4-6", "bedrock_model_map": None,
        "auth_method": auth_method, "aws_bearer_token_bedrock": token,
    }


def _verify(config: dict, *, hold_env_lock: bool = False) -> tuple[dict, list]:
    """Run _verify_bedrock; with *hold_env_lock*, someone else holds the env lock the
    whole time (a fresh lock patched in, so the module-level one is never bound to this
    test's event loop): a verify that waits on it hits the timeout."""
    from core import usage_accounting
    from routers.system import _verify_bedrock

    rows: list = []

    async def run() -> dict:
        lock = asyncio.Lock()
        if hold_env_lock:
            await lock.acquire()
        with patch("core.claude_environment._env_lock", lock):
            return await asyncio.wait_for(_verify_bedrock(config), timeout=8)

    with patch.object(usage_accounting, "record_sync", side_effect=rows.extend):
        result = asyncio.run(run())
    return result, rows


def test_api_key_verify_sends_the_given_token_when_env_has_none(stub, monkeypatch):
    """The first verify in Settings / onboarding: the key is not in os.environ yet."""
    monkeypatch.delenv(_KEY, raising=False)

    result, rows = _verify(_config("bedrock_api_key", "fm-token-under-test"))

    assert result["success"] is True, result
    assert len(stub.requests) == 1
    req = stub.requests[0]
    assert req["authorization"] == "Bearer fm-token-under-test"
    assert "/invoke" in req["path"]
    assert req["env_token"] is None, "the token must not be in os.environ during the call"
    assert _KEY not in os.environ
    assert len(rows) == 1  # the probe is still in the usage ledger


def test_api_key_verify_uses_its_own_token_over_an_env_one_and_leaves_env_alone(
        stub, monkeypatch):
    """A user export or a spawn-injected token is neither used nor modified."""
    monkeypatch.setenv(_KEY, "token-already-in-env")

    result, _ = _verify(_config("bedrock_api_key", "fm-token-under-test"))

    assert result["success"] is True, result
    assert [r["authorization"] for r in stub.requests] == ["Bearer fm-token-under-test"]
    assert stub.requests[0]["env_token"] == "token-already-in-env"
    assert os.environ.get(_KEY) == "token-already-in-env"


@pytest.mark.parametrize("method", ["ada", "sso", "iam_role"])
def test_sigv4_verify_never_uses_a_bearer_token(stub, monkeypatch, method):
    """A leftover env token and a persisted one are both ignored: sigv4 is what is tested,
    even with the env token present for the whole call (a concurrent spawn's)."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDSTUBEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "stub-secret")
    monkeypatch.setenv(_KEY, "leftover-from-a-spawn")

    result, _ = _verify(_config(method, "persisted-but-not-this-method"))

    assert result["success"] is True, result
    assert len(stub.requests) == 1
    auth = stub.requests[0]["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDSTUBEXAMPLE/"), auth
    assert os.environ.get(_KEY) == "leftover-from-a-spawn"


@pytest.mark.parametrize("method,token", [("bedrock_api_key", "fm-token-under-test"),
                                          ("sso", None)])
def test_verify_needs_no_env_lock_and_writes_no_env(stub, monkeypatch, method, token):
    """The verify touches no process env, so it never waits on the spawn path's lock
    (a chat spawn holding it does not stall Settings, and the verify never holds it
    across the network call) and leaves os.environ exactly as it found it."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDSTUBEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "stub-secret")
    monkeypatch.delenv(_KEY, raising=False)
    before = dict(os.environ)

    result, _ = _verify(_config(method, token), hold_env_lock=True)

    assert result["success"] is True, result
    assert dict(os.environ) == before


def test_job_sigv4_clients_never_use_an_env_bearer_token(stub, monkeypatch):
    """The other sigv4 bedrock-runtime clients (jobs on the SwarmAI account or on
    pre-resolved credentials, the account page's capability probe) sign sigv4 while a
    bearer token sits in os.environ: botocore would otherwise pick bearer per request."""
    import boto3
    from core import aws_account
    from jobs import bedrock as jb

    monkeypatch.setenv(_KEY, "token-in-env")
    creds = {"aws_access_key_id": "AKIDSTUBEXAMPLE", "aws_secret_access_key": "stub-secret"}
    monkeypatch.setattr(jb, "_bedrock_key", lambda: None)
    monkeypatch.setattr(jb, "_resolve_credentials", lambda: dict(creds))

    def account_session(region_name=None):
        return boto3.session.Session(region_name=region_name or "us-east-1", **creds)

    def invoke(client):
        client.invoke_model(modelId="m", body=b"{}", contentType="application/json")

    try:
        # no SwarmAI account: pre-resolved credentials
        monkeypatch.setattr(aws_account, "active", lambda: False)
        invoke(jb.get_client(force_new=True, region="us-east-1"))
        invoke(jb.build_timeout_client(read_timeout=5, max_attempts=1, region="us-east-1"))
        # the pinned SwarmAI account
        monkeypatch.setattr(aws_account, "active", lambda: True)
        monkeypatch.setattr(aws_account, "boto_session", account_session)
        invoke(jb.get_client(force_new=True, region="us-east-1"))
        invoke(jb.build_timeout_client(read_timeout=5, max_attempts=1, region="us-east-1"))
    finally:
        jb.evict_client()

    # the capability probe's Bedrock check
    monkeypatch.setattr(aws_account, "session", account_session)
    monkeypatch.setattr(aws_account, "get_record", lambda: {})
    monkeypatch.setattr(aws_account, "_update_record", lambda **_: None)
    monkeypatch.setattr(aws_account, "get_chat_via", lambda *a, **k: "aws")
    monkeypatch.setattr(aws_account, "_bedrock_model_id", lambda *a, **k: "m")
    monkeypatch.setattr(aws_account, "is_hive_mode", lambda: True)  # stop after Bedrock
    out = aws_account.run_capability_checks({})
    assert out["bedrock"]["status"] == "ok", out["bedrock"]

    auths = [r["authorization"] for r in stub.requests]
    assert len(auths) == 5, auths
    assert all(a.startswith("AWS4-HMAC-SHA256 Credential=AKIDSTUBEXAMPLE/") for a in auths), auths
    assert os.environ.get(_KEY) == "token-in-env"


def test_hive_account_check_signs_bedrock_with_the_account_not_an_env_token(
        stub, monkeypatch, client):
    """The Hive account check's Bedrock call answers for the account being checked,
    even while a bearer token sits in os.environ."""
    import boto3
    from unittest.mock import MagicMock
    from hive import accounts as ha
    from routers import hive as hive_router

    monkeypatch.setenv(_KEY, "token-in-env")
    hive_creds = {"aws_access_key_id": "AKIDHIVEEXAMPLE", "aws_secret_access_key": "stub-secret"}
    real = boto3.session.Session(region_name="us-east-1", **hive_creds)
    others = MagicMock()
    others.get_caller_identity.return_value = {"Account": "222222222222"}

    class _Session:  # the account's session: Bedrock is real (to the stub), the rest canned
        def client(self, service, **kwargs):
            return real.client("bedrock", **kwargs) if service == "bedrock" else others

    provisioner = MagicMock()
    provisioner._get_session.return_value = _Session()
    monkeypatch.setattr(hive_router, "_provisioner", provisioner)
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)

    client.post("/api/hive/accounts", json={"account_id": "222222222222"})
    acct = [r for r in client.get("/api/hive/accounts").json()
            if r["account_id"] == "222222222222"][0]["id"]
    try:
        r = client.post(f"/api/hive/accounts/{acct}/verify").json()
    finally:
        client.delete(f"/api/hive/accounts/{acct}")

    assert r["checks"]["bedrock"]["status"] == "pass", r
    assert [q["authorization"][:40] for q in stub.requests] == [
        "AWS4-HMAC-SHA256 Credential=AKIDHIVEEXAM"]
    assert os.environ.get(_KEY) == "token-in-env"
