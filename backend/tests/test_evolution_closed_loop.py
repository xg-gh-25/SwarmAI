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


class TestAuditAxisAwareness:
    """Non-cognitive classes (OPERATIONAL/UNCLASSIFIED) must NOT drive the verdict.

    The closed-loop audit judges whether the COGNITIVE evolution loop is closing.
    A polluted OPERATIONAL class (count=802, post_rule=53 — operator-noise that
    pre-dates the noise gate) must not force the audit to report unhealthy forever.
    """

    def test_operational_only_recurrence_is_healthy(self):
        # The exact live pollution: OPERATIONAL recurring, no real cognitive issue.
        state = {"OPERATIONAL": {"count": 802, "post_gate_count": 0, "post_rule_count": 53,
                                 "active_gate": None, "active_rule": "RULE_OPERATIONAL", "resolved": False}}
        verdict = audit_recurrence(state, capture_stats={"total_this_period": 802, "total_prev_period": 802})
        assert verdict["healthy"] is True, "operational-only recurrence must not fail the cognitive loop audit"

    def test_unclassified_only_recurrence_is_healthy(self):
        state = {"UNCLASSIFIED": {"count": 87, "post_gate_count": 0, "post_rule_count": 5,
                                  "active_gate": None, "active_rule": "R", "resolved": False}}
        verdict = audit_recurrence(state, capture_stats={"total_this_period": 87, "total_prev_period": 87})
        assert verdict["healthy"] is True

    def test_cognitive_recurrence_still_unhealthy_amid_operational(self):
        # A REAL cognitive recurrence must still surface even with OPERATIONAL noise present.
        state = {
            "OPERATIONAL": {"count": 802, "post_gate_count": 0, "post_rule_count": 53,
                            "active_gate": None, "active_rule": "RULE_OPERATIONAL", "resolved": False},
            "CLASS_A": {"count": 5, "post_gate_count": 3, "post_rule_count": 0,
                        "active_gate": "GC12", "active_rule": None, "resolved": False},
        }
        verdict = audit_recurrence(state, capture_stats={"total_this_period": 807, "total_prev_period": 807})
        assert verdict["healthy"] is False
        assert verdict["reason_class"] == "gate_failed"


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
        # Gate deployed but class keeps recurring (capture stable) — gate FAILED.
        # Tightened from `in (...)` to exact match (adv #5) so a gate_failed→recurring
        # routing regression is actually caught.
        state = {"CLASS_A": _cls(count=20, post_gate=5, active_gate="GC12")}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 40, "total_prev_period": 40}
        )
        assert verdict["healthy"] is False
        assert verdict["reason_class"] == "gate_failed"

    def test_recurring_without_gate_is_distinct_from_gate_failed(self):
        # No gate yet, recurrence past RULE, capture stable → "recurring"
        # (NOT gate_failed). Dedicated coverage of the standalone branch (adv #5).
        state = {"CLASS_B": _cls(count=10, post_rule=3, active_gate=None)}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 30, "total_prev_period": 30}
        )
        assert verdict["healthy"] is False
        assert verdict["reason_class"] == "recurring"

    def test_none_entry_skipped(self):
        # get_class can return None for a class_names() key that fails
        # canonicalization. audit_recurrence must skip None, not crash (adv #2).
        state = {"CLASS_A": _cls(count=12, post_gate=0, active_gate="GC12"), "GHOST": None}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 40, "total_prev_period": 42}
        )
        assert verdict["healthy"] is True  # only the valid gated-quiet class counts

    def test_capture_collapsed_no_recurrence_detail_not_misleading(self):
        # Healthy verdict, but detail must NOT claim "capture is stable" when it
        # collapsed (adv #4).
        state = {"CLASS_A": _cls(count=12, post_gate=0, active_gate="GC12")}
        verdict = audit_recurrence(
            state, capture_stats={"total_this_period": 5, "total_prev_period": 40}
        )
        assert verdict["healthy"] is True
        assert verdict["reason_class"] == "fewer_mistakes"
        assert "capture is stable" not in verdict["detail"]
        assert "watch next period" in verdict["detail"]

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
