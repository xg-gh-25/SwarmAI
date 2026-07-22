"""
DDD Weekly Report — generates a summary of all DDD cultivation activity.

Scans ALL projects for:
- ddd-changelog.jsonl entries (last 7 days)
- Pending escalation proposals
- DDD document stats (line count, last modified)

Outputs: Knowledge/Reports/YYYY-MM-DD-ddd-weekly.md

No LLM calls — pure data aggregation. Runs weekly Monday 04:00 UTC.
Also callable on-demand via job-manager.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import SWARMWS, PROJECTS_DIR

logger = logging.getLogger("swarm.jobs.ddd_weekly_report")

# DDD documents we track. Run 0 (run_393e3dc1): single source of truth.
# Guarded import — job handlers may run in a subprocess where core isn't on
# the path; the literal fallback keeps the value identical if so.
try:
    from core.project_registry import DDD_CANONICAL_DOCS as DDD_DOCS
except ImportError:  # pragma: no cover - subprocess without core on path
    DDD_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")  # ddd-canonical-fallback

# Guarded import — mirrors DDD_DOCS above. The fallback reproduces the resolver's
# new-then-old READ semantics (2-understanding/<doc> if present, else root).
try:
    from core.ddd_paths import ddd_path
except ImportError:  # pragma: no cover - subprocess without core on path
    def ddd_path(project_dir, key):  # ddd-canonical-fallback
        root = Path(project_dir)
        new_path = root / "2-understanding" / key
        old_path = root / key
        if not new_path.exists() and old_path.exists():
            return old_path
        return new_path
REPORT_WINDOW_DAYS = 7


def _md_cell(value: object) -> str:
    """Sanitize a changelog-sourced string for safe inline markdown rendering.

    Values normally come from SAFE_APPEND_SECTIONS/ROUTING_TABLE (trusted), but
    _read_changelog re-parses the on-disk JSONL with no whitelist re-validation —
    a corrupted/hand-edited ddd-changelog.jsonl could carry a section name with a
    pipe (breaks tables), backtick, or newline. Neutralize those. (Mirrors the
    _safe() sanitizer ddd_cultivation.py already applies to the same fields.)
    """
    return (
        str(value)
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("|", "\\|")
        .replace("`", "'")
    )


def run_ddd_weekly_report(config: dict | None = None) -> dict:
    """Generate the weekly DDD cultivation report.

    Scans all projects dynamically. Works with 0, 1, or N projects.
    No LLM calls — pure file reading + markdown generation.

    Returns:
        {"status": "success"|"skipped", "summary": str, "output_path": str|None}
    """
    config = config or {}
    window_days = config.get("window_days", REPORT_WINDOW_DAYS)

    if not PROJECTS_DIR.is_dir():
        return {"status": "skipped", "summary": "No Projects/ directory", "output_path": None}

    # Discover all projects (directories with at least one DDD doc)
    projects = _discover_projects()
    if not projects:
        return {"status": "skipped", "summary": "No projects with DDD docs found", "output_path": None}

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Gather data per project
    all_applied = []
    all_escalations = []
    project_health = {}

    for project_name, project_dir in projects:
        # Read changelog
        applied = _read_changelog(project_dir, cutoff)
        all_applied.extend([(project_name, e) for e in applied])

        # Read pending escalations
        escalations = _read_escalations(project_dir)
        all_escalations.extend([(project_name, e) for e in escalations])

        # DDD health stats
        health = _compute_doc_health(project_dir)
        project_health[project_name] = health

    # Generate report
    report = _generate_report(
        all_applied, all_escalations, project_health, window_days
    )

    # Write output
    output_dir = SWARMWS / "Knowledge" / "Reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = output_dir / f"{date_str}-ddd-weekly.md"
    output_path.write_text(report, encoding="utf-8")

    summary = (
        f"{len(all_applied)} applied, {len(all_escalations)} escalations, "
        f"{len(projects)} projects scanned"
    )
    logger.info("DDD weekly report: %s → %s", summary, output_path)

    return {
        "status": "success",
        "summary": summary,
        "output_path": str(output_path),
    }


def _discover_projects() -> list[tuple[str, Path]]:
    """Find all project directories that have DDD docs."""
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        # A project has DDD docs if any of the 4 files exist
        if any((d / doc).exists() for doc in DDD_DOCS):
            projects.append((d.name, d))
    return projects


def _read_changelog(project_dir: Path, cutoff: datetime) -> list[dict]:
    """Read changelog entries newer than cutoff."""
    changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
    if not changelog_path.exists():
        return []

    entries = []
    for line in changelog_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry.get("timestamp", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                entries.append(entry)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return entries


def _read_escalations(project_dir: Path) -> list[dict]:
    """Read pending escalation proposals."""
    proposals_dir = project_dir / ".artifacts" / "proposals"
    if not proposals_dir.exists():
        return []

    escalations = []
    for f in proposals_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("status") in ("pending", "escalated"):
                escalations.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return escalations


def _compute_doc_health(project_dir: Path) -> dict:
    """Compute basic health stats for DDD docs in a project."""
    stats = {}
    for doc_name in DDD_DOCS:
        doc_path = ddd_path(project_dir, doc_name)
        if doc_path.exists():
            content = doc_path.read_text(encoding="utf-8")
            lines = len(content.split("\n"))
            sections = content.count("\n## ")
            mtime = datetime.fromtimestamp(
                doc_path.stat().st_mtime, tz=timezone.utc
            )
            days_since = (datetime.now(timezone.utc) - mtime).days
            stats[doc_name] = {
                "lines": lines,
                "sections": sections,
                "days_since_modified": days_since,
                "stale": days_since > 30,
            }
        else:
            stats[doc_name] = {"exists": False}
    return stats


def _generate_report(
    applied: list[tuple[str, dict]],
    escalations: list[tuple[str, dict]],
    health: dict[str, dict],
    window_days: int,
) -> str:
    """Generate an MBR-style weekly DDD report with narrative + judgment.

    Structure inspired by AWS MBR format:
    - Executive Summary (what happened, so what)
    - Highlights & Lowlights (narrative, not just data)
    - Decisions Needed (escalations with context)
    - Health Dashboard (metrics)
    - Next Week (forward-looking)
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")

    total_applied = len(applied)
    total_escalated = len(escalations)
    total_projects = len(health)

    # Compute derived insights
    by_project: dict[str, list] = {}
    for proj, entry in applied:
        by_project.setdefault(proj, []).append(entry)

    by_doc: dict[str, int] = {}
    for _, entry in applied:
        doc = entry.get("target_doc", "?")
        by_doc[doc] = by_doc.get(doc, 0) + 1

    stale_projects = [
        p for p, docs in health.items()
        if any(d.get("stale") for d in docs.values() if isinstance(d, dict))
    ]
    # Auto-created sections (DDD drift self-healed): a whitelisted section was
    # absent from the doc so cultivation created it. Safe, but signals the doc
    # template / ROUTING_TABLE drifted — surface it so it can be reconciled
    # instead of auto-healing silently forever. Dedup by (project, doc, section).
    created_sections = sorted({
        (proj, e.get("target_doc", "?"), e.get("target_section", "?"))
        for proj, e in applied if e.get("created_section")
    })
    total_sections = sum(
        d.get("sections", 0) for docs in health.values()
        for d in docs.values() if isinstance(d, dict)
    )

    lines = [
        "---",
        "title: DDD Cultivation Weekly",
        f"date: {date_str}",
        "tags: [ddd, cultivation, weekly-report]",
        f"window: {week_start} to {date_str}",
        "---",
        "",
        f"# DDD Cultivation Weekly — {date_str}",
        "",
    ]

    # ─── Executive Summary ───
    lines.append("## Executive Summary")
    lines.append("")
    if total_applied == 0 and total_escalated == 0:
        lines.append(
            f"Quiet week. {total_projects} projects scanned, all DDD docs stable, "
            f"no cultivation activity. This means no pipeline runs produced actionable "
            f"lessons this period — either work was routine (good) or pipelines weren't used."
        )
    else:
        summary_parts = []
        if total_applied > 0:
            doc_summary = ", ".join(f"{v}× {k.replace('.md','')}" for k, v in sorted(by_doc.items()))
            summary_parts.append(
                f"**{total_applied} lessons auto-cultivated** into DDD docs ({doc_summary}) "
                f"across {len(by_project)} project(s)"
            )
        if total_escalated > 0:
            summary_parts.append(
                f"**{total_escalated} change(s) escalated** — these touch strategic docs "
                f"or contradict existing content and need your call"
            )
        if stale_projects:
            summary_parts.append(
                f"**{len(stale_projects)} project(s) have stale DDD docs** "
                f"({', '.join(stale_projects)}) — knowledge may be outdated"
            )
        lines.append(". ".join(summary_parts) + ".")
        lines.append("")
        lines.append(
            f"Net effect: DDD knowledge base grew by {total_applied} entries this week. "
            f"Every future pipeline run on these projects starts with richer context."
        )
    lines.append("")

    # ─── Highlights & Lowlights ───
    lines.append("## Highlights & Lowlights")
    lines.append("")
    if applied:
        # Highlights = most impactful applied lessons (by confidence)
        sorted_applied = sorted(applied, key=lambda x: x[1].get("confidence", 0), reverse=True)
        top_3 = sorted_applied[:3]
        for i, (proj, entry) in enumerate(top_3, 1):
            doc = _md_cell(entry.get("target_doc", "?").replace(".md", ""))
            section = _md_cell(entry.get("target_section", "?"))
            content = _md_cell(entry.get("content", ""))
            source = _md_cell(entry.get("source_run_id", "?"))
            lines.append(f"**[HL{i}] {_md_cell(proj)}/{doc} — {section}**")
            lines.append(f"> {content[:200]}")
            lines.append(f"> _Learned from: {source}_")
            lines.append("")
    else:
        lines.append("_No highlights — no cultivation activity._")
        lines.append("")

    if stale_projects:
        lines.append(f"**[LL1] Stale DDD docs: {', '.join(stale_projects)}**")
        lines.append(
            f"> These projects have docs unchanged for 30+ days. "
            f"Knowledge may be drifting from reality. "
            f"Consider running a pipeline or updating manually."
        )
        lines.append("")

    if total_escalated == 0 and not stale_projects and not applied:
        lines.append(
            "**[LL1] No cultivation input this week** — "
            "the engine needs pipeline REFLECT output to grow. "
            "If you're coding without the pipeline, DDD doesn't learn."
        )
        lines.append("")

    # ─── Decisions Needed ───
    if escalations:
        lines.append("## Decisions Needed")
        lines.append("")
        lines.append(
            f"{total_escalated} proposed change(s) need your approval. "
            f"These target strategic docs (PRODUCT.md/PROJECT.md) or modify existing content."
        )
        lines.append("")
        for i, (proj, esc) in enumerate(escalations, 1):
            lines.append(f"### Escalation {i}: {proj}/{esc.get('target_doc', '?')}")
            lines.append("")
            lines.append(f"**Target:** {esc.get('target_doc', '?')} / {esc.get('target_section', '?')}")
            lines.append(f"**Proposed change:** {esc.get('content', '')[:200]}")
            lines.append(f"**Source:** {esc.get('source_run_id', '?')}")
            lines.append(f"**Why escalated:** Not a safe additive change — touches strategic/operational docs")
            lines.append("")
            lines.append("→ **Approve** / **Reject** / **Discuss in next session**")
            lines.append("")
    else:
        lines.append("## Decisions Needed")
        lines.append("")
        lines.append("_None — all changes this week were safe additive lessons. No action required._")
        lines.append("")

    # ─── DDD Health Dashboard ───
    lines.append("## DDD Health Dashboard")
    lines.append("")
    lines.append("| Project | PRODUCT | TECH | IMPROVEMENT | PROJECT | Status |")
    lines.append("|---------|---------|------|-------------|---------|--------|")
    for proj_name, docs in sorted(health.items()):
        cells = []
        stale_count = 0
        for doc_name in DDD_DOCS:
            info = docs.get(doc_name, {})
            if not info.get("exists", True):
                cells.append("—")
            elif info.get("stale"):
                cells.append(f"⚠️ {info['lines']}L/{info['days_since_modified']}d")
                stale_count += 1
            else:
                cells.append(f"✅ {info['lines']}L")
        status = "🟢 Healthy" if stale_count == 0 else f"🟡 {stale_count} stale"
        lines.append(f"| **{proj_name}** | {' | '.join(cells)} | {status} |")
    lines.append("")

    # ─── DDD Drift: auto-created sections ───
    if created_sections:
        n = len(created_sections)
        lines.append(
            f"**⚠️ {n} auto-created section{'s' if n != 1 else ''} (template drift):** "
            f"a whitelisted section was missing from the doc, so cultivation created "
            f"it automatically. The lesson was NOT lost — but the doc template drifted "
            f"from ROUTING_TABLE. Reconcile to stop recurring auto-creates:"
        )
        for proj, doc, section in created_sections:
            lines.append(
                f"- `{_md_cell(proj)}` — {_md_cell(doc)} § **{_md_cell(section)}**"
            )
        lines.append("")

    # ─── What Changed (detailed log) ───
    if applied:
        lines.append("## Change Log (auto-applied)")
        lines.append("")
        lines.append("| # | Project | Doc | Section | Lesson (truncated) | Source |")
        lines.append("|---|---------|-----|---------|-------------------|--------|")
        for i, (proj, entry) in enumerate(
            sorted(applied, key=lambda x: x[1].get("timestamp", ""), reverse=True), 1
        ):
            doc = _md_cell(entry.get("target_doc", "?"))
            section = _md_cell(entry.get("target_section", "?"))
            content = _md_cell(entry.get("content", "")[:60])
            source = _md_cell(entry.get("source_run_id", "?"))
            lines.append(f"| {i} | {_md_cell(proj)} | {doc} | {section} | {content}… | {source} |")
        lines.append("")

    # ─── Next Week ───
    lines.append("## Next Week")
    lines.append("")
    if stale_projects:
        lines.append(f"- **Action:** Review stale DDD docs in {', '.join(stale_projects)}")
    if created_sections:
        lines.append(
            f"- **Action:** Reconcile {len(created_sections)} auto-created section(s) — "
            f"add the section to the project's DDD template (or update ROUTING_TABLE) "
            f"so cultivation appends in place instead of re-creating"
        )
    if escalations:
        lines.append(f"- **Action:** Resolve {total_escalated} pending escalation(s)")
    if not applied and not escalations:
        lines.append("- **Suggestion:** Run pipelines to start feeding the cultivation engine")
    lines.append(f"- Next report: {(now + timedelta(days=7)).strftime('%Y-%m-%d')}")
    lines.append("")

    return "\n".join(lines)
