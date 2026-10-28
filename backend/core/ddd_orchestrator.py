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
from core.project_registry import DEFAULT_PROJECT_NAME

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
    returns None. Used by recall_multi to scope a business project's skills.

    Fail-closed by construction: an allowlisted-suffix match is the ONLY path to
    a derived prefix.
    """
    if not project_name or "_" not in project_name:
        return None
    head, _, suffix = project_name.partition("_")
    head = head.strip().lower()
    if not head or suffix.strip().lower() not in _BUSINESS_PROJECT_SUFFIXES:
        return None
    return f"s_{head}-"


class DddCultivationOrchestrator:
    """Orchestrates DDD Cultivation feed channels with fault isolation.

    Each channel is a (name, callable) pair. The callable receives (root, ws_path)
    and returns a list of findings (strings). If a channel raises, the error is
    logged and captured as a finding — other channels continue unaffected.
    """

    # Per-channel budget (seconds) for event-driven execution
    _CHANNEL_BUDGETS: dict[str, float] = {
        "ddd_staleness": 2.0,
        "knowledge_staleness": 1.0,
        "signal_ddd_bridge": 3.0,
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
            ("signal_ddd_bridge", self._ch_signal_bridge, {
                EventType.SIGNAL_DIGEST,
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
                # Priority mapping: signal = 1, staleness = 2, knowledge = 3
                if name in ("signal_ddd_bridge",):
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

    def _find_artifacts_dir(self, root: Path, project: str = DEFAULT_PROJECT_NAME) -> Path | None:
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
        multiple pathspecs).
        """
        findings = []
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return findings

        cutoff = datetime.now() - timedelta(days=14)
        gate_mgr = self._get_gate_manager(root)  # Create once, reuse per-finding

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

                # Strategy 2: commits in the repos this project's bindings.yaml
                # declares (usable-by-anyone P7: no project or repo layout is special;
                # it replaced SwarmAI-checkout watch lists). One git call per repo.
                if commit_count == 0:
                    from core.ddd_bindings import governed_repo_roots
                    for repo in governed_repo_roots(project_dir):
                        path_result = subprocess.run(
                            ["git", "log", "--oneline", "--since=14 days ago"],
                            cwd=str(repo), capture_output=True, text=True,
                            timeout=_GIT_TIMEOUT,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                        )
                        commit_count += (
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

