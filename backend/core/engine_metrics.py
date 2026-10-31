"""Core Engine metrics collector — unified health & growth data for L3 self-governing.

Aggregates data from:
- MEMORY.md (section sizes, entry ages, freshness)
- DailyActivity/ (coverage, distillation health)
- context_health findings (DDD staleness, git health)
- Hook execution stats (from hook_stats.json)

All reads are filesystem-only. No LLM, no network.
Budget: <500ms for full collect.

Key public symbols:

- ``collect_engine_metrics()`` — Returns full metrics dict for API/dashboard.
- ``collect_memory_effectiveness()`` — MEMORY.md section analysis.
- ``collect_ddd_change_suggestions()`` — Granular code→doc update suggestions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import date, datetime, timedelta

from core.ddd_paths import ddd_path
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5


# ---------------------------------------------------------------------------
# Memory effectiveness — Sprint 6
# ---------------------------------------------------------------------------

def collect_memory_effectiveness(ws_path: Path) -> dict[str, Any]:
    """Analyze MEMORY.md for section health, entry freshness, and staleness.

    Returns:
        Dict with section_sizes, entry_ages, freshness_score, stale_entries.
    """
    memory_path = ws_path / ".context" / "MEMORY.md"
    if not memory_path.exists():
        return {"status": "missing", "sections": {}}

    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return {"status": "unreadable", "sections": {}}

    sections: dict[str, dict[str, Any]] = {}
    current_section = None
    current_entries: list[str] = []
    total_entries = 0
    dated_entries = 0
    stale_entries: list[dict] = []
    recent_entries = 0
    cutoff_14d = (date.today() - timedelta(days=14)).isoformat()
    cutoff_30d = (date.today() - timedelta(days=30)).isoformat()

    # Date pattern at start of entry: "- 2026-03-25:" or "- **2026-03-25**"
    date_pattern = re.compile(r"^-\s+\**(\d{4}-\d{2}-\d{2})\**[:\s]")

    for line in content.splitlines():
        if line.startswith("## ") and not line.startswith("## _"):
            # Save previous section
            if current_section:
                sections[current_section] = _analyze_section(
                    current_entries, cutoff_14d, cutoff_30d
                )
            current_section = line.lstrip("# ").strip()
            current_entries = []
        elif line.startswith("- ") and current_section:
            current_entries.append(line)
            total_entries += 1
            m = date_pattern.match(line)
            if m:
                dated_entries += 1
                entry_date = m.group(1)
                if entry_date >= cutoff_14d:
                    recent_entries += 1
                elif entry_date < cutoff_30d:
                    stale_entries.append({
                        "section": current_section,
                        "date": entry_date,
                        "preview": line[2:80].strip(),
                    })

    # Save last section
    if current_section:
        sections[current_section] = _analyze_section(
            current_entries, cutoff_14d, cutoff_30d
        )

    # Freshness score: 0-100 based on ratio of recent/total dated entries
    if dated_entries > 0:
        freshness_score = round(recent_entries / dated_entries * 100)
    else:
        freshness_score = 0

    return {
        "status": "ok",
        "total_entries": total_entries,
        "dated_entries": dated_entries,
        "recent_entries_14d": recent_entries,
        "stale_entries_30d": len(stale_entries),
        "freshness_score": freshness_score,
        "sections": sections,
        "stale_samples": stale_entries[:5],  # Top 5 for dashboard
        "size_bytes": memory_path.stat().st_size,
        "last_modified": date.fromtimestamp(memory_path.stat().st_mtime).isoformat(),
    }


def _analyze_section(
    entries: list[str],
    cutoff_14d: str,
    cutoff_30d: str,
) -> dict[str, Any]:
    """Analyze a single MEMORY.md section."""
    date_pattern = re.compile(r"^-\s+\**(\d{4}-\d{2}-\d{2})\**[:\s]")
    count = len(entries)
    dates: list[str] = []
    for e in entries:
        m = date_pattern.match(e)
        if m:
            dates.append(m.group(1))

    recent = sum(1 for d in dates if d >= cutoff_14d)
    stale = sum(1 for d in dates if d < cutoff_30d)
    oldest = min(dates) if dates else None
    newest = max(dates) if dates else None

    return {
        "count": count,
        "dated": len(dates),
        "recent_14d": recent,
        "stale_30d": stale,
        "oldest_date": oldest,
        "newest_date": newest,
    }


# ---------------------------------------------------------------------------
# DDD change suggestions — Sprint 7
# ---------------------------------------------------------------------------

def collect_ddd_change_suggestions(ws_path: Path) -> list[dict[str, str]]:
    """Code changes in the last 7 days that may need a DDD doc update.

    For every project under ``Projects/``, reads the repos its ``bindings.yaml`` declares
    (``ddd_bindings.governed_repo_roots``; usable-by-anyone P7: no project and no repo
    layout is special) and applies three layout-free rules per repo:
    - 3+ files added  -> TECH.md, Architecture
    - any file renamed -> TECH.md, Architecture
    - 3+ files removed -> IMPROVEMENT.md, What Worked / What Failed

    Returns list of {project, doc, reason, files, section} suggestions.
    """
    from core.ddd_bindings import governed_repo_roots

    suggestions: list[dict[str, str]] = []
    projects = ws_path / "Projects"
    if not projects.is_dir():
        return suggestions
    for project_dir in sorted(projects.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for repo in governed_repo_roots(project_dir):
            suggestions.extend(_repo_change_suggestions(project_dir.name, repo))
    return suggestions


def _repo_change_suggestions(project: str, repo: Path) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["git", "log", "--name-status", "--since=7 days ago",
             "--pretty=format:", "--diff-filter=ADR"],
            cwd=str(repo), capture_output=True, text=True, timeout=_GIT_TIMEOUT,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []
    changes: dict[str, list[str]] = {"A": [], "D": [], "R": []}
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0][:1] in changes:
            changes[parts[0][0]].append(parts[-1])
    out: list[dict[str, str]] = []
    where = f" in {repo.name}"

    def _dirs(files: list[str]) -> str:
        tops = sorted({f.split("/", 1)[0] if "/" in f else "." for f in files})
        return ", ".join(tops[:3])

    if len(changes["A"]) >= 3:
        out.append({"project": project, "doc": "TECH.md", "section": "Architecture",
                    "reason": f"{len(changes['A'])} files added{where} ({_dirs(changes['A'])})",
                    "files": ", ".join(changes["A"][:5])})
    if changes["R"]:
        out.append({"project": project, "doc": "TECH.md", "section": "Architecture",
                    "reason": f"{len(changes['R'])} files renamed or moved{where}",
                    "files": ", ".join(changes["R"][:5])})
    if len(changes["D"]) >= 3:
        out.append({"project": project, "doc": "IMPROVEMENT.md",
                    "section": "What Worked / What Failed",
                    "reason": f"{len(changes['D'])} files removed{where} (migration?)",
                    "files": ", ".join(changes["D"][:5])})
    return out


# ---------------------------------------------------------------------------
# Hook execution stats
# ---------------------------------------------------------------------------

def collect_hook_stats(ws_path: Path) -> dict[str, Any]:
    """Read hook execution stats from hook_stats.json if available."""
    stats_path = ws_path / "hook_stats.json"
    if not stats_path.exists():
        return {"available": False}

    try:
        data = json.loads(stats_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", data)  # Support both nested and flat format
        total_runs = sum(h.get("total_runs", 0) for h in hooks.values())
        total_errors = sum(h.get("total_errors", 0) for h in hooks.values())
        return {
            "available": True,
            "updated_at": data.get("updated_at"),
            "hook_count": len(hooks),
            "total_runs": total_runs,
            "total_errors": total_errors,
            "error_rate": round(total_errors / max(total_runs, 1) * 100, 1),
            "hooks": hooks,
        }
    except (json.JSONDecodeError, OSError):
        return {"available": False}


# ---------------------------------------------------------------------------
# Unified metrics collector — Sprint 8
# ---------------------------------------------------------------------------

def collect_engine_metrics(ws_path_str: str) -> dict[str, Any]:
    """Collect all Core Engine metrics for the dashboard API.

    Aggregates: memory effectiveness, DDD health,
    hook stats, context health findings.
    """
    ws_path = Path(ws_path_str)
    metrics: dict[str, Any] = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "engine_level": _compute_engine_level(ws_path),
    }

    # 2. Memory effectiveness
    try:
        metrics["memory"] = collect_memory_effectiveness(ws_path)
    except Exception as exc:
        logger.debug("Failed to collect memory effectiveness: %s", exc)
        metrics["memory"] = {"status": "error", "error": str(exc)}

    # 3. DDD change suggestions
    try:
        metrics["ddd_suggestions"] = collect_ddd_change_suggestions(ws_path)
    except Exception as exc:
        logger.debug("Failed to collect DDD suggestions: %s", exc)
        metrics["ddd_suggestions"] = []

    # 4. DDD staleness (quick check)
    try:
        metrics["ddd_health"] = _collect_ddd_health(ws_path)
    except Exception as exc:
        metrics["ddd_health"] = {"error": str(exc)}

    # 5. Context health findings (from last deep check)
    try:
        findings_path = ws_path / "Services" / "swarm-jobs" / "health_findings.json"
        if findings_path.exists():
            metrics["context_health"] = json.loads(
                findings_path.read_text(encoding="utf-8")
            )
        else:
            metrics["context_health"] = {"findings": [], "last_check": None}
    except (json.JSONDecodeError, OSError):
        metrics["context_health"] = {"findings": [], "last_check": None}

    # 6. Hook stats
    metrics["hooks"] = collect_hook_stats(ws_path)

    # 7. Session stats (from DailyActivity)
    try:
        metrics["sessions"] = _collect_session_stats(ws_path)
    except Exception as exc:
        metrics["sessions"] = {"error": str(exc)}

    return metrics


def _compute_engine_level(ws_path: Path) -> dict[str, Any]:
    """Compute current Core Engine growth level and progress."""
    # L0-L2: DONE (hardcoded, verified in prior sessions)
    # L3: Check which features are active
    l3_features = {
        "session_type_detection": False,
        "memory_effectiveness_tracking": False,
        "ddd_auto_update_suggestions": False,
        "growth_metrics_dashboard": False,
    }

    # Check if session-type detection exists (channel sessions get lighter prompts)
    try:
        from core.prompt_builder import PromptBuilder
        # Feature detection via attribute check — works in dev and .pyc bundles
        # PromptBuilder.build_system_prompt must accept is_channel kwarg
        import inspect
        sig = inspect.signature(PromptBuilder.build_system_prompt)
        if "is_channel" in sig.parameters:
            l3_features["session_type_detection"] = True
    except (ImportError, Exception):
        pass

    # Memory effectiveness — this module existing means it's active
    l3_features["memory_effectiveness_tracking"] = True

    # DDD auto-update suggestions — this module existing means it's active
    l3_features["ddd_auto_update_suggestions"] = True

    # Growth metrics dashboard — if this function is being called, it's active
    l3_features["growth_metrics_dashboard"] = True

    done = sum(1 for v in l3_features.values() if v)
    total = len(l3_features)

    return {
        "current": "L3" if done >= 4 else "L2",
        "l3_progress": f"{done}/{total}",
        "l3_features": l3_features,
        "levels": {
            "L0_reactive": "complete",
            "L1_self_maintaining": "complete",
            "L2_self_improving": "complete",
            "L3_self_governing": "in_progress" if done < total else "complete",
            "L4_autonomous": "future",
        },
    }


def _collect_ddd_health(ws_path: Path) -> dict[str, Any]:
    """DDD 5-dimensional health scoring — per-project, per-section.

    Uses ddd_health.compute_section_health() for full scoring when available,
    falls back to staleness-only scan on import error.
    """
    projects_dir = ws_path / "Projects"
    if not projects_dir.is_dir():
        return {"projects": []}

    try:
        from core.ddd_health import compute_section_health

        projects: list[dict] = []
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            # persist=False: this is the /engine-metrics GET dashboard READ path —
            # compute-only, no disk write (a read handler must not write
            # section_health.json on every hit; the scheduled ddd_refresh job owns
            # the snapshot cadence). #5 fix, run_e90535ea.
            result = compute_section_health(project_dir, persist=False)
            # Ensure all 4 standard docs appear with at least an "exists" key
            # (compute_section_health only includes docs with parseable sections)
            raw_docs = result.get("docs", {})
            docs_normalized: dict[str, dict] = {}
            for doc_name in DDD_CANONICAL_DOCS:
                if doc_name in raw_docs:
                    docs_normalized[doc_name] = {**raw_docs[doc_name], "exists": True}
                else:
                    doc_path = ddd_path(project_dir, doc_name)
                    docs_normalized[doc_name] = {"exists": doc_path.exists()}
            projects.append({
                "name": project_dir.name,
                "docs": docs_normalized,
                "computed_at": result.get("computed_at"),
            })
        return {"projects": projects, "scoring": "5-dimensional"}
    except ImportError:
        # Fallback: staleness-only (pre-T3 behavior)
        projects = []
        now = datetime.now()
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            docs: dict[str, dict] = {}
            for doc_name in DDD_CANONICAL_DOCS:
                doc_path = ddd_path(project_dir, doc_name)
                if doc_path.exists():
                    mtime = datetime.fromtimestamp(doc_path.stat().st_mtime)
                    age_days = (now - mtime).days
                    docs[doc_name] = {"exists": True, "age_days": age_days, "stale": age_days > 14}
                else:
                    docs[doc_name] = {"exists": False}
            projects.append({"name": project_dir.name, "docs": docs})
        return {"projects": projects, "scoring": "staleness-only"}


def _collect_session_stats(ws_path: Path) -> dict[str, Any]:
    """Session volume from the DailyActivity sidecars (last 7 days).

    One sidecar row is one session's activity on one day, so a session active on two
    days counts once per day. Channel sessions (Slack bot pings, title carrying
    ``[Channel:``) are counted apart so they do not inflate the interactive count.
    """
    from core.daily_activity_writer import read_jsonl_sidecar

    da_dir = ws_path / "Knowledge" / "DailyActivity"
    if not da_dir.is_dir():
        return {"available": False}

    cutoff = date.today() - timedelta(days=7)
    total_sessions = 0
    interactive_sessions = 0
    channel_sessions = 0
    days_active = 0

    for f in sorted(da_dir.glob("*.jsonl")):
        try:
            file_date = date.fromisoformat(f.stem[:10])
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        rows = {str(r.get("session_id")): r for r in read_jsonl_sidecar(f) if r.get("session_id")}
        if not rows:
            continue
        days_active += 1
        for row in rows.values():
            total_sessions += 1
            if "[Channel:" in str(row.get("title") or ""):
                channel_sessions += 1
            else:
                interactive_sessions += 1

    return {
        "last_7d_sessions": total_sessions,
        "last_7d_interactive": interactive_sessions,
        "last_7d_channel": channel_sessions,
        "last_7d_active_days": days_active,
        "avg_interactive_per_day": round(
            interactive_sessions / max(days_active, 1), 1
        ),
    }
