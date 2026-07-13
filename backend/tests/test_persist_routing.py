"""Tests for persist_routing — the unified routing table for knowledge persistence.

Verifies that classify_content() routes knowledge to the correct destination
and that the routing table is consumed identically by auto hooks and manual skill.
"""
import pytest


class TestClassifyContent:
    """classify_content routes to correct doc + section."""

    def test_technical_pattern_routes_to_tech(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "nc -z is safer than lsof for port checks on macOS",
            project="SwarmAI",
        )
        assert result["doc"] == "TECH.md"
        assert result["section"] in ("Runtime Traps", "Conventions")
        assert result["project"] == "SwarmAI"

    def test_failure_lesson_routes_to_improvement(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Bug: grace period bypass via force=True caused 28 kills in 24h",
            project="SwarmAI",
        )
        assert result["doc"] == "IMPROVEMENT.md"
        assert result["section"] in ("What Failed", "What to Watch For")

    def test_success_lesson_routes_to_improvement_worked(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Queue-before-force pattern prevented all eviction regressions",
            project="SwarmAI",
        )
        assert result["doc"] == "IMPROVEMENT.md"
        assert result["section"] == "What Worked"

    def test_strategic_routes_to_product(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Non-goal: we will never support multi-model routing",
            project="SwarmAI",
        )
        assert result["doc"] == "PRODUCT.md"
        assert result["project"] == "SwarmAI"

    def test_cross_project_principle_routes_to_memory(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Design principle: asymmetric cost analysis should drive API defaults",
            project=None,
        )
        assert result["doc"] == "MEMORY.md"
        assert result["project"] is None

    def test_governance_content_detected(self):
        from core.persist_routing import classify_content
        # Governance requires BOTH action keyword AND target keyword
        result = classify_content(
            "New rule for STEERING: always run adversarial review before every session",
            project=None,
        )
        assert result["is_governance"] is True

    def test_technical_standing_rule_not_governance(self):
        """'Standing rule' about technical pattern is NOT governance."""
        from core.persist_routing import classify_content
        result = classify_content(
            "Standing rule: prefer atomic writes with tmp+rename pattern",
            project="SwarmAI",
        )
        assert result["is_governance"] is False

    def test_no_match_defaults_to_improvement(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Some generic observation about the session today with enough length",
            project="SwarmAI",
        )
        # Default should be IMPROVEMENT.md (experiential catch-all)
        assert result["doc"] == "IMPROVEMENT.md"

    def test_safe_auto_flag_present(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Bug: race condition in streaming path caused data loss",
            project="SwarmAI",
        )
        assert "safe_auto" in result
        assert isinstance(result["safe_auto"], bool)

    def test_product_is_not_safe_auto(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Strategic priority: focus on self-evolution over features",
            project="SwarmAI",
        )
        assert result["doc"] == "PRODUCT.md"
        assert result["safe_auto"] is False

    def test_project_none_with_technical_content_goes_to_memory(self):
        """Without project context, technical content goes to MEMORY (cross-project)."""
        from core.persist_routing import classify_content
        result = classify_content(
            "Convention: always use nc -z for port checks",
            project=None,
        )
        # No project → cross-project → MEMORY.md
        assert result["doc"] == "MEMORY.md"

    def test_confidence_returned(self):
        from core.persist_routing import classify_content
        result = classify_content(
            "Bug: regression caused by untested recovery path",
            project="SwarmAI",
        )
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0


class TestReflectLessonNotMisroutedToStrategicPriorities:
    """Regression: reflect-stage process lessons must NOT route to PRODUCT.md#Strategic
    Priorities (a protected zone that can't auto-apply). Root cause was
    `_PRODUCT_KEYWORDS` containing process-vocab (scope/phase/milestone), which made
    pipeline lessons trip the PRODUCT branch and pile up as escalations.

    Mutation check: re-add scope|phase|milestone to _PRODUCT_KEYWORDS → these 3
    real-sample assertions go RED (back to Strategic Priorities).
    """

    # 3 REAL misrouted proposal contents pulled from disk (escalated, Strategic Priorities)
    REAL_MISROUTED = [
        "Phase-1-of-3 rollout (F14): shipping dormant primitives + schema first, "
        "with zero behavior change verified by grep, correct C037/COE10 mitigation",
        "Scope discipline held: when the RTH category re-tag created a cross-system "
        "inconsistency, I REVERTED to surgical scope rather than expand into the "
        "pre-existing category drift.",
        "The honest re-scope was BETTER than the original ask: XG wanted usage "
        "decay-weighting; the real wiring makes used knowledge survive reclaim and "
        "resurface higher in injection.",
    ]

    def test_real_process_lessons_route_to_improvement_not_product(self):
        from core.persist_routing import classify_content
        for text in self.REAL_MISROUTED:
            result = classify_content(text, project="SwarmAI")
            assert result["doc"] == "IMPROVEMENT.md", (
                f"process lesson misrouted to {result['doc']}#{result['section']}: {text[:60]}"
            )
            assert result["section"] != "Strategic Priorities"

    def test_process_lessons_are_safe_auto(self):
        """The whole point: IMPROVEMENT routing is safe_auto → no escalation pile-up."""
        from core.persist_routing import classify_content
        for text in self.REAL_MISROUTED:
            result = classify_content(text, project="SwarmAI")
            assert result["safe_auto"] is True

    def test_genuine_strategic_statement_still_routes_to_product(self):
        """False-negative guard: real strategic content MUST stay in PRODUCT."""
        from core.persist_routing import classify_content
        for text in (
            "Strategic priority: focus on self-evolution over features",
            "User-facing latency is the top priority this quarter",
            "The product roadmap centers on cross-project memory",
        ):
            result = classify_content(text, project="SwarmAI")
            assert result["doc"] == "PRODUCT.md", f"strategic content dropped: {text}"

    def test_nongoal_and_vision_branches_unchanged(self):
        from core.persist_routing import classify_content
        assert classify_content(
            "Non-goal: we will never support multi-model routing", project="SwarmAI"
        )["doc"] == "PRODUCT.md"
        assert classify_content(
            "Vision: SwarmAI is a self-evolving Agent OS", project="SwarmAI"
        )["doc"] == "PRODUCT.md"


class TestRoutingTable:
    """The ROUTING_TABLE constant is well-formed."""

    def test_all_entries_have_required_fields(self):
        from core.persist_routing import ROUTING_TABLE
        for route_key, route in ROUTING_TABLE.items():
            assert "doc" in route, f"{route_key} missing 'doc'"
            assert "safe_auto" in route, f"{route_key} missing 'safe_auto'"

    def test_improvement_sections_are_safe_auto(self):
        from core.persist_routing import ROUTING_TABLE
        improvement_routes = {k: v for k, v in ROUTING_TABLE.items()
                             if v["doc"] == "IMPROVEMENT.md"}
        assert len(improvement_routes) >= 2
        for k, v in improvement_routes.items():
            assert v["safe_auto"] is True, f"{k} should be safe_auto"

    def test_product_is_never_safe_auto(self):
        from core.persist_routing import ROUTING_TABLE
        product_routes = {k: v for k, v in ROUTING_TABLE.items()
                         if v["doc"] == "PRODUCT.md"}
        for k, v in product_routes.items():
            assert v["safe_auto"] is False, f"{k} should NOT be safe_auto"

    def test_project_md_is_never_safe_auto(self):
        from core.persist_routing import ROUTING_TABLE
        project_routes = {k: v for k, v in ROUTING_TABLE.items()
                         if v["doc"] == "PROJECT.md"}
        for k, v in project_routes.items():
            assert v["safe_auto"] is False, f"{k} should NOT be safe_auto"
