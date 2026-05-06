#!/usr/bin/env python3
"""Self-Loops Health Engine — 31 checks across 7 dimensions.

Scans all context files, DailyActivity, Knowledge/, Projects/, Evolution state,
git backup, and infrastructure health. Auto-fixes safe mechanical issues.
Reports Found/Fixed/Pending.

Usage:
    python loops_health_check.py                      # Markdown report to stdout
    python loops_health_check.py --json               # JSON for programmatic use
    python loops_health_check.py --auto-fix           # Fix safe issues
    python loops_health_check.py --output-dir DIR     # Write report to DIR
    python loops_health_check.py --alert-threshold N  # RADAR_TODOS if score < N
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────

WORKSPACE = Path(os.environ.get("SWARM_WORKSPACE", Path.home() / ".swarm-ai" / "SwarmWS"))
CONTEXT_DIR = WORKSPACE / ".context"
KNOWLEDGE_DIR = WORKSPACE / "Knowledge"
PROJECTS_DIR = WORKSPACE / "Projects"
SWARMAI_DIR = Path(os.environ.get(
    "SWARMAI_SOURCE", Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"
))
HISTORY_FILE = WORKSPACE / ".loops-health-history.json"

# Subprocess timeout per check (PE-11)
CHECK_TIMEOUT = 10


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """A single health check result."""
    id: str
    name: str
    dimension: str
    status: str = "pass"  # pass | warn | fail | n/a
    detail: str = ""
    auto_fixable: bool = False
    fixed: bool = False
    fix_action: str = ""


@dataclass
class HealthReport:
    findings: list[Finding] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def scores(self) -> dict[str, int]:
        dims: dict[str, list[Finding]] = {}
        for f in self.findings:
            dims.setdefault(f.dimension, []).append(f)
        result = {}
        for dim, checks in dims.items():
            scorable = [c for c in checks if c.status != "n/a"]
            if not scorable:
                continue  # PE-5: exclude all-n/a dimensions
            passed = sum(1 for c in scorable if c.status == "pass")
            result[dim] = int(passed / len(scorable) * 100)
        return result

    @property
    def overall_score(self) -> int:
        scores = self.scores
        return min(scores.values()) if scores else 0

    @property
    def found_count(self) -> int:
        return sum(1 for f in self.findings if f.status != "pass")

    @property
    def fixed_count(self) -> int:
        return sum(1 for f in self.findings if f.fixed)


# ─── Engine ──────────────────────────────────────────────────────────────────

class SelfLoopsHealthEngine:
    """Main health check engine. Scans, fixes, reports."""

    def __init__(self):
        self.report = HealthReport()

    def run(self, auto_fix: bool = False) -> HealthReport:
        self._check_context()
        self._check_memory()
        self._check_knowledge()
        self._check_evolution()
        self._check_coherence()
        self._check_brain_safety()
        self._check_infrastructure()

        if auto_fix:
            self._auto_remediate()

        self._record_history()
        return self.report

    # ─── Dimension 1: Self-Context ───────────────────────────────────────────

    def _check_context(self):
        # C1: All 11 context files present
        expected = [
            "SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md", "USER.md",
            "STEERING.md", "TOOLS.md", "MEMORY.md", "EVOLUTION.md",
            "KNOWLEDGE.md", "PROJECTS.md",
        ]
        missing = [f for f in expected if not (CONTEXT_DIR / f).exists()]
        self.report.findings.append(Finding(
            id="C1", name="Context files present", dimension="context",
            status="pass" if not missing else "fail",
            detail=f"{11 - len(missing)}/11" + (f", missing: {missing}" if missing else ""),
        ))

        # C2: Agent-owned freshness
        mem_age = self._file_age_days(CONTEXT_DIR / "MEMORY.md")
        evo_age = self._file_age_days(CONTEXT_DIR / "EVOLUTION.md")
        max_age = max(mem_age, evo_age)
        self.report.findings.append(Finding(
            id="C2", name="Agent-owned freshness", dimension="context",
            status="pass" if max_age < 7 else ("warn" if max_age < 14 else "fail"),
            detail=f"MEMORY: {mem_age}d, EVOLUTION: {evo_age}d",
        ))

        # C3: DDD in KNOWLEDGE
        knowledge = self._read_safe(CONTEXT_DIR / "KNOWLEDGE.md")
        has_ddd = "### Active Projects & DDD" in knowledge or "### Active Projects" in knowledge
        project_count = len(re.findall(r"- \*\*\w+\*\*", knowledge[knowledge.find("Projects & DDD"):] if has_ddd else ""))
        self.report.findings.append(Finding(
            id="C3", name="DDD in KNOWLEDGE", dimension="context",
            status="pass" if has_ddd and project_count >= 4 else ("warn" if has_ddd else "fail"),
            detail=f"Section: {'yes' if has_ddd else 'no'}, projects listed: {project_count}",
            auto_fixable=not has_ddd,
        ))

        # C4: Uncommitted context
        uncommitted = self._git_cmd(["git", "status", "--porcelain", ".context/"], WORKSPACE)
        uncommitted_files = [l.strip() for l in uncommitted.split("\n") if l.strip()]
        self.report.findings.append(Finding(
            id="C4", name="Uncommitted context", dimension="context",
            status="pass" if not uncommitted_files else "warn",
            detail=f"{len(uncommitted_files)} files" + (f": {uncommitted_files[:2]}" if uncommitted_files else ""),
            auto_fixable=bool(uncommitted_files),
        ))

    # ─── Dimension 2: Self-Memory ────────────────────────────────────────────

    def _check_memory(self):
        memory = self._read_safe(CONTEXT_DIR / "MEMORY.md")

        # M1: Distillation recency — parse dates from Recent Context + Key Decisions
        rc_section = self._extract_section(memory, "## Recent Context", "## Key Decisions")
        kd_section = self._extract_section(memory, "## Key Decisions", "## Lessons Learned")
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", rc_section + kd_section)
        if dates:
            newest = max(dates)
            gap = (date.today() - date.fromisoformat(newest)).days
        else:
            newest, gap = "none", 999
        self.report.findings.append(Finding(
            id="M1", name="Distillation recency", dimension="memory",
            status="pass" if gap < 5 else ("warn" if gap < 10 else "fail"),
            detail=f"Newest: {newest}, gap: {gap}d",
        ))

        # M2: Undistilled backlog
        da_dir = KNOWLEDGE_DIR / "DailyActivity"
        undistilled = 0
        if da_dir.exists():
            for f in da_dir.glob("20[0-9][0-9]-*.md"):
                head = self._read_safe(f, limit=300)
                if "distilled: true" not in head:
                    undistilled += 1
        self.report.findings.append(Finding(
            id="M2", name="Undistilled backlog", dimension="memory",
            status="pass" if undistilled == 0 else ("warn" if undistilled <= 3 else "fail"),
            detail=f"{undistilled} files",
            auto_fixable=undistilled > 3,
        ))

        # M3: Section caps
        caps = {"Key Decisions": 40, "Lessons Learned": 35, "Recent Context": 20, "COE Registry": 15}
        over_cap = []
        for section_name, cap in caps.items():
            section = self._extract_section(memory, f"## {section_name}", "## ")
            entries = [l for l in section.split("\n") if l.strip().startswith("- ")]
            if len(entries) > cap:
                over_cap.append(f"{section_name}: {len(entries)}/{cap}")
        self.report.findings.append(Finding(
            id="M3", name="Section caps", dimension="memory",
            status="pass" if not over_cap else "warn",
            detail=", ".join(over_cap) if over_cap else "All within limits",
            auto_fixable=bool(over_cap),
        ))

        # M4: Open Threads hygiene (stale >30d)
        ot_section = self._extract_section(memory, "## Open Threads", "## ")
        # Look for dates that are >30d old in OT entries
        ot_dates = re.findall(r"20\d{2}-\d{2}-\d{2}", ot_section)
        stale = sum(1 for d in ot_dates if (date.today() - date.fromisoformat(d)).days > 30)
        # Count Resolved entries (candidates for auto-archive)
        resolved_entries = len(re.findall(r"^- ✅", ot_section, re.MULTILINE))
        self.report.findings.append(Finding(
            id="M4", name="Open Threads hygiene", dimension="memory",
            status="pass" if stale == 0 else ("warn" if stale <= 2 else "fail"),
            detail=f"{stale} stale date references in OT" + (f", {resolved_entries} resolved entries" if resolved_entries else ""),
            auto_fixable=resolved_entries > 0,
        ))

        # M5: Archive health
        if da_dir.exists():
            cutoff = (date.today() - timedelta(days=90)).isoformat()
            old = [f.name for f in da_dir.glob("20[0-9][0-9]-*.md") if f.stem < cutoff]
            self.report.findings.append(Finding(
                id="M5", name="Archive health", dimension="memory",
                status="pass" if not old else "warn",
                detail=f"{len(old)} files >90d not archived",
                auto_fixable=len(old) > 0,
            ))
        else:
            self.report.findings.append(Finding(
                id="M5", name="Archive health", dimension="memory",
                status="n/a", detail="DailyActivity dir not found",
            ))

        # M6: Content dedup — detect duplicate entries in MEMORY.md body
        # Extracts **bold titles** from Lessons Learned and Key Decisions;
        # exact title duplicates indicate distillation promoted the same lesson twice.
        dupes_found: list[str] = []
        for section_name in ("Lessons Learned", "Key Decisions", "Recent Context"):
            section = self._extract_section(memory, f"## {section_name}", "## ")
            titles_seen: dict[str, int] = {}
            for line in section.split("\n"):
                m = re.search(r"\*\*(.+?)\*\*", line)
                if m:
                    title = m.group(1).strip().lower()
                    if title in titles_seen:
                        dupes_found.append(f"{section_name}: '{title[:50]}...'")
                    else:
                        titles_seen[title] = 1
        self.report.findings.append(Finding(
            id="M6", name="Content dedup", dimension="memory",
            status="pass" if not dupes_found else "warn",
            detail=", ".join(dupes_found[:3]) if dupes_found else "No duplicates",
            auto_fixable=bool(dupes_found),
        ))

    # ─── Dimension 3: Self-Knowledge ─────────────────────────────────────────

    def _check_knowledge(self):
        knowledge = self._read_safe(CONTEXT_DIR / "KNOWLEDGE.md")

        # K1: Index completeness (only checks dirs that SHOULD be indexed —
        # excludes DailyActivity, Signals, JobResults, Archives, Pollinate which
        # are intentionally excluded from the Knowledge Index)
        # Index uses format: `Subdir/YYYY-MM-DD-topic.md` in table rows (| date | `file` | topic |)
        # Only match inside Knowledge Index tables to avoid false matches from
        # inline code references in architecture docs.
        ki_section = self._extract_section(knowledge, "## Knowledge Index", "")
        indexed_files = set(re.findall(r"`([^`]+\.md)`", ki_section))
        # Normalize: extract just filename from indexed paths
        indexed_basenames = {Path(f).name for f in indexed_files}
        actual_files: set[str] = set()
        for subdir in ["Designs", "Notes", "Reports", "Library", "Learned", "Meetings", "Handoffs"]:
            d = KNOWLEDGE_DIR / subdir
            if d.exists():
                actual_files.update(f.name for f in d.glob("*.md"))
        missing_from_index = actual_files - indexed_basenames
        self.report.findings.append(Finding(
            id="K1", name="Index completeness", dimension="knowledge",
            status="pass" if len(missing_from_index) <= 2 else ("warn" if len(missing_from_index) <= 5 else "fail"),
            detail=f"{len(missing_from_index)} files missing from index",
            auto_fixable=len(missing_from_index) > 2,
        ))

        # K2: Architecture section currency (git log recency)
        last_change = self._git_cmd(
            ["git", "log", "-1", "--format=%ar", "--diff-filter=M", "--", ".context/KNOWLEDGE.md"],
            WORKSPACE,
        ).strip()
        # "X minutes ago", "X hours ago", "X days ago" — pass if <14 days
        is_recent = any(
            unit in last_change
            for unit in ("second", "minute", "hour", "day")
        ) and "month" not in last_change
        # Also pass if it says "X days ago" where X < 14
        if "days" in last_change:
            try:
                days_num = int(re.search(r"(\d+) days?", last_change).group(1))
                is_recent = days_num < 14
            except (AttributeError, ValueError):
                pass
        self.report.findings.append(Finding(
            id="K2", name="Architecture currency", dimension="knowledge",
            status="pass" if is_recent else "warn",
            detail=f"Last change: {last_change or 'unknown'}",
        ))

        # K3: Capability coverage (RC entries reflected)
        memory = self._read_safe(CONTEXT_DIR / "MEMORY.md")
        rc_terms = re.findall(r"\[RC\d+\] 20\d{2}-\d{2}-\d{2} (.+?)(?:\s*[—|])", memory[:5000])
        arch_section = knowledge[:knowledge.find("## Knowledge Index")] if "## Knowledge Index" in knowledge else knowledge
        gaps = sum(1 for term in rc_terms[:5] if term[:20].lower() not in arch_section.lower())
        self.report.findings.append(Finding(
            id="K3", name="Capability coverage", dimension="knowledge",
            status="pass" if gaps <= 1 else ("warn" if gaps <= 3 else "fail"),
            detail=f"{gaps}/{min(5, len(rc_terms))} recent capabilities not in architecture",
        ))

        # K4: Codebase nav valid
        nav_section = self._extract_section(knowledge, "## Codebase Navigation", "## Knowledge Index")
        entry_points = re.findall(r"`(\w+\.(?:py|tsx?))`", nav_section)
        if entry_points and SWARMAI_DIR.exists():
            found = 0
            for ep in entry_points[:10]:  # Check first 10 only (performance)
                if list(SWARMAI_DIR.rglob(ep)):
                    found += 1
            pct = int(found / min(10, len(entry_points)) * 100)
        else:
            pct = 100
        self.report.findings.append(Finding(
            id="K4", name="Codebase nav valid", dimension="knowledge",
            status="pass" if pct > 90 else ("warn" if pct > 70 else "fail"),
            detail=f"{pct}% of entry points verified",
        ))

    # ─── Dimension 4: Self-Evolution ─────────────────────────────────────────

    def _check_evolution(self):
        evolution = self._read_safe(CONTEXT_DIR / "EVOLUTION.md")

        # E1: Pipeline last run
        state_file = CONTEXT_DIR / ".evolution_last_run"
        if state_file.exists():
            age = self._file_age_days(state_file)
        else:
            # Check skill_health.json as alternative proof
            sh_path = CONTEXT_DIR / "skill_health.json"
            age = self._file_age_days(sh_path) if sh_path.exists() else 999
        self.report.findings.append(Finding(
            id="E1", name="Pipeline last run", dimension="evolution",
            status="pass" if age < 14 else ("warn" if age < 30 else "fail"),
            detail=f"{age}d ago" if age < 999 else "Never ran (no state file or skill_health.json)",
        ))

        # E2: skill_health.json
        sh_path = CONTEXT_DIR / "skill_health.json"
        if sh_path.exists():
            sh_age = self._file_age_days(sh_path)
            self.report.findings.append(Finding(
                id="E2", name="skill_health.json", dimension="evolution",
                status="pass" if sh_age < 14 else "warn",
                detail=f"Exists, {sh_age}d old",
            ))
        else:
            self.report.findings.append(Finding(
                id="E2", name="skill_health.json", dimension="evolution",
                status="fail", detail="Missing",
            ))

        # E3: Correction capture this month
        current_month = date.today().strftime("%Y-%m")
        c_count = len(re.findall(rf"### C\d+ \| {current_month}", evolution))
        self.report.findings.append(Finding(
            id="E3", name="Correction capture", dimension="evolution",
            status="pass" if c_count >= 1 else "warn",
            detail=f"{c_count} this month",
        ))

        # E4: Competence growth
        k_this = len(re.findall(rf"### K\d+ \| {current_month}", evolution))
        k_last = len(re.findall(rf"### K\d+ \| {(date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')}", evolution))
        self.report.findings.append(Finding(
            id="E4", name="Competence growth", dimension="evolution",
            status="pass" if k_this >= 1 else ("warn" if k_last >= 1 else "fail"),
            detail=f"This month: {k_this}, last month: {k_last}",
        ))

    # ─── Dimension 5: Cross-Loop Coherence ───────────────────────────────────

    def _check_coherence(self):
        memory = self._read_safe(CONTEXT_DIR / "MEMORY.md")
        knowledge = self._read_safe(CONTEXT_DIR / "KNOWLEDGE.md")
        evolution = self._read_safe(CONTEXT_DIR / "EVOLUTION.md")

        # X1: Memory→Knowledge sync
        rc_terms = re.findall(r"\[RC\d+\] 20\d{2}-\d{2}-\d{2} (.+?)(?:\s*[—|])", memory[:5000])
        arch = knowledge[:knowledge.find("## Knowledge Index")] if "## Knowledge Index" in knowledge else ""
        unsynced = sum(1 for t in rc_terms[:5] if t[:15].lower() not in arch.lower())
        self.report.findings.append(Finding(
            id="X1", name="Memory→Knowledge sync", dimension="coherence",
            status="pass" if unsynced <= 1 else ("warn" if unsynced <= 3 else "fail"),
            detail=f"{unsynced}/{min(5, len(rc_terms))} capabilities not in KNOWLEDGE",
        ))

        # X2: Memory→Evolution sync
        evo_corrections = len(re.findall(r"### C\d+", evolution))
        mem_lessons = len(re.findall(r"\[LL\d+\]", memory))
        synced = evo_corrections >= mem_lessons * 0.3
        self.report.findings.append(Finding(
            id="X2", name="Memory→Evolution sync", dimension="coherence",
            status="pass" if synced else "warn",
            detail=f"EVOLUTION C-entries: {evo_corrections}, MEMORY LL: {mem_lessons}",
        ))

        # X3: DA→Memory lag
        da_dir = KNOWLEDGE_DIR / "DailyActivity"
        if da_dir.exists():
            da_files = sorted(da_dir.glob("20[0-9][0-9]-*.md"))
            latest_da = da_files[-1].stem if da_files else "none"
            mem_dates = re.findall(r"20\d{2}-\d{2}-\d{2}", memory[:5000])
            latest_mem = max(mem_dates) if mem_dates else "none"
            lag = 0
            if latest_da != "none" and latest_mem != "none":
                try:
                    lag = (date.fromisoformat(latest_da) - date.fromisoformat(latest_mem)).days
                except ValueError:
                    lag = 0
        else:
            lag = 0
            latest_da = latest_mem = "n/a"
        self.report.findings.append(Finding(
            id="X3", name="DA→Memory lag", dimension="coherence",
            status="pass" if lag <= 3 else ("warn" if lag <= 7 else "fail"),
            detail=f"DA: {latest_da}, MEMORY: {latest_mem}, lag: {lag}d",
        ))

    # ─── Dimension 6: Brain Safety ───────────────────────────────────────────

    def _check_brain_safety(self):
        # B1: Remote exists
        remote_url = self._git_cmd(["git", "remote", "get-url", "origin"], WORKSPACE)
        has_remote = bool(remote_url.strip()) and "fatal" not in remote_url
        self.report.findings.append(Finding(
            id="B1", name="Remote exists", dimension="brain_safety",
            status="pass" if has_remote else "fail",
            detail=remote_url.strip()[:60] if has_remote else "No remote configured",
        ))

        if not has_remote:
            # Skip B2-B4 if no remote (n/a, not fail — PE-5)
            for bid, bname in [("B2", "Push recency"), ("B3", "Push health"), ("B4", "Critical files committed")]:
                self.report.findings.append(Finding(
                    id=bid, name=bname, dimension="brain_safety",
                    status="n/a", detail="No remote configured",
                ))
            return

        # B2: Commits ahead
        ahead_str = self._git_cmd(["git", "rev-list", "--count", "origin/HEAD..HEAD"], WORKSPACE)
        try:
            commits_ahead = int(ahead_str.strip())
        except ValueError:
            # Try with branch name
            ahead_str = self._git_cmd(["git", "rev-list", "--count", "origin/master..HEAD"], WORKSPACE)
            try:
                commits_ahead = int(ahead_str.strip())
            except ValueError:
                commits_ahead = -1
        self.report.findings.append(Finding(
            id="B2", name="Push recency", dimension="brain_safety",
            status="pass" if commits_ahead <= 10 else ("warn" if commits_ahead <= 50 else "fail"),
            detail=f"{commits_ahead} commits ahead of remote",
            auto_fixable=commits_ahead > 20,
        ))

        # B3: Push health (dry-run)
        dry_run = self._git_cmd(["git", "push", "--dry-run", "origin", "HEAD"], WORKSPACE, allow_fail=True)
        push_ok = "fatal" not in dry_run and "error" not in dry_run.lower()
        self.report.findings.append(Finding(
            id="B3", name="Push health", dimension="brain_safety",
            status="pass" if push_ok else "fail",
            detail="Dry-run OK" if push_ok else f"Push would fail: {dry_run[:100]}",
        ))

        # B4: Critical files committed
        status = self._git_cmd(["git", "status", "--porcelain", ".context/MEMORY.md", ".context/EVOLUTION.md"], WORKSPACE)
        uncommitted_critical = [l for l in status.split("\n") if l.strip()]
        self.report.findings.append(Finding(
            id="B4", name="Critical files committed", dimension="brain_safety",
            status="pass" if not uncommitted_critical else "warn",
            detail=f"{len(uncommitted_critical)} uncommitted critical files",
            auto_fixable=bool(uncommitted_critical),
        ))

    # ─── Dimension 7: Infrastructure ─────────────────────────────────────────

    def _check_infrastructure(self):
        # I1: Hook execution proof (CHANGELOG)
        changelog = CONTEXT_DIR / "EVOLUTION_CHANGELOG.jsonl"
        if changelog.exists():
            age = self._file_age_days(changelog)
            self.report.findings.append(Finding(
                id="I1", name="Hook execution proof", dimension="infrastructure",
                status="pass" if age < 7 else ("warn" if age < 14 else "fail"),
                detail=f"CHANGELOG last modified: {age}d ago",
            ))
        else:
            self.report.findings.append(Finding(
                id="I1", name="Hook execution proof", dimension="infrastructure",
                status="fail", detail="EVOLUTION_CHANGELOG.jsonl missing",
            ))

        # I2: DailyActivity today
        da_today = KNOWLEDGE_DIR / "DailyActivity" / f"{date.today().isoformat()}.md"
        self.report.findings.append(Finding(
            id="I2", name="DailyActivity today", dimension="infrastructure",
            status="pass" if da_today.exists() else "warn",
            detail="Exists" if da_today.exists() else "Not yet created (created on session close)",
        ))

        # I3: Token budget
        total_tokens = 0
        system_owned = 0  # P0-P2: SWARMAI, IDENTITY, SOUL (not reducible)
        agent_md_tokens = 0  # P3: AGENT.md (system-owned, large)
        system_names = {"SWARMAI.md", "IDENTITY.md", "SOUL.md"}
        for f in CONTEXT_DIR.glob("*.md"):
            if f.name.startswith("L0") or f.name == "USER.example.md":
                continue
            tokens = f.stat().st_size // 4
            total_tokens += tokens
            if f.name in system_names:
                system_owned += tokens
            elif f.name == "AGENT.md":
                agent_md_tokens = tokens
        non_reducible = system_owned + agent_md_tokens
        self.report.findings.append(Finding(
            id="I3", name="Token budget", dimension="infrastructure",
            status="pass" if total_tokens < 40000 else ("warn" if total_tokens < 60000 else "fail"),
            detail=f"{total_tokens:,} tokens ({non_reducible:,} system-owned/non-reducible: AGENT {agent_md_tokens:,} + core {system_owned:,})",
        ))

        # I4: Growth rate (from history)
        growth_pct = self._compute_growth_rate(total_tokens)
        if growth_pct is not None:
            self.report.findings.append(Finding(
                id="I4", name="Growth rate", dimension="infrastructure",
                status="pass" if growth_pct < 10 else ("warn" if growth_pct < 20 else "fail"),
                detail=f"{growth_pct:.1f}% growth (4-week comparison)",
            ))
        else:
            self.report.findings.append(Finding(
                id="I4", name="Growth rate", dimension="infrastructure",
                status="n/a", detail="Insufficient history (<2 data points)",
            ))

        # I5: Stale locks
        lock_files = list(WORKSPACE.glob(".*.lock")) + list(CONTEXT_DIR.glob("*.lock"))
        stale_locks = [
            lf for lf in lock_files
            if lf.exists() and (time.time() - lf.stat().st_mtime) > 3600
        ]
        self.report.findings.append(Finding(
            id="I5", name="Stale locks", dimension="infrastructure",
            status="pass" if not stale_locks else "warn",
            detail=f"{len(stale_locks)} stale lock files" if stale_locks else "No stale locks",
            auto_fixable=bool(stale_locks),
        ))

        # I6: Stale paused pipelines — paused >14d with feature already shipped in git
        stale_pipelines = self._find_stale_pipelines()
        self.report.findings.append(Finding(
            id="I6", name="Stale pipelines", dimension="infrastructure",
            status="pass" if not stale_pipelines else "warn",
            detail=f"{len(stale_pipelines)} paused pipelines with shipped features" if stale_pipelines else "No stale pipelines",
            auto_fixable=bool(stale_pipelines),
        ))

    # ─── Auto-Remediation ────────────────────────────────────────────────────

    def _auto_remediate(self):
        for f in self.report.findings:
            if not f.auto_fixable or f.status == "pass":
                continue
            try:
                if f.id == "C3":
                    self._fix_ddd_injection()
                    f.fixed = True
                    f.fix_action = "Injected DDD section into KNOWLEDGE.md"
                elif f.id == "C4":
                    if self._fix_commit_context():
                        f.fixed = True
                        f.fix_action = "Auto-committed .context/ files"
                elif f.id == "M3":
                    self._fix_cap_enforcement()
                    f.fixed = True
                    f.fix_action = "Enforced section caps"
                elif f.id == "M5":
                    count = self._fix_archive()
                    if count:
                        f.fixed = True
                        f.fix_action = f"Archived {count} old DailyActivity files"
                elif f.id == "K1":
                    # Index refresh is handled by context_health_hook, signal it
                    f.fix_action = "Index refreshes on next session (context_health_hook)"
                elif f.id == "B2":
                    if self._fix_push():
                        f.fixed = True
                        f.fix_action = "Pushed to remote"
                elif f.id == "B4":
                    if self._fix_commit_context():
                        f.fixed = True
                        f.fix_action = "Committed critical files"
                elif f.id == "M4":
                    count = self._fix_ot_resolved()
                    if count:
                        f.fixed = True
                        f.fix_action = f"Archived {count} resolved OT entries"
                elif f.id == "M6":
                    count = self._fix_content_dedup()
                    if count:
                        f.fixed = True
                        f.fix_action = f"Removed {count} duplicate entries from MEMORY.md"
                elif f.id == "I5":
                    count = self._fix_stale_locks()
                    f.fixed = True
                    f.fix_action = f"Removed {count} stale lock files"
                elif f.id == "I6":
                    count = self._fix_stale_pipelines()
                    if count:
                        f.fixed = True
                        f.fix_action = f"Marked {count} stale pipelines as completed"
            except Exception as exc:
                f.fix_action = f"Fix failed: {exc}"

        self.report.fixes_applied = [
            f.fix_action for f in self.report.findings if f.fixed
        ]

    def _fix_ddd_injection(self):
        """Inject DDD section into KNOWLEDGE.md."""
        if not PROJECTS_DIR.is_dir():
            return
        lines = ["### Active Projects & DDD\n", "\n"]
        ddd_names = {"PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"}
        for d in sorted(PROJECTS_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            ddd = sorted(f.name for f in d.iterdir() if f.is_file() and f.name in ddd_names)
            if ddd:
                lines.append(f"- **{d.name}** — {', '.join(ddd)}\n")
        lines.append("\n")
        new_section = "".join(lines)

        kp = CONTEXT_DIR / "KNOWLEDGE.md"
        content = kp.read_text(encoding="utf-8")
        marker = "## The 11 Context Files"
        if "### Active Projects & DDD" not in content and marker in content:
            content = content.replace(marker, new_section + marker)
            kp.write_text(content, encoding="utf-8")

    def _fix_commit_context(self) -> bool:
        """Auto-commit .context/ with integrity check (PE-4)."""
        # Integrity check: MEMORY.md must have expected headers
        mem = CONTEXT_DIR / "MEMORY.md"
        if mem.exists():
            content = mem.read_text(encoding="utf-8")
            required = ["## Recent Context", "## Key Decisions", "## Lessons Learned"]
            if not all(h in content for h in required):
                return False  # Possible corruption — don't commit
            if len(content) < 100:
                return False

        # Stage .context/ files
        result = subprocess.run(
            ["git", "add", ".context/"], cwd=str(WORKSPACE),
            capture_output=True, timeout=CHECK_TIMEOUT,
        )
        if result.returncode != 0:
            return False

        # Check if there's actually something staged (avoids empty commit failure)
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet", ".context/"],
            cwd=str(WORKSPACE), capture_output=True, timeout=CHECK_TIMEOUT,
        )
        if status.returncode == 0:
            return True  # Nothing staged — state is already clean

        result = subprocess.run(
            ["git", "commit", "-m", "chore: auto-commit context (loops-health)"],
            cwd=str(WORKSPACE), capture_output=True, timeout=CHECK_TIMEOUT,
        )
        return result.returncode == 0

    def _fix_cap_enforcement(self):
        """Trigger cap enforcement via distillation hook."""
        try:
            sys.path.insert(0, str(SWARMAI_DIR / "backend"))
            from hooks.distillation_hook import DistillationTriggerHook
            DistillationTriggerHook._enforce_section_caps(
                CONTEXT_DIR / "MEMORY.md", WORKSPACE
            )
        except Exception:
            pass  # Non-critical

    def _fix_archive(self) -> int:
        """Move old DailyActivity to Archives."""
        da_dir = KNOWLEDGE_DIR / "DailyActivity"
        archive_dir = KNOWLEDGE_DIR / "Archives"
        archive_dir.mkdir(exist_ok=True)
        cutoff = (date.today() - timedelta(days=90)).isoformat()
        archived = 0
        for f in da_dir.glob("20[0-9][0-9]-*.md"):
            if f.stem < cutoff:
                head = f.read_text(encoding="utf-8", errors="ignore")[:300]
                if "distilled: true" in head:
                    f.rename(archive_dir / f.name)
                    archived += 1
        return archived

    def _fix_push(self) -> bool:
        """Push to remote with dry-run gate."""
        dry = self._git_cmd(["git", "push", "--dry-run", "origin", "HEAD"], WORKSPACE, allow_fail=True)
        if "fatal" in dry or "error" in dry.lower():
            return False
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=str(WORKSPACE), capture_output=True, text=True, timeout=30, env=env,
        )
        return result.returncode == 0

    def _fix_ot_resolved(self) -> int:
        """Remove resolved (✅) entries from Open Threads section.

        These are already recorded in COE Registry and MEMORY archives.
        Keeping them in OT causes M4 false positives from stale dates.
        """
        mem_path = CONTEXT_DIR / "MEMORY.md"
        if not mem_path.exists():
            return 0
        content = mem_path.read_text(encoding="utf-8")
        ot_start = content.find("## Open Threads")
        if ot_start < 0:
            return 0
        # Find end of OT section (next ## or end of file)
        ot_rest = content[ot_start:]
        next_section = ot_rest.find("\n## ", 4)
        if next_section < 0:
            next_section = len(ot_rest)
        ot_section = ot_rest[:next_section]

        # Remove lines starting with "- ✅" (resolved entries)
        lines = ot_section.split("\n")
        new_lines = []
        removed = 0
        for line in lines:
            if line.strip().startswith("- ✅"):
                removed += 1
            else:
                new_lines.append(line)
        if removed == 0:
            return 0
        new_ot = "\n".join(new_lines)
        content = content[:ot_start] + new_ot + ot_rest[next_section:]
        mem_path.write_text(content, encoding="utf-8")
        return removed

    def _find_stale_pipelines(self) -> list[dict]:
        """Find paused pipelines >14d old where the feature appears shipped in git."""
        stale = []
        artifacts_dir = PROJECTS_DIR / "SwarmAI" / ".artifacts" / "runs"
        if not artifacts_dir.is_dir():
            return stale
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        for run_dir in artifacts_dir.iterdir():
            run_file = run_dir / "run.json"
            if not run_file.exists():
                continue
            try:
                run_data = json.loads(run_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if run_data.get("status") != "paused":
                continue
            # Check age
            updated = run_data.get("updated_at", "")[:10]
            if updated and updated > cutoff:
                continue  # Too recent, skip
            # Check if feature shipped — extract keywords from requirement
            req = run_data.get("requirement", "")
            # Take first 3 significant words as git search terms
            words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", req)[:3]
            if not words:
                continue
            grep_term = "|".join(words)
            git_result = self._git_cmd(
                ["git", "log", "--oneline", "--since", updated, f"--grep={grep_term}", "-1"],
                SWARMAI_DIR,
            )
            if git_result.strip():
                stale.append({"id": run_data.get("id", run_dir.name), "requirement": req[:80]})
        return stale

    def _fix_stale_pipelines(self) -> int:
        """Mark stale paused pipelines as completed."""
        stale = self._find_stale_pipelines()
        fixed = 0
        for item in stale:
            run_id = item["id"]
            run_file = PROJECTS_DIR / "SwarmAI" / ".artifacts" / "runs" / run_id / "run.json"
            if not run_file.exists():
                continue
            try:
                data = json.loads(run_file.read_text())
                data["status"] = "completed"
                data["updated_at"] = datetime.now(timezone.utc).isoformat()
                run_file.write_text(json.dumps(data, indent=2))
                fixed += 1
            except (json.JSONDecodeError, OSError):
                continue
        return fixed

    def _fix_content_dedup(self) -> int:
        """Remove duplicate entries from MEMORY.md body (same bold title = same entry).

        Keeps the FIRST occurrence (which is the newest due to prepend ordering)
        and removes subsequent duplicates. Only operates on body sections, not index.
        """
        mem_path = CONTEXT_DIR / "MEMORY.md"
        if not mem_path.exists():
            return 0
        content = mem_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        seen_titles: set[str] = set()
        new_lines: list[str] = []
        removed = 0
        for line in lines:
            m = re.search(r"\*\*(.+?)\*\*", line)
            if m and line.strip().startswith("- "):
                title = m.group(1).strip().lower()
                if title in seen_titles:
                    removed += 1
                    continue  # skip duplicate
                seen_titles.add(title)
            new_lines.append(line)
        if removed > 0:
            mem_path.write_text("\n".join(new_lines), encoding="utf-8")
        return removed

    def _fix_stale_locks(self) -> int:
        """Remove lock files older than 1 hour."""
        removed = 0
        for lf in list(WORKSPACE.glob(".*.lock")) + list(CONTEXT_DIR.glob("*.lock")):
            if lf.exists() and (time.time() - lf.stat().st_mtime) > 3600:
                lf.unlink()
                removed += 1
        return removed

    # ─── History & Growth ────────────────────────────────────────────────────

    def _record_history(self):
        """Append current run to history for growth tracking."""
        history = []
        if HISTORY_FILE.exists():
            try:
                history = json.loads(HISTORY_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        total_tokens = sum(
            f.stat().st_size // 4
            for f in CONTEXT_DIR.glob("*.md")
            if not f.name.startswith("L0") and f.name != "USER.example.md"
        )

        history.append({
            "date": date.today().isoformat(),
            "score": self.report.overall_score,
            "total_tokens": total_tokens,
            "found": self.report.found_count,
            "fixed": self.report.fixed_count,
        })

        # Keep last 52 weeks
        history = history[-52:]
        HISTORY_FILE.write_text(json.dumps(history, indent=2))

    def _compute_growth_rate(self, current_tokens: int) -> float | None:
        """Compute growth vs 4 weeks ago. Returns None if insufficient data."""
        if not HISTORY_FILE.exists():
            return None
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None

        if len(history) < 2:
            return None

        # Find entry ~4 weeks ago
        target_date = (date.today() - timedelta(days=28)).isoformat()
        baseline = None
        for entry in history:
            if entry["date"] <= target_date:
                baseline = entry
        if baseline is None:
            baseline = history[0]  # Use earliest available

        baseline_tokens = baseline.get("total_tokens", current_tokens)
        if baseline_tokens == 0:
            return None

        return ((current_tokens - baseline_tokens) / baseline_tokens) * 100

    # ─── Utilities ───────────────────────────────────────────────────────────

    @staticmethod
    def _file_age_days(path: Path) -> int:
        if not path.exists():
            return 999
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return (datetime.now() - mtime).days

    @staticmethod
    def _read_safe(path: Path, limit: int = 0) -> str:
        try:
            if limit:
                return path.read_text(encoding="utf-8", errors="ignore")[:limit]
            return path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
        """Extract text between two ## markers.

        If end_marker is empty string, returns everything from start_marker to EOF.
        """
        start = text.find(start_marker)
        if start < 0:
            return ""
        after_start = start + len(start_marker)
        if not end_marker:
            return text[after_start:]
        # Find next section header (## ) after this one
        end = text.find(f"\n{end_marker}", after_start)
        if end < 0:
            end = len(text)
        return text[after_start:end]

    @staticmethod
    def _git_cmd(cmd: list[str], cwd: Path, allow_fail: bool = False) -> str:
        try:
            # GIT_TERMINAL_PROMPT=0 prevents credential prompts from blocking
            # (e.g., push --dry-run with expired HTTPS token)
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            result = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True,
                timeout=CHECK_TIMEOUT, env=env,
            )
            if result.returncode != 0 and not allow_fail:
                return ""
            return result.stdout + result.stderr
        except (subprocess.TimeoutExpired, OSError):
            return ""

    # ─── Output ──────────────────────────────────────────────────────────────

    def to_markdown(self) -> str:
        scores = self.report.scores
        overall = self.report.overall_score
        emoji = "\U0001f7e2" if overall >= 90 else "\U0001f7e1" if overall >= 70 else "\U0001f7e0" if overall >= 50 else "\U0001f534"

        found = self.report.found_count
        fixed = self.report.fixed_count
        pending = len(self.report.pending)

        lines = [
            f"---",
            f"job_id: loops-health",
            f"status: {'healthy' if overall >= 70 else 'degraded' if overall >= 50 else 'at_risk' if overall >= 30 else 'critical'}",
            f"score: {overall}",
            f"date: {date.today().isoformat()}",
            f"found: {found}",
            f"fixed: {fixed}",
            f"pending: {pending}",
            f"---",
            f"",
            f"# Self-Loops Maintenance Report — {date.today().isoformat()}",
            f"",
            f"## Summary: Found {found} | Fixed {fixed} | Pending {pending}",
            f"",
            f"| Dimension | Score | Found | Fixed |",
            f"|-----------|-------|-------|-------|",
        ]

        dim_names = {
            "context": "Self-Context", "memory": "Self-Memory",
            "knowledge": "Self-Knowledge", "evolution": "Self-Evolution",
            "coherence": "Cross-Loop", "brain_safety": "Brain Safety",
            "infrastructure": "Infrastructure",
        }
        for dim, score in scores.items():
            dim_findings = [f for f in self.report.findings if f.dimension == dim]
            dim_found = sum(1 for f in dim_findings if f.status != "pass")
            dim_fixed = sum(1 for f in dim_findings if f.fixed)
            e = "\U0001f7e2" if score >= 90 else "\U0001f7e1" if score >= 70 else "\U0001f7e0" if score >= 50 else "\U0001f534"
            lines.append(f"| {dim_names.get(dim, dim)} | {e} {score} | {dim_found} | {dim_fixed} |")

        # Fixed section
        fixed_findings = [f for f in self.report.findings if f.fixed]
        if fixed_findings:
            lines.append("")
            lines.append("## Fixed (autonomous)")
            lines.append("")
            lines.append("| # | ID | What | Action |")
            lines.append("|---|-----|------|--------|")
            for i, f in enumerate(fixed_findings, 1):
                lines.append(f"| {i} | {f.id} | {f.name}: {f.detail[:40]} | {f.fix_action} |")

        # Findings (not fixed)
        unfixed = [f for f in self.report.findings if f.status != "pass" and not f.fixed]
        if unfixed:
            lines.append("")
            lines.append("## Needs Attention")
            lines.append("")
            for f in unfixed:
                icon = "\U0001f534" if f.status == "fail" else "\U0001f7e1"
                # Add actionability hint so users know what needs action vs what's informational
                hint = ""
                if f.auto_fixable:
                    hint = " *(auto-fixable — run with --auto-fix)*"
                elif f.status == "warn" and not f.auto_fixable:
                    hint = " *(known — monitor only)*"
                lines.append(f"- {icon} **{f.id} {f.name}** — {f.detail}{hint}")

        # Trend
        if HISTORY_FILE.exists():
            try:
                history = json.loads(HISTORY_FILE.read_text())[-4:]
                if len(history) > 1:
                    lines.append("")
                    lines.append("## Trend")
                    lines.append("")
                    lines.append("| Date | Score | Found | Fixed |")
                    lines.append("|------|-------|-------|-------|")
                    for h in history:
                        lines.append(f"| {h['date']} | {h.get('score', '?')} | {h.get('found', '?')} | {h.get('fixed', '?')} |")
            except (json.JSONDecodeError, OSError):
                pass

        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.report.timestamp,
            "overall_score": self.report.overall_score,
            "scores": self.report.scores,
            "found": self.report.found_count,
            "fixed": self.report.fixed_count,
            "findings": [
                {"id": f.id, "name": f.name, "dimension": f.dimension,
                 "status": f.status, "detail": f.detail, "fixed": f.fixed,
                 "fix_action": f.fix_action}
                for f in self.report.findings
            ],
            "fixes_applied": self.report.fixes_applied,
            "pending": self.report.pending,
        }, indent=2)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Self-Loops Health Engine")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--auto-fix", action="store_true", help="Auto-fix safe issues")
    parser.add_argument("--output-dir", type=str, help="Write report to directory")
    parser.add_argument("--alert-threshold", type=int, default=70, help="RADAR_TODOS threshold")
    args = parser.parse_args()

    engine = SelfLoopsHealthEngine()
    engine.run(auto_fix=args.auto_fix)

    if args.json:
        print(engine.to_json())
    else:
        md = engine.to_markdown()
        print(md)

        if args.output_dir:
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{date.today().isoformat()}-loops-health.md"
            out_path.write_text(md)

    # RADAR_TODOS for job alerting
    if engine.report.overall_score < args.alert_threshold:
        critical = [f for f in engine.report.findings if f.status == "fail"]
        todos = [{
            "title": f"Self-Loops Health: {engine.report.overall_score}/100 — {len(critical)} critical",
            "priority": "high" if engine.report.overall_score < 50 else "medium",
            "description": f"Scores: {engine.report.scores}",
            "context": {
                "source": "loops-health-job",
                "score": engine.report.overall_score,
                "dimensions": engine.report.scores,
                "critical_checks": [f.id for f in critical],
                "next_step": "Run s_loops-health skill for interactive diagnosis",
            },
        }]
        print(f"\n<!-- RADAR_TODOS\n{json.dumps(todos, indent=2)}\n-->")


if __name__ == "__main__":
    main()
