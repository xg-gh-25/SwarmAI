"""Background Bedrock calls use the person's Bedrock API key (F3, R2.9).

A person who signs in with a Bedrock API key may also have an old, expired default AWS
profile. The background client (jobs.bedrock) must authenticate with the key as a bearer
token, not sign with that profile. The outgoing request is captured with a botocore
``before-send`` hook, so nothing goes on the network.
"""
import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

FAKE_KEY = "bedrock-api-key-for-tests-0123456789"


class _Captured(Exception):
    """Raised from the before-send hook to stop the call after seeing the request."""


class _FakeConfig:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture
def key_user_with_bad_profile(tmp_path, monkeypatch):
    # a default profile whose keys AWS would reject
    creds = tmp_path / "credentials"
    creds.write_text("[default]\naws_access_key_id = AKIAEXPIREDEXPIRED00\n"
                     "aws_secret_access_key = expiredexpiredexpiredexpiredexpired000\n")
    conf = tmp_path / "config"
    conf.write_text("[default]\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(conf))
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                 "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(name, raising=False)

    from core import aws_account
    from core.app_config_manager import AppConfigManager
    cfg = _FakeConfig({"auth_method": "bedrock_api_key",
                       "aws_bearer_token_bedrock": FAKE_KEY,
                       "aws_region": "us-east-1"})
    monkeypatch.setattr(AppConfigManager, "instance", classmethod(lambda cls: cfg))
    monkeypatch.setattr(aws_account, "active", lambda: False)

    import jobs.bedrock as bedrock
    bedrock.evict_client()
    yield bedrock
    bedrock.evict_client()


def _authorization_of(client) -> str:
    seen = {}

    def hook(request, **_):
        seen["auth"] = request.headers.get("Authorization")
        raise _Captured()

    client.meta.events.register("before-send.bedrock-runtime.*", hook)
    with pytest.raises(_Captured):
        client.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "hi"}]}])
    auth = seen["auth"]
    return auth.decode() if isinstance(auth, bytes) else auth


def test_cached_client_uses_the_key_as_bearer(key_user_with_bad_profile):
    client = key_user_with_bad_profile.get_client(force_new=True, region="us-east-1")
    assert _authorization_of(client) == f"Bearer {FAKE_KEY}"
    # the key is not put into the process environment
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


def test_fail_fast_client_uses_the_key_as_bearer(key_user_with_bad_profile):
    client = key_user_with_bad_profile.build_timeout_client(
        read_timeout=5, max_attempts=1, region="us-east-1")
    assert _authorization_of(client) == f"Bearer {FAKE_KEY}"
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ


def test_the_key_is_never_logged(key_user_with_bad_profile, caplog):
    import logging
    # SwarmAI's own loggers at every level; third-party ones at INFO, the level the app
    # runs at (botocore's DEBUG request dump prints every header of every client, a
    # sigv4 session token included, and is never on in the app).
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.DEBUG, logger="swarm")
    client = key_user_with_bad_profile.get_client(force_new=True, region="us-east-1")
    _authorization_of(client)
    assert FAKE_KEY not in caplog.text


def test_without_the_key_the_default_chain_is_used(key_user_with_bad_profile, monkeypatch):
    from core.app_config_manager import AppConfigManager
    cfg = _FakeConfig({"auth_method": "sso", "aws_region": "us-east-1"})
    monkeypatch.setattr(AppConfigManager, "instance", classmethod(lambda cls: cfg))
    client = key_user_with_bad_profile.get_client(force_new=True, region="us-east-1")
    assert _authorization_of(client).startswith("AWS4-HMAC-SHA256")


def test_a_new_key_rebuilds_the_cached_client(key_user_with_bad_profile, monkeypatch):
    bedrock = key_user_with_bad_profile
    first = bedrock.get_client()
    assert bedrock.get_client() is first
    monkeypatch.setattr(bedrock, "_bedrock_key", lambda: "a-replacement-key-0123456789")
    assert bedrock.get_client() is not first
