#!/usr/bin/env python3
"""verify_ddd_complete.py — the P6 VERIFY gate of the DDD Setup Flow.

An **asset-aware** completeness gate for a DDD project. This is the on-demand
terminus of the 6-phase DDD Setup Flow documented in s_project-manager/SKILL.md
(§ DDD Setup Flow). It answers ONE question mechanically: *is this DDD structurally
complete for what it actually governs?*

WHY ASSET-AWARE (the load-bearing design constraint):
    A DDD's ⑤ delivery + ⑥ refresher are ASSET-DERIVED (DDD-agent-brain paradigm,
    spec §3.6). A data-agent brain (governs a data-source + skill-set) and a
    0-asset pure-knowledge brain have NO code-repo, so a code graph is
    MEANINGLESS for them. This gate therefore requires code-intel ONLY when the
    DDD governs a `kind: code-repo` asset — and even then a MISSING projection is
    PENDING (the repo isn't bound/pulled yet), never FAIL. A knowledge-primary
    brain is NEVER failed for missing code-intel. (This is the whole point: an
    earlier hand-built DDD shipped with an empty ⑤/⑥ and "looked done"; the gate
    makes the real completeness explicit without punishing brains that correctly
    have no code asset.)

TWO SCHEMAS IN bindings.yaml(verified 2026-07-19):
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
    python verify_ddd_complete.py --project <P>
    python verify_ddd_complete.py --project-dir /abs/path/to/Projects/<P>
    python verify_ddd_complete.py --project <P> --json   # machine-readable report

Exit code: 0 if no check FAILs (PASS/PENDING/N/A all ok); 1 if any check FAILs;
    2 on a usage error (project not found).
"""

from __future__ import annotations

import argparse
import json
import sys
import os
import re
from pathlib import Path
from typing import Any


def _swarmws() -> Path:
    """The SwarmWS root, resolved exactly as backend config.get_swarmws (a skill script
    cannot import backend config): SWARM_WORKSPACE, then SWARMWS, then
    $SWARM_DATA_DIR/SwarmWS, then ~/.swarm-ai/SwarmWS."""
    for var in ("SWARM_WORKSPACE", "SWARMWS"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(os.path.expanduser(value))
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai") / "SwarmWS"

# The 4 canonical ② KNOWLEDGE docs. Single-source guarded import: on SwarmAI the
# constant comes from core.project_registry (SSOT, enforced by
# test_ddd_canonical_docs_single_source); the tagged literal fallback only fires
# when this gate runs PORTABLE — inside a distributed DDD package on a foreign
# host (Kiro / Claude Code) with no SwarmAI backend on the path.
try:  # single source of truth (guarded — gate may run standalone off-host)
    from core.project_registry import DDD_CANONICAL_DOCS as CANONICAL_DOCS
except ImportError:
    CANONICAL_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")  # ddd-canonical-fallback


def _section_dir(project_dir: "Path", section_key: str) -> "Path":
    """Resolve a six-section dir (③ gates / ④ capabilities / ② knowledge),
    strangler-aware: new numbered layout (3-gates/, 4-capabilities/,
    2-understanding/knowledge/) if present, else the old bare dir. Guarded so the
    gate stays PORTABLE (a distributed DDD off-host has no SwarmAI core)."""
    try:  # ddd-six-section-fallback
        from core.ddd_paths import ddd_path
        return ddd_path(project_dir, section_key)
    except ImportError:
        from pathlib import Path as _P
        _new = {
            "gates": "3-gates",
            "capabilities": "4-capabilities",
            "skills": "4-capabilities",
            "knowledge": "2-understanding/knowledge",
        }.get(section_key, section_key)
        _old = {
            "gates": "gates", "capabilities": "skills",
            "skills": "skills", "knowledge": "Knowledge",
        }.get(section_key, section_key)
        cand_new = _P(project_dir) / _new
        return cand_new if cand_new.exists() else _P(project_dir) / _old


def _canonical_doc_path(project_dir: "Path", doc: str) -> "Path":
    """Resolve a canonical ② doc (e.g. "PRODUCT.md") to its physical path.

    The numbered-layout redesign (commit ad7f6623) moved the 4 docs under
    2-understanding/, but _check_knowledge kept probing the project ROOT — so
    EVERY six-section DDD (AIDLC/SwarmAI/SecDLC) false-FAILed ② with "missing
    doc(s)". Route through the same strangler-aware resolver as _section_dir:
    core.ddd_paths.ddd_path knows doc names, and falls back to the bare root for
    an un-migrated DDD. Guarded so the gate stays PORTABLE off-host (no SwarmAI core).
    """
    try:  # ddd-six-section-fallback (mirror of _section_dir)
        from core.ddd_paths import ddd_path
        return ddd_path(project_dir, doc)
    except ImportError:
        from pathlib import Path as _P
        cand_new = _P(project_dir) / "2-understanding" / doc
        return cand_new if cand_new.exists() else _P(project_dir) / doc

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


def _code_repo_assets(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The brain's code repos for ⑥: ``governed_assets`` entries of kind code-repo,
    plus each ``bindings:`` repo (a bound repo IS a code-repo asset)."""
    out = [a for a in _governed_assets(raw) if a.get("kind") == "code-repo"]
    named = {a.get("name") for a in out}
    for b in raw.get("bindings") or []:
        if isinstance(b, dict) and b.get("repo") and b.get("repo") not in named:
            out.append({"kind": "code-repo", "name": b["repo"]})
    return out


def _has_code_repo_asset(assets: list[dict[str, Any]]) -> bool:
    return any(a.get("kind") == "code-repo" for a in assets)


def _find_code_intel(project_dir: Path) -> Path | None:
    """The project's first built code graph db.

    On a SwarmAI host this is ``core.code_intel.code_intel_db_for`` (the one answer
    to where a project's graph lives: a confirmed local checkout's or a bind-managed
    clone's). Run portable, off a SwarmAI host, only a ``code_intel.db`` in the
    project directory can be seen.
    """
    try:
        from core.code_intel import code_intel_db_for
        return code_intel_db_for(project_dir)
    except Exception:  # noqa: BLE001 — portable run, no SwarmAI backend importable
        own = project_dir / "code_intel.db"
        return own if own.exists() else None


# ── individual checks — each returns (name, status, detail) ──────────────────

# create_project writes this placeholder into aim.json; it says nothing about the Brain
# (core.swarm_workspace_manager.DEFAULT_AIM_DESCRIPTION — this script stands alone)
_PLACEHOLDER_DESCRIPTION = re.compile(r"^DDD package for .+ \(six-section canonical structure\)\.$")


def _check_identity(d: Path) -> tuple[str, str, str]:
    """① IDENTITY & MANIFEST: .project.json + aim.json + AGENTS.md present, aim.json valid
    JSON with a real ``description`` — one sentence on what the Brain is, which its Brain
    Hub card and its package show (the create-time placeholder is not one)."""
    required = [".project.json", "aim.json", "AGENTS.md"]
    missing = [f for f in required if not (d / f).exists()]
    if missing:
        return ("① Identity/Manifest", STATUS_FAIL, f"missing manifest(s): {', '.join(missing)}")
    # aim.json must be valid JSON
    try:
        aim = json.loads((d / "aim.json").read_text(encoding="utf-8"))
    except Exception as e:
        return ("① Identity/Manifest", STATUS_FAIL, f"aim.json invalid JSON: {type(e).__name__}")
    desc = str(aim.get("description") or "").strip() if isinstance(aim, dict) else ""
    if not desc or _PLACEHOLDER_DESCRIPTION.match(desc):
        return ("① Identity/Manifest", STATUS_FAIL,
                "aim.json has no description: write one sentence on what this Brain is and "
                "for whom (its Brain Hub card shows it)")
    return ("① Identity/Manifest", STATUS_PASS, "manifests present, aim.json valid, described")


import re as _re

# An entry line with an explicit type: "- [<type>] **<title>**".
_TYPED_ENTRY_RE = _re.compile(r"^- \[(\w+)\] \*\*(.+?)\*\*")


def _portable_knowledge(texts: dict[str, str]) -> list[tuple[str, str, str]]:
    """② structure + content without the SwarmAI backend: structure cannot be
    compared to the skeleton (PENDING); content still requires one typed entry line in
    PRODUCT, TECH and IMPROVEMENT, so an untouched skeleton never passes."""
    empty = [doc for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md")
             if not any(_TYPED_ENTRY_RE.match(ln) for ln in texts[doc].splitlines())]
    content = ((STATUS_FAIL, "no typed entry (- [type] **title**) in: " + ", ".join(empty))
               if empty else (STATUS_PASS, "typed entries present (portable check)"))
    return [("② Knowledge/structure", STATUS_PENDING,
             "the skeleton comparison needs the SwarmAI backend (portable run)"),
            ("② Knowledge/content", *content)]


def _check_knowledge(d: Path) -> list[tuple[str, str, str]]:
    """② KNOWLEDGE — three deterministic checks against the skeleton (core.ddd_skeleton).

    - presence: the four canonical docs exist;
    - structure: each doc has every skeleton section (headings compared by words,
      case-insensitively; an existing brain adds missing ones with
      ``artifact_cli ddd-skeleton-align``);
    - content: PRODUCT, TECH and IMPROVEMENT each hold at least one typed entry
      (``- [type] **title**``; the untouched skeleton has none); every PRODUCT
      section either holds an entry or has a gap entry in IMPROVEMENT § What to Watch
      For whose title starts with the section name. PROJECT may stay empty (its live
      sections are the user's).
    Off a SwarmAI host (no core on the path) only presence is checked; the rest is
    PENDING.
    """
    name = "② Knowledge"
    missing = [doc for doc in CANONICAL_DOCS if not _canonical_doc_path(d, doc).exists()]
    if missing:
        return [(name, STATUS_FAIL, f"missing doc(s): {', '.join(missing)}")]
    texts: dict[str, str] = {}
    for doc in CANONICAL_DOCS:
        try:
            texts[doc] = _canonical_doc_path(d, doc).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return [(name, STATUS_FAIL, f"{doc} unreadable: {type(e).__name__}")]
    out = [(name, STATUS_PASS, "4 docs present")]
    try:
        from core import ddd_skeleton as sk
        from core.ddd_entry_lifecycle import parse_entries  # noqa: F401 — sk.typed_entries needs it
        sk.load_skeleton()
    except (ImportError, OSError):
        return out + _portable_knowledge(texts)

    gaps = {doc: sk.missing_sections(doc, t) for doc, t in texts.items()}
    gaps = {doc: m for doc, m in gaps.items() if m}
    if gaps:
        parts = []
        for doc, missing in gaps.items():
            renames = sk.near_matches(doc, texts[doc])
            parts.append(f"{doc}: " + ", ".join(
                f"{m} (rename heading '{renames[m]}' to '{m}')" if m in renames else m
                for m in missing))
        out.append((f"{name}/structure", STATUS_FAIL,
                    "missing section(s): " + "; ".join(parts)
                    + " — add them with artifact_cli ddd-skeleton-align --apply"))
    else:
        out.append((f"{name}/structure", STATUS_PASS, "every skeleton section present"))

    problems: list[str] = []
    # A doc that is still the skeleton holds no entry, so this also catches it.
    # PROJECT.md may stay empty: its live sections are the user's to fill.
    empty_docs = [doc for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md")
                  if not sk.typed_entries(texts[doc])]
    for doc in empty_docs:
        problems.append(f"{doc} holds no typed entry (- [type] **title**)")
    # The same rule the bootstrap writer reports gaps by (core.ddd_skeleton).
    unexplained = sk.unexplained_product_sections(texts["PRODUCT.md"], texts["IMPROVEMENT.md"])
    if unexplained and "PRODUCT.md" not in empty_docs:
        problems.append("PRODUCT section(s) empty with no gap in IMPROVEMENT § What to Watch "
                        f"For: {', '.join(unexplained)}")
    if problems:
        out.append((f"{name}/content", STATUS_FAIL, "; ".join(problems)))
    else:
        out.append((f"{name}/content", STATUS_PASS,
                    "typed entries present; empty PRODUCT sections are recorded as gaps"))
    out.append(_check_format(d, texts))
    out.append(_check_references(d))
    return out


def _check_references(d: Path) -> tuple[str, str, str]:
    """② Knowledge/references: backticked paths in the docs, AGENTS.md and REFRESHER.md
    that point at a file that does not exist (core.ddd_format.dangling_references).
    PENDING, never FAIL: a dated lesson may cite a file that was later deleted, so each
    one is a lead to fix or keep, not an error."""
    name = "② Knowledge/references"
    try:
        from core import ddd_format as fmt
    except ImportError:
        return (name, STATUS_PENDING, "not checked off a SwarmAI host")
    found = fmt.dangling_references(d)
    if not found:
        return (name, STATUS_PASS, "every path the docs name exists")
    shown = "; ".join(f"{f}:{line} {ref}" for f, line, ref in found[:5])
    more = f" (+{len(found) - 5} more)" if len(found) > 5 else ""
    return (name, STATUS_PENDING,
            f"{len(found)} path(s) point at nothing: {shown}{more} — correct them, or keep a dated one")


def _check_format(d: Path, texts: dict[str, str]) -> tuple[str, str, str]:
    """② Knowledge/format: every doc is in the shape CDF writes (core.ddd_format): only
    skeleton sections, each holding typed entries with their metadata line, of a type
    CDF routes there. Anything else is reference material for 2-understanding/knowledge/
    — ``artifact_cli ddd-format --apply`` moves it there without losing a line."""
    name = "② Knowledge/format"
    try:
        from core import ddd_format as fmt
    except ImportError:
        return (name, STATUS_PENDING, "not checked off a SwarmAI host")
    parts = []
    for doc, text in texts.items():
        found = fmt.check(doc, text)
        if found:
            kinds: dict[str, int] = {}
            for p in found:
                kinds[p.kind] = kinds.get(p.kind, 0) + 1
            parts.append(f"{doc}: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(kinds.items())))
    if not parts:
        return (name, STATUS_PASS, "every doc holds only typed entries in its skeleton sections")
    return (name, STATUS_FAIL, "; ".join(parts)
            + f" — run artifact_cli ddd-format --project {d.name} --apply")


def _check_gates(d: Path) -> tuple[str, str, str]:
    """③ GATES: gates dir exists (content accretes as judgment matures — empty is OK).
    Strangler-aware: 3-gates/ (numbered) or gates/ (un-migrated)."""
    if _section_dir(d, "gates").is_dir():
        return ("③ Gates", STATUS_PASS, "gates present (content accretes)")
    return ("③ Gates", STATUS_FAIL, "gates dir missing")


def _check_capabilities(d: Path, aim: dict[str, Any] | None) -> tuple[str, str, str]:
    """④ CAPABILITIES: capabilities dir exists AND every aim.json domain_skill has a
    dir with SKILL.md. Strangler-aware: 4-capabilities/ (numbered) or skills/ (old)."""
    skdir = _section_dir(d, "capabilities")
    if not skdir.is_dir():
        return ("④ Capabilities", STATUS_FAIL, "capabilities dir missing")
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
    """⑥ REFRESHER / code-intel — THE asset-aware check.

    - No code-repo governed asset → N/A (data-agent / pure-knowledge — NEVER FAIL).
    - Has a code-repo asset (a governed_assets entry or a bindings: repo) + a code
      graph that opens, has indexed files and was built from a repo the brain owns
      → PASS.
    - Otherwise (no graph yet, empty, unopenable, foreign repo) → PENDING with what
      to do — a projection is a signal, NEVER a FAIL of the brain.
    """
    if not _has_code_repo_asset(assets):
        return ("⑥ Refresher/code-intel", STATUS_NA,
                "no code-repo asset — code-intel not applicable to this brain")
    ci = _find_code_intel(d)
    if ci is None:
        return ("⑥ Refresher/code-intel", STATUS_PENDING,
                "governs a code-repo but no code graph built yet — bind the repo, or confirm its local checkout in Brain Hub, to build it (not a failure)")
    try:
        from core.code_intel import repo_root_is_owned
    except ImportError:  # portable run: presence is all that can be seen
        return ("⑥ Refresher/code-intel", STATUS_PASS, f"code graph present at {ci.name}")
    try:
        # Read-only: a check must not create WAL files, migrate the schema or take the
        # write lock a live indexer needs.
        import sqlite3
        conn = sqlite3.connect(f"{ci.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            nodes = conn.execute("SELECT COUNT(DISTINCT file_path) FROM code_nodes").fetchone()[0]
            row = conn.execute("SELECT value FROM graph_meta WHERE key = 'repo_root'").fetchone()
            root = row[0] if row else None
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a projection never FAILs the brain
        return ("⑥ Refresher/code-intel", STATUS_PENDING,
                f"code graph {ci.name} cannot be opened ({type(e).__name__}) — reindex it")
    if not nodes:
        return ("⑥ Refresher/code-intel", STATUS_PENDING,
                f"code graph {ci.name} is empty — re-bind or reindex the repo")
    if not repo_root_is_owned(d, root):
        return ("⑥ Refresher/code-intel", STATUS_PENDING,
                f"code graph {ci.name} was built from a repo this brain does not own "
                f"({root}) — bind the brain's own repo or confirm its checkout in Brain Hub")
    return ("⑥ Refresher/code-intel", STATUS_PASS, f"code graph {ci.name}: {nodes} files indexed")


# The Project Agent validator. Guarded single-source import, the same pattern as
# CANONICAL_DOCS: None only when this gate runs portable, off a SwarmAI host.
try:
    from core.project_agent import validate as _validate_project_agent
except ImportError:
    _validate_project_agent = None


def _check_project_agent(d: Path) -> tuple[str, str, str]:
    """⑤ PROJECT AGENT: project-agent.yaml, drafted at P3b from the Brain's knowledge.

    Missing → FAIL (every Brain has one; draft it with ``artifact_cli
    project-agent-draft``). A skeleton (written by code when the Brain was created: no
    summary, no typical requests) → FAIL until the drafting step fills it in. Present with any problem ``core.project_agent.validate``
    reports for this Brain (either schema; names checked against the Brain on disk) →
    FAIL. Valid → PASS: nothing is confirmed (design 2026-11-01-project-agent-simple).
    Off a SwarmAI host the validator is not importable and only the shape is checked.
    """
    name = "⑤ Project Agent"
    p = d / "project-agent.yaml"
    if not p.is_file():
        return (name, STATUS_FAIL, "no project-agent.yaml — draft it from the DDD "
                                   "(artifact_cli project-agent-draft, s_project-manager P3b)")
    try:
        import yaml  # noqa: PLC0415 — optional dep, same as _read_bindings_raw
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — a completeness gate never crashes
        return (name, STATUS_FAIL, f"project-agent.yaml could not be parsed: {type(exc).__name__}")
    if not isinstance(doc, dict):
        return (name, STATUS_FAIL, "project-agent.yaml is not a mapping")
    # a skeleton (code wrote it when the Brain was created) is not a draft yet: the
    # drafting step has not filled in a summary or typical requests (same test as
    # core.project_agent_standard.is_skeleton, inlined so the gate runs portable)
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    handles = intake.get("handles") if isinstance(intake.get("handles"), list) else []
    if (doc.get("schema") == 2 and not str(ident.get("summary") or "").strip()
            and not any(isinstance(h, str) and h.strip() for h in handles)):
        return (name, STATUS_FAIL, "project-agent.yaml is a skeleton with no summary and no typical "
                                   "requests — draft it (artifact_cli project-agent-draft, "
                                   "s_project-manager P3b)")
    problems = None
    if _validate_project_agent is not None:
        try:
            problems = _validate_project_agent(doc, d)
        except ImportError:      # a partial install: the reference checks are unavailable
            problems = None
    if problems is not None:
        if problems:
            return (name, STATUS_FAIL, "; ".join(problems[:5]))
        modes = doc.get("modes") if doc.get("schema") == 2 else None
        detail = (f"{len(modes)} modes, default {doc.get('default_mode')}" if isinstance(modes, list)
                  else "one mode (schema 1)")
        return (name, STATUS_PASS, f"ready · {detail}")
    if doc.get("schema") == 2 and not (isinstance(doc.get("modes"), list) and doc["modes"]):
        return (name, STATUS_FAIL, "not a supported value: modes []")
    detail = (f"{len(doc['modes'])} modes" if doc.get("schema") == 2 else "one mode (schema 1)")
    return (name, STATUS_PASS, f"ready · {detail}")


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
        *_check_knowledge(d),
        _check_gates(d),
        _check_capabilities(d, aim),
        _check_delivery(d, assets),
        _check_project_agent(d),
        _check_refresher_code_intel(d, _code_repo_assets(raw)),
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


def record_setup_state(project_dir: Path, report: dict[str, Any]) -> None:
    """Write .artifacts/setup.json from a P6 report: ready on PASS; a FAIL is recorded
    only while the Brain is still setting up (never downgrades a ready Brain).

    Uses core.ddd_setup_state on a SwarmAI host; off-host (a distributed DDD) it
    writes the same two-field shape itself. Never raises: recording is a side note
    of the gate, not part of its verdict.
    """
    state = "ready" if report.get("overall") == STATUS_PASS else "setting-up"
    if state == "setting-up":
        # A FAIL only keeps a Brain that is being born in setup. A Brain that is
        # already ready, or that predates setup state, is never sent back to being
        # born by a later check (e.g. the add-knowledge flow re-running P6).
        try:
            current = json.loads((project_dir / ".artifacts" / "setup.json")
                                 .read_text(encoding="utf-8")).get("state")
        except (OSError, ValueError, AttributeError):
            current = None
        if current != "setting-up":
            return
    # The checks still open, FAIL first: Brain Hub shows the first as "next".
    checks = report.get("checks") or []
    open_checks = ([c["name"] for c in checks if c.get("status") == STATUS_FAIL]
                   + [c["name"] for c in checks if c.get("status") == STATUS_PENDING])
    try:
        from core.ddd_setup_state import write_setup_state  # noqa: PLC0415
        write_setup_state(project_dir, state, report.get("counts"), open_checks)
    except ImportError:
        try:
            p = project_dir / ".artifacts" / "setup.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"state": state, "counts": report.get("counts")}, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"warning: could not record setup state: {exc}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"warning: could not record setup state: {exc}", file=sys.stderr)


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
    base = _swarmws() / "Projects"
    return (base / args.project).resolve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Asset-aware DDD completeness gate (P6 VERIFY).")
    ap.add_argument("--project", help="project name under SwarmWS/Projects/")
    ap.add_argument("--project-dir", help="absolute path to a DDD project dir (overrides --project)")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--record", action="store_true",
                    help="record the result in .artifacts/setup.json (ready on PASS); the Brain Hub "
                         "shows a Brain as being born until it is ready")
    args = ap.parse_args(argv)

    if not args.project and not args.project_dir:
        ap.error("one of --project or --project-dir is required")

    d = _resolve_project_dir(args)
    if not d.is_dir():
        print(f"error: project dir not found: {d}", file=sys.stderr)
        return 2

    report = verify_project(d)
    if args.record:
        record_setup_state(d, report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
