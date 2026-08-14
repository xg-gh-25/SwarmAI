"""Context Health Harness — keeps SwarmAI's brain accurate and current.

Single hook, two modes:
- **Light** (every session): refresh KNOWLEDGE.md + PROJECTS.md indexes
  if workspace changed since last refresh.
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

from core.initialization_manager import initialization_manager
from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)
from core.session_hooks import HookContext

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
        # Track last refresh git rev to skip no-op refreshes
        self._last_refresh_rev: Optional[str] = None
        # Dirty flag: set by _light_refresh when cultivation writes to DDD docs.
        # Consumed at end of _light_refresh to conditionally refresh PROJECTS.md.
        self._ddd_docs_modified: bool = False
        # Track Projects/ dir mtime to detect create/rename/delete without cultivation.
        self._last_projects_mtime: float = 0.0
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
        # (3s timeout per chunk), file I/O.  Run in thread pool so the
        # asyncio event loop stays responsive for FastAPI/SSE.
        loop = asyncio.get_running_loop()

        # ── Light: refresh indexes if workspace changed ──────────────
        await loop.run_in_executor(None, self._light_refresh, root, ws_path)

        # ── Deep: once per calendar day ──────────────────────────────
        today = date.today().isoformat()
        if self._last_deep_date != today:
            await loop.run_in_executor(None, self._deep_check, root, ws_path)
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
        # below (knowledge library / transcript / code_intel). These run in a
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

        # Reset dirty flag — will be set if any cultivation writes to DDD docs.
        self._ddd_docs_modified = False

        # Auto-cultivate pipeline lessons — promote REFLECT output into DDD docs
        # without requiring the agent to remember to run `run-cultivate` manually.
        try:
            if self._auto_cultivate_pipeline_lessons(root, _deadline=_cultivation_deadline):
                self._ddd_docs_modified = True
        except Exception as exc:
            logger.debug("context_health: auto-cultivation skipped: %s", exc)

        # Auto-cultivate session signals — promote corrections (Ch6) and
        # decisions (Ch5) from DailyActivity JSONL into DDD docs.
        try:
            if self._auto_cultivate_session_signals(root, _deadline=_cultivation_deadline):
                self._ddd_docs_modified = True
        except Exception as exc:
            logger.debug("context_health: session signal cultivation skipped: %s", exc)

        # T4: Maturity evidence update + promotion evaluation.
        # Runs AFTER cultivation so new changelog entries are counted.
        try:
            if self._update_maturity(root, _deadline=_cultivation_deadline):
                self._ddd_docs_modified = True
        except Exception as exc:
            logger.debug("context_health: maturity update skipped: %s", exc)

        # Memory usage tracking — scan recent DailyActivity for memory key
        # references ([RC04], [KD05], etc.) and write counts to
        # .context/.memory-usage.json.  Used by distillation for smart
        # eviction (lowest-usage entries evicted first instead of oldest).
        try:
            self._track_memory_usage(root)
        except Exception as exc:
            logger.debug("context_health: memory usage tracking skipped: %s", exc)

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

        # ── MEMORY.md lifecycle: ref bump + decay (same engine as DDD) ──
        # Extends ddd_entry_lifecycle to MEMORY.md. Same parse/bump/decay.
        try:
            self._run_memory_lifecycle(root)
        except Exception as exc:
            logger.debug("context_health: MEMORY.md lifecycle skipped: %s", exc)

        # ── KNOWLEDGE.md lifecycle: ref bump + decay (Gap #5) ──
        # Same engine as MEMORY.md — extends coverage to KNOWLEDGE.md.
        try:
            self._run_knowledge_lifecycle(root)
        except Exception as exc:
            logger.debug("context_health: KNOWLEDGE.md lifecycle skipped: %s", exc)

        # ── EVOLUTION.md lifecycle: decay + reclaim + dedup (run_2816ab1c) ──
        # Same engine as MEMORY/KNOWLEDGE — closes the last un-governed context
        # file. Only "Optimizations Learned" decays; fold_corrections (a separate
        # hook) still owns the Corrections narrative.
        try:
            self._run_evolution_lifecycle(root)
        except Exception as exc:
            logger.debug("context_health: EVOLUTION.md lifecycle skipped: %s", exc)

        # ── TTL proposals: archive stale queued proposals (Gap #22) ──
        try:
            self._expire_stale_proposals(root)
        except Exception as exc:
            logger.debug("context_health: proposal TTL skipped: %s", exc)

        # ── TTL golden-set skeletons: reclaim stale unrefined auto_seed drafts ──
        # The Darwinian end of auto_seed_case's lifecycle (run_9f5944b4). Mirrors
        # _expire_stale_proposals: an unrefined skeleton nobody refines accumulates
        # forever. reclaim_stale_skeletons is fail-safe (keeps on undecodable age)
        # and delegates deletion to hard_delete_cases; this call never blocks.
        try:
            from core.eval_service import get_eval_service
            reclaimed = get_eval_service().reclaim_stale_skeletons()
            if reclaimed:
                logger.info("context_health: reclaimed %d stale golden skeleton(s)",
                            len(reclaimed))
        except Exception as exc:
            logger.debug("context_health: skeleton reclaim skipped: %s", exc)

        # KNOWLEDGE.md text index refresh is git-gated (only reads git-tracked files)
        current_rev = self._git_rev(ws_path)
        if not (current_rev and current_rev == self._last_refresh_rev):
            try:
                self._refresh_knowledge_sync(root)
            except Exception as exc:
                logger.warning("context_health: KNOWLEDGE.md refresh failed: %s", exc)
            self._last_refresh_rev = current_rev

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

        # Transcript indexing (incremental, <10s) — P1 Memory Architecture v2.
        # Budget gate: the knowledge-library embed above can run long on a large
        # changeset; if we're past the refresh budget, defer the remaining heavy
        # syncs to the next session instead of overrunning the 30s executor timeout.
        if time.monotonic() <= _refresh_deadline:
            try:
                self._sync_transcript_index(root)
            except Exception as exc:
                logger.debug("context_health: transcript sync skipped: %s", exc)

            # Code Intelligence — incremental graph refresh (<2s typical changeset)
            if time.monotonic() <= _refresh_deadline:
                try:
                    self._refresh_code_intel(root)
                except Exception as exc:
                    logger.debug("context_health: code_intel refresh skipped: %s", exc)
            else:
                logger.info(
                    "context_health: refresh budget reached — deferring code_intel "
                    "refresh to next session",
                )
        else:
            logger.info(
                "context_health: refresh budget reached after knowledge sync — "
                "deferring transcript + code_intel indexing to next session",
            )

        # Refresh PROJECTS.md if: (a) cultivation modified DDD docs, or
        # (b) Projects/ directory itself changed (create/rename/delete).
        # This ensures system prompt always reflects current project state.
        projects_dir = root / "Projects"
        projects_changed = self._ddd_docs_modified
        if projects_dir.is_dir():
            current_mtime = projects_dir.stat().st_mtime
            if current_mtime != self._last_projects_mtime:
                projects_changed = True
                self._last_projects_mtime = current_mtime
        if projects_changed:
            try:
                self._refresh_projects_index_sync(root)
                self._refresh_knowledge_projects_section(root)
            except Exception as exc:
                logger.debug("context_health: PROJECTS.md refresh skipped: %s", exc)

        # Auto-update `updated:` date in design doc frontmatter when modified
        try:
            self._auto_update_doc_frontmatter(root)
        except Exception as exc:
            logger.debug("context_health: doc frontmatter auto-update skipped: %s", exc)

        # OS Eval canary — run programmatic golden set cases every session.
        # Zero LLM cost, <15s, surfaces regression in session briefing.
        # Must respect cultivation deadline — canary runs LAST in _light_refresh.
        try:
            self._run_eval_canary(root, _deadline=_cultivation_deadline)
        except Exception as exc:
            logger.debug("context_health: eval canary skipped: %s", exc)

    def _run_eval_canary(self, root: Path, *, _deadline: float = 0.0) -> None:
        """Run programmatic golden set cases (OS Eval canary).

        Two tiers for performance:
        - file_contains cases: every session (<1s total, pure string grep)
        - canary_pass cases: daily only (~10s, spawns Python subprocesses)

        Zero LLM cost. Results persisted to .context/.eval-canary.json
        so session briefing can surface failures as health signals.

        On failure: logs warning + writes failure details.
        On success: updates canary file with timestamp + score.
        Never blocks session start — all errors are swallowed.

        Args:
            _deadline: monotonic clock deadline from _light_refresh. If
                insufficient time remains, skip the canary to avoid being
                killed by the BackgroundHookExecutor 30s timeout.
        """
        # Budget check: skip if insufficient time remaining in the hook timeout.
        # _light_refresh has a 25s cultivation budget within a 30s hard timeout.
        # Canary runs LAST — if cultivation consumed significant time, bail.
        #
        # Thresholds:
        # - Daily tier (canary_pass + file_contains): needs ~12s for N subprocess
        #   spawns at ~1-1.5s each. 15s threshold provides headroom.
        # - Session-only tier (file_contains only): needs <1s (pure string grep).
        #   3s threshold is conservative.
        if _deadline > 0:
            remaining = _deadline - time.monotonic()
            # Determine threshold BEFORE loading golden set (avoid wasted work).
            # Daily check uses the higher threshold because canary_pass spawns
            # subprocesses; session-only uses the lower one (pure file reads).
            is_daily = self._last_deep_date != date.today().isoformat()
            min_budget = 15.0 if is_daily else 3.0
            if remaining < min_budget:
                logger.debug(
                    "context_health: eval canary skipped — only %.1fs remaining (need ≥%.0fs for %s tier)",
                    remaining, min_budget, "daily" if is_daily else "session",
                )
                return
        import sys
        # Import eval_runner from sibling scripts dir
        scripts_dir = Path(__file__).parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        try:
            from eval_runner import load_golden_set, run_eval, _golden_set_path
        except ImportError:
            logger.debug("context_health: eval_runner not importable, skipping canary")
            return

        gs_path = _golden_set_path(root)
        if not gs_path.exists():
            return

        golden_set = load_golden_set(gs_path)

        # Determine which cases to run based on frequency tier:
        # - file_contains: every session (fast, <1s)
        # - canary_pass: daily (slow, spawns subprocesses ~10s)
        all_programmatic = [
            c for c in golden_set["cases"] if c.get("eval_method") == "programmatic"
        ]

        is_daily = self._last_deep_date != date.today().isoformat()
        if is_daily:
            # Full programmatic sweep (daily)
            cases_to_run = all_programmatic
        else:
            # Fast tier only (every session): file_contains evaluator
            cases_to_run = [
                c for c in all_programmatic
                if "file_contains" in c.get("evaluators", [])
            ]

        if not cases_to_run:
            return

        golden_set_filtered = dict(golden_set)
        golden_set_filtered["cases"] = cases_to_run

        trigger = "session_canary" if not is_daily else "daily_canary"

        # Cap per-subprocess timeout to remaining budget (prevents blowing past
        # the BackgroundHookExecutor 30s deadline even if entry check passed).
        canary_timeout = None
        if _deadline > 0:
            remaining_for_cases = _deadline - time.monotonic()
            if remaining_for_cases > 0:
                # Divide remaining time across cases, min 3s per case
                per_case = max(3, int(remaining_for_cases / max(len(cases_to_run), 1)))
                canary_timeout = min(20, per_case)

        result = run_eval(golden_set_filtered, trigger, None, root,
                          canary_timeout=canary_timeout,
                          programmatic_only=True)

        # Persist canary result for session briefing
        canary_file = root / ".context" / ".eval-canary.json"
        import json as _json
        cases_error = result.get("cases_error", 0)
        canary_data = {
            "timestamp": result["triggered_at"],
            "total": result["total_cases"],
            "passed": result["cases_passed"],
            "failed": result["cases_failed"],
            "error": cases_error,
            "score": result["overall_score"],
            "duration_s": result["duration_seconds"],
        }

        if result["cases_failed"] > 0:
            # Include failure details for briefing (real regressions)
            canary_data["failures"] = [
                {"id": c["id"], "notes": c["notes"]}
                for c in result["cases"] if c["status"] == "failed"
            ]
            logger.warning(
                "context_health: eval canary FAILED %d/%d cases (score=%.1f%%)",
                result["cases_failed"], result["total_cases"], result["overall_score"],
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
        except OSError:
            pass

    def _auto_update_doc_frontmatter(self, root: Path) -> None:
        """Auto-update `updated:` field in docs/*.md that were modified this session.

        Checks git for uncommitted or recently-committed changes to docs/*.md,
        and updates the `updated:` frontmatter field to today's date if stale.
        Only touches files with existing YAML frontmatter (created:/updated:).

        FIX #1: Uses swarmai repo path (not SwarmWS workspace) since docs/ lives there.
        FIX #2: Only searches within YAML frontmatter block (between --- delimiters).
        FIX #4: Validates existing value is a YYYY-MM-DD date before replacing.
        """
        # docs/ lives in the swarmai repo, not SwarmWS. Resolution order:
        # 1. SWARMAI_DIR env var (explicit, works on all platforms)
        # 2. git rev-parse from CWD (works if CWD is inside swarmai repo)
        # 3. Standard macOS dev location (fallback)
        swarmai_dir = Path(os.environ.get("SWARMAI_DIR", "")).resolve()
        if not swarmai_dir.is_dir():
            # Try git-based discovery (works regardless of hardcoded paths)
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=2,
                    cwd=str(Path(__file__).parent.parent),  # backend/ dir is inside swarmai repo
                )
                if result.returncode == 0:
                    swarmai_dir = Path(result.stdout.strip())
            except (subprocess.TimeoutExpired, OSError):
                pass
        if not swarmai_dir.is_dir():
            swarmai_dir = Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"
        docs_dir = swarmai_dir / "docs"
        if not docs_dir.is_dir():
            return

        today_str = date.today().isoformat()
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

        # Find docs modified in working tree (staged + unstaged)
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD", "--", "docs/"],
                capture_output=True, text=True, timeout=3,
                cwd=str(swarmai_dir),
            )
            modified = [
                swarmai_dir / line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip().endswith(".md")
            ]
        except (subprocess.TimeoutExpired, OSError):
            return

        for filepath in modified:
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue

            # FIX #2: Only search within frontmatter block (between first two ---)
            fm_end = content.find("---", 3)
            if fm_end == -1:
                continue
            frontmatter = content[:fm_end]

            # Find updated: field within frontmatter only
            match = re.search(r"^updated:\s*(.+)$", frontmatter, re.MULTILINE)
            if not match:
                continue

            existing_value = match.group(1).strip().strip('"').strip("'")

            # FIX #4: Only replace if existing value is a valid date format
            if not date_re.match(existing_value):
                continue

            if existing_value == today_str:
                continue

            # Replace within the full content using the match position (safe because
            # match is within frontmatter which is a prefix of content)
            new_content = content[:match.start()] + f"updated: {today_str}" + content[match.end():]
            filepath.write_text(new_content, encoding="utf-8")
            logger.debug("context_health: auto-updated frontmatter date in %s", filepath.name)

    def _refresh_projects_index_sync(self, root: Path) -> None:
        """Sync wrapper: regenerate PROJECTS.md after cultivation modified DDD docs.

        Called from run_in_executor thread (no active event loop on this thread).
        Creates a fresh event loop for the async workspace manager call.

        Note: The module-level _cultivation_write_lock (asyncio.Lock) provides no
        mutual exclusion between this loop and the main FastAPI loop — but both
        produce identical deterministic output from the same filesystem state, so
        last-writer-wins is safe (no data loss, just redundant work).
        """
        from core.swarm_workspace_manager import swarm_workspace_manager

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                swarm_workspace_manager.refresh_projects_index(str(root))
            )
            logger.info("context_health: PROJECTS.md refreshed after cultivation")
        finally:
            loop.close()

    def _refresh_knowledge_projects_section(self, root: Path) -> None:
        """Auto-rebuild 'Active Projects & DDD' section in KNOWLEDGE.md.

        Replaces the hand-maintained project list with a filesystem-derived one.
        This ensures KNOWLEDGE.md always reflects current project names without
        manual editing on create/rename/delete.
        """
        knowledge_file = root / ".context" / "KNOWLEDGE.md"
        if not knowledge_file.exists():
            return

        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        # THE single-source line builder lives in ddd_bindings.describe_project_ddd_line
        # (run_99b70b3c R25 convergence): the SESSION_CLOSE orchestrator channel
        # (ddd_orchestrator._ch_inject_knowledge) writes the SAME section and MUST
        # produce byte-identical output, else the two writers clobber/churn each other.
        # This writer owns the freshness suffix (computed from doc mtime); it passes
        # the freshness string in, the shared helper handles tag + structure markers.
        try:
            from core.ddd_bindings import describe_project_ddd_line
        except Exception:  # pragma: no cover - defensive import
            describe_project_ddd_line = None  # type: ignore[assignment]
        if describe_project_ddd_line is None:
            return

        ddd_files = DDD_CANONICAL_DOCS
        project_lines = []
        now = time.time()
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            # Resolve via ddd_path — docs live under 2-understanding/ post-ad7f6623.
            # Cache the resolved paths so we hit the resolver once per doc, not twice.
            doc_paths = [p for p in (ddd_path(d, f) for f in ddd_files) if p.exists()]
            if not doc_paths:
                continue
            # Freshness from most recent DDD doc mtime (this writer's contribution).
            mtimes = [p.stat().st_mtime for p in doc_paths]
            days_ago = int((now - max(mtimes)) / 86400) if mtimes else 999
            if days_ago == 0:
                freshness = "today"
            elif days_ago <= 7:
                freshness = f"{days_ago}d ago"
            else:
                freshness = f"**{days_ago}d stale**"
            try:
                line = describe_project_ddd_line(d, freshness=freshness)
            except Exception:
                line = None
            if line:
                project_lines.append(line)

        if not project_lines:
            return

        # Build new section content
        new_section = "### Active Projects & DDD\n\n" + "\n".join(project_lines) + "\n"

        # Replace existing section in KNOWLEDGE.md — under the shared
        # KNOWLEDGE.md.lock (run_a1ec08e7: all KNOWLEDGE writers must serialize,
        # else this read-modify-write clobbers the decay reclaim or the DDD
        # orchestrator's write of this same section). Blocking, idempotent.
        from utils.file_lock import md_lock
        with md_lock(knowledge_file, blocking=True):
            content = knowledge_file.read_text()
            # Match from "### Active Projects & DDD" to next ### or ## heading
            pattern = r"### Active Projects & DDD\n.*?(?=\n###|\n##[^#]|\Z)"
            if re.search(pattern, content, re.DOTALL):
                content = re.sub(pattern, new_section.rstrip(), content, count=1, flags=re.DOTALL)
            else:
                # Section doesn't exist yet — insert before "## The 11 Context Files" or at end
                insert_before = "## The 11 Context Files"
                if insert_before in content:
                    content = content.replace(insert_before, new_section + "\n\n" + insert_before)
                else:
                    content += "\n\n" + new_section

            knowledge_file.write_text(content)
        logger.info("context_health: KNOWLEDGE.md Active Projects section refreshed")

    def _refresh_code_intel(self, root: Path) -> None:
        """Refresh code_intel.db if the indexed commit is behind HEAD."""
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        from core.code_intel import load_project_graph
        from core.code_intel.freshness import check_freshness

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            db_path = project_dir / "code_intel.db"
            if not db_path.exists():
                continue

            graph = load_project_graph(project_dir.name)
            if not graph:
                continue

            freshness = check_freshness(graph)
            if not freshness.stale:
                continue

            if freshness.suggest_full_rebuild:
                logger.info(
                    "code_intel %s: %d commits behind, %d files — triggering background rebuild",
                    project_dir.name, freshness.commits_behind,
                    len(freshness.changed_files),
                )
                # Emit event for background full reindex (non-blocking).
                # Uses emit_event_atomic (fcntl-locked load→append→save) to
                # avoid the race where this hook's stale state overwrites the
                # scheduler's successful job updates. See scheduler.emit_event_atomic.
                try:
                    from jobs.scheduler import emit_event_atomic
                    emit_event_atomic("code_intel_full_reindex", data={
                        "project": project_dir.name,
                        "commits_behind": freshness.commits_behind,
                        "files_changed": len(freshness.changed_files),
                    })
                except Exception as emit_err:
                    logger.debug("code_intel: failed to emit reindex event: %s", emit_err)
                continue  # don't block session start — background job handles it

            # Incremental update for small changes
            from core.code_intel.parser import parse_file
            from pathlib import Path as P

            repo_root = P(graph.get_meta("repo_root") or "")
            if not repo_root.is_dir():
                continue

            # OWNERSHIP GUARD (run_1950e67e): the THIRD index-trigger site (with the
            # startup watcher + reindex job). Never incrementally index a repo_root
            # the project doesn't OWN (its own TECH.md must declare it) — else a
            # foreign/mis-seeded repo_root re-indexes another project's files into
            # this brain (the IVTHub-indexed-SwarmAI contamination). R27 all-consumers.
            from core.code_intel import repo_root_is_owned
            if not repo_root_is_owned(project_dir, str(repo_root)):
                logger.warning(
                    "code_intel: %s repo_root %s not owned by project — skipping "
                    "incremental index (cross-project contamination guard)",
                    project_dir.name, repo_root)
                continue

            for rel_path in freshness.changed_files[:50]:  # cap at 50
                full_path = repo_root / rel_path
                if full_path.exists():
                    # P1-7: Isolate per-file errors so one bad file doesn't skip the rest
                    try:
                        result = parse_file(full_path, repo_root)
                        if result.nodes:
                            file_hash = result.nodes[0].sha256 or ""
                            graph.store_file_nodes_edges(
                                rel_path, result.nodes, result.edges, file_hash
                            )
                    except Exception as file_err:
                        logger.debug("code_intel: failed to parse %s: %s", rel_path, file_err)
                else:
                    # File was deleted — remove stale nodes/edges
                    graph.remove_file(rel_path)

            graph.rebuild_fts()
            if freshness.current_head:
                graph.set_meta("last_indexed_commit", freshness.current_head)
            # Reclaim WAL disk space — this incremental path writes via
            # store_file_nodes_edges + rebuild_fts (not incremental_update), so it
            # must checkpoint too, else the -wal file re-bloats (Gate-2 finding E).
            graph.checkpoint_truncate()

            logger.info(
                "code_intel %s: incremental update — %d files refreshed",
                project_dir.name, len(freshness.changed_files),
            )

        # Run 4b (run_2bad039d, §8.6): after refreshing code_intel, SIGNAL any
        # spec-details that went stale vs code-intel.json (domains regenerated but
        # the .spec.md projection not). Detection is core + pure; regeneration is
        # skill-owned (project_domain_skeleton), so we emit a signal rather than
        # import the projected skill (C046 core→skill boundary). Non-blocking.
        self._signal_stale_spec_details(projects_dir)

    def _signal_stale_spec_details(self, projects_dir: Path) -> None:
        """Signal spec-details staleness for each project whose specs no longer
        match their domain content-hash (freshness.detect_spec_details_staleness,
        CONTENT-based since run_fe26ed6c — NOT mtime). Two surfaces:

        1. A LOG line (always).
        2. An ESCALATION file via escalation.save_escalation — a REAL consumable
           surface the operator acts on, surfaced in the Need-You panel by
           attention_authority._collect_escalations (Level.CONSULT → REVIEW tier).
           This is the loop-closing consumer the earlier LOG-only design
           (run_2bad039d) deferred for lack of one: regeneration is skill-owned
           (s_repo-to-ddd, LLM-in-agent, C046), and the escalation is exactly the
           operator trigger for it. We deliberately do NOT emit a
           `spec_details_stale` EVENT — that would be the sink-less write-only
           signal Gate-2 (run_2bad039d) correctly rejected; an escalation has a
           human consumer (Need-You), an event has none.

        NOTE (run_50db230a): this signal was moved OFF the user ToDo surface. The
        ToDo card is now a pure user-planning surface — system signals like
        spec-drift live in their own home (the escalation file → Need-You), never
        as an auto-written todo.

        Dedup: the escalation's deterministic id ``spec_details_stale:<project>``
        overwrites in place (save_escalation is atomic write-by-id), so a re-run
        refreshes the same file rather than spamming. Fail-open: any escalation
        error leaves the LOG as the surviving signal, never blocks session start."""
        try:
            from core.code_intel.freshness import detect_spec_details_staleness
        except Exception:  # pragma: no cover - defensive import
            return
        # projects_dir is <workspace_root>/Projects — its parent is the workspace
        # root that save_escalation needs to locate .artifacts/escalations/.
        workspace_root = projects_dir.parent
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            try:
                stale = detect_spec_details_staleness(project_dir)
            except Exception as exc:  # never block session start on detection
                logger.debug("spec-details staleness check failed for %s: %s",
                             project_dir.name, exc)
                continue
            if not stale:
                continue
            logger.info(
                "spec-details STALE in %s: %d spec(s) drifted from domain content "
                "(%s) — regenerate via s_repo-to-ddd (skill-owned, manual)",
                project_dir.name, len(stale), ", ".join(stale[:5]),
            )
            self._create_spec_stale_escalation(workspace_root, project_dir.name, stale)

    def _create_spec_stale_escalation(
        self, workspace_root: Path, project: str, stale: list[str]
    ) -> None:
        """Persist a spec-drift escalation (surfaced in Need-You as REVIEW) prompting
        spec-details regeneration. Fail-open — an escalation error must never break
        session-start health refresh. The signal's home is the escalation file, NOT
        the user ToDo surface (run_50db230a)."""
        try:
            from core.escalation import Escalation, Level, save_escalation
            specs = ", ".join(stale[:5]) + (" …" if len(stale) > 5 else "")
            esc = Escalation(
                # id MUST start with `esc_` — get_open_escalations globs `esc_*.json`,
                # so a non-prefixed id would be written but never read (the signal would
                # be silently lost). Deterministic suffix keeps dedup: same project →
                # same file → save_escalation atomically overwrites (no spam).
                id=f"esc_spec_details_stale__{project}",
                level=Level.CONSULT,  # override-window advisory, not a hard BLOCK
                trigger="CONTRADICTS_LESSON",  # closest existing type (spec ↔ code drift)
                title=f"spec-details drifted in {project} ({len(stale)} spec(s))",
                situation=(f"{len(stale)} spec-details file(s) in {project} no longer "
                           f"match their domain content-hash ({specs}). The code-intel "
                           f"domain layer changed since these specs were projected."),
                recommendation="Regenerate via s_repo-to-ddd (preserves [human] §5 blocks)",
                project=project,
                pipeline_stage="",
            )
            # Atomic write-by-id: re-running refreshes the same file (no spam).
            save_escalation(workspace_root, esc)
        except Exception as exc:  # noqa: BLE001 — fail-open (log survives as signal)
            logger.debug("spec-stale escalation creation failed for %s: %s", project, exc)

    # ------------------------------------------------------------------
    # Auto-cultivation — promote REFLECT lessons into DDD docs
    # ------------------------------------------------------------------

    def _auto_cultivate_pipeline_lessons(self, root: Path, *, _deadline: float = 0) -> bool:
        """Auto-cultivate uncultivated pipeline REFLECT lessons into DDD docs.

        Scans all Projects/*/.artifacts/runs/*/run.json for completed pipeline
        runs that have reflect.lessons populated but no cultivated:true flag.
        For each, calls cultivate_from_reflect() to auto-apply safe additive
        lessons and escalate risky ones, then marks the run as cultivated.

        This replaces the manual `run-cultivate` CLI call that the agent had
        to remember (and failed 100% of the time — 141 runs, 0 cultivated).

        Capped at 5 cultivations per session AND 25s cooperative time budget.
        The hook executor enforces a 30s timeout via asyncio.wait_for, but that
        cannot actually cancel a thread-pool thread in CPython — it just stops
        waiting while the thread continues silently. The cooperative budget bails
        early so the hook finishes cleanly within the executor's window.

        Remaining uncultivated runs are processed in subsequent sessions.

        Returns:
            True if any DDD docs were modified (applied > 0), False otherwise.
        """
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return False

        from core.ddd_cultivation import cultivate_from_reflect

        _MAX_PER_SESSION = 5
        # PE-4: use shared deadline from _light_refresh (25s total for all cultivation)
        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 25.0)
        cultivated_count = 0
        any_applied = False  # Track if any DDD docs were actually modified

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            runs_dir = project_dir / ".artifacts" / "runs"
            if not runs_dir.is_dir():
                continue

            # Sort by mtime (oldest first) to ensure FIFO processing.
            # Filter to last 30 days — older uncultivated runs are stale and
            # won't produce useful DDD content. Also bounds scan cost to O(recent)
            # instead of O(total history) as pipelines accumulate.
            # Cache stat to avoid double syscall (meta-review finding).
            mtime_cutoff = time.time() - 30 * 86400
            run_items = [
                (d, d.stat().st_mtime)
                for d in runs_dir.iterdir()
                if d.is_dir()
            ]
            run_dirs = sorted(
                ((d, mt) for d, mt in run_items if mt > mtime_cutoff),
                key=lambda x: x[1],
            )

            for run_dir, _ in run_dirs:
                # Cooperative time budget — bail cleanly before hook timeout
                if time.monotonic() > _effective_deadline:
                    logger.info(
                        "context_health: auto-cultivate hit shared deadline, "
                        "deferring remaining to next session",
                    )
                    break

                run_file = run_dir / "run.json"
                if not run_file.exists():
                    continue

                try:
                    run_data = json.loads(run_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    logger.debug(
                        "context_health: auto-cultivate skipped corrupt %s", run_file
                    )
                    continue

                # Only cultivate runs that have reached a TERMINAL status. An
                # in-flight run (running/paused) may still write more reflect
                # lessons, and consuming it now would permanently mark it
                # cultivated=True (:768) → the MEMORY mirror would be held back
                # as "unqualified run" (run_qualified gate) and NEVER retried
                # after completion. Leaving it un-cultivated lets a later session
                # re-process it once status is terminal. (Gate-2, run_f73a33e2)
                _TERMINAL = ("completed", "abandoned", "cancelled", "blocked", "failed")
                if run_data.get("status") not in _TERMINAL:
                    continue

                # Find reflect stage with lessons
                reflect_stage = None
                reflect_idx = -1
                for idx, stage in enumerate(run_data.get("stages", [])):
                    if stage.get("stage") == "reflect":
                        reflect_stage = stage
                        reflect_idx = idx
                        break

                if reflect_stage is None:
                    continue
                if reflect_stage.get("cultivated"):
                    continue  # Already done
                lessons = reflect_stage.get("lessons", [])
                if not lessons:
                    continue  # Nothing to cultivate

                # Cap per session to keep _light_refresh fast
                if cultivated_count >= _MAX_PER_SESSION:
                    break

                # Cultivate
                project_name = project_dir.name
                try:
                    run_id = run_data.get("id", run_dir.name)
                    result = cultivate_from_reflect(
                        lessons, run_id, project_name, project_dir
                    )

                    # Atomic write: mark cultivated in run.json via tmp+replace
                    run_data["stages"][reflect_idx]["cultivated"] = True
                    tmp_file = run_file.with_suffix(".tmp")
                    tmp_file.write_text(
                        json.dumps(run_data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    os.replace(tmp_file, run_file)
                    cultivated_count += 1

                    if result.get("applied", 0) > 0:
                        any_applied = True

                    # ── Event extraction: REFLECT → MEMORY.md (immediate) ──
                    # Pipeline REFLECT lessons also go to MEMORY.md as
                    # structured entries. Same engine, parallel destination.
                    # Only extract high-confidence lessons (those that were
                    # applied to DDD — if DDD accepted them, they're confident).
                    if result.get("applied", 0) > 0:
                        try:
                            self._extract_lessons_to_memory(
                                root, lessons, run_id, project_name,
                            )
                        except Exception:
                            pass  # Best-effort — DDD cultivation is primary

                    logger.info(
                        "context_health: auto-cultivated %s/%s — "
                        "applied=%d, escalated=%d, rejected=%d",
                        project_name, run_id,
                        result.get("applied", 0),
                        result.get("escalated", 0),
                        result.get("rejected", 0),
                    )
                    # Section-name drift = config bug (lesson dropped), not a
                    # benign rejection — log at error level so it surfaces.
                    for _drift in result.get("drift_errors", []):
                        logger.error("context_health: %s", _drift)
                except Exception as exc:
                    logger.warning(
                        "context_health: auto-cultivate failed for %s/%s: %s",
                        project_name, run_dir.name, exc,
                    )

            if cultivated_count >= _MAX_PER_SESSION:
                break
            if time.monotonic() > _effective_deadline:
                break

        if cultivated_count > 0:
            logger.info(
                "context_health: auto-cultivated %d pipeline run(s)", cultivated_count
            )

        return any_applied

    def _extract_lessons_to_memory(
        self, root: Path, lessons: list[str], run_id: str, project: str,
    ) -> None:
        """Extract REFLECT lessons to MEMORY.md through the SSOT admission gate.

        Every lesson routes through ``ingestion_gate.admit_memory_lesson`` — the ONE
        MEMORY door all writers share (P8). That gate runs the deterministic HARD-DENY
        floors FIRST (structural-noise → thin → content_floor[confidence/governance] →
        keep_type_holdback), then the LLM judge, so the brain is guarded even when the
        judge is unavailable. Verdicts:
          • auto    → written to the judge-routed section (dedup inside the write lock).
          • pending → RECOVERABLE (judge budget-exhausted / infra down, or a keep-type
            held while the judge is down) → deferred to distill-pending.jsonl for a
            fresh-budget re-judge next cycle; NEVER dropped, NEVER auto-written.
          • discard → a real refusal (a deterministic floor, or the judge online saying
            suspect/noise) → not written (the source lesson still lives upstream).
        (The former ``run_qualified`` trust param + the ``_admit_lesson_to_memory``
        code-side stand-in were removed — the SSOT gate is the sole authority now.)
        """

        memory_path = root / ".context" / "MEMORY.md"
        if not memory_path.exists():
            return

        today = date.today().isoformat()

        # Route each lesson to its correct section via the shared route_lesson_type
        # SSOT (imported at the per-lesson site below).
        # Group lessons by target section. Each candidate carries its title so
        # step-6 dedup can compare against existing section entries INSIDE the
        # lock (against the locked snapshot, not a stale pre-lock read).
        from collections import defaultdict
        by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)

        from core.ddd_entry_lifecycle import route_lesson_type, _DECLARED_TYPE_RE, VALID_TYPES
        for lesson in lessons[:3]:
            # JUDGE GATE (run_04fd397c, XG decision A): this reflection→MEMORY path
            # was a BACKDOOR — it used _admit_lesson_to_memory (a code-side Step-0
            # STAND-IN), NOT the self_adversarial judge, so "the judge is the sole
            # admit authority" (P8) was false here. Now routes through the SAME
            # admit_memory_lesson every other MEMORY door uses. verdict=="auto" →
            # write to the judge-routed section; fail-closed (judge error/suspect/
            # noise) → discard (dropped, logged, never a human sink).
            try:
                from core.ingestion_gate import admit_memory_lesson
                verdict, target_section, reason, distilled = admit_memory_lesson(lesson)
            except Exception as exc:
                logger.warning(
                    "context_health: MEMORY judge crashed, DISCARD "
                    "lesson (%s: %s): %.80s", type(exc).__name__, exc, lesson,
                )
                continue
            if verdict == "pending":
                # RECOVERABLE (judge budget-exhausted / infra down / keep-type held while
                # the judge is unavailable) — DEFER, never drop. Lands in the shared
                # distill-pending.jsonl for a fresh-budget re-judge next cycle. This is
                # the fix for the silent-drop bug: the SSOT door used to collapse
                # pending→discard, losing every reflection lesson once the window filled.
                try:
                    from hooks.distillation_hook import requeue_pending_lesson
                    requeue_pending_lesson(root / ".context", "lesson", lesson)
                    logger.info("context_health: MEMORY lesson deferred (%s): %.80s",
                                reason, lesson)
                except Exception as exc:  # noqa: BLE001 — defer is best-effort, never break
                    logger.warning("context_health: pending defer FAILED (%s), lesson "
                                   "NOT lost from source: %.80s", type(exc).__name__, lesson)
                continue
            if verdict != "auto" or not target_section:
                logger.info(
                    "context_health: judge DISCARD MEMORY lesson (%s): %.80s",
                    reason, lesson,
                )
                continue
            # ROOT-FIX (capture-vs-distill): write the DISTILLED rule when the gate
            # rewrote a shape-dirty lesson; fail-open → distilled=None keeps original.
            lesson = distilled or lesson
            # entry_type from the type router. route_lesson_type now HONORS an
            # author-declared leading [type] tag (run_4ad5a44b), so pass the
            # tag-bearing lesson — the declared type wins over a keyword guess.
            _, entry_type = route_lesson_type(lesson)
            # Strip the leading [type] prefix from the BODY before embedding, or it
            # is written THREE times (auto [entry_type] + in title-split + in body)
            # — the triple-tag pollution that produced malformed "- [guideline]
            # **[pitfall] X** — [pitfall] X" entries (run_4ad5a44b bug b).
            # Strip ONLY a VALID type tag (same guard as route_lesson_type's honor):
            # an unconditional .sub() would corrupt a lesson whose body legitimately
            # opens with a non-type bracket like "[TODO] ..." or "[2026] ..."
            # (Gate-2 CRITICAL, run_4ad5a44b).
            _dm = _DECLARED_TYPE_RE.match(lesson)
            lesson_body = (
                _DECLARED_TYPE_RE.sub("", lesson, count=1)
                if _dm and _dm.group(1).lower() in VALID_TYPES
                else lesson
            )
            title = lesson_body.split("—")[0].strip() if "—" in lesson_body else lesson_body[:60]
            title = title.rstrip(".")
            entry_line = f"- [{entry_type}] **{title}** — {lesson_body} ({today}, {run_id})"
            meta_line = f"  <!-- ref:0 | last:{today} | decay:active -->"
            by_section[target_section].append((title, f"{entry_line}\n{meta_line}"))

        if not by_section:
            return

        # Insert each group at the section boundary via _modify_content (prepend).
        # R-1 (run_55c02bbe): the prior raw-splice insert math had an off-by-one
        # that landed the new entry+meta BETWEEN an existing bullet and its meta,
        # orphaning the existing meta as a 2nd consecutive line (120 stacks in
        # live MEMORY.md). _modify_content inserts at a clean section boundary
        # (right after the header), which structurally cannot orphan an existing
        # entry's meta. PREPEND (not append) keeps newest-at-TOP — the convention
        # distillation_hook._write_section uses for these same sections, and the
        # one _enforce_section_caps relies on (it trims the BOTTOM = oldest).
        # Reuses the same locked writer distillation uses (R-1 Gate-2 #2/#3).
        from scripts.locked_write import _modify_content

        # R-1 Gate-2 meta-review (MED interaction): MEMORY.md is concurrently
        # written by distillation + _run_memory_lifecycle, both under MEMORY.md.lock.
        # This writer previously read+wrote UNLOCKED → lost-update race. Take the
        # SAME lock and read INSIDE it so the modify is computed from the locked
        # snapshot. Non-blocking: if busy, skip (best-effort — caller wraps in
        # try/except and DDD cultivation is the primary destination).
        from utils.file_lock import flock_exclusive_nb, flock_unlock
        lock_path = memory_path.with_suffix(".md.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive_nb(lock_fd)
        except OSError:
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: MEMORY.md lock busy, skipping lesson extract")
            return

        try:
            content = memory_path.read_text(encoding="utf-8")
            # Step 6 — dedup INSIDE the lock, against the locked snapshot. An
            # exact/normalized-title match with an existing entry in the SAME
            # target section → HOLD-BACK (benign skip). Uses parse_entries (the
            # canonical MEMORY.md parser); no embeddings (that path is removed
            # dead code). Normalization = casefold + strip so trivial variants
            # collide.
            from core.ddd_entry_lifecycle import parse_entries

            def _norm(t: str) -> str:
                return t.strip().casefold()

            existing_by_section: dict[str, set[str]] = defaultdict(set)
            for e in parse_entries(content):
                if e.section and e.title:
                    existing_by_section[e.section].add(_norm(e.title))

            wrote = 0
            for section_name, candidates in by_section.items():
                seen = existing_by_section[section_name]
                fresh_blocks = []
                for title, block in candidates:
                    key = _norm(title)
                    if key in seen:
                        logger.info(
                            "context_health: HOLD-BACK MEMORY lesson (duplicate "
                            "of existing '%s' in %s): %.60s",
                            title, section_name, block,
                        )
                        continue
                    seen.add(key)  # also dedups within this same batch
                    fresh_blocks.append(block)
                if not fresh_blocks:
                    continue
                new_block = "\n".join(fresh_blocks)
                content = _modify_content(content, section_name, new_block, "prepend")
                wrote += len(fresh_blocks)

            if wrote:
                # Reindex IN-LOCK via the SAME pure function the facade uses
                # NEW ARCHITECTURE (2026-08-14): the in-prompt index was DELETED
                # (live MEMORY is full-injected; recall scans body-BM25). No index
                # to maintain — the write persists the entry directly.
                memory_path.write_text(content, encoding="utf-8")
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

        logger.debug(
            "context_health: extracted lessons to MEMORY.md from %s/%s",
            project, run_id,
        )

    # ------------------------------------------------------------------
    # Auto-cultivation — promote session corrections + decisions into DDD
    # ------------------------------------------------------------------

    # State file: tracks which JSONL session records have been cultivated
    _SESSION_CULTIVATED_STATE = ".context/.session_cultivated.json"

    def _auto_cultivate_session_signals(self, root: Path, *, _deadline: float = 0) -> bool:
        """Auto-cultivate corrections and decisions from DailyActivity JSONL into DDD docs.

        Reads recent DailyActivity JSONL sidecars (last 7 days), extracts
        corrections (Ch6 — highest priority) and decisions (Ch5), feeds them
        through the same keyword classifier used by pipeline REFLECT cultivation.

        Idempotency: tracks cultivated session_ids in a state file. Each session
        is processed at most once. State file is a simple JSON list of session IDs.

        Capped at 10 sessions per invocation, sharing the same cooperative time
        budget mindset as pipeline cultivation.

        Returns:
            True if any DDD docs were modified (total_applied > 0), False otherwise.
        """
        da_dir = root / "Knowledge" / "DailyActivity"
        if not da_dir.is_dir():
            return False

        from core.ddd_cultivation import cultivate_from_corrections, cultivate_from_decisions

        # Load cultivated state (ordered list of session_ids already processed).
        # Uses a list to preserve insertion order — capping takes oldest first.
        state_path = root / self._SESSION_CULTIVATED_STATE
        cultivated_list: list = []
        if state_path.is_file():
            try:
                cultivated_list = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(cultivated_list, list):
                    cultivated_list = []
            except (json.JSONDecodeError, OSError):
                cultivated_list = []
        cultivated_ids: set = set(cultivated_list)  # O(1) lookup

        # Scan JSONL sidecars from last 7 days
        today = date.today()
        cutoff = today - timedelta(days=7)

        jsonl_files = sorted(da_dir.glob("*.jsonl"))
        # Filter to recent files by filename date prefix
        recent_jsonls = []
        for jf in jsonl_files:
            try:
                file_date = date.fromisoformat(jf.stem[:10])
                if file_date >= cutoff:
                    recent_jsonls.append(jf)
            except (ValueError, IndexError):
                continue

        if not recent_jsonls:
            return False

        # Determine project directory for cultivation target.
        # Default to SwarmAI (the workspace project — corrections about the
        # agent itself are the most common signal). Future: infer from session
        # topics or files_modified.
        projects_dir = root / "Projects"
        default_project = "SwarmAI"
        default_project_dir = projects_dir / default_project
        if not default_project_dir.is_dir():
            return False

        _MAX_SESSIONS = 10
        # PE-4: use shared deadline from _light_refresh (25s total for all cultivation)
        _effective_deadline = _deadline if _deadline > 0 else (time.monotonic() + 15.0)
        processed = 0
        total_applied = 0
        total_escalated = 0

        from core.daily_activity_writer import read_jsonl_sidecar

        for jsonl_path in recent_jsonls:
            if processed >= _MAX_SESSIONS:
                break
            if time.monotonic() > _effective_deadline:
                break

            records = read_jsonl_sidecar(jsonl_path)
            for record in records:
                if processed >= _MAX_SESSIONS:
                    break
                if time.monotonic() > _effective_deadline:
                    break

                session_id = record.get("session_id", "")
                if not session_id or session_id in cultivated_ids:
                    continue

                corrections = record.get("corrections", [])
                decisions = record.get("decisions", [])

                if not corrections and not decisions:
                    cultivated_ids.add(session_id)
                    # Don't count empty sessions toward _MAX_SESSIONS —
                    # only actual cultivations should consume the budget.
                    continue

                # Cultivate corrections (Ch6 — highest priority per HLD)
                if corrections:
                    try:
                        result = cultivate_from_corrections(
                            corrections, session_id, default_project, default_project_dir
                        )
                        total_applied += result.get("applied", 0)
                        total_escalated += result.get("escalated", 0)
                        for _drift in result.get("drift_errors", []):
                            logger.error("context_health: %s", _drift)
                    except Exception as exc:
                        logger.debug(
                            "context_health: session correction cultivation failed "
                            "for %s: %s", session_id[:8], exc,
                        )

                # Cultivate decisions (Ch5) — pre-filter pipeline-internal noise
                if decisions:
                    filtered_decisions = [
                        d for d in decisions
                        if isinstance(d, str)
                        and len(d) >= 30
                        and not any(d.strip().startswith(pfx) for pfx in _DECISION_NOISE_PREFIXES)
                    ]
                    if filtered_decisions:
                        try:
                            result = cultivate_from_decisions(
                                filtered_decisions, session_id, default_project, default_project_dir
                            )
                            total_applied += result.get("applied", 0)
                            total_escalated += result.get("escalated", 0)
                            for _drift in result.get("drift_errors", []):
                                logger.error("context_health: %s", _drift)
                        except Exception as exc:
                            logger.debug(
                                "context_health: session decision cultivation failed "
                                "for %s: %s", session_id[:8], exc,
                            )

                cultivated_ids.add(session_id)
                processed += 1

        # Persist state — cap at 500 most recent to prevent unbounded growth.
        # Atomic write (tmp + os.replace) prevents corruption on crash/SIGKILL.
        # Persist whenever new IDs were added (including empty-signal sessions).
        if len(cultivated_ids) > len(cultivated_list):
            # Rebuild ordered list: old (from file) + newly added, deduped, capped
            new_ids = [sid for sid in cultivated_list if sid in cultivated_ids]
            existing_set = set(new_ids)  # PE-2: O(1) lookup, built once
            for sid in cultivated_ids:
                if sid not in existing_set:
                    new_ids.append(sid)
            capped_ids = new_ids[-500:]  # Keep 500 newest (oldest evicted first)
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = state_path.with_suffix(".tmp")
                tmp_path.write_text(
                    json.dumps(capped_ids, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp_path, state_path)
            except OSError as exc:
                logger.debug("context_health: failed to persist cultivation state: %s", exc)

            logger.info(
                "context_health: session signal cultivation — "
                "processed=%d, applied=%d, escalated=%d",
                processed, total_applied, total_escalated,
            )

        return total_applied > 0

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

    def _refresh_knowledge_sync(self, root: Path) -> None:
        """Synchronous KNOWLEDGE.md index refresh — filesystem scan only.

        Three-tier format:
        - COMPACT (DailyActivity, JobResults, Signals): count + pattern only
        - HOT/COLD (dirs with >10 files): most recent _HOT_ENTRIES (=5) + "N older files" summary
        - FULL (dirs with ≤10 files): complete listing
        """
        knowledge_dir = root / "Knowledge"
        context_file = root / ".context" / "KNOWLEDGE.md"
        if not context_file.exists() or not knowledge_dir.is_dir():
            return

        # Scan Knowledge/ subdirs for .md files
        index_lines: list[str] = []
        subdirs = sorted(
            d for d in knowledge_dir.iterdir()
            if d.is_dir() and d.name not in {"Archives", "__pycache__"}
        )

        for subdir in subdirs:
            files = sorted(
                f for f in subdir.iterdir()
                if f.suffix == ".md" and f.is_file()
            )
            if not files:
                continue

            # Tier 1: COMPACT — summary only (high-volume machine-generated)
            if subdir.name in self._COMPACT_INDEX_DIRS:
                first_date = files[0].stem[:10] if len(files[0].stem) > 10 else "unknown"
                last_date = files[-1].stem[:10] if len(files[-1].stem) > 10 else "unknown"
                index_lines.append(f"\n### {subdir.name}\n")
                index_lines.append(
                    f"{len(files)} files from {first_date} to {last_date}. "
                    f"Pattern: `Knowledge/{subdir.name}/YYYY-MM-DD-*.md`. Read on demand."
                )
                continue

            # Tier 2: HOT/COLD — recent _HOT_ENTRIES (=5) + cold summary (large dirs)
            if len(files) > self._HOT_COLD_THRESHOLD:
                hot_files = files[-self._HOT_ENTRIES:]  # Most recent by sort order
                cold_count = len(files) - self._HOT_ENTRIES

                index_lines.append(f"\n### {subdir.name}\n")
                index_lines.append(
                    f"_{len(files)} total, showing {self._HOT_ENTRIES} most recent. "
                    f"{cold_count} older files available via workspace-finder/Glob._\n"
                )
                index_lines.append("| Date | File | Topic |")
                index_lines.append("|------|------|-------|")
                for f in hot_files:
                    name = f.stem
                    date_str = name[:10] if len(name) > 10 and name[4] == "-" else "unknown"
                    topic = self._extract_title(f) or name
                    index_lines.append(
                        f"| {date_str} | `{subdir.name}/{f.name}` | {topic} |"
                    )
                continue

            # Tier 3: FULL — complete listing (small dirs)
            index_lines.append(f"\n### {subdir.name}\n")
            index_lines.append("| Date | File | Topic |")
            index_lines.append("|------|------|-------|")
            for f in files:
                # Extract date and title from filename
                name = f.stem
                date_str = name[:10] if len(name) > 10 and name[4] == "-" else "unknown"
                # Try to read first heading for topic
                topic = self._extract_title(f) or name
                index_lines.append(
                    f"| {date_str} | `{subdir.name}/{f.name}` | {topic} |"
                )

        if not index_lines:
            return

        # Replace Knowledge Index section in KNOWLEDGE.md — under the shared
        # KNOWLEDGE.md.lock so this read-modify-write serializes with the decay
        # reclaim + the DDD-section writers (a file lock protects a doc only if
        # ALL writers hold it — run_a1ec08e7). Blocking (mirror _refresh_memory_index):
        # the section refresh is idempotent + must not lose to a racing writer.
        try:
            from utils.file_lock import md_lock
            with md_lock(context_file, blocking=True):
                content = context_file.read_text(encoding="utf-8")
                marker = "## Knowledge Index"
                if marker not in content:
                    return  # No section to replace

                before = content.split(marker)[0]
                # Find the next ## section after Knowledge Index
                after_marker = content.split(marker, 1)[1]
                next_section_idx = after_marker.find("\n## ")
                if next_section_idx >= 0:
                    after = after_marker[next_section_idx:]
                else:
                    after = "\n\n---\n\n_Auto-refreshed on startup from Knowledge/ directories._\n"

                # Structural cap: prevent Knowledge Index from growing unboundedly
                non_empty_lines = [l for l in index_lines if l.strip()]
                if len(non_empty_lines) > self._INDEX_LINE_CAP:
                    logger.warning(
                        "context_health: Knowledge Index has %d lines (cap=%d). "
                        "Truncating to cap. Consider archiving old knowledge files.",
                        len(non_empty_lines), self._INDEX_LINE_CAP,
                    )
                    # Truncate on a SECTION boundary, not a raw line (Gate-2 LOW-2):
                    # a raw slice can cut a `### header`+table-header away from its
                    # rows, leaving broken markdown. Walk to the cap, then back up to
                    # the last `### ` header so every kept section is whole.
                    cut = self._INDEX_LINE_CAP * 2
                    if cut < len(index_lines):
                        boundary = cut
                        while boundary > 0 and not index_lines[boundary].startswith("### "):
                            boundary -= 1
                        # boundary now sits ON a `### ` header → drop it + everything
                        # after, keeping only whole preceding sections. Fallback to
                        # the raw cut if no header found (shouldn't happen).
                        index_lines = index_lines[:boundary] if boundary > 0 else index_lines[:cut]

                new_content = before + marker + "\n" + "\n".join(index_lines) + "\n" + after
                context_file.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            logger.warning("context_health: KNOWLEDGE.md refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # T4: Maturity evidence update + auto-promotion
    # ------------------------------------------------------------------

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

        Scans recent runs (last 10). If a run has status=completed AND a deliver
        stage with status=completed, ALL sections in this project's DDD are marked
        verified_by_production=True + used_in_decision=True (the pipeline consumed
        DDD context at EVALUATE/THINK/BUILD).

        Marks processed runs with 'maturity_updated: true' to avoid re-processing.
        """
        import json as _json
        from core.ddd_maturity import (
            inject_maturity,
            parse_maturity,
        )

        runs_dir = project_path / ".artifacts" / "runs"
        if not runs_dir.is_dir():
            return

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

            if data.get("maturity_updated"):
                continue  # Already processed
            if data.get("status") != "completed":
                continue

            # Check if deliver stage completed
            stages = data.get("stages", [])
            has_deliver = any(
                s.get("stage") == "deliver" and s.get("status") == "completed"
                for s in stages
            )
            if not has_deliver:
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
            data["maturity_updated"] = True
            try:
                tmp = run_json.with_suffix(".tmp")
                tmp.write_text(_json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, run_json)
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

    def _run_memory_lifecycle(self, root: Path) -> None:
        """Run DDD lifecycle engine on MEMORY.md: ref bump + decay.

        Same engine as DDD IMPROVEMENT.md — extends coverage to MEMORY.md.
        Uses existing parse_entries/bump_references/assess_decay/inject_entry_metadata.
        Bumps refs from recent DailyActivity text. Decays unreferenced entries.
        Evergreen: COE Registry, Standing Preferences (immune to decay).
        """
        from core.ddd_entry_lifecycle import (
            assess_decay,
            collapse_stacked_metadata,
            inject_entry_metadata,
            parse_entries,
        )

        memory_path = root / ".context" / "MEMORY.md"
        if not memory_path.exists():
            return

        from core.ddd_entry_lifecycle import (
            MEMORY_EVERGREEN_SECTIONS,
            reclaim_duplicate_entries,
            reclaim_noise_entries,
        )
        evergreen = MEMORY_EVERGREEN_SECTIONS

        # H1 (adversarial): MEMORY.md is user-owned and concurrently written
        # (Edit tool, locked_write.py, distillation). The whole read-modify-write
        # — bump, decay, AND the destructive reclaim — MUST hold the same
        # MEMORY.md.lock that other writers use, else a reclaim overwrite races
        # a concurrent edit (lost update). Read content INSIDE the lock so the
        # strip is computed from the locked snapshot.
        from utils.file_lock import flock_exclusive_nb, flock_unlock
        lock_path = memory_path.with_suffix(".md.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive_nb(lock_fd)
        except OSError:
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: MEMORY.md lock busy, skipping lifecycle")
            return

        try:
            content = memory_path.read_text(encoding="utf-8")

            # ── HEAL (R-1, run_55c02bbe): collapse any stacked/orphaned metadata
            # before parse. A historical off-by-one splice could orphan an
            # existing entry's meta as a 2nd consecutive line; parse_entries +
            # inject_entry_metadata are orphan-blind, so this dedicated sweep is
            # the auto-heal that keeps new orphans from accumulating. Pure +
            # idempotent: a no-op once clean. Runs under the same MEMORY.md.lock.
            healed = collapse_stacked_metadata(content)
            if healed != content:
                memory_path.write_text(healed, encoding="utf-8")
                content = healed
                logger.info("context_health: MEMORY.md healed stacked metadata")

            entries = parse_entries(content)
            if not entries:
                return

            today = date.today()

            # run_3cb6b9ae Cycle-3 (#2): the usage→ref bridge was REMOVED. It mapped
            # `.memory-usage.json` numeric-ID counts onto body entries via the
            # `- [KD01] title` index shape — a shape ONLY the deleted in-prompt index
            # carried (#6). With the index gone, build_usage_ref_map returned {}
            # permanently (dead by starvation), so no entry was ever bumped. The
            # producer (_track_memory_usage → .memory-usage.json) is KEPT — it still
            # feeds the loops-health `memory_precision` signal — only the dead
            # index-ID→body-ref bridge is gone. ref_count for reclaim protection now
            # comes solely from the metadata already on each entry.
            bumped = 0

            # ── Decay: assess state transitions ──
            # A2 (run_55cb38d6): MEMORY.md uses a FASTER 45d dormant threshold.
            # The fast-churn operational type is GUIDELINE — written faster than the
            # 60d global decay can reclaim it. NOTE (run_123652ae): pitfall is NO
            # LONGER fast-decayed here — it is now EVERGREEN by type (assess_decay
            # EVERGREEN_TYPES), because the MEMORY [PIT##] entries are real hard-won
            # judgment (e.g. "removing a shared-helper is a contract change"), not
            # churn — the original "GUI/PIT are fast-churn noise" framing was wrong
            # about PIT. So the 45d threshold now only bites guideline/process.
            # KNOWLEDGE.md (below) + IMPROVEMENT/DDD keep 60d global. dormant→archived
            # stays 150d for all. (thresholds tightened 90/180 -> 60/150, run_186a5f15.)
            transitions = assess_decay(
                entries, today, evergreen_sections=evergreen, dormant_days=45
            )

            # APPLY the transitions to the entry objects. assess_decay RETURNS
            # transitions but does NOT mutate entries — without this loop the
            # persisted metadata keeps the stale "active" state, so a
            # dormant/archived transition is logged but never written, and reclaim
            # (which requires decay∈{dormant,archived}) can never select the entry.
            # This was the root cause of MEMORY.md never shrinking (9 transitions
            # logged, 0 persisted).
            #
            # We do NOT archive here (unlike ddd_orchestrator, which lacks a
            # guaranteed reclaim pass). The MEMORY path ALWAYS runs
            # reclaim_noise_entries below, and reclaim archives+strips BOTH
            # dormant AND archived entries (_NOISY_DECAY_STATES) via
            # _archive_and_strip. So we persist EVERY transitioned state (incl.
            # archived) through inject_entry_metadata — which only annotates,
            # never removes (verified: an entry excluded from the list keeps its
            # OLD metadata in the body) — and let reclaim be the SINGLE archive+
            # strip authority. This avoids the double-archive that a separate
            # archive_entries here would cause (archive_entries has no dedup, and
            # inject can't strip the just-archived bullet, so reclaim would archive
            # it a second time).
            for t in transitions:
                t.entry.decay_state = t.new_state

            # Only write if something changed
            if bumped > 0 or transitions:
                updated = inject_entry_metadata(content, entries)
                memory_path.write_text(updated, encoding="utf-8")
                content = updated  # reclaim operates on the latest content
                if transitions:
                    logger.info(
                        "context_health: MEMORY.md lifecycle — %d bumped, %d transitions (%s)",
                        bumped, len(transitions),
                        ", ".join(f"{t.entry.title[:30]}:{t.old_state}→{t.new_state}" for t in transitions[:3]),
                    )

            # ── CLEAN (M0 ②): reclaim stale operational noise from MEMORY.md ──
            # Archive to .context/MEMORY-archive-YYYY-MM.md and physically strip (decay +
            # inject_entry_metadata never remove bullets). Evergreen sections
            # (Principles/Corrections/COE Registry/...) AND keep-class types are
            # protected — only plain dormant operational entries are reclaimed.
            reclaim_report = reclaim_noise_entries(
                content, today, memory_path.parent,
                evergreen_sections=evergreen,
                archive_name=f"MEMORY-archive-{today.strftime('%Y-%m')}.md",
                source_path=memory_path,
                dry_run=False,
            )
            if reclaim_report.new_content is not None:
                # reclaim wrote memory_path + dated .bak (source_path given). NEW
                # ARCHITECTURE (2026-08-14): the in-prompt index was DELETED, so
                # there is no index to rebuild after the strip — the stripped
                # content reclaim already wrote to disk IS the final state.
                logger.info(
                    "context_health: MEMORY.md reclaim — %d archived+stripped, %d protected",
                    reclaim_report.archived, reclaim_report.kept_protected,
                )

            # ── DEDUP (run_2816ab1c): archive+strip EXACT duplicates the age-based
            # reclaim above can't see (a fresh dup is still `active`). Same lock,
            # same evergreen/keep-class protection. Runs on the latest content.
            content = memory_path.read_text(encoding="utf-8")
            dup_report = reclaim_duplicate_entries(
                content, today, memory_path.parent,
                evergreen_sections=evergreen,
                archive_name=f"MEMORY-archive-{today.strftime('%Y-%m')}.md",
                source_path=memory_path,
                dry_run=False,
            )
            if dup_report.new_content is not None:
                # NEW ARCHITECTURE: no index to rebuild — reclaim_duplicate_entries
                # already wrote the stripped content to disk.
                logger.info(
                    "context_health: MEMORY.md dedup — %d exact-dup archived+stripped",
                    dup_report.archived,
                )
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    def _run_knowledge_lifecycle(self, root: Path) -> None:
        """Run DDD lifecycle engine on KNOWLEDGE.md: decay + archive + strip.

        Same engine + machinery as MEMORY.md. assess_decay computes transitions;
        this method APPLIES them (mirror _run_memory_lifecycle) then runs
        reclaim_noise_entries to archive+strip dormant NON-evergreen entries
        (KNOWLEDGE-archive.md + dated .bak). KNOWLEDGE is almost entirely
        load-bearing reference, so KNOWLEDGE_EVERGREEN_SECTIONS protects every
        reference section by name — only genuinely disposable sections decay
        (Gate-1 CRITICAL, run_a1ec08e7: without this the runtime Self-Identity /
        arch / pipeline facts would be stripped).

        Concurrency: the whole read-modify-write is under md_lock(KNOWLEDGE.md.lock,
        blocking=False) — a file lock protects a doc ONLY if EVERY writer holds it,
        so the 3 section-refresh writers (_refresh_knowledge_sync,
        _refresh_knowledge_projects_section, ddd_orchestrator._ch_inject_knowledge)
        take the SAME lock (blocking). Non-blocking here (skip-if-busy) mirrors
        _run_memory_lifecycle: a destructive strip must never wait on a held lock
        inside the <30s hook. (The old prose ref-bump was removed in R2-prime —
        honest ref comes from the id-based producer, so `bumped` is always 0.)
        """
        from core.ddd_entry_lifecycle import (
            KNOWLEDGE_EVERGREEN_SECTIONS,
            assess_decay,
            inject_entry_metadata,
            parse_entries,
            reclaim_duplicate_entries,
            reclaim_noise_entries,
        )
        from utils.file_lock import md_lock

        knowledge_path = root / ".context" / "KNOWLEDGE.md"
        if not knowledge_path.exists():
            return

        evergreen = KNOWLEDGE_EVERGREEN_SECTIONS
        with md_lock(knowledge_path, blocking=False) as got_lock:
            if not got_lock:
                logger.debug("context_health: KNOWLEDGE.md lock busy, skipping lifecycle")
                return

            content = knowledge_path.read_text(encoding="utf-8")
            entries = parse_entries(content)
            if not entries:
                return

            today = date.today()
            bumped = 0

            # Decay: evergreen reference sections are immune (Gate-1 CRITICAL).
            transitions = assess_decay(
                entries, today, evergreen_sections=evergreen
            )

            # APPLY transitions to entry objects before inject (same missing-apply
            # bug fixed in _run_memory_lifecycle — assess_decay returns transitions
            # but does not mutate entries). We do NOT archive here; reclaim below is
            # the SINGLE archive+strip authority (avoids the double-archive that a
            # separate archive_entries would cause — inject can't strip a bullet, so
            # reclaim would re-archive it).
            for t in transitions:
                t.entry.decay_state = t.new_state

            if bumped > 0 or transitions:
                updated = inject_entry_metadata(content, entries)
                knowledge_path.write_text(updated, encoding="utf-8")
                content = updated  # reclaim operates on the latest content
                if transitions:
                    logger.info(
                        "context_health: KNOWLEDGE.md lifecycle — %d bumped, %d transitions",
                        bumped, len(transitions),
                    )

            # CLEAN: archive+strip dormant NON-evergreen operational entries.
            # source_path gives the dated .bak; no reindex (KNOWLEDGE has no
            # MEMORY_INDEX block — the ## Knowledge Index nav is rebuilt by
            # _refresh_knowledge_sync, a separate writer under the same lock).
            reclaim_report = reclaim_noise_entries(
                content, today, knowledge_path.parent,
                evergreen_sections=evergreen,
                archive_name="KNOWLEDGE-archive.md",
                source_path=knowledge_path,
                dry_run=False,
            )
            if reclaim_report.new_content is not None:
                logger.info(
                    "context_health: KNOWLEDGE.md reclaim — %d archived+stripped, %d protected",
                    reclaim_report.archived, reclaim_report.kept_protected,
                )

            # DEDUP (run_2816ab1c): exact-dup sweep, same lock + evergreen guard.
            content = knowledge_path.read_text(encoding="utf-8")
            dup_report = reclaim_duplicate_entries(
                content, today, knowledge_path.parent,
                evergreen_sections=evergreen,
                archive_name="KNOWLEDGE-archive.md",
                source_path=knowledge_path,
                dry_run=False,
            )
            if dup_report.new_content is not None:
                logger.info(
                    "context_health: KNOWLEDGE.md dedup — %d exact-dup archived+stripped",
                    dup_report.archived,
                )

    def _run_evolution_lifecycle(self, root: Path) -> None:
        """Run DDD lifecycle engine on EVOLUTION.md: DEDUP ONLY (no age-decay).

        EVOLUTION.md had ZERO lifecycle before run_2816ab1c — it accreted forever
        (its Corrections Captured section is folded SEPARATELY by
        evolution_maintenance_hook.fold_corrections; that hook does NOT age-decay).

        DESIGN DECISION (user directive, run_2816ab1c): EVOLUTION is FULLY evergreen
        for age-decay — EVOLUTION_EVERGREEN_SECTIONS lists ALL 7 sections, so
        assess_decay + reclaim_noise_entries transition/strip NOTHING here. The O-
        entries are distilled operational wisdom (value, not age-churn — Principle
        1); size control is fold_corrections + dedup, never age-death. So this method
        runs ONLY the exact-dup DEDUP sweep. (assess_decay/reclaim are still invoked
        with the all-evergreen set — they are cheap no-ops that keep the code path
        identical to MEMORY/KNOWLEDGE, and self-document that decay is intentionally
        inert here rather than silently absent.)

        Concurrency (Gate-1 Axis E): EVOLUTION.md is also written by
        evolution_maintenance_hook (fold_corrections, via locked_write). The whole
        read-modify-write MUST hold EVOLUTION.md.lock so the dedup strip never races
        that fold. Non-blocking skip-if-busy mirrors MEMORY (a strip must not wait on
        a held lock inside the <30s hook).
        """
        from core.ddd_entry_lifecycle import (
            EVOLUTION_EVERGREEN_SECTIONS,
            assess_decay,
            inject_entry_metadata,
            parse_entries,
            reclaim_duplicate_entries,
            reclaim_noise_entries,
        )
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        evolution_path = root / ".context" / "EVOLUTION.md"
        if not evolution_path.exists():
            return

        evergreen = EVOLUTION_EVERGREEN_SECTIONS
        lock_path = evolution_path.with_suffix(".md.lock")
        lock_fd = None
        try:
            lock_fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive_nb(lock_fd)
        except OSError:
            if lock_fd:
                lock_fd.close()
            logger.debug("context_health: EVOLUTION.md lock busy, skipping lifecycle")
            return

        try:
            content = evolution_path.read_text(encoding="utf-8")
            entries = parse_entries(content)
            if not entries:
                return

            today = date.today()

            # Decay: only "Optimizations Learned" (plain operational) is eligible;
            # every other section is evergreen. dormant_days defaults to the global
            # 60 (EVOLUTION is not fast-churn like MEMORY's 45).
            transitions = assess_decay(
                entries, today, evergreen_sections=evergreen
            )
            for t in transitions:
                t.entry.decay_state = t.new_state

            if transitions:
                updated = inject_entry_metadata(content, entries)
                evolution_path.write_text(updated, encoding="utf-8")
                content = updated
                logger.info(
                    "context_health: EVOLUTION.md lifecycle — %d transitions",
                    len(transitions),
                )

            # CLEAN: archive+strip dormant NON-evergreen (Optimizations Learned only).
            # Monthly shard (mirrors MEMORY:2126) — unifies all EVOLUTION archive
            # writers onto EVOLUTION-archive-{YYYY-MM}.md (legacy fixed file is
            # pre-2026-08 history, never written to again).
            evo_shard = f"EVOLUTION-archive-{today.strftime('%Y-%m')}.md"
            reclaim_report = reclaim_noise_entries(
                content, today, evolution_path.parent,
                evergreen_sections=evergreen,
                archive_name=evo_shard,
                source_path=evolution_path,
                dry_run=False,
            )
            if reclaim_report.new_content is not None:
                content = reclaim_report.new_content
                logger.info(
                    "context_health: EVOLUTION.md reclaim — %d archived+stripped, %d protected",
                    reclaim_report.archived, reclaim_report.kept_protected,
                )

            # DEDUP: exact-dup sweep, same lock + evergreen guard. Same monthly shard.
            content = evolution_path.read_text(encoding="utf-8")
            dup_report = reclaim_duplicate_entries(
                content, today, evolution_path.parent,
                evergreen_sections=evergreen,
                archive_name=evo_shard,
                source_path=evolution_path,
                dry_run=False,
            )
            if dup_report.new_content is not None:
                logger.info(
                    "context_health: EVOLUTION.md dedup — %d exact-dup archived+stripped",
                    dup_report.archived,
                )
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    @staticmethod
    def _proposal_age_seconds(proposal_file: Path, data: dict, now: float) -> float:
        """Age of a proposal in seconds from its TRUE creation time.

        mtime is an UNRELIABLE age proxy (git checkout / rsync / status-rewrite
        resets it — run_419ff7d4 Gate-2 HIGH). Prefer, in order:
          1. the filename stamp `proposal_<id>_YYYYMMDD-HHMMSS.json` (set at write,
             never rewritten — the reliable source),
          2. the JSON `created_at` field,
          3. file mtime (last resort — only when neither above parses).
        Returns the LARGEST age the reliable sources support (so a reset mtime can
        only ever make a proposal look OLDER-or-equal, never spuriously younger).
        """
        candidates: list[float] = []
        m = re.search(r"_(\d{8})-(\d{6})\.json$", proposal_file.name)
        if m:
            try:
                ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                candidates.append(now - ts.timestamp())
            except ValueError:
                pass
        created = data.get("created_at")
        if created:
            try:
                dt = datetime.fromisoformat(created)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                candidates.append(now - dt.timestamp())
            except (ValueError, TypeError):
                pass
        if candidates:
            return max(candidates)  # trust the oldest reliable signal, ignore reset mtime
        try:
            return now - proposal_file.stat().st_mtime  # last resort
        except OSError:
            return 0.0  # unknowable → treat as fresh (never reclaim on doubt)

    def _expire_stale_proposals(self, root: Path) -> None:
        """Expire + RECLAIM stale cultivation proposals across ALL projects.

        TWO actions (run_419ff7d4 — the old version only did #1, which flipped
        status but NEVER removed files → the proposals dir grew unbounded, 514
        files / 351 terminal observed):
          1. EXPIRE: a still-pending/queued proposal older than 14 days →
             status flipped to "expired" in place (was the only behavior).
          2. RECLAIM (the fix): a TERMINAL proposal (status NOT in
             AWAITING_HUMAN_STATUSES — i.e. applied/rejected/dismissed/expired)
             whose file is older than the RETENTION window → MOVED to a sibling
             `archive/` subdir. ARCHIVE, never delete: ProposalFeedbackTracker
             still counts archived proposals for reject-precision (its glob is
             widened to scan archive/ too), and recall stays intact.

        Safety (Gate-1 run_419ff7d4):
          - LIVE (pending/escalated) proposals are NEVER moved, at any age — the
            terminal check gates first, so an unparseable/missing status (defaults
            to a non-terminal read) is left untouched.
          - The RETENTION window (30d) >> any in-flight approve/reject, so a
            proposal being actively written (fresh mtime) is never mid-write-moved
            (no lock needed: age gate excludes the hot file).
          - Malformed JSON / bad mtime → skip (leave the file), never move on doubt.
        """
        import shutil
        from core.ddd_cultivation import AWAITING_HUMAN_STATUSES
        projects_dir = root / "Projects"
        if not projects_dir.is_dir():
            return

        now = time.time()
        expire_ttl = 14 * 24 * 60 * 60       # 14 days: pending → expired (action 1)
        retention = 30 * 24 * 60 * 60        # 30 days: terminal → archive (action 2)
        # >2× the 14d TTL and far beyond any in-flight approve, so archiving never
        # races a live write and never reclaims a still-referenced proposal.
        expired = 0
        reclaimed = 0

        for project_dir in projects_dir.iterdir():
            proposals_dir = project_dir / ".artifacts" / "proposals"
            if not proposals_dir.is_dir():
                continue
            archive_dir = proposals_dir / "archive"

            for proposal_file in proposals_dir.glob("*.json"):
                try:
                    data = json.loads(proposal_file.read_text(encoding="utf-8"))
                    status = data.get("status", "pending")

                    # AGE from the proposal's TRUE creation time, NOT file mtime
                    # (Gate-2 HIGH, run_419ff7d4): mtime is an UNRELIABLE age proxy —
                    # a git checkout / rsync / bulk status-rewrite resets it, so a
                    # 60-day-old terminal proposal can look 22 days old by mtime and
                    # escape reclaim (live: 253 files shared one reset mtime → an
                    # mtime gate archived 58 of 259 truly-old). The filename carries
                    # the real created stamp: proposal_<id>_YYYYMMDD-HHMMSS.json.
                    # Fallback to created_at, then mtime, only when the name lacks it.
                    file_age = self._proposal_age_seconds(proposal_file, data, now)

                    # Action 1 — EXPIRE a stale still-open proposal (flip in place).
                    if status in ("pending", "queued") and file_age >= expire_ttl:
                        data["status"] = "expired"
                        data["expired_reason"] = "14-day TTL exceeded"
                        proposal_file.write_text(
                            json.dumps(data, indent=2), encoding="utf-8"
                        )
                        expired += 1
                        continue  # newly-expired: reclaim on a later sweep

                    # Action 2 — RECLAIM a terminal proposal past the retention window.
                    # LIVE (AWAITING_HUMAN_STATUSES) is never moved, regardless of age.
                    if status not in AWAITING_HUMAN_STATUSES and file_age >= retention:
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(proposal_file), str(archive_dir / proposal_file.name))
                        reclaimed += 1
                except (OSError, json.JSONDecodeError, ValueError):
                    continue  # skip on any doubt — never move a file we can't read

        if expired or reclaimed:
            logger.info(
                "context_health: expired %d stale proposals (>14d), "
                "reclaimed %d terminal proposals to archive/ (>30d)",
                expired, reclaimed,
            )

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

    def _sync_transcript_index(self, root: Path) -> None:
        """Incremental sync of JSONL transcripts into the FTS5 keyword index.

        Indexes Claude Code session transcripts for verbatim recall via
        the Recall Engine (Memory Architecture v2, Phase 5 / P1).
        MemPalace benchmark: raw verbatim scores 96.6% vs 84.2% for summaries.

        Follows the same pattern as _sync_knowledge_library: open vec DB,
        create store, embed, sync. Failures are silent.
        """
        from core.transcript_indexer import TranscriptStore, sync_transcript_index
        from core.vec_db import open_vec_db

        # Derive transcript dir from the authoritative workspace path
        # (initialization_manager — always set at startup) rather than
        # config.json (which may not have workspace_path yet on first run).
        #
        # NEVER fall back to scanning ~/.claude/projects/ base dir — it
        # contains dirs with "Desktop" in the path, triggering macOS TCC
        # "would like to access Desktop" permission popups.
        base = Path.home() / ".claude" / "projects"
        transcripts_dir = None

        def _path_to_slug(p: str) -> str:
            """Convert a filesystem path to Claude SDK project slug.

            SDK format: replace / with - (keeping leading -), replace . with -.
            e.g. ~/.swarm-ai/SwarmWS -> -Users-gawan--swarm-ai-SwarmWS
            """
            return str(Path(p).resolve()).replace("/", "-").replace(".", "-")

        # Primary: derive from initialization_manager (always available)
        ws_path = initialization_manager.get_cached_workspace_path()
        if ws_path:
            slug = _path_to_slug(ws_path)
            candidate = base / slug
            if candidate.is_dir():
                transcripts_dir = candidate

        # Secondary: also check swarmai repo path from config
        if transcripts_dir is None:
            try:
                from core.app_config_manager import app_config_manager
                if app_config_manager is not None:
                    swarmai_dir = app_config_manager.get("swarmai_dir")
                    if swarmai_dir:
                        candidate = base / _path_to_slug(swarmai_dir)
                        if candidate.is_dir():
                            transcripts_dir = candidate
            except (ImportError, Exception):
                pass

        if transcripts_dir is None:
            logger.debug(
                "context_health: no matching transcript dir found for workspace %s, "
                "skipping transcript indexing this cycle", ws_path,
            )
            return

        if not transcripts_dir.is_dir():
            return

        with open_vec_db() as conn:
            if conn is None:
                logger.debug("context_health: recall DB connect failed, skipping transcript sync")
                return

            store = TranscriptStore(conn)
            store.ensure_tables()

            # Self-heal a malformed transcript_fts (same external-content FTS5
            # corruption class as knowledge_fts, run_1d198980). Maintenance layer
            # is the RIGHT place — off the event loop, single-flight, already a
            # write context — NOT the recall read path. A corrupt index otherwise
            # makes transcript recall silently empty until the next full re-index.
            if not store._fts_is_healthy():
                logger.warning("context_health: transcript_fts malformed — rebuilding index")
                try:
                    store.repair_fts_index()
                    logger.info("context_health: transcript_fts rebuilt from content table")
                except Exception as exc:  # noqa: BLE001 — repair is best-effort
                    logger.error("context_health: transcript_fts repair failed: %s: %s",
                                 type(exc).__name__, exc)

            # WRITER STOPPED (pure-filesystem recall design §5.5/DoD8, 2026-06-28):
            # transcript recall is FTS5-only (vector leg removed 2026-08-14, PRI11) —
            # no embedding step.
            stats = sync_transcript_index(store, transcripts_dir)

        if stats.get("files_indexed", 0) > 0:
            logger.info(
                "context_health: transcripts synced — %d indexed, %d skipped, %d chunks",
                stats["files_indexed"], stats["files_skipped"], stats["chunks_added"],
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
            artifacts_dir = root / "Projects" / "SwarmAI" / ".artifacts"
            if artifacts_dir.is_dir():
                health = check_adversarial_health(artifacts_dir)
                if health.get("degradation_warning"):
                    findings.append(
                        f"[gap/high] Adversarial review may be degraded — "
                        f"{health['consecutive_zero_count']} consecutive pipeline runs "
                        f"with >50 changed lines had 0 findings. Consider rotating "
                        f"adversarial prompt."
                    )
        except Exception as exc:
            logger.debug("context_health: adversarial meta-check skipped: %s", exc)

        # 4. DailyActivity — today's file should exist if we're running
        da_dir = root / "Knowledge" / "DailyActivity"
        today_file = da_dir / f"{date.today().isoformat()}.md"
        if da_dir.is_dir() and not today_file.exists():
            findings.append(f"MISSING: DailyActivity/{today_file.name} (no session logged today)")

        # 5. Enforce section caps on MEMORY.md (daily, not just post-distillation)
        memory_path = context_dir / "MEMORY.md"
        if memory_path.exists():
            try:
                from hooks.distillation_hook import DistillationTriggerHook
                DistillationTriggerHook._enforce_section_caps(memory_path, root)
                # Size-driven archive (hysteresis) after count-caps: the token-size
                # lever that keeps the always-injected live MEMORY.md bounded
                # (>30K body → archive lowest-value operational to 25K). Runs daily.
                DistillationTriggerHook._enforce_size_valve(memory_path, root)
            except Exception as exc:
                logger.warning("context_health: section cap enforcement failed: %s", exc)

        # 6. Memory consistency — detect stale claims in MEMORY.md body
        if memory_path.exists():
            findings += self._detect_stale_memory_claims(memory_path)

        # 7. L1 cache freshness — if source .md newer than cache, invalidate
        self._check_cache_freshness(context_dir, findings)

        # 8. Enforce retention policies (archive/delete old files)
        try:
            self._enforce_retention_policies(ws_path)
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

        # 12. Pipeline crash-zombie sweep (daily) — auto-abandon stale runs.
        #     The new-run trigger (_auto_abandon_stale_runs) only fires when a
        #     NEW run starts; without this daily sweep, crash-zombie paused runs
        #     (status=paused, reason=session_crash_auto_detected, 0 tokens) pile
        #     up forever when no new pipeline runs (12 found 2026-06-30, manually
        #     cleared). cleanup_orphans uses the SAME _abandon_verdict, so it
        #     reaps running-orphans AND crash-zombies while PRESERVING intentional
        #     pauses (Gate BLOCK / awaiting-decision / work-done). (run_5caa2588)
        findings += self._sweep_pipeline_zombies()

        # 13. DDD completeness — detect HALF-CREATED projects (≥1 but <4 standard
        #     DDD docs). This is the gap that let CMHK_SalesIntel sit with only
        #     IMPROVEMENT.md (3 missing docs) for >1 month unwarned: cultivation
        #     checks CONTENT freshness, _refresh_knowledge_projects_section
        #     silently skips missing docs (`if docs:`), and nothing checked
        #     EXISTENCE of the standard 4. A half-created project breaks
        #     cross-project index refs + leaves EVALUATE without a PRODUCT/TECH
        #     base. (run_5a29f00c)
        findings += self._check_ddd_completeness(root)

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

    def _sweep_pipeline_zombies(self) -> list[str]:
        """Daily sweep: auto-abandon crash-zombie + stale-running pipeline runs.

        Delegates to artifact_cli.cleanup_orphans (the SAME _abandon_verdict the
        new-run trigger uses), so intentional pauses are preserved. Fail-open: a
        sweep error is logged + reported, never raised — a health-check sub-item
        must never break the whole deep check (or block session startup).
        """
        findings: list[str] = []
        try:
            from scripts.artifact_cli import cleanup_orphans
            result = cleanup_orphans()  # default threshold 2h; verdict gates the rest
            n = result.get("abandoned_count", 0)
            if n:
                findings.append(
                    f"AUTO-ABANDONED {n} stale pipeline run(s) "
                    f"(crash-zombie / orphan) across "
                    f"{result.get('projects_scanned', 0)} project(s)"
                )
        except Exception as e:  # noqa: BLE001 — fail-open by design
            findings.append(
                f"pipeline zombie sweep failed (non-fatal): "
                f"{type(e).__name__}: {e}"
            )
        return findings

    def _check_ddd_completeness(self, root: Path) -> list[str]:
        """Flag HALF-CREATED projects: ≥1 but <4 of the standard DDD docs.

        A DDD project has 4 docs (PRODUCT/TECH/IMPROVEMENT/PROJECT). A project
        with SOME but not ALL is half-created — usually because it was made
        outside standard `s_project-manager` provisioning. That silently breaks
        cross-project index refs (PROJECTS.md points at files that don't exist)
        and leaves the pipeline's EVALUATE stage without a PRODUCT/TECH base.

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
            if result.stdout.strip():
                uncommitted = [
                    l.strip() for l in result.stdout.strip().splitlines()
                ]
                findings.append(
                    f"UNCOMMITTED: {len(uncommitted)} context file(s): "
                    + ", ".join(l.split()[-1] for l in uncommitted[:5])
                )
        except (subprocess.TimeoutExpired, OSError):
            pass

        return findings

    def _inject_ddd_into_knowledge(self, root: Path) -> None:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        DddCultivationOrchestrator()._ch_inject_knowledge(root, str(root))

    def _detect_knowledge_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_knowledge_staleness(root, ws_path)

    def _check_ddd_staleness(self, root: Path, ws_path: str) -> list[str]:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_ddd_staleness(root, ws_path)

    def _auto_apply_ddd_proposals(self, root: Path) -> None:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        DddCultivationOrchestrator()._auto_apply_ddd_proposals(root)

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
        """If any source .context/*.md is newer than L1 cache, invalidate."""
        cache_file = context_dir / "L1_SYSTEM_PROMPTS.md"
        if not cache_file.exists():
            return

        cache_mtime = cache_file.stat().st_mtime
        for source in context_dir.glob("*.md"):
            if source.name.startswith("L") or source.name == cache_file.name:
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

    # The 9 context files that compose the system prompt (assembly order)
    _CONTEXT_FILES = (
        "SOUL.md", "AGENT.md", "USER.md", "STEERING.md", "TOOLS.md",
        "MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md", "PROJECTS.md",
    )
    # OBSERVABILITY thresholds (run_3f25a73a). These are NOT gates — the
    # assembly line does NOT truncate (XG directive 2026-06-28, pure-filesystem
    # recall design §3.5: "the read+assembly line DEFAULTS to trusting that the
    # files it loads are healthy and within spec"). These thresholds only emit a
    # WARNING/EMERGENCY finding to the deep-check report so the SEPARATE write-side
    # management line (decay/archive/trim — deferred #3) has a signal. WARNING is
    # anchored to the 91K effective assembly budget; EMERGENCY is a clear-over.
    # Measured with the calibrated estimate_tokens — the same estimator the
    # assembly uses, so this number matches what actually enters the prompt.
    _WARNING_THRESHOLD = 91_000
    _EMERGENCY_THRESHOLD = 130_000

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

    # Matches embedded token self-claims like "~47K tokens", "~44K tokens",
    # "≈ 30,000 tokens", "152K tok" — a NUMBER (with optional K/k suffix) near
    # the word token(s). Used by _check_self_report_drift.
    _SELF_REPORT_TOKEN_RE = re.compile(
        r"[~≈]?\s*([0-9][0-9,\.]*)\s*([KkMm])?\s*(?:tok\b|tokens?\b)",
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
        """Write-side governance health (R3). Zero-LLM, entry-count based.

        Catches the drift class that silently rotted memory governance:
          1. caps-key validity  — every SECTION_CAPS key is a real current
             section (a dead key = that section is silently uncapped).
          2. section over-cap   — any section whose live entry count exceeds
             its cap with no reclaim progress (membership bloat).
          3. orphan sections    — duplicate/orphan "## Distilled" sections (the
             fallback target when a write goes to a non-existent section).

        Counts ENTRIES, never tokens (R3-8: the token estimator is ~33% off;
        gating governance on it would be building on a broken ruler). A finding
        is a smoke alarm surfaced to the briefing, never an auto-mutation.
        """
        findings: list[str] = []
        memory_path = context_dir / "MEMORY.md"
        if not memory_path.exists():
            return findings

        try:
            from hooks.distillation_hook import SECTION_CAPS
            from core.ddd_entry_lifecycle import MEMORY_SECTION_NAMES
        except Exception as exc:  # pragma: no cover — SSoT import should succeed
            return [f"[write-gov] could not load SSoT for write-governance check: {exc}"]

        # 1. caps-key validity (defense against BROKEN-1 recurrence)
        dead_keys = [k for k in SECTION_CAPS if k not in MEMORY_SECTION_NAMES]
        if dead_keys:
            findings.append(
                f"[write-gov] SECTION_CAPS dead keys (section silently uncapped): {dead_keys}"
            )

        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError:
            return findings

        # Count entries per section (bullets directly under each "## <name>").
        import re as _re
        section_counts: dict[str, int] = {}
        current: str | None = None
        for line in content.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                section_counts.setdefault(current, 0)
            elif current and _re.match(r"^- (?!\[Archived\])", line.strip()):
                section_counts[current] = section_counts.get(current, 0) + 1

        # 2. section over-cap (entry count, not token)
        for sec, cap in SECTION_CAPS.items():
            n = section_counts.get(sec, 0)
            if n > cap:
                findings.append(
                    f"[write-gov] section '{sec}' over cap: {n}/{cap} entries "
                    f"(reclaim/decay not keeping up)"
                )

        # 3. orphan "## Distilled" sections (misfile fallback signature)
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

    def _enforce_retention_policies(self, ws_path: str) -> None:
        """Enforce time-based archival and cleanup.

        1. DailyActivity >90 days -> move to Knowledge/Archives/
        2. Archives >365 days -> delete (except MEMORY-archive-*.md)
        3. Open Threads with resolved marker >7 days -> log for manual review
           (actual removal is handled by section cap enforcement, not here)
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
                        # Protect undistilled files from archival — but only up to
                        # 180 days.  Beyond that, archive regardless to prevent
                        # unbounded DailyActivity growth from distillation failures.
                        cutoff_180 = datetime.now() - timedelta(days=180)
                        if file_date >= cutoff_180:
                            content = f.read_text(encoding="utf-8")
                            if "distilled: true" not in content[:500]:  # check frontmatter only
                                logger.warning("Skipping undistilled file %s (>90d but not yet distilled)", f.name)
                                continue
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

        # 3. Archive resolved Open Threads >7 days — remove from MEMORY.md
        #    and append to MEMORY-archive-YYYY-MM.md (same pattern as
        #    _enforce_section_caps in distillation_hook.py).
        memory_path = root / ".context" / "MEMORY.md"
        if memory_path.exists():
            self._archive_resolved_open_threads(memory_path, root, cutoff_7)

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

    def _archive_resolved_open_threads(
        self, memory_path: Path, root: Path, cutoff: datetime
    ) -> None:
        """Remove resolved OT entries >cutoff from MEMORY.md, append to archive.

        Uses flock on the MEMORY.md.lock sidecar file, matching the
        locking pattern in distillation_hook._enforce_section_caps and
        scripts/locked_write.py.
        """
        from utils.file_lock import flock_exclusive, flock_unlock

        lock_path = memory_path.with_suffix(memory_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")  # noqa: SIM115
            flock_exclusive(fd)
            try:
                content = memory_path.read_text(encoding="utf-8")
                ot_match = re.search(
                    r"(## Open Threads\n)(.*?)(?=\n## |\Z)",
                    content, re.DOTALL,
                )
                if not ot_match:
                    return

                ot_header = ot_match.group(1)
                ot_body = ot_match.group(2)
                lines = ot_body.split("\n")
                keep_lines: list[str] = []
                archived_lines: list[str] = []

                for line in lines:
                    stripped = line.strip()
                    if not stripped.startswith("- ") or "\u2705" not in stripped:
                        keep_lines.append(line)
                        continue
                    entry_date = self._parse_ot_entry_date(stripped)
                    if entry_date is None or entry_date >= cutoff:
                        keep_lines.append(line)
                        continue
                    # Resolved and older than cutoff — archive it
                    archived_lines.append(stripped)
                    logger.info("Archiving resolved OT entry: %s", stripped[:80])

                if not archived_lines:
                    return

                # Rewrite MEMORY.md without the archived entries
                new_ot_body = "\n".join(keep_lines)
                new_content = (
                    content[:ot_match.start()]
                    + ot_header + new_ot_body
                    + content[ot_match.end():]
                )
                # MemoryGuard: sanitize before writing
                try:
                    from core.memory_guard import MemoryGuard
                    new_content = MemoryGuard().sanitize(new_content)
                except ImportError:
                    pass  # memory_guard module not available yet
                except Exception as guard_exc:
                    logger.warning(
                        "context_health: MemoryGuard failed during OT archival: %s",
                        guard_exc,
                    )
                memory_path.write_text(new_content, encoding="utf-8")

                # Archive via the single chokepoint → gitignored private .context/
                # (source_path=memory_path lands it as MEMORY.md's sibling, NEVER the
                # git-tracked Knowledge/Archives/ — CYCLE 1').
                from core.ddd_entry_lifecycle import archive_raw_lines
                today = date.today()
                archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
                archive_raw_lines(
                    root, archived_lines, archive_name,
                    source_path=memory_path,
                    block_header=f"### Archived Open Threads ({today.isoformat()})",
                    create_header=f"# Memory Archive — {today.strftime('%Y-%m')}",
                )
                logger.info(
                    "context_health: archived %d resolved OT entries to .context/%s",
                    len(archived_lines), archive_name,
                )
            finally:
                flock_unlock(fd)
        except Exception as exc:
            logger.warning("context_health: OT archival failed: %s", exc)
        finally:
            if fd:
                fd.close()

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

    def _validate_entity_index(self, root: Path) -> list[str]:
        """Thin wrapper for individual channel invocation (used by tests)."""
        from core.ddd_orchestrator import DddCultivationOrchestrator
        return DddCultivationOrchestrator()._ch_entity_index(root, str(root))
