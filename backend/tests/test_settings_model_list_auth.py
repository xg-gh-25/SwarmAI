"""Settings' model list (routers.settings._bedrock_client) signs with chat's own credentials.

botocore reads AWS_BEARER_TOKEN_BEDROCK when it signs each request. A control-plane client
with no signature version set in code therefore listed the models of whatever token was
in the process environment: none before the first chat in API-key mode, and another
account's for an ada/sso sign-in with a token exported in the shell. These tests look at
the Authorization header that reaches a local stub (tests/test_verify_bedrock_stub).
"""
from __future__ import annotations

import pytest

from tests.test_verify_bedrock_stub import stub  # noqa: F401 — the stub endpoint fixture

_KEY = "AWS_BEARER_TOKEN_BEDROCK"
SAVED = "saved-bedrock-api-key-0123456789"


def _list(monkeypatch, key):
    from core import aws_account
    from jobs import bedrock as jb
    from routers import settings
    monkeypatch.setattr(jb, "_bedrock_key", lambda: key)
    monkeypatch.setattr(aws_account, "active", lambda: False)
    monkeypatch.setattr(aws_account, "region", lambda config=None: "us-east-1")
    return settings._list_bedrock_inference_profiles([])


def test_api_key_mode_lists_with_the_saved_key_before_any_chat(stub, monkeypatch):  # noqa: F811
    monkeypatch.delenv(_KEY, raising=False)          # no chat has put it there
    assert _list(monkeypatch, SAVED) == []
    assert stub.requests and stub.requests[-1]["authorization"] == f"Bearer {SAVED}"


def test_api_key_mode_ignores_another_token_in_the_environment(stub, monkeypatch):  # noqa: F811
    monkeypatch.setenv(_KEY, "a-token-from-the-shell-0123456789")
    _list(monkeypatch, SAVED)
    assert stub.requests[-1]["authorization"] == f"Bearer {SAVED}"


def test_a_sigv4_sign_in_never_lists_with_a_token_in_the_environment(stub, monkeypatch):  # noqa: F811
    monkeypatch.setenv(_KEY, "a-token-from-the-shell-0123456789")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLEEXAMPLE00")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "example-secret-key-for-the-stub-only")
    _list(monkeypatch, None)
    assert stub.requests[-1]["authorization"].startswith("AWS4-HMAC-SHA256 ")
