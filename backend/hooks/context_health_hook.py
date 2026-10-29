"""Context Health Harness — keeps SwarmAI's brain accurate and current.

Single hook, two modes:
- **Light** (every session): refresh the KNOWLEDGE.md index
  if workspace changed since last refresh.
  (PROJECTS.md in-prompt index was removed 2026-08-14.)
- **Deep** (once per day): validate all 11 context files, check MEMORY.md
  accuracy vs git, detect DDD staleness, verify git health.

All checks are filesystem + Bedrock embedding (delta-sync).  Auto-fixes
what it can, logs what it can't.  Heavy work runs in a thread pool to
avoid blocking the asyncio event loop.  Budget: <3s light, <10s deep.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core import executors
from core.context_directory_loader import (
    EFFECTIVE_1M_BUDGET as _CDL_EFFECTIVE_1M_BUDGET,
    EMERGENCY_MARGIN as _CDL_EMERGENCY_MARGIN,
)  # budget-threshold SoT (run_72ca2a97) — derived, not hand-typed
from core.initialization_manager import initialization_manager
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)
from core.session_hooks import HookContext
from core.project_registry import DEFAULT_PROJECT_NAME

logger = logging.getLogger(__name__)


# NOTE: the former module-level `_is_cjk_like` CJK detector was REMOVED in
# run_3f25a73a. It re-implemented CJK detection with a range that DIVERGED from
# ContextDirectoryLoader._CJK_RE (it had Hangul but not Kana; the loader had Kana
# but not Hangul — Gate-1 finding C). _check_token_budget now calls the canonical
# estimate_tokens, which owns the single unified CJK range. Do NOT reintroduce a
# second detector here — it WILL drift.


# Pipeline-internal decision prefixes to filter before DDD cultivation.
# These are pipeline validator output, not user decisions.
# Keep: "user override:", "standing rule:", architecture decisions.
_DECISION_NOISE_PREFIXES = (
    "→ Recommend:", "├─", "publish --validate",
    "advance →", "run-", "0/", "1/", "2/", "3/", "4/", "5/",
)


# OS-Eval canary runtime state under .context/ (written by _run_eval_canary).
# Session tier → _CANARY_STATE_FILE (contract unchanged); daily tier →
# _CANARY_DAILY_STATE_FILE (+ its md_lock sidecar ``<name>.lock``), kept separate
# so a session-tier run never overwrites the daily result.
_CANARY_STATE_FILE = ".eval-canary.json"
_CANARY_DAILY_STATE_FILE = ".eval-canary-daily.json"
_CANARY_DAYS_KEPT = 30  # per-day quick-check record in the daily state (``days``)
_CANARY_STATE_PREFIX = ".eval-canary"
# Daily tier: per-case cap = the manual-run cap in eval_runner.eval_canary_pass.
_CANARY_DAILY_CASE_TIMEOUT = 20
# runtime_health: eval_runner.eval_runtime_health caps at 30s with a 15s floor.
_CANARY_RUNTIME_HEALTH_CAP = 30
_CANARY_RUNTIME_HEALTH_FLOOR = 15
# Budget timeouts tolerated per case per day before it is recorded as failed.
_CANARY_DAILY_MAX_DEFERRALS = 3
# Daily tier: don't start another case with less than this much hook budget left.
_CANARY_DAILY_MIN_CASE_BUDGET = 3.0


def _is_canary_state_path(path: str) -> bool:
    """True if a (porcelain) path names canary runtime state, not a context file."""
    name = path.strip().strip('"').rstrip("/").rsplit("/", 1)[-1]
    return name.startswith(_CANARY_STATE_PREFIX)


def _is_runtime_state_path(path: str) -> bool:
    """True if a (porcelain) .context path is machine-written runtime state: a
    dotfile (.code_change_feed_last_commit.txt, .eval-*, .memory-usage*,
    .evolution_*, .session_cultivated.json …) rewritten by hooks and jobs. The
    context files themselves (MEMORY.md, AGENT.md …) never start with a dot. Being
    dirty is expected for these, not a "forgot to commit" signal (65 such
    UNCOMMITTED warnings by 2026-10-21)."""
    name = path.strip().strip('"').rstrip("/").rsplit("/", 1)[-1]
    return name.startswith(".")


# Episodic war-story detector — the 7th MEMORY admission gate (run_117bcdf4).
#
# ROOT CAUSE it closes: a REFLECT lesson whose BODY narrates a single-run event
# ("Gate-2 caught X", "5th consecutive C042 catch this session", "GUI122
# RECURRED this run", "又抓到 …") is EPISODIC — it records what happened in one
# pipeline run, not a reusable rule. It passed all 6 prior gates (it is a
# well-formed, long, non-governance guideline) and got auto-sunk into the MEMORY
# Guidelines/Pitfalls HOT PATH, where 92 such entries accumulated before the
# 2026-07-28 decay-archive sweep. The full lesson still lands in IMPROVEMENT.md /
# run.json — this gate ONLY keeps the run-narration out of the injected hot path
# (Principle 1: sediment preserved, only the judgment-substrate is gated).
#
# DISCRIMINATOR = a gate-ACTOR token CO-OCCURRING with a run-EVENT verb near the
# START — NOT a bare topic token (Gate-2 fresh-context adversarial CRITICAL,
# run_117bcdf4). The first cut anchored on the topic token alone (`Gate-[012]\b`)
# and silently DROPPED genuine rules ABOUT the gating system — "Gate-2 must always
# run before merge", "adversarial gate design should assume the author is wrong",
# "M3 skeptic passes must precede any code write" — exactly the class a
# self-improving pipeline most needs to keep. The fix: an actor ("Gate-N",
# "adversarial", "M3 skeptic", "GUIxxx", an Nth-catch ordinal) is episodic ONLY
# when it co-occurs with a narration VERB (caught/blocked/reframed/flipped/killed/
# RECURRED/vindicated/flagged/…). A rule about gates ("must run", "should assume",
# "is cheaper") has the actor but NO event verb → correctly ADMITTED. The verb may
# PRECEDE the actor (passive "Caught by Gate-2", leading run-id "In run_x, Gate-2
# caught …") — so we scan a bounded HEAD window for actor×verb co-occurrence in
# either order, not a strict opener. Still NOT a per-phrasing denylist (PIT40):
# the actor×verb co-occurrence is the invariant; the verb list is a small closed
# set of run-event verbs. The window is bounded (~140 chars) so a later-sentence
# incidental "caught" in a long semantic rule can't retro-flag it.
_EPISODIC_ACTOR = (
    r"Gate-\d\b"                                              # Gate-0..9
    r"|M\d+\s+skeptic\b"                                      # M3 skeptic
    r"|adversarial\b"                                         # adversarial (gate/review)
    r"|(?:GUI|PIT|COE|DEC|COR)\d+\b"                          # GUI122 / PIT40 …
    r"|\d+(?:st|nd|rd|th)\s+(?:consecutive\s+)?\w*\s*catch\b" # 5th consecutive C042 catch
)
_EPISODIC_VERB = (
    r"caught|catch\b|blocked|block\b|reframed|flipped|killed|vindicated"
    r"|flagged|RECURRED|earned its keep|corrected|refuted|overturned|reopened"
)
_EPISODIC_HEAD = 140
_EPISODIC_AV_RE = re.compile(rf"(?:{_EPISODIC_ACTOR}).{{0,60}}?(?:{_EPISODIC_VERB})", re.I)   # actor → verb
_EPISODIC_VA_RE = re.compile(rf"(?:{_EPISODIC_VERB}).{{0,20}}?(?:{_EPISODIC_ACTOR})", re.I)   # verb → actor (passive)
# CJK war-story: an actor/deixis token co-occurring with a caught/recurred verb.
# CJK carries the event directly (抓到/拦住/复发), so require that verb near an
# actor or run-deixis (Gate-N / adversarial / skeptic / 又 / 本轮 / 这次 / 第N次).
_EPISODIC_CJK_RE = re.compile(
    r"(?:Gate-\d|M\d+\s*skeptic|adversarial|又|本轮|这次|这轮|第.{1,3}次)"
    r".{0,12}?(?:抓到|抓住|拦住|挡住|复发|又犯|命中)"
    r"|(?:抓到|抓住|拦住|挡住|复发).{0,12}?(?:Gate-\d|adversarial|skeptic)",
    re.I,
)


def _is_episodic_warstory(lesson: str) -> bool:
    """True if `lesson` narrates a single-run gate EVENT (a war-story), so it must
    be HELD BACK from the MEMORY hot path (it still lands in IMPROVEMENT.md).

    Discriminator = a gate ACTOR (Gate-N / adversarial / M-N skeptic / GUIxxx /
    Nth-catch) CO-OCCURRING with a run-event VERB (caught/blocked/RECURRED/…)
    within a bounded head window. A rule ABOUT the gating system ("Gate-2 must
    always run", "adversarial gate design should…") has the actor but no event
    verb → returns False (admitted). Verb may precede the actor (passive / leading
    run-id). NOT a per-phrasing denylist — actor×verb co-occurrence is the
    invariant (PIT40).
    """
    if not lesson:
        return False
    head = lesson[:_EPISODIC_HEAD]
    return bool(
        _EPISODIC_AV_RE.search(head)
        or _EPISODIC_VA_RE.search(head)
        or _EPISODIC_CJK_RE.search(head)
    )


class ContextHealthHook:
    """Unified context health harness.

    Registered AFTER auto-commit so it sees committed state.
    Runs light refresh every session, deep check once per calendar day.
    """

    name = "context_health"

    # Git timeout matches auto_commit_hook
    _GIT_TIMEOUT = 10

    def __init__(self) -> None:
        self._last_deep_date: Optional[str] = None
        # Track last refresh git rev to skip no-op refreshes.
        # Dirty flag: set by _light_refresh when cultivation writes to DDD docs.
        # (The old PROJECTS.md in-prompt index refresh was removed 2026-08-14.)
        # Token budget measurement (populated by _check_token_budget in deep check)
        self._token_measurement: dict = {}

    async def execute(self, context: HookContext) -> None:
        ws_path = initialization_manager.get_cached_workspace_path()
        if not ws_path:
            return

        root = Path(ws_path)
        if not root.is_dir():
            return

        # Both _light_refresh and _deep_check are sync-heavy: git
        # subprocesses (5-10s timeouts each), Bedrock embedding calls
        # (3s timeout per chunk), file I/O.  Run on the dedicated 'subprocess'
        # pool (NOT the default pool) so a slow refresh never starves the event
        # loop's /health scheduling (COE run_b36c7880, run_d72047b0).
        # ── Light: refresh indexes if workspace changed ──────────────
        await executors.run_in("maintenance", self._light_refresh, root, ws_path)

        # ── Deep: once per calendar day ──────────────────────────────
        today = date.today().isoformat()
        if self._last_deep_date != today:
            await executors.run_in("maintenance", self._deep_check, root, ws_path)
            self._last_deep_date = today

    # ------------------------------------------------------------------
    # Light refresh — every session, <2s
    # ------------------------------------------------------------------

    def _light_refresh(self, root: Path, ws_path: str) -> None:
        """Refresh KNOWLEDGE.md index, MEMORY.md index, and FTS5 keyword stores."""
        # PE-4: Shared cultivation deadline (25s total for BOTH passes).
        # BackgroundHookExecutor has 30s timeout — 25s leaves 5s headroom.
        _cultivation_deadline = time.monotonic() + 25.0

        # Whole-refresh wall-clock budget for the HEAVY, un-budgeted index syncs
        # below (the knowledge library). These run in a
        # thread the executor's asyncio timeout CANNOT cancel, so embedding a
        # large changeset (first full index ~100s; Bedrock retries on transient
        # ModelErrorException stretch it further) keeps the thread alive long
        # after the executor's 30s timeout already fired — recording a spurious
        # "Background hook 'context_health' timed out" + a false CONSECUTIVE
        # CRITICAL, while the work silently completes off-budget.
        # Fix: size this budget UNDER the real 30s executor timeout (matches
        # _cultivation_deadline=25s, 5s headroom) so the deferral guards below
        # fire BEFORE the executor cancels — the tail syncs are DELTA
        # (content_hash), so deferring them to the next session is safe and
        # self-healing, and the hook now exits clean instead of "timed out".
        _refresh_deadline = time.monotonic() + 25.0

        # Auto-cultivate session signals — promote corrections (Ch6) and
        # decisions (Ch5) from DailyActivity JSONL into DDD docs.
        try:
            self._auto_cultivate_session_signals(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: session signal cultivation skipped: %s", exc)

        # DDD maturity annotation writes are retired in CDF stage B.

        # Legacy memory-usage tracking writes are retired in CDF stage B.

        # NEW ARCHITECTURE (2026-08-14): the in-prompt MEMORY index was DELETED
        # (live MEMORY is full-injected; recall scans body-BM25). No index to
        # refresh — _refresh_memory_index is gone.

        # DDD skill-registry rebuild runs unconditionally here (like the memory
        # index above) — it must catch uncommitted aim.json domain_skills edits +
        # newly-added DDDs, which otherwise stay invisible to skill discovery until
        # the daemon restarts (build_manifest was startup-only). Self-contained
        # fail-soft (see the method) — never blocks the rest of the refresh.
        try:
            self._refresh_ddd_registry(root)
        except Exception as exc:
            logger.warning("context_health: DDD skill registry refresh failed: %s", exc)

        # DDD JOB-registry rebuild — MUST run AFTER the skill registry above (job
        # records resolve depends_on_skill against the freshly-rebuilt skill
        # manifest). Same unconditional + fail-soft discipline. (J1, run_5ec6b7ad)
        try:
            self._refresh_ddd_job_registry(root)
        except Exception as exc:
            logger.warning("context_health: DDD job registry refresh failed: %s", exc)

        # Governed corpus lifecycle and proposal mutations are retired in CDF stage B.

        # Knowledge Library + Transcript FTS5 indexing runs OUTSIDE
        # the git-rev gate.  These stores have their own delta-sync via
        # content_hash — unchanged files are skipped cheaply (~50ms for
        # 160 hash lookups).  Many Knowledge/ files are written by hooks
        # and jobs WITHOUT git commits (DailyActivity, JobResults, Signals),
        # so the git gate was blocking them from ever being indexed.
        # Bug: previously inside git-rev gate, only 1/160 files indexed.
        try:
            self._sync_knowledge_library(root, deadline=_refresh_deadline)
        except Exception as exc:
            logger.debug("context_health: knowledge library sync skipped: %s", exc)

        # Code graphs are not refreshed here: the daemon never parses a repo (design
        # 2026-10-29-code-intel-unification §0.2); a graph is refreshed when it is read.

        # OS Eval canary — session tier every session, daily tier resumed within
        # the remaining budget (see _run_eval_canary). Zero LLM cost.
        # Must respect cultivation deadline — canary runs LAST in _light_refresh.
        try:
            self._run_eval_canary(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: eval canary skipped: %s", exc)

    def _run_eval_canary(self, root: Path, *, _deadline: float = 0.0) -> None:
        """Run programmatic golden set cases (OS Eval canary).

        Two tiers for performance:
        - Session tier: pure file_contains cases, every session (<1s, string
          grep). Result → .context/.eval-canary.json (contract unchanged).
        - Daily tier: every other programmatic case (canary_pass /
          runtime_health spawn subprocesses, 2-13s each). Runs at most once per
          calendar day, RESUMABLE across sessions within the hook budget — see
          ``_run_daily_canary``. Result → .context/.eval-canary-daily.json.

        Zero LLM cost (programmatic_only). Never blocks session start.

        Args:
            _deadline: monotonic clock deadline from _light_refresh. If
                insufficient time remains, skip the canary so the hook stays
                inside its cooperative budget (the executor's per-hook
                timeout is set in main.py: SessionLifecycleHookManager).
        """
        # Budget check: _light_refresh has a 25s cooperative cultivation budget
        # and the canary runs LAST. The session tier needs <1s; 3s is
        # conservative. The daily tier budgets itself per case (_run_daily_canary).
        if _deadline > 0:
            remaining = _deadline - time.monotonic()
            if remaining < 3.0:
                logger.debug(
                    "context_health: eval canary skipped — only %.1fs remaining (need ≥3s)",
                    remaining,
                )
                return
        import sys
        # Import eval_runner from sibling scripts dir
        scripts_dir = Path(__file__).parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        try:
            from eval_runner import load_merged_golden_set, run_eval
        except ImportError:
            logger.warning("context_health: eval_runner not importable, skipping canary")
            return

        try:
            golden_set = load_merged_golden_set(root)
        except (FileNotFoundError, ValueError, AssertionError) as exc:
            # A missing product set means a broken install; say so instead of
            # silently skipping the canary every day.
            logger.warning("context_health: golden set not loadable, canary skipped: %s", exc)
            return

        all_programmatic = [
            c for c in golden_set["cases"] if c.get("eval_method") == "programmatic"
        ]
        # Session tier = pure file_contains (never spawns a subprocess); daily
        # tier = everything else programmatic.
        session_cases = [
            c for c in all_programmatic
            if "file_contains" in c.get("evaluators", [])
            and not set(c.get("evaluators", [])) & self._CANARY_SPAWNING_EVALUATORS
        ]
        session_ids = {c["id"] for c in session_cases}
        daily_cases = [c for c in all_programmatic if c["id"] not in session_ids]

        if session_cases:
            self._run_session_canary(root, golden_set, session_cases, run_eval)
        if daily_cases:
            self._run_daily_canary(root, daily_cases, _deadline=_deadline)

    # Evaluators that spawn a subprocess (seconds each) — never in the session tier.
    _CANARY_SPAWNING_EVALUATORS = frozenset({"canary_pass", "runtime_health"})

    def _run_session_canary(self, root: Path, golden_set: dict,
                            cases: list[dict], run_eval) -> None:
        """Session tier: run the pure file_contains cases, write .eval-canary.json."""
        golden_set_filtered = dict(golden_set)
        golden_set_filtered["cases"] = cases

        result = run_eval(golden_set_filtered, "session_canary", None, root,
                          programmatic_only=True)

        # Persist canary result for session briefing
        canary_file = root / ".context" / _CANARY_STATE_FILE
        import json as _json
        cases_error = result.get("cases_error", 0)
        # a case that timed out did not pass: counted with the failures, never as clean
        cases_timeout = result.get("cases_timeout", 0)
        canary_data = {
            "timestamp": result["triggered_at"],
            "total": result["total_cases"],
            "passed": result["cases_passed"],
            "failed": result["cases_failed"],
            "error": cases_error,
            "timeout": cases_timeout,
            "verdict": (result.get("release") or {}).get("verdict"),
            "duration_s": result["duration_seconds"],
        }

        if result["cases_failed"] > 0 or cases_timeout > 0:
            # Include failure details for briefing (real regressions, and hangs)
            canary_data["failures"] = [
                {"id": c["id"], "status": c["status"], "notes": c["notes"]}
                for c in result["cases"] if c["status"] in ("failed", "timeout")
            ]
            logger.warning(
                "context_health: eval canary FAILED %d/%d cases, %d timed out (release=%s)",
                result["cases_failed"], result["total_cases"], cases_timeout,
                canary_data["verdict"],
            )
        elif cases_error > 0:
            # Config errors: surface as info, not warning (not a regression)
            canary_data["config_errors"] = [
                {"id": c["id"], "notes": c["notes"]}
                for c in result["cases"] if c["status"] == "error"
            ]
            logger.info(
                "context_health: eval canary %d/%d pass, %d config errors (%.1fs)",
                result["cases_passed"], result["total_cases"], cases_error,
                result["duration_seconds"],
            )
        else:
            logger.info(
                "context_health: eval canary %d/%d pass (%.1fs)",
                result["cases_passed"], result["total_cases"], result["duration_seconds"],
            )

        try:
            canary_file.parent.mkdir(parents=True, exist_ok=True)
            canary_file.write_text(_json.dumps(canary_data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.debug("context_health: session canary write failed: %s", exc)

    # "Command timed out (7s)" / "runtime_health command timed out (7s)" — the
    # timeout notes eval_runner's subprocess evaluators emit.
    _CANARY_TIMEOUT_RE = re.compile(r"command timed out \((\d+)s\)", re.IGNORECASE)

    @classmethod
    def _is_budget_timeout(cls, result: dict, case_timeout: int,
                           full_cap: int = _CANARY_DAILY_CASE_TIMEOUT) -> bool:
        """True if a case "failed" only because the HOOK budget capped its timeout.

        A timeout at the case's full manual-run cap (``full_cap``, see
        ``_daily_case_caps``) is a real failure — the case is slow/hung exactly as
        it would be on a manual run. A timeout under a smaller budget-derived cap
        says nothing about the case.
        """
        if result.get("status") not in ("timeout", "failed") or case_timeout >= full_cap:
            return False
        return bool(cls._CANARY_TIMEOUT_RE.search(result.get("notes", "")))

    @staticmethod
    def _daily_case_caps(case: dict) -> tuple[int, int]:
        """(full manual-run cap, minimum runnable budget) for one daily case.

        Mirrors eval_runner: canary_pass caps at 20s; runtime_health runs
        ``max(15, min(30, override))`` — a 30s cap with a 15s FLOOR, so it must
        not start with less than 15s of hook budget left (it would overrun).
        """
        if "runtime_health" in (case.get("evaluators") or []):
            return _CANARY_RUNTIME_HEALTH_CAP, _CANARY_RUNTIME_HEALTH_FLOOR
        return _CANARY_DAILY_CASE_TIMEOUT, 0

    def _run_daily_canary(self, root: Path, cases: list[dict], *,
                          _deadline: float = 0.0) -> None:
        """Daily tier: subprocess-spawning programmatic cases, once per day, resumable.

        The whole tier (~50 cases × 2-13s) can never fit in one ~25s hook budget,
        so it is spread over the day's sessions:
        - Today's date is claimed in .eval-canary-daily.json BEFORE any case runs
          (on disk, not in memory), so a daemon restart or an executor-killed hook
          thread never restarts the tier from zero — the next run RESUMES.
        - A non-blocking md_lock means two hook runs (e.g. an orphaned thread from
          a timed-out hook + the next session) never run the tier concurrently.
        - Each case gets its manual-run cap (20s; runtime_health 30s with a 15s
          floor) or whatever budget remains; a case that timed out under a
          budget-reduced cap, or never started, is left PENDING (not failed) and
          retried by the next session — deferred cases run last, and a case that
          budget-times-out _CANARY_DAILY_MAX_DEFERRALS times is recorded failed.
        - An exception from one case records it as ``error``; the tier goes on.
        - Progress is persisted after every case.
        """
        try:
            from eval_runner import evaluate_case
        except ImportError:
            logger.debug("context_health: eval_runner not importable, skipping daily canary")
            return
        from utils.file_lock import md_lock

        state_path = root / ".context" / _CANARY_DAILY_STATE_FILE
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with md_lock(state_path, blocking=False) as got:
            if not got:
                logger.info(
                    "context_health: daily eval canary already running in another "
                    "hook run — skipping",
                )
                return
            today = date.today().isoformat()
            state = self._load_daily_canary_state(state_path)
            if state.get("date") != today:
                # Claim today's tier BEFORE running anything; the per-day record
                # (``days``) carries over.
                state = {
                    "date": today,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "results": {},
                    "days": [d for d in (state.get("days") or []) if isinstance(d, dict)],
                }
                self._write_daily_canary_state(state_path, state, cases)
            self._resume_daily_canary(root, state_path, state, cases,
                                      evaluate_case, _deadline)

    def _resume_daily_canary(self, root: Path, state_path: Path, state: dict,
                             cases: list[dict], evaluate_case,
                             _deadline: float) -> None:
        """Run today's still-pending daily cases within the budget (lock held)."""
        results = state.setdefault("results", {})
        pending = [c for c in cases if c["id"] not in results]
        if not pending:
            if not state.get("complete"):
                self._write_daily_canary_state(state_path, state, cases)
            return

        # Head-of-line fix: a case that keeps timing out under the budget would
        # otherwise sit first in line every session and starve the rest. Run
        # previously deferred cases LAST (stable by strike count), and after
        # _CANARY_DAILY_MAX_DEFERRALS budget timeouts record it as failed.
        strikes: dict = state.setdefault("deferrals", {})
        pending.sort(key=lambda c: int(strikes.get(c["id"], 0)))

        deferred: list[str] = []
        for case in pending:
            full_cap, floor = self._daily_case_caps(case)
            case_timeout = full_cap
            if _deadline > 0:
                remaining = _deadline - time.monotonic()
                if remaining < max(_CANARY_DAILY_MIN_CASE_BUDGET, floor):
                    # Not enough budget for THIS case (an evaluator with a
                    # timeout floor would overrun the hook) — a smaller case
                    # later in line may still fit. Not a strike.
                    deferred.append(case["id"])
                    continue
                case_timeout = min(case_timeout, int(remaining))
            try:
                r = evaluate_case(case, root, canary_timeout=case_timeout,
                                  programmatic_only=True)
            except Exception as exc:  # noqa: BLE001 — one broken case must not end the tier
                r = {"status": "error", "evaluator": "",
                     "notes": f"evaluate_case raised {type(exc).__name__}: {exc}"[:300]}
            if self._is_budget_timeout(r, case_timeout, full_cap):
                n = int(strikes.get(case["id"], 0)) + 1
                strikes[case["id"]] = n
                if n < _CANARY_DAILY_MAX_DEFERRALS:
                    deferred.append(case["id"])  # budget artefact, not a regression
                    self._write_daily_canary_state(state_path, state, cases)
                    continue
                r = {**r, "notes": (f"timed out under the hook budget {n} times "
                                    f"(last cap {case_timeout}s): {r.get('notes', '')}")[:300]}
            results[case["id"]] = {
                "status": r.get("status", "skipped"),
                "evaluator": r.get("evaluator", ""),
                "notes": r.get("notes", ""),
                "duration_ms": r.get("duration_ms", 0),
            }
            self._write_daily_canary_state(state_path, state, cases)

        state["deferred"] = deferred
        summary = self._write_daily_canary_state(state_path, state, cases)
        if summary.get("complete") and (summary.get("failed") or summary.get("timeout")):
            logger.warning(
                "context_health: daily eval canary FAILED %d/%d cases, %d timed out (release=%s)",
                summary["failed"], summary["total"], summary.get("timeout", 0), summary.get("verdict"),
            )
        else:
            logger.info(
                "context_health: daily eval canary %d/%d done (%d passed, %d failed, "
                "%d deferred to next session)",
                summary["done"], summary["total"], summary["passed"],
                summary["failed"], len(deferred),
            )

    @staticmethod
    def _load_daily_canary_state(state_path: Path) -> dict:
        """Read the daily canary state; a missing/corrupt file reads as empty."""
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("context_health: unreadable daily canary state (%s) — restarting tier", exc)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_daily_canary_state(state_path: Path, state: dict,
                                  cases: list[dict]) -> dict:
        """Recompute the summary over TODAY's case list and write atomically.

        Only cases in ``cases`` count (a case removed from the golden set mid-day
        drops out). ``verdict`` is the release decision over today's cases
        (core/eval_release.decide_release): a case not run yet today counts as having
        no result, so the verdict is incomplete until every case has run. A timeout is
        still listed (``timeouts``): a hang must not disappear.

        ``days`` keeps one entry per day for the last ``_CANARY_DAYS_KEPT`` days
        (``{date, passed, failed, timeout, error, skipped, total, not_run, verdict}``,
        ``not_run`` = no result yet; today's
        replaced on every write): the Offline Eval tab's day-by-day row.
        """
        ids = [c["id"] for c in cases]
        results = state.get("results", {})
        done = [results[i] for i in ids if i in results]
        n = {s: sum(1 for r in done if r.get("status") == s)
             for s in ("passed", "failed", "error", "skipped", "timeout")}
        from core.eval_release import decide_release

        state.pop("score", None)  # written before the release decision replaced it
        state.update({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(ids),
            "done": len(done),
            "pending": len(ids) - len(done),
            "complete": len(done) == len(ids),
            **n,
            "verdict": decide_release(cases, [{"id": i, **r} for i, r in results.items()
                                              if i in set(ids)])["verdict"],
            "failures": [
                {"id": i, "notes": results[i].get("notes", "")}
                for i in ids if results.get(i, {}).get("status") == "failed"
            ],
            "timeouts": [
                {"id": i, "notes": results[i].get("notes", "")}
                for i in ids if results.get(i, {}).get("status") == "timeout"
            ],
        })
        if state.get("date"):
            day = {"date": state["date"], "passed": n["passed"], "failed": n["failed"],
                   "timeout": n["timeout"], "error": n["error"], "skipped": n["skipped"],
                   "total": len(ids), "not_run": len(ids) - len(done),
                   "verdict": state["verdict"]}
            days = [d for d in (state.get("days") or [])
                    if isinstance(d, dict) and d.get("date") != state["date"]]
            state["days"] = sorted(days + [day], key=lambda d: str(d.get("date")))[-_CANARY_DAYS_KEPT:]
        tmp = state_path.with_name(state_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(tmp, state_path)
        except OSError as exc:
            logger.warning("context_health: daily canary state write failed: %s", exc)
        return state

    # ------------------------------------------------------------------
    # Auto-cultivation — promote session signals into DDD docs
    # ------------------------------------------------------------------

    # State file: tracks which JSONL session records have been cultivated
    _SESSION_CULTIVATED_STATE = ".context/.session_cultivated.json"

    def _auto_cultivate_session_signals(
        self, root: Path, *, _deadline: float = 0,
    ) -> bool:
        """Retired: session distillation now extracts complete claim groups.

        The former per-line DailyActivity decisions/corrections path had a
        measured keep rate below one percent and also mutated DDD files.
        """
        return False


    # High-volume dirs get compact summary instead of per-file table (saves ~1500 tokens)
    _COMPACT_INDEX_DIRS = {"DailyActivity", "JobResults", "Signals"}
    _HOT_COLD_THRESHOLD = 10  # Dirs with >10 files use Hot/Cold format
    _HOT_ENTRIES = 5  # Number of most-recent entries to show in Hot tier
    # (10→5, run_5f040023: the Knowledge Index is auto-regenerated every startup;
    # halving the Hot tier permanently trims ~4K tokens off KNOWLEDGE.md's
    # injected size — the nav is a lead-to-Glob, not the content, so 5 recent
    # per dir + a cold-count pointer is sufficient. Older files stay reachable
    # via workspace-finder/Glob as the summary line states.)
    _INDEX_LINE_CAP = 90  # Structural cap on Knowledge Index section lines (120→90)
    def _update_maturity(self, root: Path, *, _deadline: float = 0) -> bool:
        """Update maturity evidence from changelog and auto-promote eligible sections.

        Steps:
        1. For each project with DDD docs, update source_count from changelog.
        2. F5: Set verified_by_production from completed pipeline runs.
        3. Evaluate promotions (sparse→growing, growing→mature).
        4. Apply promotions + log to changelog.

        Runs after cultivation so new changelog entries are counted.
        Respects shared _deadline from _light_refresh (PE-3).

        Returns:
            True if any sections were promoted (DDD docs modified), False otherwise.
        """
        from core.ddd_maturity import (
            evaluate_all_promotions,
            promote_section,
            update_evidence_from_changelog,
        )

        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return False

        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 10.0)
        any_promoted = False

        for project_path in projects_dir.iterdir():
            if not project_path.is_dir():
                continue
            # PE-3: Respect time budget
            if time.monotonic() > _effective_deadline:
                logger.debug("context_health: maturity update hit deadline, stopping")
                break
            # Skip projects without DDD docs
            if not ddd_path(project_path, "TECH.md").exists() and not ddd_path(project_path, "PRODUCT.md").exists():
                continue

            try:
                # Step 1: Update evidence from changelog
                update_evidence_from_changelog(project_path)

                # Step 2 (F5): Set verified_by_production from completed pipeline runs.
                # If a pipeline delivered successfully using this project's DDD,
                # all sections contributed to that success → mark verified.
                self._set_verified_from_pipeline_runs(project_path)

                # Step 3+4: Evaluate and apply promotions
                promotions = evaluate_all_promotions(project_path)
                for promo in promotions:
                    success = promote_section(
                        project_path, promo["doc"], promo["section"], promo["to_level"]
                    )
                    if success:
                        any_promoted = True
                        # Log promotion to changelog
                        self._log_maturity_promotion(project_path, promo)
                        logger.info(
                            "context_health: maturity promoted %s/%s %s → %s",
                            promo["doc"], promo["section"],
                            promo["from_level"], promo["to_level"],
                        )
            except Exception as exc:
                logger.debug(
                    "context_health: maturity update for %s skipped: %s",
                    project_path.name, exc,
                )

        return any_promoted

    def _log_maturity_promotion(self, project_dir: Path, promo: dict) -> None:
        """Log a maturity promotion event to the DDD changelog."""
        import json as _json

        changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
        changelog_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_doc": promo["doc"],
            "target_section": promo["section"],
            "source_stage": "maturity_promotion",
            "change_type": "promotion",
            "detail": f"{promo['from_level']} → {promo['to_level']}",
            "evidence": promo.get("evidence", {}),
        }
        with open(changelog_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")

    def _set_verified_from_pipeline_runs(self, project_path: Path) -> None:
        """F5: Set verified_by_production on maturity states from completed pipeline runs.

        Scans recent runs (last 10). If a Pipeline v1 run has status=completed AND its
        run.json ``stages`` map shows DELIVER passed (completed / degraded), ALL sections
        in this project's DDD are marked verified_by_production=True +
        used_in_decision=True (the pipeline consumed DDD context).

        Processed run ids are kept in ``.artifacts/maturity_verified_runs.json``, never
        in run.json: that is the v1 engine's projection, which it rewrites.
        """
        import json as _json
        from core.ddd_maturity import (
            inject_maturity,
            parse_maturity,
        )

        runs_dir = project_path / ".artifacts" / "runs"
        if not runs_dir.is_dir():
            return
        ledger = project_path / ".artifacts" / "maturity_verified_runs.json"
        try:
            done = _json.loads(ledger.read_text(encoding="utf-8"))
            done = [str(x) for x in done] if isinstance(done, list) else []
        except (ValueError, OSError):
            done = []

        # Find completed runs with deliver stage, not yet processed
        found_run: tuple | None = None  # (run_dir, run_json, data)
        for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:10]:
            run_json = run_dir / "run.json"
            if not run_json.exists():
                continue
            try:
                data = _json.loads(run_json.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue

            if run_dir.name in done:
                continue  # Already processed
            if not isinstance(data, dict) or data.get("status") != "completed":
                continue

            # v1: run.json "stages" is the {stage: state} map
            stages = data.get("stages")
            if not (isinstance(stages, dict)
                    and stages.get("deliver") in ("completed", "degraded")):
                continue

            found_run = (run_dir, run_json, data)
            break  # One run per session is enough

        if not found_run:
            return

        run_dir, run_json, data = found_run

        # Apply verification to all DDD doc sections FIRST, before marking run.
        # This ensures we don't permanently lose verification if doc writes fail.
        # Use cross-platform file lock (same lock file as _ch_entry_lifecycle).
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        any_doc_updated = False
        for doc_name in DDD_CANONICAL_DOCS:
            # Resolve via the six-section layout resolver — migrated DDDs keep the
            # canonical docs under 2-understanding/. A raw `project_path / doc_name`
            # here silently missed every migrated doc (root .exists()=False → skip),
            # so maturity verification (verified_by_production / used_in_decision)
            # was NEVER written to migrated DDDs (split-brain, run_ff06972d).
            doc_path = ddd_path(project_path, doc_name)
            if not doc_path.exists():
                continue

            # SHARED doc-write lock (run_06350217): use md_lock so this verified-flag
            # write holds the SAME <doc>.md.lock every other DDD-doc writer uses. The
            # old raw flock on `.{doc}.lock` (= .IMPROVEMENT.md.lock) was a DIVERGENT
            # name → it did NOT mutually exclude the decay/append/retire writers on
            # IMPROVEMENT.md.lock. Non-blocking: skip a doc held by another writer.
            from utils.file_lock import md_lock
            with md_lock(doc_path, blocking=False) as _got:
                if not _got:
                    continue  # another writer holds this doc — skip
                content = doc_path.read_text(encoding="utf-8")
                states = parse_maturity(content)
                if not states:
                    continue

                changed = False
                for state in states.values():
                    if not state.verified_by_production:
                        state.verified_by_production = True
                        changed = True
                    if not state.used_in_decision:
                        state.used_in_decision = True
                        changed = True

                if changed:
                    new_content = inject_maturity(content, states)
                    if new_content != content:
                        doc_path.write_text(new_content, encoding="utf-8")
                        any_doc_updated = True

        # Only mark run as processed AFTER doc writes succeeded.
        # If no docs were updated (all locked or no maturity states), still mark
        # to avoid re-scanning — but only if at least one doc was attempted.
        if any_doc_updated or not any(
            ddd_path(project_path, d).exists() for d in DDD_CANONICAL_DOCS
        ):
            try:
                tmp = ledger.with_suffix(".tmp")
                tmp.write_text(_json.dumps((done + [run_dir.name])[-200:]), encoding="utf-8")
                os.replace(tmp, ledger)
            except OSError:
                pass
            logger.debug(
                "context_health: F5 verified maturity from run %s for %s",
                run_dir.name, project_path.name,
            )

    def _track_memory_usage(self, root: Path) -> None:
        """Scan session transcripts for memory key references.

        Finds patterns like ``[RC04]``, ``[KD05]``, ``[LL07]``, ``[COE02]``
        in recent Claude session transcripts (JSONL) where the agent actually
        references memory entries during conversations.

        Previous implementation scanned DailyActivity (0 signal — DA never
        contains memory keys). Session transcripts DO contain them in both
        tool_use blocks and assistant responses.

        Distillation reads this file to decide eviction order: entries with
        zero usage are evicted first when section caps are exceeded, forming
        the compound loop: use → track → evict unused → memory improves.
        """
        _KEY_RE = re.compile(r"\[([A-Z]{2,3}\d{2,3})\]")

        usage_path = root / ".context" / ".memory-usage.json"
        usage_path.parent.mkdir(parents=True, exist_ok=True)

        # Lock the WHOLE read-modify-write (Gate-2 MEDIUM, run_241014d4): two
        # near-simultaneous session-closes run this on the shared thread pool with
        # no synchronization, risking double-decay, lost citation bursts, or a torn
        # sidecar. Non-blocking + skip-if-busy (matches the MEMORY.md writer below):
        # a skipped tracking run is harmless (next session re-counts from the
        # 7-day transcript window); a corrupted file is not.
        from utils.file_lock import flock_exclusive_nb, flock_unlock
        lock_path = usage_path.with_suffix(".json.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive_nb(lock_fd)
        except OSError:
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: memory-usage lock busy, skipping track")
            return

        try:
            self._track_memory_usage_locked(root, usage_path, _KEY_RE)
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    def _track_memory_usage_locked(self, root, usage_path, _KEY_RE) -> None:
        """Body of _track_memory_usage, run under the memory-usage file lock."""
        # Load existing usage. Values may be int (legacy) or float (post-decay) —
        # the decay below turns them to float; all readers are float-safe.
        usage: dict[str, float] = {}
        if usage_path.exists():
            try:
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # ── Write-time decay (run_81f6d20c) — break the cumulative ratchet ──
        # The counts above were ADD-only and never decremented, so a once-hot
        # entry stayed reclaim-protected forever and the file grew unbounded.
        # Apply exponential decay ONCE per calendar day, gated by a sidecar
        # last_decay date (the producer runs on every session close). A legacy
        # file with NO sidecar is treated as last_decay=today → NO decay on the
        # first upgrade, so currently-used entries lose no protection. A corrupt
        # sidecar fails safe (no decay).
        #
        # Decay happens here (before the increment, so we fade OLD counts then add
        # fresh citations), but the sidecar last_decay is written AFTER the counts
        # file at the end of this method (see `_meta_to_write`). Write-ORDER matters
        # (Gate-1 flaw 3): counts-then-meta means a crash between writes re-decays
        # next run (over-decay, self-correcting) instead of marking decay done while
        # the on-disk counts are still undecayed (silent skip forever).
        meta_path = root / ".context" / ".memory-usage-meta.json"
        _meta_to_write: str | None = None
        if usage:
            from core.memory_decay import decay_usage_counts
            today = date.today()
            last_decay = today
            _force_heal = False  # rewrite the sidecar even when no decay happens
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    last_decay = date.fromisoformat(meta["last_decay"])
                    if last_decay > today:
                        # Future last_decay (clock skew / hand-edit): negative
                        # days_elapsed would skip decay forever until the wall clock
                        # passes the bogus date. Clamp to today + heal the sidecar.
                        last_decay = today
                        _force_heal = True
                except (json.JSONDecodeError, OSError, KeyError, ValueError):
                    # Corrupt sidecar → fail safe (no decay THIS run) but HEAL it,
                    # else it is re-read corrupt forever and decay is permanently
                    # disabled with no signal (Gate-2 MEDIUM, run_241014d4).
                    last_decay = today
                    _force_heal = True
            days_elapsed = (today - last_decay).days
            if days_elapsed > 0:
                usage = decay_usage_counts(usage, days_elapsed)
            if days_elapsed > 0 or not meta_path.exists() or _force_heal:
                _meta_to_write = today.isoformat()

        # Source 1: Recent session transcripts (last 7 days)
        transcripts_dir = Path.home() / ".claude" / "projects"
        if transcripts_dir.is_dir():
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            # Track which files we've already scanned (avoid re-counting)
            scanned_marker = root / ".context" / ".memory-usage-scanned.txt"
            scanned_set: set[str] = set()
            if scanned_marker.exists():
                try:
                    scanned_set = set(scanned_marker.read_text().splitlines())
                except OSError:
                    pass

            new_scanned: list[str] = []
            for jsonl_file in transcripts_dir.rglob("*.jsonl"):
                # Only scan recent files (mtime check is fast)
                try:
                    if jsonl_file.stat().st_mtime < (time.time() - 7 * 86400):
                        continue
                except OSError:
                    continue
                file_key = str(jsonl_file)
                if file_key in scanned_set:
                    continue
                # Read and scan for memory key references
                try:
                    content = jsonl_file.read_text(encoding="utf-8", errors="ignore")
                    for key in _KEY_RE.findall(content):
                        usage[key] = usage.get(key, 0) + 1
                    new_scanned.append(file_key)
                except OSError:
                    continue

            # Update scanned marker — prune entries for files older than scan window (7d)
            # Using date-based cutoff instead of count-based cap to prevent re-scanning
            # files that fell off the cap but are still within the 7d mtime window (G1 fix).
            if new_scanned:
                all_scanned = list(scanned_set) + new_scanned
                # Prune: only keep entries whose files still exist and are within 7d
                mtime_cutoff = time.time() - 7 * 86400
                pruned = []
                for entry in all_scanned:
                    try:
                        if Path(entry).stat().st_mtime >= mtime_cutoff:
                            pruned.append(entry)
                    except OSError:
                        pass  # File deleted — drop from marker
                scanned_marker.write_text("\n".join(pruned), encoding="utf-8")

        # Source 2: DailyActivity (secondary — may contain refs from session summaries)
        daily_dir = root / "Knowledge" / "DailyActivity"
        if daily_dir.is_dir():
            cutoff_da = (date.today() - timedelta(days=7)).isoformat()
            for f in sorted(daily_dir.glob("*.md"), reverse=True):
                if f.stem < cutoff_da:
                    break
                try:
                    body = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for key in _KEY_RE.findall(body):
                    usage[key] = usage.get(key, 0) + 1

        # Atomic write (tmp + os.replace) so a crash/torn-write never leaves a
        # partial counts file for the next reader (Gate-2 MEDIUM, run_241014d4).
        _tmp = usage_path.with_suffix(".json.tmp")
        _tmp.write_text(json.dumps(usage, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(_tmp, usage_path)

        # Write the decay sidecar AFTER the counts (Gate-1 flaw 3 write-order):
        # if a crash lands between these two writes, next run sees an UNADVANCED
        # last_decay and re-decays (self-correcting) — never marks decay done over
        # undecayed counts. Atomic for the same torn-write reason.
        if _meta_to_write is not None:
            try:
                _mtmp = meta_path.with_suffix(".json.tmp")
                _mtmp.write_text(
                    json.dumps({"last_decay": _meta_to_write}), encoding="utf-8"
                )
                os.replace(_mtmp, meta_path)
            except OSError:
                pass

    def _refresh_ddd_registry(self, root: Path) -> None:
        """Rebuild the DDD skill-registry manifest from every DDD's aim.json.

        The manifest (``.context/ddd_skill_registry.json``) is what SkillManager's
        ddd-tier scan reads to discover a DDD's domain skills. WITHOUT this, the
        manifest was only built at daemon startup (``refresh_builtin_defaults`` →
        ``build_manifest``), so a DDD's ``aim.json plugins.domain_skills`` change,
        or a newly-added DDD, was NOT reflected until the daemon restarted (a new
        DDD's skills stayed invisible to discovery). Rebuilding here — in the
        per-session light-refresh, unconditionally, like ``_refresh_memory_index``
        — closes that gap: an uncommitted aim.json edit is picked up next session.

        ``build_manifest`` is cheap (walk ``Projects/*/aim.json``, JSON reads + one
        atomic write, no LLM/network) and idempotent (tmp + os.replace), and it now
        carries an empty-overwrite guard (won't wipe a good cache on a transient
        Projects/ read error). Fail-soft: a registry refresh failure must NEVER take
        down the rest of context-health refresh, so any error is logged and
        swallowed. (run_669e29f6)
        """
        try:
            from core import ddd_skill_registry
            from core.skill_manager import skill_manager
            builtin = skill_manager.builtin_path
            if builtin is None:
                return  # skill_manager not wired yet → no-op (production-safe)
            ddd_skill_registry.build_manifest(root, builtin)
        except Exception as exc:  # noqa: BLE001 — must never break context-health
            logger.debug("context_health: DDD skill registry refresh skipped: %s", exc)

    def _refresh_ddd_job_registry(self, root: Path) -> None:
        """Rebuild the DDD JOB-registry manifest from every DDD's bindings.yaml.

        The manifest (``.context/ddd_job_registry.json``) is a read-on-demand
        ownership index of every mounted DDD's ``kind: job`` governed assets — which
        DDD owns which scheduled job + which domain skill each job depends on
        (``depends_on_skill_resolved`` surfaces a dangling job whose skill isn't
        discoverable). Option A / J1: this is a SIDECAR index consumed by
        diagnostics / s_ddd-manager / distribution tooling — NOT by the scheduler
        (scheduler is first-source-wins; making it DDD-aware is J1a/J2). See
        ``core/ddd_job_registry.py`` + design 2026-07-19-ddd-jobs-tools-registry.

        MUST run AFTER ``_refresh_ddd_registry`` (called below it in _light_refresh):
        job records resolve ``depends_on_skill`` against the freshly-rebuilt skill
        manifest. If the skill manifest is stale/missing, resolution degrades to
        False (dangling, surfaced not crashed) — never fatal. Cheap (per-project
        yaml read + one atomic write, no LLM/network) + fail-soft: a job-registry
        failure must NEVER take down the rest of context-health refresh.
        (run_5ec6b7ad, J1)
        """
        try:
            from core import ddd_job_registry
            ddd_job_registry.build_manifest_jobs(root)
        except Exception as exc:  # noqa: BLE001 — must never break context-health
            logger.debug("context_health: DDD job registry refresh skipped: %s", exc)

    # _sync_memory_embeddings REMOVED (pure-filesystem READ-line finalize,
    # run_2f621986, design 2026-06-28 §3). It was the memory_vec WRITER and had
    # ZERO production callers (the call site was already removed when the read
    # side went keyword-only; only the dead method body remained). Memory recall
    # is keyword/FTS5 only — no embedding writer needed. The _RECOVERY_DRAIN_BUDGET
    # constant and the MemoryEmbeddingStore/EmbeddingClient imports it used are
    # gone with it.

    def _sync_knowledge_library(self, root: Path, deadline: float | None = None) -> None:
        """Incremental sync of Knowledge/ files into the FTS5 keyword index.

        Scans Knowledge/ for new/changed .md files, chunks them, and
        delta-syncs into knowledge_chunks + knowledge_fts (FTS5 keyword index).
        Typical: 1-3 file changes, <5s. First full index: ~100s.

        ``deadline`` (time.monotonic) bounds the per-file embed loop so a large
        changeset can't overrun the 30s executor timeout — remaining files
        defer to the next session (content_hash delta-sync makes this safe).

        Failures are silent — recall engine degrades gracefully.
        """
        knowledge_dir = root / "Knowledge"
        if not knowledge_dir.is_dir():
            return

        from core.knowledge_store import KnowledgeStore, sync_knowledge_index
        from core.vec_db import open_vec_db

        with open_vec_db() as conn:
            if conn is None:
                logger.debug("context_health: recall DB connect failed, skipping library sync")
                return

            store = KnowledgeStore(conn)
            store.ensure_tables()

            # Self-heal a malformed FTS index (run_1d198980). This is the RIGHT
            # layer for repair — off the event loop, single-flight per session,
            # already a write context — NOT the recall read path (which must
            # stay read-only; rebuild takes a write lock + re-tokenizes all
            # chunks). A corrupt index otherwise makes Library recall silently
            # empty until the next full re-index.
            if not store._fts_is_healthy():
                logger.warning("context_health: knowledge_fts malformed — rebuilding index")
                try:
                    store.repair_fts_index()
                    logger.info("context_health: knowledge_fts rebuilt from content table")
                except Exception as exc:  # noqa: BLE001 — repair is best-effort
                    logger.error("context_health: knowledge_fts repair failed: %s: %s",
                                 type(exc).__name__, exc)

            # FTS5-ONLY (pure-filesystem recall, vector leg removed 2026-08-14 —
            # see PRI11). sync_knowledge_index builds the keyword index only; there
            # is no embedding step. Archives→FTS5 (Run-1 DoD4) indexed here too.
            stats = sync_knowledge_index(
                store, knowledge_dir, deadline=deadline,
            )
            if stats.get("deferred", 0) > 0:
                logger.info(
                    "context_health: refresh budget reached mid knowledge-library "
                    "sync — deferring %d file(s) to next session",
                    stats["deferred"],
                )
            # NOTE: knowledge_vec orphan-backfill REMOVED (writer stopped) — no
            # vector leg to heal. FTS5 has no orphan-vector concept.

        if stats.get("chunks_added", 0) > 0 or stats.get("files_removed", 0) > 0:
            logger.info(
                "context_health: knowledge library synced — "
                "%d files scanned, %d chunks added, %d skipped, %d removed",
                stats["files_scanned"], stats["chunks_added"],
                stats["chunks_skipped"], stats["files_removed"],
            )

    # ------------------------------------------------------------------
    # Deep check — once per day, <10s
    # ------------------------------------------------------------------

    def _deep_check(self, root: Path, ws_path: str) -> None:
        """Full context health validation."""
        findings: list[str] = []

        # 1. Context files exist and non-empty
        context_dir = root / ".context"
        if context_dir.is_dir():
            for md_file in sorted(context_dir.glob("*.md")):
                if md_file.name.startswith("L") and md_file.name.endswith("_SYSTEM_PROMPTS.md"):
                    continue  # Cache files, not source
                size = md_file.stat().st_size
                if size == 0:
                    findings.append(f"EMPTY: {md_file.name} (0 bytes)")

        # 2. Git health
        findings += self._check_git_health(root, ws_path)

        # 3. DDD Cultivation — event-driven (v2).
        #    Emits SESSION_CLOSE event via dispatcher. Channels subscribed to
        #    SESSION_CLOSE fire via the event-driven path. orchestrator.run()
        #    is retained as fallback if dispatcher isn't warmed up yet.
        try:
            from core.cultivation_dispatcher import (
                EventType, emit_cultivation_event_threadsafe, get_dispatcher,
            )
            dispatcher = get_dispatcher()
            if dispatcher.loop is not None:
                # Dispatcher is warmed up — use event-driven path only
                emit_cultivation_event_threadsafe(
                    EventType.SESSION_CLOSE,
                    source="context_health_hook",
                    payload={"trigger": "deep_check"},
                    priority=2,
                )
            else:
                # Dispatcher not yet warmed (first session) — fallback to legacy
                from core.ddd_orchestrator import DddCultivationOrchestrator
                orchestrator = DddCultivationOrchestrator()
                findings += orchestrator.run(root, ws_path)
                # Also emit to warm up the dispatcher for next session
                emit_cultivation_event_threadsafe(
                    EventType.SESSION_CLOSE,
                    source="context_health_hook",
                    payload={"trigger": "deep_check_warmup"},
                    priority=2,
                )
        except Exception as exc:
            # Ultimate fallback: if dispatcher fails entirely, run legacy
            try:
                from core.ddd_orchestrator import DddCultivationOrchestrator
                orchestrator = DddCultivationOrchestrator()
                findings += orchestrator.run(root, ws_path)
            except Exception as inner_exc:
                logger.warning(
                    "context_health: DDD cultivation failed (non-blocking): %s / %s",
                    exc, inner_exc,
                )

        # 3h. Adversarial meta-monitoring — surface degradation in session briefing
        try:
            from core.adversarial_meta import check_adversarial_health
            artifacts_dir = root / "Projects" / DEFAULT_PROJECT_NAME / ".artifacts"
            if artifacts_dir.is_dir():
                health = check_adversarial_health(artifacts_dir)
                if health.get("degradation_warning"):
                    findings.append(
                        f"[gap/high] Adversarial review may be degraded — "
                        f"{health['consecutive_zero_count']} consecutive reviewed pipeline "
                        f"runs had 0 findings. Consider rotating "
                        f"adversarial prompt."
                    )
        except Exception as exc:
            logger.debug("context_health: adversarial meta-check skipped: %s", exc)

        # 4. DailyActivity — today's file should exist if we're running
        da_dir = root / "Knowledge" / "DailyActivity"
        today_file = da_dir / f"{date.today().isoformat()}.md"
        if da_dir.is_dir() and not today_file.exists():
            findings.append(f"MISSING: DailyActivity/{today_file.name} (no session logged today)")

        # 5. MEMORY capacity is diagnostic-only; CDF never evicts baseline units.
        memory_path = context_dir / "MEMORY.md"

        # 6. Memory consistency — detect stale claims in MEMORY.md body
        if memory_path.exists():
            findings += self._detect_stale_memory_claims(memory_path)

        # 7. L1 cache freshness — if source .md newer than cache, invalidate
        self._check_cache_freshness(context_dir, findings)

        # 8. Enforce retention policies (archive/delete old files)
        try:
            findings += self._enforce_retention_policies(ws_path)
        except Exception as exc:
            logger.warning("context_health: retention policy enforcement failed: %s", exc)

        # 9. Auto-refresh AI_CONTEXT.md + AGENTS.md metrics (codebase root)
        try:
            from scripts.refresh_ai_docs import refresh as refresh_ai_docs
            result = refresh_ai_docs()
            if result.get("files_updated"):
                logger.info(
                    "context_health: refreshed %s",
                    ", ".join(result["files_updated"]),
                )
        except Exception as exc:
            logger.debug("context_health: AI docs refresh skipped: %s", exc)

        # 10. Governance budget enforcement (Three-Layer Governance)
        findings += self._check_governance_budgets(root, context_dir)

        # 10b. Write-side governance health (R3): catch the drift class that let
        #      caps/section-targets silently rot. Entry-count based (NOT token —
        #      R3-8: the token estimator is ~33% off, can't gate on it).
        findings += self._check_write_governance(context_dir)

        # 11. Context token budget measurement
        if context_dir.is_dir():
            findings += self._check_token_budget(context_dir)
            # 11a. Daily size-snapshot for the C&M brain trend charts (run_d0ba3f69).
            #      Uses the numbers _check_token_budget just populated into
            #      self._token_measurement. UPSERT-by-date (idempotent — this
            #      deep-check can re-run same-day across restarts). Net-new series,
            #      no backfill (XG: count from launch date forward).
            try:
                self._append_brain_size_snapshot(root, context_dir)
            except Exception as e:  # observability write — never block the hook
                logger.debug("brain size snapshot skipped: %s: %s", type(e).__name__, e)

        # 11b. Self-report drift: a context file that states its own token size
        #      in prose ("~44K tokens") and has since grown misleads the agent
        #      about its own context. WARN-only (run_3f25a73a).
        if context_dir.is_dir():
            findings += self._check_self_report_drift(context_dir)

        # 13. DDD completeness — detect HALF-CREATED projects (≥1 but <4 standard
        #     DDD docs). This is the gap that let CMHK_SalesIntel sit with only
        #     IMPROVEMENT.md (3 missing docs) for >1 month unwarned: cultivation
        #     checks CONTENT freshness, the (now-removed) project-section
        #     writer silently skipped missing docs, and nothing checked
        #     EXISTENCE of the standard 4. A half-created project breaks
        #     cross-project index refs + leaves EVALUATE without a PRODUCT/TECH
        #     base. (run_5a29f00c)
        findings += self._check_ddd_completeness(root)
        findings += self._check_ddd_shape(root)

        # 14. README six-section drift — the platform Projects/README.md must keep
        #     describing all six canonical DDD sections (SSOT:
        #     swarm_workspace_manager.DDD_SIX_SECTION_NAMES, the same constant the
        #     per-project AGENTS.md is generated from). WARN-only, fail-open — a
        #     human-readable README is not a cognitive organ, so this just prevents
        #     silent staleness, never blocks.
        findings += self._check_readme_six_sections(root)

        # 15. Recall/DDD-inject degradation — surface the WRITE-ONLY degradation
        #     counters (session_router) that were incremented but never read, so a
        #     silently-failing recall (empty every session on a real failure) is no
        #     longer invisible for the daemon's whole lifetime. (run_e9861490)
        findings += self._check_recall_degradation()

        # Persist findings for session briefing
        self._persist_findings(root, findings)

        # Report
        if findings:
            logger.warning(
                "context_health: deep check found %d issue(s):\n  %s",
                len(findings), "\n  ".join(findings),
            )
        else:
            logger.info("context_health: deep check passed — all healthy")


    def _check_ddd_completeness(self, root: Path) -> list[str]:
        """Flag HALF-CREATED projects: ≥1 but <4 of the standard DDD docs.

        A DDD project has 4 docs (PRODUCT/TECH/IMPROVEMENT/PROJECT). A project
        with SOME but not ALL is half-created — usually because it was made
        outside standard `s_project-manager` provisioning. That leaves the
        pipeline's EVALUATE stage without a PRODUCT/TECH base and breaks
        cross-project references that expect the full doc set.

        A dir with 0 DDD docs is NOT a DDD project (skip it). A dir with all 4
        is healthy (skip it). Only the 1-3 range is flagged.

        Fail-open: any error is logged + returned as a finding, never raised —
        a health sub-item must not break the whole deep check or session start.
        """
        DDD_DOCS = DDD_CANONICAL_DOCS
        findings: list[str] = []
        try:
            projects_dir = root / "Projects"
            if not projects_dir.is_dir():
                return findings
            for d in sorted(projects_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                present = [f for f in DDD_DOCS if ddd_path(d, f).exists()]
                if present and len(present) < len(DDD_DOCS):
                    missing = [f for f in DDD_DOCS if f not in present]
                    findings.append(
                        f"[gap/medium] DDD-INCOMPLETE: project '{d.name}' has "
                        f"{len(present)}/4 standard DDD docs — missing "
                        f"{', '.join(missing)}. Half-created projects break "
                        f"cross-project index refs + leave pipeline EVALUATE "
                        f"without a PRODUCT/TECH base. Backfill via s_project-manager."
                    )
        except Exception as e:  # noqa: BLE001 — fail-open by design
            findings.append(
                f"DDD completeness check failed (non-fatal): "
                f"{type(e).__name__}: {e}"
            )
        return findings

    def _check_ddd_shape(self, root: Path) -> list[str]:
        """Per Brain: docs out of the shape CDF writes, and paths the docs name that do
        not exist (core.ddd_format). Nothing writes; each finding says the fix. Fail-open:
        a Brain that cannot be read is reported, never raised."""
        findings: list[str] = []
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return findings
        from core import ddd_format
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or not ddd_path(d, "PRODUCT.md").exists():
                continue
            try:
                problems = sum(len(v) for v in ddd_format.project_report(d).values())
                if problems:
                    findings.append(
                        f"[drift/medium] DDD-FORMAT: Brain '{d.name}' has {problems} line(s) out of "
                        f"the shape CDF writes — `artifact_cli ddd-format --project {d.name}` lists "
                        f"them, `--apply` moves them losslessly.")
                refs = ddd_format.dangling_references(d, root)
                if refs:
                    f, line, ref = refs[0]
                    findings.append(
                        f"[drift/low] DDD-REFS: Brain '{d.name}' names {len(refs)} path(s) that do "
                        f"not exist (first: {f}:{line} `{ref}`) — correct them, or keep a dated one.")
            except Exception as e:  # noqa: BLE001 — fail-open by design
                findings.append(f"DDD shape check of '{d.name}' failed (non-fatal): {type(e).__name__}: {e}")
        return findings

    def _check_recall_degradation(self) -> list[str]:
        """Surface the WRITE-ONLY recall/DDD-inject degradation counters (run_e9861490).

        session_router increments _recall_degraded_count + _ddd_inject_count on every
        recall/inject failure, but NOTHING read them — a silently-failing recall was
        invisible for the daemon's whole lifetime (the months-hidden class the
        counters were built to kill; GUI83: a positive counter is only half — it must
        be READ). This is the read side.

        Reports ONLY true-failure totals (crash/timeout/unavailable) — NEVER the
        informational no-match keys (empty_with_keywords / declined:no_ddd_hits),
        which would false-alarm on every legitimate empty recall. Factual, no
        blocking threshold (STEERING #2): one self-describing line labeled
        'since daemon start', so a lone transient reads as transient, not chronic.
        The synonym-miss rate (empty_with_keywords) is reported SEPARATELY as a
        signal-quality note, not a failure. Fail-open: a metric read never breaks
        the health check.
        """
        findings: list[str] = []
        try:
            from core.session_router import (
                get_recall_degraded_snapshot, get_ddd_inject_snapshot,
                recall_true_failure_total, ddd_inject_true_failure_total,
                _is_recall_true_failure, _is_ddd_true_failure,
            )
        except Exception as e:  # noqa: BLE001 — never break the health check on a metric
            logger.debug("recall-degradation check skipped (import failed): %s", e)
            return findings

        try:
            recall_snap = get_recall_degraded_snapshot()
            ddd_snap = get_ddd_inject_snapshot()

            recall_fails = recall_true_failure_total(recall_snap)
            if recall_fails > 0:
                reasons = ", ".join(
                    f"{r}={n}" for r, n in sorted(recall_snap.items())
                    if _is_recall_true_failure(r)
                )
                findings.append(
                    f"RECALL DEGRADED (since daemon start): {recall_fails} "
                    f"true-failure(s) [{reasons}] — recall returned empty on a real "
                    f"failure, not a no-match. Check the daemon log for the WARNINGs."
                )

            ddd_fails = ddd_inject_true_failure_total(ddd_snap)
            if ddd_fails > 0:
                reasons = ", ".join(
                    f"{r}={n}" for r, n in sorted(ddd_snap.items())
                    if _is_ddd_true_failure(r)
                )
                findings.append(
                    f"DDD-INJECT DEGRADED (since daemon start): {ddd_fails} "
                    f"true-failure(s) [{reasons}]."
                )

            # Signal-quality (NOT a failure): a high synonym-miss rate hints the
            # keyword-only recall is missing entries that exist under other wording
            # (the blind spot the vector-leg removal created). Informational only.
            synonym_miss = recall_snap.get("empty_with_keywords", 0)
            if synonym_miss >= 20:
                findings.append(
                    f"RECALL synonym-miss rate elevated (since daemon start): "
                    f"empty_with_keywords={synonym_miss} — recall ran but matched "
                    f"nothing this often; entries may exist under different wording "
                    f"(keyword-only blind spot). Not a failure, a coverage signal."
                )

            # Catch-all (Gate-2 LOW, run_e9861490): a reason that is NEITHER a
            # known true-failure NOR known-informational is UNCLASSIFIED — surface
            # it so a future writer's new reason string can't silently vanish from
            # every signal (the dead-signal recursion this whole fix exists to kill).
            from core.session_router import (
                recall_unclassified_reasons, ddd_unclassified_reasons,
            )
            unclassified = {
                **recall_unclassified_reasons(recall_snap),
                **{f"ddd:{k}": v for k, v in ddd_unclassified_reasons(ddd_snap).items()},
            }
            if unclassified:
                pairs = ", ".join(f"{r}={n}" for r, n in sorted(unclassified.items()))
                findings.append(
                    f"RECALL/DDD degradation has UNCLASSIFIED reason(s) [{pairs}] — "
                    f"a writer emits a reason the health classifier doesn't recognize. "
                    f"Classify it in session_router (true-failure vs known-informational) "
                    f"so it stops hiding."
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("recall-degradation check failed (non-blocking): %s", e)

        return findings

    def _check_readme_six_sections(self, root: Path) -> list[str]:
        """Flag DRIFT: Projects/README.md no longer describes all six DDD sections.

        The platform-level Projects/README.md documents the canonical six-section
        DDD structure (Identity / Knowledge / Gates / Capabilities / Delivery
        Contract / Refresher). The SAME structure is described in every project's
        AGENTS.md, generated from a template in core.swarm_workspace_manager. If a
        section is renamed/added there but the hand-maintained README isn't
        updated, the README silently drifts from canonical truth (R30).

        This is a WARN-only string-presence check: it catches a section NAME being
        dropped from the README (the common drift), NOT semantic divergence of a
        section's description — a human-readable doc is not a cognitive organ, so a
        cheap presence check is the right weight, never a BLOCK.

        SSOT: the six names come from swarm_workspace_manager.DDD_SIX_SECTION_NAMES,
        so this check never hardcodes its own copy of the vocabulary. (That constant
        is kept CONSISTENT-BY-CONVENTION with the AGENTS.md template's long-form
        prose — each short name is a substring of its long form — but is NOT
        mechanically bound to it; see the constant's own note. This check verifies
        the README against the constant, not against the template.)

        Fail-open: absent README (nothing to check) or any error → silent / logged
        finding, never raised — a health sub-item must not break the deep check.
        """
        findings: list[str] = []
        try:
            readme = root / "Projects" / "README.md"
            if not readme.is_file():
                return findings  # nothing to check — not an error
            from core.swarm_workspace_manager import DDD_SIX_SECTION_NAMES
            text = readme.read_text(encoding="utf-8")
            missing = [name for name in DDD_SIX_SECTION_NAMES if name not in text]
            if missing:
                findings.append(
                    f"[gap/low] README-DRIFT: Projects/README.md no longer mentions "
                    f"DDD section(s): {', '.join(missing)}. The six-section structure "
                    f"(SSOT: swarm_workspace_manager.DDD_SIX_SECTION_NAMES) is what every "
                    f"project's AGENTS.md is generated from — the README must describe all "
                    f"six or it drifts from canonical truth. Update Projects/README.md."
                )
        except Exception as e:  # noqa: BLE001 — fail-open by design
            findings.append(
                f"README six-section check failed (non-fatal): "
                f"{type(e).__name__}: {e}"
            )
        return findings

    def _check_git_health(self, root: Path, ws_path: str) -> list[str]:
        """Check git state: stale locks, uncommitted context files."""
        findings = []

        # Stale index.lock
        lock_file = root / ".git" / "index.lock"
        if lock_file.exists():
            age = datetime.now().timestamp() - lock_file.stat().st_mtime
            if age > 300:  # > 5 minutes = definitely stale
                try:
                    lock_file.unlink()
                    findings.append("AUTO-FIXED: removed stale .git/index.lock (age=%.0fs)" % age)
                except OSError:
                    findings.append("STALE: .git/index.lock (age=%.0fs, cannot remove)" % age)

        # Uncommitted .context/ changes
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", ".context/"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=self._GIT_TIMEOUT,
            )
            # Canary state files (.eval-canary*.json + lock sidecar) are
            # machine-written runtime state rewritten by THIS hook every session —
            # their being dirty is expected, not a "forgot to commit" signal.
            # Excluded in code (the workspace .gitignore is user-owned).
            uncommitted = [
                l.strip() for l in result.stdout.strip().splitlines()
                if l.strip() and not _is_runtime_state_path(l.split()[-1])
            ]
            if uncommitted:
                findings.append(
                    f"UNCOMMITTED: {len(uncommitted)} context file(s): "
                    + ", ".join(l.split()[-1] for l in uncommitted[:5])
                )
        except (subprocess.TimeoutExpired, OSError):
            pass

        return findings

    def _detect_knowledge_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_knowledge_staleness(root, ws_path)

    def _check_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_ddd_staleness(root, ws_path)

    @staticmethod
    def _detect_stale_memory_claims(memory_path: Path) -> list[str]:
        """Detect stale or inconsistent claims in MEMORY.md body.

        Mechanical checks only — no LLM needed.  Catches the class of bugs
        where facts change (feature shipped, concept eliminated, item resolved)
        but the memory entry still says otherwise.  COE03/C005 pattern.

        Checks:
        1. Open Threads body↔state: ✅ entries under active subsections
        2. Stale forward-references: "Next:", "TODO:", "NOT yet" in entries
           older than 14 days (likely completed but not updated)
        3. Index↔body count mismatch (caught structurally by index regen,
           but flagged here for visibility)
        """
        findings: list[str] = []
        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError:
            return findings

        # ── Check 1: ✅ entries in active OT subsections ──
        # These should only appear under "### Resolved" — if they're under
        # P0/P1/P2, someone resolved it but didn't move it.
        ot_match = re.search(
            r"## Open Threads\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if ot_match:
            ot_body = ot_match.group(1)
            # Split by ### subsections
            current_subsection = ""
            for line in ot_body.split("\n"):
                if line.startswith("### "):
                    current_subsection = line.strip()
                elif (
                    line.strip().startswith("- \u2705")
                    and "Resolved" not in current_subsection
                ):
                    title = line.strip()[:80]
                    findings.append(
                        f"STALE-OT: resolved entry in active section "
                        f"({current_subsection}): {title}"
                    )

        # ── Check 2: Stale forward-references in old entries ──
        # Patterns that suggest "this hasn't happened yet" in entries > 14d old
        stale_patterns = [
            (r"NOT yet (?:created|built|implemented|shipped)", "NOT yet"),
            (r"Next:\s+build\b", "Next: build"),
            (r"TODO:\s+\w", "TODO:"),
            (r"not yet built", "not yet built"),
            (r"\bdeferred\b|\bon hold\b", "deferred/on hold"),  # only flag if > 30d
        ]
        today = date.today()

        from core.ddd_entry_lifecycle import MEMORY_ACTIVE_SECTIONS, MEMORY_PERMANENT_SECTIONS
        _staleness_scan = [s for s in (*MEMORY_PERMANENT_SECTIONS, *MEMORY_ACTIVE_SECTIONS) if s != "Open Threads"]
        for section_name in _staleness_scan:
            # Extract section body
            sec_match = re.search(
                rf"## {re.escape(section_name)}\n(.*?)(?=\n## |\Z)", content, re.DOTALL
            )
            if not sec_match:
                continue

            for line in sec_match.group(1).split("\n"):
                line = line.strip()
                if not line.startswith("- "):
                    continue

                # Extract date from entry
                date_match = re.match(r"- (\d{4}-\d{2}-\d{2})", line)
                if not date_match:
                    continue

                try:
                    entry_date = datetime.strptime(
                        date_match.group(1), "%Y-%m-%d"
                    ).date()
                except ValueError:
                    continue

                age_days = (today - entry_date).days
                # "deferred/on hold" only stale after 30d, others after 14d
                for pattern, label in stale_patterns:
                    threshold = 30 if "deferred" in pattern else 14
                    if age_days > threshold and re.search(pattern, line, re.IGNORECASE):
                        title = line[2:72]  # strip "- ", cap at 70 chars
                        findings.append(
                            f"STALE-CLAIM: \"{label}\" in {section_name} "
                            f"entry ({age_days}d old): {title}..."
                        )
                        break  # one finding per entry

        return findings

    def _check_cache_freshness(self, context_dir: Path, findings: list[str]) -> None:
        """If any L1 SOURCE file is newer than the L1 cache, invalidate it.

        Scoped to ``CONTEXT_FILES`` — the exact files the loader assembles into
        L1 and checks in its own mtime freshness leg. A dir-wide ``*.md`` glob
        also matched non-L1 files rewritten every session (``user_suggestions.md``
        by user_observer_hook, read directly by prompt_builder, never part of L1)
        and so deleted a valid cache on every deep check.
        """
        from core.context_directory_loader import CONTEXT_FILES

        cache_file = context_dir / "L1_SYSTEM_PROMPTS.md"
        if not cache_file.exists():
            return

        cache_mtime = cache_file.stat().st_mtime
        for spec in CONTEXT_FILES:
            source = context_dir / spec.filename
            if not source.exists():
                continue
            if source.stat().st_mtime > cache_mtime:
                try:
                    cache_file.unlink()
                    findings.append(
                        f"AUTO-FIXED: invalidated L1 cache ({source.name} is newer)"
                    )
                except OSError:
                    findings.append(f"STALE-CACHE: L1 cache older than {source.name}")
                break  # Only need to invalidate once

    # ── Context Token Budget Measurement ─────────────────────────────

    # The truncatable context files that compose the system prompt (assembly order).
    # PROJECTS.md removed 2026-08-14 (in-prompt index deleted).
    _CONTEXT_FILES = (
        "SOUL.md", "AGENT.md", "USER.md", "STEERING.md", "TOOLS.md",
        "MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md",
    )
    # OBSERVABILITY thresholds (run_3f25a73a). These are NOT gates — the
    # assembly line does NOT truncate (XG directive 2026-06-28, pure-filesystem
    # recall design §3.5: "the read+assembly line DEFAULTS to trusting that the
    # files it loads are healthy and within spec"). These thresholds only emit a
    # WARNING/EMERGENCY finding to the deep-check report so the SEPARATE write-side
    # management line (decay/archive/trim — deferred #3) has a signal. WARNING is
    # anchored to the effective assembly budget; EMERGENCY is a clear-over.
    # DERIVED from the budget SoT in context_directory_loader (run_72ca2a97) — a
    # change to BUDGET_1M_MODEL or the ephemeral formula now propagates here
    # automatically (no hand-typed literal). WARNING = 141K, EMERGENCY = 180K.
    _WARNING_THRESHOLD = _CDL_EFFECTIVE_1M_BUDGET
    _EMERGENCY_THRESHOLD = _CDL_EFFECTIVE_1M_BUDGET + _CDL_EMERGENCY_MARGIN

    def _check_token_budget(self, context_dir: Path) -> list[str]:
        """Measure total token consumption across all 9 context files.

        Uses the CANONICAL ``ContextDirectoryLoader.estimate_tokens`` (the SAME
        estimator the prompt assembly uses) — NOT a local re-implementation.
        Before run_3f25a73a this method had its own ``cjk*1.5 + ascii/3.5``
        formula that diverged ~2.2x from the canonical on CJK (Gate-1 finding);
        adopting the canonical kills the divergence and means this number equals
        what actually enters the prompt. Emits WARNING/EMERGENCY (observability,
        not a gate — see threshold note above). Persists to
        self._token_measurement for the session briefing.
        """
        from core.context_directory_loader import ContextDirectoryLoader

        findings: list[str] = []
        total_tokens = 0
        file_tokens: dict[str, int] = {}

        for fname in self._CONTEXT_FILES:
            path = context_dir / fname
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            tokens = ContextDirectoryLoader.estimate_tokens(content)
            file_tokens[fname] = tokens
            total_tokens += tokens

        # Store measurement for external consumers (session briefing, optimizer job)
        self._token_measurement = {
            "total_tokens": total_tokens,
            "per_file": file_tokens,
            "warning_threshold": self._WARNING_THRESHOLD,
            "emergency_threshold": self._EMERGENCY_THRESHOLD,
            "over_budget": total_tokens > self._WARNING_THRESHOLD,
        }

        if total_tokens > self._EMERGENCY_THRESHOLD:
            sorted_files = sorted(file_tokens.items(), key=lambda x: -x[1])
            top3 = ", ".join(f"{f}({t})" for f, t in sorted_files[:3])
            findings.append(
                f"[context/budget] EMERGENCY: {total_tokens}/{self._EMERGENCY_THRESHOLD} "
                f"tokens (real calibrated estimate). Top: {top3}. Write-side trim "
                f"(decay/archive) needed — assembly does NOT truncate."
            )
        elif total_tokens > self._WARNING_THRESHOLD:
            sorted_files = sorted(file_tokens.items(), key=lambda x: -x[1])
            top3 = ", ".join(f"{f}({t})" for f, t in sorted_files[:3])
            findings.append(
                f"[context/budget] WARNING: {total_tokens}/{self._WARNING_THRESHOLD} "
                f"tokens (real calibrated estimate). Top: {top3}. Over assembly budget — "
                f"plan write-side trim (assembly injects full, does not truncate)."
            )

        return findings

    def _append_brain_size_snapshot(self, root: Path, context_dir: Path) -> None:
        """Append today's C&M brain size snapshot to the trend series (run_d0ba3f69).

        Reuses the numbers ``_check_token_budget`` just populated into
        ``self._token_measurement`` (prompt tokens + per-file), plus MEMORY.md's
        on-disk byte size. UPSERT-by-date via ``brain_size_series.append_snapshot``
        (idempotent — safe to re-run same-day across daemon restarts). Net-new
        series, NO backfill: the chart shows "collecting since launch" until it has
        enough points. Observability write — the caller wraps this in try/except so
        a snapshot failure never blocks the deep-check.
        """
        from core.brain_size_series import append_snapshot, SERIES_RELPATH

        measurement = getattr(self, "_token_measurement", None)
        if not isinstance(measurement, dict) or not measurement.get("total_tokens"):
            return  # budget wasn't measured this run — nothing to snapshot

        memory_path = context_dir / "MEMORY.md"
        memory_bytes = memory_path.stat().st_size if memory_path.is_file() else 0

        append_snapshot(
            root / SERIES_RELPATH,
            date_str=date.today().isoformat(),
            prompt_tokens=int(measurement.get("total_tokens", 0)),
            memory_bytes=int(memory_bytes),
            per_file=dict(measurement.get("per_file", {})),
        )

    # Matches EXPLICIT self-size claims only: a size SUBJECT label ("Context
    # files:", "this file", "file/context/prompt size", "self-size", "total
    # context") followed — on the same line, with no other number in between —
    # by "~N[K] tokens" / "≈ 30,000 tokens" / "152K tok". Used by
    # _check_self_report_drift.
    # A bare "N tokens" is NOT a self-claim: KNOWLEDGE.md documents budget
    # CONSTANTS ("EPHEMERAL_HEADROOM (9,000 tokens)", "= 141,000 tokens for
    # context files", "task_budget | 128K tokens"); the old any-number regex
    # compared those against the file's own size and fired a false drift.
    _SELF_REPORT_TOKEN_RE = re.compile(
        r"(?:context\s+files?|this\s+file|(?:file|context|prompt)\s+size"
        r"|self[- ]size|total\s+context)\b"
        r"[^\n\d]{0,20}?[~≈]?\s*([0-9][0-9,\.]*)\s*([KkMm])?\s*(?:tok\b|tokens?\b)",
        re.IGNORECASE,
    )

    def _check_self_report_drift(self, context_dir: Path) -> list[str]:
        """Catch context files that self-report a token size diverging from reality.

        A context file sometimes states its own size in prose (e.g. KNOWLEDGE.md:
        "Context files: ~44K tokens"). When the file actually grows, that claim
        goes stale and the agent then *believes* a wrong number about its own
        context (the exact drift that motivated run_3f25a73a — the system
        self-reported ~44K when the real calibrated size was ~152K).

        This scans each context file for embedded "~N[K] tokens" claims, compares
        each to the file's live calibrated estimate, and emits a WARNING finding
        when a claim diverges > DRIFT_PCT. OBSERVABILITY ONLY — never auto-edits a
        file (R3-8: the write/management line owns mutations, not this read-side
        health check).
        """
        from core.context_directory_loader import ContextDirectoryLoader

        DRIFT_PCT = 0.25
        findings: list[str] = []

        for fname in self._CONTEXT_FILES:
            path = context_dir / fname
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            live = ContextDirectoryLoader.estimate_tokens(content)
            if live <= 0:
                continue
            for m in self._SELF_REPORT_TOKEN_RE.finditer(content):
                num_s, suffix = m.group(1), m.group(2)
                try:
                    claimed = float(num_s.replace(",", ""))
                except ValueError:
                    continue
                if suffix and suffix.lower() == "k":
                    claimed *= 1_000
                elif suffix and suffix.lower() == "m":
                    claimed *= 1_000_000
                # Only meaningful for file-scale claims (skip tiny inline numbers
                # like "5 tokens" that aren't self-size claims).
                if claimed < 5_000:
                    continue
                divergence = abs(live - claimed) / max(live, 1)
                if divergence > DRIFT_PCT:
                    findings.append(
                        f"[context/self-report-drift] {fname}: claims "
                        f"~{int(claimed):,} tokens but live calibrated estimate is "
                        f"{live:,} ({int(divergence*100)}% off). Update the self-claim "
                        f"or trim the file — a stale self-size misleads the agent."
                    )
                    break  # one finding per file is enough signal
        return findings

    def _check_write_governance(self, context_dir: Path) -> list[str]:
        """Write-side governance health (R3). Zero-LLM.

        Flags duplicate/orphan "## Distilled" sections in MEMORY.md: the fallback
        target when a write went to a section that does not exist. Per-section
        entry caps are gone with the size valve (CDF r16.6 §12: nothing trims
        MEMORY.md; the injection selector bounds the prompt). A finding is a smoke
        alarm surfaced to the briefing, never an auto-mutation.
        """
        findings: list[str] = []
        memory_path = context_dir / "MEMORY.md"
        if not memory_path.exists():
            return findings
        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError:
            return findings
        distilled = content.count("\n## Distilled")
        if distilled:
            findings.append(
                f"[write-gov] {distilled} orphan '## Distilled' section(s) — "
                f"a write targeted a non-existent section (drift); reconcile into real sections"
            )
        return findings

    def _check_governance_budgets(self, root: Path, context_dir: Path) -> list[str]:
        """Enforce Three-Layer Governance budget limits.

        Counts principles in SOUL.md, rules in AGENT.md, and standing rules
        in STEERING.md. Warns if any exceed their hard cap.

        Budget caps (from design):
          - SOUL.md principles: ≤12
          - AGENT.md rules: ≤25
          - STEERING.md standing rules: ≤15
        """
        findings: list[str] = []

        # Check SOUL.md principle count (### P\d+: — require colon to avoid false matches)
        soul_path = context_dir / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8")
                principles = len(re.findall(r"^### P\d+:", content, re.MULTILINE))
                if principles > 12:
                    findings.append(
                        f"[governance/budget] SOUL.md principles OVER BUDGET: "
                        f"{principles}/12"
                    )
            except OSError:
                pass

        # Check AGENT.md rule count (R\d+\. at start of line)
        # Check workspace copy (the one agent uses at runtime)
        agent_path = context_dir / "AGENT.md"
        if agent_path.exists():
            try:
                content = agent_path.read_text(encoding="utf-8")
                rules = len(re.findall(r"^R\d+\.", content, re.MULTILINE))
                if rules > 25:
                    findings.append(
                        f"[governance/budget] AGENT.md rules OVER BUDGET: "
                        f"{rules}/25"
                    )
            except OSError:
                pass

        # Check STEERING.md standing rules (### headings under ## Standing Rules)
        steering_path = context_dir / "STEERING.md"
        if steering_path.exists():
            try:
                content = steering_path.read_text(encoding="utf-8")
                # Count ### sections under "## Standing Rules"
                in_standing = False
                rule_count = 0
                for line in content.splitlines():
                    if line.startswith("## Standing Rules"):
                        in_standing = True
                        continue
                    if in_standing and line.startswith("## ") and not line.startswith("## Standing"):
                        break
                    if in_standing and line.startswith("### "):
                        rule_count += 1
                if rule_count > 15:
                    findings.append(
                        f"[governance/budget] STEERING.md rules OVER BUDGET: "
                        f"{rule_count}/15"
                    )
            except OSError:
                pass

        return findings

    def _persist_findings(self, root: Path, findings: list[str]) -> None:
        """Write findings to health_findings.json for session briefing.

        The proactive intelligence system reads this file at session start
        to surface health alerts. Structured as:
        {
            "timestamp": "ISO8601",
            "findings": [{"level": "warning|info|critical", "message": "..."}],
            "memory_health": null  // populated by weekly maintenance job
        }
        """
        import json

        findings_dir = root / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True, exist_ok=True)
        findings_file = findings_dir / "health_findings.json"

        structured = []
        for f in findings:
            level = "critical" if f.startswith("EMPTY") else \
                    "warning" if any(f.startswith(p) for p in ("UNCOMMITTED", "STALE", "MISSING")) else \
                    "info"
            structured.append({"level": level, "message": f})

        data = {
            "timestamp": datetime.now().isoformat(),
            "findings": structured,
            "memory_health": None,  # Populated by weekly-maintenance job
        }

        try:
            # Merge memory_health from previous run (weekly job may have written it)
            if findings_file.exists():
                try:
                    prev = json.loads(findings_file.read_text(encoding="utf-8"))
                    if prev.get("memory_health"):
                        data["memory_health"] = prev["memory_health"]
                except (json.JSONDecodeError, OSError):
                    pass

            findings_file.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to persist health findings: %s", e)

    # ------------------------------------------------------------------
    # Retention Policies
    # ------------------------------------------------------------------

    def _enforce_retention_policies(self, ws_path: str) -> list[str]:
        """Enforce time-based archival and cleanup; return findings for the report.

        1. DailyActivity >90 days -> move to Knowledge/Archives/
        2. Archives >365 days -> delete (except MEMORY-archive-*.md)
        3. Open Threads with resolved marker >7 days -> reported only (MEMORY.md is
           written by CDF alone; the user archives them)
        """
        root = Path(ws_path)
        da_dir = root / "Knowledge" / "DailyActivity"
        archive_dir = root / "Knowledge" / "Archives"
        archive_dir.mkdir(parents=True, exist_ok=True)

        cutoff_90 = datetime.now() - timedelta(days=90)
        cutoff_365 = datetime.now() - timedelta(days=365)
        cutoff_7 = datetime.now() - timedelta(days=7)

        # 1. Archive old DailyActivity
        if da_dir.exists():
            for f in da_dir.glob("*.md"):
                try:
                    file_date = datetime.strptime(f.stem, "%Y-%m-%d")
                    if file_date < cutoff_90:
                        # No "distilled" gate: distillation is retired (CDF reads
                        # sessions directly), so nothing marks a day distilled.
                        dest = archive_dir / f.name
                        f.rename(dest)
                        logger.info("Archived DailyActivity: %s", f.name)
                except ValueError:
                    continue

        # 2. Delete old archives (except MEMORY-archive-*)
        # Note: MEMORY-archive-* files are double-protected:
        # (a) name prefix check skips them explicitly, and
        # (b) their stems (e.g. "MEMORY-archive-2026-04") fail strptime
        #     on [:10] slice ("MEMORY-arc"), so they'd be skipped anyway.
        if archive_dir.exists():
            for f in archive_dir.glob("*.md"):
                if f.name.startswith("MEMORY-archive-"):
                    continue  # Never delete memory archives
                try:
                    file_date = datetime.strptime(f.stem[:10], "%Y-%m-%d")
                    if file_date < cutoff_365:
                        f.unlink()
                        logger.info("Deleted old archive: %s", f.name)
                except (ValueError, IndexError):
                    continue

        # 3. Resolved Open Threads >7 days: reported, never moved (CDF single writer).
        memory_path = root / ".context" / "MEMORY.md"
        findings: list[str] = []
        if memory_path.exists():
            resolved = self._resolved_open_threads(memory_path, cutoff_7)
            if resolved:
                findings.append(
                    f"STALE: {len(resolved)} resolved Open Threads entr"
                    f"{'y' if len(resolved) == 1 else 'ies'} older than 7 days in MEMORY.md "
                    "(archive them by editing MEMORY.md)")
        return findings

    # ------------------------------------------------------------------
    # Open Thread archival
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ot_entry_date(line: str) -> Optional[datetime]:
        """Parse date from an Open Thread entry line. Returns None if unparseable."""
        # Format 1: ISO date at line start: "- 2024-03-22: ..."
        iso_start = re.match(r"- (\d{4}-\d{2}-\d{2})", line)
        if iso_start:
            try:
                return datetime.strptime(iso_start.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        # Format 2: ISO date anywhere in parens: "- ... (2024-03-22)"
        iso_any = re.search(r"\((\d{4}-\d{2}-\d{2})\)", line)
        if iso_any:
            try:
                return datetime.strptime(iso_any.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        # Format 3: Short month/day in parens: (3/22), (12/5)
        short_date = re.search(r"\((\d{1,2})/(\d{1,2})\)", line)
        if short_date:
            try:
                month = int(short_date.group(1))
                day = int(short_date.group(2))
                return datetime(datetime.now().year, month, day)
            except (ValueError, OverflowError):
                pass
        return None

    def _resolved_open_threads(self, memory_path: Path, cutoff: datetime) -> list[str]:
        """Resolved (✅) Open Threads entries dated before *cutoff* (read-only).

        Open Threads holds only what the user wrote (COE threads are computed on read,
        core.coe_threads), and CDF is the one writer of MEMORY.md: nothing here moves
        an entry. A resolved entry older than the cutoff is reported as a health
        finding so the user can archive it with governed_edit.
        """
        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("context_health: cannot read MEMORY.md for Open Threads: %s", exc)
            return []
        ot_match = re.search(r"(## Open Threads\n)(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if not ot_match:
            return []
        out: list[str] = []
        for line in ot_match.group(2).split("\n"):
            stripped = line.strip()
            if not stripped.startswith("- ") or "\u2705" not in stripped:
                continue
            entry_date = self._parse_ot_entry_date(stripped)
            if entry_date is not None and entry_date < cutoff:
                out.append(stripped)
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _git_rev(self, ws_path: str) -> Optional[str]:
        """Get current HEAD rev, or None."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _extract_title(filepath: Path) -> Optional[str]:
        """Read first markdown heading or YAML title from a file."""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                in_frontmatter = False
                for i, line in enumerate(f):
                    if i == 0 and line.strip() == "---":
                        in_frontmatter = True
                        continue
                    if in_frontmatter:
                        if line.strip() == "---":
                            in_frontmatter = False
                            continue
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip("\"'")
                            return title
                        continue
                    if line.startswith("# "):
                        return line[2:].strip()
                    if i > 15:
                        break
        except Exception:
            pass
        return None
