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
import json
import re
import sys
from pathlib import Path

_REQUIRED = ("id", "category", "dimension", "eval_method", "affected_by", "evaluators")

# Privacy: sensitive words OR instance-structure references. Word-only is
# insufficient (Gate-1 #5: 145 cases ref instance paths w/o a sensitive word).
_SENSITIVE = re.compile(r"gawan|amazon\.com|cmhk|password|secret|aws_access|api_key", re.I)
_INSTANCE = re.compile(
    r"\.context/|(STEERING|AGENT|SOUL|MEMORY|EVOLUTION|USER|PRODUCT|TECH|IMPROVEMENT|PROJECT)"
    r"\.(md|R\d|P\d|PIT\d|DEC\d|PRI\d)|"
    # ANY project (not a hardcoded allowlist — a new project must not leak, Gate-2 M2)
    r"Projects/[A-Za-z0-9_-]+|"
    # other instance-only roots that carry workspace structure
    r"Knowledge/|Services/|\.artifacts/|EvalHistory/",
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
    # grep that matches anything
    if grep in (".", ".*", "", "^"):
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


def validate_case(case: dict, existing: list[dict], for_public: bool) -> tuple[bool, dict]:
    """Run all gates. privacy_scan runs ONLY for_public (PROMOTE). Returns
    (ok, report) where report maps gate name → (ok, errors)."""
    report = {}
    g1 = gate_schema(case); report["schema"] = g1
    g2 = gate_duplicate(case, existing); report["duplicate"] = g2
    g3 = gate_non_vacuous(case); report["non_vacuous"] = g3
    gates = [g1, g2, g3]
    if for_public:
        gp = privacy_scan(case); report["privacy"] = gp
        gates.append(gp)
    ok = all(g[0] for g in gates)
    return ok, report


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a golden case (4-gate).")
    ap.add_argument("--case-file", required=True, help="JSON file with one case")
    ap.add_argument("--for-public", action="store_true", help="run privacy gate (PROMOTE)")
    args = ap.parse_args()
    case = json.loads(Path(args.case_file).read_text())
    ok, report = validate_case(case, existing=[], for_public=args.for_public)
    for gate, (g_ok, errs) in report.items():
        mark = "✓" if g_ok else "✗"
        print(f"  {mark} {gate}" + ("" if g_ok else f": {'; '.join(errs)}"))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
