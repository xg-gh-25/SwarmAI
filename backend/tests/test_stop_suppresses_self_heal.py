"""Stop must suppress self-heal — a user Stop is never followed by a proactive heal.

WHAT IS TESTED
--------------
Regression guard for the "can't stop / auto-resumes after Stop" bug introduced when
self-heal was defaulted ON (commit 92a337db). The post-stream self-heal check in
``SessionUnit.send()`` runs after the streaming loop exits — including when the loop
exited because the user pressed Stop. Without a guard, a user Stop could be followed
by a proactive self-heal kill -> COLD -> armed checkpoint -> respawn ("auto-resume"),
defeating Stop.

ROOT-CAUSE NUANCE
-----------------
``_interrupted`` is NOT a usable signal here: ``_read_formatted_response`` clears it
mid-stream (sets ``_interrupted = False`` when handling the interrupt), so by the time
the post-stream self-heal check runs it is already False. The fix uses a DURABLE flag
``_user_stopped_current_turn`` that is:
  - set True at ``interrupt()`` entry,
  - passed into the self-heal decision authority
    (``RecoveryCoordinator.decide(user_stopped=...)``, refactored out of the old
    inline ``and not self._user_stopped_current_turn`` gate under R3), whose
    ``_universal_guard`` returns ``SKIP / "user_stopped_current_turn"``,
  - cleared only in the next ``send()``'s Layer 0 synchronous preamble.

These are structural (source-inspection) tests in the style of
``test_recovery_checkpoint_unified.py::TestStaleCheckpointClearedOnSend`` — they lock
the invariant at the exact code sites without spinning up the full send() machinery
(which requires a live SDK subprocess).
"""

import inspect

from core.session_unit import SessionUnit
from core.session_healing import RecoveryVerdict


class TestStopSuppressesSelfHeal:
    """The self-heal gate must exclude user-stopped turns, with a durable flag."""

    def test_self_heal_gate_passes_user_stopped_to_coordinator(self):
        """send() must feed the durable stopped flag into the recovery authority.

        The old inline gate (`and not self._user_stopped_current_turn`) was
        refactored under R3 into RecoveryCoordinator.decide(user_stopped=...).
        Lock the WIRING: send() must pass the flag through, or a Stop would no
        longer suppress self-heal (the 'can't stop / auto-resume' regression).
        """
        source = inspect.getsource(SessionUnit.send)
        assert "_self_heal_enabled" in source, "self-heal gate not found in send()"
        assert "user_stopped=self._user_stopped_current_turn" in source, (
            "send() must pass user_stopped=self._user_stopped_current_turn into "
            "the recovery decision (RecoveryCoordinator.decide) — otherwise a "
            "user Stop can be followed by a proactive heal/respawn."
        )

    def test_recovery_coordinator_skips_when_user_stopped(self):
        """BEHAVIORAL: the recovery authority refuses to heal a user-stopped turn.

        This is the actual invariant (not just the wiring): with user_stopped=True
        the coordinator returns SKIP / 'user_stopped_current_turn', so no kill /
        respawn follows a Stop. Contrast: user_stopped=False is not short-circuited
        by the universal guard.
        """
        unit = SessionUnit(session_id="test-stop-suppress-behavioral", agent_id="default")
        coordinator = unit._recovery_coordinator

        stopped = coordinator.decide(
            "turn_timeout", enabled=True, user_stopped=True,
            state="streaming", graceful_pending=False,
        )
        assert stopped.verdict is RecoveryVerdict.SKIP, (
            "a user-stopped turn must SKIP recovery (no proactive heal/respawn)"
        )
        assert stopped.reason == "user_stopped_current_turn"

        not_stopped = coordinator.decide(
            "turn_timeout", enabled=True, user_stopped=False,
            state="streaming", graceful_pending=False,
        )
        assert not_stopped.verdict is not RecoveryVerdict.SKIP or (
            not_stopped.reason != "user_stopped_current_turn"
        ), "user_stopped=False must not be skipped by the user-stopped guard"

    def test_layer0_resets_user_stopped_flag(self):
        """send() Layer 0 preamble must reset the durable stopped flag for a new turn."""
        source = inspect.getsource(SessionUnit.send)
        assert "self._user_stopped_current_turn = False" in source, (
            "send() Layer 0 must clear self._user_stopped_current_turn so a prior "
            "Stop does not suppress self-heal on a genuinely new turn."
        )

    def test_interrupt_sets_user_stopped_flag(self):
        """interrupt() must set the durable stopped flag at entry."""
        source = inspect.getsource(SessionUnit.interrupt)
        assert "self._user_stopped_current_turn = True" in source, (
            "interrupt() must set self._user_stopped_current_turn = True so the "
            "post-stream self-heal check can tell a user Stop from a clean completion."
        )

    def test_flag_declared_in_init(self):
        """The durable flag must be an instance attribute (no module-level state)."""
        source = inspect.getsource(SessionUnit.__init__)
        assert "self._user_stopped_current_turn" in source, (
            "self._user_stopped_current_turn must be declared in __init__ "
            "(instance-scoped, never module-level)."
        )
