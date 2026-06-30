"""Tests for gate_refs (golden_case_validator) + EVOLUTION.-prefix resolver support
(eval_runner._resolve_reference). The anti-drift structural gate from run_b1efcb5b /
correction C044: a golden case's dotted refs (MEMORY./STEERING./AGENT./SOUL./EVOLUTION.)
MUST resolve to non-empty content, or the case silently feeds the judge wrong/empty
context (axis 1 of the 5-axis eval-usefulness rubric, TECH.md Eval Golden-Case Policy).

Mutation-proof: each gate test asserts BOTH a drifted/empty ref is REJECTED and a
resolvable ref is ACCEPTED — flipping the gate's predicate flips a test (non-vacuous).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_runner  # noqa: E402
import golden_case_validator as gcv  # noqa: E402

ROOT = Path.home() / ".swarm-ai" / "SwarmWS"


# ─── EVOLUTION. resolver support (eval_runner._resolve_reference) ───────────────

def test_evolution_correction_id_resolves_nonempty():
    """EVOLUTION.C012 must resolve to the C012 correction body (bold **C012** form),
    not empty. Before the fix the resolver had no EVOLUTION. branch → returned ''."""
    out = eval_runner._resolve_reference("EVOLUTION.C012", ROOT)
    assert out, "EVOLUTION.C012 resolved EMPTY — resolver missing EVOLUTION. branch"
    assert "C012" in out


def test_evolution_class_name_resolves_nonempty():
    """EVOLUTION.CLASS_A must resolve to the '### CLASS A' heading section
    (underscore→space normalization)."""
    out = eval_runner._resolve_reference("EVOLUTION.CLASS_A", ROOT)
    assert out, "EVOLUTION.CLASS_A resolved EMPTY"
    assert "CLASS A" in out


def test_evolution_unknown_id_resolves_empty():
    """A non-existent EVOLUTION id stays empty (so gate_refs can still reject it)."""
    assert eval_runner._resolve_reference("EVOLUTION.C999_NOPE", ROOT) == ""


# ─── gate_refs (golden_case_validator) ──────────────────────────────────────────

def _case(refs, source="X"):
    return {
        "id": "GS_TEST", "category": "decision", "dimension": "judgment_quality",
        "eval_method": "llm", "evaluators": ["goal_success"],
        "affected_by": refs, "source": source,
    }


def test_gate_refs_rejects_empty_dotted_ref():
    """A dotted ref that resolves empty (STEERING.R1 — moved to AGENT.md) must FAIL."""
    ok, errs = gcv.gate_refs(_case(["STEERING.R1"]), root=ROOT)
    assert not ok, "gate_refs accepted STEERING.R1 which resolves EMPTY"
    assert any("STEERING.R1" in e for e in errs)


def test_gate_refs_rejects_evolution_when_unresolvable():
    """EVOLUTION.C999_NOPE (unknown) must FAIL gate_refs."""
    ok, errs = gcv.gate_refs(_case(["EVOLUTION.C999_NOPE"]), root=ROOT)
    assert not ok


def test_gate_refs_accepts_resolvable_dotted_refs():
    """Resolvable refs (MEMORY.DEC39 RSS, AGENT.R1, EVOLUTION.C012) must PASS."""
    ok, errs = gcv.gate_refs(_case(["MEMORY.DEC39", "AGENT.R1", "EVOLUTION.C012"]), root=ROOT)
    assert ok, f"gate_refs rejected resolvable refs: {errs}"


def test_gate_refs_ignores_bare_md_filenames():
    """Gate-2 BLOCKER (run_b1efcb5b): bare filename refs (MEMORY.md / AGENT.md /
    EVOLUTION.md) are WHOLE-FILE refs (the auto-seed hook uses them), NOT entry/rule
    ids — they must be OUT OF SCOPE, never rejected. The `.md` token previously
    matched _DOTTED_REF and got wrongly rejected."""
    ok, errs = gcv.gate_refs(_case(["MEMORY.md", "AGENT.md", "EVOLUTION.md",
                                     "STEERING.md", "SOUL.md"]), root=ROOT)
    assert ok, f"gate_refs wrongly rejected bare .md filenames: {errs}"


def test_gate_refs_ignores_bare_identifiers():
    """Bare non-dotted ids (GC12, 'Pipeline Rule 23') are OUT OF SCOPE — gate_refs
    only governs the dotted-prefix forms (matching _resolve_reference's prefix gate).
    A bare id is not claimed to resolve, so it must not FALSE-REJECT."""
    ok, errs = gcv.gate_refs(_case(["GC12", "Pipeline Rule 23"]), root=ROOT)
    assert ok, f"gate_refs false-rejected bare identifiers: {errs}"


def test_gate_refs_ignores_slash_paths_that_exist():
    """A real repo/workspace slash-path resolves; gate_refs accepts it."""
    ok, errs = gcv.gate_refs(_case(["Projects/SwarmAI/TECH.md"]), root=ROOT)
    assert ok, f"gate_refs rejected an existing path: {errs}"


def test_gate_refs_checks_source_field_too():
    """source carrying a dotted ref is validated like affected_by (drift hides there too)."""
    ok, errs = gcv.gate_refs(_case(["AGENT.R1"], source="MEMORY.DEC16-but-claims-RSS"), root=ROOT)
    # DEC16 resolves (dark-theme) so non-empty → passes the EMPTY check; source dotted parse:
    # 'MEMORY.DEC16-but-claims-RSS' is not a clean dotted id, treated as prose → ignored.
    assert ok
