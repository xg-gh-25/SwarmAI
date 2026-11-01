"""SDK auth-error sanitization (Gate-2 meta-review O3).

A raw SDK authentication error must map to a clean, actionable message that
points the user at Settings — never a raw stack trace, never mwinit, and (since
Anthropic-direct keys are no longer supported) never at an Anthropic key.
"""
from core.session_utils import _sanitize_sdk_error


def test_anthropic_auth_error_maps_to_settings_message():
    raw = 'API error: {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
    friendly, action = _sanitize_sdk_error(raw)
    assert "sign-in" in friendly.lower() or "key" in friendly.lower()
    assert action and "settings" in action.lower()
    assert "mwinit" not in (action or "").lower()
    assert "anthropic" not in (action or "").lower()


def test_401_maps_to_settings_message():
    raw = "Request failed: 401 Unauthorized"
    friendly, action = _sanitize_sdk_error(raw)
    assert action and "settings" in action.lower()


def test_unrelated_error_still_generic():
    raw = "some unrelated failure"
    friendly, action = _sanitize_sdk_error(raw)
    # unchanged generic fallback (not the auth message)
    assert "settings" not in (action or "").lower()


def test_bedrock_bearer_expiry_maps_to_bedrock_message_not_ada(monkeypatch):
    """Meta-review HIGH: a 24/7 daemon + <=12h bearer token WILL expire
    mid-stream. The bedrock-runtime ExpiredToken error must map to a
    Bedrock-specific 'generate a new token' hint — NOT the ada/mwinit hint,
    and NOT the Anthropic-API-key hint."""
    monkeypatch.setattr("core.aws_account.get_chat_via", lambda config=None: "bedrock_key")
    raw = "botocore.errorfactory.ExpiredTokenException: The bearer token has expired"
    friendly, action = _sanitize_sdk_error(raw)
    assert "bedrock" in friendly.lower()
    assert action and "settings" in action.lower()
    assert "mwinit" not in (action or "").lower()
    # must be the Bedrock message, not the Anthropic one
    assert "anthropic api key" not in (action or "").lower()


def test_a_bearer_token_error_gets_the_key_text_for_a_key_user(monkeypatch):
    """A Bedrock-key user's bearer-token error (here a 403) is about the key."""
    monkeypatch.setattr("core.aws_account.get_chat_via", lambda config=None: "bedrock_key")
    raw = "AWS_BEARER_TOKEN_BEDROCK token invalid or expired (403)"
    friendly, action = _sanitize_sdk_error(raw)
    assert "bedrock" in friendly.lower()


def test_an_expired_aws_session_mid_turn_is_a_sign_in_not_a_key(monkeypatch):
    """An SSO / access-key / ADA user's token expiring mid-turn: sign in again on the
    account page; never "your Bedrock API key", a key they do not have."""
    from core.session_utils import sdk_error_code
    monkeypatch.setattr("core.aws_account.get_chat_via", lambda config=None: "aws")
    for raw in ("ExpiredTokenException: The security token included in the request is expired",
                "An error occurred (UnrecognizedClientException): The security token included in the request is invalid",
                "401 Unauthorized"):
        friendly, action = _sanitize_sdk_error(raw)
        assert "bedrock api key" not in friendly.lower() and "bearer" not in friendly.lower(), raw
        assert "swarm aws account" in action.lower(), raw
        assert sdk_error_code(raw) == "CREDENTIALS_EXPIRED", raw


def test_the_code_follows_the_text():
    from core.session_utils import sdk_error_code
    assert sdk_error_code("ECONNRESET by peer") == "SDK_ERROR"
    assert sdk_error_code("something else", default="X") == "X"


def test_errors_that_only_mention_tokens_or_401_are_not_sign_in_failures():
    """Context-length / max_tokens errors and a 401 that is a duration are not sign-in
    failures: they keep their own text and code (else the card says "sign in again" and
    a successful re-verify hides the real error)."""
    from core.session_utils import sdk_error_code
    for raw in ("API Error (bedrock): input length and max_tokens exceed context limit",
                "ValidationException: max_tokens is invalid for this model",
                "Input is too long for requested model (bedrock). prompt tokens 210000",
                "Request failed after 401 ms"):
        assert sdk_error_code(raw) == "SDK_ERROR", raw
        friendly, _ = _sanitize_sdk_error(raw)
        assert "sign-in" not in friendly.lower() and "bearer" not in friendly.lower(), raw


def test_spawn_and_conversation_errors_get_the_sign_in_code_too():
    from core.session_utils import sdk_error_code
    assert sdk_error_code("ExpiredTokenException: expired", "SPAWN_FAILED") == "CREDENTIALS_EXPIRED"
    assert sdk_error_code("ENOENT spawn claude", "SPAWN_FAILED") == "SPAWN_FAILED"
