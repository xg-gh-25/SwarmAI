#!/usr/bin/env python3
"""verify_ddd_complete.py — the P6 VERIFY gate of the DDD Setup Flow.

An **asset-aware** completeness gate for a DDD project. This is the on-demand
terminus of the 6-phase DDD Setup Flow documented in s_project-manager/SKILL.md
(§ DDD Setup Flow). It answers ONE question mechanically: *is this DDD structurally
complete for what it actually governs?*

WHY ASSET-AWARE (the load-bearing design constraint):
    A DDD's ⑤ delivery + ⑥ refresher are ASSET-DERIVED (DDD-agent-brain paradigm,
    spec §3.6). A data-agent brain (governs a data-source + skill-set) and a
    0-asset pure-knowledge brain have NO code-repo, so a `code-intel.json` is
    MEANINGLESS for them. This gate therefore requires code-intel ONLY when the
    DDD governs a `kind: code-repo` asset — and even then a MISSING projection is
    PENDING (the repo isn't bound/pulled yet), never FAIL. A knowledge-primary
    brain is NEVER failed for missing code-intel. (This is the whole point: an
    earlier hand-built DDD shipped with an empty ⑤/⑥ and "looked done"; the gate
    makes the real completeness explicit without punishing brains that correctly
    have no code asset.)

TWO SCHEMAS IN bindings.yaml (verified 2026-07-19, run_df79b8ce):
    - `governed_assets:` (kind: skill-set / data-source / code-repo / …) — the
      asset inventory. Parsed by ZERO other Python code; THIS gate is its first
      reader, via a tolerant direct `yaml.safe_load` (NOT core.ddd_bindings.
      load_bindings, which RAISES ValueError on a bindings.yaml that has
      governed_assets but no `bindings:` key — the CMHK/IVTHub shape).
    - `bindings:` (kind: internal / external) — repo clone targets, read by
      core.ddd_bindings. This gate does NOT need it for the code-intel decision
      (Gate-1 refinement: the governed_assets kind alone drives the requirement;
      no fragile governed_asset→binding join).

RELATIONSHIP TO context_health_hook._check_ddd_completeness:
    That is a PASSIVE background scan (runs ~once/day in the deep health check)
    that only flags half-created projects (1-3 of 4 docs). THIS is an ON-DEMAND
    SUPERSET gate invoked at P6 of the setup flow (six-section structure +
    asset-aware ⑤/⑥). Different cadence, different scope — they do not compete;
    the hook keeps surfacing drift in briefings, this gate is the flow's exit门禁.

FAIL-OPEN: every YAML/parse/IO error degrades gracefully (a broken bindings.yaml
    classifies as no-repo, an unreadable doc is reported, nothing crashes the gate)
    — a completeness gate must never itself become a source of failure.

Usage:
    python verify_ddd_complete.py --project IVTHub
    python verify_ddd_complete.py --project-dir /abs/path/to/Projects/IVTHub
    python verify_ddd_complete.py --project IVTHub --json   # machine-readable report

Exit code: 0 if no check FAILs (PASS/PENDING/N/A all ok); 1 if any check FAILs;
    2 on a usage error (project not found).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The 4 canonical ② KNOWLEDGE docs. Inlined (not imported from
# core.project_registry) so this gate is PORTABLE — it must run inside a
# distributed DDD package on a foreign host (Kiro / Claude Code) with no SwarmAI
# backend on the path. SSOT for cross-check: core/project_registry.py:31.
CANONICAL_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")

# Placeholder markers left by the s_project-manager CREATE scaffold. A doc whose
# body is (almost) only these = not yet written = FAIL.
_PLACEHOLDER_MARKERS = (
    "_What is this",
    "_e.g.",
    "_Priority",
    "_How do you",
    "_What are you",
    "_System overview",
    "_Absolute path",
    "_Naming, file",
    "Patterns that succeeded. Will grow",
    "Patterns that failed",
    "Recurring problems to watch",
    "_What are you working on",
    "_Active work item",
    "Nothing currently blocking",
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_PENDING = "PENDING"  # governed a code-repo but projection not built yet — not a failure
STATUS_NA = "N/A"           # not applicable to this brain's asset shape


# ── tolerant bindings.yaml reader (does NOT use core.ddd_bindings) ───────────

def _read_bindings_raw(project_dir: Path) -> dict[str, Any]:
    """yaml.safe_load bindings.yaml into a dict. Fail-open: {} on any error.

    Deliberately NOT core.ddd_bindings.load_bindings — that validates a
    `bindings: list[Binding]` model and RAISES ValueError on the governed_assets-
    only shape (CMHK/IVTHub). A completeness gate must read every DDD shape.
    """
    p = project_dir / "bindings.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        # Broken YAML → treat as no-repo (fail-open); the structural checks still run.
        return {}


def _governed_assets(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract governed_assets as a list of dicts. Tolerant of missing/malformed."""
    ga = raw.get("governed_assets")
    if not isinstance(ga, list):
        return []
    return [a for a in ga if isinstance(a, dict)]


def _has_code_repo_asset(assets: list[dict[str, Any]]) -> bool:
    return any(a.get("kind") == "code-repo" for a in assets)


def _find_code_intel(project_dir: Path) -> Path | None:
    """Locate code-intel.json anywhere under the project (root or nested .artifacts/).

    Location varies (SwarmAI: project root; ai_ready_repo: nested .ai-ready/) — glob
    recursively (Gate-1 P4). Bounded: a project dir is small; first hit wins.
    """
    root = project_dir / "code-intel.json"
    if root.exists():
        return root
    try:
        for hit in project_dir.glob("**/code-intel.json"):
            return hit
    except Exception:
        pass
    return None


# ── individual checks — each returns (name, status, detail) ──────────────────

def _check_identity(d: Path) -> tuple[str, str, str]:
    """① IDENTITY & MANIFEST: .project.json + aim.json + AGENTS.md present + aim.json valid JSON."""
    required = [".project.json", "aim.json", "AGENTS.md"]
    missing = [f for f in required if not (d / f).exists()]
    if missing:
        return ("① Identity/Manifest", STATUS_FAIL, f"missing manifest(s): {', '.join(missing)}")
    # aim.json must be valid JSON
    try:
        json.loads((d / "aim.json").read_text(encoding="utf-8"))
    except Exception as e:
        return ("① Identity/Manifest", STATUS_FAIL, f"aim.json invalid JSON: {type(e).__name__}")
    return ("① Identity/Manifest", STATUS_PASS, "manifests present, aim.json valid")


def _doc_is_placeholder(text: str) -> bool:
    """A doc is a placeholder if, after stripping headers/markers, little real prose remains.

    Measures real CONTENT VOLUME (chars), not line count — a single substantive
    paragraph on one line is NOT a stub (Gate-2 C1: `< 2 lines` false-FAILed a
    concise doc). Markers are matched at LINE START, not as a substring, so prose
    that merely mentions a marker phrase (`see _e.g. below`) is not miscounted
    (Gate-2 C2: substring match false-positived real prose).
    """
    lines = [ln.strip() for ln in text.splitlines()]
    body = [ln for ln in lines if ln and not ln.startswith("#") and not ln.startswith(">")]
    if not body:
        return True

    def _is_marker(ln: str) -> bool:
        # Scaffold placeholders are emitted as whole italic/bullet lines — anchor to
        # the line start (after stripping list/emphasis punctuation), never substring.
        stripped = ln.lstrip("*-_ ").rstrip("_")
        return any(ln.startswith(m) or stripped.startswith(m.strip("_")) for m in _PLACEHOLDER_MARKERS)

    real = [ln for ln in body if not _is_marker(ln)]
    # Placeholder if the real (non-marker) prose is thin — measured by total chars,
    # so ONE substantive paragraph passes but an empty/near-empty body does not.
    real_chars = sum(len(ln) for ln in real)
    return real_chars < 40


def _check_knowledge(d: Path) -> tuple[str, str, str]:
    """② KNOWLEDGE: all 4 canonical docs present AND non-placeholder + Knowledge/ dir."""
    missing = [doc for doc in CANONICAL_DOCS if not (d / doc).exists()]
    if missing:
        return ("② Knowledge", STATUS_FAIL, f"missing doc(s): {', '.join(missing)}")
    placeholders = []
    for doc in CANONICAL_DOCS:
        try:
            if _doc_is_placeholder((d / doc).read_text(encoding="utf-8")):
                placeholders.append(doc)
        except Exception as e:
            placeholders.append(f"{doc}(unreadable: {type(e).__name__})")
    if placeholders:
        return ("② Knowledge", STATUS_FAIL, f"placeholder/empty doc(s): {', '.join(placeholders)}")
    return ("② Knowledge", STATUS_PASS, "4 docs present + substantive")


def _check_gates(d: Path) -> tuple[str, str, str]:
    """③ GATES: gates/ dir exists (content accretes as judgment matures — empty is OK)."""
    if (d / "gates").is_dir():
        return ("③ Gates", STATUS_PASS, "gates/ present (content accretes)")
    return ("③ Gates", STATUS_FAIL, "gates/ dir missing")


def _check_capabilities(d: Path, aim: dict[str, Any] | None) -> tuple[str, str, str]:
    """④ CAPABILITIES: skills/ exists AND every aim.json domain_skill has a dir with SKILL.md."""
    skdir = d / "skills"
    if not skdir.is_dir():
        return ("④ Capabilities", STATUS_FAIL, "skills/ dir missing")
    # Defensive extraction — aim.json is hand-editable, so `plugins` may be a
    # non-dict and `domain_skills` a non-list (Gate-2 D1: a list/str `plugins`
    # crashed .get() → uncaught, broke fail-open; D2: a str `domain_skills`
    # iterated CHARACTERS → nonsense FAIL). Coerce every layer to its expected type.
    declared: list = []
    if isinstance(aim, dict):
        plugins = aim.get("plugins")
        if isinstance(plugins, dict):
            ds = plugins.get("domain_skills")
            if isinstance(ds, list):
                declared = ds
    missing = []
    for s in declared:
        if not isinstance(s, str):
            continue  # skip malformed entries rather than crash
        sd = skdir / s
        if not sd.is_dir() or not (sd / "SKILL.md").exists():
            missing.append(s)
    if missing:
        return ("④ Capabilities", STATUS_FAIL,
                f"aim.json domain_skills not on disk (or no SKILL.md): {', '.join(missing)}")
    if declared:
        return ("④ Capabilities", STATUS_PASS, f"{len(declared)} domain skill(s) present + match aim.json")
    return ("④ Capabilities", STATUS_PASS, "skills/ present (no domain skills declared)")


def _check_delivery(d: Path, assets: list[dict[str, Any]]) -> tuple[str, str, str]:
    """⑤ DELIVERY CONTRACT: if the brain governs assets, bindings.yaml must exist.

    A 0-asset pure-knowledge brain has no ⑤ — that is COMPLETE, not missing (N/A).
    """
    has_bindings = (d / "bindings.yaml").exists()
    if not has_bindings and not assets:
        return ("⑤ Delivery Contract", STATUS_NA, "0-asset pure-knowledge brain — no ⑤ (complete)")
    if not has_bindings and assets:
        # assets discovered some other way but no file — shouldn't happen, flag it
        return ("⑤ Delivery Contract", STATUS_FAIL, "governed assets present but no bindings.yaml")
    if has_bindings and not assets:
        # bindings.yaml exists but declares no governed_assets — could be a bindings: repo project
        return ("⑤ Delivery Contract", STATUS_PASS, "bindings.yaml present (repo/empty governed_assets)")
    # Format each asset as kind:name so two distinct same-kind assets (e.g. two
    # independent code-repos) are distinguishable — a bare kind list rendered
    # them as a phantom "code-repo, code-repo" duplicate. Fall back to bare kind
    # when an asset declares no name.
    def _label(a: dict[str, Any]) -> str:
        kind = a.get("kind", "?")
        name = a.get("name")
        return f"{kind}:{name}" if name else kind
    return ("⑤ Delivery Contract", STATUS_PASS,
            f"{len(assets)} governed asset(s): {', '.join(_label(a) for a in assets)}")


def _check_refresher_code_intel(d: Path, assets: list[dict[str, Any]]) -> tuple[str, str, str]:
    """⑥ REFRESHER / code-intel — THE asset-aware check (the XG constraint).

    - No code-repo governed asset → N/A (data-agent / pure-knowledge — NEVER FAIL).
    - Has a code-repo asset + code-intel.json found → PASS.
    - Has a code-repo asset + NO code-intel.json → PENDING (repo not bound/pulled
      yet) — a signal, NEVER a FAIL.
    """
    if not _has_code_repo_asset(assets):
        return ("⑥ Refresher/code-intel", STATUS_NA,
                "no code-repo asset — code-intel not applicable to this brain")
    ci = _find_code_intel(d)
    if ci is not None:
        try:
            rel = ci.relative_to(d)
        except ValueError:
            rel = ci
        return ("⑥ Refresher/code-intel", STATUS_PASS, f"code-intel present at {rel}")
    return ("⑥ Refresher/code-intel", STATUS_PENDING,
            "governs a code-repo but code-intel.json not built yet — bind+refresh to generate (not a failure)")


# ── orchestration ────────────────────────────────────────────────────────────

def verify_project(project_dir: str | Path) -> dict[str, Any]:
    """Run all checks on a DDD project dir. Returns a structured report dict.

    report = {
      "project": str, "checks": [{"name","status","detail"}...],
      "overall": "PASS"|"FAIL", "exit_code": 0|1,
      "counts": {"PASS":n,"FAIL":n,"PENDING":n,"N/A":n}
    }
    A FAIL in ANY check → overall FAIL (exit 1). PENDING / N/A never fail the gate.
    """
    d = Path(project_dir)
    raw = _read_bindings_raw(d)
    assets = _governed_assets(raw)
    aim: dict[str, Any] | None = None
    try:
        aim = json.loads((d / "aim.json").read_text(encoding="utf-8"))
    except Exception:
        aim = None

    checks = [
        _check_identity(d),
        _check_knowledge(d),
        _check_gates(d),
        _check_capabilities(d, aim),
        _check_delivery(d, assets),
        _check_refresher_code_intel(d, assets),
    ]
    check_dicts = [{"name": n, "status": s, "detail": t} for (n, s, t) in checks]
    counts = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_PENDING: 0, STATUS_NA: 0}
    for c in check_dicts:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    overall = STATUS_FAIL if counts[STATUS_FAIL] else STATUS_PASS
    return {
        "project": d.name,
        "checks": check_dicts,
        "overall": overall,
        "exit_code": 1 if overall == STATUS_FAIL else 0,
        "counts": counts,
    }


_ICON = {STATUS_PASS: "✓", STATUS_FAIL: "✗", STATUS_PENDING: "…", STATUS_NA: "–"}


def _render(report: dict[str, Any]) -> str:
    lines = [f"DDD completeness — {report['project']}: {report['overall']}"]
    for c in report["checks"]:
        lines.append(f"  {_ICON.get(c['status'], '?')} [{c['status']:<7}] {c['name']}: {c['detail']}")
    cn = report["counts"]
    lines.append(
        f"  → {cn.get('PASS',0)} pass, {cn.get('FAIL',0)} fail, "
        f"{cn.get('PENDING',0)} pending, {cn.get('N/A',0)} n/a"
    )
    return "\n".join(lines)


def _resolve_project_dir(args: argparse.Namespace) -> Path:
    if args.project_dir:
        return Path(args.project_dir).expanduser().resolve()
    # default workspace Projects/ root
    base = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects"
    return (base / args.project).resolve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Asset-aware DDD completeness gate (P6 VERIFY).")
    ap.add_argument("--project", help="project name under SwarmWS/Projects/")
    ap.add_argument("--project-dir", help="absolute path to a DDD project dir (overrides --project)")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    if not args.project and not args.project_dir:
        ap.error("one of --project or --project-dir is required")

    d = _resolve_project_dir(args)
    if not d.is_dir():
        print(f"error: project dir not found: {d}", file=sys.stderr)
        return 2

    report = verify_project(d)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
