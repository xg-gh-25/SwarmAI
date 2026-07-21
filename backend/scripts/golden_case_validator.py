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
  gate_teeth      — (new gate-eligible only) declares verification.negative_command
  gate_refs       — (non-grandfathered) dotted refs resolve non-empty (anti-drift)
  gate_redline    — if `redline` present it must be bool; if true it must carry a
                    RUNNABLE evaluator (else it always-skips = always-passes = an
                    unenforceable red-line). run_21490939.
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

# EVERY evaluator eval_runner.evaluate_case knows how to run (deterministic +
# llm). MUST equal eval_runner's PROGRAMMATIC|LLM|BEHAVIOR canonical union — the
# mirror is CI-enforced by test_runnable_evaluators_mirror_eval_runner_dispatch
# (hand-copied, not module-level-derived, to avoid the circular import:
# eval_runner imports compute_case_stamp from THIS module). A case whose
# evaluators are ALL outside this set returns 'skipped' at runtime ("No supported
# evaluator") — benign for a normal case, but for a RED-LINE it is an evasion
# (always-skip = always-pass, Gate-1 F3), so gate_redline refuses it.
_RUNNABLE_EVALUATORS = frozenset(
    {"file_contains", "keyword_match", "trajectory_exact", "trajectory_in_order",
     "trajectory_any_order", "canary_pass", "runtime_health", "recall_at_k",
     "trajectory_capture", "goal_success", "quality_score"}
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


def gate_redline(case: dict) -> tuple[bool, list[str]]:
    """A red-line (zero-tolerance) marker must be VALID and ENFORCEABLE.

    ``redline`` is the severity field eval_runner.compute_redline vetoes on: any
    red-line case that FAILS/ERRORS forces the whole eval NO-GO regardless of the
    aggregate % or eval_method. Because it is that powerful, the marker itself is
    gated:
      1. If present, ``redline`` MUST be a real ``bool`` — a string "true" / int 1
         would be truthy-but-wrong and silently mis-gate.
      2. If ``redline is True``, the case MUST carry at least one RUNNABLE evaluator
         (eval_runner knows how to execute it). Closes the Gate-1 F3 evasion: a
         red-line whose evaluators are all unknown returns 'skipped' at runtime,
         and compute_redline treats skipped as not-a-violation (correct for the
         canary-skip case) — so an unrunnable red-line would always-skip =
         always-pass, a red-line that can never fire. Refuse it at the gate.

    redline absent / False → not a red-line, nothing to validate here (a junk
    evaluator on a non-red-line is caught by other gates, not this one)."""
    rl = case.get("redline")
    if rl is None:
        return True, []
    if not isinstance(rl, bool):
        return False, [f"redline: must be a boolean (got {type(rl).__name__} {rl!r})"]
    if rl is True:
        evs = case.get("evaluators") or []
        if not (set(evs) & _RUNNABLE_EVALUATORS):
            return False, ["redline: a zero-tolerance case must declare at least one "
                           "RUNNABLE evaluator (else it always 'skips' at runtime and "
                           f"the veto never fires). Known evaluators: {sorted(_RUNNABLE_EVALUATORS)}"]
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


# ─── gate_refs (anti-drift, run_b1efcb5b / C044) ─────────────────────────────
# A golden case's dotted refs (MEMORY./STEERING./AGENT./SOUL./EVOLUTION.) must
# resolve to NON-EMPTY content, or the case silently feeds the LLM judge wrong or
# empty context (axis 1 of the 5-axis eval-usefulness policy — TECH.md). This is
# the structural fix for the silent-drift class: STEERING.RX refs went empty after
# the 2026-06-27 reorg, EVOLUTION. wasn't resolved at all, MEMORY ids reshuffled.
#
# Self-contained resolver (NOT an import of eval_runner — eval_runner imports THIS
# module at runtime, so importing it back is a circular import). It MIRRORS
# eval_runner._resolve_reference's prefix gate: only the dotted forms are governed;
# bare identifiers (GC12, "Pipeline Rule 23") and slash-paths are out of scope here
# (bare ids are never claimed to resolve; paths are checked by existence only).

# Match a clean dotted ENTRY/RULE ref like MEMORY.DEC39 / AGENT.R1 / EVOLUTION.CLASS_A.
# Trailing token allows letters/digits/underscore (CLASS_A, R16b, PIT172).
# `(?!md$)` EXCLUDES the bare filename forms MEMORY.md / AGENT.md / EVOLUTION.md etc.
# — those are whole-file refs (the auto-seed hook uses them), NOT entry/rule ids, and
# must stay out of scope or gate_refs would reject a legitimate bare-filename ref
# (Gate-2 BLOCKER, run_b1efcb5b). `.md` is a filename, not an entry to resolve.
_DOTTED_REF = re.compile(r"^(MEMORY|STEERING|AGENT|SOUL|EVOLUTION)\.(?!md$)([A-Za-z0-9_]+)$")


def _ctx_path(root: Path, name: str) -> Path:
    return root / ".context" / name


def _ref_resolves(ref: str, root: Path) -> bool:
    """True iff a dotted ref resolves to non-empty content in the live .context files.
    Mirrors eval_runner's resolution semantics closely enough to catch drift/empties."""
    m = _DOTTED_REF.match(ref.strip())
    if not m:
        return True  # not a governed dotted ref → out of scope, never false-reject
    kind, key = m.group(1), m.group(2)

    if kind == "MEMORY":
        txt = _read(_ctx_path(root, "MEMORY.md"))
        return f"[{key}]" in txt
    if kind in ("STEERING", "AGENT", "SOUL"):
        txt = _read(_ctx_path(root, f"{kind}.md"))
        # eval_runner._extract_rule patterns: '### R1' / '**R1**' / 'R1.' / 'R1:'
        return any(p in txt for p in (f"### {key}", f"**{key}**", f"{key}.", f"{key}:"))
    if kind == "EVOLUTION":
        txt = _read(_ctx_path(root, "EVOLUTION.md"))
        norm = key.replace("_", " ")  # mirror eval_runner._extract_evolution_entry
        if norm.upper().startswith("CLASS "):
            return f"### {norm}:" in txt or f"### {norm}\n" in txt
        # correction id: eval_runner matches the NORMALIZED bold marker, not raw key
        return f"**{norm}**" in txt
    return True


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


def _dotted_refs_in(case: dict) -> list[str]:
    refs = list(case.get("affected_by") or [])
    src = case.get("source")
    if isinstance(src, str):
        # source may be prose ("DEC40 + GUI184") — only pull clean dotted tokens
        refs += [tok for tok in re.split(r"[\s,+]+", src) if _DOTTED_REF.match(tok)]
    return [r for r in refs if isinstance(r, str) and _DOTTED_REF.match(r.strip())]


def gate_refs(case: dict, root: Path | None = None) -> tuple[bool, list[str]]:
    """Every dotted ref (affected_by + source) must resolve non-empty. Bare ids and
    slash-paths are out of scope (mirrors eval_runner's prefix gate). Returns (ok, errors).

    Fail-OPEN when the workspace has no .context/ dir: refs have nowhere to resolve
    (channel/hive deploy, or a test tmp workspace), so the gate cannot judge drift and
    must not false-reject. Drift validation only applies where the context files exist."""
    if root is None:
        root = Path.home() / ".swarm-ai" / "SwarmWS"
    if not (root / ".context").is_dir():
        return (True, [])  # no context to resolve against → cannot validate, fail-open
    errs = []
    for ref in _dotted_refs_in(case):
        if not _ref_resolves(ref, root):
            errs.append(f"refs: '{ref}' resolves to EMPTY/absent in .context — "
                        "drifted or unhandled (re-anchor to the correct current id)")
    return (not errs, errs)


def validate_case(case: dict, existing: list[dict], for_public: bool,
                  grandfathered: bool = False) -> tuple[bool, dict]:
    """Run all gates. privacy_scan runs ONLY for_public (PROMOTE). gate_teeth +
    gate_refs are skipped for grandfathered (legacy) cases (so the pre-existing
    drifted corpus doesn't all fail at once — same containment as gate_teeth). New/
    edited cases MUST pass gate_refs. Returns (ok, report) where report maps gate
    name → (ok, errors). On a clean pass, report["stamp"] carries the content-bound
    validated_by_4gate value for the caller to persist."""
    report = {}
    g1 = gate_schema(case); report["schema"] = g1
    g2 = gate_duplicate(case, existing); report["duplicate"] = g2
    g3 = gate_non_vacuous(case); report["non_vacuous"] = g3
    g4 = gate_teeth(case, grandfathered=grandfathered); report["teeth"] = g4
    # gate_redline runs for ALL cases (incl. grandfathered): the marker is opt-in,
    # so a legacy case without `redline` passes vacuously — but if any case (new or
    # old) IS marked redline, the marker must be valid + enforceable.
    g_rl = gate_redline(case); report["redline"] = g_rl
    gates = [g1, g2, g3, g4, g_rl]
    if not grandfathered:
        g5 = gate_refs(case); report["refs"] = g5
        gates.append(g5)
    if for_public:
        gp = privacy_scan(case); report["privacy"] = gp
        gates.append(gp)
    ok = all(g[0] for g in gates)
    if ok:
        report["stamp"] = compute_case_stamp(case)
    return ok, report


def _load_corpus_cases(root: Path) -> list[dict]:
    """Load every case the runner sees — REUSE eval_runner.load_golden_set so the
    sweep shares ONE loader with the runner (no divergent second view). That loader
    merges public + sibling private, FAILS LOUD on an id present in BOTH files
    (migration error), raises FileNotFoundError if the public set is absent (so a
    typo'd --root can't false-green to "CLEAN"), and validates basic structure
    (version/cases/required fields) — closing the malformed-corpus crash surface.

    The import is lazy + path-shimmed because eval_runner itself lazily imports
    THIS module (compute_case_stamp) — top-level import would risk a cycle; a
    function-local import is the same pattern eval_runner uses in reverse."""
    import sys as _sys
    _here = str(Path(__file__).resolve().parent.parent)  # …/backend
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from scripts.eval_runner import load_golden_set
    gs = load_golden_set(root / "Eval" / "golden_set.yaml")
    return gs.get("cases", []) or []


def validate_corpus(root: Path) -> list[tuple[str, list[str]]]:
    """Sweep gate_refs over EVERY case in the corpus. gate_refs normally fires
    only on the ADD/UPDATE write path (eval_service), so a ref that drifted to
    EMPTY after a doc reorg sits green in the resting corpus forever (the
    load/run hole, run_51d897f6). This sweep closes it for REPORTING.

    Returns [(case_id, [ref-error, ...]), ...] for every case with ≥1 stale ref.

    NOTE (Gate-1 BLOCKER2, verified): _ref_resolves for MEMORY refs only checks
    the id EXISTS ('[DEC15]' in txt), NOT that it points at the intended content.
    So this sweep catches EMPTY/absent refs (STEERING.R* post-reorg) but is
    structurally BLIND to renumber-drift (DEC15→DEC38 content moved but id still
    present). Those re-anchors are manual-only with no automated backstop."""
    rows: list[tuple[str, list[str]]] = []
    for case in _load_corpus_cases(root):
        ok, errs = gate_refs(case, root)
        if not ok:
            rows.append((case.get("id", "<no-id>"), errs))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate golden case(s) — 4-gate or corpus sweep.")
    ap.add_argument("--case-file", help="JSON file with one case (single-case 4-gate mode)")
    ap.add_argument("--for-public", action="store_true", help="run privacy gate (PROMOTE)")
    ap.add_argument("--validate-corpus", action="store_true",
                    help="sweep gate_refs over ALL cases in the corpus (drift report)")
    ap.add_argument("--root", help="SwarmWS root for --validate-corpus (default: ~/.swarm-ai/SwarmWS)")
    ap.add_argument("--exit-nonzero", action="store_true",
                    help="with --validate-corpus: exit 1 if any stale ref found "
                         "(default: report-only, exit 0 — so CI adoption doesn't red "
                         "pre-existing drift on day 1)")
    args = ap.parse_args()

    if args.validate_corpus:
        root = Path(args.root) if args.root else (Path.home() / ".swarm-ai" / "SwarmWS")
        try:
            rows = validate_corpus(root)
        except (FileNotFoundError, AssertionError) as e:
            # wrong --root (no golden set), dup-id collision, or a malformed/
            # under-spec corpus — a drift gate must fail LOUD + ACTIONABLE on a
            # corpus it cannot trust, never silently report "CLEAN" (Gate-2 F2/F4/F5).
            print(f"CORPUS UNVERIFIABLE — {type(e).__name__}: {e}")
            return 2
        if not rows:
            print("CORPUS CLEAN — 0 stale refs")
            return 0
        print(f"STALE REFS — {len(rows)} case(s):")
        for cid, errs in rows:
            print(f"  ✗ {cid}: {'; '.join(errs)}")
        return 1 if args.exit_nonzero else 0

    if not args.case_file:
        ap.error("--case-file is required unless --validate-corpus is given")
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
