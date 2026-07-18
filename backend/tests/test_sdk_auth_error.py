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


def test_bedrock_bearer_expiry_maps_to_bedrock_message_not_ada():
    """Meta-review HIGH: a 24/7 daemon + <=12h bearer token WILL expire
    mid-stream. The bedrock-runtime ExpiredToken error must map to a
    Bedrock-specific 'generate a new token' hint — NOT the ada/mwinit hint,
    and NOT the Anthropic-API-key hint."""
    raw = "botocore.errorfactory.ExpiredTokenException: The bearer token has expired"
    friendly, action = _sanitize_sdk_error(raw)
    assert "bedrock" in friendly.lower()
    assert action and "settings" in action.lower()
    assert "mwinit" not in (action or "").lower()
    # must be the Bedrock message, not the Anthropic one
    assert "anthropic api key" not in (action or "").lower()


def test_bedrock_expiry_ordered_before_anthropic_pattern():
    """The Bedrock bearer signature must win over the generic 401/auth pattern
    (it's listed first) so a bedrock-runtime 403/expired isn't mislabeled as an
    Anthropic key problem."""
    raw = "AWS_BEARER_TOKEN_BEDROCK token invalid or expired (403)"
    friendly, action = _sanitize_sdk_error(raw)
    assert "bedrock" in friendly.lower()
