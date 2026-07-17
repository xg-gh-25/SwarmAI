"""SDK auth-error sanitization (Gate-2 meta-review O3).

An Anthropic API key revoked/rotated AFTER setup surfaces only when the SDK
subprocess calls Anthropic (api-key mode skips the STS pre-flight + /health
check). The raw SDK auth error must map to a clean, actionable message that
points the user at Settings — never a raw stack trace, never mwinit.
"""
from core.session_utils import _sanitize_sdk_error


def test_anthropic_auth_error_maps_to_settings_message():
    raw = 'API error: {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
    friendly, action = _sanitize_sdk_error(raw)
    assert "key" in friendly.lower() or "authentication" in friendly.lower()
    assert action and "settings" in action.lower()
    assert "mwinit" not in (action or "").lower()


def test_401_maps_to_settings_message():
    raw = "Request failed: 401 Unauthorized"
    friendly, action = _sanitize_sdk_error(raw)
    assert action and "settings" in action.lower()


def test_unrelated_error_still_generic():
    raw = "some unrelated failure"
    friendly, action = _sanitize_sdk_error(raw)
    # unchanged generic fallback (not the auth message)
    assert "settings" not in (action or "").lower()
