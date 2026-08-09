"""Declared-type honoring in classify_content (root-fix Run 1, run_c7e1e39d).

ROOT: a REFLECT lesson's TYPE is known at author-time, but classify_content
re-derives the route purely from prose keywords — and its project-scoped branch
STRUCTURALLY cannot reach the high-order homes (decision/principle/correction),
so those types are starved (measured: pitfall 303 / guideline 251 / decision 17).

FIX (coherence-scoped authority): a declared, validated leading `[type]` prefix:
  - HIGH-ORDER (decision/principle/correction) → authoritative TYPE_ROUTE home
    (keyword routing bypassed — it can never reach these homes).
  - OPERATIONAL (guideline/pitfall/process/model) → tag stripped, keyword routing
    keeps picking the finer section, BUT a protected-zone landing is remapped to
    the type-coherent default (a [pitfall] must never be dropped in PRODUCT§Non-Goals).
  - INVALID / ABSENT → unchanged keyword routing (strangler-fig: byte-identical).

These tests are the executable contract for that behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.persist_routing import classify_content, TYPE_ROUTE, ROUTING_TABLE
from core.ddd_entry_lifecycle import VALID_TYPES


# ── AC1 + AC4: high-order declared types route to their coherent home ──────────

@pytest.mark.parametrize(
    "declared, exp_doc, exp_section",
    [
        ("decision", "PROJECT.md", "Recent Decisions"),
        ("principle", "PRODUCT.md", "Design Philosophy — When Beliefs Become Enforcement"),
        ("correction", "IMPROVEMENT.md", "What Failed"),
    ],
)
def test_high_order_declared_type_routes_to_home(declared, exp_doc, exp_section):
    # Prose that WOULD keyword-route somewhere else entirely.
    text = f"[{declared}] the pipeline caught a race that silently broke the reconcile"
    r = classify_content(text, project="SwarmAI")
    assert r["doc"] == exp_doc, f"{declared}: doc {r['doc']} != {exp_doc}"
    assert r["section"] == exp_section, f"{declared}: section {r['section']!r} != {exp_section!r}"
    # High-order homes are human-curated — never auto-apply.
    assert r["safe_auto"] is False, f"{declared} must escalate (safe_auto=False)"


def test_high_order_home_sections_exist_in_routing_table():
    # Each TYPE_ROUTE high-order route_key must resolve through ROUTING_TABLE.
    # safe_auto is enforced at classify_content's RETURN (a per-proposal property),
    # NOT necessarily in the table — correction re-homes to `what_failed` whose table
    # default is auto=True, but a DECLARED [correction] escalates. So assert the
    # CONTRACT (the returned value), not the raw table default.
    for t in ("decision", "principle", "correction"):
        route_key = TYPE_ROUTE[t]
        assert route_key in ROUTING_TABLE, f"{t} -> {route_key} missing from ROUTING_TABLE"
        r = classify_content(f"[{t}] a declared high-order lesson about the system design", project="SwarmAI")
        assert r["safe_auto"] is False, f"declared [{t}] must escalate at return"


def test_mutation_flip_tag_changes_route():
    """AC4 coherence: the SAME prose with a different declared type routes to a
    different home — proving the route is driven by the declaration, not the prose."""
    base = "the system chose X over Y because the io pool would starve under load"
    r_dec = classify_content(f"[decision] {base}", project="SwarmAI")
    r_pri = classify_content(f"[principle] {base}", project="SwarmAI")
    assert r_dec["doc"] == "PROJECT.md"
    assert r_pri["doc"] == "PRODUCT.md"
    assert r_dec["section"] != r_pri["section"]


# ── AC2: strangler-fig — undeclared lessons route byte-identically to today ────

# Frozen snapshot of TODAY's keyword routing for bare (undeclared) lessons.
# If any of these change, strangler-fig is broken.
_BARE_CASES = [
    ("asyncio.to_thread for subprocess in async context prevents pool exhaustion",
     "TECH.md"),
    ("SMOKE caught 2 runtime crashes that unit tests missed — highest ROI",
     "IMPROVEMENT.md"),
    ("the reconcile race broke tab switching and silently dropped messages",
     "IMPROVEMENT.md"),
]


@pytest.mark.parametrize("text, exp_doc", _BARE_CASES)
def test_bare_lesson_routes_unchanged(text, exp_doc):
    r = classify_content(text, project="SwarmAI")
    assert r["doc"] == exp_doc


# ── AC3: invalid declared type is NOT honored — falls through to keyword ───────

def test_invalid_declared_type_falls_through():
    # [decsion] (typo) and [foobar] must NOT be treated as a route directive.
    typo = classify_content("[decsion] chose X over Y for the io pool", project="SwarmAI")
    bogus = classify_content("[foobar] the async subprocess convention", project="SwarmAI")
    # Neither lands in PROJECT§Recent Decisions (the real decision home) —
    # they route by keyword like any bare lesson.
    assert not (typo["doc"] == "PROJECT.md" and typo["section"] == "Recent Decisions")
    # Bogus keyword-routes (a convention word present) — must be a keyword result.
    assert bogus["doc"] in ("TECH.md", "IMPROVEMENT.md", "PRODUCT.md")


def test_valid_type_set_matches_lifecycle():
    """The declared-type validation set is the SAME 7 as ddd_entry_lifecycle —
    no 4th private copy that could drift."""
    from core.persist_routing import _DECLARED_TYPES
    assert set(_DECLARED_TYPES) == set(VALID_TYPES)


# ── AC5: operational tag is stripped so it can't pollute keyword hits ──────────

def test_operational_tag_stripped_no_keyword_pollution():
    # A [pitfall] tag on a lesson whose BODY has no failure words must not be
    # forced to What-Failed by the literal word "pitfall" in the tag.
    r = classify_content("[guideline] prefer atomic writes with a single writer", project="SwarmAI")
    # 'guideline' operational -> keyword routes the BODY (a convention).
    assert r["doc"] == "TECH.md"


# ── AC4 (fence): operational declared tag must not be dropped in a protected zone ─

@pytest.mark.parametrize("prefix", ["", "[decision] ", "[principle] ", "[correction] "])
def test_governance_outranks_declared_type(prefix):
    """Gate-2 Security Finding 1: a governance rule-change (action+target) MUST hit the
    governance gate even when tagged with a high-order [type] — the tag must not reroute
    it into an ordinary DDD section and skip the human-governance boundary."""
    gov = ("From now on, add rule: every session must always run the adversarial "
           "review before deliver in STEERING")
    r = classify_content(f"{prefix}{gov}", project="SwarmAI")
    assert r["is_governance"] is True, f"{prefix!r} must not bypass governance"
    assert r["route_key"] == "governance"
    assert r["doc"] == "AGENT.md"


def test_operational_tag_fenced_from_protected_zone():
    """A [pitfall] whose prose trips PRODUCT keywords must NOT land in a protected
    PRODUCT zone (where it would be dropped as skipped_protected) — it is remapped
    to the type-coherent default (What Failed)."""
    r = classify_content(
        "[pitfall] the strategic roadmap vision non-goal broke when the race fired",
        project="SwarmAI",
    )
    assert r["doc"] != "PRODUCT.md", "operational tag must be fenced from PRODUCT protected zone"
    assert r["safe_auto"] is True, "type-coherent operational default is auto-applicable"
