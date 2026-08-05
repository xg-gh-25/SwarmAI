"""Regression tests for the blank-turn ("前端没渲染、后端正常") retry guard.

WHAT IS TESTED
--------------
``streaming_orchestrator._is_blank_api_result`` — the predicate that decides
whether a non-error ResultMessage produced NOTHING renderable and must be
retried instead of silently transitioning to IDLE (leaving the user staring at
a blank turn while the backend logs a clean completion).

WHY IT EXISTS (the prod bug this would have caught)
---------------------------------------------------
On the stable daemon (post-04:47, boot c3a12557), session 2e87b27f at
2026-06-26 17:43 ran a 68s turn that returned ``input_tokens=0,
output_tokens=0`` with ``subtype="success"`` and no streamed content. The old
guard only fired on an EMPTY subtype (``not subtype``), so a ``"success"``
envelope slipped through both empty-guards → clean STREAMING→IDLE → no retry →
blank frontend. ``api_empty_response`` fired 0 times all day despite 3 such
empty results. Fix: the predicate now retries when subtype is ``""`` OR
``"success"`` (the only two values that can reach the guard — error subtypes
return/raise upstream).

KEY INVARIANTS
--------------
1. SHARED PREDICATE (anti-drift): the test imports and calls the SAME
   ``_is_blank_api_result`` the orchestrator uses — not a re-implemented mirror.
   The project keeps regressing on mirrored conditions that drift from prod
   (dumb-spawn equal-timestamp fixture, _open_tool_uses MagicMock-truthy). A
   shared predicate makes drift impossible.
2. ROUTING: the RuntimeError the orchestrator raises on a blank result MUST be
   classified retriable by ``_is_retriable_error`` — otherwise send() treats it
   as fatal and the blank turn returns. A guard that never triggers its
   recovery is the recurring failure mode this asserts against.
3. NO OVER-CAPTURE: streamed content, nonzero output tokens, a user interrupt,
   an error, or an unrecognised subtype must NOT be retried.

Methodology: pure-predicate unit tests (the orchestrator delegates the whole
condition to the shared function, so this is end-to-end meaningful without
~200 lines of SDK async-iterator mocking) + one routing assertion.
"""
from __future__ import annotations

from core.session_utils import _is_retriable_error
from core.streaming_orchestrator import _is_blank_api_result


def _blank(**over) -> bool:
    """Call the SHARED predicate with the blank-success baseline, overridable."""
    kw = dict(
        content_emitted=False,
        is_error=False,
        interrupted=False,
        output_tokens=0,
        subtype="success",
    )
    kw.update(over)
    return _is_blank_api_result(**kw)


class TestBlankResultTriggers:
    """Invariant: a non-error, zero-output, no-content result is retried."""

    def test_success_subtype_zero_output_is_blank(self):
        # THE prod bug: subtype="success" + 0 output + no content slipped the
        # old `not subtype` guard. Must now be caught.
        assert _blank() is True

    def test_empty_subtype_zero_output_is_blank(self):
        # Original behaviour preserved: an empty subtype (API didn't respond).
        assert _blank(subtype="") is True


class TestBlankResultDoesNotOverCapture:
    """Invariant: anything that actually produced/owns output is NOT retried."""

    def test_streamed_content_not_blank(self):
        assert _blank(content_emitted=True) is False

    def test_nonzero_output_tokens_not_blank(self):
        assert _blank(output_tokens=12) is False

    def test_user_interrupt_not_blank(self):
        # A user Stop legitimately ends a turn with no output — never retry it.
        assert _blank(interrupted=True) is False

    def test_error_result_not_blank(self):
        # Error results are handled by the error path upstream; the blank guard
        # must not also claim them (defensive — they can't reach here).
        assert _blank(is_error=True) is False

    def test_unrecognised_subtype_not_blank(self):
        # Only "" and "success" reach the guard; an unexpected subtype must NOT
        # be auto-retried (guards against future SDK subtypes silently looping).
        assert _blank(subtype="error_max_turns") is False
        assert _blank(subtype="turn_limit_reached") is False


class TestBlankResultRouting:
    """Invariant 2: the raised signal routes into send()'s retry loop."""

    def test_raised_message_is_retriable(self):
        # The exact string streaming_orchestrator raises on a blank result.
        msg = (
            "API returned empty response (output_tokens=0, duration=68.0s) — "
            "likely transient 429/503/timeout (session_id=2e87b27f)"
        )
        assert _is_retriable_error(msg) is True


class TestUnexpectedErrorRetriable:
    """Bedrock transient-500 ("unexpected error during processing") must
    auto-retry, not surface to the user (run_2a41d2d3).

    Prod evidence (backend-daemon.log:38723, 2026-08-05 22:09:19): session
    9d00e432 got is_error=True subtype=success error_text="API Error: The
    system encountered an unexpected error during processing. Try your request
    again." — a Bedrock transient internal-5xx, semantically identical to the
    already-whitelisted internal.?server.?error. It was NOT in the whitelist, so
    streaming_orchestrator classified it non-retriable, yielded the error, and
    the user had to manually resend (which almost always succeeded → transient).
    """

    # The exact string Bedrock returns (from the live daemon log).
    RAW = (
        "API Error: The system encountered an unexpected error during "
        "processing. Try your request again."
    )

    def test_raw_bedrock_string_is_retriable(self):
        # AC1: the raw error must classify as retriable.
        assert _is_retriable_error(self.RAW) is True

    def test_wrapped_string_is_retriable(self):
        # AC2 (the no-op guard): streaming_orchestrator wraps the retriable
        # error as RuntimeError("Retriable SDK error: <text>") and send()
        # (session_unit.py:2253) RE-classifies that WRAPPED string. If the
        # pattern only matched the raw form, the re-check would flip it to
        # non-retriable and crash to DEAD — making the fix a no-op. The
        # pattern is a plain substring, so it must survive the prefix.
        wrapped = f"Retriable SDK error: {self.RAW}"
        assert _is_retriable_error(wrapped) is True

    def test_permanent_error_still_not_retriable(self):
        # AC3 (over-match guard, PIT46): a genuinely permanent Bedrock error
        # (bad model id / validation) must NOT be swept into auto-retry — the
        # pattern is specific to the transient "unexpected error during
        # processing" phrase, not a broad "unexpected error" / "try again".
        permanent = (
            "ValidationException: The provided model identifier is invalid."
        )
        assert _is_retriable_error(permanent) is False
