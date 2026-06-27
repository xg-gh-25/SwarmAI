#!/usr/bin/env python3
"""golden_case_validator.py — quality gates for golden cases (run_69b1c644 Cycle 5).

The mechanism behind s_golden-case: a case cannot enter the corpus (ADD) or be
promoted to the shippable public set (PROMOTE) without passing these gates. This
is what makes adding cases "不能太随便".

Gates:
  G1 schema       — required fields present, valid types
  G2 duplicate    — structural similarity vs existing (same verification target)
  G3 non-vacuous  — assertion isn't trivially true (no echo-its-own-literal,
                    no match-anything grep)  [design's G4]
  privacy_scan    — (PROMOTE only) no sensitive words / instance-paths / DDD refs.
                    Instance cases are FINE in private; this is the ship boundary.

Note on G3-teeth (mutation): for programmatic cases the real teeth-test is the
fault-injection `--negative` flag in the case's harness (see fault_inject_*.py);
this validator enforces the *non-vacuous* static check. Full mutation-testing of
LLM/behavior cases is intentionally weaker (judge-fixture) — see the design.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_REQUIRED = ("id", "category", "dimension", "eval_method", "affected_by", "evaluators")

# Fields excluded from the content-bound stamp. _origin is injected by
# eval_service on load + stripped on write; validated_by_4gate is the stamp
# itself (circularity); tags/notes/promoted_from are eval_service
# _USER_OWNED_FIELDS — merge-mutable, never part of body identity. Excluding
# these makes the stamp invariant to a yaml round-trip (Gate-1 BLOCK-A: a naive
# sha256(dict) mismatches after the first CRUD re-serialize → BVT empties → gate
# RED forever).
_STAMP_EXCLUDED_FIELDS = frozenset(
    {"_origin", "validated_by_4gate", "tags", "notes", "promoted_from"}
)

# Fast-deterministic evaluators that make a case BVT-gate-eligible. MUST mirror
# eval_runner._GATE_ELIGIBLE_EVALUATORS — gate_teeth only applies to these.
_GATE_ELIGIBLE_EVALUATORS = frozenset(
    {"file_contains", "keyword_match", "trajectory_exact",
     "trajectory_in_order", "trajectory_any_order", "canary_pass"}
)


def compute_case_stamp(case: dict) -> str:
    """Content-bound stamp = sha256 of the CANONICAL case body.

    Canonical = JSON with sorted keys (order-invariant) over the body with
    _STAMP_EXCLUDED_FIELDS removed. This is the SAME function eval_runner.compute_bvt
    uses to recompute-and-compare, so a case whose body was edited outside the
    sanctioned 4-gate path (validated_by_4gate not updated) no longer matches →
    drops out of the BVT. Drift/negligence detection, NOT tamper-resistance
    (the algorithm is public; a deliberate editor can re-stamp — the freshness
    backstop for that is compute_code_digest)."""
    body = {k: v for k, v in case.items() if k not in _STAMP_EXCLUDED_FIELDS}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _is_gate_eligible(case: dict) -> bool:
    return (case.get("eval_method") != "llm"
            and bool(set(case.get("evaluators", [])) & _GATE_ELIGIBLE_EVALUATORS))


def gate_teeth(case: dict, grandfathered: bool = False) -> tuple[bool, list[str]]:
    """G3 teeth — a gate-eligible programmatic case must declare a negative check
    (verification.negative_command) so it can be proven to go RED on broken input.

    Only enforced on NEW (non-grandfathered) gate-eligible cases. Legacy cases
    predate this requirement and have zero negatives; enforcing it on them would
    fail-all → can't stamp → BVT empties (Gate-1 BLOCK-D). They are grandfathered
    until retrofitted. Non-gate-eligible cases (llm/behavior) never gate, so teeth
    does not apply."""
    if grandfathered or not _is_gate_eligible(case):
        return True, []
    v = case.get("verification", {}) or {}
    if not (v.get("negative_command") or "").strip():
        return False, ["teeth: gate-eligible case must declare verification.negative_command "
                       "(a command that proves the case goes RED on broken input)"]
    return True, []

# Privacy: sensitive words OR instance-structure references. Word-only is
# insufficient (Gate-1 #5: 145 cases ref instance paths w/o a sensitive word).
_SENSITIVE = re.compile(r"gawan|amazon\.com|cmhk|password|secret|aws_access|api_key", re.I)
_INSTANCE = re.compile(
    r"\.context/|(STEERING|AGENT|SOUL|MEMORY|EVOLUTION|USER|PRODUCT|TECH|IMPROVEMENT|PROJECT)"
    r"\.(md|R\d|P\d|PIT\d|DEC\d|PRI\d)|"
    # ANY project (not a hardcoded allowlist — a new project must not leak, Gate-2 M2)
    r"Projects/[A-Za-z0-9_-]+|"
    # other instance-only roots that carry workspace structure
    r"Knowledge/|Services/|\.artifacts/|Eval/|EvalHistory/",
    re.I,
)

# Vacuous: a command that just echoes the literal it asserts, or a grep that
# matches anything. These pass without testing anything real (GUI21).
_VACUOUS_CMDS = re.compile(r"^\s*(echo|printf|true|:)\b", re.I)


def gate_schema(case: dict) -> tuple[bool, list[str]]:
    errs = [f"missing required field: {f}" for f in _REQUIRED if not case.get(f)]
    if case.get("affected_by") is not None and not isinstance(case.get("affected_by"), list):
        errs.append("affected_by must be a list")
    if case.get("evaluators") is not None and not isinstance(case.get("evaluators"), list):
        errs.append("evaluators must be a list")
    return (not errs, errs)


def _verif_key(case: dict) -> str:
    v = case.get("verification", {}) or {}
    return f"{v.get('file','')}|{v.get('grep','')}|{v.get('command','')}|{v.get('expected_contains','')}"


def gate_duplicate(case: dict, existing: list[dict]) -> tuple[bool, list[str]]:
    key = _verif_key(case)
    if not key.strip("|"):
        return True, []  # no verification to compare (e.g. llm case) — skip structural dup
    for e in existing:
        if e.get("id") == case.get("id"):
            continue
        if _verif_key(e) == key:
            return False, [f"duplicate: same verification as existing case {e.get('id')}"]
    return True, []


def gate_non_vacuous(case: dict) -> tuple[bool, list[str]]:
    v = case.get("verification", {}) or {}
    cmd = (v.get("command") or "").strip()
    exp = (v.get("expected_contains") or "").strip()
    grep = (v.get("grep") or "").strip()
    # echo/printf that just re-emits the asserted literal = tests nothing
    if cmd and _VACUOUS_CMDS.match(cmd) and exp and exp in cmd:
        return False, [f"vacuous: command '{cmd}' trivially echoes its own assertion '{exp}'"]
    # grep that matches anything — only when a grep field is ACTUALLY present.
    # A missing grep ("" after .get) is NOT vacuous: canary_pass cases assert via
    # command/expected_contains and legitimately have no grep field. The prior
    # `grep in (..., "", ...)` conflated "no grep field" with "grep matches
    # anything" and false-killed every canary case (incl. the existing
    # GS_RCHAIN_* probes).
    if "grep" in v and grep in (".", ".*", "", "^"):
        return False, [f"vacuous: grep '{grep}' matches anything"]
    return True, []


def privacy_scan(case: dict) -> tuple[bool, list[str]]:
    blob = json.dumps(case, ensure_ascii=False)
    hits = []
    if _SENSITIVE.search(blob):
        hits.append("privacy: contains sensitive word (gawan/amazon.com/cmhk/secret…)")
    if _INSTANCE.search(blob):
        hits.append("privacy: references instance structure (.context/ / DDD doc / Projects/*) "
                    "— this case is instance-specific, keep it private")
    return (not hits, hits)


def validate_case(case: dict, existing: list[dict], for_public: bool,
                  grandfathered: bool = False) -> tuple[bool, dict]:
    """Run all gates. privacy_scan runs ONLY for_public (PROMOTE). gate_teeth is
    skipped for grandfathered (legacy) cases (Gate-1 BLOCK-D). Returns (ok, report)
    where report maps gate name → (ok, errors). On a clean pass, report["stamp"]
    carries the content-bound validated_by_4gate value for the caller to persist."""
    report = {}
    g1 = gate_schema(case); report["schema"] = g1
    g2 = gate_duplicate(case, existing); report["duplicate"] = g2
    g3 = gate_non_vacuous(case); report["non_vacuous"] = g3
    g4 = gate_teeth(case, grandfathered=grandfathered); report["teeth"] = g4
    gates = [g1, g2, g3, g4]
    if for_public:
        gp = privacy_scan(case); report["privacy"] = gp
        gates.append(gp)
    ok = all(g[0] for g in gates)
    if ok:
        report["stamp"] = compute_case_stamp(case)
    return ok, report


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a golden case (4-gate).")
    ap.add_argument("--case-file", required=True, help="JSON file with one case")
    ap.add_argument("--for-public", action="store_true", help="run privacy gate (PROMOTE)")
    args = ap.parse_args()
    case = json.loads(Path(args.case_file).read_text())
    ok, report = validate_case(case, existing=[], for_public=args.for_public)
    for gate, result in report.items():
        # report["stamp"] (clean-pass only) is a str, not a (ok, errs) gate
        # tuple — skip it in the gate summary or the unpack crashes (exit 1
        # on every successful validation).
        if gate == "stamp":
            continue
        g_ok, errs = result
        mark = "✓" if g_ok else "✗"
        print(f"  {mark} {gate}" + ("" if g_ok else f": {'; '.join(errs)}"))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
