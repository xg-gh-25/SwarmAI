"""Tests for the Gate-2 → arena feed (SCOPE B, run_4db42c78).

Two pieces:
  1. run-observe `adversarial_patterns` event accepts + persists the new
     action / first_pass_high / convergence_iterations fields.
  2. `_extract_run_metrics` auto-derives an `adversarial_patterns` block
     (by-SEVERITY, since category is not persisted in findings) + an ACTION
     context sourced from run_state stages — at the completion gate, without
     re-reading artifacts.

Design: Projects/SwarmAI/Designs/2026-07-11-gate2-verdict-arena-corpus-evolution-feed-design.md
Anti-poison invariant: this path NEVER writes corrections.jsonl / correction_tracker
/ auto_seed (AC6) — action(what the agent chose) stays separate from label(what the
environment verified).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_cli  # noqa: E402


class _Args:
    """Minimal argparse-namespace stand-in for cmd_run_observe."""
    def __init__(self, **kw):
        # every observe arg the handler may read, defaulting to None
        for k in ("project", "run_id", "event", "stage", "timestamp", "retries",
                  "reason", "partial", "tokens_consumed", "scope", "indicators",
                  "user_override", "files_estimated", "frontend", "backend",
                  "modules", "alternatives", "probes", "resolved", "escalated",
                  "categories", "rp_violations", "fixed", "dismissed",
                  "action", "first_pass_high", "convergence_iterations",
                  "review_count", "adversarial_count", "overlap"):
            setattr(self, k, None)
        for k, v in kw.items():
            setattr(self, k, v)


# ─────────────────────────────────────────────────────────────────────────
# Piece 1 — run-observe adversarial_patterns event carries the new fields
# ─────────────────────────────────────────────────────────────────────────

def test_event_round_trips_action_firstpass_convergence(tmp_path, monkeypatch):
    """AC1: emitting adversarial_patterns with --action/--first-pass-high/
    --convergence-iterations persists all three into METRICS.json."""
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({"id": "run_x", "project": "SwarmAI"}),
                        encoding="utf-8")
    monkeypatch.setattr(artifact_cli, "_resolve_run_file",
                        lambda p, r: run_file)

    args = _Args(
        project="SwarmAI", run_id="run_x", event="adversarial_patterns",
        categories=json.dumps({"correctness": 2}),
        first_pass_high="1",
        convergence_iterations="2",
        action=json.dumps({"profile": "full", "scope": "standard",
                           "approach": "A"}),
    )
    artifact_cli.cmd_run_observe(args, reg=None)

    metrics = json.loads((tmp_path / "METRICS.json").read_text(encoding="utf-8"))
    ap = metrics["adversarial_patterns"]
    assert ap["findings_by_category"] == {"correctness": 2}  # existing field intact
    assert ap["first_pass_high"] == 1
    assert ap["convergence_iterations"] == 2
    assert ap["action"] == {"profile": "full", "scope": "standard", "approach": "A"}


def test_event_defaults_new_fields_when_absent(tmp_path, monkeypatch):
    """AC4 (additive): omitting the new args yields empty/zero defaults — an
    existing emitter that passes only the old 4 args is unaffected."""
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({"id": "run_y"}), encoding="utf-8")
    monkeypatch.setattr(artifact_cli, "_resolve_run_file", lambda p, r: run_file)

    args = _Args(project="SwarmAI", run_id="run_y", event="adversarial_patterns",
                 categories=json.dumps({"security": 1}), fixed="1")
    artifact_cli.cmd_run_observe(args, reg=None)

    ap = json.loads((tmp_path / "METRICS.json").read_text())["adversarial_patterns"]
    assert ap["findings_by_category"] == {"security": 1}
    assert ap["findings_fixed"] == 1
    assert ap["action"] == {}
    assert ap["first_pass_high"] == 0
    assert ap["convergence_iterations"] == 0


# ─────────────────────────────────────────────────────────────────────────
# Piece 2′ — _extract_run_metrics derives adversarial_patterns
# ─────────────────────────────────────────────────────────────────────────

def _run_state_with_deliver(findings, *, profile="full", scope="standard",
                            approach="A: do the thing", convergence_iters=2):
    """A minimal completed run_state carrying a deliver adversarial_review +
    the evaluate/think stage records that hold the ACTION context."""
    return {
        "id": "run_test",
        "project": "SwarmAI",
        "status": "completed",
        "profile": profile,
        "stages": [
            {"stage": "evaluate", "status": "completed", "scope": scope},
            {"stage": "think", "status": "completed", "approach_chosen": approach},
            {
                "stage": "deliver", "status": "completed",
                "adversarial_review": {"spawned": True, "findings": findings},
                "convergence": {"iterations": convergence_iters},
            },
        ],
    }


def test_derives_adversarial_patterns_by_severity():
    """AC2: a completed run with categorized-by-severity findings produces an
    adversarial_patterns block counting high/med/low + resolved."""
    findings = [
        {"severity": "HIGH", "resolved": True, "finding": "core/a.py:1 bug"},
        {"severity": "MEDIUM", "resolved": True, "finding": "core/b.py:2 bug"},
        {"severity": "LOW", "resolved": False, "finding": "core/c.py:3 nit"},
    ]
    m = artifact_cli._extract_run_metrics("SwarmAI", "run_test",
                                          _run_state_with_deliver(findings))
    ap = m.get("adversarial_patterns")
    assert ap is not None, "adversarial_patterns block must be derived at completion"
    assert ap["by_severity"] == {"high": 1, "med": 1, "low": 1, "other": 0}
    assert ap["resolved"] == 2
    assert ap["first_pass_high"] == 1  # HIGH+CRITICAL count caught this run


def test_action_context_from_run_state_not_artifacts():
    """AC3: the ACTION block (what the agent CHOSE) is sourced from run_state
    stages — profile, evaluate.scope, think.approach_chosen — never a re-read."""
    findings = [{"severity": "HIGH", "resolved": True, "finding": "x"}]
    m = artifact_cli._extract_run_metrics(
        "SwarmAI", "run_test",
        _run_state_with_deliver(findings, profile="bugfix",
                                scope="complex", approach="B: mirror pattern"),
    )
    action = m["adversarial_patterns"]["action"]
    assert action["profile"] == "bugfix"
    assert action["scope"] == "complex"
    assert action["approach"] == "B: mirror pattern"


def test_no_adversarial_patterns_when_no_findings():
    """A run with an empty findings list produces NO adversarial_patterns block
    (nothing was caught → no signal to feed; label-variance protection).
    The KEY must be ABSENT, not present-with-None — locks the return guard
    (Gate-2 LOW: `is None` alone passed even if the guard were removed)."""
    m = artifact_cli._extract_run_metrics(
        "SwarmAI", "run_test", _run_state_with_deliver([]))
    assert "adversarial_patterns" not in m


def test_by_severity_partition_is_total():
    """Gate-2 MED: unknown/missing severities must NOT silently vanish — the
    partition high+med+low+other == len(findings) always holds."""
    findings = [
        {"severity": "HIGH", "resolved": True, "finding": "a"},
        {"severity": "INFO", "resolved": False, "finding": "b"},   # unknown
        {"severity": "", "resolved": False, "finding": "c"},        # missing
        {"finding": "d"},                                            # no severity key
    ]
    m = artifact_cli._extract_run_metrics(
        "SwarmAI", "run_test", _run_state_with_deliver(findings))
    bs = m["adversarial_patterns"]["by_severity"]
    assert bs == {"high": 1, "med": 0, "low": 0, "other": 3}
    assert sum(bs.values()) == len(findings)  # total partition invariant


def test_by_severity_counts_critical_as_high():
    """CRITICAL is counted in the high bucket + first_pass_high (consistent with
    the confidence gate's blocking severities)."""
    findings = [
        {"severity": "CRITICAL", "resolved": True, "finding": "x"},
        {"severity": "high", "resolved": True, "finding": "y"},  # lowercase
    ]
    m = artifact_cli._extract_run_metrics(
        "SwarmAI", "run_test", _run_state_with_deliver(findings))
    ap = m["adversarial_patterns"]
    assert ap["by_severity"]["high"] == 2
    assert ap["first_pass_high"] == 2


def test_convergence_iterations_carried_as_credit_signal():
    """convergence_iterations is carried (credit-assignment aid), NOT a reward."""
    findings = [{"severity": "MEDIUM", "resolved": True, "finding": "x"}]
    m = artifact_cli._extract_run_metrics(
        "SwarmAI", "run_test",
        _run_state_with_deliver(findings, convergence_iters=3))
    assert m["adversarial_patterns"]["convergence_iterations"] == 3


def test_arena_path_touches_no_evolution_writers():
    """AC6: the derive path must NOT import or CALL corrections.jsonl /
    correction_tracker / auto_seed writers. Guard by parsing the function's AST
    for import statements + attribute/name USAGE — not raw substring, so an
    explanatory comment naming the forbidden writers (to say it must NOT touch
    them) is not a false positive. A structural check the model can't rationalize
    past, and non-vacuous: it fails if real usage is introduced."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(artifact_cli._extract_run_metrics))
    tree = ast.parse(src)
    forbidden = {"correction_tracker", "auto_seed", "record_correction",
                 "CorrectionClassTracker", "auto_seed_case"}
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                used.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                used.update(node.module.split("."))
            used.update(a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
    leak = forbidden & used
    assert not leak, (
        f"_extract_run_metrics must not import/call {leak} (AC6 action≠label — "
        "the arena feed reads run_state + returns a dict, never writes evolution state)")
