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
import re
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
                    # Judge decides ADMIT/REJECT only; a correction is semantically a
                    # [pitfall]→## Pitfalls (its fixed home since Gap #17), so we do NOT
                    # re-route on the judge's section (that would write "[pitfall]" into
                    # e.g. ## Corrections — a type/section mismatch, adversarial BUG#1).
                    verdict, _section, _reason, distilled = admit_memory_lesson(summary)
                    if verdict == "auto":
                        # ROOT-FIX (capture-vs-distill): if the gate distilled a
                        # shape-dirty entry, write the DISTILLED rule, never our own
                        # raw summary (writer≠finalizer). fail-open: distilled=None →
                        # keep summary.
                        body = distilled or summary
                        ws = _Path.home() / ".swarm-ai" / "SwarmWS"
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
                    pass  # Non-blocking — MEMORY write is best-effort

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: file tracker — records Read/Edit/Write file paths
# ---------------------------------------------------------------------------

_TRACKED_TOOLS = {"Read", "Edit", "Write"}


def create_file_tracker(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUse hook that tracks files touched during the session.

    Populates ``session_context["_files_touched"]`` (a set) with file paths
    from Read, Edit, and Write tool calls.  Used by PreCompact injection and
    session checkpoint.
    """
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "")

        # Detect test/scan execution (Self-Monitoring evidence)
        if tool == "Bash":
            tool_input = _extract_field(input_data, "tool_input", {})
            command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
            if "pytest" in command or "python -m pytest" in command:
                ctx["_ran_tests"] = True

        if tool not in _TRACKED_TOOLS:
            return {}

        tool_input = _extract_field(input_data, "tool_input", {})
        file_path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
        if file_path:
            if "_files_touched" not in ctx:
                ctx["_files_touched"] = set()
            ctx["_files_touched"].add(file_path)

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
_ADVERSARIAL_TYPE_RE = re.compile(r"adversar|red.?team|skeptic|reviewer", re.IGNORECASE)
_ADVERSARIAL_INTENT_RE = re.compile(
    r"adversar|refute|red.?team|poke\s+holes?|stress.?test|find\s+(?:bugs?|regressions?|"
    r"issues?|flaws?|holes?)|attack\s+(?:this|the)\s+(?:diff|change|design|code)|"
    r"try\s+to\s+break|review\s+as\s+a\s+skeptic|对抗|挑刺|找\s*bug|找出\s*bug",
    re.IGNORECASE,
)


def _is_adversarial_intent(subagent_type: str, description: str, prompt: str) -> bool:
    """True if a sub-agent was spawned for ADVERSARIAL review (vs Explore/investigate).

    subagent_type is the primary signal; description + prompt are a liberal keyword
    fallback. Empty everything → False (no evidence = not adversarial; fail-safe:
    the gate should DENY, not silently pass, when intent is unknowable)."""
    st = str(subagent_type or "")
    if _ADVERSARIAL_TYPE_RE.search(st):
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

    Two modes:
    1. If ``_active_pipeline_run_id`` is set in session_context: writes
       ``<run_id>.marker`` (precise match — pipeline orchestrator sets this).
    2. Otherwise: writes ``<session_id>_<ts>.marker`` as session-level evidence.
       The validator accepts either form. This ensures the hook produces
       evidence even before the orchestrator wires up the run_id.
    """
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        session_id = ctx.get("sdk_session_id", "")
        run_id = ctx.get("_active_pipeline_run_id")

        if not session_id and not run_id:
            return {}

        try:
            _PIPELINE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.time()

            # OFF-LOOP (run_a1f4c2d8): both audit markers in ONE thread hop, not two —
            # they are written as a PAIR (run-specific when available, session-level
            # always, as fallback evidence), so splitting them would double the hops and
            # let a cancellation land between two writes meant to be one audit record.
            def _write_markers() -> None:
                # Write run-specific marker if run_id available (primary path)
                if run_id:
                    marker_file = _PIPELINE_AUDIT_DIR / f"{run_id}.marker"
                    marker_file.write_text(json.dumps({
                        "ts": ts,
                        "event": "SubagentStop",
                        "run_id": run_id,
                        "session_id": session_id,
                    }))

                # Always write session-level marker as fallback evidence
                session_marker = _PIPELINE_AUDIT_DIR / f"session_{session_id}_{int(ts)}.marker"
                session_marker.write_text(json.dumps({
                    "ts": ts,
                    "event": "SubagentStop",
                    "session_id": session_id,
                    "run_id": run_id or "unknown",
                }))

            await asyncio.to_thread(_write_markers)
        except Exception:
            logger.warning("Failed to write agent audit marker (session=%s)", session_id)

        return {}

    return _hook


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

        # Build compact survival instructions
        files = sorted(ctx.get("_files_touched", set()))
        parts = [
            "[System: Context was just compacted. Key session state below.]",
        ]
        if files:
            file_list = ", ".join(files[:20])  # cap at 20 files
            parts.append(f"Files touched this session (re-read if needed): {file_list}")

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
