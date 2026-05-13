"""
SwarmAI Monthly Report — MBR-style health & progress report for all Core Engine capabilities.

Aggregates data from 12 subsystems:
Memory, Context, Pipeline, DDD Cultivation, Evolution, Self-Health,
Jobs, Channels, Code Intelligence, Skills, Pollinate, Sessions.

Output: Knowledge/Reports/YYYY-MM-ddd-swarmai-monthly.md

No LLM calls — pure data aggregation + template rendering.
Runs monthly (1st, 05:00 UTC) and on-demand via skill.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import SWARMWS, PROJECTS_DIR, CONTEXT_DIR, DAILY_DIR, JOB_RESULTS_JSONL

logger = logging.getLogger("swarm.jobs.swarmai_monthly_report")

DDD_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")


def run_swarmai_monthly_report(config: dict | None = None) -> dict:
    """Generate the SwarmAI monthly report.

    Args:
        config: Optional overrides. Keys:
            - month: "YYYY-MM" (default: last full month)
            - project: project name for pipeline stats (default: "SwarmAI")

    Returns:
        {"status": "success"|"skipped", "summary": str, "output_path": str|None}
    """
    config = config or {}
    now = datetime.now(timezone.utc)

    # Determine report month (default: previous month)
    month_str = config.get("month")
    if month_str:
        year, month = int(month_str[:4]), int(month_str[5:7])
    else:
        # Previous month
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = first_of_this_month - timedelta(days=1)
        year, month = last_month.year, last_month.month

    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    month_label = f"{year}-{month:02d}"

    # Collect all metrics
    metrics = {
        "memory": _collect_memory_metrics(month_start, month_end),
        "context": _collect_context_metrics(),
        "pipeline": _collect_pipeline_metrics(month_start, month_end),
        "cultivation": _collect_cultivation_metrics(month_start, month_end),
        "evolution": _collect_evolution_metrics(),
        "health": _collect_health_metrics(),
        "jobs": _collect_job_metrics(month_start, month_end),
        "code_intel": _collect_code_intel_metrics(),
        "skills": _collect_skill_metrics(),
        "pollinate": _collect_pollinate_metrics(month_start, month_end),
        "sessions": _collect_session_metrics(month_start, month_end),
        "git": _collect_git_metrics(month_start, month_end),
    }

    # Generate report
    report = _generate_monthly_report(metrics, month_label, month_start, month_end)

    # Write output
    output_dir = SWARMWS / "Knowledge" / "Reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{month_label}-swarmai-monthly.md"
    output_path.write_text(report, encoding="utf-8")

    summary = (
        f"Month {month_label}: "
        f"{metrics['pipeline']['runs_completed']} pipeline runs, "
        f"{metrics['cultivation']['applied']} cultivated, "
        f"{metrics['git']['commits']} commits"
    )
    logger.info("SwarmAI monthly report: %s → %s", summary, output_path)

    return {"status": "success", "summary": summary, "output_path": str(output_path)}


# ─── Data Collectors ───


def _collect_memory_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Memory subsystem: MEMORY.md size, DailyActivity count, archives."""
    memory_path = CONTEXT_DIR / "MEMORY.md"
    memory_lines = 0
    memory_entries = 0
    if memory_path.exists():
        content = memory_path.read_text(encoding="utf-8")
        memory_lines = len(content.split("\n"))
        memory_entries = content.count("\n- ")

    # DailyActivity files this month
    daily_count = 0
    if DAILY_DIR.exists():
        month_prefix = month_start.strftime("%Y-%m")
        daily_count = sum(
            1 for f in DAILY_DIR.glob(f"{month_prefix}-*.md")
        )

    # Archives
    archives_dir = SWARMWS / "Knowledge" / "Archives"
    archive_count = sum(1 for _ in archives_dir.glob("*.md")) if archives_dir.exists() else 0

    return {
        "lines": memory_lines,
        "entries": memory_entries,
        "daily_files_this_month": daily_count,
        "archives": archive_count,
    }


def _collect_context_metrics() -> dict:
    """Context subsystem: file sizes, total tokens estimate."""
    total_tokens = 0
    file_sizes = {}
    if CONTEXT_DIR.exists():
        for f in CONTEXT_DIR.glob("*.md"):
            size = len(f.read_text(encoding="utf-8"))
            tokens = size // 4  # rough estimate
            file_sizes[f.name] = tokens
            total_tokens += tokens

    return {"total_tokens": total_tokens, "file_sizes": file_sizes}


def _collect_pipeline_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Pipeline: runs this month, confidence scores, stages."""
    runs_dir = PROJECTS_DIR / "SwarmAI" / ".artifacts" / "runs"
    if not runs_dir.exists():
        return {"runs_completed": 0, "avg_confidence": 0, "total_lessons": 0}

    runs_completed = 0
    confidences = []
    total_lessons = 0
    profiles = {}

    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue
        try:
            data = json.loads(run_json.read_text())
            created = data.get("created_at", "")
            if not created:
                continue
            ts = datetime.fromisoformat(created)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if month_start <= ts < month_end and data.get("status") == "completed":
                runs_completed += 1
                # Extract confidence from stages
                for stage in data.get("stages", []):
                    if isinstance(stage, dict) and stage.get("stage") == "reflect":
                        lessons = stage.get("lessons", [])
                        total_lessons += len(lessons)
                profile = data.get("profile", "unknown")
                profiles[profile] = profiles.get(profile, 0) + 1
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # Try to get confidence from delivery artifacts
    artifacts_dir = PROJECTS_DIR / "SwarmAI" / ".artifacts"
    for f in artifacts_dir.glob("delivery-*.json"):
        try:
            data = json.loads(f.read_text())
            score = data.get("confidence_score")
            if isinstance(score, dict):
                confidences.append(score.get("score", 0))
            elif isinstance(score, (int, float)):
                confidences.append(score)
        except (json.JSONDecodeError, TypeError):
            continue

    avg_conf = sum(confidences) / len(confidences) if confidences else 0

    return {
        "runs_completed": runs_completed,
        "avg_confidence": round(avg_conf, 1),
        "total_lessons": total_lessons,
        "profiles": profiles,
    }


def _collect_cultivation_metrics(month_start: datetime, month_end: datetime) -> dict:
    """DDD Cultivation: applied, escalated, per-project."""
    applied = 0
    escalated = 0
    by_project = {}

    if not PROJECTS_DIR.exists():
        return {"applied": 0, "escalated": 0, "by_project": {}}

    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue

        # Changelog
        changelog = proj_dir / ".artifacts" / "ddd-changelog.jsonl"
        proj_applied = 0
        if changelog.exists():
            for line in changelog.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry.get("timestamp", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if month_start <= ts < month_end:
                        proj_applied += 1
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
            applied += proj_applied

        # Proposals
        proposals_dir = proj_dir / ".artifacts" / "proposals"
        proj_escalated = 0
        if proposals_dir.exists():
            for f in proposals_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    if data.get("status") in ("pending", "escalated"):
                        proj_escalated += 1
                except (json.JSONDecodeError, TypeError):
                    continue
            escalated += proj_escalated

        if proj_applied > 0 or proj_escalated > 0:
            by_project[proj_dir.name] = {"applied": proj_applied, "escalated": proj_escalated}

    return {"applied": applied, "escalated": escalated, "by_project": by_project}


def _collect_evolution_metrics() -> dict:
    """Evolution: corrections, competences, failed evolutions."""
    evo_path = CONTEXT_DIR / "EVOLUTION.md"
    if not evo_path.exists():
        return {"corrections": 0, "competences": 0, "optimizations": 0}

    content = evo_path.read_text(encoding="utf-8")
    corrections = content.count("### C0") + content.count("### C1") + content.count("### C2")
    competences = content.count("### K0") + content.count("### K1")
    optimizations = content.count("### O0")

    return {"corrections": corrections, "competences": competences, "optimizations": optimizations}


def _collect_health_metrics() -> dict:
    """Self-Health: findings from health_findings.json."""
    findings_path = CONTEXT_DIR / "health_findings.json"
    if not findings_path.exists():
        return {"total_findings": 0, "by_severity": {}}

    try:
        data = json.loads(findings_path.read_text())
        findings = data if isinstance(data, list) else data.get("findings", [])
        by_severity = {}
        for f in findings:
            sev = f.get("severity", "unknown") if isinstance(f, dict) else "unknown"
            by_severity[sev] = by_severity.get(sev, 0) + 1
        return {"total_findings": len(findings), "by_severity": by_severity}
    except (json.JSONDecodeError, TypeError):
        return {"total_findings": 0, "by_severity": {}}


def _collect_job_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Jobs: runs, success rate, failures."""
    if not JOB_RESULTS_JSONL.exists():
        return {"total_runs": 0, "success": 0, "failed": 0, "success_rate": 0}

    total = 0
    success = 0
    failed = 0

    for line in JOB_RESULTS_JSONL.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts_str = entry.get("run_at") or entry.get("timestamp") or entry.get("time", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if month_start <= ts < month_end:
                total += 1
                status = entry.get("status", "")
                if status == "success":
                    success += 1
                elif status in ("failed", "error"):
                    failed += 1
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    rate = round(success / total * 100, 1) if total > 0 else 0
    return {"total_runs": total, "success": success, "failed": failed, "success_rate": rate}


def _collect_code_intel_metrics() -> dict:
    """Code Intelligence: index stats if available."""
    try:
        from core.code_intel import load_project_graph
        g = load_project_graph("SwarmAI")
        if g:
            summary = g.get_codebase_summary()
            return {
                "available": True,
                "symbols": summary.get("total_symbols", 0),
                "edges": summary.get("total_edges", 0),
                "dead_code": summary.get("dead_code_count", 0),
                "last_indexed": summary.get("last_indexed", "unknown"),
            }
    except (ImportError, Exception):
        pass
    return {"available": False}


def _collect_skill_metrics() -> dict:
    """Skills: count, tiers."""
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    if not skills_dir.exists():
        return {"total": 0, "always": 0, "lazy": 0}

    total = 0
    always = 0
    lazy = 0
    for s in skills_dir.iterdir():
        if s.is_dir() and s.name.startswith("s_"):
            total += 1
            skill_md = s / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")[:500]
                if "tier: always" in content:
                    always += 1
                else:
                    lazy += 1

    return {"total": total, "always": always, "lazy": lazy}


def _collect_pollinate_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Pollinate: content produced this month."""
    pollinate_dir = SWARMWS / "Knowledge" / "Pollinate"
    if not pollinate_dir.exists():
        return {"pieces": 0, "formats": []}

    pieces = 0
    formats = set()
    month_prefix = month_start.strftime("%Y-%m")

    for d in pollinate_dir.iterdir():
        if d.is_dir() and d.name.startswith(month_prefix):
            pieces += 1
            # Try to detect format from contents
            for f in d.iterdir():
                if f.suffix in (".mp4", ".mov"):
                    formats.add("video")
                elif f.suffix in (".mp3", ".wav"):
                    formats.add("audio")
                elif f.suffix == ".png":
                    formats.add("poster")

    return {"pieces": pieces, "formats": sorted(formats)}


def _collect_session_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Sessions: count from git log (proxy — actual session DB not accessible from job)."""
    # Use DailyActivity as proxy for active session days
    active_days = 0
    if DAILY_DIR.exists():
        month_prefix = month_start.strftime("%Y-%m")
        active_days = sum(1 for f in DAILY_DIR.glob(f"{month_prefix}-*.md"))

    return {"active_days": active_days}


def _collect_git_metrics(month_start: datetime, month_end: datetime) -> dict:
    """Git: commits and files changed this month."""
    since = month_start.strftime("%Y-%m-%d")
    until = month_end.strftime("%Y-%m-%d")

    # Find swarmai repo — try common locations
    swarmai_repo = Path("/Users/gawan/Desktop/SwarmAI-Workspace/swarmai")
    if not (swarmai_repo / ".git").exists():
        swarmai_repo = SWARMWS.parent  # fallback

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"--since={since}", f"--until={until}"],
            capture_output=True, text=True, timeout=10,
            cwd=str(swarmai_repo)
        )
        commits = len([l for l in result.stdout.strip().split("\n") if l])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        commits = 0

    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"--since={since}", f"--until={until}", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(swarmai_repo)
        )
        # Last line: "X files changed, Y insertions(+), Z deletions(-)"
        lines = result.stdout.strip().split("\n")
        files_changed = 0
        if lines:
            last = lines[-1]
            if "file" in last:
                parts = last.split(",")
                for p in parts:
                    if "file" in p:
                        files_changed = int("".join(c for c in p if c.isdigit()) or 0)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        files_changed = 0

    return {"commits": commits, "files_changed": files_changed}


# ─── Report Generator ───


def _generate_monthly_report(
    metrics: dict, month_label: str, month_start: datetime, month_end: datetime
) -> str:
    """Generate MBR-style monthly report."""
    mem = metrics["memory"]
    ctx = metrics["context"]
    pipe = metrics["pipeline"]
    cult = metrics["cultivation"]
    evo = metrics["evolution"]
    health = metrics["health"]
    jobs = metrics["jobs"]
    code = metrics["code_intel"]
    skills = metrics["skills"]
    poll = metrics["pollinate"]
    sess = metrics["sessions"]
    git = metrics["git"]

    lines = [
        "---",
        "title: SwarmAI Monthly Report",
        f"date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"month: {month_label}",
        "tags: [swarmai, monthly, core-engine, health]",
        "---",
        "",
        f"# SwarmAI Monthly — {month_label}",
        "",
    ]

    # ─── Executive Summary ───
    lines.append("## Executive Summary")
    lines.append("")

    # Build narrative
    highlights = []
    if pipe["runs_completed"] > 0:
        highlights.append(f"{pipe['runs_completed']} pipeline runs (avg confidence {pipe['avg_confidence']}/12)")
    if cult["applied"] > 0:
        highlights.append(f"{cult['applied']} lessons auto-cultivated into DDD")
    if git["commits"] > 0:
        highlights.append(f"{git['commits']} commits shipped")

    if highlights:
        lines.append(f"**{month_label}** — " + ", ".join(highlights) + ".")
    else:
        lines.append(f"**{month_label}** — Quiet month. System stable, no major changes.")

    lines.append("")
    lines.append(
        f"Active {sess['active_days']} days. "
        f"Memory: {mem['entries']} entries ({mem['lines']}L). "
        f"Context budget: {ctx['total_tokens']}tok. "
        f"Jobs: {jobs['success_rate']}% success rate ({jobs['total_runs']} runs)."
    )
    lines.append("")

    # ─── P0 Metrics ───
    lines.append("## P0 Metrics")
    lines.append("")
    lines.append("| Subsystem | Key Metric | Value | Status |")
    lines.append("|-----------|-----------|-------|--------|")

    # Memory
    mem_status = "🟢" if mem["entries"] > 20 else "🟡"
    lines.append(f"| **Memory** | Entries in MEMORY.md | {mem['entries']} | {mem_status} |")
    lines.append(f"| | DailyActivity files (this month) | {mem['daily_files_this_month']} | — |")

    # Context
    ctx_status = "🟢" if ctx["total_tokens"] < 80000 else "🟡"
    lines.append(f"| **Context** | System prompt total | {ctx['total_tokens']:,} tok | {ctx_status} |")

    # Pipeline
    pipe_status = "🟢" if pipe["avg_confidence"] >= 8 else ("🟡" if pipe["avg_confidence"] >= 6 else "🔴")
    lines.append(f"| **Pipeline** | Runs completed | {pipe['runs_completed']} | {pipe_status} |")
    lines.append(f"| | Avg confidence | {pipe['avg_confidence']}/12 | |")
    lines.append(f"| | Lessons extracted | {pipe['total_lessons']} | |")

    # DDD Cultivation
    cult_status = "🟢" if cult["applied"] > 0 else "🟡"
    lines.append(f"| **DDD Cultivation** | Lessons auto-applied | {cult['applied']} | {cult_status} |")
    lines.append(f"| | Pending escalations | {cult['escalated']} | |")

    # Evolution
    evo_status = "🟢" if evo["corrections"] > 0 else "🟡"
    lines.append(f"| **Evolution** | Corrections captured | {evo['corrections']} | {evo_status} |")
    lines.append(f"| | Competences earned | {evo['competences']} | |")

    # Jobs
    job_status = "🟢" if jobs["success_rate"] >= 95 else ("🟡" if jobs["success_rate"] >= 80 else "🔴")
    lines.append(f"| **Jobs** | Success rate | {jobs['success_rate']}% | {job_status} |")
    lines.append(f"| | Total runs | {jobs['total_runs']} | |")
    lines.append(f"| | Failures | {jobs['failed']} | |")

    # Skills
    lines.append(f"| **Skills** | Total | {skills['total']} ({skills['always']} always / {skills['lazy']} lazy) | 🟢 |")

    # Code Intel
    if code.get("available"):
        lines.append(f"| **Code Intel** | Symbols indexed | {code['symbols']:,} | 🟢 |")
        lines.append(f"| | Dead code | {code['dead_code']} | |")

    # Pollinate
    if poll["pieces"] > 0:
        lines.append(f"| **Pollinate** | Content produced | {poll['pieces']} | 🟢 |")

    # Health
    health_status = "🟢" if health["total_findings"] < 5 else "🟡"
    lines.append(f"| **Self-Health** | Open findings | {health['total_findings']} | {health_status} |")

    # Git
    lines.append(f"| **Codebase** | Commits | {git['commits']} | — |")

    lines.append("")

    # ─── Highlights & Lowlights ───
    lines.append("## Highlights & Lowlights")
    lines.append("")

    if pipe["runs_completed"] > 0:
        lines.append(f"**[HL] Pipeline active** — {pipe['runs_completed']} runs completed, "
                    f"producing {pipe['total_lessons']} lessons for DDD cultivation.")
        lines.append("")
    if cult["applied"] > 0:
        lines.append(f"**[HL] DDD growing organically** — {cult['applied']} lessons auto-applied "
                    f"across {len(cult['by_project'])} project(s). Knowledge compounds without manual effort.")
        lines.append("")
    if git["commits"] > 20:
        lines.append(f"**[HL] High velocity** — {git['commits']} commits this month.")
        lines.append("")

    if jobs["failed"] > 3:
        lines.append(f"**[LL] Job failures elevated** — {jobs['failed']} failures out of "
                    f"{jobs['total_runs']} runs ({100 - jobs['success_rate']:.0f}% failure rate). "
                    f"Investigate recurring failures.")
        lines.append("")
    if health["total_findings"] > 5:
        lines.append(f"**[LL] Health findings accumulating** — {health['total_findings']} "
                    f"open findings. Review and resolve.")
        lines.append("")
    if cult["applied"] == 0 and pipe["runs_completed"] == 0:
        lines.append("**[LL] No cultivation activity** — no pipeline runs = no DDD growth. "
                    "The flywheel needs pipeline REFLECT to spin.")
        lines.append("")

    # ─── Subsystem Deep Dive ───
    lines.append("## Subsystem Health")
    lines.append("")

    # Memory
    lines.append("### Memory & Knowledge")
    lines.append("")
    lines.append(f"- MEMORY.md: {mem['entries']} entries, {mem['lines']} lines")
    lines.append(f"- DailyActivity: {mem['daily_files_this_month']} files this month")
    lines.append(f"- Archives: {mem['archives']} total")
    lines.append("")

    # Context
    lines.append("### Context Management")
    lines.append("")
    lines.append(f"- Total system prompt: ~{ctx['total_tokens']:,} tokens")
    top_files = sorted(ctx["file_sizes"].items(), key=lambda x: x[1], reverse=True)[:5]
    if top_files:
        lines.append("- Top 5 context files by size:")
        for fname, tokens in top_files:
            lines.append(f"  - {fname}: {tokens:,} tok")
    lines.append("")

    # Pipeline
    lines.append("### Autonomous Pipeline")
    lines.append("")
    lines.append(f"- Runs completed: {pipe['runs_completed']}")
    lines.append(f"- Average confidence: {pipe['avg_confidence']}/12")
    if pipe.get("profiles"):
        profile_str = ", ".join(f"{k}: {v}" for k, v in pipe["profiles"].items())
        lines.append(f"- Profiles used: {profile_str}")
    lines.append("")

    # Evolution
    lines.append("### Self-Evolution Loop")
    lines.append("")
    lines.append(f"- Corrections captured: {evo['corrections']}")
    lines.append(f"- Competences earned: {evo['competences']}")
    lines.append(f"- Optimizations: {evo['optimizations']}")
    lines.append("")

    # DDD
    lines.append("### DDD Cultivation")
    lines.append("")
    lines.append(f"- Lessons auto-applied: {cult['applied']}")
    lines.append(f"- Escalations pending: {cult['escalated']}")
    if cult["by_project"]:
        for proj, stats in cult["by_project"].items():
            lines.append(f"  - {proj}: {stats['applied']} applied, {stats['escalated']} escalated")
    lines.append("")

    # ─── Risks & Next Month ───
    lines.append("## Risks & Next Month")
    lines.append("")

    risks = []
    if jobs["success_rate"] < 90:
        risks.append(f"Job success rate at {jobs['success_rate']}% — below 90% threshold")
    if health["total_findings"] > 10:
        risks.append(f"{health['total_findings']} unresolved health findings accumulating")
    if cult["escalated"] > 3:
        risks.append(f"{cult['escalated']} DDD escalations pending — knowledge decisions deferred")
    if ctx["total_tokens"] > 70000:
        risks.append(f"Context budget at {ctx['total_tokens']:,} tok — approaching 80K effective limit")

    if risks:
        for r in risks:
            lines.append(f"- ⚠️ {r}")
    else:
        lines.append("- No risks identified. System operating within healthy parameters.")
    lines.append("")

    lines.append("---")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. "
                f"Data window: {month_label}-01 to {month_label}-{(month_end - timedelta(days=1)).day:02d}._")
    lines.append("")

    return "\n".join(lines)
