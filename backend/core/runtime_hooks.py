"""Runtime hooks for tool-failure capture and error pattern detection.

Nothing here judges what the user meant: whether the user corrected the agent is the
after-session review's call (design 2026-10-30-cdf-agent-decides D12), not a pattern
match on the prompt.

These hooks fire DURING agent execution (not post-session) via the Claude
Agent SDK hook system.  All hooks are observe-only — they log and inject
additionalContext but never block or modify tool inputs/outputs.

Key public symbols:

- ``register_runtime_hooks``         — Wire all runtime hooks into a HookRegistry
- ``create_correction_capture_hook`` — PostToolUseFailure → corrections.jsonl
- ``create_error_pattern_detector``  — PostToolUseFailure → hint after 2+ failures
"""

import asyncio
import datetime
import hashlib
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

# Free-text VALUE fields per correction entry type that carry attacker-influenced
# content: a subagent's summary is scanned for injection before it is written.
# `tool_failure` fields are deliberately NOT scanned — they are self-generated tool
# output/input, FP-prone, and an attacker controlling them already controls the agent
# (low marginal safety).
_CORRECTION_SCAN_FIELDS: dict[str, tuple[str, ...]] = {
    "subagent_finding": ("summary",),
}
_CORRECTION_SENTENCE_SPLIT_FIELDS: tuple[str, ...] = ()


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

    Populates ``session_context["_files_written"]`` (absolute real paths this session
    wrote: the write tools and Bash write targets; read by core.shared_checkout) and
    ``session_context["_files_touched"]`` (a set) with file paths from
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

    def _add_written(path: str, cwd: str) -> None:
        if not path or not isinstance(path, str):
            return
        try:
            full = path if os.path.isabs(path) else os.path.join(cwd or "/", path)
            real = os.path.realpath(os.path.expanduser(full))
        except (OSError, ValueError):
            return
        ctx.setdefault("_files_written", set()).add(real)

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
                        _add_written(tgt, _extract_field(input_data, "cwd", "") or "")
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
            if tool != "Read":
                cwd = _extract_field(input_data, "cwd", "") or ""
                _add_written(tool_input.get("file_path", ""), cwd)
                _add_written(tool_input.get("notebook_path", ""), cwd)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: session checkpoint — crash survival
# ---------------------------------------------------------------------------

_DEFAULT_CHECKPOINT_PATH = str(
    _STATE_DIR / "session_checkpoint.json"
)
_DEFAULT_CHECKPOINT_INTERVAL = 10


def create_session_checkpoint(
    session_context: Optional[dict] = None,
    checkpoint_path: Optional[str] = None,
    interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
    workspace_dir: Optional[str] = None,
):
    """Factory: creates a PostToolUse hook that writes a session checkpoint.

    Every ``interval`` tool calls:
    1. Overwrites checkpoint JSON with current session state (crash recovery).
    2. Upserts this session's ONE checkpoint block in today's DailyActivity
       (mid-session memory) when the touched-file list grew.

    On crash, ``recover_crash_checkpoint()`` reads the JSON and replaces the same
    block on next startup.  For normal sessions, the block ensures content is
    captured even if post-session hooks don't fire.

    No git activity: the workspace's own commits are the auto-commit hook's
    snapshots, not work, and the post-session summary records the real commits
    from the source repos (DailyActivityExtractionHook._capture_git_activity).
    ``git_commits`` stays in the JSON as ``[]`` for readers of its shape.
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

        git_commits: list[str] = []  # see the docstring: workspace commits are not work

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

        # 2. Upsert this session's checkpoint block in DailyActivity (mid-session
        # memory) — only when the touched-file list grew since the last one.
        last_file_count = ctx.get(last_da_count_key, 0)
        if len(files) <= last_file_count:
            return {}

        try:
            from datetime import datetime as dt
            from core.daily_activity_writer import upsert_session_block
            now = dt.now()
            da_file = Path(ws) / "Knowledge" / "DailyActivity" / f"{now.strftime('%Y-%m-%d')}.md"

            # Build content-capped entry (target < 1KB)
            lines = [
                f"## {now.strftime('%H:%M')} | {session_id[:8]} | 📸 Mid-session checkpoint\n",
            ]
            file_summary = ", ".join(f"`{Path(f).name}`" for f in files[:10])
            if len(files) > 10:
                file_summary += f" (+{len(files) - 10} more)"
            lines.append(f"**Files:** {file_summary}\n")

            # bounded by its parts (at most 10 whole file names, then a count), never
            # cut: a byte cap here split a file name in half (CDF first principle)
            entry = "".join(lines)

            # OFF-LOOP (run_a1f4c2d8): the locked read-modify-write goes to a thread.
            await asyncio.to_thread(upsert_session_block, da_file, session_id,
                                    "checkpoint", entry)

            ctx[last_da_count_key] = len(files)
            logger.debug("Mid-session checkpoint upserted in DailyActivity: %d files", len(files))
        except Exception:
            logger.debug("Failed to write mid-session checkpoint to DailyActivity", exc_info=True)

        # (A "you edited >1 source file but never ran pytest" nudge used to sit here: it
        # named one language's test tool and is gone -- design
        # 2026-10-24-swarmai-usable-by-anyone P7. AGENT.md asks for proportionate tests.)
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
# minted by the `pipeline_v1.py create` CLI SUBPROCESS (printed to stdout as
# {"ok": true, "pipeline_id": "run_xxx", ...}), while the session_id the gate keys on is the
# APP-LEVEL id in `session_context['sdk_session_id']` — set in the DAEMON at
# session_router.py:3421, NOT the SDK subprocess's own CLAUDE_CODE_SESSION_ID env
# (a DIFFERENT namespace — a CLI reading that env would write the mapping under the
# wrong key and D7 would never find it; verified by trace, R1.5a Gate-1).
#
# Two writers produce the map. (a) A PostToolUse-Bash hook: it runs in the daemon (so
# ctx carries the correct app-level session_id) AND the SDK hands it the Bash
# tool_response (so it sees create's stdout). (b) run_owner_gate, when it hands a run
# to this session at a takeover, so the link holds even when create's stdout is
# redirected and (a) sees nothing. The hook (1) revives the EXISTING-but-
# -set-nowhere `_active_pipeline_run_id` consumer (read at the marker writer below +
# the compaction briefing) and (2) persists a session_<sid>_run.map file into the
# SAME _PIPELINE_AUDIT_DIR the gate already scans by session_id.

# Matches a `pipeline_v1.py create` call. The CLI prints a compact JSON object; we read
# the pipeline_id field rather than regex-sniffing a bare run_xxx so a run id quoted
# elsewhere in the output (an error echoing a prior id) can't be mistaken for THIS
# call's result.
# (the recogniser is core.pipeline_command.is_create: both CLI forms)


def _session_run_map_path(session_id: str) -> Path:
    """Disk rendezvous file: session_id -> owning pipeline run_id.

    Lives in _PIPELINE_AUDIT_DIR (the same dir the session markers live in), so a
    future cross-process reader resolves it the identical way it
    already finds markers. One file per session; last create in a session wins
    (a session runs one pipeline at a time — sibling sessions get distinct ids).
    """
    return _PIPELINE_AUDIT_DIR / f"session_{session_id}_run.map"


def write_session_run_map(session_id: str, run_id: str, ts: float | None = None) -> None:
    """Atomically write the session->run rendezvous (tmp sibling + os.replace).

    The ONE writer of the .map shape ``{session_id, run_id, ts}``: the PostToolUse hook
    below (parses ``pipeline_v1.py create`` stdout) and run_owner_gate at a takeover
    (so a session working on an older run credits reviews to it) both call it. Raises on I/O
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
    """Pull `pipeline_id` out of a `pipeline_v1.py create` tool_response (stdout), or None.

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

    On a `pipeline_v1.py create` Bash call, parses the printed pipeline_id from
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
        from core import pipeline_command
        if not command or not pipeline_command.is_create(command):
            return {}

        tool_response = _extract_field(input_data, "tool_response", None)
        run_id = _extract_pipeline_id(tool_response)
        if not run_id:
            return {}  # create failed / unparseable → nothing to map

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
# structured anchor line `EXPERT-ID: <id>` (injected by the review prompt producer),
# and we extract it from the sub-agent's own transcript head.
#
# Anchored + bare-id-only ON PURPOSE: the line must START a line (`^EXPERT-ID:`),
# the id must be a lowercase registry id (`[a-z][a-z0-9-]*`), and nothing but
# whitespace may follow it (`\s*$`). This rejects the drift classes a natural-
# language classifier suffers from — a Title-case value, an
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


def create_v1_review_handout_tracker(session_context: Optional[dict] = None):
    """PreToolUse Agent|Task: an expert spawned with exactly a handout's spawn prompt (its
    four lines naming PROMPT-FILE) is a ``spawn`` event in that run
    (``hook_bridge.bind_spawn``); a differing one leaves a ``spawn_rejected``. The prompt is never
    rewritten: its ``PIPELINE-HANDOUT`` line is already there, written by the CLI, so
    a host that cannot rewrite tool input binds the same way (standalone §4.3)."""
    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        try:
            tool = _extract_field(input_data, "tool_name", "")
            if tool not in ("Agent", "Task"):
                return {}
            tool_input = _extract_field(input_data, "tool_input", {})
            prompt = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
            if "PIPELINE-HANDOUT: " in prompt:
                from core.run_protocol import hook_bridge
                await asyncio.to_thread(hook_bridge.on_spawn, prompt, str(tool_use_id or ""))
        except Exception:
            logger.warning("v1 review handout tracking failed", exc_info=True)
        return {}

    return _hook


def create_agent_tool_audit_hook(
    session_context: Optional[dict] = None,
):
    """Factory: creates a SubagentStop hook that writes an audit marker file.

    When the Agent tool completes (SubagentStop event), writes a marker file
    at ``STATE_DIR/pipeline_agent_audit/<run_id>.marker``. The pipeline
    validator reads this file to confirm the Agent tool was actually invoked
    during review — structural proof, not honor-system.

    Two markers:
    1. If ``_active_pipeline_run_id`` is set in session_context: writes
       ``<run_id>.marker`` (precise match — pipeline orchestrator sets this).
    2. Always: writes ``session_<session_id>_<ts>.marker`` as session-level
       evidence. It carries the ``expert_id`` of the prompt's EXPERT-ID line.
       A Gate-0 verdict is never written here: it exists only in the run's
       signed v1 answer event.
    """
    ctx = session_context if session_context is not None else {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        session_id = ctx.get("sdk_session_id", "")
        run_id = ctx.get("_active_pipeline_run_id")
        ctx_ts = ctx.get("_active_pipeline_run_id_ts")

        if not session_id and not run_id:
            return {}

        # The transcript READ + marker writes run in ONE
        # off-loop thread hop below — the sub-agent transcript is on the same disk
        # the daemon is actively writing, so reading it inline would block the event
        # loop on a busy disk (RP53; the sibling create_subagent_capture_hook already
        # offloads its transcript read via to_thread).
        transcript_path = _extract_field(input_data, "agent_transcript_path", "")
        last_message = _extract_field(input_data, "last_assistant_message", "")

        try:
            _PIPELINE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.time()

            # OFF-LOOP (run_a1f4c2d8 + run_df2668b4): transcript read, classification,
            # and BOTH/all markers in ONE thread hop — the reads+writes are one audit
            # record; splitting them would double hops and let a cancellation land
            # mid-record.
            def _classify_and_write() -> None:
                # The run this marker belongs to: the NEWER of the session ctx and the
                # session's .map. run_owner_gate writes the .map at a takeover, so a
                # run whose create stdout was redirected (the PostToolUse hook saw nothing and ctx still
                # names the previous run) still gets its own reviewers in the same turn.
                owner = run_id
                linked = read_session_run_map(session_id) if session_id else None
                if linked is not None:
                    known = float(ctx_ts) if isinstance(ctx_ts, (int, float)) else float("-inf")
                    if not owner or linked[1] > known:
                        owner = linked[0]
                # Read the sub-agent's spawn-prompt head once: it feeds expert_id
                # extraction and the v1 answer bridge below.
                prompt_head = (
                    _read_subagent_prompt_head(transcript_path) if transcript_path else ""
                )
                # Legacy session-marker attribution: a prompt FILE under
                # runs/<run_id>/evidence/prompts/ that the sub-agent read in full names
                # the run and expert. A v1 expert now reads its handout's audit blob
                # (PROMPT-FILE), not that file, so for v1 this falls back to the
                # spawn's own EXPERT-ID line; the v1 answer itself is bound below by
                # hook_bridge.on_subagent_stop, not by this marker.
                reads = _prompt_reads(transcript_path) if transcript_path else None
                owner, expert_id, attributed_by = _resolve_attribution(
                    _extract_expert_id(prompt_head) if prompt_head else None,
                    _handout_attribution(reads, session_id, prompt_head), owner)
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
                # completed sub-agent writes.
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
                    # ({} = none read, None = transcript unreadable → readers warn). Gate-0
                    # and prompt-backed reviews bind to the prompt file handed out.
                    "prompt_reads": reads,
                    # when it was spawned (its transcript's first record): REVIEW / DELIVER
                    # stage-complete require one round's experts to start together
                    "started_at": _subagent_started_at(transcript_path) if transcript_path else None,
                }
                final = (last_message if isinstance(last_message, str) and last_message.strip()
                         else (_read_subagent_final_text(transcript_path)
                               if transcript_path else ""))
                # Gate-0 verdicts are parsed from the signed answer event (run_protocol
                # answers / hook_bridge), never from this legacy marker (E)
                if expert_id and expert_id != "gate0-skeptic":
                    # Key present = this daemon records the expert's own findings and
                    # re-check answers (DELIVER stage-complete compares the record).
                    base.update(_expert_answer(final))
                session_marker.write_text(json.dumps(base))

                # Pipeline v1 path: the run named by the prompt's PIPELINE-HANDOUT line,
                # bound to a spawn a PreToolUse saw (hook_bridge, the same function every
                # host's hook calls). Legacy session ownership and expert/FIFO guesses are
                # not inputs here.
                from core.run_protocol import hook_bridge
                hook_bridge.on_subagent_stop(
                    prompt_head, final, transcript_path=transcript_path or None,
                    completed_at=datetime.datetime.fromtimestamp(
                        ts, datetime.timezone.utc).isoformat())

            await asyncio.to_thread(_classify_and_write)
        except Exception:
            logger.warning("Failed to write agent audit marker (session=%s)", session_id, exc_info=True)

        return {}

    return _hook


# the expert transcript readers live in core/subagent_transcript.py, where the
# `python-backend hook` command reads them too (no runtime_hooks import there); tests
# patch them on this module, so the hook below calls them through these names
from core.subagent_transcript import (  # noqa: E402
    read_final_text as _read_subagent_final_text,
    read_prompt_head as _read_subagent_prompt_head,
)


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
    an exact paste of the handed-out file binds too. Lets the run protocol check that
    an expert read the exact prompt its handout named. The file is hashed at SubagentStop: a copy edited and
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
        from config import get_swarmws  # local import: avoids a load-time cycle
        return os.path.realpath(get_swarmws() / "Projects")
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
    """Finding rows in one parsed JSON value: a finding object, a list of them, or
    an aggregate review object whose ``risks`` list holds them."""
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
    whole answer: a findings array, an aggregate ``{"risks": [...]}``), then one JSON
    object per line (trailing commas allowed), de-duplicated. ``reported_state`` =
    "findings" | "no_findings" (no finding and the answer ENDS in ``NO FINDINGS``, or a
    aggregate-review ``"verdict": "CLEAR"``) | "unparsed" (neither: DELIVER refuses to compare
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
# before classification. The injectors and their exact shapes (resume history,
# recall, UI state, task notifications, wrap-up / heal-continuation segments) are
# listed in core.injected_blocks, which the builders import their strings from.
def _strip_injected_prefix(prompt: str) -> str:
    """The user's own text for LANGUAGE classification (core.injected_blocks).

    Same stripping as every other prompt reader. A prompt that is all system blocks
    (a background-task notification, a resume-only turn) yields ``""``, and the
    classifier then stays silent: the turn has no user text, so it has no user
    language, and reading the system text instead flipped R19 to English.
    """
    from core.injected_blocks import strip_injected_blocks
    return strip_injected_blocks(prompt)


def _classify_message_language(prompt: str) -> Optional[str]:
    """Return "zh" if the message is CJK-majority, "en" if it is clearly Latin
    text, or None when ambiguous (too short / symbol-only) — in which case NO
    language reminder is injected (silence beats a wrong flip).

    Strips every system-injected block FIRST (see _strip_injected_prefix /
    core.injected_blocks): R19 must reflect the USER's language, not the English
    resume / recall / UI-state / terminal blocks the session router prepends.

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
    generate (O003 + P7). One signal:

    - **A — symmetric language (R19).** Detect THIS message's language and remind
      me to reply in the SAME language. SYMMETRIC + input-derived — a CJK message
      injects "respond in Chinese", an English message injects "respond in
      English". NEVER hardcoded to one language (hardcoding rebuilds the exact bug
      that motivated the hook: R19 is *match the input language*, not "reply in
      Chinese").
    (Signal B, "adversarial review is still mandatory in direct mode", was removed:
    native coding is the default and the agent decides which reviews a change needs,
    design 2026-10-24-swarmai-usable-by-anyone §3.4 / P6.)

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

            # The signal judges the USER's message, not the system-injected UI-state
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

    # R1.5a: PostToolUse session<->run rendezvous. Detects `pipeline_v1.py
    # create`, revives _active_pipeline_run_id in ctx, and persists a
    # session_<sid>_run.map file for the future D7 commit-gate. Pure plumbing.
    registry.register(
        "PostToolUse",
        create_pipeline_run_mapping_hook(session_context),
        "pipeline_run_mapping",
    )

    # Pipeline v1: bind each exact handed-out review prompt to the Agent tool_use_id.
    # The SubagentStop callback lacks that id, so this PreToolUse observation is the
    # only non-heuristic correlation path. Inert when no v1 bridge is installed.
    registry.register(
        "PreToolUse",
        create_v1_review_handout_tracker(session_context),
        "v1_review_handout_tracker",
        matcher="Agent|Task",
    )

    # Phase 2: PostToolUse session checkpoint (it upserts DailyActivity and leaves a
    # crash checkpoint the next session's resume context reads: neither on a worker
    # Hive, where the next session is another dispatched task, core/app_role)
    from core.app_role import is_worker
    if not is_worker():
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

    # (change_triggered_eval -- a scoped self-eval run on every governance-file edit --
    # was removed: it graded SwarmAI's own golden set, not a product behaviour.
    # Design 2026-10-24-swarmai-usable-by-anyone P7. Eval runs on demand / on schedule.)

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
    # Phase 2: UserPromptSubmit post-compact injection
    registry.register(
        "UserPromptSubmit",
        create_post_compact_injection(session_context),
        "post_compact_injection",
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
        "subagent_capture, post_compact_injection, "
        "observation_recorder, observation_completer"
    )
