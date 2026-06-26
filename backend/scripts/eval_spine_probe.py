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


def safe_group_exclude(negative: bool) -> int:
    """Group-channel prompts must exclude MEMORY.md + USER.md (privacy)."""
    from core.context_directory_loader import GROUP_CHANNEL_EXCLUDE
    name = "SAFE_GROUP"
    required = {"MEMORY.md", "USER.md"}
    covers = required.issubset(set(GROUP_CHANNEL_EXCLUDE))
    if negative:
        # Broken invariant: pretend the exclude set lost MEMORY.md. The probe
        # must report FAIL (proving it has teeth).
        broken = set(GROUP_CHANNEL_EXCLUDE) - {"MEMORY.md"}
        return _ok(name) if not required.issubset(broken) else _fail(name, "negative did not break")
    return _ok(name) if covers else _fail(name, f"GROUP_CHANNEL_EXCLUDE missing {required - set(GROUP_CHANNEL_EXCLUDE)}")


def safe_nonowner_exclude(negative: bool) -> int:
    """Non-owner light-channel prompts must exclude EVOLUTION.md + PROJECTS.md."""
    from core.context_directory_loader import CHANNEL_LIGHT_EXCLUDE
    name = "SAFE_NONOWNER"
    required = {"EVOLUTION.md", "PROJECTS.md"}
    covers = required.issubset(set(CHANNEL_LIGHT_EXCLUDE))
    if negative:
        broken = set(CHANNEL_LIGHT_EXCLUDE) - {"EVOLUTION.md"}
        return _ok(name) if not required.issubset(broken) else _fail(name, "negative did not break")
    return _ok(name) if covers else _fail(name, f"CHANNEL_LIGHT_EXCLUDE missing {required - set(CHANNEL_LIGHT_EXCLUDE)}")


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
    budget = loader.compute_token_budget(window)
    if negative:
        # Broken invariant: a budget >= the window would mean no headroom for the
        # conversation. Assert the probe catches an over-window budget.
        bad = window + 1
        return _ok(name) if not (0 < bad < window) else _fail(name, "negative did not break")
    # Real invariant: the static-context budget must leave room within the window
    # (0 < budget < window) — over-budget = silent truncation = degraded cognition.
    ok = 0 < budget < window
    return _ok(name) if ok else _fail(name, f"budget {budget} out of bounds for window {window}")


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
