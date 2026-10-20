"""Runtime hooks for real-time correction capture and error pattern detection.

These hooks fire DURING agent execution (not post-session) via the Claude
Agent SDK hook system.  All hooks are observe-only — they log and inject
additionalContext but never block or modify tool inputs/outputs.

Key public symbols:

- ``register_runtime_hooks``         — Wire all runtime hooks into a HookRegistry
- ``create_correction_capture_hook`` — PostToolUseFailure → corrections.jsonl
- ``create_error_pattern_detector``  — PostToolUseFailure → hint after 2+ failures
- ``create_user_correction_detector``— UserPromptSubmit → corrections.jsonl
"""

import asyncio
import json
import logging
import os
import re
import stat
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hook_builder import HookRegistry

logger = logging.getLogger(__name__)

# Default corrections log path — can be overridden in factory functions
from jobs.paths import STATE_DIR as _STATE_DIR
_DEFAULT_CORRECTIONS_PATH = str(
    _STATE_DIR / "corrections.jsonl"
)

# Consecutive failure threshold before injecting a hint
_FAILURE_HINT_THRESHOLD = 2

# ── Enforcement injector (run_e57b7554) — per-turn UserPromptSubmit reminders ──
# Direct-mode triggers (CN + EN). A match means the user explicitly asked to skip
# the pipeline / "just do it" — the ONE sanctioned bypass (STEERING #13 / R1) —
# which is exactly the moment the adversarial-review-still-mandatory reminder must
# fire. Case-insensitive; word-anchored on the EN side so "adjust it" etc. don't hit.
_DIRECT_MODE_RE = re.compile(
    r"直接做|直接干|直接改|跳过\s*pipeline|不走\s*pipeline|别走\s*pipeline|别用\s*pipeline|不用\s*pipeline"
    r"|\bjust do it\b|\bskip (?:the )?pipeline\b|\bskip the ceremony\b",
    re.IGNORECASE,
)
# CJK share of SEMANTIC UNITS (cjk_chars / (cjk_chars + latin_words)) at/above which
# a message that contains CJK is treated as Chinese. Weighing CJK-chars against
# Latin-WORDS (not a raw char ratio) is deliberate: it keeps XG's normal
# Chinese-with-English-tech-terms style ("帮我 review 这个 CI log") classified as
# Chinese, while a lone CJK quote inside a many-word English sentence stays English.
# 0.30 = a message needs meaningfully more CJK structure than English words to flip
# to Chinese, but a handful of English terms can't drag real Chinese to English.
# Boundary-tested in test_runtime_hooks.py.
_LANG_CJK_WEIGHT_THRESHOLD = 0.30

# Rotation: keep the newest N entries when file exceeds MAX_SIZE_BYTES.
# 500 entries × ~1.5KB = ~750KB — well within reason for a local log.
_MAX_CORRECTIONS_ENTRIES = 500
_MAX_CORRECTIONS_SIZE_BYTES = 512 * 1024  # 512KB trigger threshold

# Correction pattern regex — conservative to minimize false positives.
# Matches when patterns appear at word boundaries or start of string.
_CORRECTION_PATTERNS_EN = re.compile(
    r"""(?ix)                   # case-insensitive, verbose
    (?:^|\b)(?:
        (?:that(?:'s|s)?\s+)?(?:wrong|incorrect|not\s+right|not\s+correct)
      | no[\s,]+(?:that|it|this)
      | actually[\s,]+(?:no\b|not\b|don'?t|shouldn'?t|isn'?t|wasn'?t|can'?t|won'?t|wouldn'?t|never\b)
      | you(?:'re|\s+are)\s+wrong
      | that(?:'s|s)?\s+not\s+(?:what|how)
      | I\s+(?:said|meant|asked)
      | 不对
      | 错了
      | 搞错
      | 你搞错了
      | 不是这样
      | 应该是
    )
    """
)

# Meta-cognitive / Socratic correction patterns (gap fix run_e681a61d).
# These capture corrections phrased as a REDIRECT or REFRAME rather than an
# explicit "you're wrong". COR02-disciplined: every pattern is a multi-word
# phrase with correction-specific SEMANTICS — NOT a bare keyword (no lone 吗/去),
# so genuine info-questions ("这个怎么用吗", "你能去查一下文档吗") don't trigger.
#
# KNOWN, DELIBERATE RECALL GAP — pure question-form challenges are NOT captured.
# Corrections phrased as a bare interrogative — "你知道X吗", "你确定X吗",
# "你是不是X" — are intentionally LEFT OUT. They are structurally identical to
# benign info-questions ("你知道这个怎么用吗"), so a pure regex cannot tell a
# challenge from a question without false positives — exactly the COR02 class.
# This is an accepted recall gap, NOT a bug: precision is held over recall here.
# Capturing question-form challenges requires SEMANTIC judgment (interrogative +
# reference to the agent's own prior claim/action), which belongs on a different
# surface (see Designs/2026-06-25-self-evolution-closed-loop-design.md, M3.5
# APPLY-gate / Stop-hook), NOT in this hot-path regex. Do NOT "fix" this by
# adding 你知道/你确定/吗 here — that re-opens COR02. The boundary is locked in
# BOTH directions by test_runtime_hooks.py: test_detects_meta_cognitive_redirect
# (imperatives MUST fire) + test_question_form_challenges_are_a_known_recall_gap
# (interrogatives MUST NOT fire).
_CORRECTION_PATTERNS_META = re.compile(
    r"""(?ix)
    (?:
        # Investigate-redirect: imperative "go (and) look/check ..." — steering
        # the agent to re-investigate. Requires the directive verb at a boundary.
        (?:^|[\s,。，、]) (?:go\s+(?:and\s+)?(?:check|look|investigate|verify|dig)\b)
      | (?:^|[\s,。，、]) (?:你\s*)?去\s*(?:查|看|核实|确认|检查|验证)
        # "你看下/你看一下 X" — imperative "(you) take a look at" redirect. Requires
        # the leading 你 (directive at the agent) so it doesn't match 我看下 (I'll
        # look) or 看下面/看下文 (look below — a reference, not a correction).
      | (?:^|[\s,。，、]) 你\s*看\s*(?:一)?下(?!面|文|方)
        # Reframe: "rethink / reconsider / look again at ..." — discard current path.
      | (?:^|\b) (?:re-?think|re-?consider|re-?examine|look\s+again)\b
      | 重新\s*(?:想|看|考虑|检查|评估|分析|审视)
        # Contrastive correction: "not X, (but) Y" / "不是 X，是 Y" — explicit
        # redirect away from the agent's stated direction. Needs the paired marker.
      | 不是.{0,30}(?:而是|应该|是别的|是另)
      | (?:it'?s|that'?s)\s+not\s+.{0,40}\b(?:but|it'?s|rather)\b
    )
    """
)


# Free-text VALUE fields per correction entry type that carry attacker-influenced
# content and are later replayed/fed-to-LLM. `user_correction.prompt` is scanned
# with sentence-split because judgment_classifier feeds it RAW to a live Bedrock
# call (a 2nd-sentence payload is a real threat there). `tool_failure` fields are
# deliberately NOT scanned — they are self-generated tool output/input, FP-prone,
# and an attacker controlling them already controls the agent (low marginal safety).
_CORRECTION_SCAN_FIELDS: dict[str, tuple[str, ...]] = {
    "user_correction": ("prompt",),
    "subagent_finding": ("summary",),
}
_CORRECTION_SENTENCE_SPLIT_FIELDS: tuple[str, ...] = ("prompt",)


def _append_correction(path: str, entry: dict) -> None:
    """Append a correction entry to JSONL file, rotating when oversized.

    Write-time injection gate (run_6af300b3): before writing, scan the
    attacker-influenced free-text fields for injection patterns. On a hit the
    entry is DROPPED (not written) and logged — poison never enters the store, so
    it can never be replayed into a future agent's context or fed to the judgment
    classifier's live LLM. See core/injection_patterns.py.

    Rotation: delegates to ``utils.jsonl_rotation.rotate_jsonl_if_oversized``
    (512 KB trigger, keeps newest 500 entries).  One stat() per write;
    rotation is rare (~monthly at normal usage).  Best-effort — never raises.
    """
    try:
        # ── Write-time injection gate: drop a poisoned entry, never store it ──
        scan_fields_for_type = _CORRECTION_SCAN_FIELDS.get(str(entry.get("type", "")))
        if scan_fields_for_type:
            from core.injection_patterns import scan_fields
            to_scan = {f: entry.get(f) for f in scan_fields_for_type}
            hits = scan_fields(to_scan, sentence_split_fields=_CORRECTION_SENTENCE_SPLIT_FIELDS)
            if hits:
                logger.warning(
                    "Dropped correction entry (type=%s): injection pattern(s) %s in field(s) %s",
                    entry.get("type"), list(hits.values()), list(hits.keys()),
                )
                return

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Append the new entry
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)

        # Rotate if oversized
        from utils.jsonl_rotation import rotate_jsonl_if_oversized
        rotate_jsonl_if_oversized(
            p,
            max_size_bytes=_MAX_CORRECTIONS_SIZE_BYTES,
            max_entries=_MAX_CORRECTIONS_ENTRIES,
        )

    except Exception:
        logger.exception("Failed to write correction to %s", path)


def _extract_field(data: Any, field: str, default: Any = "") -> Any:
    """Extract field from dict or object — SDK hook inputs can be either."""
    if isinstance(data, dict):
        return data.get(field, default)
    return getattr(data, field, default)


# ---------------------------------------------------------------------------
# PostToolUseFailure: correction capture → corrections.jsonl
# ---------------------------------------------------------------------------

def create_correction_capture_hook(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUseFailure hook that logs tool failures.

    Args:
        corrections_path: Path to corrections.jsonl (default: ~/.swarm-ai/state/)
        session_context: Session context dict for session_id extraction
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        tool_input = _extract_field(input_data, "tool_input", {})
        error = _extract_field(input_data, "error", "")

        entry = {
            "ts": time.time(),
            "session_id": sid,
            "type": "tool_failure",
            "tool": tool,
            "input_summary": str(tool_input)[:500],
            "error": str(error)[:1000],
        }
        _append_correction(path, entry)
        ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1
        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUseFailure: error pattern detection → additionalContext hint
# ---------------------------------------------------------------------------

def create_error_pattern_detector(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUseFailure hook that detects consecutive failures.

    After 2+ consecutive failures on the same tool, injects an additionalContext
    hint to nudge the agent toward a different approach.

    State is stored in session_context["_failure_tracker"] (per-session, no globals).
    A paired success hook (from ``create_failure_tracker_reset``) clears the
    counter when a tool succeeds — so "consecutive" means truly consecutive.
    """
    # Per-tool consecutive failure counter — stored in session_context
    tracker_key = "_failure_tracker"
    ctx = session_context or {}
    if tracker_key not in ctx:
        ctx[tracker_key] = defaultdict(int)

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        error = _extract_field(input_data, "error", "")
        tracker = ctx[tracker_key]
        tracker[tool] += 1
        count = tracker[tool]

        if count >= _FAILURE_HINT_THRESHOLD:
            hint = (
                f"[System: {tool} has failed {count} consecutive times. "
                f"Last error: {str(error)[:200]}. Consider a different approach.]"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUseFailure",
                    "additionalContext": hint,
                }
            }

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: reset failure tracker on success
# ---------------------------------------------------------------------------

def create_failure_tracker_reset(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUse hook that resets the consecutive failure counter.

    When a tool succeeds, its entry in ``_failure_tracker`` is cleared so that
    the error pattern detector only counts truly consecutive failures.
    """
    tracker_key = "_failure_tracker"
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        tracker = ctx.get(tracker_key)
        if tracker and tool in tracker:
            tracker[tool] = 0
        return {}

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: correction pattern detection → corrections.jsonl
# ---------------------------------------------------------------------------

def create_user_correction_detector(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a UserPromptSubmit hook that detects user corrections.

    Scans user prompts for correction signals (CN + EN patterns) and logs
    to corrections.jsonl.  Observe-only — does not inject additionalContext.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        prompt = _extract_field(input_data, "prompt", "")
        if not prompt:
            return {}

        # Two tiers of match (adversarial MED, run_e681a61d):
        #  - explicit_error: "that's wrong / 不对 / 应该是" — a recorded MISTAKE.
        #    Logs to corrections.jsonl AND (if it passes the value gate) writes a
        #    MEMORY [pitfall]. Golden-case seeding is NO LONGER done here (moved
        #    post-session + CLASS-gated, M5 :306); the MEMORY write is now likewise
        #    value-gated via is_memory_worthy_correction (run_4443a967, :317).
        #  - meta_only: a redirect/reframe ("去查 X / 重新想 / go check") — STEERING,
        #    not a mistake. Logs to corrections.jsonl (for the post-session
        #    classifier to judge) but does NOT write a MEMORY [pitfall] — a redirect
        #    is not a "do not repeat" pattern, and recording one pollutes MEMORY.
        is_explicit_error = bool(_CORRECTION_PATTERNS_EN.search(prompt))
        is_meta = bool(_CORRECTION_PATTERNS_META.search(prompt))
        if is_explicit_error or is_meta:
            entry = {
                "ts": time.time(),
                "session_id": sid,
                "type": "user_correction",
                "prompt": prompt[:1000],
            }
            _append_correction(path, entry)
            ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1
            ctx["_correction_just_detected"] = True  # Signal for observation DDD event

            # Evolution v3 Phase 1: classification + counting moved to the
            # post-session judgment classifier (governance_router.classify_new_corrections),
            # which assigns a REAL class (CLASS_A/B/C) instead of "UNCLASSIFIED" and
            # gates on a watermark. Counting here was a premature blind increment that
            # (a) double-counted against the post-session record() and (b) left every
            # entry stuck at UNCLASSIFIED (never promoted). The corrections.jsonl append
            # above is the durable signal the classifier consumes. (Gate-1 fix, run_7a8f9866.)

            # Persistent side-effects (MEMORY pitfall) fire ONLY for explicit-error
            # corrections — a recorded mistake. META-only redirects are steering,
            # not pitfalls; recording them would pollute MEMORY with non-pitfalls.
            if is_explicit_error:
                # NOTE (M5 Part 2, run_0305426d): golden-case seeding was REMOVED
                # from this hot path. It used to fire seed_from_correction(...,
                # "UNCLASSIFIED") synchronously on every explicit-error prompt —
                # BEFORE any classification — which dumped unclassified test-session
                # noise (GS_C_test-ses_*) straight into golden_set. Seeding now
                # happens POST-SESSION via governance_router.classify_new_corrections
                # (evolution_maintenance_hook), gated on a REAL CLASS
                # (counter_state=pending_confirm) so operator/transient noise
                # (counter_state=ignored) never seeds a case. The corrections.jsonl
                # append above is the durable signal that path consumes.

                # Gap #17: Immediate correction → MEMORY.md.
                # Best-effort — failure must never break the hook chain.
                #
                # JUDGE-GATED (run_04fd397c, XG decision A): this was a MEMORY BACKDOOR —
                # it wrote via a value-floor (is_memory_worthy_correction) that is NOT the
                # self_adversarial judge, violating "the judge is the sole admit authority"
                # (P8). Now routes through admit_memory_lesson — the SAME judge every other
                # MEMORY door uses. verdict=="auto" → write to the judge-routed section
                # (fail-closed: judge error/suspect/noise → discard, NOT written). The
                # corrections.jsonl append above is the durable signal the post-session
                # classifier consumes, so a judge-discard here loses NO signal, only noise.
                try:
                    from pathlib import Path as _Path
                    from core.ingestion_gate import admit_memory_lesson
                    from scripts.locked_write import locked_read_modify_write
                    summary = prompt[:150].replace("\n", " ").strip()
                    # DETERMINISTIC CORRECTION FLOOR (restored — call-out #2): a raw
                    # correction prompt is a SIGNAL, not automatically a lesson. Strip the
                    # trigger phrases ("that's wrong", "use X instead"); if the residue
                    # teaches nothing (< MIN_LESSON_LENGTH) it must NEVER reach MEMORY —
                    # even if the judge would pass it. 2c8fc37f dropped this floor and left
                    # the judge as the only guard, so a pure "That's wrong, use async
                    # instead" would sediment as a [pitfall]. This floor is correction-
                    # specific (belongs to THIS door, not the shared memory_distill tiers,
                    # which must not strip trigger phrases from a normal reflection lesson).
                    # The corrections.jsonl append already happened above — rejecting here
                    # loses NO signal, only declines to sediment a non-lesson.
                    try:
                        from core.ddd_cultivation import is_memory_worthy_correction
                        _worthy = is_memory_worthy_correction(summary)
                    except Exception:  # noqa: BLE001 — floor error → fail-closed (don't write)
                        logger.debug("_hook: suppressed exception on a fail-safe path", exc_info=True)
                        _worthy = False
                    if not _worthy:
                        verdict = "discard"
                        distilled = None
                    else:
                        # Judge decides ADMIT/REJECT only; a correction is semantically a
                        # [pitfall]→## Pitfalls (its fixed home since Gap #17), so we do NOT
                        # re-route on the judge's section (that would write "[pitfall]" into
                        # e.g. ## Corrections — a type/section mismatch, adversarial BUG#1).
                        verdict, _section, _reason, distilled = admit_memory_lesson(summary)
                    ws = _Path.home() / ".swarm-ai" / "SwarmWS"
                    if verdict == "pending":
                        # RECOVERABLE (judge budget-exhausted / infra down) — DEFER to the
                        # shared distill-pending.jsonl, never drop. Fixes the silent-drop:
                        # the SSOT door used to collapse pending→discard, so once the judge
                        # window filled, every correction was lost with no archive.
                        try:
                            from hooks.distillation_hook import requeue_pending_lesson
                            requeue_pending_lesson(ws / ".context", "lesson", summary)
                        except Exception:  # noqa: BLE001 — defer is best-effort
                            logger.debug("_hook: suppressed exception on a fail-safe path", exc_info=True)
                            pass
                    elif verdict == "auto":
                        # ROOT-FIX (capture-vs-distill): if the gate distilled a
                        # shape-dirty entry, write the DISTILLED rule, never our own
                        # raw summary (writer≠finalizer). fail-open: distilled=None →
                        # keep summary.
                        body = distilled or summary
                        memory_path = ws / ".context" / "MEMORY.md"
                        if memory_path.exists():
                            today = time.strftime("%Y-%m-%d")
                            entry_text = (
                                f"\n- [pitfall] **{body}** — "
                                f"({today}, {sid[:8]}, correction)\n"
                            )
                            # dedup=True: don't re-append a lesson already in MEMORY.
                            locked_read_modify_write(
                                memory_path, "## Pitfalls", entry_text,
                                mode="append", dedup=True,
                            )
                except Exception:
                    logger.debug("_hook: suppressed exception on a fail-safe path", exc_info=True)
                    pass  # Non-blocking — MEMORY write is best-effort

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: file tracker — records Read/Edit/Write file paths
# ---------------------------------------------------------------------------

# DoD2: MultiEdit/NotebookEdit are file-writing tools the sibling-exclude set
# (auto_commit_hook._other_live_sessions_touched) must see — before this they
# were invisible, so a sibling's MultiEdit/NotebookEdit could be swept into
# another session's commit. NotebookEdit uses `notebook_path`, not `file_path`.
_TRACKED_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def create_file_tracker(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUse hook that tracks files touched during the session.

    Populates ``session_context["_files_touched"]`` (a set) with file paths from
    Read/Edit/Write/MultiEdit (``file_path``), NotebookEdit (``notebook_path``),
    and Bash write targets (``>``/``>>``/``tee``/``cp``/``mv`` via
    ``parse_bash_write_targets``).  Used by PreCompact injection, session
    checkpoint, AND the auto_commit sibling-exclude set (R29/DEC30) — the latter
    is why coverage must be complete: a missed write is a path a sibling session
    could sweep into its own commit.
    """
    ctx = session_context or {}

    def _add_touched(path: str) -> None:
        if not path:
            return
        if "_files_touched" not in ctx:
            ctx["_files_touched"] = set()
        ctx["_files_touched"].add(path)

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "")

        if tool == "Bash":
            tool_input = _extract_field(input_data, "tool_input", {})
            command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
            # Detect test/scan execution (Self-Monitoring evidence)
            if "pytest" in command or "python -m pytest" in command:
                ctx["_ran_tests"] = True
            # DoD2: record Bash write targets (`>`/`>>`/tee/cp/mv DEST) so a
            # Bash-written file lands in _files_touched (reuses the SSOT parser —
            # never a second write-target parser). Fail-safe: parser returns []
            # on anything it can't confidently resolve.
            if command:
                try:
                    from core.file_change_classifier import parse_bash_write_targets
                    for tgt in parse_bash_write_targets(command):
                        _add_touched(tgt)
                except Exception as e:  # never break the hook path
                    logger.debug("file_tracker: bash write-target parse failed: %s", e)
            return {}

        if tool not in _TRACKED_TOOLS:
            return {}

        tool_input = _extract_field(input_data, "tool_input", {})
        if isinstance(tool_input, dict):
            # Edit/Write/MultiEdit → file_path; NotebookEdit → notebook_path.
            _add_touched(tool_input.get("file_path", ""))
            _add_touched(tool_input.get("notebook_path", ""))

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: session checkpoint — crash survival
# ---------------------------------------------------------------------------

_DEFAULT_CHECKPOINT_PATH = str(
    _STATE_DIR / "session_checkpoint.json"
)
_DEFAULT_CHECKPOINT_INTERVAL = 10


def _get_recent_git_commits(workspace_dir: str, since_ts: float) -> list[str]:
    """Get recent git commits since a timestamp. Returns list of oneline strings.

    Subprocess with 2s timeout — never blocks the agent. Returns empty on any error.
    """
    from datetime import datetime, timezone

    try:
        since_str = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        result = subprocess.run(
            ["git", "log", "--oneline", "-5", f"--since={since_str}"],
            capture_output=True, text=True, timeout=2,
            cwd=workspace_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[:5]
    except Exception:
        logger.debug("_get_recent_git_commits: suppressed exception on a fail-safe path", exc_info=True)
        pass
    return []


def create_session_checkpoint(
    session_context: Optional[dict] = None,
    checkpoint_path: Optional[str] = None,
    interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
    workspace_dir: Optional[str] = None,
):
    """Factory: creates a PostToolUse hook that writes a session checkpoint.

    Every ``interval`` tool calls:
    1. Overwrites checkpoint JSON with current session state (crash recovery).
    2. Appends a content snapshot to today's DailyActivity (mid-session memory).

    On crash, ``recover_crash_checkpoint()`` reads the JSON and writes to
    DailyActivity on next startup.  For normal sessions, the DailyActivity
    append ensures content is captured even if post-session hooks don't fire.
    """
    path = checkpoint_path or _DEFAULT_CHECKPOINT_PATH
    ctx = session_context or {}
    from jobs.paths import SWARMWS as _SWARMWS
    ws = workspace_dir or str(_SWARMWS)
    counter_key = "_tool_count"
    start_ts_key = "_session_start_ts"
    last_da_count_key = "_last_da_checkpoint_count"

    if counter_key not in ctx:
        ctx[counter_key] = 0
    if start_ts_key not in ctx:
        ctx[start_ts_key] = time.time()

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        ctx[counter_key] = ctx.get(counter_key, 0) + 1
        count = ctx[counter_key]

        if count % interval != 0:
            return {}

        session_id = ctx.get("sdk_session_id", "unknown")
        files = sorted(ctx.get("_files_touched", set()))
        corrections = ctx.get("_corrections_count", 0)
        start_ts = ctx.get(start_ts_key, time.time())

        # Fetch recent git commits (2s timeout, never blocks)
        git_commits = _get_recent_git_commits(ws, start_ts)

        # 1. Write checkpoint JSON (crash recovery)
        # Enrich with observation data if available
        recent_observations = []
        session_summary = {}
        ring = ctx.get("_observations")
        if ring is not None:
            try:
                recent_observations = ring.snapshot(last_n=10)
                # Compute simple summary stats
                all_obs = ring.all_completed()
                if all_obs:
                    from collections import Counter
                    tool_counts = Counter(o.tool_name for o in all_obs)
                    dominant = tool_counts.most_common(1)[0] if tool_counts else ("", 0)
                    test_runs = sum(1 for o in all_obs if o.tool_name == "Bash" and "pytest" in o.intent)
                    test_passes = sum(1 for o in all_obs if o.tool_name == "Bash" and "pytest" in o.intent and o.result_status == "success")
                    session_summary = {
                        "dominant_tool": dominant[0],
                        "dominant_count": dominant[1],
                        "test_runs": test_runs,
                        "test_pass_rate": round(test_passes / test_runs, 2) if test_runs > 0 else None,
                    }
                ring.pending_cleanup()
            except Exception:
                logger.debug("_hook: suppressed exception on a fail-safe path", exc_info=True)
                pass  # Observation enrichment is best-effort

        checkpoint = {
            "session_id": session_id,
            "ts": time.time(),
            "tool_count": count,
            "files_touched": files[:20],  # Cap for JSON size
            "corrections_count": corrections,
            "git_commits": git_commits,
            "recent_observations": recent_observations,
            "session_summary": session_summary,
        }

        # OFF-LOOP (run_a1f4c2d8): SDK hook callbacks are awaited ON the event loop
        # mid-session, so a mkdir + write here stalled every other request and every
        # chat tab's SSE stream. Small writes, but they fire on a cadence during an
        # active turn — exactly when latency is visible.
        def _write_checkpoint() -> None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")

        try:
            await asyncio.to_thread(_write_checkpoint)
        except Exception:
            logger.exception("Failed to write session checkpoint to %s", path)

        # 2. Append content snapshot to DailyActivity (mid-session memory)
        # Only write if new files or commits since last checkpoint.
        # Track both file count AND commit count to prevent redundant writes
        # when git_commits is non-empty but unchanged since last checkpoint.
        last_file_count = ctx.get(last_da_count_key, 0)
        last_commit_count = ctx.get("_last_da_commit_count", 0)
        has_new_files = len(files) > last_file_count
        has_new_commits = len(git_commits) > last_commit_count
        if not has_new_files and not has_new_commits:
            return {}

        try:
            from datetime import datetime as dt
            now = dt.now()
            da_dir = Path(ws) / "Knowledge" / "DailyActivity"
            da_dir.mkdir(parents=True, exist_ok=True)
            da_file = da_dir / f"{now.strftime('%Y-%m-%d')}.md"

            # Build content-capped entry (target < 1KB)
            lines = [
                f"\n## {now.strftime('%H:%M')} | {session_id[:8]} | 📸 Mid-session checkpoint\n",
            ]
            if files:
                file_summary = ", ".join(f"`{Path(f).name}`" for f in files[:10])
                if len(files) > 10:
                    file_summary += f" (+{len(files) - 10} more)"
                lines.append(f"**Files:** {file_summary}\n")
            if git_commits:
                lines.append("**Git activity:**\n")
                for c in git_commits[:3]:
                    lines.append(f"- `{c[:72]}`\n")
            if corrections:
                lines.append(f"**Corrections:** {corrections}\n")

            entry = "".join(lines)
            # Hard cap at 1KB
            if len(entry.encode("utf-8")) > 1024:
                entry = entry[:1000] + "\n...(truncated)\n"

            # Concurrency note: we use plain open("a") instead of locked_write.py.
            # Our entries are <1KB (hard-capped above), and on macOS/APFS small
            # appends (<4KB) to a single file are effectively atomic at the
            # filesystem level.  These hooks are observe-only and crash-safe by
            # design — a torn write loses one checkpoint entry, which is
            # acceptable for mid-session snapshots.
            # OFF-LOOP (run_a1f4c2d8): the append goes to a thread; the atomicity
            # reasoning above is unchanged (still one plain open("a") of a <1KB entry).
            def _append_da(_f=da_file, _e=entry) -> None:
                with open(_f, "a", encoding="utf-8") as f:
                    f.write(_e)

            await asyncio.to_thread(_append_da)

            ctx[last_da_count_key] = len(files)
            ctx["_last_da_commit_count"] = len(git_commits)
            logger.debug(
                "Mid-session checkpoint written to DailyActivity: %d files, %d commits",
                len(files), len(git_commits),
            )
        except Exception:
            logger.debug("Failed to write mid-session checkpoint to DailyActivity", exc_info=True)

        # ── Self-Monitoring gate: multi-file change without test evidence ──
        # If agent has edited >1 source file but never ran pytest/test,
        # inject a reminder via additionalContext (soft gate — warns, doesn't block).
        edited_source_files = [
            f for f in files
            if any(f.endswith(ext) for ext in (".py", ".ts", ".tsx", ".rs"))
            and "/tests/" not in f
            and "/test_" not in f
        ]
        has_test_evidence = ctx.get("_ran_tests", False)

        if len(edited_source_files) > 1 and not has_test_evidence:
            return {
                "additionalContext": (
                    "[Self-Monitoring] You have edited "
                    f"{len(edited_source_files)} source files without running "
                    "tests or post-task scan. Before declaring done, run "
                    "targeted tests: `pytest tests/test_<module>.py --timeout=60`"
                )
            }

        return {}

    return _hook


# ---------------------------------------------------------------------------
# SubagentStop: transcript capture
# ---------------------------------------------------------------------------

_SUBAGENT_TAIL_BYTES = 5 * 1024  # Read last 5KB of transcript

_ERROR_PATTERNS = re.compile(
    r"""(?i)(?:
        Error:\s+\S+
      | Exception:\s+\S+
      | FAILED\s+tests/
      | AssertionError
      | ImportError
      | FileNotFoundError
      | KeyError
      | TypeError
      | ValueError
      | RuntimeError
    )""",
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# SubagentStop: Pipeline Agent Tool Audit — write marker file for Gate 2
# ---------------------------------------------------------------------------

_PIPELINE_AUDIT_DIR = _STATE_DIR / "pipeline_agent_audit"

# ---------------------------------------------------------------------------
# Session<->run rendezvous (R1.5a — the D7 "key system")
# ---------------------------------------------------------------------------
# The future D7 commit-gate must resolve "which pipeline run does the committing
# SESSION belong to". The two ids never meet anywhere else: a pipeline run_id is
# minted by the `artifact_cli.py run-create` CLI SUBPROCESS (printed to stdout as
# {"pipeline_id": "run_xxx"}), while the session_id the gate keys on is the
# APP-LEVEL id in `session_context['sdk_session_id']` — set in the DAEMON at
# session_router.py:3421, NOT the SDK subprocess's own CLAUDE_CODE_SESSION_ID env
# (a DIFFERENT namespace — a CLI reading that env would write the mapping under the
# wrong key and D7 would never find it; verified by trace, R1.5a Gate-1).
#
# Two writers produce the map. (a) A PostToolUse-Bash hook: it runs in the daemon (so
# ctx carries the correct app-level session_id) AND the SDK hands it the Bash
# tool_response (so it sees run-create's stdout). (b) `run-create` itself, keyed on
# SWARM_APP_SESSION_ID — the daemon injects the SAME app-level id into the agent's
# env, unlike CLAUDE_CODE_SESSION_ID above — so the link holds even when run-create's
# stdout is redirected and (a) sees nothing. The hook (1) revives the EXISTING-but-
# -set-nowhere `_active_pipeline_run_id` consumer (read at the marker writer below +
# the compaction briefing) and (2) persists a session_<sid>_run.map file into the
# SAME _PIPELINE_AUDIT_DIR the gate already scans by session_id.

# Matches the run-create stdout line. The CLI prints a compact JSON object; we read
# the pipeline_id field rather than regex-sniffing a bare run_xxx so a run id quoted
# elsewhere in the output (an error echoing a prior id) can't be mistaken for THIS
# call's result.
_RUN_CREATE_CMD_RE = re.compile(r"artifact_cli\.py\s+run-create\b")


def _session_run_map_path(session_id: str) -> Path:
    """Disk rendezvous file: session_id -> owning pipeline run_id.

    Lives in _PIPELINE_AUDIT_DIR (the same dir the adversarial commit gate scans by
    session_id), so a future cross-process reader resolves it the identical way it
    already finds markers. One file per session; last run-create in a session wins
    (a session runs one pipeline at a time — sibling sessions get distinct ids).
    """
    return _PIPELINE_AUDIT_DIR / f"session_{session_id}_run.map"


def write_session_run_map(session_id: str, run_id: str, ts: float | None = None) -> None:
    """Atomically write the session->run rendezvous (tmp sibling + os.replace).

    The ONE writer of the .map shape ``{session_id, run_id, ts}``: the PostToolUse hook
    below (live, parses run-create stdout), ``artifact_cli run-create`` itself (so a
    run whose stdout was redirected is still linked) and ``artifact_cli stage-start``
    (so a session working on an older run credits reviews to it) all call it. Raises on I/O
    error; callers decide whether that is fatal."""
    _PIPELINE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    dest = _session_run_map_path(session_id)
    payload = json.dumps({"session_id": session_id, "run_id": run_id,
                          "ts": time.time() if ts is None else ts})
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(payload)
    os.replace(tmp, dest)


def read_session_run_map(session_id: str) -> "tuple[str, float] | None":
    """``(run_id, ts)`` from the session's .map, or None (missing / corrupt / empty
    run_id). Sync file I/O: async callers dispatch it off-loop. Never raises."""
    try:
        if not session_id:
            return None
        p = _session_run_map_path(session_id)
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        run_id = data.get("run_id")
        if not (isinstance(run_id, str) and run_id.strip()):
            return None
        try:
            ts = float(data.get("ts"))
        except (TypeError, ValueError):
            ts = 0.0
        return run_id.strip(), ts
    except Exception:  # noqa: BLE001 — attribution read must never break a hook
        logger.debug("read_session_run_map: suppressed exception on a fail-safe path",
                     exc_info=True)
        return None


def _extract_pipeline_id(tool_response: Any) -> Optional[str]:
    """Pull `pipeline_id` out of a run-create tool_response (stdout), or None.

    tool_response may be a dict, a str (raw stdout), or a list of content blocks
    depending on how the SDK surfaces it — handle all three, never raise."""
    def _from_text(text: str) -> Optional[str]:
        # The CLI prints exactly one compact JSON object with pipeline_id. Find the
        # LAST such object (the final line is the result; earlier lines may be logs).
        for m in reversed(list(re.finditer(r'\{[^{}]*"pipeline_id"\s*:\s*"([^"]+)"[^{}]*\}', text))):
            return m.group(1)
        return None

    if tool_response is None:
        return None
    if isinstance(tool_response, dict):
        pid = tool_response.get("pipeline_id")
        if isinstance(pid, str) and pid:
            return pid
        # SDK sometimes wraps stdout under common keys
        for k in ("stdout", "output", "content", "text"):
            v = tool_response.get(k)
            if isinstance(v, str) and v:
                got = _from_text(v)
                if got:
                    return got
        return None
    if isinstance(tool_response, str):
        return _from_text(tool_response)
    if isinstance(tool_response, list):
        for block in tool_response:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if isinstance(t, str):
                    got = _from_text(t)
                    if got:
                        return got
    return None


def create_pipeline_run_mapping_hook(session_context: Optional[dict] = None):
    """PostToolUse-Bash hook: join the just-created run_id with THIS session.

    On a `artifact_cli.py run-create` Bash call, parses the printed pipeline_id from
    tool_response, sets ``session_context['_active_pipeline_run_id']`` (reviving the
    existing-but-dead consumer the marker writer reads), and persists a
    session_<sid>_run.map rendezvous file. The disk write runs OFF-LOOP
    (asyncio.to_thread) + atomic (write temp, os.replace) — the same discipline the
    sibling SubagentStop audit hook uses, so a busy disk never blocks the event loop
    and a crash mid-write never leaves a torn map. Pure plumbing: writes NO gate
    verdict, changes NO existing marker shape."""
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        if _extract_field(input_data, "tool_name", "") != "Bash":
            return {}
        tool_input = _extract_field(input_data, "tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if not command or not _RUN_CREATE_CMD_RE.search(command):
            return {}

        tool_response = _extract_field(input_data, "tool_response", None)
        run_id = _extract_pipeline_id(tool_response)
        if not run_id:
            return {}  # run-create failed / unparseable → nothing to map

        session_id = ctx.get("sdk_session_id", "") or ""
        # Revive the in-process consumer immediately (marker writer + compaction
        # briefing read ctx['_active_pipeline_run_id']). This half needs no disk.
        # Stamp the SAME ts into ctx and onto the disk .map so the two writers
        # (this live hook + session_router's resume re-hydration) order consistently
        # under last-write-wins (keystone R1.5a-fix / Gate-1 H1: freshness, not presence).
        _now = time.time()
        ctx["_active_pipeline_run_id"] = run_id
        ctx["_active_pipeline_run_id_ts"] = _now

        if not session_id:
            return {}  # no session to key the disk rendezvous on — ctx half still done

        def _persist() -> None:
            try:
                # Atomic (a torn map read by the gate would be worse than a missing one).
                write_session_run_map(session_id, run_id, ts=_now)
            except Exception as e:  # never break the hook path
                logger.debug("pipeline_run_mapping: persist failed: %s", e)

        await asyncio.to_thread(_persist)
        return {}

    return _hook


# R1.5b-i (方案 Z, zero Claude coupling): the expert identity is OUR OWN
# information — we decide which expert to spawn and author its prompt — so it must
# NOT round-trip through the SDK agent-registry (that is the rejected X/Y coupling:
# .claude/agents or ClaudeAgentOptions.agents=). Instead the spawn prompt carries a
# structured anchor line `EXPERT-ID: <id>` (injected by deliver.md/gate2-adversarial.md),
# and we extract it from the sub-agent's own transcript head — the same head already
# read for adversarial-intent classification, so no new transcript read.
#
# Anchored + bare-id-only ON PURPOSE: the line must START a line (`^EXPERT-ID:`),
# the id must be a lowercase registry id (`[a-z][a-z0-9-]*`), and nothing but
# whitespace may follow it (`\s*$`). This rejects the drift classes the natural-
# language `_is_adversarial_intent` classifier suffers from — a Title-case value, an
# embedded-in-prose mention, a trailing-words line — by returning None (fail-safe: a
# wrong-shaped line yields "unattributed", never a wrong expert). This REPLACES the
# former `_expert_id_from_agent_type`, which derived the id from the SDK `agent_type`
# — the rejected coupling path, and `general-purpose` for real spawns.
#
# We validate the FIRST `EXPERT-ID:`-prefixed line ONLY — never scan past a malformed
# one. The contract puts the anchor on line 1 of the spawn prompt; the orchestrator
# fills review content (requirement text, changed-file paths, pasted excerpts) BELOW
# it, inside the same 2000-char head window. A plain `.search` for the WELL-FORMED
# pattern would skip an unfilled/malformed line 1 (`EXPERT-ID: <EXPERT_ID>`) and match
# an attacker/author-influenced `EXPERT-ID: <id>` line in that content → wrong
# attribution. Binding to the first EXPERT-ID line closes that: a malformed anchor
# yields None, it is never silently overridden by injected content below.
_EXPERT_ID_PREFIX = re.compile(r"^EXPERT-ID:.*$", re.MULTILINE)
_EXPERT_ID_LINE = re.compile(r"^EXPERT-ID:\s*([a-z][a-z0-9-]*)\s*$")


def _extract_expert_id(prompt_head: str) -> Optional[str]:
    """Extract the registry expert_id from a spawn-prompt head's `EXPERT-ID:` anchor
    line, or None if absent/malformed.

    Binds to the FIRST line beginning `EXPERT-ID:` and validates ONLY that line — it
    does NOT scan forward past a malformed anchor into injected review content below
    (which would let an author-influenced `EXPERT-ID: <id>` line mis-attribute the
    expert).

    Fail-safe by design: no `EXPERT-ID:` line, or a first one that is not a well-formed
    `EXPERT-ID: <lowercase-id>`, returns None — it does NOT fall back to the SDK
    agent_type (that was the rejected coupling path, and agent_type is `general-purpose`
    for real spawns anyway). None means "unattributed"; the D7 gate (R1.5b-ii) treats
    None as never-a-specific-expert."""
    if not prompt_head:
        return None
    prefix = _EXPERT_ID_PREFIX.search(prompt_head)
    if not prefix:
        return None
    m = _EXPERT_ID_LINE.match(prefix.group(0))
    return m.group(1) if m else None


# Adversarial-intent classification (Plan B v4). The commit gate
# (security_hooks.create_adversarial_commit_gate) must pass ONLY when an
# ADVERSARIAL-review sub-agent completed this session — not any sub-agent (an
# Explore/investigation agent must NOT satisfy it). The spike (run_df2668b4)
# established: the SDK gives SubagentStop `agent_type` + `agent_transcript_path`
# (the sub-agent's OWN transcript, whose HEAD is the spawn prompt), and there is
# NO tool_use_id on SubagentStop to correlate back to the Agent PreToolUse call —
# so classification happens HERE, at completion, per-agent-correct by construction.
#
# subagent_type is the PRIMARY signal (a dedicated adversarial/reviewer type is
# unambiguous); the spawn prompt is a LIBERAL keyword fallback. Bias LIBERAL on the
# adversarial side (the threat model is the honest 手滑 "test passed → commit"
# reflex, NOT a malicious bypass — a false-block just trains users toward the FORCE
# escape hatch), but require ADVERSARIAL-review vocabulary (refute/attack/find bugs/
# red-team/poke holes/stress-test/skeptic/对抗/挑刺), NOT bare "review"/"find code",
# so a locate/investigate Explore agent is cleanly excluded.
# agent_type must be ADVERSARIALLY-qualified — bare "reviewer" is NOT enough (a
# generic code-reviewer doing a style pass is not an adversarial review; REVIEW
# HIGH-2). Require an adversarial/red-team/skeptic qualifier.
_ADVERSARIAL_TYPE_RE = re.compile(
    r"adversar|red.?team|skeptic|adversarial.?review", re.IGNORECASE
)
# Intent verbs that signal ADVERSARIAL review. Two tiers, either matches:
#  (1) strong standalone terms — refute/attack/red-team/hunt/audit/vulnerab/…
#      (liberal per the threat model: a false-block just pushes users to FORCE).
#  (2) "find/look-for <defect>" — but ONLY when bound to the DIFF/CHANGE subject,
#      so an Explore prompt ("find the config file", "find bugs get logged") is
#      NOT swept in (REVIEW MED-2). The bare "find bugs" with no diff-anchor is
#      deliberately excluded.
_DEFECT = r"(?:bugs?|regressions?|issues?|flaws?|holes?|weakness(?:es)?|vulnerab\w*|edge\s+cases?)"
_SUBJECT = r"(?:this|the)\s+(?:diff|change|changeset|code|design|implementation|fix|pr|patch)"
# Optional adjectives an adversarial prompt puts between the verb and the defect:
# "find ANY bugs", "find SECURITY issues", "find POTENTIAL regressions".
_QUAL = r"(?:(?:any|all|potential|possible|security|hidden|subtle|real)\s+){0,3}"
_ADVERSARIAL_INTENT_RE = re.compile(
    r"adversar|refute|red.?team|poke\s+holes?|stress.?test|"
    r"try\s+to\s+break|break\s+(?:this|it)\b|review\s+as\s+a\s+skeptic|"
    r"challenge\s+(?:the|this|your)|critically\s+review|"
    r"hunt\s+for\s+" + _QUAL + _DEFECT + r"|audit\s+" + _SUBJECT + r"|"
    # "find/look for <qual> <defect> in the code/diff" — the SUBJECT includes 'code',
    # so "find security issues in the code" matches; still excludes Explore prompts
    # like "find the config file" (no defect token) and "find bugs get logged" (no
    # 'in <subject>').
    r"(?:find|look\s+for)\s+" + _QUAL + _DEFECT + r"[^.\n]{0,40}?\bin\s+" + _SUBJECT + r"|"
    # "review this diff ... find <defect>" — adversarial review phrased subject-first
    r"review\s+" + _SUBJECT + r"[^.\n]{0,40}?\b(?:find|for)\s+" + _QUAL + _DEFECT + r"|"
    r"attack\s+" + _SUBJECT + r"|"
    r"对抗|挑刺|找\s*bug|找出\s*bug|攻击(?:这|该)",
    re.IGNORECASE,
)


_ADV_EXPERTS_CACHE: "list" = [None]  # [(signature, ids)] — one slot, one assignment


def _registered_adversarial_experts() -> frozenset:
    """Ids of registered experts whose frontmatter says ``spawn_as: adversarial``.

    Cached on the expert files' names + mtimes, so a SubagentStop does not re-parse
    the whole tree each time. A registry that fails to load is logged at WARNING (it
    makes every expert fall back to keyword classification, and D7 then refuses)
    and yields an empty set; classification falls back to the keywords."""
    try:
        from core.expert_manager import _EXPERTS_ROOT, scan_experts
        # rglob: the same file set scan_experts reads, so any expert edit re-keys it
        sig = tuple(sorted((str(p), p.stat().st_mtime_ns)
                           for p in _EXPERTS_ROOT.rglob("*.md")))
        cached = _ADV_EXPERTS_CACHE[0]
        if cached is not None and cached[0] == sig:
            return cached[1]
        ids = frozenset(eid for eid, info in scan_experts().items()
                        if str(getattr(info, "spawn_as", "")).strip().lower() == "adversarial")
        if not ids:
            # An absent or empty library (e.g. a packaged build without experts/) reads
            # the same as "no expert is adversarial": say so instead of falling back silently.
            logger.warning("expert registry at %s has no spawn_as: adversarial expert; "
                           "EXPERT-ID spawns fall back to keyword classification", _EXPERTS_ROOT)
        _ADV_EXPERTS_CACHE[0] = (sig, ids)
        return ids
    except Exception:  # noqa: BLE001 — a broken registry must not break the hook
        logger.warning("expert registry unreadable: EXPERT-ID spawns fall back to keyword "
                       "adversarial classification", exc_info=True)
        return frozenset()


def _is_adversarial_intent(subagent_type: str, description: str, prompt: str) -> bool:
    """True if a sub-agent was spawned for ADVERSARIAL review (vs Explore/investigate).

    Three signals, any one suffices: (1) subagent_type names an adversarial role;
    (2) the prompt's first EXPERT-ID line names a registered expert whose
    ``spawn_as`` is ``adversarial`` — the registry is the authority, so the wording of
    the rest of the prompt does not matter (run_a87bfe3f: two experts spawned with a
    plain "read your instructions" second line were not counted and D7 refused);
    (3) a liberal keyword match on description + prompt. Empty everything → False
    (no evidence = not adversarial; fail-safe: the gate should DENY, not silently
    pass, when intent is unknowable)."""
    st = str(subagent_type or "")
    if _ADVERSARIAL_TYPE_RE.search(st):
        return True
    eid = _extract_expert_id(prompt or "")
    if eid and eid in _registered_adversarial_experts():
        return True
    blob = f"{description or ''}\n{prompt or ''}"
    return bool(_ADVERSARIAL_INTENT_RE.search(blob))


def create_agent_tool_audit_hook(
    session_context: Optional[dict] = None,
):
    """Factory: creates a SubagentStop hook that writes an audit marker file.

    When the Agent tool completes (SubagentStop event), writes a marker file
    at ``STATE_DIR/pipeline_agent_audit/<run_id>.marker``. The pipeline
    validator reads this file to confirm the Agent tool was actually invoked
    during adversarial review — structural proof, not honor-system.

    Three markers (the first two are the original pipeline-validator evidence,
    unchanged; the third gates the commit hook):
    1. If ``_active_pipeline_run_id`` is set in session_context: writes
       ``<run_id>.marker`` (precise match — pipeline orchestrator sets this).
    2. Always: writes ``session_<session_id>_<ts>.marker`` as session-level
       evidence. The validator (Check 9b) accepts either form. It carries the
       ``expert_id`` of the prompt's EXPERT-ID line and, for a gate skeptic
       (gate0-skeptic / gate1-skeptic), the ``verdict`` its final answer gave.
    3. CONDITIONAL: if THIS sub-agent's intent is adversarial-review (classified
       via _is_adversarial_intent on agent_type + the transcript-head spawn
       prompt), ALSO writes ``session_<session_id>_adv_<ts>.marker`` carrying a
       ``reviewed_paths`` key. This is the evidence security_hooks.
       create_adversarial_commit_gate requires — via _session_adversarial_coverage,
       which DIFF-BINDS the commit: the committed path-set must be covered by the
       union of these markers' reviewed_paths. An investigation/Explore sub-agent
       writes markers 1–2 but NOT this one, so it cannot satisfy the commit gate.
    """
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        session_id = ctx.get("sdk_session_id", "")
        run_id = ctx.get("_active_pipeline_run_id")
        ctx_ts = ctx.get("_active_pipeline_run_id_ts")

        if not session_id and not run_id:
            return {}

        # agent_type is read cheaply on-loop (it's already in input_data). The
        # transcript READ + intent classification + marker writes ALL run in ONE
        # off-loop thread hop below — the sub-agent transcript is on the same disk
        # the daemon is actively writing, so reading it inline would block the event
        # loop on a busy disk (RP53; the sibling create_subagent_capture_hook already
        # offloads its transcript read via to_thread).
        agent_type = _extract_field(input_data, "agent_type", "")
        transcript_path = _extract_field(input_data, "agent_transcript_path", "")
        last_message = _extract_field(input_data, "last_assistant_message", "")
        hook_cwd = _extract_field(input_data, "cwd", "")

        try:
            _PIPELINE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.time()

            # OFF-LOOP (run_a1f4c2d8 + run_df2668b4): transcript read, classification,
            # and BOTH/all markers in ONE thread hop — the reads+writes are one audit
            # record; splitting them would double hops and let a cancellation land
            # mid-record.
            def _classify_and_write() -> None:
                # The run this marker belongs to: the NEWER of the session ctx and the
                # session's .map. run-create writes the .map itself, so a run whose
                # stdout was redirected (the PostToolUse hook saw nothing and ctx still
                # names the previous run) still gets its own reviewers in the same turn.
                owner = run_id
                linked = read_session_run_map(session_id) if session_id else None
                if linked is not None:
                    known = float(ctx_ts) if isinstance(ctx_ts, (int, float)) else float("-inf")
                    if not owner or linked[1] > known:
                        owner = linked[0]
                # Read the sub-agent's spawn-prompt head ONCE, UNCONDITIONALLY (R1.5b-i):
                # it feeds BOTH adversarial-intent classification AND expert_id
                # extraction. It MUST be read outside the `if not adversarial` branch —
                # if agent_type already classifies adversarial, the head was never read
                # before, so the EXPERT-ID line (and thus expert_id) would never be
                # extracted → completed-expert set empty → the D7 gate self-locks. One
                # read here (still inside this off-loop thread hop) serves both uses.
                prompt_head = (
                    _read_subagent_prompt_head(transcript_path) if transcript_path else ""
                )
                # Classify THIS sub-agent's intent: agent_type primary; the HEAD of
                # its OWN transcript (spawn prompt) as fallback. Per-agent-correct by
                # construction — this SubagentStop fires for exactly one sub-agent.
                # Which run and which expert: a prompt FILE that stage-start (or
                # gate2-recheck / gate0-prompt) wrote under runs/<run_id>/evidence/prompts/
                # and the sub-agent read in full names both, so a session working on two
                # runs credits the right one, and a "Read <file> and follow it" spawn with
                # no EXPERT-ID line of its own still counts (16 such spawns 2026-10-18).
                reads = _prompt_reads(transcript_path) if transcript_path else None
                owner, expert_id, attributed_by = _resolve_attribution(
                    _extract_expert_id(prompt_head) if prompt_head else None,
                    _handout_attribution(reads, session_id, prompt_head), owner)
                adversarial = _is_adversarial_intent(agent_type, "", "")
                if not adversarial and (prompt_head or expert_id):
                    # the resolved id goes first so the registry check sees a file-derived one
                    adversarial = _is_adversarial_intent(
                        agent_type, "", (f"EXPERT-ID: {expert_id}\n" if expert_id else "") + prompt_head)

                # Write run-specific marker if run_id available (primary path)
                if owner:
                    marker_file = _PIPELINE_AUDIT_DIR / f"{owner}.marker"
                    marker_file.write_text(json.dumps({
                        "ts": ts,
                        "event": "SubagentStop",
                        "run_id": owner,
                        "session_id": session_id,
                    }))

                # Always write session-level marker as fallback evidence. UNCHANGED
                # shape — pipeline_validator Check 9b + _session_has_subagent_evidence
                # both read this; do not rename/restructure it.
                # Microseconds: parallel sub-agents of one session finishing in the same
                # second used to overwrite each other's marker (an undercount of rounds).
                session_marker = _PIPELINE_AUDIT_DIR / f"session_{session_id}_{int(ts * 1_000_000)}.marker"
                # ``expert_id`` is ADDITIVE (existing readers ignore unknown keys): it lets
                # stage-complete count review rounds per expert from the one marker every
                # completed sub-agent writes — the _adv_ marker below exists only for
                # sub-agents classified adversarial, so counting from it undercounts.
                base = {
                    "ts": ts,
                    "event": "SubagentStop",
                    "session_id": session_id,
                    "run_id": owner or "unknown",
                    "expert_id": expert_id,
                    # "prompt_file" = run / expert taken from a handed-out prompt file it
                    # read; "session" = the session's newest run + the prompt's own line
                    "attributed_by": attributed_by,
                    # Key present = this daemon captures prompt reads for EVERY sub-agent
                    # ({} = none read, None = transcript unreadable → readers warn). Gate-1,
                    # Gate-0 and the Gate-2 re-check bind to the prompt file handed out.
                    "prompt_reads": reads,
                    # when it was spawned (its transcript's first record): REVIEW / DELIVER
                    # stage-complete require one round's experts to start together
                    "started_at": _subagent_started_at(transcript_path) if transcript_path else None,
                }
                if expert_id in _GATE_VERDICT_RE:
                    # Key present = this daemon captures verdicts (None = unparsed answer).
                    # The SDK hands the sub-agent's last assistant text in; the transcript
                    # tail is the fallback for an input without it.
                    final = last_message if isinstance(last_message, str) and last_message.strip() \
                        else (_read_subagent_final_text(transcript_path) if transcript_path else "")
                    base["verdict"] = _gate_verdict(expert_id, final)
                    if expert_id in ("root-cause", "gate1-skeptic"):
                        # root-cause: its reasons go into REPORT §10 / the PLAN redesign
                        # verbatim; gate1-skeptic: its BLOCK / WARN points go into the
                        # build record and REVIEW.md (artifact_cli._gate1_points)
                        base["verdict_text"] = (final or "")[:2000]
                elif expert_id:
                    # Key present = this daemon records the expert's own findings and
                    # re-check answers (DELIVER stage-complete compares the record).
                    final = last_message if isinstance(last_message, str) and last_message.strip() \
                        else (_read_subagent_final_text(transcript_path) if transcript_path else "")
                    base.update(_expert_answer(final))
                session_marker.write_text(json.dumps(base))

                # DISTINCT adversarial marker — consumed by the live commit gate
                # `_session_adversarial_coverage` (security_hooks.py), which matches the
                # `_adv_` infix AND reads `reviewed_paths` below to diff-bind coverage.
                # Written ONLY on adversarial completion + only when we have a session to
                # scope it to. The `_adv_` infix is what the gate matches; a base marker
                # (ts is numeric) can never contain it, so the two are unambiguous.
                if adversarial and session_id:
                    payload = {
                        "ts": ts,
                        "event": "SubagentStop",
                        "session_id": session_id,
                        "adversarial": True,
                        "agent_type": agent_type or "unknown",
                        # R1.5b-ii: carry the owning run_id so the D7 commit-gate can
                        # attribute this completed expert to the CORRECT run — a sibling
                        # run in the SAME session must not satisfy a requirement it did
                        # not review (Gate-1 cross-run finding). None/"unknown" when no
                        # pipeline run owns this session (manual adversarial spawn).
                        "run_id": owner or None,
                        # R1.5b-i: carry WHICH expert ran (structured), not just THAT an
                        # adversarial sub-agent ran — the D7 gate (R1.5b-ii) matches a
                        # run's declared required_experts against the expert_ids that
                        # actually completed. The id comes from the `EXPERT-ID:` anchor
                        # line WE injected into the spawn prompt (方案 Z, zero Claude
                        # coupling) and read back from this sub-agent's transcript head —
                        # NOT from the SDK agent_type (which is `general-purpose` for real
                        # spawns). Nullable: None = unattributed (no well-formed line).
                        # The same resolution as the base marker (prompt file first).
                        "expert_id": expert_id,
                    }
                    # Plan A diff-relevance: bind the review to the paths it covered.
                    # KEY PRESENCE is load-bearing — reviewed_paths present (incl. [])
                    # bounds coverage; ABSENT = unbounded (git unavailable, back-compat).
                    # The gate reads this by key presence, never Python truthiness.
                    reviewed = _reviewed_paths_at_head(hook_cwd) if hook_cwd else None
                    if reviewed is not None:
                        payload["reviewed_paths"] = reviewed
                    # Microseconds, like the base marker: same-second siblings must not
                    # overwrite each other (D7 would miss an expert that did run).
                    adv_marker = _PIPELINE_AUDIT_DIR / f"session_{session_id}_adv_{int(ts * 1_000_000)}.marker"
                    adv_marker.write_text(json.dumps(payload))

            await asyncio.to_thread(_classify_and_write)
        except Exception:
            logger.warning("Failed to write agent audit marker (session=%s)", session_id)

        return {}

    return _hook


def _read_subagent_prompt_head(transcript_path: str) -> str:
    """Return the sub-agent's spawn prompt from the HEAD of its transcript (the
    first sidechain user message), or "" on any error. The spawn prompt reveals
    adversarial-review intent; unlike the error-capture hook (which reads the
    TAIL for errors), intent lives at the head. Bounded read, fail-soft."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(12):  # scan only the first few records
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message", rec)
                content = msg.get("content", "") if isinstance(msg, dict) else msg
                if isinstance(content, list):  # content-block array
                    # Join with "\n", NOT " ": the SDK can split the first user
                    # message into multiple text blocks (observed: a resume-history
                    # block + the prompt block). A SPACE join collapses the newline
                    # after the `EXPERT-ID: <id>` anchor line into trailing text on
                    # that line, so the `^EXPERT-ID: <id>\s*$` extractor (which
                    # requires the id to END the line) fails → expert_id=None → the
                    # D7 commit-gate false-blocks a completed review. "\n" preserves
                    # the real line boundary so the anchor stays its own line, while
                    # genuinely authored trailing junk on one line stays junk (the
                    # extractor regex is UNCHANGED — the fail-safe tests still hold).
                    content = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                if isinstance(content, str) and content.strip():
                    return content[:2000]
        return ""
    except Exception:
        logger.debug("_read_subagent_prompt_head: suppressed exception on a fail-safe path", exc_info=True)
        return ""


# A gate skeptic's OWN verdict, read from its final answer, so stage-complete can compare
# the verdict the orchestrator records with what the skeptic said (artifact_cli
# `_gate_verdict_mismatch`). Only LINE-ANCHORED verdict lines count — gate1: a line that
# starts "GATE 1 VERDICT:" (the mandated first line of its report); gate0: a line whose
# first ≤40 chars lead to "verdict:" ("4. Verdict: …", "Claim 2 — Verdict: …") — and a
# template line listing the options ("PASS | WARN | BLOCK") never counts. When several
# lines give different tokens the MOST SEVERE wins: quoted plan / claim text (DATA the
# orchestrator wrote) can then only make the parsed verdict stricter, never turn a BLOCK
# into a PASS (red-team 2026-10-18). No such line → None (stage-complete warns).
_GATE_VERDICT_RE = {
    "gate0-skeptic": re.compile(
        r"^[^\n]{0,40}?\bverdict\W{0,6}(UNSUPPORTED|ALREADY-SATISFIED|WRONG-FRAME|SUPPORTED)\b"
        r"(?!\s*\|)", re.I | re.M),
    "gate1-skeptic": re.compile(
        r"^\W*GATE\s*1\s*VERDICT\W{0,6}(PASS|WARN|PARTIAL|BLOCK)\b(?!\s*\|)", re.I | re.M),
    # Gate-2 fixing-spec skeptic (core/gate2_fixspec): judges the spec, not the code.
    "fixspec-skeptic": re.compile(
        r"^\W*FIXSPEC\s*VERDICT\W{0,6}(PASS|BLOCK)\b(?!\s*\|)", re.I | re.M),
    # Gate-2 root-cause review after a capped, unconverged loop (core/gate2_convergence).
    "root-cause": re.compile(
        r"^\W*ROOT[-\s]?CAUSE\s*VERDICT\W{0,6}(SOUND|WRONG-APPROACH)\b(?!\s*\|)", re.I | re.M),
}
# Severity: the most severe token on the anchored lines is the skeptic's verdict.
GATE_VERDICT_RANK = {"PASS": 0, "WARN": 1, "PARTIAL": 1, "BLOCK": 2,
                     "SUPPORTED": 0, "UNSUPPORTED": 1, "ALREADY-SATISFIED": 1, "WRONG-FRAME": 1,
                     "SOUND": 0, "WRONG-APPROACH": 1}
_GATE_ANSWER_TAIL_BYTES = 256 * 1024  # fallback transcript read; the SDK input carries the text


def _subagent_started_at(transcript_path: str) -> Optional[float]:
    """Epoch seconds of the sub-agent's first transcript record (its spawn), or None.
    stage-complete compares these to tell one parallel wave from serial spawns."""
    try:
        import datetime as _dt
        with open(Path(transcript_path), "r", encoding="utf-8", errors="replace") as f:
            for _ in range(12):
                line = f.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("timestamp") if isinstance(rec, dict) else None
                if isinstance(t, str) and t:
                    return _dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        return None
    return None


def _read_subagent_final_text(transcript_path: str) -> str:
    """The text of the sub-agent's LAST assistant message (bounded tail read), or ""."""
    try:
        p = Path(transcript_path)
        size = p.stat().st_size
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            if size > _GATE_ANSWER_TAIL_BYTES:
                f.seek(size - _GATE_ANSWER_TAIL_BYTES)
                f.readline()  # skip the partial first line
            lines = f.read().splitlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message", rec)
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(content, str) and content.strip():
                return content
        return ""
    except Exception:
        logger.debug("_read_subagent_final_text: suppressed exception on a fail-safe path", exc_info=True)
        return ""


# A Bash READ of a prompt file: `cat <path>` (optionally with flags). Merely naming the
# path (`ls`, `test -f`, a comment) is no read (red-team 2026-10-18).
_PROMPT_CAT_RE = re.compile(r"(?:^|[;&|(]\s*|\s)cat\s+(?:-\w+\s+)*['\"]?([^\s'\"`;|&]*/evidence/prompts/[\w.-]+\.md)")
_TRANSCRIPT_SCAN_BYTES = 16 * 1024 * 1024  # real gate1 transcripts: p50 ~290 KB, max ~540 KB
_PROMPT_FILE_MAX_BYTES = 2 * 1024 * 1024  # handed-out prompts are a few KB; change.diff ≤ 60 KB


def _prompt_reads(transcript_path: str) -> Optional[dict]:
    """{path: sha256 of the file now} for every pipeline prompt file
    (``…/evidence/prompts/*.md``) the sub-agent loaded IN FULL — a Read without
    offset / limit, or a Bash ``cat <path>`` — plus ``<inline:N>`` hashes of its own
    prompt (each text block and the whole message, with / without a trailing newline), so
    an exact paste of the handed-out file binds too. Lets stage-complete check that a
    gate skeptic read the exact prompt stage-start handed out (artifact_cli
    ``_gate1_prompt_unread``). The file is hashed at SubagentStop: a copy edited and
    restored while the skeptic ran is not caught (deliberate cheating, not a cut corner).
    None when the transcript cannot be read (the checker then warns instead of blocking)."""
    import hashlib
    out: dict = {}
    try:
        p = Path(transcript_path)
        if p.stat().st_size > _TRANSCRIPT_SCAN_BYTES:
            return None
        paths: list[str] = []
        first_user = True
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if first_user and '"user"' in line:
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        rec = None
                    if isinstance(rec, dict) and rec.get("type") == "user":
                        first_user = False
                        msg = rec.get("message", rec)
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                        blocks = ([b.get("text", "") for b in content
                                   if isinstance(b, dict) and b.get("type") == "text"]
                                  if isinstance(content, list) else [content])
                        texts = [t for t in blocks + ["\n".join(blocks)] if isinstance(t, str) and t]
                        shas = {hashlib.sha256(v.encode("utf-8")).hexdigest()
                                for t in texts for v in (t, t.rstrip("\n"), t.rstrip("\n") + "\n")}
                        for n, h in enumerate(sorted(shas)):
                            out[f"<inline:{n}>"] = h
                        # the WHOLE first message only (an exact spawn-text check must not
                        # match one block of a message that carries a steering block too)
                        whole = "\n".join(blocks) if isinstance(content, list) else content
                        if isinstance(whole, str) and whole:
                            firsts = {hashlib.sha256(v.encode("utf-8")).hexdigest()
                                      for v in (whole, whole.rstrip("\n"), whole.rstrip("\n") + "\n")}
                            for n, h in enumerate(sorted(firsts)):
                                out[f"<first:{n}>"] = h
                        continue
                if '"tool_use"' not in line or "evidence/prompts/" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = rec.get("message", rec) if isinstance(rec, dict) else {}
                content = msg.get("content") if isinstance(msg, dict) else None
                for b in content if isinstance(content, list) else []:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if (b.get("name") == "Read" and isinstance(inp.get("file_path"), str)
                            and inp.get("offset") in (None, 0, 1) and inp.get("limit") is None):
                        paths.append(inp["file_path"])  # a partial Read is no read of the gate
                    elif b.get("name") == "Bash" and isinstance(inp.get("command"), str):
                        paths += _PROMPT_CAT_RE.findall(inp["command"])
        for path in dict.fromkeys(paths):
            fp = Path(os.path.expanduser(path))
            # The REAL file must be a regular prompt-sized file under evidence/prompts/:
            # `<run>/evidence/prompts/../../../big.img` or a FIFO is never read (this
            # runs for every sub-agent, in the hook thread).
            real = Path(os.path.realpath(fp))
            if "/evidence/prompts/" not in fp.as_posix() or "/evidence/prompts/" not in real.as_posix():
                continue
            try:
                st = real.stat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_size <= _PROMPT_FILE_MAX_BYTES:
                out[fp.as_posix()] = hashlib.sha256(real.read_bytes()).hexdigest()
        return out
    except Exception:
        logger.debug("_prompt_reads: suppressed exception on a fail-safe path", exc_info=True)
        return None


# A prompt file the pipeline tools hand out: <...>/.artifacts/runs/<run_id>/evidence/prompts/<name>.md
_HANDOUT_PATH_RE = re.compile(r"/\.artifacts/runs/(run_[A-Za-z0-9_-]{1,64})/evidence/prompts/[\w.-]+\.md$")


_RUN_JSON_MAX_BYTES = 4 * 1024 * 1024


def _projects_root() -> Optional[str]:
    """realpath of the workspace's Projects dir (where every run lives), or None."""
    try:
        from config import get_app_data_dir  # local import: avoids a load-time cycle
        return os.path.realpath(get_app_data_dir() / "SwarmWS" / "Projects")
    except Exception:  # noqa: BLE001
        return None


def _handout_attribution(reads: Optional[dict], session_id: str, spawn_prompt: str = "") -> list:
    """Distinct (run_id, expert_id) pairs named by the handed-out prompt files a
    sub-agent read in full (``_prompt_reads`` keys): the run from the file's path, the
    expert from the file's own ``EXPERT-ID: <id>`` first line (the tools write it first).

    A file counts only when ALL hold — anything unprovable names nothing (fail closed):
    its spawn prompt names the file (a sub-agent spawned to do something else that
    happens to read an expert's prompt is not that expert); the real path lies under the
    workspace's Projects dir as Projects/<P>/.artifacts/runs/<run_id>/evidence/prompts/;
    that run's run.json is a regular file whose ``session_id`` is this session (a foreign
    or unstamped run is never credited). A file with no EXPERT-ID line names nothing.
    Never raises."""
    pairs: list = []
    root = _projects_root()
    if not (root and session_id and spawn_prompt):
        return pairs
    home = os.path.expanduser("~")
    for path in (reads or {}):
        try:
            if path.startswith("<"):  # <inline:N> / <first:N> hashes, not files
                continue
            named = path in spawn_prompt or (path.startswith(home + "/")
                                             and "~" + path[len(home):] in spawn_prompt)
            if not named:
                continue
            real = os.path.realpath(path)
            m = _HANDOUT_PATH_RE.search(real)
            rel = os.path.relpath(real, root) if m else ""
            if not m or rel.startswith("..") or rel.count("/") != 6:
                continue  # <P>/.artifacts/runs/<run>/evidence/prompts/<name>.md
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                first = next((ln for ln in (f.readline() for _ in range(5)) if ln.strip()), "")
            eid = _extract_expert_id(first.rstrip("\n"))
            if not eid:
                continue
            run_json = Path(real).parent.parent.parent / "run.json"
            st = os.stat(run_json)
            if not stat.S_ISREG(st.st_mode) or st.st_size > _RUN_JSON_MAX_BYTES:
                continue
            data = json.loads(run_json.read_text(encoding="utf-8"))
            if not (isinstance(data, dict) and data.get("session_id") == session_id):
                continue
            pair = (m.group(1), eid)
            if pair not in pairs:
                pairs.append(pair)
        except Exception:  # noqa: BLE001 — attribution must never break the hook
            logger.debug("_handout_attribution: suppressed exception", exc_info=True)
    return pairs


def _resolve_attribution(head_eid: Optional[str], pairs: list,
                         owner: Optional[str]) -> "tuple[Optional[str], Optional[str], str]":
    """(run_id, expert_id, attributed_by) for one completed sub-agent.

    1. The prompt's own ``EXPERT-ID:`` line names the expert; a handed-out file of THAT
       expert it read in full names the run (exactly one run, else ambiguous → session).
    2. No such line: the handed-out file names both, if it read exactly one.
    3. Otherwise the session's newest run (``owner``) and the line's id (maybe None)."""
    if head_eid:
        runs = {r for r, e in pairs if e == head_eid}
        if len(runs) == 1:
            return next(iter(runs)), head_eid, "prompt_file"
        return owner, head_eid, "session"
    if len(pairs) == 1:
        return pairs[0][0], pairs[0][1], "prompt_file"
    return owner, None, "session"


# A Gate-2 expert's OWN answer, kept on its marker so DELIVER stage-complete can check the
# findings the orchestrator recorded against what the expert reported (artifact_cli
# ``_gate2_findings_errors``) and a re-check's RESOLVED / NOT RESOLVED per item.
_REPORTED_MAX = 60
_SEV_TOKEN = re.compile(r"^(CRITICAL|BLOCKER|BLOCK|HIGH|MEDIUM|MED|LOW)$", re.I)
# A re-check answer line: "R1: RESOLVED — …", "- R2 (a.py:12): NOT RESOLVED", "Item R3 is
# unresolved". The verdict words are searched on the rest of the line.
_RECHECK_LINE_RE = re.compile(r"^\W*(?:item\s+)?R(\d{1,3})\b([^\n]{0,200})", re.I | re.M)
_CODE_BLOCK_RE = re.compile(r"```[\w-]*\n([\s\S]*?)```")


# A finding's own text fields kept on the marker row besides the six the gates count on
# (the Gate-2 ledger keeps only its own MODEL_FIELDS). Bounded; a list / dict value is
# kept as its JSON text. They are payload, not identity: _expert_answer de-duplicates
# on the six.
_FINDING_TEXT_FIELDS = ("fix", "exploit", "evidence")
_FINDING_TEXT_MAX = 600


def _finding_row(d: dict) -> Optional[dict]:
    sev = str(d.get("severity") or "").strip()
    if not _SEV_TOKEN.match(sev):
        return None
    line = d.get("line")
    row = {"severity": sev.upper(), "confidence": d.get("confidence"),
           "path": str(d.get("path") or "")[:300],
           "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
           "category": str(d.get("category") or "")[:80],
           "summary": str(d.get("summary") or d.get("description") or "")[:240]}
    for k in _FINDING_TEXT_FIELDS:
        v = d.get(k)
        if isinstance(v, (list, dict)) and v:
            try:
                v = json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError):
                v = None
        if isinstance(v, str) and v.strip():
            row[k] = v.strip()[:_FINDING_TEXT_MAX]
    return row


_FINDING_IDENTITY = ("severity", "confidence", "path", "line", "category", "summary")


def _rows_of(obj) -> list:
    """Finding rows in one parsed JSON value: a finding object, a list of them, or a
    meta-review object whose ``risks`` list holds them (``description`` = summary)."""
    if isinstance(obj, list):
        return [r for x in obj for r in _rows_of(x)]
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("risks"), list):
        return [r for x in obj["risks"] if isinstance(x, dict)
                for r in [_finding_row({**x, "summary": x.get("summary") or x.get("description")})] if r]
    r = _finding_row(obj)
    return [r] if r else []


def _expert_answer(final_text: str) -> dict:
    """Findings and re-check answers from a Gate-2 expert's final answer.

    ``reported`` = its findings: whole JSON values first (each fenced code block and the
    whole answer: a findings array, a meta-review ``{"risks": [...]}``), then one JSON
    object per line (trailing commas allowed), de-duplicated. ``reported_state`` =
    "findings" | "no_findings" (no finding and the answer ENDS in ``NO FINDINGS``, or a
    meta-review ``"verdict": "CLEAR"``) | "unparsed" (neither: DELIVER refuses to compare
    blind). ``recheck_answers`` = {"<n>": "RESOLVED" | "NOT RESOLVED"} from ``R<n> …``
    lines; NOT RESOLVED wins when an item is answered both ways."""
    text = final_text or ""
    rows: list = []
    for chunk in [m.group(1) for m in _CODE_BLOCK_RE.finditer(text)] + [text]:
        try:
            rows += _rows_of(json.loads(chunk.strip()))
        except (ValueError, TypeError):
            pass
    for raw in text.splitlines():
        s = raw.strip().lstrip("-*• ").strip().rstrip(",").strip()
        if not (s.startswith("{") and s.endswith("}")) or "severity" not in s:
            continue
        try:
            rows += _rows_of(json.loads(s))
        except (ValueError, TypeError):
            continue
    uniq: list = []
    seen: set = set()
    for r in rows:   # the same finding twice (fenced and inline) with other fix text is one
        key = tuple(repr(r.get(k)) for k in _FINDING_IDENTITY)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    answers: dict = {}
    for n, rest in _RECHECK_LINE_RE.findall(text):
        n = str(int(n))
        if re.search(r"NOT\s+RESOLVED|UNRESOLVED", rest, re.I):
            answers[n] = "NOT RESOLVED"
        elif re.search(r"\bRESOLVED\b", rest, re.I) and answers.get(n) != "NOT RESOLVED":
            answers[n] = "RESOLVED"
    tail = next((ln for ln in reversed(text.strip().splitlines()) if ln.strip()), "")
    if uniq:
        state = "findings"
    elif re.search(r"\bNO FINDINGS\b", tail) or re.search(r"\"verdict\"\s*:\s*\"CLEAR\"", text):
        state = "no_findings"
    else:
        state = "unparsed"
    return {"reported": uniq[:_REPORTED_MAX], "reported_state": state,
            "recheck_answers": answers}


def _gate_verdict(expert_id: Optional[str], final_text: str) -> Optional[str]:
    """The verdict a gate skeptic gave in ``final_text`` (upper-case): the most severe
    token on its anchored verdict lines (first such line on a tie), else None."""
    rx = _GATE_VERDICT_RE.get(expert_id or "")
    found = [t.upper() for t in rx.findall(final_text or "")] if rx else []
    if not found:
        return None
    return max(found, key=lambda t: (GATE_VERDICT_RANK.get(t, 0), -found.index(t)))


# Git timeout for the diff-relevance capture (Plan A). Bounded so a hung/locked git
# never wedges the SubagentStop thread (mirrors the gate's fail-open discipline).
_GIT_CAPTURE_TIMEOUT = 10


def _repo_root_for(dir_path: str) -> Optional[str]:
    """Canonical repo-root resolver — MUST be identical to the gate's, so the
    writer's and the gate's absolute paths compare byte-for-byte (macOS /var vs
    /private/var etc.). Returns realpath of `git -C <dir> rev-parse --show-toplevel`,
    or None if not a git repo / git unavailable / timeout."""
    if not dir_path:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", dir_path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=_GIT_CAPTURE_TIMEOUT,
        )
        if r.returncode != 0:
            return None
        top = r.stdout.strip()
        return __import__("os").path.realpath(top) if top else None
    except (OSError, subprocess.SubprocessError):
        return None


def _candidate_review_repos(dir_path: str) -> list[str]:
    """The git repos whose working-tree changes count as 'what this run touched':
    the sub-agent's cwd repo AND the SwarmWS workspace repo. Returned as de-duped
    realpath roots (a repo present under both keys is scanned once).

    Why a UNION (run_a5999658): a pipeline is launched from the swarmai SOURCE repo,
    but a workspace-resident project's code lives in the SEPARATE SwarmWS repo. The
    adversarial sub-agent inherits the launch cwd, so a cwd-only diff misses the
    SwarmWS files BUILD changed — the marker then can't cover them and the commit
    gate DENYs forever. Scanning both captures the changes wherever they landed.
    The workspace root is derived via get_app_data_dir() (honors SWARM_DATA_DIR) —
    never a hardcoded home path — so tests/sandboxes pin a scratch repo.
    """
    roots: list[str] = []
    seen: set[str] = set()

    def _add(candidate: Optional[str]) -> None:
        root = _repo_root_for(candidate) if candidate else None
        if root is not None and root not in seen:
            seen.add(root)
            roots.append(root)

    _add(dir_path)
    try:
        from config import get_app_data_dir  # local import: avoids a load-time cycle
        _add(str(get_app_data_dir() / "SwarmWS"))
    except Exception:
        logger.debug("_candidate_review_repos: suppressed exception on a fail-safe path", exc_info=True)
        pass  # workspace unresolvable → just scan the cwd repo (no fail-open change)
    return roots


def _reviewed_paths_at_head(dir_path: str) -> Optional[list[str]]:
    """The set of file paths the review touched vs HEAD, as ABSOLUTE realpaths,
    UNIONED across the candidate repos (cwd repo + SwarmWS workspace). TRI-STATE —
    the caller stores the result by KEY PRESENCE, never truthiness:
      - None  → EVERY candidate repo is git-unavailable / not a repo / error →
                caller OMITS reviewed_paths (path-less marker = unbounded, back-compat).
      - []    → at least one repo was reachable AND all reachable repos are clean →
                reviewed NOTHING → bounds coverage to the empty set (NOT unbounded).
      - [abs] → the union of changed paths across the reachable candidate repos.

    Fail-open red-line (RP50 / the C2 hole): None is returned ONLY when NO candidate
    repo could be queried at all — never to paper over one ambiguous/clean repo. A
    reachable-but-clean repo yields [] (bounded), keeping the gate's diff-bind honest.
    """
    import os
    roots = _candidate_review_repos(dir_path)
    if not roots:
        return None  # nothing resolvable → unbounded (back-compat)

    union: list[str] = []
    seen: set[str] = set()
    any_reachable = False
    for root in roots:
        try:
            # `git status --porcelain=v1 -z` — NOT `git diff HEAD` — so BUILD's NEW
            # files (untracked) are captured too. A brand-new handler.py is untracked
            # until something `git add`s it; `git diff HEAD` shows only tracked changes
            # and would silently miss exactly the files a fresh BUILD creates.
            #
            # -z is LOAD-BEARING (Gate-2 HIGH): without it, git C-quotes paths with
            # spaces/non-ASCII (`"my file.py"`) while the gate's PENDING side uses
            # `git diff --name-only` which does NOT quote — the two absolute paths then
            # differ by the quote chars, `covered` never contains the pending path, and
            # a genuinely-reviewed commit is falsely DENIED. -z emits RAW unquoted paths
            # NUL-separated, byte-for-byte matching the diff side.
            r = subprocess.run(
                ["git", "-C", root, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                capture_output=True, text=True, timeout=_GIT_CAPTURE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            # LOUD, never silent (SOUL P4): a timed-out candidate repo is skipped, which
            # drops its changed files from coverage → a legitimately-reviewed commit in
            # THAT repo would be DENIED (the original cross-repo bug, resurrected). On a
            # huge workspace `git status --untracked-files=all` can exceed the cap; if
            # this WARNs in production, raise _GIT_CAPTURE_TIMEOUT or narrow untracked
            # scope — do NOT let the skip stay invisible.
            logger.warning(
                "[reviewed-paths] `git status` timed out (>%ss) for candidate repo %s — "
                "its changed paths are EXCLUDED from adversarial coverage this marker; a "
                "reviewed commit there may be falsely DENIED. Raise _GIT_CAPTURE_TIMEOUT "
                "if this recurs.", _GIT_CAPTURE_TIMEOUT, root,
            )
            continue  # other candidates may still answer
        except (OSError, subprocess.SubprocessError):
            continue  # this repo unavailable → skip it, but others may answer
        if r.returncode != 0:
            continue  # git couldn't answer for this repo → skip (not a global None)
        any_reachable = True
        # -z format: each entry is `XY <path>\0`; a rename/copy (R/C) is
        # `XY <new>\0<old>\0` — the NEW path comes FIRST, then a separate NUL-terminated
        # old path. We iterate NUL fields, take each status-bearing field's path, and
        # skip the bare old-path field that trails a rename/copy.
        fields = [f for f in r.stdout.split("\0") if f]
        i = 0
        while i < len(fields):
            entry = fields[i]
            status = entry[:2]
            rel = entry[3:]  # 2 status chars + 1 space, then the RAW (unquoted) path
            if rel:
                abs_path = os.path.realpath(os.path.join(root, rel))
                if abs_path not in seen:
                    seen.add(abs_path)
                    union.append(abs_path)
            # a rename/copy consumes the following NUL field (the old path) — skip it
            if status and (status[0] in ("R", "C") or status[1] in ("R", "C")):
                i += 2
            else:
                i += 1

    if not any_reachable:
        return None  # EVERY candidate repo failed git → unbounded (back-compat)
    return union  # possibly [] (all reachable repos clean) — bounded, never None


def create_subagent_capture_hook(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a SubagentStop hook that reads the agent transcript.

    Reads the last 5KB of the transcript, extracts error patterns via regex,
    and writes findings to corrections.jsonl.  Observe-only.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        transcript_path = _extract_field(input_data, "agent_transcript_path", "")
        agent_id = _extract_field(input_data, "agent_id", "unknown")

        if not transcript_path:
            return {}

        try:
            p = Path(transcript_path)
            if not p.exists():
                return {}

            # Read tail of transcript — OFF-LOOP (run_a1f4c2d8). Bounded to
            # _SUBAGENT_TAIL_BYTES, but a transcript lives on the same disk the agent is
            # actively writing to, so even a seek+read is real latency on the loop. The
            # stat and the read stay in ONE helper: the seek offset is derived from the
            # size, so splitting them would open a window where the file grew between
            # measuring and seeking.
            def _read_tail() -> str:
                size = p.stat().st_size
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    if size > _SUBAGENT_TAIL_BYTES:
                        f.seek(size - _SUBAGENT_TAIL_BYTES)
                        f.readline()  # skip partial first line
                    return f.read()

            tail = await asyncio.to_thread(_read_tail)

            # Extract error patterns
            errors = _ERROR_PATTERNS.findall(tail)
            if not errors:
                return {}

            summary = "; ".join(dict.fromkeys(errors))[:500]  # dedup, cap at 500 chars
            entry = {
                "ts": time.time(),
                "session_id": sid,
                "type": "subagent_finding",
                "agent_id": agent_id,
                "summary": summary,
            }
            _append_correction(path, entry)
            ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1

        except Exception:
            logger.exception("Failed to capture subagent transcript from %s", transcript_path)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: post-compact context injection
# ---------------------------------------------------------------------------

def create_post_compact_injection(
    session_context: Optional[dict] = None,
):
    """Factory: creates a UserPromptSubmit hook that injects context after compaction.

    When ``session_context["_compacted"]`` is True (set by PreCompact hook),
    the next UserPromptSubmit injects ``additionalContext`` with:
    - Files touched during this session (for re-reading)
    - Basic session continuity instructions

    Fire-once: resets ``_compacted`` flag after injection.
    """
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        if not ctx.get("_compacted"):
            return {}

        # Build compact survival instructions. Enriched with REAL session_context
        # signal only (never fabricated) so the agent re-anchors on what it was
        # DOING, not just which files it touched — the thin "files list only" block
        # lost task goal / recent decisions across a compaction (XG 2026-10-14:
        # "把 post_compact_injection 的续接内容做厚一点"). Each line is guarded: it
        # appears ONLY when its signal is present, so a bare session gets just the
        # base line (no invented continuity). This whole block rides the SDK
        # additionalContext channel (hookSpecificOutput below) — it is NEVER glued
        # onto the user prompt, so _classify_message_language (R19) cannot see it.
        files = sorted(ctx.get("_files_touched", set()))
        parts = [
            "[System: Context was just compacted. Key session state below.]",
        ]
        if files:
            file_list = ", ".join(files[:20])  # cap at 20 files
            parts.append(f"Files touched this session (re-read if needed): {file_list}")

        # Recent activity — dominant tool + a few recent intents from the
        # _observations ring (the SAME read pattern create_pre_compact_checkpoint
        # uses; best-effort so a malformed ring never breaks the hook chain).
        ring = ctx.get("_observations")
        if ring is not None:
            try:
                completed = ring.all_completed()
                if completed:
                    from collections import Counter
                    tool_counts = Counter(o.tool_name for o in completed)
                    dominant, dom_n = tool_counts.most_common(1)[0]
                    parts.append(
                        f"Recent activity: {len(completed)} tool calls, "
                        f"dominant={dominant} ({dom_n})."
                    )
                recent = ring.snapshot(last_n=3)
                intents = [o.intent for o in recent if getattr(o, "intent", "")]
                if intents:
                    parts.append("Most recent steps: " + " | ".join(intents[-3:]) + ".")
            except Exception:
                logger.debug("_hook: suppressed exception on a fail-safe path", exc_info=True)
                pass  # enrichment is best-effort; never break the hook chain

        # Test status — did this session run tests? (continuity: pick the thread back up)
        if ctx.get("_ran_tests"):
            parts.append("This session has run tests (re-check status if mid-TDD).")

        # Corrections — how many user corrections landed (a signal the agent was
        # being steered; worth re-reading the correction trail after a compaction).
        corrections = ctx.get("_corrections_count", 0)
        if corrections:
            parts.append(f"User corrections this session: {corrections} (re-read the correction trail).")

        # Active pipeline run — a compacted pipeline MUST keep its run thread.
        run_id = ctx.get("_active_pipeline_run_id")
        if run_id:
            parts.append(
                f"Active pipeline run: {run_id} — re-read its run.json/checkpoint to resume the correct stage."
            )

        # Reset flag — fire once
        ctx["_compacted"] = False

        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": " ".join(parts),
            }
        }

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: per-turn enforcement injector (A: symmetric language,
# B: direct-mode adversarial reminder) — run_e57b7554
# ---------------------------------------------------------------------------

# SYSTEM-injected blocks get glued onto the user's message before it reaches this
# hook, and they are ENGLISH prose — classifying the COMBINED string mis-flips R19
# (verified live 2026-08-10: "你给我说中文" + the UI-state block → weight 0.17 < 0.30
# → wrongly "en"). R19 must reflect the USER's language, so these blocks are stripped
# before classification. Three known injectors (adversarial run abc8551392a864ff2):
#   1. UI-state PREFIX (session_router._prefix_ui_state_onto_query): `{block}\n\n{user}`
#      — leading, header in _INJECTED_LEADING_HEADERS, single-\n internally.
#   2. wrap-up APPEND (session_unit.send): `{user}\n\n---\n\n{WRAP_UP_PROMPT}` — trailing.
#   3. heal-continuation PREPEND (session_unit.send): `{continuation}\n\n---\n\n{user}`.
# (2)+(3) are delimited by the system's own `\n\n---\n\n` separator and carry a
# recognizable header/marker — split on that and drop segments that ARE a system block.
_INJECTED_LEADING_HEADERS = ("## Current UI State", "## Currently Open File")
_SYSTEM_BLOCK_MARKERS = (
    "## Current UI State", "## Currently Open File",  # UI-state
    "## Task Continuation",                            # heal-continuation
    "SYSTEM NOTE",                                      # wrap-up prompts
)
_INJECT_DELIM = "\n\n---\n\n"


def _strip_injected_prefix(prompt: str) -> str:
    """Return ONLY the user's own text, with system-injected blocks removed, so
    language classification reflects the USER's language — not the English UI-state /
    wrap-up / heal-continuation prose the router+session layer glue onto the query.

    Fail-safe by construction: non-str input returns unchanged; if stripping would
    remove everything (a prompt that is ALL system blocks, or a real user message
    that merely starts with one of these headers), the ORIGINAL prompt is returned
    rather than an empty string — never drop real user content on a false match."""
    if not isinstance(prompt, str):
        return prompt  # multimodal list / non-str → not our concern (leave as-is)

    # Split on the system's own block delimiter and drop whole segments that ARE a
    # known system block; from surviving segments, strip a leading UI-state header
    # block (prepended with only `\n\n`, no `---`).
    kept: list[str] = []
    for seg in prompt.split(_INJECT_DELIM):
        s = seg.lstrip()
        if s.startswith(_SYSTEM_BLOCK_MARKERS) and not s.startswith(_INJECTED_LEADING_HEADERS):
            continue  # a pure system block (heal-continuation / wrap-up) → drop
        if s.startswith(_INJECTED_LEADING_HEADERS):
            # UI-state block leads this segment; the FIRST blank line is the seam to
            # the user text (the block itself is single-\n-joined internally).
            seam = s.find("\n\n")
            seg = s[seam + 2:] if seam != -1 else ""
        kept.append(seg)

    user = _INJECT_DELIM.join(kept).strip()
    return user if user else prompt  # never return empty — fail back to the original


def _classify_message_language(prompt: str) -> Optional[str]:
    """Return "zh" if the message is CJK-majority, "en" if it is clearly Latin
    text, or None when ambiguous (too short / symbol-only) — in which case NO
    language reminder is injected (silence beats a wrong flip).

    Strips any system-injected UI-state prefix FIRST (see _strip_injected_prefix):
    R19 must reflect the USER's language, not the English proprioception block the
    session router prepends to the query.

    REUSES the single canonical full-range CJK detector
    ``ContextDirectoryLoader._CJK_RE`` (the ONE object, imported — NOT copied:
    context_directory_loader.py documents that the way to avoid detector
    divergence, run_3f25a73a, is to import the single source, never re-declare a
    parallel regex). "zh" here is shorthand for "a CJK-like language" — the full
    range includes Kana/Hangul, all of which are correctly NOT-English.
    """
    prompt = _strip_injected_prefix(prompt)
    stripped = "".join(prompt.split())
    if len(stripped) < 3:
        return None  # too short to classify — stay silent
    from core.context_directory_loader import ContextDirectoryLoader
    cjk = len(ContextDirectoryLoader._CJK_RE.findall(prompt))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", prompt))

    # Compare SEMANTIC UNITS, not a char ratio: 1 CJK char ≈ 1 word of meaning
    # (the tokenizer weights them similarly — CJK ~1.1 tok/char vs Latin ~1 tok/word).
    # A char-ratio is the WRONG tool here — it can't separate "English + a CJK quote"
    # from "Chinese + English technical terms" (the latter is XG's normal style:
    # "帮我 review 这个 CI log"), and mis-flipping THAT to English is the exact R19
    # bug this hook exists to prevent. Weighing CJK-chars vs Latin-WORDS protects it:
    # a few English tech terms can't outvote CJK sentence structure, and a lone CJK
    # quote in an English sentence can't outvote many English words.
    if cjk == 0:
        return "en" if latin_words >= 1 else None  # pure Latin (or symbols → silent)
    # CJK present. It's Chinese UNLESS the CJK is a tiny embedded fraction of a
    # clearly-Latin-dominant message (a quoted term), i.e. Latin words vastly
    # outnumber CJK chars.
    weight = cjk / (cjk + latin_words)          # CJK share of semantic units
    if weight >= _LANG_CJK_WEIGHT_THRESHOLD:
        return "zh"
    if latin_words >= 1:
        return "en"                              # Latin-dominant, CJK is incidental
    return None


def create_enforcement_injector(session_context: Optional[dict] = None):
    """Factory: a UserPromptSubmit hook that injects per-turn enforcement
    reminders into ``additionalContext`` — moving machine-decidable, per-turn
    rules OFF decaying static prose and INTO the reading path just before I
    generate (O003 + P7). Two signals, composed into one additionalContext:

    - **A — symmetric language (R19).** Detect THIS message's language and remind
      me to reply in the SAME language. SYMMETRIC + input-derived — a CJK message
      injects "respond in Chinese", an English message injects "respond in
      English". NEVER hardcoded to one language (hardcoding rebuilds the exact bug
      that motivated the hook: R19 is *match the input language*, not "reply in
      Chinese").
    - **B — direct-mode guard (R1 / STEERING #13).** If the message carries a
      direct-mode trigger (直接做 / just do it / skip pipeline), remind me that
      adversarial review stays MANDATORY even in direct mode
      (code → test → adversarial → commit) — the one moment I'm most tempted to
      skip it.

    Observe-only + fail-safe: any internal error returns ``{}`` so the hook chain
    is never broken. Mirrors the additionalContext shape of
    ``create_post_compact_injection`` so it rides the same in-turn merge path.
    """
    _ = session_context  # reserved for parity with sibling factories; unused today

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            prompt = _extract_field(input_data, "prompt", "")
            if not prompt:
                return {}

            # Both signals judge the USER's message, not the system-injected UI-state
            # prefix the session router prepends — strip it once, use for both.
            user_text = _strip_injected_prefix(prompt)

            parts: list[str] = []

            # Signal A — symmetric language reminder (classifier re-strips defensively,
            # but pass the already-stripped text so the two signals agree on the input).
            lang = _classify_message_language(user_text)
            if lang == "zh":
                parts.append(
                    "⚠️ 用户这条消息是中文 → 本轮必须用中文回复(R19:匹配用户输入语言)。"
                )
            elif lang == "en":
                parts.append(
                    "⚠️ The user's message is in English → respond in English this "
                    "turn (R19: match the user's input language)."
                )

            # Signal B — direct-mode adversarial guard
            if _DIRECT_MODE_RE.search(user_text):
                parts.append(
                    "⚠️ Direct-mode request detected: adversarial review is STILL "
                    "mandatory before commit (sequence: code → test → adversarial → "
                    "commit). 'Too simple for adversarial' is the signal it's needed "
                    "(R1 / STEERING #13). Cut ceremony, never cut the adversarial gate."
                )

            if not parts:
                return {}

            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": " ".join(parts),
                }
            }
        except Exception:
            # Observe-only: never break the hook chain on a classifier error.
            logger.debug("enforcement_injector failed", exc_info=True)
            return {}

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: high-signal observation capture → DailyActivity
# ---------------------------------------------------------------------------

_HIGH_SIGNAL_PATTERNS = re.compile(
    r"""(?ix)(?:^|\b)(?:
        I\s+decid(?:ed|e)
      | we\s+decid(?:ed|e)
      | decision:\s
      | important:\s
      | rule:\s
      | lesson:\s
      | never\s+again
      | from\s+now\s+on
      | 我(?:们)?决定
      | 决定了
      | 以后(?:都|要|不)
      | 重要(?:：|:)
      | 教训(?:：|:)
      | 规则(?:：|:)
    )"""
)


def create_high_signal_capture(
    session_context: Optional[dict] = None,
    workspace_dir: Optional[str] = None,
):
    """Factory: creates a UserPromptSubmit hook that captures high-signal observations.

    Detects decision/lesson/rule signals in user prompts and appends them
    to today's DailyActivity file.  Does NOT write to MEMORY.md — distillation
    pipeline decides what gets promoted.  This is "faster capture without
    skipping the quality gate."

    Deduplication: tracks captured prompts in session_context to avoid
    writing the same signal twice if the user repeats.
    """
    ctx = session_context or {}
    from jobs.paths import SWARMWS as _SWARMWS_hs
    ws = workspace_dir or str(_SWARMWS_hs)
    captured_key = "_high_signal_captured"

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        prompt = _extract_field(input_data, "prompt", "")
        if not prompt or len(prompt) < 10:
            return {}

        if not _HIGH_SIGNAL_PATTERNS.search(prompt):
            return {}

        # Dedup within session
        if captured_key not in ctx:
            ctx[captured_key] = set()
        sig = prompt[:100]  # signature for dedup
        if sig in ctx[captured_key]:
            return {}
        ctx[captured_key].add(sig)

        # Append to today's DailyActivity
        try:
            from datetime import datetime
            now = datetime.now()

            entry = (
                f"\n**🔔 High-signal capture** ({now.strftime('%H:%M')}): "
                f"{prompt[:500]}\n"
            )

            # OFF-LOOP (run_a1f4c2d8): mkdir + append in ONE thread hop. This fires on a
            # UserPromptSubmit hook — i.e. the instant the user hits send — so blocking
            # here directly delays the turn the user is waiting on.
            # Concurrency note (unchanged): plain open("a") is safe here — entries are
            # well under 4KB (macOS/APFS atomic append threshold).  These are
            # observe-only hooks; a torn write loses one signal entry, which is
            # acceptable.  locked_write.py is not needed for this use case.
            def _append_signal() -> None:
                da_dir = Path(ws) / "Knowledge" / "DailyActivity"
                da_dir.mkdir(parents=True, exist_ok=True)
                da_file = da_dir / f"{now.strftime('%Y-%m-%d')}.md"
                with open(da_file, "a", encoding="utf-8") as f:
                    f.write(entry)

            await asyncio.to_thread(_append_signal)

            logger.debug("High-signal captured to DailyActivity: %.80s", prompt)
        except Exception:
            logger.exception("Failed to write high-signal to DailyActivity")

        return {}

    return _hook


# ---------------------------------------------------------------------------
# Reader: aggregate corrections.jsonl data for evolution optimizer
# ---------------------------------------------------------------------------

def read_correction_stats(
    corrections_path: Optional[str] = None,
    recency_days: int = 7,
) -> dict[str, dict]:
    """Read corrections.jsonl and compute per-skill stats for the optimizer.

    Returns a dict keyed by skill/tool name::

        {
            "Bash": {"recent_corrections": 3, "repeat_count": 5, "total": 12},
            "s_evaluate": {"recent_corrections": 1, "repeat_count": 2, "total": 4},
        }

    - ``recent_corrections``: entries within the last ``recency_days`` days.
    - ``repeat_count``: total entries for this skill (proxy for how often
      the same skill keeps failing).
    - ``total``: same as repeat_count (explicit alias for clarity).

    Returns empty dict on any error — caller should handle gracefully.
    """
    path = Path(corrections_path or _DEFAULT_CORRECTIONS_PATH)
    if not path.exists():
        return {}

    cutoff = time.time() - (recency_days * 86400)
    stats: dict[str, dict] = {}

    try:
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Use "tool" for tool_failure, try to extract skill name for user_correction
            key = entry.get("tool", "")
            if not key and entry.get("type") == "user_correction":
                key = "_user_correction"  # aggregate bucket

            if not key:
                continue

            if key not in stats:
                stats[key] = {"recent_corrections": 0, "repeat_count": 0, "total": 0}

            stats[key]["total"] += 1
            stats[key]["repeat_count"] += 1
            ts = entry.get("ts", 0)
            if ts >= cutoff:
                stats[key]["recent_corrections"] += 1

    except Exception:
        logger.exception("Failed to read correction stats from %s", path)
        return {}

    return stats


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PostToolUse: memory edit guard — validates Edit calls on MEMORY.md/EVOLUTION.md
# ---------------------------------------------------------------------------

# Agent-owned context files that should be written via s_persist / s_self-evolution,
# not hand-edited. KNOWLEDGE.md added 2026-06-28 (run_3f3be114) — the always-injected
# index is a routing target too.
_MEMORY_FILE_SUFFIXES = ("MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md")

# Skills whose invocation "covers" a memory-file write this turn (routing satisfied).
_PERSIST_SKILLS = frozenset({"s_persist", "s_self-evolution"})

# session_context key holding the set of persist-class skills invoked THIS turn.
# Populated by create_persist_skill_tracker (PreToolUse), cleared by
# create_persist_skill_tracker_reset (UserPromptSubmit = new turn).
_PERSIST_SKILLS_KEY = "_persist_skills_this_turn"


def create_memory_edit_guard(session_context: Optional[dict] = None):
    """Factory: PostToolUse hook guarding Edit/Write on agent-owned context files.

    Two independent signals (both WARN-only — observe-after-the-fact, never deny):

    1. **Content scan** (always): runs MemoryGuard on the written content when the
       target ends in MEMORY.md / EVOLUTION.md / KNOWLEDGE.md, warning on dangerous
       patterns now sitting in the system prompt.
    2. **Persist-skill routing** (only when ``session_context`` is provided): if the
       file was hand-Edited/Written WITHOUT invoking s_persist / s_self-evolution this
       turn, warns the agent to route through the skill. This is the defense-outside-
       the-agent for the recurring O028/C035 adherence failure ("I hand-wrote it
       instead of using s_persist"). When ``session_context`` is None (backward-compat
       for the 28 existing callers), the routing check is INERT — only the content
       scan runs, exactly as before.
    """
    ctx = session_context  # None for legacy callers → routing check skipped

    async def _hook(tool_use: dict, tool_use_id: str, session: Any) -> dict:
        tool_name = tool_use.get("tool_name", "")
        if tool_name not in ("Edit", "Write"):
            return {}

        tool_input = tool_use.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        # Only check the agent-owned context files
        if not any(file_path.endswith(suffix) for suffix in _MEMORY_FILE_SUFFIXES):
            return {}

        # Write uses "content"; Edit uses "new_string"
        new_content = tool_input.get("new_string") or tool_input.get("content") or ""

        warnings: list[str] = []

        # Signal 1 — content scan (always, mirrors prior behavior)
        if new_content:
            try:
                from core.memory_guard import MemoryGuard
                result = MemoryGuard().scan(new_content)
                if result.rejected:
                    categories = {f.category for f in result.findings if f.action == "reject"}
                    warnings.append(
                        f"⚠️ MemoryGuard WARNING: {tool_name} to {file_path.split('/')[-1]} "
                        f"contains dangerous patterns: {', '.join(categories)}. "
                        f"This content is now in the system prompt. "
                        f"Consider reverting the edit immediately."
                    )
                    logger.warning(
                        "MemoryGuard: %s to %s rejected — %s",
                        tool_name, file_path, categories,
                    )
            except ImportError:
                pass  # memory_guard not available
            except Exception as exc:
                logger.debug("MemoryGuard %s check failed: %s", tool_name, exc)

        # Signal 2 — persist-skill routing (only with session_context)
        if ctx is not None:
            try:
                skills_this_turn = ctx.get(_PERSIST_SKILLS_KEY) or set()
                if not (_PERSIST_SKILLS & set(skills_this_turn)):
                    warnings.append(
                        f"⚠️ Persist-routing WARNING: {tool_name} to "
                        f"{file_path.split('/')[-1]} was hand-written without invoking "
                        f"s_persist (or s_self-evolution) this turn. These agent-owned "
                        f"context files should be persisted via the skill, which routes "
                        f"to the correct file/section, dedups against existing entries, "
                        f"and stamps source:manual metadata. Recurring O028/C035 pattern: "
                        f"hand-editing bypasses the skill's dedup + routing. Re-run via "
                        f"s_persist if this was a knowledge/memory update."
                    )
                    logger.info(
                        "persist-routing: %s to %s without persist skill this turn",
                        tool_name, file_path,
                    )
            except Exception as exc:
                logger.debug("persist-routing check failed: %s", exc)

        if warnings:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "\n".join(warnings),
                }
            }
        return {}

    return _hook


def create_persist_skill_tracker(session_context: Optional[dict] = None):
    """Factory: PreToolUse hook (matcher=Skill) recording persist-class skill
    invocations into ``session_context[_PERSIST_SKILLS_KEY]`` (a set) for THIS turn.

    The set is consumed by create_memory_edit_guard (to suppress the routing WARN
    when a persist skill WAS invoked) and cleared by create_persist_skill_tracker_reset
    on each new user turn. Approve-only — never blocks (skill_access_checker owns
    skill authorization; this hook only observes)."""
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            # _extract_field (not raw .get) — SDK hook inputs may be dict OR object;
            # this is the module's established convention (see file_tracker, line ~361).
            if _extract_field(input_data, "tool_name", "") == "Skill":
                tool_input = _extract_field(input_data, "tool_input", {})
                skill = tool_input.get("skill", "") if isinstance(tool_input, dict) else ""
                if skill in _PERSIST_SKILLS:
                    ctx.setdefault(_PERSIST_SKILLS_KEY, set()).add(skill)
        except Exception as exc:
            logger.debug("persist_skill_tracker failed: %s", exc)
        return {}

    return _hook


def create_persist_skill_tracker_reset(session_context: Optional[dict] = None):
    """Factory: UserPromptSubmit hook that clears the per-turn persist-skill set.

    A new user prompt = a new turn, so the skill marker must reset (mirrors the
    create_failure_tracker_reset paired-hook pattern)."""
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            ctx[_PERSIST_SKILLS_KEY] = set()
        except Exception as exc:
            logger.debug("persist_skill_tracker_reset failed: %s", exc)
        return {}

    return _hook


def register_runtime_hooks(
    registry: "HookRegistry",
    session_context: dict,
    corrections_path: Optional[str] = None,
) -> None:
    """Register all runtime hooks into a HookRegistry.

    Called from hook_builder.build_hooks() to wire runtime observation.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH

    # PostToolUseFailure hooks
    registry.register(
        "PostToolUseFailure",
        create_correction_capture_hook(path, session_context),
        "correction_capture",
    )
    registry.register(
        "PostToolUseFailure",
        create_error_pattern_detector(session_context),
        "error_pattern_detector",
    )

    # PostToolUse: reset failure tracker on success
    registry.register(
        "PostToolUse",
        create_failure_tracker_reset(session_context),
        "failure_tracker_reset",
    )

    # Phase 2: PostToolUse file tracker
    registry.register(
        "PostToolUse",
        create_file_tracker(session_context),
        "file_tracker",
    )

    # R1.5a: PostToolUse session<->run rendezvous. Detects `artifact_cli.py
    # run-create`, revives _active_pipeline_run_id in ctx, and persists a
    # session_<sid>_run.map file for the future D7 commit-gate. Pure plumbing.
    registry.register(
        "PostToolUse",
        create_pipeline_run_mapping_hook(session_context),
        "pipeline_run_mapping",
    )

    # Phase 2: PostToolUse session checkpoint
    registry.register(
        "PostToolUse",
        create_session_checkpoint(session_context),
        "session_checkpoint",
    )

    # PreToolUse: persist-skill tracker (records s_persist/s_self-evolution this turn)
    registry.register(
        "PreToolUse",
        create_persist_skill_tracker(session_context),
        "persist_skill_tracker",
        matcher="Skill",
    )

    # Phase 2: PostToolUse memory edit guard (content scan + persist-routing WARN on
    # Edit/Write to MEMORY/EVOLUTION/KNOWLEDGE.md). session_context enables the routing check.
    registry.register(
        "PostToolUse",
        create_memory_edit_guard(session_context),
        "memory_edit_guard",
    )

    # Phase 4: PostToolUse change-triggered eval (STEERING/AGENT edits → scoped eval)
    try:
        from core.eval_hooks import create_change_triggered_eval
        registry.register(
            "PostToolUse",
            create_change_triggered_eval(session_context),
            "change_triggered_eval",
        )
    except Exception:
        logger.debug("register_runtime_hooks: suppressed exception on a fail-safe path", exc_info=True)
        pass  # Non-blocking — eval hooks are optional

    # Phase 2: SubagentStop transcript capture
    registry.register(
        "SubagentStop",
        create_subagent_capture_hook(path, session_context),
        "subagent_capture",
    )

    # Gate 2 Agent Tool Audit: write marker file on SubagentStop
    registry.register(
        "SubagentStop",
        create_agent_tool_audit_hook(session_context),
        "agent_tool_audit",
    )

    # UserPromptSubmit hooks
    registry.register(
        "UserPromptSubmit",
        create_user_correction_detector(path, session_context),
        "user_correction_detector",
    )

    # Phase 2: UserPromptSubmit post-compact injection
    registry.register(
        "UserPromptSubmit",
        create_post_compact_injection(session_context),
        "post_compact_injection",
    )

    # Phase 2: UserPromptSubmit high-signal observation capture
    registry.register(
        "UserPromptSubmit",
        create_high_signal_capture(session_context),
        "high_signal_capture",
    )

    # UserPromptSubmit: reset per-turn persist-skill tracker (new turn = clean slate)
    registry.register(
        "UserPromptSubmit",
        create_persist_skill_tracker_reset(session_context),
        "persist_skill_tracker_reset",
    )

    # UserPromptSubmit: per-turn enforcement injector (run_e57b7554) — symmetric
    # language reminder (R19) + direct-mode adversarial guard (R1). Moves per-turn,
    # machine-decidable rules off decaying static prose into the reading path (O003/P7).
    registry.register(
        "UserPromptSubmit",
        create_enforcement_injector(session_context),
        "enforcement_injector",
    )

    # Observation hooks (LAST in chain — after all other hooks)
    try:
        from core.observation_hooks import register_observation_hooks
        register_observation_hooks(registry, session_context)
    except ImportError:
        logger.debug("observation_hooks not available — skipping")
    except Exception:
        logger.debug("observation_hooks registration failed", exc_info=True)

    logger.info(
        "Runtime hooks registered: correction_capture, error_pattern_detector, "
        "failure_tracker_reset, file_tracker, session_checkpoint, memory_edit_guard, "
        "subagent_capture, user_correction_detector, post_compact_injection, "
        "high_signal_capture, observation_recorder, observation_completer"
    )
