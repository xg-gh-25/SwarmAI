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
    Priorities (a protected zone that can't auto-apply). Two root causes, two fixes:

    1. (run_eba5fc53) `_PRODUCT_KEYWORDS` contained process-vocab (scope/phase/milestone)
       → removed.
    2. (run_dca69c87) the `product_priority` catch-all fired on a SINGLE incidental
       product word (a lone priority/strategic/roadmap). Fix: the PRODUCT branch is
       entered only on product_hits>=2 OR an explicit-intent phrase (non-goal / vision /
       defer / mission / thesis). A lone incidental word falls through to the existing
       IMPROVEMENT/TECH selector — no keyword duplication, single source of truth.

    Mutation check: revert the >=2 threshold to product_hits>0 → the single-word
    real-sample assertions go RED (back to Strategic Priorities).
    """

    # REAL misrouted proposal contents pulled from disk (escalated, Strategic Priorities).
    # The first 3 were fixed by run_eba5fc53; the rest are the run_dca69c87 residuals —
    # each carries exactly ONE incidental product word (priority/strategic/roadmap/user-facing).
    REAL_MISROUTED = [
        "Phase-1-of-3 rollout (F14): shipping dormant primitives + schema first, "
        "with zero behavior change verified by grep, correct C037/COE10 mitigation",
        "Scope discipline held: when the RTH category re-tag created a cross-system "
        "inconsistency, I REVERTED to surgical scope rather than expand into the "
        "pre-existing category drift.",
        "The honest re-scope was BETTER than the original ask: XG wanted usage "
        "decay-weighting; the real wiring makes used knowledge survive reclaim and "
        "resurface higher in injection.",
        # run_dca69c87 residuals — single incidental product word:
        "Run C delivered the design's strategic core the HONEST way: it did NOT "
        "rebuild the aggregator, it wired the existing one and made it honest.",
        "Priority chain matters: pitfall beats correction because failure is a "
        "stronger signal than self-awareness language.",
        "This finding maps to roadmap kill-path #4 (streaming-timeout / R3c); the "
        "storm-amplifier is an independent backend bug kept out of scope.",
        "M3 WRONG-FRAME saved a dangerous default: the requirement said opt-OUT but "
        "the raw eval default must stay opt-IN because it spawns real agents.",
    ]

    def test_real_process_lessons_not_in_protected_product_zone(self):
        """The actual bug: process lessons must NOT land in a PRODUCT protected zone
        (Vision/Non-Goals/Strategic Priorities). Routing to IMPROVEMENT or TECH
        (experiential/technical, both safe_auto) is correct — the invariant is
        'not a protected PRODUCT zone', not a specific doc."""
        from core.persist_routing import classify_content
        for text in self.REAL_MISROUTED:
            result = classify_content(text, project="SwarmAI")
            assert not (
                result["doc"] == "PRODUCT.md"
                and result["section"] in ("Vision", "Non-Goals", "Strategic Priorities")
            ), f"process lesson misrouted to protected {result['doc']}#{result['section']}: {text[:60]}"

    def test_process_lessons_are_safe_auto(self):
        """The whole point: routing lands in a safe_auto zone → no escalation pile-up."""
        from core.persist_routing import classify_content
        for text in self.REAL_MISROUTED:
            result = classify_content(text, project="SwarmAI")
            assert result["safe_auto"] is True

    def test_genuine_strategic_statement_still_routes_to_product(self):
        """False-negative guard: real strategic content (>=2 product words OR an
        explicit-intent phrase) MUST stay in PRODUCT."""
        from core.persist_routing import classify_content
        for text in (
            "Strategic priority: focus on self-evolution over features",  # strategic+priority
            "User-facing latency is the top priority this quarter",       # user-facing+priority
            "Non-goal: defer the enterprise SSO roadmap indefinitely",    # non-goal intent phrase
        ):
            result = classify_content(text, project="SwarmAI")
            assert result["doc"] == "PRODUCT.md", f"strategic content dropped: {text}"

    def test_nongoal_and_vision_branches_unchanged(self):
        """Explicit-intent phrases route to PRODUCT even at a single hit."""
        from core.persist_routing import classify_content
        assert classify_content(
            "Non-goal: we will never support multi-model routing", project="SwarmAI"
        )["doc"] == "PRODUCT.md"
        assert classify_content(
            "Vision: SwarmAI is a self-evolving Agent OS", project="SwarmAI"
        )["doc"] == "PRODUCT.md"

    def test_single_product_word_statement_routes_to_improvement_by_design(self):
        """DOCUMENTED TRADEOFF (Gate-2 F1, run_dca69c87): a statement carrying exactly
        ONE product word and no explicit-intent phrase is WEAK strategic signal — it
        now routes to IMPROVEMENT (a safe_auto zone), NOT PRODUCT#Strategic Priorities.
        This is intentional: the escalation-pileup cost of admitting every lone
        'roadmap'/'strategic'/'priority' mention outweighs the rare mis-file of a genuine
        one-word product statement (which is auto-applied, not lost, and easily promoted).
        Genuine strategy almost always carries >=2 product words or an intent phrase."""
        from core.persist_routing import classify_content
        r = classify_content(
            "The product roadmap centers on cross-project memory", project="SwarmAI"
        )
        assert r["doc"] == "IMPROVEMENT.md"
        assert r["safe_auto"] is True  # not lost, not escalated — auto-applied

    def test_defer_a_task_is_not_a_product_nongoal(self):
        """Gate-2 F2: 'defer the retry/fix/cleanup' is a process idiom, not a product
        Non-Goal. As a lone trigger it must NOT route to the protected PRODUCT#Non-Goals
        zone. A genuine 'defer <feature>' strategic call (>=2 product hits) still does."""
        from core.persist_routing import classify_content
        r = classify_content(
            "We defer the retry until the flaky harness is stabilized", project="SwarmAI"
        )
        assert not (r["doc"] == "PRODUCT.md" and r["section"] == "Non-Goals")
        assert r["safe_auto"] is True
        # genuine strategic defer (defer + priority + strategic = >=2 hits) still PRODUCT:
        r2 = classify_content(
            "We are deferring multi-tenant to Q3 to prioritize the SDK — a strategic priority",
            project="SwarmAI",
        )
        assert r2["doc"] == "PRODUCT.md"


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
