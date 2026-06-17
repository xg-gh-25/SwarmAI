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
  - checked in the self-heal gate (``and not self._user_stopped_current_turn``),
  - cleared only in the next ``send()``'s Layer 0 synchronous preamble.

These are structural (source-inspection) tests in the style of
``test_recovery_checkpoint_unified.py::TestStaleCheckpointClearedOnSend`` — they lock
the invariant at the exact code sites without spinning up the full send() machinery
(which requires a live SDK subprocess).
"""

import inspect

from core.session_unit import SessionUnit


class TestStopSuppressesSelfHeal:
    """The self-heal gate must exclude user-stopped turns, with a durable flag."""

    def test_self_heal_gate_checks_user_stopped_flag(self):
        """send()'s self-heal gate must include `not self._user_stopped_current_turn`."""
        source = inspect.getsource(SessionUnit.send)
        assert "_self_heal_enabled" in source, "self-heal gate not found in send()"
        # The gate line must AND-in the user-stopped guard so a Stop skips self-heal.
        assert "not self._user_stopped_current_turn" in source, (
            "send() self-heal gate must include 'not self._user_stopped_current_turn' "
            "— otherwise a user Stop can be followed by a proactive heal/respawn "
            "(the 'can't stop / auto-resume' regression)."
        )

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
