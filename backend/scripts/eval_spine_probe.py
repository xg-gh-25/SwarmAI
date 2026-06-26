#!/usr/bin/env python3
"""eval_spine_probe.py — deterministic spine canaries for the BVT gate
(run_5edf2cc0 C4, gap G4).

The pre-existing gate set covered only factual_accuracy/capability dims. These
probes add the SPINE: the invariants whose failure means "Swarm is unsafe or
degraded to run at all" — context-isolation (privacy), the gate's own freshness
binding, and prompt-budget. Each subcommand prints `<NAME>_OK` (exit 0) or
`<NAME>_FAIL ...` (exit 1). A trailing `negative` arg flips the assertion so the
teeth gate (golden_case_validator.gate_teeth) can prove the probe goes RED on a
broken invariant — structural, no LLM, sub-second.

Subcommands:
  safe_group_exclude   — group-channel context MUST drop MEMORY.md + USER.md
  safe_nonowner_exclude — non-owner (light) channel MUST drop EVOLUTION.md + PROJECTS.md
  gate_freshness       — ci_eval_gate's code_digest changes when an eval input changes
  prompt_budget        — effective context budget stays within the model window cap
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ok(name: str) -> int:
    print(f"{name}_OK")
    return 0


def _fail(name: str, why: str) -> int:
    print(f"{name}_FAIL {why}")
    return 1


def _check_exclude(name: str, attr: str, required: set, negative: bool) -> int:
    """Assert the REAL context_directory_loader exclude-set covers `required`.

    negative mode: monkeypatch the actual module attribute to a BROKEN set
    (missing one required file) and assert this very check then returns FAIL.
    This tests the REAL invariant + the check's own teeth (not local set-arithmetic
    — adversarial MEDIUM #4: the prior version subtracted from a local copy, a
    tautology of set semantics that never touched the imported symbol)."""
    import core.context_directory_loader as cdl
    if negative:
        saved = getattr(cdl, attr)
        try:
            setattr(cdl, attr, frozenset(set(saved) - {next(iter(required))}))
            broke = _check_exclude(name, attr, required, negative=False)
        finally:
            setattr(cdl, attr, saved)
        # The positive check MUST have failed (rc 1) on the broken set.
        return _ok(name) if broke != 0 else _fail(name, "negative did not break the real invariant")
    current = set(getattr(cdl, attr))
    return _ok(name) if required.issubset(current) else _fail(name, f"{attr} missing {required - current}")


def safe_group_exclude(negative: bool) -> int:
    """Group-channel prompts must exclude MEMORY.md + USER.md (privacy)."""
    return _check_exclude("SAFE_GROUP", "GROUP_CHANNEL_EXCLUDE", {"MEMORY.md", "USER.md"}, negative)


def safe_nonowner_exclude(negative: bool) -> int:
    """Non-owner light-channel prompts must exclude EVOLUTION.md + PROJECTS.md."""
    return _check_exclude("SAFE_NONOWNER", "CHANNEL_LIGHT_EXCLUDE", {"EVOLUTION.md", "PROJECTS.md"}, negative)


def gate_freshness(negative: bool) -> int:
    """The gate's code_digest MUST change when an eval input changes — the
    meta-test that the freshness binding actually binds (if this breaks, every
    other gate is fake-green)."""
    from scripts.eval_runner import compute_code_digest, _find_swarmai_repo, _GATE_CODE_PATHS
    name = "GATE_FRESH"
    root = Path.home() / ".swarm-ai" / "SwarmWS"
    try:
        repo = _find_swarmai_repo()
    except Exception as e:
        return _fail(name, f"cannot find repo: {e}")
    base = compute_code_digest(root, code_root=repo)
    if negative:
        # Broken invariant: a digest computed over an empty path list must NOT
        # equal the real digest (proves the digest actually depends on inputs).
        import scripts.eval_runner as er
        saved = er._GATE_CODE_PATHS
        try:
            er._GATE_CODE_PATHS = []
            empty = compute_code_digest(root, code_root=repo)
        finally:
            er._GATE_CODE_PATHS = saved
        return _ok(name) if empty != base else _fail(name, "digest ignored its inputs")
    # Positive: digest is a stable non-empty 16-hex over real inputs.
    again = compute_code_digest(root, code_root=repo)
    stable = base == again and len(base) == 16 and bool(_GATE_CODE_PATHS)
    return _ok(name) if stable else _fail(name, f"unstable/empty digest base={base} again={again}")


def prompt_budget(negative: bool) -> int:
    """The effective context budget must stay within the model window (over-budget
    = silent truncation = degraded cognition)."""
    from core.context_directory_loader import ContextDirectoryLoader
    name = "PROMPT_BUDGET"
    loader = ContextDirectoryLoader(Path.home() / ".swarm-ai" / "SwarmWS" / ".context")
    window = 1_000_000

    def _within(w: int) -> bool:
        # Real invariant: the REAL compute_token_budget must return a positive
        # budget strictly inside the window (over-budget = silent truncation).
        b = loader.compute_token_budget(w)
        return 0 < b < w

    if negative:
        # compute_token_budget is correctly bounded at every real window (it scales
        # the tier DOWN), so there is no breaking INPUT. To prove the check has
        # teeth, monkeypatch the REAL method to an over-window impl and assert this
        # very check then catches it (same discipline as the exclude probes).
        saved = loader.compute_token_budget
        try:
            loader.compute_token_budget = lambda w: w + 1  # broken: exceeds window
            broke = _within(window)
        finally:
            loader.compute_token_budget = saved
        return _ok(name) if not broke else _fail(name, "negative did not break the real budget check")
    return _ok(name) if _within(window) else _fail(name, f"budget out of bounds for window {window}")


_PROBES = {
    "safe_group_exclude": safe_group_exclude,
    "safe_nonowner_exclude": safe_nonowner_exclude,
    "gate_freshness": gate_freshness,
    "prompt_budget": prompt_budget,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _PROBES:
        print(f"usage: eval_spine_probe.py <{'|'.join(_PROBES)}> [negative]", file=sys.stderr)
        return 2
    negative = len(sys.argv) > 2 and sys.argv[2] == "negative"
    return _PROBES[sys.argv[1]](negative)


if __name__ == "__main__":
    sys.exit(main())
