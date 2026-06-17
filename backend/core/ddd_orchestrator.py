"""DDD Cultivation Orchestrator — self-contained DDD feed engine.

Orchestrates 7 independent DDD feed channels. Each channel runs in its own
try/except — one crash never affects others. Returns merged findings list.

All channel logic lives HERE — no delegation back to context_health_hook.
context_health_hook calls orchestrator.run(), not the other way around.

Channels:
    1. DDD staleness detection
    2. Auto-apply mechanical proposals + feedback tracking
    3. DDD→KNOWLEDGE injection
    4. Knowledge staleness detection
    5. Entity index validation
    6. Signal→DDD bridge (hooks.signal_ddd_bridge)
    7. Code Intelligence drift (core.code_intel_feed)

Public symbols:
    - DddCultivationOrchestrator  — main orchestrator class
"""
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from core.cultivation_dispatcher import ChannelTask, EventType

logger = logging.getLogger(__name__)

# Type alias for channel functions
ChannelFn = Callable[[Path, str], list[str]]

# Timeout for git subprocess calls (seconds)
_GIT_TIMEOUT = 10

# Sections that are never auto-applied (require human judgment)
_SEMANTIC_SECTIONS = ("Non-Goals", "Vision", "Architecture")

# Source paths that should trigger DDD staleness for specific projects.
# Key: project name (case-sensitive, matches Projects/<name>/)
# Value: list of paths (relative to swarmai repo root) to watch via git log.
# When these paths have recent commits AND the DDD doc is old, flag as stale.
# This catches cases where commit messages don't mention the project name
# (e.g., pipeline commits say "pipeline:" not "AIDLC").
# NOTE: paths are checked against the SWARMAI repo (not SwarmWS).
_SOURCE_WATCH_PATHS: dict[str, list[str]] = {
    "AIDLC": [
        "backend/skills/s_autonomous-pipeline/",
        "backend/core/ddd/",
        "backend/skills/s_deliver/",
        "backend/skills/s_pollinate/",
    ],
    "SwarmAI": [
        "backend/core/session_unit.py",
        "backend/core/session_router.py",
        "backend/core/context_directory_loader.py",
        "backend/channels/gateway.py",
        "backend/channels/adapters/",
        "backend/main.py",
    ],
}


def _find_swarmai_root() -> "Path | None":
    """Find the swarmai source repo root (NOT SwarmWS).

    Resolution order:
    1. SWARMAI_ROOT env var
    2. Relative to this file (core/ → backend/ → swarmai/)
    3. Sibling of workspace path (legacy layout)
    """
    env_root = os.environ.get("SWARMAI_ROOT")
    if env_root:
        p = Path(env_root)
        if (p / "backend").is_dir():
            return p

    # This file: core/ddd_orchestrator.py → core/ → backend/ → swarmai/
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "backend").is_dir():
        return source_root

    return None


class DddCultivationOrchestrator:
    """Orchestrates DDD Cultivation feed channels with fault isolation.

    Each channel is a (name, callable) pair. The callable receives (root, ws_path)
    and returns a list of findings (strings). If a channel raises, the error is
    logged and captured as a finding — other channels continue unaffected.
    """

    # Per-channel budget (seconds) for event-driven execution
    _CHANNEL_BUDGETS: dict[str, float] = {
        "ddd_staleness": 2.0,
        "auto_apply_proposals": 3.0,
        "ddd_knowledge_injection": 1.0,
        "knowledge_staleness": 1.0,
        "entity_index_validation": 2.0,
        "signal_ddd_bridge": 3.0,
        "code_intel_drift": 5.0,
        "entry_lifecycle": 3.0,
        "mechanical_refresh": 3.0,
        "memory_refresh": 3.0,
    }

    def _get_gate_manager(self, root: Path) -> "GateManager | None":
        """Get or create GateManager, initializing gate_promotion_data.json if missing."""
        try:
            from core.gate_promotion import GateManager
            artifacts_dir = self._find_artifacts_dir(root)
            if artifacts_dir:
                return GateManager(artifacts_dir)
        except Exception as exc:
            logger.debug("ddd_orchestrator: gate manager init failed: %s", exc)
        return None

    def __init__(self) -> None:
        # Each channel: (name, callable, set of subscribed EventTypes)
        self.channels: list[tuple[str, ChannelFn, set[EventType]]] = [
            ("ddd_staleness", self._ch_ddd_staleness, {
                EventType.GIT_COMMIT, EventType.TIMER_30MIN,
            }),
            ("auto_apply_proposals", self._ch_auto_apply, {
                EventType.PROPOSAL_DECIDED, EventType.SESSION_CLOSE,
            }),
            ("ddd_knowledge_injection", self._ch_inject_knowledge, {
                EventType.SESSION_CLOSE, EventType.GIT_COMMIT,
            }),
            ("knowledge_staleness", self._ch_knowledge_staleness, {
                EventType.GIT_COMMIT, EventType.TIMER_30MIN,
            }),
            ("entity_index_validation", self._ch_entity_index, {
                EventType.SESSION_CLOSE,
            }),
            ("signal_ddd_bridge", self._ch_signal_bridge, {
                EventType.SIGNAL_DIGEST,
            }),
            ("code_intel_drift", self._ch_code_intel, {
                EventType.CODE_INTEL_INDEXED,
            }),
            ("entry_lifecycle", self._ch_entry_lifecycle, {
                EventType.TIMER_30MIN, EventType.SESSION_CLOSE,
            }),
            ("mechanical_refresh", self._ch_mechanical_refresh, {
                EventType.GIT_COMMIT, EventType.TIMER_30MIN,
            }),
            ("memory_refresh", self._ch_memory_refresh, {
                EventType.GIT_COMMIT, EventType.TIMER_30MIN,
            }),
        ]

    def run(self, root: Path, ws_path: str) -> list[str]:
        """Execute all channels (legacy batch mode), return merged findings.

        Each channel runs independently. Failures are captured as findings
        (not re-raised). Returns all findings from all successful channels
        plus error notices from failed ones.

        Note: In v2, prefer get_tasks_for_event() + ChannelExecutor for
        event-driven execution. This method is kept for backward compat
        and manual health-check triggers.
        """
        all_findings: list[str] = []

        for name, channel_fn, _events in self.channels:
            try:
                findings = channel_fn(root, ws_path)
                if findings:
                    all_findings.extend(findings)
            except Exception as exc:
                # Capture error as finding — never let one channel kill others
                logger.warning(
                    "ddd_orchestrator: channel '%s' failed (non-blocking): %s",
                    name, exc,
                )
                all_findings.append(
                    f"CHANNEL_ERROR: {name} — {type(exc).__name__}: {exc}"
                )

        # Check gate promotions (v2) — evaluate eligibility after channels run
        try:
            gate_mgr = self._get_gate_manager(root)
            if gate_mgr:
                promoted = gate_mgr.check_promotions()
                for gate_name in promoted:
                    all_findings.append(
                        f"GATE_PROMOTED: {gate_name} → hard enforcement"
                    )
        except Exception as exc:
            logger.debug("ddd_orchestrator: gate promotion check skipped: %s", exc)

        return all_findings

    def get_tasks_for_event(
        self, event_type: EventType, root: Path, ws_path: str
    ) -> list[ChannelTask]:
        """Return ChannelTasks for channels subscribed to this event type.

        Used by EventDispatcher to build the execution batch for a specific
        event. Only channels whose subscription set includes event_type are
        returned.
        """
        tasks: list[ChannelTask] = []
        for name, channel_fn, subscribed_events in self.channels:
            if event_type in subscribed_events:
                budget = self._CHANNEL_BUDGETS.get(name, 3.0)
                # Priority mapping: signal/code_intel/auto_apply = 1, staleness/inject = 2, entity/knowledge = 3
                if name in ("auto_apply_proposals", "signal_ddd_bridge", "code_intel_drift"):
                    priority = 1
                elif name in ("ddd_staleness", "ddd_knowledge_injection"):
                    priority = 2
                else:
                    priority = 3
                tasks.append(ChannelTask(
                    name=name,
                    priority=priority,
                    budget=budget,
                    fn=channel_fn,
                    root=root,
                    ws_path=ws_path,
                ))
        return tasks

    # ── Helper ─────────────────────────────────────────────────────────────

    def _find_proposals_dir(self, root: Path) -> Path | None:
        """Locate the proposals directory for the active project."""
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return None
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            proposals = project_dir / ".artifacts" / "proposals"
            if proposals.is_dir() and any(proposals.glob("proposal_*.json")):
                return proposals
        return None

    def _find_artifacts_dir(self, root: Path, project: str = "SwarmAI") -> Path | None:
        """Locate the .artifacts directory for the specified project.

        Args:
            root: Workspace root path
            project: Project name (default SwarmAI — the primary project)
        """
        # Prefer explicit project
        explicit = root / "Projects" / project / ".artifacts"
        if explicit.is_dir():
            return explicit
        # Fallback: first project with .artifacts/
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return None
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            artifacts = project_dir / ".artifacts"
            if artifacts.is_dir():
                return artifacts
        return None

    # ── Channel 1: DDD Staleness ───────────────────────────────────────────

    def _ch_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Flag DDD docs stale >14 days vs active code commits."""
        findings = []
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return findings

        cutoff = datetime.now() - timedelta(days=14)
        gate_mgr = self._get_gate_manager(root)  # Create once, reuse per-finding

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue

            for ddd_name in ("TECH.md", "PRODUCT.md"):
                ddd_file = project_dir / ddd_name
                if not ddd_file.exists():
                    continue

                mtime = datetime.fromtimestamp(ddd_file.stat().st_mtime)
                if mtime > cutoff:
                    continue

                try:
                    # Strategy 1: grep commit messages for project name
                    result = subprocess.run(
                        ["git", "log", "--oneline", "--since=14 days ago",
                         "--grep", project_dir.name, "--", "."],
                        cwd=ws_path, capture_output=True, text=True,
                        timeout=_GIT_TIMEOUT,
                    )
                    commit_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0

                    # Strategy 2: check watched source paths (catches commits
                    # that don't mention the project name in commit message).
                    # NOTE: runs against SWARMAI repo, not SwarmWS.
                    if commit_count == 0 and project_dir.name in _SOURCE_WATCH_PATHS:
                        swarmai_root = _find_swarmai_root()
                        if swarmai_root:
                            for watch_path in _SOURCE_WATCH_PATHS[project_dir.name]:
                                path_result = subprocess.run(
                                    ["git", "log", "--oneline", "--since=14 days ago",
                                     "--", watch_path],
                                    cwd=str(swarmai_root),
                                    capture_output=True, text=True,
                                    timeout=_GIT_TIMEOUT,
                                )
                                if path_result.stdout.strip():
                                    commit_count = len(path_result.stdout.strip().splitlines())
                                    break  # One matching path is enough

                    if commit_count > 0:
                        days_stale = (datetime.now() - mtime).days
                        findings.append(
                            f"DDD-STALE: {project_dir.name}/{ddd_name} "
                            f"({days_stale}d old, {commit_count} recent commits)"
                        )
                        # Gate trigger: staleness detected = file_tracker gate fires
                        if gate_mgr:
                            gate_mgr.record_trigger("file_tracker")
                except (subprocess.TimeoutExpired, OSError):
                    pass

        return findings

    # ── Channel 2: Auto-Apply Proposals + Feedback ─────────────────────────

    def _ch_auto_apply(self, root: Path, ws_path: str) -> list[str]:
        """Auto-apply mechanical DDD refresh proposals + feedback tracking."""
        self._auto_apply_ddd_proposals(root)

        # After applying proposals, compute channel precision stats
        try:
            from core.proposal_feedback import ProposalFeedbackTracker

            proposals_dir = self._find_proposals_dir(root)
            if proposals_dir and proposals_dir.is_dir():
                tracker = ProposalFeedbackTracker()
                artifacts_dir = proposals_dir.parent  # .artifacts/
                tracker.compute_channel_stats(proposals_dir, persist_to=artifacts_dir)
        except Exception as exc:
            logger.debug("ddd_orchestrator: feedback tracking skipped: %s", exc)

        return []  # Side-effect only, no findings

    def _auto_apply_ddd_proposals(self, root: Path) -> None:
        """Auto-apply mechanical DDD refresh proposals.

        Scans Projects/*/.artifacts/ddd-refresh-*.md for proposals.
        For each proposal with confidence >= 8:
        - Parse Current/Proposed code blocks
        - Classify: mechanical (only adds lines) vs semantic (modifies/deletes)
        - Skip changes targeting Non-Goals, Vision, or Architecture sections
        - Apply mechanical changes to the target DDD doc
        - Rename proposal to .applied after processing
        - Log applied changes to health_findings.json
        """
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        applied_changes: list[dict] = []
        gate_mgr = self._get_gate_manager(root)  # Create once, reuse per-trigger

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            artifacts_dir = project_dir / ".artifacts"
            if not artifacts_dir.is_dir():
                continue

            proposals = sorted(artifacts_dir.glob("ddd-refresh-*.md"))
            proposals = [p for p in proposals if not p.name.endswith(".applied")]

            for proposal_path in proposals:
                try:
                    content = proposal_path.read_text(encoding="utf-8")

                    # Extract confidence score
                    conf_match = re.search(r"\*\*Confidence:\*\*\s*(\d+)/10", content)
                    if not conf_match:
                        continue
                    confidence = int(conf_match.group(1))
                    if confidence < 8:
                        proposal_path.rename(proposal_path.with_suffix(".md.applied"))
                        # Gate trigger: low-confidence proposal rejected = noise_filter fires
                        if gate_mgr:
                            gate_mgr.record_trigger("noise_filter")
                        continue

                    # Check for semantic section targets
                    targets_line = ""
                    for line in content.splitlines():
                        if "_Targets:" in line or "Targets:" in line:
                            targets_line = line.lower()
                            break
                    targets_semantic = any(
                        s.lower() in targets_line for s in _SEMANTIC_SECTIONS
                    )

                    # Parse Current/Proposed blocks
                    block_pattern = re.compile(
                        r"\*\*Current:\*\*\s*\n```\n(.*?)\n```\s*\n+"
                        r"\*\*Proposed:\*\*\s*\n```\n(.*?)\n```",
                        re.DOTALL,
                    )
                    for match in block_pattern.finditer(content):
                        current_block = match.group(1)
                        proposed_block = match.group(2)

                        current_lines = current_block.strip().splitlines()
                        proposed_lines = proposed_block.strip().splitlines()

                        is_mechanical = (
                            len(proposed_lines) > len(current_lines)
                            and proposed_lines[:len(current_lines)] == current_lines
                        )

                        if not is_mechanical or targets_semantic:
                            # Gate trigger: semantic section or non-mechanical change skipped
                            if targets_semantic and gate_mgr:
                                gate_mgr.record_trigger("trust_annotation")
                            continue

                        from utils.file_lock import flock_exclusive, flock_unlock
                        for ddd_name in ("TECH.md", "IMPROVEMENT.md", "PRODUCT.md"):
                            ddd_path = project_dir / ddd_name
                            if not ddd_path.exists():
                                continue
                            lock_path = ddd_path.with_suffix(ddd_path.suffix + ".lock")
                            lock_file = None
                            try:
                                lock_file = open(lock_path, "w")
                                flock_exclusive(lock_file)
                            except OSError:
                                if lock_file:
                                    lock_file.close()
                                continue
                            try:
                                ddd_content = ddd_path.read_text(encoding="utf-8")
                                if current_block in ddd_content:
                                    new_content = ddd_content.replace(
                                        current_block, proposed_block, 1
                                    )
                                    ddd_path.write_text(new_content, encoding="utf-8")
                                    applied_changes.append({
                                        "project": project_dir.name,
                                        "doc": ddd_name,
                                        "proposal": proposal_path.name,
                                        "type": "mechanical_append",
                                    })
                                    logger.info(
                                        "DDD auto-apply: applied mechanical change to %s/%s from %s",
                                        project_dir.name, ddd_name, proposal_path.name,
                                    )
                                    break
                            finally:
                                flock_unlock(lock_file)
                                lock_file.close()

                    proposal_path.rename(proposal_path.with_suffix(".md.applied"))

                except Exception as exc:
                    logger.warning("DDD auto-apply failed for %s: %s", proposal_path.name, exc)

        # Log to health_findings.json
        if applied_changes:
            findings_dir = root / "Services" / "swarm-jobs"
            findings_file = findings_dir / "health_findings.json"
            if findings_file.exists():
                try:
                    data = json.loads(findings_file.read_text(encoding="utf-8"))
                    for change in applied_changes:
                        data["findings"].append({
                            "level": "info",
                            "message": (
                                f"DDD-AUTO-APPLY: {change['type']} in "
                                f"{change['project']}/{change['doc']} "
                                f"from {change['proposal']}"
                            ),
                        })
                    findings_file.write_text(
                        json.dumps(data, indent=2, default=str),
                        encoding="utf-8",
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Failed to log DDD auto-apply: %s", exc)

    # ── Channel 3: DDD→KNOWLEDGE Injection ─────────────────────────────────

    def _ch_inject_knowledge(self, root: Path, ws_path: str) -> list[str]:
        """Inject or update Active Projects & DDD section in KNOWLEDGE.md."""
        projects_dir = root / "Projects"
        knowledge_path = root / ".context" / "KNOWLEDGE.md"
        if not projects_dir.is_dir() or not knowledge_path.exists():
            return []

        # Build DDD summary
        lines = ["### Active Projects & DDD\n", "\n"]
        ddd_names = {"PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"}
        found_any = False
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            ddd_files = sorted(f.name for f in d.iterdir() if f.is_file() and f.name in ddd_names)
            if ddd_files:
                lines.append(f"- **{d.name}** — {', '.join(ddd_files)}\n")
                found_any = True

        if not found_any:
            return []

        lines.append("\n")
        new_section = "".join(lines)

        content = knowledge_path.read_text(encoding="utf-8")
        section_marker = "### Active Projects & DDD"
        insert_before = "## The 11 Context Files"

        if section_marker in content:
            start = content.find(section_marker)
            rest = content[start + len(section_marker):]
            end_match = re.search(r"\n#{2,3} ", rest)
            if end_match:
                end_pos = start + len(section_marker) + end_match.start()
            elif insert_before in rest:
                end_pos = start + len(section_marker) + rest.find(insert_before)
            else:
                return []
            content = content[:start] + new_section + content[end_pos:]
        elif insert_before in content:
            content = content.replace(insert_before, new_section + insert_before)
        else:
            return []

        knowledge_path.write_text(content, encoding="utf-8")
        logger.info(
            "ddd_orchestrator: injected DDD summary into KNOWLEDGE.md (%d projects)",
            sum(1 for ln in lines if ln.startswith("- ")),
        )
        return []

    # ── Channel 4: Knowledge Staleness ─────────────────────────────────────

    def _ch_knowledge_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Detect when backend code changed but KNOWLEDGE.md hasn't been updated."""
        findings = []
        swarmai_dir = Path(os.environ.get(
            "SWARMAI_SOURCE",
            str(Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"),
        ))
        if not (swarmai_dir / ".git").exists():
            return findings

        key_dirs = ["backend/core", "backend/hooks", "backend/routers", "backend/channels"]
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct", "--"] + key_dirs,
                cwd=str(swarmai_dir), capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return findings
            backend_ts = int(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return findings

        knowledge_path = root / ".context" / "KNOWLEDGE.md"
        if not knowledge_path.exists():
            return findings
        knowledge_mtime = int(knowledge_path.stat().st_mtime)

        drift_days = (backend_ts - knowledge_mtime) // 86400
        if drift_days > 7:
            findings.append(
                f"STALE: KNOWLEDGE.md architecture may be outdated — "
                f"backend code changed {drift_days}d after last KNOWLEDGE edit. "
                f"Run `loops health` or manually review Architecture section."
            )
            logger.info(
                "ddd_orchestrator: KNOWLEDGE.md is %dd behind backend changes",
                drift_days,
            )

        return findings

    # ── Channel 5: Entity Index Validation ─────────────────────────────────

    def _ch_entity_index(self, root: Path, ws_path: str) -> list[str]:
        """Validate Entity Index references in PROJECTS.md point to real sections."""
        findings: list[str] = []
        projects_md = root / ".context" / "PROJECTS.md"
        if not projects_md.exists():
            return findings

        try:
            content = projects_md.read_text(encoding="utf-8")
        except OSError:
            return findings

        if "## Cross-Project Knowledge Index" not in content:
            return findings

        # Only iterate lines within Entity Index section
        in_entity_section = False
        entity_lines: list[str] = []
        for line in content.splitlines():
            if "## Cross-Project Knowledge Index" in line:
                in_entity_section = True
                continue
            if in_entity_section:
                if line.startswith("---") or (line.startswith("## ") and "Cross-Project" not in line):
                    break
                entity_lines.append(line)

        ref_pattern = re.compile(r"([^/|]+)/([^#|]+)#(.+?)(?:,\s*|$|\s*\|)")
        stale_count = 0
        headings_cache: dict[Path, list[str]] = {}

        for line in entity_lines:
            if not line.startswith("| ") or "References" in line or "---" in line:
                continue

            for match in ref_pattern.finditer(line):
                project = match.group(1).strip()
                doc = match.group(2).strip()
                section = match.group(3).strip()
                project_dir = root / "Projects" / project
                doc_path = project_dir / f"{doc}.md"

                if not project_dir.exists() or not doc_path.exists():
                    stale_count += 1
                    continue

                if doc_path not in headings_cache:
                    try:
                        doc_content = doc_path.read_text(encoding="utf-8")
                        headings_cache[doc_path] = [
                            l[3:].strip()
                            for l in doc_content.splitlines()
                            if l.startswith("## ") and not l.startswith("### ")
                        ]
                    except OSError:
                        headings_cache[doc_path] = []

                if section not in headings_cache[doc_path]:
                    stale_count += 1

        if stale_count > 0:
            findings.append(
                f"STALE_ENTITY_REFS: {stale_count} entity index reference(s) "
                f"point to missing sections — will refresh on next startup"
            )
            logger.info(
                "Entity Index has %d stale refs — refresh on next startup",
                stale_count,
            )

        return findings

    # ── Channel 6: Signal→DDD Bridge ───────────────────────────────────────

    def _ch_signal_bridge(self, root: Path, ws_path: str) -> list[str]:
        """Signal→DDD bridge (high-relevance signals → proposals)."""
        from hooks.signal_ddd_bridge import bridge_signals_to_ddd

        proposal_count = bridge_signals_to_ddd(ws_path)
        if proposal_count > 0:
            logger.info(
                "ddd_orchestrator: signal bridge generated %d proposals",
                proposal_count,
            )
        return []

    # ── Channel 7: Code Intelligence Drift ─────────────────────────────────

    def _ch_code_intel(self, root: Path, ws_path: str) -> list[str]:
        """Code Intelligence drift → TECH.md proposals."""
        from core.code_intel_feed import detect_tech_drift

        drift_count = detect_tech_drift(ws_path)
        if drift_count > 0:
            logger.info(
                "ddd_orchestrator: code intel drift generated %d proposals",
                drift_count,
            )
        return []

    def _ch_entry_lifecycle(self, root: Path, ws_path: str) -> list[str]:
        """Channel 8: Per-entry reference bumping, decay assessment, and state transitions.

        Runs on TIMER_30MIN and SESSION_CLOSE. For each project's IMPROVEMENT.md:
        1. Bump references from recent DailyActivity text (F1 activation)
        2. Assess decay transitions (active→dormant→archived)
        3. Archive entries that reached end-of-life
        Uses fcntl advisory lock to prevent concurrent read-modify-write.
        """
        import fcntl
        from datetime import date as _date, timedelta as _timedelta
        from core.ddd_entry_lifecycle import (
            archive_entries,
            assess_decay,
            bump_references,
            inject_entry_metadata,
            parse_entries,
        )

        findings: list[str] = []
        projects_dir = Path(ws_path) / "Projects"
        if not projects_dir.is_dir():
            return findings

        today = _date.today()
        resolved_projects = projects_dir.resolve()

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            # S1/S3 fix: skip symlinks and validate containment
            if project_dir.is_symlink():
                continue
            if not project_dir.resolve().is_relative_to(resolved_projects):
                continue
            imp_path = project_dir / "IMPROVEMENT.md"
            if not imp_path.is_file():
                continue

            lock_fd = None
            try:
                # Advisory file lock to prevent concurrent writes (F5 fix)
                # C1 fix: use try/finally around entire open+lock sequence
                lock_path = project_dir / ".IMPROVEMENT.md.lock"
                lock_fd = open(lock_path, "w")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError):
                    # Another process holds the lock — skip this project
                    lock_fd.close()
                    lock_fd = None
                    continue

                content = imp_path.read_text(encoding="utf-8")
                entries = parse_entries(content)
                if not entries:
                    # C2 fix: don't use continue — fall through to finally
                    pass
                else:
                    # F1: Bump references from recent DailyActivity text
                    # This makes ref counts non-zero for actively-discussed knowledge,
                    # preventing the decay engine from archiving useful entries.
                    daily_dir = Path(ws_path) / "Knowledge" / "DailyActivity"
                    if daily_dir.is_dir():
                        today_str = today.isoformat()
                        yesterday_str = (today - _timedelta(days=1)).isoformat()
                        signal_texts: list[str] = []
                        try:
                            for da_file in sorted(daily_dir.iterdir(), reverse=True)[:10]:
                                if da_file.suffix != ".md":
                                    continue
                                if da_file.stem.startswith(today_str) or da_file.stem.startswith(yesterday_str):
                                    signal_texts.append(da_file.read_text(errors="ignore")[:8000])
                        except OSError:
                            pass  # DailyActivity unreadable — skip bumping, still decay

                        if signal_texts:
                            combined_text = "\n".join(signal_texts)
                            graph_path = Path(ws_path) / ".context" / ".knowledge-graph.yaml"
                            bumped = bump_references(
                                entries, combined_text, today,
                                graph_path=graph_path if graph_path.exists() else None,
                            )
                            if bumped:
                                findings.append(
                                    f"ENTRY_BUMP: {bumped} entries referenced in "
                                    f"{project_dir.name} (from DailyActivity)"
                                )

                    transitions = assess_decay(entries, today)
                    if transitions:
                        # Separate archival transitions from dormant transitions
                        to_archive = []
                        for t in transitions:
                            t.entry.decay_state = t.new_state
                            findings.append(
                                f"ENTRY_DECAY: [{t.entry.entry_type}] "
                                f"'{t.entry.title[:50]}' "
                                f"{t.old_state}→{t.new_state} ({t.reason})"
                            )
                            if t.new_state == "archived":
                                to_archive.append(t.entry)

                        # Archive entries that transitioned to archived state
                        if to_archive:
                            archive_entries(project_dir, to_archive)

                    # Write updated metadata back — covers BOTH ref bumps (F1)
                    # and decay transitions. Only write if content actually changed.
                    active_entries = [e for e in entries if e.decay_state != "archived"]
                    updated = inject_entry_metadata(content, active_entries)
                    if updated != content:
                        imp_path.write_text(updated, encoding="utf-8")

            except Exception as exc:
                logger.debug(
                    "entry_lifecycle: %s failed: %s",
                    project_dir.name, type(exc).__name__,
                )
            finally:
                # C1/C2 fix: always release lock regardless of path taken
                if lock_fd is not None:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except (OSError, IOError):
                        pass
                    lock_fd.close()

    # ── Channel 9: Mechanical Auto-Refresh (Layer 1) ──────────────────────

    def _ch_mechanical_refresh(self, root: Path, ws_path: str) -> list[str]:
        """Layer 1: Detect and fix numeric/list drift in DDD & context files.

        Zero LLM. Uses filesystem as source of truth. Provably correct.
        Runs on GIT_COMMIT (detect drift from code changes) + TIMER_30MIN.
        """
        findings: list[str] = []

        try:
            from core.auto_refresh import (
                MechanicalRefresher, log_refresh_results,
            )

            swarmai_root = _find_swarmai_root()
            if not swarmai_root:
                return findings

            refresher = MechanicalRefresher(swarmai_root, Path(root))
            results = refresher.detect_and_fix()

            if not results:
                return findings

            # Apply fixes
            applied = refresher.apply_fixes(results)

            # Log for weekly report audit trail
            log_path = Path(root) / ".context" / ".auto_refresh_log.jsonl"
            applied_results = [r for r in results if r.applied]
            if applied_results:
                log_refresh_results(applied_results, log_path)

            # Report findings
            for r in results:
                status = "FIXED" if r.applied else "DETECTED"
                findings.append(
                    f"AUTO-REFRESH-L1: {status} {r.target_file}: "
                    f"'{r.old_value}' → '{r.new_value}' [{r.evidence}]"
                )

            if applied > 0:
                logger.info(
                    "ddd_orchestrator.mechanical_refresh: applied %d fixes", applied
                )

        except Exception as exc:
            logger.warning("ddd_orchestrator.mechanical_refresh failed: %s", exc)
            findings.append(f"CHANNEL_ERROR: mechanical_refresh — {type(exc).__name__}: {exc}")

        return findings

    # ── Channel 10: Memory Entry Refresh ──────────────────────────────────

    def _ch_memory_refresh(self, root: Path, ws_path: str) -> list[str]:
        """Cross-reference MEMORY.md KD/LL entries against source code constants.

        Detects when a KD references a value that has changed in code.
        Layer 1 (mechanical) — fixes numeric drift in existing memory entries.
        """
        findings: list[str] = []

        try:
            from core.auto_refresh import (
                MemoryEntryRefresher, MechanicalRefresher, log_refresh_results,
            )

            swarmai_root = _find_swarmai_root()
            if not swarmai_root:
                return findings

            refresher = MemoryEntryRefresher(swarmai_root, Path(root))
            results = refresher.scan_memory()

            if not results:
                return findings

            # Apply fixes using MechanicalRefresher's apply logic
            mech = MechanicalRefresher(swarmai_root, Path(root))
            applied = mech.apply_fixes(results)

            # Log
            log_path = Path(root) / ".context" / ".auto_refresh_log.jsonl"
            applied_results = [r for r in results if r.applied]
            if applied_results:
                log_refresh_results(applied_results, log_path)

            for r in results:
                status = "FIXED" if r.applied else "DETECTED"
                findings.append(
                    f"AUTO-REFRESH-MEMORY: {status} {r.old_value} → {r.new_value} [{r.evidence}]"
                )

            if applied > 0:
                logger.info(
                    "ddd_orchestrator.memory_refresh: applied %d fixes", applied
                )

        except Exception as exc:
            logger.warning("ddd_orchestrator.memory_refresh failed: %s", exc)
            findings.append(f"CHANNEL_ERROR: memory_refresh — {type(exc).__name__}: {exc}")

        return findings

        return findings
