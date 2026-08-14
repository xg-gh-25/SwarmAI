"""DDD Cultivation Orchestrator — self-contained DDD feed engine.

Orchestrates several independent DDD feed channels. Each channel runs in its own
try/except — one crash never affects others. Returns merged findings list.

All channel logic lives HERE — no delegation back to context_health_hook.
context_health_hook calls orchestrator.run(), not the other way around.

Channels (see self.channels in __init__ for the live set):
    - DDD staleness detection
    - DDD→KNOWLEDGE injection
    - Knowledge staleness detection
    - Entity index validation
    - Signal→DDD bridge (hooks.signal_ddd_bridge)
    - Code Intelligence drift (core.code_intel_feed)
    - Entry lifecycle (per-entry decay/reclaim)

Note: the auto_refresh channels (mechanical/memory/llm content-rewrite) were
REMOVED (run_781ffbd9) — zero output in production + superseded by R30#4.

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

# Per-DDD refresh-anchor store (gitignored via `Projects/*` — never committed except
# the SwarmAI project). Records the upstream source commit the DDD was last VERIFIED
# against, so source-drift is detectable without a headless clone / autonomous job.
_REFRESH_STATE_FILENAME = ".refresh_state.json"
_SOURCE_ANCHOR_KEY = "source_anchor_commit"


def _read_source_anchor(project_dir: Path) -> "str | None":
    """Read the last-verified upstream source commit for a DDD project.

    Fail-safe: returns None on missing file / unreadable / malformed JSON / missing
    key — the source-drift check treats None as "no anchor → skip", never an error.
    """
    try:
        raw = (Path(project_dir) / _REFRESH_STATE_FILENAME).read_text(encoding="utf-8")
        val = json.loads(raw).get(_SOURCE_ANCHOR_KEY)
        return val if isinstance(val, str) and val else None
    except (OSError, ValueError, TypeError):
        return None


def write_source_anchor(project_dir: Path, commit: str) -> None:
    """Persist the upstream source commit the DDD is now verified against.

    Merge-writes into ``.refresh_state.json`` (preserves any other keys). Public —
    called by the human/REFRESHER re-verify step (and tests) to CLEAR a drift signal.
    Best-effort: swallows OSError (an unwritable anchor must never crash cultivation).
    """
    p = Path(project_dir) / _REFRESH_STATE_FILENAME
    try:
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
        data[_SOURCE_ANCHOR_KEY] = commit
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _upstream_source_head(project_dir: Path) -> "tuple[str, str] | None":
    """Resolve a DDD's bound upstream source repo and read its current git HEAD.

    Reads ``bindings.yaml`` RAW (``yaml.safe_load``) rather than via
    ``ddd_bindings.load_bindings`` — the strict ``BindingsDoc`` schema parses only the
    ``bindings:`` array and drops ``governed_assets`` (pydantic ``extra='ignore'``),
    and it has ~99 callers we must not perturb for one optional read-only field
    (R27). We look for the first ``governed_assets[*]`` entry of ``kind: data-source``
    (or ``code-repo``) that declares a ``source_workspace`` local path, then run
    ``git rev-parse HEAD`` there.

    Returns ``(source_label, head7)`` or ``None`` on ANY failure — no bindings file,
    no declared source_workspace, path absent, not a git repo, git error/timeout,
    non-UTF8 output. READ-ONLY (never writes the upstream repo); the only external
    call is a timeout-bounded ``git rev-parse``.
    """
    try:
        import yaml

        bindings = Path(project_dir) / "bindings.yaml"
        if not bindings.exists():
            return None
        doc = yaml.safe_load(bindings.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return None
        assets = doc.get("governed_assets")
        if not isinstance(assets, list):
            return None
        source_ws = None
        label = "source"
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if asset.get("kind") in ("data-source", "code-repo") and asset.get("source_workspace"):
                source_ws = str(asset["source_workspace"])
                label = str(asset.get("name") or asset.get("kind") or "source")
                break
        if not source_ws:
            return None
        ws = Path(source_ws)
        if not (ws / ".git").exists():
            return None
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ws), capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        head = result.stdout.strip()
        if not head:
            return None
        return (label, head[:7].lower())
    except (OSError, ValueError, UnicodeDecodeError, subprocess.SubprocessError, yaml.YAMLError):
        return None

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
        "knowledge_staleness": 1.0,
        "signal_ddd_bridge": 3.0,
        "code_intel_drift": 5.0,
        "entry_lifecycle": 3.0,
        # mechanical_refresh/memory_refresh/llm_refresh budgets REMOVED (run_781ffbd9)
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
            ("knowledge_staleness", self._ch_knowledge_staleness, {
                EventType.GIT_COMMIT, EventType.TIMER_30MIN,
            }),
            ("signal_ddd_bridge", self._ch_signal_bridge, {
                EventType.SIGNAL_DIGEST,
            }),
            # Drift detection is now a HEALTH signal (run_8d5fe9d1, Admission Component A):
            # it writes code_drift_health.json, no longer proposals. Its old trigger
            # (CODE_INTEL_INDEXED, emitted by detect_tech_drift itself) is gone with the
            # proposal path, so run it on the health cadence (TIMER_30MIN) like the other
            # pull-only health channels — otherwise the handler is orphaned + drift stops.
            ("code_intel_drift", self._ch_code_intel, {
                EventType.TIMER_30MIN,
            }),
            ("entry_lifecycle", self._ch_entry_lifecycle, {
                EventType.TIMER_30MIN, EventType.SESSION_CLOSE,
            }),
            # mechanical_refresh / memory_refresh / llm_refresh channels REMOVED
            # (run_781ffbd9): the auto_refresh module produced zero output in 2
            # months (0 refresh-log written, Layer-2 0 proposals) and its target
            # class (stored drift-numbers) is banned upstream by R30#4. Module deleted.
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
                # Priority mapping: signal/code_intel/auto_apply = 1, staleness = 2, knowledge = 3
                if name in ("auto_apply_proposals", "signal_ddd_bridge", "code_intel_drift"):
                    priority = 1
                elif name in ("ddd_staleness",):
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
                ddd_file = ddd_path(project_dir, ddd_name)  # six-section strangler
                if not ddd_file.exists():
                    continue
                mtime = datetime.fromtimestamp(ddd_file.stat().st_mtime)
                if mtime > cutoff:
                    continue
                stale_docs.append((ddd_name, mtime))

            # Source-anchor drift (mtime-INDEPENDENT — runs BEFORE the stale_docs
            # continue-guard, else it would only fire for docs that are ALSO
            # mtime-stale, i.e. never for a freshly-refreshed DDD like the one this
            # was built for). Fires when a bound upstream source repo's HEAD has
            # moved past the last-verified anchor. Fully fail-safe: any error →
            # zero findings, no raise (never block cultivation on an unreachable
            # or anchorless source — the common case on CI / another machine).
            try:
                _src_head = _upstream_source_head(project_dir)
                _anchor = _read_source_anchor(project_dir)
                # Compare case-insensitively: git SHAs are lowercase, but a
                # hand/tool-stored anchor may be uppercase → normalize both to
                # avoid a false-drift that never clears (Gate-2 F1).
                if _src_head and _anchor and _src_head[1] != _anchor[:7].lower():
                    findings.append(
                        f"DDD-SOURCE-DRIFT: {project_dir.name} upstream "
                        f"{_src_head[0]} moved ({_anchor[:7]}->{_src_head[1]}), "
                        f"re-verify TECH.md"
                    )
            except Exception:
                pass  # fail-safe: source-drift check must never break cultivation

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
                # Admission Component D (AC7, run_8d5fe9d1): CLOSE the calibration loop —
                # consume the freshly-computed stats' self-correction recommendations
                # (previously dead code). project_dir = artifacts_dir.parent.
                from core.ddd_cultivation import apply_channel_self_corrections
                apply_channel_self_corrections(artifacts_dir.parent)
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

                        # Shared doc-write lock via md_lock (run_06350217): the SAME
                        # <doc>.md.lock name every other writer of these docs uses,
                        # so this mechanical writeback mutually excludes concurrent
                        # apply_to_ddd / decay / retire (was hand-rolled flock_exclusive
                        # — correct name already, but re-derived; md_lock owns it now).
                        from utils.file_lock import md_lock
                        for ddd_name in ("TECH.md", "IMPROVEMENT.md", "PRODUCT.md"):
                            # Resolve via the six-section resolver (strangler): a
                            # migrated DDD keeps its docs under 2-understanding/, so a
                            # a bare root read (project_dir joined with the doc name) would miss it and
                            # mechanical writeback would silently stop applying
                            # (run_3a636c88). NOTE: local var is `doc_path` — must NOT
                            # shadow the imported `ddd_path` FUNCTION (it was `ddd_path`
                            # before, a latent TypeError trap + the root-path bug).
                            doc_path = ddd_path(project_dir, ddd_name)
                            if not doc_path.exists():
                                continue
                            with md_lock(doc_path, blocking=True):
                                ddd_content = doc_path.read_text(encoding="utf-8")
                                if current_block in ddd_content:
                                    new_content = ddd_content.replace(
                                        current_block, proposed_block, 1
                                    )
                                    doc_path.write_text(new_content, encoding="utf-8")
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
        Uses the shared md_lock (utils.file_lock) advisory lock — the SAME
        <doc>.md.lock name every DDD-doc writer uses — to prevent concurrent
        read-modify-write (run_06350217).
        """
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

            try:
                # Shared doc-write lock (run_06350217): lock on the SAME
                # <doc>.md.lock name every other IMPROVEMENT.md writer uses
                # (apply_to_ddd, auto-apply, llm-apply, retire) via md_lock — the
                # old hand-rolled `.IMPROVEMENT.md.lock` name diverged from theirs,
                # so this decay strip did NOT mutually exclude a concurrent append
                # → lost-update race. md_lock is co-located (<doc>.md.lock), cross-
                # platform, non-blocking here (skip the project if another writer
                # holds it), and never unlinks (inode-race safe, run_24d9f714).
                from utils.file_lock import md_lock
                with md_lock(imp_path, blocking=False) as _got:
                    if not _got:
                        continue  # another writer holds this doc — skip this project

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

                            # Archive entries that transitioned to archived state.
                            # source_path=imp_path so the archive lands NEXT TO the
                            # resolved live doc (2-understanding/), not the raw root
                            # — the split-brain that grew a 17MB orphan (run_f71e5920).
                            if to_archive:
                                archive_entries(project_dir, to_archive,
                                                source_path=imp_path)

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
                            # reclaim_noise_entries already wrote imp_path
                            # (source_path given); recovery is archive + git, no
                            # .bak (6463e1ab). Just log.
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
            # md_lock (above) released + closed the fd on with-exit; no manual
            # finally needed (run_06350217 — was a hand-rolled fcntl LOCK_UN).

        return findings

