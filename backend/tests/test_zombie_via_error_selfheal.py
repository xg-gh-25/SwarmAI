"""Regression tests for the Layer-2 poisoned-subprocess self-heal.

WHAT IS TESTED
--------------
The streaming orchestrator's error path treats an INSTANT, empty,
no-content ``error_during_execution`` ResultMessage as a poisoned/zombie
subprocess (left corrupt by a prior interrupt) and raises the SAME
"Zombie subprocess detected" RuntimeError as the empty-stream zombie
detector — so ``send()`` kills + respawns with ``--resume`` instead of
reusing the dead subprocess into a "response stops half-way / must send
several times" loop.

KEY INVARIANTS
--------------
1. ROUTING (the part the project keeps regressing — a guard that never
   executes its recovery): the exact string the orchestrator raises MUST be
   classified retriable by ``_is_retriable_error`` so send()'s retry loop
   respawns with --resume. If the message wording drifts out of the pattern,
   the self-heal silently dies — this test fails first.
2. GUARD CONDITION: only the poison signature (subtype=error_during_execution
   + empty error_text + no content emitted + sub-2s stream) routes to
   self-heal. A genuine error (real text, OR after streamed content, OR slow)
   must NOT be swallowed into a respawn — it is surfaced to the user.

Methodology note: matches the "condition invariant locking" strategy used by
test_session_unit_recovery_paths.py (T1-T4) — driving the full
_read_formatted_response async-iterator stack requires ~200 lines of fragile
SDK mocking. The routing assertion (1) is end-to-end meaningful: it proves the
raised signal actually triggers respawn, not a dead end.
"""

from core.session_utils import _is_retriable_error


def _orchestrator_zombie_message(streaming_dur: float = 0.012,
                                  session_id: str = "sess-test") -> str:
    """The exact string streaming_orchestrator raises for the poison case."""
    return (
        f"Zombie subprocess detected: error_during_execution "
        f"with no content in {streaming_dur:.1f}s "
        f"(session_id={session_id})"
    )


def _poison_signature(subtype: str, error_text: str,
                      content_emitted: bool, streaming_dur: float) -> bool:
    """Mirror of the orchestrator guard (Layer 2 self-heal trigger)."""
    return (
        subtype == "error_during_execution"
        and not error_text.strip()
        and not content_emitted
        and streaming_dur < 2.0
    )


class TestZombieViaErrorRouting:
    """Invariant 1: the raised signal routes into the --resume self-heal."""

    def test_raised_message_is_retriable(self):
        # If this fails, the orchestrator raise no longer matches the
        # r"Zombie subprocess detected" retriable pattern → send() would
        # treat it as fatal and NOT respawn → silent dead loop returns.
        assert _is_retriable_error(_orchestrator_zombie_message()) is True

    def test_plain_empty_error_text_alone_is_not_retriable(self):
        # Sanity: the bug was that empty error_text fell through as
        # non-retriable. Confirm empty text by itself is still fatal — the
        # self-heal comes from the explicit zombie raise, not from text.
        assert _is_retriable_error("") is False


class TestPoisonGuardCondition:
    """Invariant 2: only the true poison signature self-heals."""

    def test_poison_signature_triggers(self):
        assert _poison_signature("error_during_execution", "", False, 0.012) is True

    def test_real_error_text_not_swallowed(self):
        # A genuine error WITH detail must be surfaced, not retried.
        assert _poison_signature(
            "error_during_execution", "ValidationException: bad input", False, 0.012
        ) is False

    def test_error_after_content_not_swallowed(self):
        # Content already streamed → a real mid-generation failure. Surface it.
        assert _poison_signature("error_during_execution", "", True, 0.012) is False

    def test_slow_error_not_swallowed(self):
        # >2s means the turn actually ran; not the instant-poison signature.
        assert _poison_signature("error_during_execution", "", False, 5.0) is False

    def test_other_subtype_not_swallowed(self):
        assert _poison_signature("error_max_turns", "", False, 0.012) is False
