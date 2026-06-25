"""M5 — closed-loop audit (self-evolution design §5 + §6e).

Two pure functions close the feedback edge ⑥→①:

1. audit_recurrence — distinguishes "fewer corrections because fewer MISTAKES"
   (known-class recurrence fell after a gate/rule) from "fewer because we LOGGED
   LESS" (total capture fell while known-class recurrence did NOT improve). This
   is the Goodhart guard the design demands: the extract-gate could deflate the
   correction count by capturing less, faking evolution.

2. loop_closed_meta_test — injects a synthetic class-tagged lesson and walks it
   through the circuit (clean → resident → replay), returning {closed, broken_link}.
   On failure it NAMES the exact broken link instead of a bare False. Behavior,
   not existence: each link is a callable the caller supplies, so the meta-test
   asserts real transitions, not the presence of a file.
"""

from core.evolution.closed_loop import audit_recurrence, loop_closed_meta_test


def _cls(count, post_gate=0, post_rule=0, active_gate=None, resolved=False):
    return {
        "count": count, "post_gate_count": post_gate, "post_rule_count": post_rule,
        "active_gate": active_gate, "active_rule": None, "resolved": resolved,
    }


class TestAuditRecurrence:
    def test_fewer_mistakes_is_healthy(self):
        # Known class CLASS_A: gated, zero recurrence since gate. Capture total
        # is stable. → genuine improvement.
        state = {"CLASS_A": _cls(count=12, post_gate=0, active_gate="GC12")}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 40, "total_prev_period": 42}
        )
        assert verdict["healthy"] is True
        assert verdict["reason_class"] == "fewer_mistakes"

    def test_logged_less_is_goodhart(self):
        # Total capture COLLAPSED (40 → 8) but the gated class still recurs
        # (post_gate=3). Correction count fell because we LOGGED LESS, not
        # because we made fewer mistakes. → Goodhart, NOT healthy.
        state = {"CLASS_A": _cls(count=15, post_gate=3, active_gate="GC12")}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 8, "total_prev_period": 40}
        )
        assert verdict["healthy"] is False
        assert verdict["reason_class"] == "logged_less"

    def test_recurrence_rising_despite_gate_is_unhealthy(self):
        # Gate deployed but class keeps recurring — the gate FAILED, escalate.
        state = {"CLASS_A": _cls(count=20, post_gate=5, active_gate="GC12")}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 40, "total_prev_period": 40}
        )
        assert verdict["healthy"] is False
        assert verdict["reason_class"] in ("gate_failed", "recurring")

    def test_empty_state_is_vacuously_healthy(self):
        verdict = audit_recurrence({}, capture_stats={"total_this_period": 0, "total_prev_period": 0})
        assert verdict["healthy"] is True

    def test_resolved_classes_excluded(self):
        # A resolved class must not count against health.
        state = {"OLD": _cls(count=8, post_gate=0, active_gate="G1", resolved=True)}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 30, "total_prev_period": 30}
        )
        assert verdict["healthy"] is True


class TestLoopClosedMetaTest:
    """Behavioral: each link is a callable; failure NAMES the broken link."""

    def _all_pass(self):
        return {
            "inject": lambda lesson: True,
            "clean": lambda lesson: True,   # survives clean (not pruned)
            "resident": lambda lesson: True,  # present in a resident store
            "replay": lambda lesson: True,   # triggering scenario reflects it
        }

    def test_closed_when_all_links_pass(self):
        r = loop_closed_meta_test(**self._all_pass())
        assert r["closed"] is True
        assert r["broken_link"] is None

    def test_names_broken_clean_link(self):
        links = self._all_pass()
        links["clean"] = lambda lesson: False  # synthetic lesson pruned by clean
        r = loop_closed_meta_test(**links)
        assert r["closed"] is False
        assert r["broken_link"] == "clean"

    def test_names_broken_resident_link(self):
        links = self._all_pass()
        links["resident"] = lambda lesson: False
        r = loop_closed_meta_test(**links)
        assert r["closed"] is False
        assert r["broken_link"] == "resident"

    def test_names_broken_replay_link(self):
        links = self._all_pass()
        links["replay"] = lambda lesson: False
        r = loop_closed_meta_test(**links)
        assert r["closed"] is False
        assert r["broken_link"] == "replay"

    def test_stops_at_first_broken_link(self):
        # inject fails → must name inject, not run later links.
        calls = []
        links = {
            "inject": lambda l: (calls.append("inject"), False)[1],
            "clean": lambda l: (calls.append("clean"), True)[1],
            "resident": lambda l: (calls.append("resident"), True)[1],
            "replay": lambda l: (calls.append("replay"), True)[1],
        }
        r = loop_closed_meta_test(**links)
        assert r["broken_link"] == "inject"
        assert calls == ["inject"], "must short-circuit at first broken link"

    def test_link_exception_is_treated_as_broken(self):
        links = self._all_pass()
        def boom(lesson):
            raise RuntimeError("link crashed")
        links["resident"] = boom
        r = loop_closed_meta_test(**links)
        assert r["closed"] is False
        assert r["broken_link"] == "resident"
