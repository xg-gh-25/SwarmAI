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
from core.ddd_paths import ddd_path

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
#
# MANUAL overrides — for projects whose in-repo source does NOT follow the
# `s_<x>-*` skill-prefix convention (core files, multi-skill AIDLC). Projects
# that DO follow the convention are auto-derived (see _watch_paths_for); the
# two are UNIONed, never either/or. (run_91bc0651: DDD-alive M1 — replaces the
# old hardcoded-only dict so a NEW project that follows the convention gets a
# watch leg with ZERO manual registration.)
_MANUAL_WATCH_PATHS: dict[str, list[str]] = {
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


# Business-domain project suffixes that follow the "domain owns s_<domain>-*"
# convention (a project `<DOMAIN>_<SUFFIX>` owns the whole s_<domain>-* skill
# family). This is an ALLOWLIST, not a denylist (allowlist fails closed —
# per session lesson: a naive token-prefix mis-attributes, e.g. GitHub_Community
# would wrongly attach unrelated s_github-research/s_github-trending AND miss the
# real s_github_community; ai_ready_repo would broad-match s_ai-*). Only the
# business-project family is known to follow the domain-owns-family convention;
# everything else falls back to _MANUAL_WATCH_PATHS ∪ Strategy-1 commit-grep
# (project-agnostic, already covers every project). run_91bc0651 Gate-2 H3/H4.
_BUSINESS_PROJECT_SUFFIXES: frozenset[str] = frozenset({
    "salesintel", "biz", "isv",
})


def _derive_skill_prefix(project_name: str) -> "str | None":
    """Derive the skill-prefix for a BUSINESS-DOMAIN project by convention.

    ONLY `<DOMAIN>_<SUFFIX>` where SUFFIX ∈ _BUSINESS_PROJECT_SUFFIXES (e.g.
    CMHK_SalesIntel → `s_cmhk-`, Foo_BIZ → `s_foo-`). These projects follow the
    "domain owns the whole s_<domain>-* skill family" convention. Any other name
    (GitHub_Community, ai_ready_repo, SwarmAI, PhysicalAI, single-token names)
    returns None → NO auto-derive → falls back to manual ∪ Strategy-1.

    Fail-closed by construction: an allowlisted-suffix match is the ONLY path to
    a derived prefix. A wrong domain still can't produce a phantom path because
    _watch_paths_for verifies the prefix exists under backend/skills/ before
    attaching (verify-before-attach).
    """
    if not project_name or "_" not in project_name:
        return None
    head, _, suffix = project_name.partition("_")
    head = head.strip().lower()
    if not head or suffix.strip().lower() not in _BUSINESS_PROJECT_SUFFIXES:
        return None
    return f"s_{head}-"


def _watch_paths_for(
    project_name: str, swarmai_root: "Path | None" = None,
) -> list[str]:
    """Return the git-log watch paths for a project: MANUAL ∪ AUTO-DERIVED.

    - MANUAL: explicit entries in _MANUAL_WATCH_PATHS (core / non-convention).
    - AUTO-DERIVED: if `_<x>` convention yields a prefix `s_<x>-` AND at least
      one skill dir with that prefix EXISTS under backend/skills/, attach that
      glob dir(s). Verify-before-attach — a non-existent prefix attaches nothing
      (no noise), so a project without in-repo skills simply has no derived leg
      and falls back to the project-agnostic Strategy-1 commit-grep.

    Replaces the old `project in _SOURCE_WATCH_PATHS` / `_SOURCE_WATCH_PATHS[p]`
    lookups. Empty list = not watched via Strategy-2 (Strategy-1 still covers it).
    """
    paths: list[str] = list(_MANUAL_WATCH_PATHS.get(project_name, []))

    prefix = _derive_skill_prefix(project_name)
    if prefix:
        if swarmai_root is None:
            swarmai_root = _find_swarmai_root() or None
        if swarmai_root:
            skills_dir = Path(swarmai_root) / "backend" / "skills"
            if skills_dir.is_dir():
                for d in sorted(skills_dir.iterdir()):
                    # verify-before-attach: only real skill dirs matching prefix
                    if d.is_dir() and d.name.startswith(prefix):
                        rel = f"backend/skills/{d.name}/"
                        if rel not in paths:
                            paths.append(rel)
    return paths


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
        "llm_refresh": 30.0,  # LLM call can take 10-20s
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
            ("llm_refresh", self._ch_llm_refresh, {
                EventType.TIMER_30MIN,
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
        """Flag DDD docs stale >14 days vs active code commits.

        Git-call batching (cultivation 2s-budget fix): the recent-commit count
        is computed ONCE PER PROJECT, not per-doc-per-path. Previously this ran
        (a) the identical Strategy-1 `--grep` query twice when both TECH.md and
        PRODUCT.md were stale, and (b) Strategy-2 as one `git log` subprocess
        PER watched path in a loop — so a project with 6 watch paths spawned
        6+ git processes, blowing the 2.0s channel budget (CHANNEL_TIMEOUT
        ×17/day). Now: ≤2 git calls per project with stale docs, and Strategy-2
        passes ALL watch paths to a single `git log -- p1 p2 …` (git accepts
        multiple pathspecs). swarmai_root is resolved at most once.
        """
        findings = []
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return findings

        cutoff = datetime.now() - timedelta(days=14)
        gate_mgr = self._get_gate_manager(root)  # Create once, reuse per-finding
        swarmai_root: "Path | bool | None" = None  # resolve lazily, at most once

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue

            # Collect stale DDD docs first — git is only queried if at least one
            # doc is stale, and only ONCE for the whole project.
            stale_docs: list[tuple[str, datetime]] = []
            for ddd_name in ("TECH.md", "PRODUCT.md"):
                ddd_file = project_dir / ddd_name
                if not ddd_file.exists():
                    continue
                mtime = datetime.fromtimestamp(ddd_file.stat().st_mtime)
                if mtime > cutoff:
                    continue
                stale_docs.append((ddd_name, mtime))

            if not stale_docs:
                continue  # no stale docs → no git work for this project

            commit_count = 0
            try:
                # Strategy 1: grep commit messages for project name (one call).
                result = subprocess.run(
                    ["git", "log", "--oneline", "--since=14 days ago",
                     "--grep", project_dir.name, "--", "."],
                    cwd=ws_path, capture_output=True, text=True,
                    timeout=_GIT_TIMEOUT,
                )
                commit_count = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0

                # Strategy 2: watched source paths — ONE git call over ALL paths
                # (was one subprocess per path). Catches commits that don't
                # mention the project name. NOTE: runs against SWARMAI repo.
                if commit_count == 0:
                    if swarmai_root is None:
                        swarmai_root = _find_swarmai_root() or False
                    watch_paths = (
                        _watch_paths_for(project_dir.name, swarmai_root or None)
                        if swarmai_root else []
                    )
                    if swarmai_root and watch_paths:
                        path_result = subprocess.run(
                            ["git", "log", "--oneline", "--since=14 days ago",
                             "--", *watch_paths],
                            cwd=str(swarmai_root),
                            capture_output=True, text=True,
                            timeout=_GIT_TIMEOUT,
                        )
                        commit_count = (
                            len(path_result.stdout.strip().splitlines())
                            if path_result.stdout.strip() else 0
                        )
            except (subprocess.TimeoutExpired, OSError):
                commit_count = 0

            if commit_count > 0:
                for ddd_name, mtime in stale_docs:
                    days_stale = (datetime.now() - mtime).days
                    findings.append(
                        f"DDD-STALE: {project_dir.name}/{ddd_name} "
                        f"({days_stale}d old, {commit_count} recent commits)"
                    )
                    # Gate trigger: staleness detected = file_tracker gate fires
                    # (recorded per stale doc, preserving prior behavior).
                    if gate_mgr:
                        gate_mgr.record_trigger("file_tracker")

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

        # ── Gap #11: Auto-commit after L2 auto-apply ──────────────────────
        if applied_changes:
            try:
                changed_files = list({
                    str(project_dir / c["doc"])
                    for c in applied_changes
                    for project_dir in [root / "Projects" / c["project"]]
                })
                subprocess.run(
                    ["git", "add"] + changed_files,
                    cwd=str(root), capture_output=True, timeout=10,
                )
                msg = f"chore(ddd): auto-apply {len(applied_changes)} mechanical refresh(es)"
                subprocess.run(
                    ["git", "commit", "-m", msg, "--no-verify"],
                    cwd=str(root), capture_output=True, timeout=10,
                )
                logger.info("ddd_orchestrator: auto-committed %d DDD changes", len(applied_changes))
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.debug("ddd_orchestrator: auto-commit skipped: %s", exc)

    # ── Channel 3: DDD→KNOWLEDGE Injection ─────────────────────────────────

    def _ch_inject_knowledge(self, root: Path, ws_path: str) -> list[str]:
        """Inject or update Active Projects & DDD section in KNOWLEDGE.md."""
        projects_dir = root / "Projects"
        knowledge_path = root / ".context" / "KNOWLEDGE.md"
        if not projects_dir.is_dir() or not knowledge_path.exists():
            return []

        # Build DDD summary via THE single-source line builder (run_99b70b3c R25):
        # context_health_hook._refresh_knowledge_projects_section writes the SAME
        # section on Projects/ mtime change and MUST produce byte-identical lines,
        # else the two live writers clobber/churn each other every cycle. Passing
        # freshness=None makes the helper COMPUTE the "(updated …)" suffix itself, so
        # this channel and the health-hook emit identical lines (suffix + tag +
        # structure markers all from the helper — no divergent format here).
        try:
            from core.ddd_bindings import describe_project_ddd_line
        except Exception:  # pragma: no cover - defensive import
            describe_project_ddd_line = None  # type: ignore[assignment]
        if describe_project_ddd_line is None:
            return []

        lines = ["### Active Projects & DDD\n", "\n"]
        found_any = False
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            try:
                line = describe_project_ddd_line(d, freshness=None)
            except Exception:
                line = None
            if line:
                lines.append(line + "\n")
                found_any = True

        if not found_any:
            return []

        lines.append("\n")
        new_section = "".join(lines)

        # Under the shared KNOWLEDGE.md.lock: this DDD-section writer must
        # serialize with the context_health_hook writers (index refresh, Active
        # Projects refresh, decay reclaim) — all KNOWLEDGE.md writers hold the
        # same lock, else this read-modify-write clobbers a concurrent strip
        # (run_a1ec08e7). Blocking + idempotent (byte-identical section builder).
        from utils.file_lock import md_lock
        with md_lock(knowledge_path, blocking=True):
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
        from datetime import date as _date
        from core.ddd_entry_lifecycle import (
            archive_entries,
            assess_decay,
            inject_entry_metadata,
            parse_entries,
            reclaim_noise_entries,
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
            imp_path = ddd_path(project_dir, "IMPROVEMENT.md")
            if not imp_path.is_file():
                continue

            lock_fd = None
            try:
                # Advisory file lock to prevent concurrent writes (F5 fix)
                # C1 fix: use try/finally around entire open+lock sequence
                # Lock MUST be co-located with the file it guards — imp_path now
                # resolves under 2-understanding/ (six-section tree), so the lock
                # lives beside it, not at a hardcoded root path (else the lock and
                # the file it protects diverge → the guard is void).
                lock_path = imp_path.with_name(".IMPROVEMENT.md.lock")
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
                    # F1 prose-bump REMOVED (R2-prime, run_e50621b6). Bumping ref
                    # from DailyActivity prose-substring matches was a TOXIC fake
                    # signal (generic-titled entries gamed it → undeserved decay
                    # protection). The honest ref producer is
                    # memory_decay.bump_entry_references (real entry-IDs cited in
                    # session messages). ref_count + the decay HIGH_REF branch are
                    # KEPT — only this prose producer is removed. NOTE: this call
                    # site passed graph_path but NOT context_files, so it never
                    # triggered the G1 graph auto-extraction (which is gated on
                    # context_files) — removing it loses no graph behavior.
                    #
                    # ACCESS-DECAY bump (run_644bfea6): the HONEST usage signal
                    # for DDD entries. recall records which entries it actually
                    # surfaced into .ddd-usage.json (keyed by content anchor);
                    # here we bump each matching entry's last_referenced to its
                    # recorded hit date BEFORE assess_decay reads last_referenced
                    # (ddd_entry_lifecycle.py:695). This keeps genuinely-used
                    # lessons alive instead of decaying them on age alone.
                    # Anchor MUST use the same normalizer as the write side.
                    # best-effort: any failure leaves age-only decay intact.
                    try:
                        from core.ddd_usage import (
                            entry_anchor_text,
                            load_ddd_usage,
                        )
                        _usage = load_ddd_usage(project_dir.name)
                        if _usage:
                            for _e in entries:
                                # Anchor IS the key (no section — recall and
                                # parse_entries disagree on sub-section names,
                                # so keying on section silently mismatched).
                                _anchor = entry_anchor_text(_e.raw_text)
                                _hit = _usage.get(_anchor) if _anchor else None
                                if _hit and (
                                    _e.last_referenced is None
                                    or _hit > _e.last_referenced
                                ):
                                    _e.last_referenced = _hit
                    except Exception:  # noqa: BLE001 — best-effort usage bump
                        pass
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
                        content = updated  # reclaim operates on the latest content

                    # CLEAN (M0 ②): reclaim stale operational noise — archive
                    # AND physically strip (inject_entry_metadata only annotates,
                    # it never removes, so archived entries would otherwise persist
                    # and keep counting as noise). is_keep_class protects permanent
                    # knowledge (COE/principle/correction/decision/model/ref>=2).
                    reclaim_report = reclaim_noise_entries(
                        content, today, project_dir,
                        source_path=imp_path, dry_run=False,
                    )
                    if reclaim_report.new_content is not None:
                        # reclaim_noise_entries already wrote imp_path + .bak
                        # (source_path given). Just log.
                        findings.append(
                            f"ENTRY_RECLAIM: {reclaim_report.archived} stale entries "
                            f"archived+stripped from {project_dir.name} "
                            f"({reclaim_report.kept_protected} protected)"
                        )

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

        return findings

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

    # ── Channel 11: LLM-Proposed Refresh (Layer 2) ────────────────────────

    def _ch_llm_refresh(self, root: Path, ws_path: str) -> list[str]:
        """Layer 2: LLM-proposed section diff for stale DDD docs.

        Runs on TIMER_30MIN but internally throttled to max 1x per (project, doc)
        per 7 days. Uses Bedrock Sonnet. Evidence-mandatory prompt with citation
        verification. Auto-applies HIGH/MEDIUM, escalates LOW.

        Budget: ~50K tokens per call, max 3 calls per weekly cycle.
        """
        findings: list[str] = []

        try:
            from core.auto_refresh import (
                LlmRefreshProposer, log_refresh_results, RefreshResult,
            )
            from core.ddd_cultivation import CultivationProposal, write_proposal

            swarmai_root = _find_swarmai_root()
            if not swarmai_root:
                return findings

            proposer = LlmRefreshProposer(swarmai_root, Path(root))
            projects_dir = Path(root) / "Projects"
            if not projects_dir.is_dir():
                return findings

            # First: check staleness (same logic as _ch_ddd_staleness, but we
            # only act on projects that are ALREADY stale + throttle allows)
            proposals_generated = 0
            max_proposals_per_cycle = 3

            for project_dir in sorted(projects_dir.iterdir()):
                if not project_dir.is_dir():
                    continue
                if proposals_generated >= max_proposals_per_cycle:
                    break

                project_name = project_dir.name

                for doc_name in ("TECH.md", "PRODUCT.md"):
                    if proposals_generated >= max_proposals_per_cycle:
                        break

                    doc_path = project_dir / doc_name
                    if not doc_path.exists():
                        continue

                    # Throttle check
                    if not proposer.should_run(project_name, doc_name):
                        continue

                    # Check if actually stale (>14 days + recent commits)
                    if not self._is_doc_stale_with_commits(
                        project_dir, doc_path, swarmai_root, ws_path
                    ):
                        continue

                    # Get recent commits for evidence
                    commits = self._get_recent_commits_for_project(
                        project_name, swarmai_root
                    )
                    if not commits:
                        continue

                    # Extract the main content section (first 3000 chars after frontmatter)
                    try:
                        content = doc_path.read_text(encoding="utf-8")
                        # Skip maturity annotations at top
                        section = self._extract_main_section(content)
                    except (OSError, UnicodeDecodeError):
                        continue

                    # Get source excerpts from watched paths
                    source_excerpts = self._get_source_excerpts(
                        project_name, swarmai_root
                    )

                    # Generate proposal
                    proposal = proposer.generate_proposal(
                        project=project_name,
                        doc_name=doc_name,
                        section_name=self._guess_section_name(content),
                        current_section=section,
                        recent_commits=commits,
                        source_excerpts=source_excerpts,
                    )

                    if proposal is None:
                        continue

                    # Record that we ran (throttle update)
                    proposer.record_run(project_name, doc_name)
                    proposals_generated += 1

                    # Route by confidence
                    if proposal.confidence >= 0.8:
                        # HIGH: auto-apply
                        applied = self._apply_llm_proposal(
                            doc_path, proposal, root
                        )
                        if applied:
                            findings.append(
                                f"AUTO-REFRESH-L2: APPLIED {project_name}/{doc_name} "
                                f"§{proposal.section_name} (confidence={proposal.confidence:.2f})"
                            )
                    elif proposal.confidence >= 0.5:
                        # MEDIUM: auto-apply with log for review
                        applied = self._apply_llm_proposal(
                            doc_path, proposal, root
                        )
                        if applied:
                            findings.append(
                                f"AUTO-REFRESH-L2: APPLIED (REVIEW) {project_name}/{doc_name} "
                                f"§{proposal.section_name} (confidence={proposal.confidence:.2f})"
                            )
                    else:
                        # LOW: escalate via proposal system
                        escalation = CultivationProposal(
                            target_doc=doc_name,
                            target_section=proposal.section_name,
                            content=f"[Layer 2 LLM refresh] {proposal.proposed_text[:200]}...",
                            source_run_id="auto_refresh_l2",
                            confidence=proposal.confidence,
                        )
                        write_proposal(escalation, project_dir)
                        findings.append(
                            f"AUTO-REFRESH-L2: ESCALATED {project_name}/{doc_name} "
                            f"§{proposal.section_name} (confidence={proposal.confidence:.2f})"
                        )
                        # Gap #10: Auto-create Radar Todo for escalated proposals
                        try:
                            from core.proactive_intelligence import _create_health_todo
                            _create_health_todo(
                                f"DDD Escalation: {project_name}/{doc_name} §{proposal.section_name} "
                                f"needs review (confidence={proposal.confidence:.2f})",
                                severity="warning",
                            )
                        except Exception:
                            pass  # Non-critical — todo creation is best-effort

            if proposals_generated > 0:
                logger.info(
                    "ddd_orchestrator.llm_refresh: generated %d proposals",
                    proposals_generated,
                )

        except Exception as exc:
            logger.warning("ddd_orchestrator.llm_refresh failed: %s", exc)
            findings.append(f"CHANNEL_ERROR: llm_refresh — {type(exc).__name__}: {exc}")

        return findings

    def _is_doc_stale_with_commits(
        self, project_dir: Path, doc_path: Path, swarmai_root: Path, ws_path: str,
    ) -> bool:
        """Check if doc is >14 days old AND has recent code commits."""
        from datetime import datetime, timedelta

        mtime = datetime.fromtimestamp(doc_path.stat().st_mtime)
        if mtime > datetime.now() - timedelta(days=14):
            return False

        # Check watched paths for this project
        project_name = project_dir.name
        for watch_path in _watch_paths_for(project_name, swarmai_root):
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "--since=14 days ago",
                     "--", watch_path],
                    cwd=str(swarmai_root),
                    capture_output=True, text=True,
                    timeout=_GIT_TIMEOUT,
                )
                if result.stdout.strip():
                    return True
            except (subprocess.TimeoutExpired, OSError):
                pass

        # Fallback: check workspace commits mentioning project name
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--since=14 days ago",
                 "--grep", project_name, "--", "."],
                cwd=ws_path, capture_output=True, text=True,
                timeout=_GIT_TIMEOUT,
            )
            return bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _get_recent_commits_for_project(
        self, project_name: str, swarmai_root: Path,
    ) -> list[str]:
        """Get recent commit subjects for a project's watched paths."""
        commits: list[str] = []
        for watch_path in _watch_paths_for(project_name, swarmai_root):
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "--since=14 days ago",
                     "--max-count=10", "--", watch_path],
                    cwd=str(swarmai_root),
                    capture_output=True, text=True,
                    timeout=_GIT_TIMEOUT,
                )
                if result.stdout.strip():
                    commits.extend(result.stdout.strip().splitlines())
            except (subprocess.TimeoutExpired, OSError):
                pass
        # Deduplicate (same commit may appear in multiple paths)
        seen = set()
        unique = []
        for c in commits:
            h = c.split()[0] if c else ""
            if h and h not in seen:
                seen.add(h)
                unique.append(c)
        return unique[:10]

    def _get_source_excerpts(self, project_name: str, swarmai_root: Path) -> str:
        """Get key source file excerpts for LLM context (max 3000 chars)."""
        excerpts: list[str] = []
        max_total = 3000

        _wp = _watch_paths_for(project_name, swarmai_root)
        if not _wp:
            return ""

        for watch_path in _wp[:3]:
            full_path = swarmai_root / watch_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8")[:1500]
                    excerpts.append(f"--- {watch_path} ---\n{content}\n")
                except (OSError, UnicodeDecodeError):
                    pass
            elif full_path.is_dir():
                # For directories, list files
                try:
                    files = sorted(f.name for f in full_path.iterdir() if f.suffix == ".md")
                    excerpts.append(f"--- {watch_path} (files) ---\n" + "\n".join(files) + "\n")
                except OSError:
                    pass

            if sum(len(e) for e in excerpts) > max_total:
                break

        return "\n".join(excerpts)[:max_total]

    def _extract_main_section(self, content: str) -> str:
        """Extract main body (skip frontmatter/maturity annotations)."""
        lines = content.splitlines()
        # Skip lines until first ## heading
        start = 0
        for i, line in enumerate(lines):
            if line.startswith("## ") and "maturity:" not in line:
                start = i
                break
        return "\n".join(lines[start:start + 80])  # ~80 lines = ~3000 chars

    def _guess_section_name(self, content: str) -> str:
        """Guess the primary section name from first ## heading."""
        for line in content.splitlines():
            if line.startswith("## ") and "maturity:" not in line:
                return line.lstrip("# ").strip()
        return "Main"

    def _apply_llm_proposal(
        self, doc_path: Path, proposal, root: Path,
    ) -> bool:
        """Apply LLM proposal to the DDD doc. Returns True if applied.

        Safety guards:
        - Length sanity: if proposed text differs >50% in length from current, skip
          (prevents truncation mismatch from garbling docs)
        - Exact match required: current_text must appear verbatim in doc
        - Atomic write: write to .tmp then os.replace
        """
        from core.auto_refresh import RefreshResult, log_refresh_results

        try:
            import fcntl

            # Length sanity check (pre-lock — no file I/O needed)
            current_len = len(proposal.current_text)
            proposed_len = len(proposal.proposed_text)
            if current_len > 0 and abs(proposed_len - current_len) / current_len > 0.5:
                logger.warning(
                    "auto_refresh.L2: length mismatch (current=%d, proposed=%d) "
                    "— skipping to prevent garble. File: %s",
                    current_len, proposed_len, doc_path.name,
                )
                return False

            # Read-modify-write under sidecar lock (consistent with _auto_apply_ddd_proposals)
            lock_path = doc_path.with_suffix(doc_path.suffix + ".lock")
            lock_fd = open(lock_path, "w")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                content = doc_path.read_text(encoding="utf-8")

                # Exact match replacement
                if proposal.current_text not in content:
                    logger.debug(
                        "auto_refresh.L2: current_text not found in %s — skipping",
                        doc_path.name,
                    )
                    return False

                new_content = content.replace(
                    proposal.current_text, proposal.proposed_text, 1
                )
                # Atomic write under lock
                tmp_path = doc_path.with_suffix(".tmp")
                tmp_path.write_text(new_content, encoding="utf-8")
                os.replace(tmp_path, doc_path)
            finally:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
                lock_fd.close()

            # Success path only (after lock released)
            result = RefreshResult(
                target_file=str(doc_path.relative_to(root)),
                old_value=proposal.current_text[:80] + "...",
                new_value=proposal.proposed_text[:80] + "...",
                evidence=f"LLM refresh, {len(proposal.citations)} citations verified",
                layer=2,
                applied=True,
                confidence=proposal.confidence,
            )
            log_path = Path(root) / ".context" / ".auto_refresh_log.jsonl"
            log_refresh_results([result], log_path)

            logger.info(
                "auto_refresh.L2: applied to %s (confidence=%.2f)",
                doc_path.name, proposal.confidence,
            )
            return True
        except Exception as exc:
            logger.warning("auto_refresh.L2: apply failed for %s: %s", doc_path.name, exc)
            return False
