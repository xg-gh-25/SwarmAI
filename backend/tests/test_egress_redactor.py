"""Tests for channel egress redaction (Phase-0 gap G1).

Drives the REAL redactor functions + the REAL OutboundMessage chokepoint — no
mock of the code under change (GUI32/PIT13: a test that patches the symbol it
guards is theater). Cross-chunk tests prove the rolling buffer catches a secret
split across streaming batches; mutation note per assertion says what reverting
the fix would break (→ RED).
"""

from __future__ import annotations


from channels.egress_redactor import (
    StreamRedactor,
    redact_credentials,
    redact_exfiltration_urls,
    redact_text,
)
from channels.base import OutboundMessage


# --- redact_credentials: known shapes redacted -------------------------------

def test_aws_access_key_redacted():
    # Revert redact_credentials -> this AKIA leaks (RED).
    src = "Here is the key AKIAIOSFODNN7EXAMPLE for the account."
    out = redact_credentials(src)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED]" in out


def test_pem_private_key_block_redacted():
    src = (
        "cert:\n-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdef\n"
        "-----END RSA PRIVATE KEY-----\ndone"
    )
    out = redact_credentials(src)
    assert "MIIEpAIBAAKCAQEA1234567890abcdef" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "[REDACTED]" in out


def test_prefixed_provider_token_redacted():
    src = "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ok"
    out = redact_credentials(src)
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in out
    assert "[REDACTED]" in out


def test_bearer_token_redacted():
    src = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6"
    out = redact_credentials(src)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6" not in out


def test_secret_assignment_keeps_key_redacts_value():
    src = "aws_secret_access_key=wJalrXUtnFEMI0K7MDENGbPxRfiCYEXAMPLEKEY"
    out = redact_credentials(src)
    assert "wJalrXUtnFEMI0K7MDENGbPxRfiCYEXAMPLEKEY" not in out
    # key name preserved (only the value is masked)
    assert "aws_secret_access_key" in out


# --- redact_exfiltration_urls: only credential-bearing URLs ------------------

def test_url_with_embedded_credential_redacted():
    src = "clone https://user:p4ssw0rd@github.com/x/y.git now"
    out = redact_exfiltration_urls(src)
    assert "p4ssw0rd" not in out


def test_url_with_token_query_redacted():
    src = "fetch https://api.example.com/data?token=SECRETVALUE123456 please"
    out = redact_exfiltration_urls(src)
    assert "SECRETVALUE123456" not in out


# --- false-positive guard: legit content survives (pre-mortem: over-match) ---

def test_plain_url_not_redacted():
    src = "See the docs at https://docs.aws.amazon.com/lambda/index.html"
    out = redact_text(src)
    assert out == src  # no credential -> untouched


def test_git_sha_not_redacted():
    src = "commit a1b2c3d4e5f60718293a4b5c6d7e8f9012345678 landed"
    out = redact_text(src)
    assert out == src


def test_plain_prose_untouched():
    src = "The quarterly revenue grew and the deploy finished cleanly."
    assert redact_text(src) == src


def test_empty_text_safe():
    assert redact_text("") == ""
    assert redact_text(None) is None  # type: ignore[arg-type]


# --- OutboundMessage chokepoint (structural G1) ------------------------------

def test_outbound_message_redacts_text_on_construct():
    # Revert OutboundMessage.__post_init__ -> the key leaks to the adapter (RED).
    msg = OutboundMessage(
        channel_id="c1",
        external_chat_id="chat1",
        text="key AKIAIOSFODNN7EXAMPLE here",
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in msg.text
    assert "[REDACTED]" in msg.text


def test_outbound_message_plain_text_unchanged():
    msg = OutboundMessage(channel_id="c1", external_chat_id="chat1", text="hello team")
    assert msg.text == "hello team"


# --- StreamRedactor rolling buffer: cross-chunk split ------------------------

def test_stream_redactor_catches_credential_split_across_chunks():
    """A secret split across two chunks must never be emitted half-formed.

    Revert native_flush_now's redactor wiring OR StreamRedactor.feed -> the
    'AKIA' prefix leaks in chunk 1 (RED).
    """
    r = StreamRedactor()
    # 'AKIAIOSFODNN7EXAMPLE' split: 'AKIAIOSFO' | 'DNN7EXAMPLE more text '
    out1 = r.feed("prefix AKIAIOSFO")
    # chunk1 holds the in-flight token 'AKIAIOSFO' — must NOT appear
    assert "AKIAIOSFO" not in out1
    out2 = r.feed("DNN7EXAMPLE done here ")
    combined = out1 + out2
    # the completed key must be redacted, never emitted whole
    assert "AKIAIOSFODNN7EXAMPLE" not in combined
    assert "[REDACTED]" in combined


def test_stream_redactor_emits_safe_prefix_promptly():
    r = StreamRedactor()
    out = r.feed("hello world here is more ")
    # everything up to the last whitespace is safe to emit immediately
    assert "hello world here is more" in out


def test_stream_redactor_flush_releases_tail():
    r = StreamRedactor()
    r.feed("trailing_token_no_space")  # withheld (no whitespace)
    tail = r.flush()
    assert "trailing_token_no_space" in tail
    # idempotent
    assert r.flush() == ""


def test_stream_redactor_flush_redacts_withheld_credential():
    r = StreamRedactor()
    # a bare credential with no trailing whitespace is withheld until flush
    r.feed("AKIAIOSFODNN7EXAMPLE")
    tail = r.flush()
    assert "AKIAIOSFODNN7EXAMPLE" not in tail
    assert "[REDACTED]" in tail


def test_stream_redactor_full_reassembly_is_redacted():
    """End-to-end: feed a stream containing a secret, concatenate all emissions
    + flush, assert the secret never appears in the reassembled output."""
    r = StreamRedactor()
    chunks = ["The key is ", "AKIAIOS", "FODNN7EXAMPLE", " and that's it"]
    out = "".join(r.feed(c) for c in chunks) + r.flush()
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "The key is" in out  # legit content preserved


# --- adversarial C1: MULTI-TOKEN credentials split across whitespace ---------
# These were leaked by the original "single-token" invariant (adversarial C1).

def test_stream_redactor_bearer_split_across_whitespace():
    """`Bearer <token>` contains a space; the token must not leak once complete."""
    r = StreamRedactor()
    out = "".join(r.feed(c) for c in
                  ["Authorization is Bearer ", "abcdefghijklmnop1234 done "]) + r.flush()
    assert "abcdefghijklmnop1234" not in out
    assert "[REDACTED]" in out


def test_stream_redactor_secret_assignment_split_across_whitespace():
    """`api_key = SECRET` has spaces around `=`; value must not leak."""
    r = StreamRedactor()
    out = "".join(r.feed(c) for c in
                  ["config api_key = ", "SECRETVALUE123456 end "]) + r.flush()
    assert "SECRETVALUE123456" not in out


def test_stream_redactor_url_credential_split():
    r = StreamRedactor()
    out = "".join(r.feed(c) for c in
                  ["clone https://user:", "p4ssw0rd@github.com/x.git now "]) + r.flush()
    assert "p4ssw0rd" not in out


def test_stream_redactor_long_single_token_over_margin_not_leaked():
    """A single token longer than the safety margin is still withheld until a
    boundary — never emitted mid-token."""
    r = StreamRedactor()
    big = "x" * 3000
    emitted = r.feed(big)  # 3000-char unbroken token, no trailing whitespace
    assert big[:100] not in emitted  # nothing of the in-flight token leaks
    # complete it with whitespace + confirm it eventually flushes intact-safe
    tail = r.feed(" ") + r.flush()
    assert "x" * 3000 in (emitted + tail)  # legit content not lost


# --- adversarial H1: bare JWT (no Bearer prefix) -----------------------------

def test_bare_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQabcdef"
    out = redact_text(f"the session token is {jwt} ok")
    assert jwt not in out
    assert "[REDACTED]" in out
