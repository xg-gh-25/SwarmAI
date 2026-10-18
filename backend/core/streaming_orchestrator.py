"""Streaming Orchestrator — owns SDK response streaming and event formatting.

Extracted from session_unit.py as part of the strangler-fig refactoring.
This module contains the core streaming loop (~890 LOC):
- _stream_response(): sends query to SDK, reads response stream
- _read_formatted_response(): parses SDK messages into SSE events

Architecture:
- StreamingOrchestrator holds a parent reference to SessionUnit
- All SessionUnit state is accessed via self._parent.X
- SessionUnit retains a thin delegation method for _read_formatted_response
  (used by continue_with_permission and test files)
- Future: replace self._parent.X with typed callbacks for full decoupling

Design doc: Knowledge/Designs/2026-06-18-session-unit-strangler-fig-extraction-design.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Protocol

from . import executors
from . import pipeline_stage_events as _pse
from .compaction_guard import EscalationLevel
from .session_healing import get_process_rss_mb
from .ui_actions import (
    build_ui_command_event,
    build_surface_events_async,
    ensure_report_for_run,
    parse_completion_run_id,
    read_run_status,
    UI_ACTION_FULL_TOOL_NAME,
    SURFACE_OUTPUTS_FULL_TOOL_NAME,
)

if TYPE_CHECKING:
    from .session_unit import SessionUnit

logger = logging.getLogger(__name__)


def _is_blank_api_result(
    *,
    content_emitted: bool,
    is_error: bool,
    interrupted: bool,
    output_tokens: int,
    subtype: str,
) -> bool:
    """True when a non-error ResultMessage produced NOTHING renderable → retry.

    The "前端没渲染、后端正常" (blank turn) signature: the SDK returned a clean
    ResultMessage but no assistant content reached the user. By the time the
    orchestrator evaluates this, the error subtypes have already returned/raised
    upstream (``error_max_turns`` → turn_limit_reached; ``error_during_execution``
    / ``is_error`` → error path), so ``subtype`` here is only ever ``""`` or
    ``"success"`` — BOTH warrant a retry when empty.

    The original guard only fired on an empty subtype (``not subtype``), which let
    a ``subtype="success"`` envelope with 0 output tokens slip through silently.
    Observed live (run_7cf9da85 follow-up): session 2e87b27f, 2026-06-26 17:43 —
    a 68s turn returned input=0/output=0, subtype=success, no content; it passed
    both empty-guards, transitioned cleanly to IDLE, and the user saw a blank turn
    with no retry. This predicate is shared verbatim by the orchestrator guard and
    its regression test so the two can never drift (the project's recurring
    mirror-drift trap).

    Args mirror the orchestrator's locals at the result point. Returns True only
    for the blank-success/blank-empty case; any streamed content, any output
    tokens, an interrupt, an error, or an unrecognised subtype → False (do not
    retry).
    """
    return (
        not content_emitted
        and not is_error
        and not interrupted
        and output_tokens == 0
        and subtype in ("", "success")
    )


# ═══════════════════════════════════════════════════════════════════
# Callbacks Protocol (Phase 3 target — not used yet)
# ═══════════════════════════════════════════════════════════════════


class StreamingCallbacks(Protocol):
    """Interface contract between StreamingOrchestrator and SessionUnit.

    Phase 2 (current): Not used — orchestrator accesses parent directly.
    Phase 3 (future): SessionUnit implements this protocol; orchestrator
    calls through it instead of holding a parent reference.
    """

    def on_state_transition(self, new_state: Any) -> None:
        """Notify parent of state change (e.g., STREAMING → IDLE)."""
        ...

    async def on_kill_needed(self) -> None:
        """Request parent to kill the subprocess."""
        ...

    def get_session_id(self) -> str:
        """Get the owning session's ID."""
        ...


# ═══════════════════════════════════════════════════════════════════
# StreamingOrchestrator — Phase 2 (logic migrated, parent ref access)
# ═══════════════════════════════════════════════════════════════════


class StreamingOrchestrator:
    """Owns SDK response streaming and event formatting logic.

    Architecture:
    - Holds a reference to the parent SessionUnit
    - _stream_response() and _read_formatted_response() live here
    - All SessionUnit state accessed via self._parent.X
    - stream_query() is the public entry point

    Entry points:
    - stream_query(): called by SessionUnit.send(), _retry_with_resume(),
      _handle_buffer_overflow(), continue_with_answer()
    - _read_formatted_response(): called via SessionUnit delegation stub
      by continue_with_permission() and test files

    Future: replace self._parent.X with typed callbacks for full decoupling.
    """

    # No __slots__ — allows unittest.mock.patch.object() on instances.
    # Memory cost is negligible (one instance per session).

    # Leak dead-end continuation (see the ResultMessage branch). Consecutive
    # in-process retries allowed without a real tool_use: one corrective
    # message recovered ~90% of leaks in past transcripts, two ≈99%.
    _MAX_LEAK_CONTINUATIONS = 2
    # Descriptive only — it MUST NOT contain tool-call markup (that markup in
    # context is what primes the leak). Modeled on the CLI's own message for
    # the stop_reason=tool_use shape, which reliably gets a real tool_use.
    _LEAK_CONTINUATION_PROMPT = (
        "Your last tool call was written as plain text, so it was not "
        "executed. Retry it now as a real tool call and continue the task."
    )

    def __init__(self, parent: "SessionUnit") -> None:
        """Initialize with parent SessionUnit reference.

        Args:
            parent: The owning SessionUnit instance. All field accesses go
                through self._parent.X until Phase 3 decouples via callbacks.
        """
        self._parent = parent
        self._session_id = parent.session_id

    async def stream_query(
        self,
        query_content: Any,
    ) -> AsyncIterator[dict]:
        """Stream a query through the SDK and yield formatted SSE events.

        Public entry point — replaces direct SessionUnit._stream_response() calls.
        Delegates to self._stream_response() which owns the query send logic.

        Args:
            query_content: User message text (str) or multimodal blocks (list).

        Yields:
            Formatted SSE event dicts (text_delta, thinking_delta, tool_use, etc.)
        """
        async for event in self._stream_response(query_content):
            yield event

    # From this many leaks in one SDK session (counted since its transcript was
    # last cleaned) the history is treated as poisoned → clean + restart.
    _LEAK_RESTART_THRESHOLD = 2

    def _count_leak(self) -> None:
        """Count a leak against the CURRENT SDK session; a new SDK session id
        starts from zero (its history is a different transcript)."""
        parent = self._parent
        sid = parent._sdk_session_id
        if getattr(parent, "_leak_sid", None) != sid:
            parent._leak_sid = sid
            parent._leak_count = 0
        parent._leak_count = getattr(parent, "_leak_count", 0) + 1

    def _should_restart_for_leak(self, message: Any) -> bool:
        """True when this leaked message should trigger the clean restart."""
        from claude_agent_sdk import ToolUseBlock

        parent = self._parent
        if getattr(parent, "_leak_count", 0) < self._LEAK_RESTART_THRESHOLD:
            return False
        if not getattr(parent, "_leak_restart_eligible", False):
            return False  # not the primary send() stream, or already used
        if not parent._sdk_session_id:
            return False  # nothing to --resume
        if any(isinstance(b, ToolUseBlock) for b in message.content):
            return False  # a real call is in flight — never cut it off
        if parent._open_tool_uses:
            return False
        if parent._interrupted or getattr(parent, "_user_stopped_current_turn", False):
            return False
        return True

    # Continuation after a glitch-prefix restart. The tool call DID run, so this
    # must not say it failed. No tool-call markup and no glitch word in it.
    _GLITCH_CONTINUATION_PROMPT = "Continue the task where you left off."

    def _should_restart_for_glitch(self) -> bool:
        """True at a tool-result boundary where the glitch restart is safe."""
        parent = self._parent
        if not getattr(parent, "_leak_restart_eligible", False):
            return False  # not the primary send() stream, or budget used
        if not parent._sdk_session_id:
            return False  # nothing to --resume
        if parent._open_tool_uses:
            return False  # a tool is still running — never cut it off
        if getattr(parent, "_pending_tool_use_id", None) is not None:
            return False  # an AskUserQuestion / approval is outstanding
        if parent._interrupted or getattr(parent, "_user_stopped_current_turn", False):
            return False
        return True

    def _record_token_usage_bg(self, message: Any, usage: dict) -> None:
        """Fire-and-forget token accounting for a response whose `result` is
        not forwarded (leak continuation, degeneration abort) — its tokens
        were still spent."""
        if not usage:
            return
        try:
            import database
            asyncio.get_running_loop().create_task(
                database.db.record_token_usage(
                    session_id=self._parent.session_id,
                    source="cli",
                    input_tokens=usage.get("input_tokens") or 0,
                    output_tokens=usage.get("output_tokens") or 0,
                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                    cost_usd=getattr(message, "total_cost_usd", None),
                    model=self._parent._model_name,
                )
            )
        except Exception:
            logger.debug("_record_token_usage_bg: suppressed exception on a fail-safe path", exc_info=True)
            pass  # fire-and-forget — never break streaming

    @property
    def stall_seconds(self) -> Optional[float]:
        """Proxy to parent's streaming_stall_seconds."""
        return self._parent.streaming_stall_seconds

    def _auto_surfaced_run_ids(self) -> set:
        """Session-scoped set of run_ids whose finish-time Canvas surface has already
        fired this session (run_beff6754) — the idempotency guard shared by BOTH the
        explicit surface_run_outputs branch and the backend-auto completion
        observation, so a run is surfaced AT MOST ONCE regardless of which path fired.

        Lazily attached to the parent SessionUnit via getattr rather than declared in
        SessionUnit.__init__ — that class is CRITICAL (500+ callers) and this state is
        orchestrator-owned; a lazy attr keeps the change fully local to this module.
        A per-run-id set (not per-turn) is correct: it dedups auto-vs-explicit within a
        session while still surfacing a genuinely different run, and a re-run of the
        SAME completed run_id (retry) correctly does not re-pop the Canvas."""
        seen = getattr(self._parent, "_auto_surfaced_run_ids_set", None)
        if seen is None:
            seen = set()
            self._parent._auto_surfaced_run_ids_set = seen
        return seen

    def _pipeline_stage_snapshot(self) -> dict:
        """Session-scoped ``run_id → last-seen stage state`` (pipeline_stage_events).

        The baseline every stage diff is taken against. Session-scoped (lazily on the
        parent SessionUnit, like ``_auto_surfaced_run_ids``) rather than per turn, so a
        pipeline that spans several turns never repeats a stage row, and two
        artifact_cli calls in one message that both see the same transition emit it
        once: the first result advances the snapshot, the second diffs against it."""
        snap = getattr(self._parent, "_pipeline_stage_snapshot_map", None)
        if snap is None:
            snap = {}
            self._parent._pipeline_stage_snapshot_map = snap
        return snap

    async def _watch_pipeline_stage_call(self, run_id: str) -> None:
        """Seed the snapshot for a run this session has not seen yet, at its tool_use
        — normally before the CLI has written run.json — so the first diff only shows
        what this call changed, not every stage the run finished earlier. (The CLI runs
        the tool on its own, so the order is not guaranteed; if the write wins, that one
        row is missed, never duplicated.) A run already in the snapshot needs no read.
        Fail-safe: any error leaves the run unseeded, and the result then only seeds
        (see _pipeline_stage_events) — a missing baseline never replays history."""
        snap = self._pipeline_stage_snapshot()
        if run_id in snap:
            return
        try:
            state = await executors.run_in("io", _pse.read_stage_state, run_id)
        except Exception as e:  # noqa: BLE001 — a display aid must never break the turn
            logger.warning("pipeline stage pre-read failed for %r: %s", run_id, e)
            return
        if state is not None:
            snap[run_id] = state

    async def _pipeline_stage_events(self, run_id: str) -> list[dict]:
        """After a SUCCESSFUL artifact_cli call: re-read run.json (the authority — the
        command string proves nothing), diff its stages against the session snapshot,
        advance the snapshot, and return one ``assistant`` event carrying a
        ``pipeline_stage`` block per newly finished stage ([] when nothing changed).

        The event goes through the normal assistant path, so session_router persists
        it as its own row and the chat shows it in place after a reload. Fail-safe:
        any error → [] and the turn continues."""
        try:
            after = await executors.run_in("io", _pse.read_stage_state, run_id)
            if after is None:
                return []
            snap = self._pipeline_stage_snapshot()
            before = snap.get(run_id)
            snap[run_id] = after
            at = datetime.now().astimezone().isoformat()
            blocks: list[dict] = []
            # The run's alignment (its requirement and plan), once per run per
            # session, the first time a call names a run still in progress.
            announced = getattr(self._parent, "_pipeline_runs_announced", None)
            if announced is None:
                announced = self._parent._pipeline_runs_announced = set()
            if run_id not in announced and after.get("status") in (None, "running"):
                announced.add(run_id)
                rb = _pse.run_block(run_id, after, at)
                if rb:
                    blocks.append(rb)
            if before is not None:
                # No baseline (the pre-read failed or found no run.json): seed only.
                # Diffing against nothing would emit every stage already finished.
                blocks += _pse.new_stage_blocks(run_id, before, after, at)
            # What needs the user rides with the DELIVER row (structured fields only).
            if any(b.get("stage") == "deliver" for b in blocks):
                ab = await executors.run_in("io", _pse.attention_block, run_id, after, at)
                if ab:
                    blocks.append(ab)
        except Exception as e:  # noqa: BLE001 — a display aid must never break the turn
            logger.warning("pipeline stage emit failed for %r: %s", run_id, e)
            return []
        if not blocks:
            return []
        # Not model output: _content_emitted (zombie/empty-turn detection) is left alone.
        return [{"type": "assistant", "content": blocks, "model": None}]

    def _clear_completed_sub_agents(self, message: Any) -> None:
        """Remove tracking entries for sub-agents (Agent tool) that completed.

        Sub-agent (Agent tool) results are delivered by the SDK in a
        parent-level ``UserMessage`` (tool_result blocks live in user-typed
        turns per the Anthropic protocol), NOT in the ``AssistantMessage``
        that the main loop's ToolResultBlock branch handles. Because the loop
        had no UserMessage handler, ``_active_agent_tools`` entries were never
        popped — leaving ``count`` frozen, the elapsed timer climbing forever,
        and the progress label stale (the "Spec compliance review" bug).

        This is CLEANUP ONLY — it never yields events and never renders
        content. ``_active_agent_tools`` only ever holds **Agent-tool** ids
        (populated under ``block.name == "Agent"``), so ``pop(id, None)`` is a
        safe no-op for any unrelated tool_result (e.g. a parent's own
        Edit/Write result) — this helper leaves those untouched. NOTE: a
        parent's own Edit/Write result ALSO arrives via UserMessage (per the
        Anthropic protocol), and its ``file_changed`` emit is handled INLINE in
        the UserMessage branch of ``_read_formatted_response`` (guarded on
        ``_pending_file_changes`` membership), NOT here and NOT in the
        AssistantMessage branch (run_0520a394).

        Args:
            message: An SDK message (typically UserMessage). Its ``content``
                may be a plain string or a list of content blocks.
        """
        from claude_agent_sdk import ToolResultBlock

        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return  # string content (or none) carries no tool_result blocks
        for block in content:
            if isinstance(block, ToolResultBlock):
                self._parent._active_agent_tools.pop(block.tool_use_id, None)
                # Agent-tool results arrive HERE (UserMessage), not in the
                # AssistantMessage ToolResultBlock branch (PIT03). Clear the
                # open-tool tracker on this path too, else a sub-agent id
                # lingers and could false-trip the tool-hang tier (run_fb6e94a9).
                self._parent._open_tool_uses.pop(block.tool_use_id, None)
                self._parent._tool_hang_interrupted = False
                # Clear the grace window too — it was armed for a specific
                # interrupt; unrelated tool completion must not keep the
                # destructive backstop suppressed (adversarial v1 MED).
                self._parent._tool_hang_interrupt_at = None
                # A completed tool = the session recovered + made progress, so
                # reset the escalation counter — it must count CONSECUTIVE
                # unrecovered wedges, not lifetime-separate ones (adversarial v2 MED).
                self._parent._tool_hang_episodes = 0

    # ═══════════════════════════════════════════════════════════════
    # Migrated from session_unit.py — _stream_response
    # ═══════════════════════════════════════════════════════════════

    async def _stream_response(
        self,
        query_content: Any,
    ) -> AsyncIterator[dict]:
        """Send query and yield raw SDK messages.

        Reads ``client.receive_response()`` and yields each message
        as-is.  Handles state transitions:

        - On ``result`` message → STREAMING → IDLE
        - On ``ask_user_question`` / ``cmd_permission_request`` →
          STREAMING → WAITING_INPUT
        - On error → raises exception for caller to handle

        The caller (``send()``) is responsible for retry logic and
        error event construction.

        Args:
            query_content: User message text (str) or multimodal blocks (list).
        """
        if self._parent._client is None:
            raise RuntimeError(
                f"No client available for session {self._parent.session_id}"
            )

        # ── Sanitize query content: strip null bytes ─────────────
        if isinstance(query_content, str) and "\x00" in query_content:
            logger.warning("session_unit: stripping null bytes from query_content")
            query_content = query_content.replace("\x00", "")
        elif isinstance(query_content, list):
            for block in query_content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    if "\x00" in block["text"]:
                        logger.warning("session_unit: stripping null bytes from content block")
                        block["text"] = block["text"].replace("\x00", "")

        # Send the query
        if isinstance(query_content, list):
            # Multimodal content — wrap in async generator
            async def _multimodal_gen():
                msg = {
                    "type": "user",
                    "message": {"role": "user", "content": query_content},
                    # Always None — the tool-result routing path was removed when
                    # continue_with_answer migrated to _read_formatted_response().
                    # Key retained to preserve the exact SDK message shape.
                    "parent_tool_use_id": None,
                }
                yield msg

            await self._parent._client.query(_multimodal_gen())
        else:
            await self._parent._client.query(query_content)

        logger.info(
            "Query sent for session %s, reading response...",
            self._parent.session_id,
        )

        # Reset hang timer — stream just started; don't carry stale
        # _last_activity_time from a previous turn that ended 90s+ ago.
        self._parent._health_sensor.record_activity()

        # Read and format the SDK response stream
        async for event in self._read_formatted_response():
            yield event


    # run_cce6f4b9: event-driven WRITE surfacing is restored here as
    # _build_file_write_events (below), the symmetric twin of _build_file_delete_events.
    # It replaces the per-turn git sweep (sweep_turn_delta) that run_4de279ca had made
    # the sole authority — that sweep was the tab-isolation/trigger/cost regression root
    # cause. Both emits fire at the ToolResult(success) point (see stream loop).

    async def _build_file_write_events(
        self,
        raw_paths: list[str],
        resolve_cache: "dict[str, dict | None]",
        modified_since: "float | None" = None,
    ) -> list[dict]:
        """Turn a tool's WRITTEN raw path(s) into unified file_changed(operation=written)
        events — the EVENT-DRIVEN live-surfacing emit (run_cce6f4b9, the symmetric twin
        of _build_file_delete_events). Restores the per-tool emit that run_4de279ca
        replaced with a per-turn global git sweep (the tab-isolation/trigger/cost
        regression root cause).

        For each raw path: resolve its PHYSICAL absolute path ONCE (per-turn cache),
        then classify via `needs_human_review` (the SOLE verdict authority — same one the
        delete path uses). Resolution runs off-loop (bare-name branch may os.walk) but
        ONLY for these deliverable-candidate writes — reads/greps never reach here.

        Two gates, both mirroring the delete twin:
          - Layer-1 emit gate (run_6ebe2d09): a WRITTEN path that fails to resolve is NOT
            a real file (a just-written deliverable always exists on disk), so resolve=None
            means garbage (e.g. a mis-parsed Bash `>` fragment) → DROP.
          - review-worthy gate: needs_human_review.review_worthy is False for machine
            noise (process / gitignored) → DROP. The TRUE `kind` (content/knowledge/source)
            is CARRIED on the event; the FRONTEND gates rail-vs-pop by kind (useReferencedFiles
            drops process/source from the rail; useCanvasAutoSurface pops only content/knowledge).
            The backend does NOT gate on kind — it emits every review-worthy write with its
            real kind, exactly as the pre-run_4de279ca emit did.

        Fail-safe: each path's work is independent (gather); one path's exception never
        sinks the batch. On a needs_human_review error, fall back to kind="content" so a
        resolved deliverable still surfaces (mirrors the pre-regression fallback).
        """
        from core.needs_human_review import needs_human_review
        from core.canvas_surface import is_canvas_surfaceable
        from core.project_registry import get_swarmws
        from routers.workspace_api import resolve_path_to_physical

        ws_root = get_swarmws()

        async def _resolve_one(raw: str) -> "dict | None":
            if raw in resolve_cache:
                return resolve_cache[raw]
            try:
                r = await executors.run_in("io", resolve_path_to_physical, raw, ws_root)
            except Exception:  # noqa: BLE001 — fail-safe: unresolvable → dropped below
                logger.debug("_resolve_one: suppressed exception on a fail-safe path", exc_info=True)
                r = None
            resolve_cache[raw] = r
            return r

        async def _build_one(raw: str) -> "dict | None":
            resolved = await _resolve_one(raw)
            # Layer-1 emit gate: unresolvable → not a real file → DROP.
            if not resolved:
                return None
            # Candidate paths (interpreter heredoc literals) only count if the file
            # really changed during the tool call — a script that READ it names it
            # too. 1s slack covers coarse filesystem mtime resolution.
            if modified_since is not None:
                try:
                    _mtime = os.stat(resolved["absolute"]).st_mtime
                except OSError:
                    return None
                if _mtime < modified_since - 1.0:
                    return None
            # Unified review verdict. needs_human_review does its own owning-tree
            # resolution on the ABSOLUTE path + a `git check-ignore` subprocess → off-loop
            # via to_thread. Fail-safe: on error, fall back to kind="content" (still
            # review-worthy) so a resolved deliverable is not silently dropped.
            try:
                verdict = await executors.run_in(
                    "io", needs_human_review, resolved["absolute"], "written"
                )
                if not verdict.review_worthy:
                    # SwarmWS/bound-worktree machine-noise → never surface. BUT a file
                    # the session touched OUTSIDE every known tree (an external repo or
                    # plain FS) is declined by needs_human_review only because it's not
                    # git-commit material — it IS a Canvas-review-worthy activity
                    # (run_5d9178bf). Consult the Canvas-only predicate (a gitignored
                    # external secret still declines). off-loop (git subprocesses).
                    surf = await executors.run_in(
                        "io", is_canvas_surfaceable, resolved["absolute"]
                    )
                    if not surf.surfaceable:
                        return None  # machine noise / gitignored / inside-tree → never surface
                    # Record this external path as SURFACED for THIS session so the
                    # stateless render endpoint can allow rendering it (read-only)
                    # WITHOUT re-running the stateless predicate (which would leak
                    # /etc/passwd — the Gate-0 hole). run_c014a4f3.
                    from core import session_registry as _sr
                    _sid = self._parent.session_id
                    _sr.record_surfaced_path(_sid, resolved["absolute"])
                    ev: dict = {
                        "type": "file_changed",
                        "path": resolved["relative"],
                        "absolutePath": resolved["absolute"],
                        "kind": surf.kind,
                        "operation": "written",
                        # session_id rides the rail row so the render fetch can pass it
                        # back (the frontend has NO session_id prop — run_26aa6caa
                        # removed it; the row-carried id is the stateless carrier).
                        "sessionId": _sid,
                    }
                    if surf.base_ref:
                        ev["baseRef"] = surf.base_ref
                    return ev
                kind = verdict.kind
            except Exception as _verdict_err:  # noqa: BLE001
                # Fail-safe to kind="content" (a resolved deliverable still surfaces),
                # but LOG loudly — a silent demote would hide a real crash in
                # needs_human_review (_owning_tree / _classify_kind / git) behind a
                # plausible-looking content row (operational MED, run_cce6f4b9).
                logger.warning(
                    "file_write_events: needs_human_review errored for %s: %s — "
                    "surfacing as kind=content (fail-safe)",
                    resolved.get("absolute"), f"{type(_verdict_err).__name__}: {_verdict_err}",
                )
                kind = "content"
            return {
                "type": "file_changed",
                "path": resolved["relative"],
                "absolutePath": resolved["absolute"],
                "kind": kind,
                "operation": "written",
            }

        built = await asyncio.gather(
            *(_build_one(raw) for raw in raw_paths),
            return_exceptions=True,
        )
        # Drop None (unresolvable / not-review-worthy) AND any Exception (a single path's
        # failure must not sink the batch). gather preserves input order.
        return [ev for ev in built if isinstance(ev, dict)]

    async def _script_output_events(
        self, sweep: tuple[str, float], resolve_cache: "dict[str, dict | None]",
    ) -> list[dict]:
        """file_changed events for the files a Bash call's script wrote under its
        root during the call (core.script_output_sweep), through the normal write
        gate (resolve, mtime re-check, needs_human_review). Fail-safe: [] on error."""
        from core.script_output_sweep import recent_outputs
        root, since = sweep
        try:
            paths = await executors.run_in("io", recent_outputs, root, since)
            if not paths:
                return []
            return await self._build_file_write_events(paths, resolve_cache, modified_since=since)
        except Exception:  # noqa: BLE001 — a display aid must never break the turn
            logger.debug("script output sweep failed for %r", root, exc_info=True)
            return []

    async def _build_file_delete_events(
        self, raw_paths: list[str], resolve_cache: "dict[str, dict | None]"
    ) -> list[dict]:
        """Turn a tool's DELETED raw path(s) into unified file_changed(operation=deleted)
        events so the frontend can REMOVE stale rail rows (G1, run_5a7be540).

        Unlike a write, a deleted file no longer exists on disk, so
        resolve_path_to_physical (which requires is_file) usually returns None — a
        relative path or a plain-name delete can't be stat-relativized after the
        fact. That is fine: the delete event carries the BEST path form we have
        (resolved relative if the resolve happened to succeed — e.g. an absolute
        path still structurally under a Projects/ symlink — else the raw path for
        BOTH path and absolutePath). The FRONTEND removes conservatively (anchored
        match on path/absolutePath), so a delete that matches nothing is a harmless
        no-op (a stale row lingers == today's behavior; safe direction).

        run_4de279ca (Gate-2 F6/F7): the bookkeeping filter uses the SAME git-based
        authority as writes (`needs_human_review`), NOT the old hardcoded
        `_BOOKKEEPING_DIRS` denylist — so a delete and a write of the same path get
        the SAME verdict (no two-authority divergence). `needs_human_review` is
        PATH-based (git check-ignore matches path patterns; `.resolve()` works on a
        non-existent path) — it never stats the file, so a deleted (gone) path
        classifies fine. Only a review-worthy path emits a delete event (drops its
        stale rail row); machine noise never had a row to drop.
        """
        from core.needs_human_review import needs_human_review
        from core.canvas_surface import is_canvas_surfaceable
        from core.project_registry import get_swarmws
        from routers.workspace_api import resolve_path_to_physical

        ws_root = get_swarmws()
        events: list[dict] = []
        for raw in raw_paths:
            # needs_human_review runs a `git check-ignore` SUBPROCESS → off-loop via
            # to_thread (RP53: a sync subprocess here would block the daemon's shared
            # event loop for every deleted path — a `rm *.tmp` batch = N blocking spawns
            # freezing /health). Mirrors _build_file_write_events' off-loop verdict; a
            # verdict error fails-safe to "surface it" (a stale row lingering ==
            # pre-G1 behavior, the safe direction for a DELETE).
            try:
                _worthy = (await executors.run_in(
                    "io", needs_human_review, raw, "written"
                )).review_worthy
            except Exception:  # noqa: BLE001 — fail-safe: emit the delete (drop the row)
                logger.debug("_build_file_delete_events: suppressed exception on a fail-safe path", exc_info=True)
                _worthy = True
            # Resolve to a physical absolute path FIRST (shared with the emit below +
            # symmetric with the write gate, which consults is_canvas_surfaceable on
            # resolved["absolute"], not the raw path). A deleted file is usually gone,
            # so resolve often returns None — fall back to the raw path.
            if raw in resolve_cache:
                resolved = resolve_cache[raw]
            else:
                try:
                    resolved = await executors.run_in("io", resolve_path_to_physical, raw, ws_root)
                except Exception:
                    logger.debug("_build_file_delete_events: suppressed exception on a fail-safe path", exc_info=True)
                    resolved = None
                resolve_cache[raw] = resolved
            if not _worthy:
                # An OUTSIDE-tree file (external repo / plain FS) is declined by
                # needs_human_review, but if it WAS surfaced as an external row, its
                # delete must still mark that row (run_5d9178bf). Consult the Canvas
                # predicate on the RESOLVED ABSOLUTE path (symmetric with the write
                # gate) — a relative `rm notes.txt` in an external repo resolves to an
                # absolute path whose parent dir git-probes correctly. A gitignored
                # external secret never had a row → declines → nothing to mark.
                _ext_probe = resolved["absolute"] if resolved else raw
                try:
                    _ext_surf = (await executors.run_in(
                        "io", is_canvas_surfaceable, _ext_probe
                    )).surfaceable
                except Exception:  # noqa: BLE001 — fail-safe: don't emit a phantom delete
                    logger.debug("_build_file_delete_events: suppressed exception on a fail-safe path", exc_info=True)
                    _ext_surf = False
                if not _ext_surf:
                    continue
            path = resolved["relative"] if resolved else raw
            abs_path = resolved["absolute"] if resolved else raw
            events.append({
                "type": "file_changed",
                "path": path,
                "absolutePath": abs_path,
                "relevance": "incidental",
                "kind": "content",
                "operation": "deleted",
            })
        return events

    @staticmethod
    def _notice_event(text: str) -> dict:
        """An in-conversation notice: an ``assistant`` text event, so it is
        persisted with the turn and rendered in the chat (the frontend keeps a
        confirmed text block that differs from earlier ones)."""
        return {"type": "assistant", "content": [{"type": "text", "text": text}], "model": None}

    def _compaction_events(self, subtype: str, data: Any) -> list[dict]:
        """Live SSE events for a CLI compaction, from its stream SystemMessages.

        Observed CLI sequence (probed 2026-10-17): ``status`` status=compacting
        → PreCompact hook → ``status`` status=None compact_result=success|… →
        ``compact_boundary`` with ``compact_metadata`` {trigger, pre_tokens,
        post_tokens, duration_ms}. These used to be dropped, so the user saw a
        silent 1-2 minute pause and the notice only after ``result``.

        * status=compacting → ``context_compacting`` (started).
        * status=None, compact_result not "success" → ``context_compacting``
          (failed).
        * compact_boundary → ``context_compacted`` with sizes; clears the
          PreCompact flag so the turn-end fallback notice does not repeat it, and
          activates the CompactionGuard on the REAL signal (not only the
          averaged-usage drop heuristic); followed by a ``context_warning``
          snapshot of the post-compaction size (``SessionUnit._record_compact_boundary``).
        """
        from .prompt_builder import PromptBuilder

        data = data if isinstance(data, dict) else {}
        parent = self._parent
        if subtype == "status":
            if data.get("status") == "compacting":
                parent._last_progress_time = time.time()
                logger.info("session_unit.compaction_started session_id=%s",
                            parent.session_id)
                return [PromptBuilder.build_compacting_notice()]
            result = data.get("compact_result")
            if data.get("status") is None and result and result != "success":
                logger.warning("session_unit.compaction_failed session_id=%s result=%s",
                               parent.session_id, result)
                return [PromptBuilder.build_compacting_notice(failed=True)]
            return []
        if subtype == "compact_boundary":
            parent._last_progress_time = time.time()
            md = data.get("compact_metadata")
            ctx = getattr(parent, "_hook_session_context", None)
            if isinstance(ctx, dict):
                ctx["_compact_notice_pending"] = False
            try:
                parent._compaction_guard.activate()
            except Exception:  # noqa: BLE001 — visibility must never break the stream
                logger.debug("compaction_guard.activate failed", exc_info=True)
            evt = PromptBuilder.build_compact_boundary_notice(md)
            logger.info(
                "session_unit.compaction_done session_id=%s trigger=%s pre=%s post=%s",
                parent.session_id, evt.get("trigger"),
                evt.get("preTokens"), evt.get("postTokens"),
            )
            events = [evt]
            # The context snapshot follows the CLI's own post-compaction size, so
            # the ring shows it immediately instead of going empty until the next
            # request (or "none" when the CLI reported no size).
            try:
                events.append(parent._record_compact_boundary(md))
            except Exception:  # noqa: BLE001 — visibility must never break the stream
                logger.debug("context usage compact record failed", exc_info=True)
            return events
        return []

    @staticmethod
    def _tool_result_block(block: Any, tool_name: str) -> dict:
        """A UI tool_result block: truncated content + a one-line ``outcome``
        computed from the FULL result (e.g. "lines 1–240", "12 matches")."""
        from core.tool_summarizer import (
            summarize_tool_result, tool_result_text, truncate_tool_result,
        )

        is_error = bool(getattr(block, "is_error", False))
        text = tool_result_text(getattr(block, "content", None))
        truncated, was_truncated = truncate_tool_result(text)
        return {
            "type": "tool_result", "tool_use_id": block.tool_use_id,
            "content": truncated, "is_error": is_error, "truncated": was_truncated,
            "outcome": summarize_tool_result(tool_name, text, is_error),
        }

    @staticmethod
    def _subagent_progress_event(message: Any, task_parent: dict) -> "dict | None":
        """A live ``subagent_progress`` event from an SDK Task* system message.

        Without it a sub-agent run is silent in the chat: the main agent emits
        nothing until the Agent tool returns, so the user cannot tell anything is
        running (XG 2026-10-18: "不能把我的 chat experience 搞断流"). Keyed by
        ``parentToolUseId`` — the Agent tool_use the sub-agent runs under — so
        the UI nests it under that step. ``task_parent`` (task_id → parent id) is
        per-turn state: TaskUpdatedMessage carries no tool_use_id of its own.
        Not an ``assistant`` event, so it is never persisted.
        """
        from claude_agent_sdk import (
            TaskNotificationMessage, TaskProgressMessage, TaskStartedMessage, TaskUpdatedMessage,
        )

        if not isinstance(message, (TaskStartedMessage, TaskProgressMessage,
                                    TaskNotificationMessage, TaskUpdatedMessage)):
            return None
        task_id = getattr(message, "task_id", None)
        parent = getattr(message, "tool_use_id", None) or task_parent.get(task_id)
        if not parent:
            return None  # a background task not tied to a step in this turn
        if task_id:
            task_parent[task_id] = parent
        event: dict = {"type": "subagent_progress", "parentToolUseId": parent, "taskId": task_id}
        if isinstance(message, TaskUpdatedMessage):
            status = message.status or (message.patch or {}).get("status")
            if not status:
                return None
            event["status"] = "stopped" if status == "killed" else status
            return event
        if isinstance(message, TaskNotificationMessage):
            event["status"] = message.status
            if message.summary:
                event["summary"] = message.summary[:300]
        else:
            event["status"] = "running"
        if getattr(message, "description", None):
            event["description"] = message.description[:200]
        if getattr(message, "last_tool_name", None):
            event["lastToolName"] = message.last_tool_name
        usage = getattr(message, "usage", None) or {}
        if usage:
            event["toolUses"] = usage.get("tool_uses")
            event["durationMs"] = usage.get("duration_ms")
        return event

    def _stall_event(self, text: str) -> dict:
        """A no-progress notice as the in-tab ``still_working`` status — NOT an
        ``assistant`` event: it describes the stream, so it must not be
        persisted as something the model said, nor replace text still
        streaming in the UI."""
        start = self._parent._streaming_start_time
        return {
            "type": "still_working",
            "elapsedSeconds": int(time.time() - start) if start else 0,
            "message": text,
            "stall": True,
        }

    async def _interrupt_draining(self, response_iter: Any) -> None:
        """Run the user-Stop interrupt while still reading the SDK stream.

        The SDK buffers at most 100 messages between its stdout reader and us;
        a looping model fills that fast, and a full buffer stops the reader
        from ever seeing the interrupt's control response (5s timeout, then a
        hard kill). A real user Stop never hits this because the stream keeps
        being consumed meanwhile — so do the same: read and discard until the
        interrupt returns. Messages read here belong to the aborted block —
        except the turn's ResultMessage, whose usage is still recorded (a loop
        near the output cap is the costliest turn there is).
        """
        from claude_agent_sdk import ResultMessage

        itask = asyncio.ensure_future(self._parent.interrupt())
        while not itask.done():
            ntask = asyncio.ensure_future(response_iter.__anext__())
            done, _ = await asyncio.wait({itask, ntask}, return_when=asyncio.FIRST_COMPLETED)
            if ntask not in done:
                ntask.cancel()
                try:
                    await ntask
                except BaseException as exc:  # noqa: BLE001 — the stream is being torn down
                    logger.debug("output_degeneration drain: pending read cancelled (%s)", type(exc).__name__)
                break
            try:
                msg = ntask.result()
            except BaseException as exc:  # noqa: BLE001 — exhausted / closed: nothing left to drain
                logger.debug("output_degeneration drain: stream ended (%s)", type(exc).__name__)
                break
            if isinstance(msg, ResultMessage):
                self._record_token_usage_bg(msg, getattr(msg, "usage", None) or {})
        try:
            await itask
        except Exception as exc:  # noqa: BLE001 — the turn must still be closed for the user
            logger.error(
                "session_unit.output_degeneration_interrupt_failed session_id=%s: %s",
                self._parent.session_id, f"{type(exc).__name__}: {exc}",
            )

    async def _abort_on_degeneration(
        self, det: Any, parent_tool_use_id: Optional[str], response_iter: Any,
    ) -> list[dict]:
        """End the turn because a content block degenerated into a loop.

        Uses the user-Stop path — ``SessionUnit.interrupt()`` (non-autonomous:
        sets ``_user_stopped_current_turn``, recycles the poisoned subprocess
        to COLD keeping the --resume id), with the stream drained meanwhile
        (``_interrupt_draining``). Never retried: re-running the same context
        is likely to loop again. The caller ``return``s right after yielding
        these events, so nothing that handles a ResultMessage — leak
        continuation (``client.query``), leak/glitch restarts, retry guards —
        can run for this turn.

        Every per-turn parent field is read/written BEFORE the interrupt: once
        it reaches IDLE, the drain worker may already start the next send() on
        this unit, whose state must not be touched.

        Returns the events to yield: for a text block, its legit prefix (cut at
        the start of the repeated run, leak/glitch text stripped) plus the
        notice as an ``assistant`` event — it replaces the provisional streamed
        text in the UI and is what gets persisted — then a ``result`` that
        closes the turn. The CLI still writes the partial block to its own
        transcript; ``_transcript_poisoned`` makes the next send() clean it
        (``leak_transcript``) before --resume.
        """
        from .output_degeneration import (
            DEGENERATION_NOTICE,
            compress_ratio,
            find_degenerate_run,
        )
        from .session_utils import strip_glitch_prefix, strip_tool_call_leak

        text = det.text
        run = find_degenerate_run(text)
        cut = run[0] if run else len(text)
        logger.warning(
            "session_unit.output_degeneration session_id=%s block=%s tool=%s sidechain=%s "
            "chars=%d cut_at=%d ratio=%.1f sample=%r — interrupting turn (user-Stop path, no retry)",
            self._parent.session_id, det.kind, det.name or "-", bool(parent_tool_use_id),
            len(text), cut, compress_ratio(text[-2000:]), text[cut:cut + 80],
        )
        if det.kind == "tool_use":
            body = (
                f"{DEGENERATION_NOTICE}\n(工具 {det.name or '?'} 的调用输入陷入重复,已中止;"
                f"若它已开始执行,结果可能不完整 / the call to {det.name or 'a tool'} was "
                "stopped; if it had already started, its result may be incomplete.)"
            )
        else:
            prefix = ""
            if not parent_tool_use_id and det.kind == "text":
                prefix = strip_glitch_prefix(strip_tool_call_leak(text[:cut])).rstrip()
            body = f"{prefix}\n\n{DEGENERATION_NOTICE}" if prefix else DEGENERATION_NOTICE
        start = self._parent._streaming_start_time
        events = [
            self._notice_event(body),
            {
                "type": "result",
                "subtype": "output_degenerated",
                "stop_reason": "output_degenerated",
                "session_id": self._parent.session_id,
                "duration_ms": int((time.time() - start) * 1000) if start else 0,
                "total_cost_usd": None,
                "num_turns": 1,
                "usage": None,
            },
        ]
        self._parent._transcript_poisoned = True
        self._parent._content_emitted = True
        await self._interrupt_draining(response_iter)
        return events

    # ═══════════════════════════════════════════════════════════════
    # Migrated from session_unit.py — _read_formatted_response
    # ═══════════════════════════════════════════════════════════════

    async def _read_formatted_response(self) -> AsyncIterator[dict]:
        """Read SDK response stream and yield formatted SSE events.

        Shared by ``_stream_response`` (after query) and
        ``continue_with_permission`` / ``continue_with_answer``
        (resume after user input).

        Handles state transitions:
        - On result → STREAMING → IDLE
        - On ask_user_question → STREAMING → WAITING_INPUT
        - On error → raises for caller to handle
        """
        # Lazy import to avoid circular dependency (session_unit → streaming_orchestrator → session_unit)
        from .session_unit import SessionState  # noqa: F811

        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
            UserMessage,
        )
        from claude_agent_sdk.types import StreamEvent, ThinkingBlock

        try:
            from core.tool_summarizer import summarize_tool_use, get_tool_category, truncate_tool_result
            _has_tool_summarizer = True
        except ImportError:
            _has_tool_summarizer = False

        # ── Per-message timeout: structurally prevents hanging ─────
        # The SDK async iterator can hang forever if the subprocess
        # stops producing messages (no ResultMessage, no error, nothing).
        # Wrap each __anext__() call with a timeout so the stream
        # CANNOT stay stuck.  On timeout, we raise — the caller's
        # retry logic handles recovery with --resume.
        #
        # First message uses a shorter timeout because the subprocess
        # should send an init/system message quickly after spawn.
        # 180s accommodates cross-region Bedrock + --resume session restore.
        # Single timeout for both fresh and resume — simpler, fewer states.
        MESSAGE_TIMEOUT = self._parent._compute_message_timeout()  # Adaptive: scales with context
        # First-message timeout (run_4b74b764, Part B). Policy lives in ONE place
        # (_compute_init_timeout): resume sessions floor at the adaptive timeout
        # (heavy conversation replay before inference), fresh keep the fast 180s.
        is_resume = self._parent._sdk_session_id is not None
        INIT_TIMEOUT = self._parent._compute_init_timeout()
        # Poll interval for surfacing "still working" heartbeats during a
        # silent wait. Must be small enough to bubble a long/stuck step
        # promptly; the heartbeat itself throttles to one notice per
        # LONG_TURN_HEARTBEAT_S. Does NOT cancel the in-flight SDK read.
        HEARTBEAT_POLL_S = 30.0

        # (is_resume computed above for the adaptive INIT_TIMEOUT floor)
        is_first_message = True
        saw_assistant_message = False  # Track if LLM actually responded

        # ── Tool-call leak: live hiding + dead-end continuation ────
        # Per content-block index: text streamed so far, and whether a leak
        # onset was seen (later deltas for that block are not forwarded — the
        # authoritative `assistant` event replaces the provisional text).
        _delta_text_buf: dict[int, str] = {}
        _delta_leak_hidden: set[int] = set()
        # True while the most recent text/tool output of this response is a
        # leaked tool call (cleared by any real tool_use or later real prose).
        # If the turn ENDS in that state the model's action never ran — the
        # CLI only self-retries when stop_reason=tool_use, not end_turn.
        _leak_tail = False
        # Consecutive continuations that produced no real tool_use. Bounds the
        # retry so a model that keeps leaking cannot loop.
        _leak_continuations = 0
        # True once a main-thread text block carried the glitch-prefix shape
        # ("core 跑测试…"). The word is stripped from what is shown/persisted,
        # but the CLI's in-memory history keeps it and the model copies it, so
        # the next safe boundary (tool results back, nothing open) hands off to
        # the clean+resume restart; if the turn ends first, the transcript is
        # flagged for cleaning before the next send() reuses it.
        _glitch_pending = False

        # ── Output degeneration + no-progress watch (core.output_degeneration) ──
        # One detector per streamed content block, keyed by (parent_tool_use_id,
        # index): a sub-agent's block can share an index with a main-thread one.
        # Fed by text_delta AND input_json_delta (a tool input can loop too). A
        # hit ends the turn through the user-Stop path (_abort_on_degeneration).
        # ProgressWatch only produces notices for invisible-thinking / silent
        # stalls — it never stops the turn.
        from .output_degeneration import DegenerationDetector, ProgressWatch

        _degen: dict[tuple, DegenerationDetector] = {}
        _progress = ProgressWatch(now=time.time())

        # ── Permission queue watcher ──────────────────────────────
        # The dangerous_command_gate hook blocks inside PreToolUse
        # awaiting a user decision.  While it blocks, the SDK cannot
        # produce new messages.  We race the SDK iterator against the
        # PermissionManager session queue so we can surface the
        # cmd_permission_request to the frontend via SSE.
        from core.permission_manager import permission_manager as _pm
        perm_queue = _pm.get_session_queue(self._parent.session_id)

        response_iter = self._parent._client.receive_response().__aiter__()
        _STREAM_EXHAUSTED = object()  # Sentinel: iterator is done
        # tool_use_id → list[raw_path] WRITTEN by that tool (Write/Edit/NotebookEdit
        # file_path + Bash `>`/tee/cp/mv-DEST). Emits operation=written at the ToolResult
        # (success) point — the EVENT-DRIVEN live-surfacing emit (run_cce6f4b9), which
        # rides the WRITING session's own stream (so it is stamped with the correct
        # session → tab-isolation correct by construction, unlike the deleted global sweep).
        _pending_file_changes: dict[str, list[str]] = {}
        # tool_use_id → list[raw_path] DELETED by that tool (Bash rm / mv-SRC). Emits
        # operation=deleted at the ToolResult point so the rail drops stale rows (G1,
        # run_5a7be540). Separate from _pending_file_changes because one Bash command
        # can both write and delete.
        _pending_file_deletes: dict[str, list[str]] = {}
        # tool_use_id → (candidate raw paths, tool_use start time) for a Bash
        # interpreter heredoc patch (`python3 - <<'PY' … open(p,'w')`). The paths
        # are only string literals in the script, so each is emitted as written
        # ONLY if its mtime moved during the call (a read-only literal is dropped).
        _pending_heredoc_writes: dict[str, tuple[list[str], float]] = {}
        # tool_use_id → (root, start time) for a Bash call whose SCRIPT may write
        # files its command never names (`python make_deck.py`): the successful
        # result sweeps root for files written during the call (script_output_sweep)
        # and emits them through the same write gate.
        _pending_script_sweeps: dict[str, tuple[str, float]] = {}
        # tool_use_id → run_id for a Bash `run-update --status completed` command
        # (run_beff6754). Emits the finish-time Canvas surface at the ToolResult point
        # IF run.json confirms status==completed — the backend-auto replacement for the
        # agent-discipline surface_run_outputs call (which docs-only runs skipped, so
        # Canvas never auto-opened on the most common pipeline).
        _pending_completion: dict[str, str] = {}
        # tool_use_id → tool name, so a result can be summarized per tool.
        _tool_names: dict[str, str] = {}
        # tool_use_id → the Agent tool_use it ran under (sub-agent steps only).
        # Stamped on its live tool_result so the UI nests it under that step.
        _tool_parent: dict[str, str] = {}
        # SDK task_id → parent Agent tool_use id (TaskUpdatedMessage lacks one).
        _task_parent: dict[str, str] = {}
        # tool_use_id → run_id for ANY Bash `artifact_cli.py … --run-id <id>` call.
        # Its successful result re-reads run.json and yields a persisted
        # pipeline_stage block per newly finished stage (_pipeline_stage_events) —
        # stage progress shown by the backend, not left to the model writing it.
        _pending_stage_watch: dict[str, str] = {}
        # Per-TURN resolution cache (perf directive): raw_path → resolved dict|None.
        # Turn-scoped (local to this method), so no cross-session leak; keyed by the
        # raw path string. Only deliverable-candidate writes are ever resolved —
        # reads/greps never reach here, so they never pay the os.walk bare-name cost.
        _resolve_cache: dict[str, "dict | None"] = {}

        # run_cce6f4b9: the per-turn git-snapshot live-surfacing path (turn-START
        # porcelain_snapshot baseline + turn-END sweep_turn_delta) was DELETED here — it
        # was the root cause of the Canvas tab-isolation leak (global tree diff, no session
        # scope), the non-progressive trigger (turn-end batch), and the per-turn full-tree
        # scan cost. Live WRITE surfacing is now event-driven: _build_file_write_events at
        # the ToolResult(success) point below (symmetric with the delete emit). Non-parent
        # writes (sub-agent/skill/CLI/hook — SDK-sidechain-filtered) surface at PIPELINE
        # FINISH via sweep_run_changes / surface_run_outputs (the retained author-agnostic
        # fallback), an XG-accepted tradeoff for removing the per-turn regression.

        async def _next_or_sentinel():
            """Wrap __anext__ so StopAsyncIteration doesn't leak into Task.

            Python converts StopAsyncIteration inside a Task into
            RuntimeError('async generator raised StopAsyncIteration').
            Wrapping it here returns a sentinel instead, which the
            caller checks after task.result().
            """
            try:
                return await response_iter.__anext__()
            except StopAsyncIteration:
                return _STREAM_EXHAUSTED

        while True:
            current_timeout = INIT_TIMEOUT if is_first_message else MESSAGE_TIMEOUT

            # Race: SDK message vs permission request from hook
            sdk_task = asyncio.ensure_future(
                asyncio.wait_for(
                    _next_or_sentinel(),
                    timeout=current_timeout,
                )
            )
            perm_task = asyncio.ensure_future(perm_queue.get())

            try:
                # Heartbeat polling: wake every HEARTBEAT_POLL_S to surface a
                # "still working" notice during a long or stuck wait, WITHOUT
                # cancelling the in-flight sdk_task — cancelling it would abort
                # the __anext__ read mid-message and could lose/corrupt a
                # message. sdk_task and perm_task persist across ticks; only the
                # tick timer is re-armed. This is what makes a silent hang
                # visible (the heartbeat is otherwise event-driven and never
                # fires when no SDK events arrive).
                while True:
                    tick_task = asyncio.ensure_future(asyncio.sleep(HEARTBEAT_POLL_S))
                    try:
                        done, pending = await asyncio.wait(
                            [sdk_task, perm_task, tick_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except BaseException:
                        tick_task.cancel()
                        raise
                    if tick_task in done and sdk_task not in done and perm_task not in done:
                        # Neither a message nor a permission arrived this tick —
                        # surface a heartbeat (throttled internally) and keep
                        # waiting on the SAME sdk_task/perm_task.
                        _note = _progress.check(
                            time.time(), tools_open=bool(self._parent._open_tool_uses)
                        )
                        if _note is not None:
                            yield self._stall_event(_note)
                        try:
                            hb = self._parent._maybe_build_elapsed_heartbeat()
                            if hb is not None:
                                # Keep a reported stall on screen: the generic
                                # heartbeat shares the toast slot.
                                _stall = _progress.stall_note(time.time())
                                if _stall is not None:
                                    hb = {**hb, "message": _stall, "stall": True}
                                yield hb
                        except Exception as hb_exc:
                            logger.debug(
                                "streaming_orchestrator.heartbeat_failed session_id=%s: %s",
                                getattr(self._parent, "session_id", "?"),
                                f"{type(hb_exc).__name__}: {hb_exc}",
                            )
                        continue
                    # A real task finished — drop the tick timer and proceed.
                    tick_task.cancel()
                    try:
                        await tick_task
                    except asyncio.CancelledError:
                        pass  # expected: we just cancelled it (fires on every streamed event)
                    except Exception:
                        logger.debug("_read_formatted_response: tick timer raised on cancel", exc_info=True)
                    done.discard(tick_task)
                    pending.discard(tick_task)
                    break
            except BaseException:
                # Cleanup on unexpected errors — and on GeneratorExit /
                # CancelledError at a heartbeat/notice yield (consumer closed
                # the stream): an orphaned perm_queue.get() would swallow the
                # next permission request on this session's queue.
                sdk_task.cancel()
                perm_task.cancel()
                raise

            # Cancel the loser
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, StopAsyncIteration, asyncio.TimeoutError):
                    pass

            # ── Permission request OR ask_user_question won the race ──
            if perm_task in done:
                try:
                    perm_request = perm_task.result()
                except Exception:
                    # Queue.get shouldn't fail, but be safe
                    logger.debug("_read_formatted_response: suppressed exception on a fail-safe path", exc_info=True)
                    continue

                # ── AskUserQuestion item (kind-discriminated) ─────────
                # The ask_question_gate hook enqueues {kind:"ask_user_question",
                # tool_use_id, questions} on the SAME per-session queue. It blocks
                # awaiting ask_question_manager.wait_for_answer(tool_use_id). Surface
                # the question to the frontend; the answer arrives via
                # continue_with_answer → set_answer(tool_use_id) which unblocks the
                # hook (the hook then injects answers via updatedInput).
                if perm_request.get("kind") == "ask_user_question":
                    from core.ask_question_manager import ask_question_manager as _aqm
                    _tuid = perm_request.get("tool_use_id")
                    # Kind-aware drop-guard: an ask item's waiter lives in the
                    # AskQuestionManager, NOT permission_manager. A stale item whose
                    # hook was cancelled (respawn/timeout) has no live waiter — drop
                    # it so the user can't "answer into a void".
                    if not _tuid or not _aqm.has_live_waiter(_tuid):
                        logger.info(
                            "session_unit: dropping stale ask_user_question %s "
                            "(no live waiter) session_id=%s",
                            _tuid, self._parent.session_id,
                        )
                        continue
                    questions = perm_request.get("questions", [])
                    logger.info(
                        "session_unit.ask_question_surfaced session_id=%s "
                        "tool_use_id=%s questions=%d",
                        self._parent.session_id, _tuid, len(questions),
                    )
                    # Track the outstanding tool_use so the drain worker won't
                    # inject a turn while the question is open (keyed on block.id).
                    self._parent._pending_tool_use_id = _tuid
                    self._parent._pending_question = {
                        "tool_use_id": _tuid,
                        "questions": questions,
                    }
                    yield {
                        "type": "ask_user_question",
                        "toolUseId": _tuid,
                        "questions": questions,
                        "sessionId": self._parent.session_id,
                    }
                    self._parent._transition(SessionState.WAITING_INPUT)
                    self._parent.last_used = time.time()
                    return

                # The per-session queue survives respawn (keyed by session_id,
                # only dropped on session end), so a stale request whose hook was
                # cancelled can be replayed here. Surfacing it would let the user
                # "approve" into a void with no awaiting hook. Drop any request
                # with no live waiter (its wait_for_permission_decision already
                # popped the event in its finally on cancellation/timeout).
                from core.permission_manager import permission_manager as _pm_live
                _req_id = perm_request.get("requestId")
                if not _req_id or not _pm_live.has_live_waiter(_req_id):
                    logger.info(
                        "session_unit: dropping stale permission request %s "
                        "(no live waiter) session_id=%s",
                        _req_id, self._parent.session_id,
                    )
                    continue

                logger.info(
                    "session_unit.permission_surfaced session_id=%s "
                    "request_id=%s command=%s",
                    self._parent.session_id,
                    perm_request.get("requestId", "?"),
                    str(perm_request.get("toolInput", {}).get("command", ""))[:60],
                )
                # Root-1 SSOT Phase 2 (L4/F3): a permission prompt is also an
                # outstanding tool_use — track it so the drain worker won't inject
                # a turn while it's open. Keyed by requestId (the permission's id).
                self._parent._pending_tool_use_id = perm_request["requestId"]
                self._parent._pending_question = {
                    "tool_use_id": perm_request["requestId"],
                    "request_id": perm_request["requestId"],
                    "tool_name": perm_request.get("toolName", "Bash"),
                    "tool_input": perm_request.get("toolInput", {}),
                    "reason": perm_request.get("reason", ""),
                    "options": perm_request.get("options", ["approve", "deny"]),
                }
                yield {
                    "type": "cmd_permission_request",
                    "requestId": perm_request["requestId"],
                    "sessionId": perm_request.get("sessionId", self._parent.session_id),
                    "toolName": perm_request.get("toolName", "Bash"),
                    "toolInput": perm_request.get("toolInput", {}),
                    "reason": perm_request.get("reason", ""),
                    "options": perm_request.get("options", ["approve", "deny"]),
                }
                self._parent._transition(SessionState.WAITING_INPUT)
                self._parent.last_used = time.time()
                return

            # ── SDK message won the race ──────────────────────────
            try:
                message = sdk_task.result()
                if message is _STREAM_EXHAUSTED:
                    break
                is_first_message = False
            except asyncio.TimeoutError:
                phase = "init" if is_first_message else "streaming"
                # ── Tool-FREE CPU-liveness gate (run_dcd668a6) ─────────
                # The wall-clock timeout fired, but that alone does NOT prove a
                # hang: a long tool-free inference (heavy extended-thinking on
                # large context) is SDK-silent yet CPU-BUSY. Before killing,
                # probe the subprocess tree CPU. If it's WORKING, extend the
                # deadline (bounded per attempt + hard-ceilinged) by looping
                # again — the next iteration builds a FRESH sdk_task (a NEW
                # __anext__), NOT a resume of the cancelled read (the SDK
                # json_buffer lives in a DETACHED reader task, so a fresh
                # __anext__ re-parks cleanly on the memory stream). WEDGED or
                # UNKNOWN → raise as before (fail-safe: never hang forever on an
                # unmeasurable/dead process). Applies to init too — a large-
                # context cold-resume TTFT is the most common false-timeout.
                _parent = self._parent
                _pid = _parent.pid
                _start = _parent._streaming_start_time
                _elapsed = (time.time() - _start) if _start else 0.0
                _ceiling = _parent.TOOL_FREE_HARD_CEILING_S
                if (
                    _pid
                    and _parent._tool_free_extensions < _parent.TOOL_FREE_MAX_EXTENSIONS
                    and _elapsed < _ceiling
                ):
                    verdict = await _parent._tool_free_hang_verdict(_pid)
                    if verdict == "working":
                        _parent._tool_free_extensions += 1
                        logger.warning(
                            "session_unit.%s_timeout_extended session_id=%s — "
                            "no SDK message for %.0fs but subprocess CPU-busy "
                            "(extension %d/%d, elapsed=%.0fs/%.0fs) — extending, "
                            "NOT killing",
                            phase, _parent.session_id, current_timeout,
                            _parent._tool_free_extensions,
                            _parent.TOOL_FREE_MAX_EXTENSIONS, _elapsed, _ceiling,
                        )
                        # Reset the liveness clocks so the watchdog backstop
                        # doesn't immediately kill on the same stale silence.
                        _parent._last_event_time = time.time()
                        continue  # fresh sdk_task next iteration (new __anext__)
                logger.error(
                    "session_unit.%s_timeout session_id=%s — "
                    "no SDK message for %.0fs (resume=%s), breaking stream",
                    phase, self._parent.session_id, current_timeout, is_resume,
                )
                raise RuntimeError(
                    f"Streaming timeout ({phase}): no SDK response for "
                    f"{current_timeout:.0f}s (session_id={self._parent.session_id}, "
                    f"resume={is_resume})"
                )

            # ── Heartbeat: track liveness for diagnostics ──────────
            # Reset BOTH liveness timers on every SDK event:
            #  - _last_event_time → pid_watchdog output-liveness check
            #  - health_sensor.record_activity() → HealthSensor hang_detected
            # Without the record_activity() call, the hang timer only advanced
            # on per-tool record_turn(), so an active stream (tokens/thinking)
            # or a single long tool call could trip the 90s hang detector and
            # trigger a spurious self-heal mid-response.
            self._parent._last_event_time = time.time()
            self._parent._health_sensor.record_activity()
            _progress.on_event(self._parent._last_event_time)

            # ── Long-turn heartbeat (Root 2 / AC5) ─────────────────
            # If this turn has been running a long time, surface a "still
            # working" notice so the FE reads it as expected, not a hang.
            # Event-driven (fires on the next SDK event after the threshold);
            # one notice per interval. Never written to the system prompt.
            try:
                hb = self._parent._maybe_build_elapsed_heartbeat()
                if hb is not None:
                    _stall = _progress.stall_note(time.time())
                    if _stall is not None:
                        hb = {**hb, "message": _stall, "stall": True}
                    yield hb
            except Exception as hb_exc:
                # Best-effort — never break the stream — but log so a
                # persistently-throwing heartbeat (e.g. an attribute rename) is
                # visible rather than silently swallowed for hours.
                logger.debug(
                    "streaming_orchestrator.heartbeat_failed session_id=%s: %s",
                    getattr(self._parent, "session_id", "?"),
                    f"{type(hb_exc).__name__}: {hb_exc}",
                )

            # Capture SDK session ID from init message
            if hasattr(message, "session_id") and message.session_id:
                self._parent._sdk_session_id = message.session_id

            # ── SystemMessage: session init metadata ──────────────
            if isinstance(message, SystemMessage):
                if message.subtype == "thinking_tokens":
                    # The only progress signal of an invisible-thinking turn.
                    _data = getattr(message, "data", None) or {}
                    _progress.on_thinking_tokens(
                        time.time(), _data.get("estimated_tokens_delta") or 0
                    )
                    _note = _progress.check(
                        time.time(), tools_open=bool(self._parent._open_tool_uses)
                    )
                    if _note is not None:
                        yield self._stall_event(_note)
                    continue
                if message.subtype == "init":
                    self._parent._sdk_session_id = message.data.get("session_id")
                    yield {
                        "type": "session_start",
                        "sessionId": self._parent.session_id,
                    }
                elif message.subtype in ("status", "compact_boundary"):
                    for _cev in self._compaction_events(
                        message.subtype, getattr(message, "data", None)
                    ):
                        yield _cev
                else:
                    # A sub-agent's lifecycle / progress: real work the user must see.
                    _sa_ev = self._subagent_progress_event(message, _task_parent)
                    if _sa_ev is not None:
                        self._parent._last_progress_time = time.time()
                        _progress.on_visible(self._parent._last_progress_time)
                        yield _sa_ev
                continue  # Don't forward other system messages

            # ── StreamEvent: token-by-token streaming ─────────────
            if isinstance(message, StreamEvent):
                event_data = message.event
                event_type = event_data.get("type", "")
                _dkey = (getattr(message, "parent_tool_use_id", None), event_data.get("index", 0))
                if event_type == "content_block_delta":
                    delta = event_data.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        self._parent._content_emitted = True
                        self._parent._last_progress_time = time.time()  # real content
                        _progress.on_visible(self._parent._last_progress_time)
                        _idx = event_data.get("index", 0)
                        _dtext = delta["text"]
                        _det = _degen.get(_dkey)
                        if _det is None:
                            _det = _degen[_dkey] = DegenerationDetector(kind="text")
                        if _det.feed(_dtext):
                            for _ev in await self._abort_on_degeneration(
                                _det, _dkey[0], response_iter
                            ):
                                yield _ev
                            return
                        if _dkey[0] is not None:
                            # A sub-agent's text is not the main reply: the frontend
                            # appends every text_delta to the main answer, and the
                            # leak buffers below are keyed by index alone.
                            continue
                        if _idx in _delta_leak_hidden:
                            continue  # rest of a leaked tool call — never shown
                        _prev = _delta_text_buf.get(_idx, "")
                        _buf = _prev + _dtext
                        _delta_text_buf[_idx] = _buf
                        # Only run the onset check when "‹invoke" can have just
                        # completed (cheap gate on the hot per-token path).
                        if "<invoke" in _buf[-(len(_dtext) + 7):]:
                            from .session_utils import find_stream_leak_onset
                            _onset = find_stream_leak_onset(_buf)
                            if _onset != -1:
                                _delta_leak_hidden.add(_idx)
                                _dtext = _dtext[: max(0, _onset - len(_prev))]
                                if not _dtext:
                                    continue
                        yield {"type": "text_delta", "text": _dtext, "index": _idx}
                    elif delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                        # A tool input streaming in is visible progress, and it
                        # can loop like text (e.g. a Write whose content repeats).
                        _progress.on_visible(time.time())
                        _det = _degen.get(_dkey)
                        if _det is None:
                            _det = _degen[_dkey] = DegenerationDetector(kind="tool_use")
                        if _det.feed(delta["partial_json"]):
                            for _ev in await self._abort_on_degeneration(
                                _det, _dkey[0], response_iter
                            ):
                                yield _ev
                            return
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        self._parent._last_progress_time = time.time()  # real thinking
                        _progress.on_visible(self._parent._last_progress_time)  # shown to the user
                        if _dkey[0] is None:  # a sub-agent's thinking is not the main turn's
                            yield {"type": "thinking_delta", "thinking": delta["thinking"], "index": event_data.get("index", 0)}
                elif event_type == "content_block_start":
                    block = event_data.get("content_block", {})
                    # Fresh detector for EVERY block start (an index is reused by
                    # the next message's blocks, whatever their type).
                    _degen[_dkey] = DegenerationDetector(
                        kind=block.get("type", ""), name=block.get("name", "") or "",
                    )
                    if _dkey[0] is not None:
                        continue  # sub-agent block boundaries: nothing to show
                    if block.get("type") == "thinking":
                        yield {"type": "thinking_start", "index": event_data.get("index", 0)}
                    elif block.get("type") == "text":
                        # New text block at this index — fresh leak-hiding state.
                        _delta_text_buf.pop(event_data.get("index", 0), None)
                        _delta_leak_hidden.discard(event_data.get("index", 0))
                        yield {"type": "text_start", "index": event_data.get("index", 0)}
                elif event_type == "content_block_stop":
                    _degen.pop(_dkey, None)  # block done — release its text
                    if _dkey[0] is None:
                        yield {"type": "content_block_stop", "index": event_data.get("index", 0)}
                continue

            # ── UserMessage: tool_result blocks (Anthropic protocol) ─
            # Per the Anthropic protocol a tool_result is carried by a role=user
            # turn, so the parent's own Edit/Write/Bash results AND sub-agent
            # (Agent) results both arrive HERE via UserMessage (the AssistantMessage
            # ToolResultBlock branch handles server/advisor tool results, not these
            # client-tool executions). Two disjoint jobs on this branch:
            #   (a) sub-agent cleanup — pop _active_agent_tools/_open_tool_uses so
            #       count/timer/label don't freeze (via _clear_completed_sub_agents).
            #   (b) file DELETE emit — a Bash rm/mv-SRC result lands here; emit
            #       operation=deleted so the rail drops the stale row. (run_4de279ca:
            #       the WRITE emit that used to live here is GONE — the turn-end git
            #       sweep is now the sole write-surfacing authority, author-agnostic.
            #       Deletes still need this explicit emit because a deleted path is
            #       skipped by the sweep, cf. run_5a7be540.)
            if isinstance(message, UserMessage):
                self._parent._last_progress_time = time.time()  # sub-agent progress
                _progress.on_visible(self._parent._last_progress_time)  # tool results
                self._clear_completed_sub_agents(message)
                # (b) write + delete emit (run_cce6f4b9). List-guard mirrors
                # _clear_completed_sub_agents (string content carries no blocks). Parent
                # AND sub-agent tool results arrive here as UserMessage ToolResultBlocks;
                # emitting on success stamps the WRITING session (correct tab isolation).
                _um_content = getattr(message, "content", None)
                # (c) Live tool result → UI. The CLI's tool results arrive ONLY
                # here, and were never forwarded: every step stayed a spinner
                # until the turn ended and showed no outcome. A sub-agent's result
                # carries `parent_tool_use_id` (the Agent step it nests under).
                # Its own SSE type, NOT "assistant": the router persists only a
                # compact {tool_use_id, is_error, outcome} of it, never the content.
                if isinstance(_um_content, list):
                    _tr_blocks = []
                    for _blk in _um_content:
                        if isinstance(_blk, ToolResultBlock) and _blk.tool_use_id in _tool_names:
                            _tr = self._tool_result_block(_blk, _tool_names.pop(_blk.tool_use_id))
                            _parent_id = _tool_parent.pop(_blk.tool_use_id, None)
                            if _parent_id:
                                _tr["parent_tool_use_id"] = _parent_id
                            _tr_blocks.append(_tr)
                    if _tr_blocks:
                        yield {"type": "tool_result", "content": _tr_blocks}
                if isinstance(_um_content, list):
                    for _blk in _um_content:
                        if not isinstance(_blk, ToolResultBlock):
                            continue
                        _tuid = getattr(_blk, "tool_use_id", None)
                        if not getattr(_blk, "is_error", False):
                            if _tuid in _pending_file_changes:
                                _um_writes = _pending_file_changes.pop(_tuid, None)
                                if _um_writes:
                                    _wr_events = await self._build_file_write_events(
                                        _um_writes, _resolve_cache
                                    )
                                    for _ev in _wr_events:
                                        yield _ev
                            if _tuid in _pending_heredoc_writes:
                                _hd_paths, _hd_t0 = _pending_heredoc_writes.pop(_tuid)
                                _hd_events = await self._build_file_write_events(
                                    _hd_paths, _resolve_cache, modified_since=_hd_t0
                                )
                                for _ev in _hd_events:
                                    yield _ev
                            if _tuid in _pending_script_sweeps:
                                for _ev in await self._script_output_events(
                                    _pending_script_sweeps.pop(_tuid), _resolve_cache
                                ):
                                    yield _ev
                            if _tuid in _pending_file_deletes:
                                _um_dels = _pending_file_deletes.pop(_tuid, None)
                                if _um_dels:
                                    _del_events = await self._build_file_delete_events(
                                        _um_dels, _resolve_cache
                                    )
                                    for _ev in _del_events:
                                        yield _ev
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) A successful `run-update --status completed`
                            # tool_result → confirm run.json ACTUALLY reached completed
                            # (a gate-BLOCKED completion still exits 0, so the command
                            # string is not proof), then fire build_surface_events so the
                            # REPORT.md kind=knowledge event auto-opens Canvas WITHOUT the
                            # agent calling surface_run_outputs (which docs-only runs skip).
                            # Idempotent vs an explicit surface (per-session run-id set);
                            # fully fail-safe (any error → no surface, turn survives).
                            if _tuid in _pending_completion:
                                _um_rid = _pending_completion.pop(_tuid, None)
                                try:
                                    _um_seen = self._auto_surfaced_run_ids()
                                    if _um_rid and _um_rid not in _um_seen:
                                        _um_status = await asyncio.to_thread(
                                            read_run_status, _um_rid
                                        )
                                        if _um_status == "completed":
                                            await ensure_report_for_run(_um_rid)
                                            # glob+read+stat — off-loop entry point
                                            # keeps the hot streaming turn free.
                                            _um_surf = await build_surface_events_async(_um_rid)
                                            for _ev in _um_surf:
                                                yield _ev
                                            _um_seen.add(_um_rid)
                                        else:
                                            # Observability (Gate-2 meta-review): a
                                            # completion COMMAND whose run.json is not
                                            # actually 'completed' (gate BLOCKED it — CLI
                                            # still exited 0) drops the surface. debug, not
                                            # warning: a non-completed run-update is normal
                                            # + frequent, only useful when chasing "Canvas
                                            # didn't open after my pipeline finished".
                                            logger.debug(
                                                "auto-surface skipped: run %r status=%r "
                                                "(not 'completed')", _um_rid, _um_status,
                                            )
                                except Exception as _e:  # noqa: BLE001 — hot-path fail-safe
                                    logger.warning(
                                        "auto-surface on completion failed for %r: %s",
                                        _um_rid, _e,
                                    )
                            # ── Pipeline stage rows (successful artifact_cli call) ──
                            if _tuid in _pending_stage_watch:
                                _um_stage_rid = _pse.resolve_watch(
                                    _pending_stage_watch.pop(_tuid), getattr(_blk, "content", None))
                                if _um_stage_rid:
                                    for _ev in await self._pipeline_stage_events(_um_stage_rid):
                                        yield _ev
                        else:
                            # FAILED tool result: discard any pending write/delete/completion
                            # tracking so it can never surface later (symmetric with the
                            # AssistantMessage ToolResult branch). Turn-local dicts, so this
                            # is hygiene, not a cross-turn leak — but the two branches MUST
                            # behave identically.
                            _pending_file_changes.pop(_tuid, None)
                            _pending_file_deletes.pop(_tuid, None)
                            _pending_heredoc_writes.pop(_tuid, None)
                            _pending_script_sweeps.pop(_tuid, None)
                            _pending_completion.pop(_tuid, None)
                            _pending_stage_watch.pop(_tuid, None)
                # ── Glitch prefix in history → clean + resume at this boundary ──
                # Tool results just came back, so the model is about to write
                # its next response from a history that still holds the glitch
                # word (copied by ~31% of later calls). With nothing open, hand
                # off to send()'s one-shot restart: kill, clean the transcript,
                # --resume, and continue the turn. Same guards and budget as the
                # leak restart (primary stream only, once per user message).
                if _glitch_pending and self._should_restart_for_glitch():
                    from .session_utils import (
                        LEAK_RESTART_KIND_PREFIX,
                        LEAK_RESTART_MARKER,
                    )

                    self._parent._leak_restart_eligible = False
                    logger.warning(
                        "session_unit.tool_call_glitch_restart session_id=%s — "
                        "glitch prefix in history; cleaning transcript + resume",
                        self._parent.session_id,
                    )
                    raise RuntimeError(
                        f"{LEAK_RESTART_MARKER} {LEAK_RESTART_KIND_PREFIX} "
                        f"(session_id={self._parent.session_id})"
                    )
                continue

            # ── AssistantMessage: full content blocks ─────────────
            if isinstance(message, AssistantMessage):
                saw_assistant_message = True
                # Per-request usage of the MAIN agent (a sub-agent's usage is its
                # own context) → the real current context size for the warning.
                _am_usage = getattr(message, "usage", None)
                if _am_usage and getattr(message, "parent_tool_use_id", None) is None:
                    self._parent._last_call_usage = _am_usage
                    # Publish it now, not at turn end: the context ring follows the
                    # backend snapshot request by request (deduped per API call).
                    if isinstance(_am_usage, dict):
                        try:
                            from .prompt_builder import PromptBuilder
                            _ctx_evt = self._parent._record_context_usage(
                                PromptBuilder.sum_usage_input_tokens(_am_usage),
                                basis="last_call",
                            )
                        except Exception:  # noqa: BLE001 — metadata must never break the stream
                            logger.debug("context usage record failed", exc_info=True)
                            _ctx_evt = None
                        if _ctx_evt is not None:
                            yield _ctx_evt
                self._parent._last_progress_time = time.time()  # real content blocks
                if any(isinstance(b, (TextBlock, ToolUseBlock)) for b in message.content):
                    _progress.on_visible(self._parent._last_progress_time)
                content_blocks = []
                _msg_leaked = False
                # A sub-agent's message (the SDK forwards its tool calls with
                # parent_tool_use_id = the Agent step): its tool_use blocks are
                # stamped so the UI nests them under that step; its text/thinking
                # (only with forward_subagent_text) is not the main reply.
                _am_parent = getattr(message, "parent_tool_use_id", None)
                for block in message.content:
                    if _am_parent is not None and isinstance(block, (TextBlock, ThinkingBlock)):
                        continue
                    if isinstance(block, TextBlock):
                        # ── Tool-call XML leak guard (strip, never terminate) ──
                        # The model occasionally emits tool-call SYNTAX
                        # (‹invoke name=...>‹parameter ...>) as RAW plain text
                        # instead of a real tool_use block. The SDK hands it over
                        # as a TextBlock. Disposition (XG-directed, 去容灾化):
                        # STRIP the raw leak text (best-effort pollution
                        # reduction) and KEEP the block — the turn yields to its
                        # natural completion and the MODEL decides what to do
                        # next. We do NOT kill the subprocess and do NOT raise to
                        # force a --resume respawn: termination authority belongs
                        # to the model, and kill+respawn only upgraded a quiet
                        # half-answer into an interrupted+rerun (the pain this
                        # removes — STEERING #2 "容灾不能冒充正常逻辑"; a model
                        # bug is not survivable-by-restart, it is cleaned in place
                        # and left to the model). Pollution never persists
                        # regardless: `_clean_blocks_for_persist` (the assistant
                        # persist door), `session_pending` (the user persist door)
                        # and `context_injector` (the resume read side) each strip
                        # UNCONDITIONALLY, independent of this detect — and with a
                        # WIDER anchor that also catches the truncated form this
                        # detector misses. This strip is a best-effort live-stream
                        # clean on top of those durable layers.
                        #
                        # Strip removes the leak WITH its one-word opener line
                        # and no placeholder (nothing of a failed call is shown).
                        # `_leak_tail` marks it: if the turn ends right here the
                        # ResultMessage branch continues the conversation in the
                        # same warm process instead of dead-ending.
                        #
                        # One path for BOTH shapes (pure-leak AND leak alongside a
                        # real tool_use) — the former `_has_real_tool_use` fork was
                        # removed with the kill()+raise it gated: strip + keep is
                        # correct for both.
                        from .session_utils import (
                            detect_glitch_prefix,
                            detect_tool_call_leak,
                            strip_glitch_prefix,
                            strip_tool_call_leak,
                        )

                        if detect_tool_call_leak(block.text):
                            cleaned_text = strip_glitch_prefix(
                                strip_tool_call_leak(block.text)
                            )
                            logger.warning(
                                "session_unit.tool_call_leak_stripped "
                                "session_id=%s — tool-call XML in text channel; "
                                "stripped from persistence (block_len=%d→%d), "
                                "turn preserved (no kill/respawn — 去容灾化)",
                                self._parent.session_id,
                                len(block.text), len(cleaned_text),
                            )
                            # Always yield the (possibly empty) cleaned block: the
                            # frontend only drops its provisional streamed text
                            # when the assistant event carries a text block.
                            content_blocks.append(
                                {"type": "text", "text": cleaned_text}
                            )
                            _leak_tail = True
                            if not _msg_leaked:
                                _msg_leaked = True
                                self._count_leak()
                            continue
                        if block.text.strip():
                            _leak_tail = False  # model wrote real prose after it
                        _text = block.text
                        # ── Glitch-prefix shape (same glitch, second shape) ──
                        # A corrupted opener word left at the start of a
                        # narration line. Strip it from what is shown/persisted
                        # (always yield the block so the frontend replaces its
                        # provisional streamed text). Only main-thread text arms
                        # the restart: a sub-agent's text lives in its own
                        # sidechain transcript, which a parent restart can't clean.
                        if detect_glitch_prefix(_text):
                            _text = strip_glitch_prefix(_text)
                            if not getattr(message, "parent_tool_use_id", None):
                                _glitch_pending = True
                            logger.warning(
                                "session_unit.tool_call_glitch_prefix_stripped "
                                "session_id=%s block_len=%d→%d",
                                self._parent.session_id, len(block.text), len(_text),
                            )
                        content_blocks.append({"type": "text", "text": _text})
                    elif isinstance(block, ThinkingBlock):
                        # Skip content-free thinking blocks. Bedrock CAN emit
                        # thinking blocks with empty/whitespace content under
                        # certain conditions (signature-only, redacted reasoning)
                        # — persisting them pollutes the DB and renders ghost
                        # widgets. NOTE: this is conditional, NOT universal.
                        # Verified 2026-06-01 (v1.17.5, claude-opus-4-8): under
                        # adaptive thinking the common case is FULL plaintext
                        # (529 non-empty deltas vs 7 empty in one turn; 12/12
                        # blocks had 51-985 chars of content). The empty-block
                        # path is the rare exception, not the rule.
                        # See: Knowledge/Notes/2026-06-01-thinking-block-7layer-diagnosis.md
                        if block.thinking and block.thinking.strip():
                            content_blocks.append({
                                "type": "thinking",
                                "thinking": block.thinking,
                                # Preserve signature — required to replay thinking
                                # to the API on any future multi-turn reconstruction.
                                "signature": getattr(block, "signature", ""),
                            })
                        else:
                            # The model DID respond (it produced a thinking block,
                            # just with redacted/empty content). Mark content as
                            # emitted so zombie-detection (streaming_dur<2s +
                            # not _content_emitted → kill+retry) and empty-result
                            # guards don't false-fire on a legitimate Opus 4.8
                            # turn whose only block is empty thinking. Skipping the
                            # block must not also remove the proof that the LLM
                            # answered.
                            self._parent._content_emitted = True
                    elif isinstance(block, ToolUseBlock):
                        # A real tool call = the model is acting again; any
                        # earlier leak in this response was recovered.
                        _leak_tail = False
                        _leak_continuations = 0
                        # ── Track EVERY open tool_use for hang detection (run_fb6e94a9) ──
                        # Records emission time; cleared when the matching
                        # ToolResultBlock arrives. The PID watchdog reads this
                        # to tell a stuck tool from genuine thinking. Skip
                        # AskUserQuestion — it intentionally blocks on the user
                        # (the hook owns its lifecycle) and would false-trip.
                        if block.name != "AskUserQuestion":
                            # Store (start_time, tool_name) so the watchdog can
                            # apply a per-tool-type open window (run_fb6e94a9):
                            # Agent/Bash legitimately run long, so they get a
                            # longer window before the CPU-liveness probe.
                            self._parent._open_tool_uses[block.id] = (
                                time.time(), block.name,
                            )
                        # ── Track sub-agent (Agent tool) for progress observability ──
                        if block.name == "Agent" and isinstance(block.input, dict):
                            _agent_label = block.input.get("description") or block.input.get("prompt") or ""
                            self._parent._active_agent_tools[block.id] = {
                                "label": _agent_label[:80],
                                "start_time": time.time(),
                            }
                        # ── File-change tracking (run_cce6f4b9: event-driven emit) ──
                        # Track WRITE + DELETE targets keyed by tool_use_id; the matching
                        # ToolResult(success) emits file_changed at that point (mid-stream,
                        # on the writing session's own stream → correct session stamp / tab
                        # isolation). This RESTORES the per-tool emit that run_4de279ca
                        # replaced with a per-turn global git sweep (the regression root
                        # cause). Resolution + verdict happen at the ToolResult point (a
                        # failed tool never surfaces; a read/grep never pays resolution).
                        #   (a) Write/Edit/NotebookEdit → block.input.file_path
                        #   (b) Bash `>`/tee/cp/mv-DEST  → parse_bash_write_targets
                        #   (b') Bash rm/mv-SRC          → parse_bash_delete_targets
                        # A single Bash command may BOTH write and delete (e.g. `mv A B`):
                        # separate dicts, both keyed by the same block.id.
                        if block.name in ("Edit", "Write", "NotebookEdit") and isinstance(block.input, dict):
                            _fp = block.input.get("file_path", "")
                            if _fp:
                                _pending_file_changes[block.id] = [_fp]
                        elif block.name == "Bash" and isinstance(block.input, dict):
                            from core.file_change_classifier import (
                                parse_bash_heredoc_write_candidates,
                                parse_bash_write_targets,
                                parse_bash_delete_targets,
                            )
                            _cmd = block.input.get("command", "")
                            _write_targets = parse_bash_write_targets(_cmd)
                            if _write_targets:
                                _pending_file_changes[block.id] = _write_targets
                            _hd_cands = [
                                p for p in parse_bash_heredoc_write_candidates(_cmd)
                                if p not in _write_targets
                            ]
                            if _hd_cands:
                                _pending_heredoc_writes[block.id] = (_hd_cands, time.time())
                            from core.script_output_sweep import sweep_root as _sweep_root
                            _sw_root = _sweep_root(_cmd, getattr(self._parent, "_cwd", None))
                            if _sw_root:
                                _pending_script_sweeps[block.id] = (_sw_root, time.time())
                            # G1 (run_5a7be540): a Bash rm/mv-SRC deletes files → track
                            # them so the ToolResult(success) emits operation=deleted and
                            # the rail drops the stale row.
                            _del_targets = parse_bash_delete_targets(_cmd)
                            if _del_targets:
                                _pending_file_deletes[block.id] = _del_targets
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) If this Bash command is a `run-update
                            # --status completed`, register its run_id; the matching
                            # SUCCESSFUL ToolResult confirms run.json status and fires
                            # build_surface_events — so the REPORT.md kind=knowledge
                            # event auto-opens Canvas WITHOUT the agent having to call
                            # surface_run_outputs (which docs-only runs skip). This is
                            # only a PRE-FILTER; run.json is the authority at emit time.
                            _completion_run_id = parse_completion_run_id(_cmd)
                            if _completion_run_id:
                                _pending_completion[block.id] = _completion_run_id
                            # ── Pipeline stage rows: watch every artifact_cli call that
                            # names a run; its successful result yields the stages it
                            # finished (see _pipeline_stage_events). Seed the baseline
                            # now, normally before the call writes run.json.
                            _stage_run_id = _pse.parse_watched_run_id(_cmd)
                            if _stage_run_id:
                                _pending_stage_watch[block.id] = _stage_run_id
                                await self._watch_pipeline_stage_call(_stage_run_id)
                            elif _pse.is_run_create(_cmd):
                                # No id until the CLI mints it: resolved from the call's
                                # output; no baseline to seed (the run does not exist yet).
                                _pending_stage_watch[block.id] = _pse.RUN_CREATE_WATCH
                        # ── UI-action (ACT): agent drives its own UI (Run 2) ──
                        # The agent calls the ui_action tool; we observe it here,
                        # validate cmd against the fail-closed allowlist, and yield
                        # an ADDITIVE ui_command SSE event (the SDK still delivers the
                        # tool's normal result to the agent — this does not replace
                        # it). Mirrors the file_changed emit. The frontend derives its
                        # own event+target from cmd; a non-allowlisted cmd yields None
                        # here → nothing emitted (fail-closed at the source).
                        if block.name == UI_ACTION_FULL_TOOL_NAME and isinstance(block.input, dict):
                            # allow_abs: absolute host paths may reach the resolver
                            # ONLY on a genuine local-desktop session. _has_channel_context
                            # is True for ANY channel (owner-over-channel included), so
                            # `not _has_channel_context` == true local desktop. NOT
                            # is_channel_session (that is False for owner-over-channel →
                            # would leak abs paths into the owner's remote stream, C041).
                            # getattr default True → if the flag were ever absent, we
                            # treat it as "has channel context" → allow_abs False →
                            # fail CLOSED (reject abs). The attr is always init'd in
                            # SessionUnit.__init__, so this is belt-and-suspenders.
                            _has_chan = getattr(self._parent, "_has_channel_context", True)
                            _ui_ev = build_ui_command_event(
                                block.input.get("cmd"),
                                block.input.get("path"),
                                allow_abs=not _has_chan,
                            )
                            if _ui_ev is not None:
                                yield _ui_ev
                            # fall through: do NOT `continue` — the SDK runs the tool
                            # and returns its ack result to the agent normally.
                        # ── Finish-time PR-review batch (run_b8ea6d5c, CHANNEL A) ──
                        # The agent calls surface_run_outputs(run_id) at pipeline
                        # COMPLETE; we observe it here (same pattern as ui_action) and
                        # yield N additive file_changed events — one per committed
                        # source file, kind=source-final — so N persistent OUTPUTS rows
                        # appear at once. The SDK still delivers the tool's ack. This is
                        # the ONLY way N rows reach the rail (run-commit CLI can't emit).
                        if block.name == SURFACE_OUTPUTS_FULL_TOOL_NAME and isinstance(block.input, dict):
                            # ORDERING ROOT-FIX (run_f1fbf37d): guarantee the run's
                            # REPORT.md exists BEFORE we build the batch. The report
                            # is produced by a SEPARATE agent step (run-report); if
                            # surface_run_outputs is called before it, the report row
                            # is silently dropped. The consumer now ensures its own
                            # input — regenerate a missing/stub report in-process
                            # (off-loop, fail-safe: never raises) so build_surface_events
                            # (still PURE) finds it and emits the report row.
                            _sf_run_id = block.input.get("run_id")
                            # Idempotency (run_beff6754): surface a run AT MOST ONCE per
                            # session regardless of which path fires. SYMMETRIC guard —
                            # the completion-observation branch checks-before-emit, so
                            # this branch must too, else a completion that surfaced FIRST
                            # (backend-auto) followed by an explicit surface_run_outputs
                            # would double-emit (Gate-2 correctness finding). In the
                            # common flow (explicit surface precedes completion) the set
                            # is empty here, so this still emits normally.
                            _sf_seen = self._auto_surfaced_run_ids()
                            _sf_already = isinstance(_sf_run_id, str) and _sf_run_id in _sf_seen
                            if not _sf_already:
                                await ensure_report_for_run(_sf_run_id)
                                # off-loop entry point (glob+read+stat) — keeps the
                                # shared daemon loop (+/health) free mid-streaming-turn.
                                _sf_events = await build_surface_events_async(_sf_run_id)
                                for _sf_ev in _sf_events:
                                    yield _sf_ev
                                if isinstance(_sf_run_id, str) and _sf_run_id:
                                    _sf_seen.add(_sf_run_id)
                            # fall through: SDK runs the tool, returns its ack normally.
                        if block.name == "AskUserQuestion":
                            # The ask_question_gate PreToolUse hook intercepts this
                            # tool call BEFORE the CLI self-resolves it: it enqueues
                            # {kind:"ask_user_question", tool_use_id, questions} on the
                            # per-session queue and BLOCKS on wait_for_answer. The
                            # perm_task branch above surfaces the question SSE and
                            # transitions to WAITING_INPUT; continue_with_answer →
                            # set_answer unblocks the hook, which injects answers via
                            # updatedInput. So here we only SKIP the tool_use block —
                            # do NOT render it as a content widget, do NOT self-resolve
                            # or drain (the old headless self-resolution bug). The hook
                            # owns the lifecycle; the stream continues normally.
                            continue
                        if _has_tool_summarizer:
                            summary = summarize_tool_use(block.name, block.input)
                            category = get_tool_category(block.name)
                        else:
                            summary = f"{block.name}(...)"
                            category = "unknown"
                        _tu_block = {
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "summary": summary, "category": category,
                        }
                        if _am_parent is not None:
                            _tu_block["parent_tool_use_id"] = _am_parent
                            _tool_parent[block.id] = _am_parent
                        content_blocks.append(_tu_block)
                        _tool_names[block.id] = block.name
                        # ── Record tool call for compaction guard ──
                        self._parent._compaction_guard.record_tool_call(
                            block.name, block.input
                        )
                        level = self._parent._compaction_guard.check()
                        if level != EscalationLevel.MONITORING:
                            guard_event = self._parent._compaction_guard.build_guard_event(level)
                            if guard_event:
                                yield guard_event
                            if level in (
                                EscalationLevel.HARD_WARN,
                                EscalationLevel.KILL,
                            ):
                                # Flush accumulated content blocks before
                                # interrupting — otherwise text/tool_use blocks
                                # from earlier in this AssistantMessage are lost.
                                if content_blocks:
                                    yield {
                                        "type": "assistant",
                                        "content": content_blocks,
                                        "model": getattr(message, "model", None),
                                    }
                                logger.warning(
                                    "compaction_guard.interrupt "
                                    "session_id=%s action=%s",
                                    self._parent.session_id, level.value,
                                )
                                await self._parent.interrupt()
                                return
                    elif isinstance(block, ToolResultBlock):
                        # ── Clear sub-agent progress when Agent tool completes ──
                        self._parent._active_agent_tools.pop(block.tool_use_id, None)
                        # ── Clear open-tool tracking; the tool produced a result ──
                        # (run_fb6e94a9). A completed tool resets the once-per-
                        # episode interrupt guard so a LATER stuck tool in the
                        # same turn can still be escaped.
                        self._parent._open_tool_uses.pop(block.tool_use_id, None)
                        self._parent._tool_hang_interrupted = False
                        self._parent._tool_hang_interrupt_at = None
                        # Recovery + progress → reset escalation counter (v2 MED).
                        self._parent._tool_hang_episodes = 0
                        block_content = str(block.content) if block.content else ""
                        if _has_tool_summarizer:
                            truncated, was_truncated = truncate_tool_result(block_content)
                        else:
                            truncated = block_content[:2000]
                            was_truncated = len(block_content) > 2000
                        content_blocks.append({
                            "type": "tool_result", "tool_use_id": block.tool_use_id,
                            "content": truncated, "is_error": getattr(block, "is_error", False),
                            "truncated": was_truncated,
                        })
                        # ── Emit file WRITE + DELETE event(s) (run_cce6f4b9) ──
                        # Event-driven live surfacing: a Write/Edit/Bash tool result here
                        # emits operation=written (restored per-tool emit, replacing the
                        # deleted per-turn git sweep); a rm/mv-SRC result emits
                        # operation=deleted (drops the stale rail row — the sweep can't see
                        # a gone file). Both fire only on a SUCCESSFUL result and are
                        # stamped by the writing session (correct tab isolation).
                        _fc_events: list[dict] = []
                        if not getattr(block, "is_error", False):
                            _written_paths = _pending_file_changes.pop(block.tool_use_id, None)
                            if _written_paths:
                                _fc_events += await self._build_file_write_events(
                                    _written_paths, _resolve_cache
                                )
                            _deleted_paths = _pending_file_deletes.pop(block.tool_use_id, None)
                            if _deleted_paths:
                                _fc_events += await self._build_file_delete_events(
                                    _deleted_paths, _resolve_cache
                                )
                            _sweep = _pending_script_sweeps.pop(block.tool_use_id, None)
                            if _sweep:
                                _fc_events += await self._script_output_events(_sweep, _resolve_cache)
                            # ── Backend-auto Canvas surface on pipeline COMPLETE ──
                            # (run_beff6754) A successful `run-update --status completed`
                            # tool_result: confirm run.json ACTUALLY reached completed
                            # (a gate-BLOCKED completion still exits 0 — the command
                            # string is not proof), then fire build_surface_events so the
                            # REPORT.md kind=knowledge event auto-opens Canvas. Idempotent
                            # vs an explicit surface_run_outputs (per-session run-id set).
                            # Fully fail-safe: any error degrades to prior behavior (no
                            # surface) and never breaks the streaming turn.
                            _completion_rid = _pending_completion.pop(block.tool_use_id, None)
                            if _completion_rid:
                                try:
                                    _seen = self._auto_surfaced_run_ids()
                                    if _completion_rid not in _seen:
                                        _status = await asyncio.to_thread(
                                            read_run_status, _completion_rid
                                        )
                                        if _status == "completed":
                                            await ensure_report_for_run(_completion_rid)
                                            # off-loop entry point (glob+read)
                                            _fc_events += await build_surface_events_async(
                                                _completion_rid
                                            )
                                            _seen.add(_completion_rid)
                                except Exception as _e:  # noqa: BLE001 — hot-path fail-safe
                                    logger.warning(
                                        "auto-surface on completion failed for %r: %s",
                                        _completion_rid, _e,
                                    )
                            # ── Pipeline stage rows (successful artifact_cli call) ──
                            # Queued AFTER the content_blocks flush below, so the
                            # stage row lands after the tool_result it came from.
                            _stage_rid = _pse.resolve_watch(
                                _pending_stage_watch.pop(block.tool_use_id, None), block.content)
                            if _stage_rid:
                                _fc_events += await self._pipeline_stage_events(_stage_rid)
                        else:
                            # a FAILED tool result: discard any pending tracking (never
                            # surface a write/delete/completion/stage watch that errored)
                            # so it can't leak later.
                            _pending_file_changes.pop(block.tool_use_id, None)
                            _pending_file_deletes.pop(block.tool_use_id, None)
                            _pending_completion.pop(block.tool_use_id, None)
                            _pending_stage_watch.pop(block.tool_use_id, None)
                            _pending_script_sweeps.pop(block.tool_use_id, None)
                        if _fc_events:
                            # Flush accumulated content blocks first (ordering:
                            # the tool_result must precede its file_changed).
                            if content_blocks:
                                self._parent._content_emitted = True
                                yield {
                                    "type": "assistant",
                                    "content": content_blocks,
                                    "model": getattr(message, "model", None),
                                }
                                content_blocks = []
                            for _ev in _fc_events:
                                yield _ev
                if content_blocks:
                    self._parent._content_emitted = True
                    yield {
                        "type": "assistant",
                        "content": content_blocks,
                        "model": getattr(message, "model", None),
                    }
                # ── Repeated leak → clean the transcript and restart ──
                # Leak text stays in the CLI's own history and primes more
                # leaks (6-8 per session once it starts). From the 2nd leak in
                # one SDK session, an in-process retry runs on that poisoned
                # history, so instead hand off to send()'s one-shot restart:
                # kill, strip the leak text from the transcript, --resume, and
                # continue the turn. Only when no tool is running (nothing real
                # is cut off) and only on the primary send() stream.
                if _msg_leaked and self._should_restart_for_leak(message):
                    from .session_utils import LEAK_RESTART_MARKER

                    self._parent._leak_restart_eligible = False
                    logger.warning(
                        "session_unit.tool_call_leak_restart session_id=%s "
                        "leaks_in_sdk_session=%d — cleaning transcript + resume",
                        self._parent.session_id, self._parent._leak_count,
                    )
                    raise RuntimeError(
                        f"{LEAK_RESTART_MARKER} (session_id={self._parent.session_id})"
                    )
                continue

            # ── ResultMessage — response complete or error ──────────
            if isinstance(message, ResultMessage):
                is_error = getattr(message, "is_error", False)
                subtype = getattr(message, "subtype", None)

                # ── Turn limit reached (NOT a real error) ─────────
                # CLI emits is_error=True + subtype="error_max_turns"
                # when the configured max_turns limit is hit. This is a
                # graceful pause, not an error — the agent completed its
                # last tool call successfully but the CLI won't start
                # another API roundtrip. The user can Resume to continue.
                #
                # BUG FIX (2026-06-01): Previously this fell through to
                # the error path, yielding an error event that caused the
                # frontend to show "Interrupted" and potentially clear
                # streamed content. Now we emit a distinct event type so
                # the frontend can show "Turn limit reached" and preserve
                # all previously streamed content.
                #
                # Evidence: run_bbe3f167 — pipeline hit 101 turns (CLI
                # default maxTurns=100), emitted error_max_turns, frontend
                # showed "Interrupted" and user had to manually Resume.
                if is_error and subtype == "error_max_turns":
                    self._parent._active_agent_tools = {}  # Clear stale sub-agent progress
                    num_turns = getattr(message, "num_turns", None)
                    logger.info(
                        "session_unit.turn_limit_reached session_id=%s "
                        "num_turns=%s subtype=%s",
                        self._parent.session_id, num_turns, subtype,
                    )
                    # Yield a non-error event — frontend preserves content
                    yield {
                        "type": "turn_limit_reached",
                        "num_turns": num_turns,
                        "message": (
                            "Turn limit reached — send a message to continue."
                        ),
                    }
                    # Transition to IDLE (not error) — session is healthy,
                    # user can send the next message to continue work.
                    self._parent._transition(SessionState.IDLE)
                    self._parent.last_used = time.time()
                    # CLI exited after error_max_turns (exit code 1).
                    # Clear process references so next send() knows to
                    # respawn (instead of writing to dead pipe → crash →
                    # retry). Keep _sdk_session_id intact so respawn uses
                    # --resume and preserves conversation context.
                    self._parent._client = None
                    self._parent._wrapper = None
                    # Still emit usage/metadata for this completed segment
                    self._parent._lifecycle_response_count += 1
                    usage = getattr(message, "usage", None) or {}
                    logger.info(
                        "session_unit.result_usage session_id=%s "
                        "raw_usage=%s input_tokens=%s model=%s "
                        "lifecycle_response=%d",
                        self._parent.session_id,
                        usage,
                        usage.get("input_tokens") if usage else None,
                        self._parent._model_name,
                        self._parent._lifecycle_response_count,
                    )
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "turn_limit_reached",
                        "stop_reason": "turn_limit",
                        "session_id": self._parent.session_id,
                        "duration_ms": getattr(message, "duration_ms", 0),
                        "total_cost_usd": getattr(message, "total_cost_usd", None),
                        "num_turns": num_turns,
                        "usage": {
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                        } if usage else None,
                    }
                    # Persist token usage (same as normal result path)
                    if usage:
                        try:
                            import database
                            asyncio.get_running_loop().create_task(
                                database.db.record_token_usage(
                                    session_id=self._parent.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._parent._model_name,
                                )
                            )
                        except Exception:
                            logger.debug("_read_formatted_response: suppressed exception on a fail-safe path", exc_info=True)
                            pass  # fire-and-forget — never break streaming

                    for meta_event in self._parent._emit_post_stream_metadata(
                        usage, num_turns=num_turns or 1,
                    ):
                        yield meta_event
                    return

                if is_error or subtype == "error_during_execution":
                    error_text = str(
                        getattr(message, "result", "")
                        or getattr(message, "error", "")
                    )

                    from .session_utils import (
                        _is_retriable_error,
                        _sanitize_sdk_error,
                        _build_error_event,
                    )

                    if self._parent._interrupted:
                        self._parent._interrupted = False
                        self._parent._transition(SessionState.IDLE)
                        self._parent.last_used = time.time()
                        # Still yield the error so the user sees what
                        # went wrong — silently swallowing SDK errors
                        # causes blank responses (e.g. unknown slash
                        # commands).  Only suppress if error_text is
                        # genuinely empty (pure cancellation).
                        if error_text.strip():
                            friendly, suggested = _sanitize_sdk_error(
                                error_text
                            )
                            yield _build_error_event(
                                code="SDK_ERROR",
                                message=friendly,
                                suggested_action=suggested,
                            )
                        return

                    # ── Poisoned-subprocess self-heal (Layer 2) ───────
                    # A subprocess that was previously interrupted (by the
                    # CompactionGuard ladder or a user Stop) can be left in a
                    # corrupt turn-state.  When the NEXT send() reuses it, it
                    # returns an INSTANT error_during_execution with no error
                    # detail and no streamed content (≈ms after query).  This is
                    # the SAME failure as the zombie detector below (stream ended
                    # instantly after interrupt) but shaped as an error
                    # ResultMessage instead of an empty stream — so it bypassed
                    # that guard, was treated as non-retriable (empty text), and
                    # the dead subprocess was reused on every retry, producing
                    # the "response stops half-way / must send several times"
                    # loop.  Route it into the SAME kill + --resume respawn: a
                    # fresh subprocess loads the conversation from disk, not the
                    # poisoned in-memory state.  Guarded tightly (empty text +
                    # no content + <2s) so a genuine mid-generation error — which
                    # has real text or arrives after streamed content — is still
                    # surfaced, never silently retried.
                    streaming_dur = (
                        time.time() - self._parent._streaming_start_time
                        if self._parent._streaming_start_time else 0.0
                    )
                    if (
                        subtype == "error_during_execution"
                        and not error_text.strip()
                        and not self._parent._content_emitted
                        and streaming_dur < 2.0
                    ):
                        logger.warning(
                            "session_unit.zombie_via_error session_id=%s "
                            "duration=%.3fs subtype=%s error_text=empty "
                            "content_emitted=False — killing for --resume respawn",
                            self._parent.session_id, streaming_dur, subtype,
                        )
                        await self._parent.kill()
                        # Same retriable signal as the empty-stream zombie path
                        # (matches the r"Zombie subprocess detected" pattern in
                        # _is_retriable_error) → send() respawns with --resume.
                        raise RuntimeError(
                            f"Zombie subprocess detected: error_during_execution "
                            f"with no content in {streaming_dur:.1f}s "
                            f"(session_id={self._parent.session_id})"
                        )

                    if _is_retriable_error(error_text):
                        raise RuntimeError(f"Retriable SDK error: {error_text}")

                    # Non-retriable error — yield error event and RETURN.
                    # BUG FIX (2026-06-14): Previously this had no return
                    # statement, causing fall-through to the normal result
                    # path below.  Result: (1) backend log showed normal
                    # streaming→idle with result_usage, zero error signal;
                    # (2) frontend received BOTH an error event AND a
                    # normal result event (double-delivery); (3) the
                    # output_tokens=0 empty-response guard (line ~2621)
                    # was bypassed because it checks `not is_error`.
                    # Evidence: session e2c335b9 2026-06-14 16:57-17:08,
                    # two turns with 392 and 0 output tokens showed as
                    # normal completions in logs.
                    logger.warning(
                        "session_unit.sdk_error session_id=%s is_error=%s "
                        "subtype=%s error_text=%.200s",
                        self._parent.session_id, is_error, subtype, error_text,
                    )
                    friendly, suggested = _sanitize_sdk_error(error_text)
                    # terminal=True: this branch is reached ONLY for a NON-retriable
                    # error — the retriable path raised at `if _is_retriable_error(...)`
                    # above, and the zombie path raised before that. A permanent error
                    # (e.g. 400 geo-block) will reproduce identically on re-send, so the
                    # pending-drain finalize must NOT roll the message back to pending
                    # (that re-drives it every IDLE edge forever, and poison_guard
                    # recycles once PER re-drive — the 764x-recycle incident, run_29959bcd).
                    # SCOPE: this cuts the drained-pending RE-DRIVE limb (the 764x driver).
                    # It does NOT change _last_turn_clean, so poison_guard's own design
                    # behavior — recycle-before-reuse ONCE on the next send to a session
                    # whose last turn ended non-clean — is intentionally unchanged (that is
                    # a single bounded recycle, not the unbounded loop this fix targets).
                    # The event is still yielded to the user below, so the error is
                    # surfaced, never silently dropped.
                    yield _build_error_event(
                        code="SDK_ERROR", message=friendly, suggested_action=suggested,
                        terminal=True,
                    )
                    # Clear stale sub-agent progress (matches
                    # turn_limit_reached and interrupt paths).
                    self._parent._active_agent_tools = {}
                    # Transition to IDLE — session is not broken, just this
                    # turn failed.  User can retry.  Matches the interrupted
                    # path (line ~2484) which also transitions to IDLE.
                    self._parent._transition(SessionState.IDLE)
                    self._parent.last_used = time.time()
                    # If CLI subprocess died (the error it returned may have
                    # been its dying gasp), clear client refs so next send()
                    # respawns instead of writing to a dead pipe.
                    # _sdk_session_id is preserved for --resume on respawn.
                    if self._parent.pid:
                        try:
                            os.kill(self._parent.pid, 0)  # signal 0 = liveness check
                        except (ProcessLookupError, OSError):
                            # Process is dead — clear refs
                            self._parent._client = None
                            self._parent._wrapper = None
                    # Still track usage for cost accounting (even failed
                    # turns consume input tokens for the prompt).
                    self._parent._lifecycle_response_count += 1
                    usage = getattr(message, "usage", None) or {}
                    logger.info(
                        "session_unit.result_usage session_id=%s "
                        "raw_usage=%s input_tokens=%s model=%s "
                        "lifecycle_response=%d (ERROR path)",
                        self._parent.session_id,
                        usage,
                        usage.get("input_tokens") if usage else None,
                        self._parent._model_name,
                        self._parent._lifecycle_response_count,
                    )
                    # Yield result event so frontend knows the turn ended
                    yield {
                        "type": "result",
                        "subtype": "sdk_error",
                        "stop_reason": "error",
                        "session_id": self._parent.session_id,
                        "duration_ms": getattr(message, "duration_ms", 0),
                        "total_cost_usd": getattr(message, "total_cost_usd", None),
                        "num_turns": getattr(message, "num_turns", 1),
                        "usage": {
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                        } if usage else None,
                    }
                    # Persist token usage (fire-and-forget)
                    if usage:
                        try:
                            import database
                            asyncio.get_running_loop().create_task(
                                database.db.record_token_usage(
                                    session_id=self._parent.session_id,
                                    source="cli",
                                    input_tokens=usage.get("input_tokens") or 0,
                                    output_tokens=usage.get("output_tokens") or 0,
                                    cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                    cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                    cost_usd=getattr(message, "total_cost_usd", None),
                                    model=self._parent._model_name,
                                )
                            )
                        except Exception:
                            logger.debug("_read_formatted_response: suppressed exception on a fail-safe path", exc_info=True)
                            pass  # fire-and-forget
                    # Emit context metadata so frontend updates context
                    # ring/bar — especially important for timeout errors
                    # that correlate with large contexts.
                    for meta_event in self._parent._emit_post_stream_metadata(
                        usage, num_turns=getattr(message, "num_turns", 1) or 1,
                    ):
                        yield meta_event
                    return

                # Yield result event with usage metrics
                self._parent._lifecycle_response_count += 1
                usage = getattr(message, "usage", None) or {}
                logger.info(
                    "session_unit.result_usage session_id=%s "
                    "raw_usage=%s input_tokens=%s model=%s "
                    "lifecycle_response=%d",
                    self._parent.session_id,
                    usage,
                    usage.get("input_tokens") if usage else None,
                    self._parent._model_name,
                    self._parent._lifecycle_response_count,
                )

                # ── Observability: cache miss detection ───────────
                # A cache miss (cache_read=0) with large context means
                # full prompt was re-sent to Bedrock — latency spike
                # risk and potential timeout trigger.  Log for
                # post-mortem diagnosis.
                cache_read = usage.get("cache_read_input_tokens") or 0
                cache_create = usage.get("cache_creation_input_tokens") or 0
                if cache_read == 0 and cache_create > 50_000:
                    logger.info(
                        "session_unit.cache_miss session_id=%s "
                        "cache_creation=%d cache_read=0 — full prompt "
                        "sent (latency risk)",
                        self._parent.session_id, cache_create,
                    )

                stop_reason = getattr(message, "stop_reason", None) or ""
                subtype = getattr(message, "subtype", "") or ""

                # Log stop_reason for observability — especially important
                # for detecting max_tokens truncation (model forced to stop
                # mid-sentence). Distinct from end_turn (model chose to stop).
                if stop_reason and stop_reason != "end_turn":
                    logger.warning(
                        "session_unit.non_standard_stop session_id=%s "
                        "stop_reason=%s subtype=%s output_tokens=%s "
                        "content_emitted=%s",
                        self._parent.session_id,
                        stop_reason,
                        subtype,
                        (usage.get("output_tokens") or 0) if usage else 0,
                        self._parent._content_emitted,
                    )

                # ── Retry guards — MUST run BEFORE yielding `result` ──
                # A `result` event is the definitive turn-end signal for the
                # frontend (it stops the spinner, marks the tab idle, clears
                # pending state). If we yield it and THEN raise-for-retry, the
                # frontend finalizes the turn while the backend respawns and
                # keeps streaming — a torn state: the retry's `session_start`
                # arrives in idle mode (reducer no-op, no setIsStreaming(true))
                # so the retried content streams into a tab the UI thinks is
                # done. Raising HERE means a blank/degraded turn never reaches
                # the frontend as a turn-end; send() respawns and the retry's
                # session_start lands while still STREAMING → UI stays armed.
                streaming_dur = (
                    time.time() - self._parent._streaming_start_time
                    if self._parent._streaming_start_time else None
                )
                output_tok = (usage.get("output_tokens") or 0) if usage else 0

                # (a) Post-interrupt corruption: warm-but-broken subprocess
                # returns an empty ResultMessage instantly (<2s, no content).
                # See: 2026-03-22 12:36:08 instant idle after interrupt.
                if (
                    streaming_dur is not None
                    and streaming_dur < 2.0
                    and not self._parent._content_emitted
                    and not is_error
                    and saw_assistant_message  # Only degraded if LLM tried to respond
                ):
                    logger.warning(
                        "session_unit.empty_result_detected "
                        "session_id=%s duration=%.3fs — subprocess "
                        "degraded after interrupt, killing for respawn",
                        self._parent.session_id, streaming_dur,
                    )
                    await self._parent.kill()
                    raise RuntimeError(
                        f"Empty result from degraded subprocess: "
                        f"stream ended in {streaming_dur:.1f}s with no "
                        f"content (session_id={self._parent.session_id})"
                    )

                # (b) API empty / blank-success no-op at any duration:
                # Bedrock 429/503/timeout (empty subtype) OR a subtype="success"
                # envelope with 0 output tokens and no content — the live
                # blank-turn bug (session 2e87b27f 2026-06-26 17:43). Raising
                # triggers the send() retry loop (string matched by
                # _is_retriable_error). Predicate shared with the regression
                # test via _is_blank_api_result so the two can never drift.
                if _is_blank_api_result(
                    content_emitted=self._parent._content_emitted,
                    is_error=is_error,
                    interrupted=self._parent._interrupted,
                    output_tokens=output_tok,
                    subtype=subtype,
                ):
                    logger.warning(
                        "session_unit.api_empty_response session_id=%s "
                        "duration=%.1fs output_tokens=0 subtype='%s' — "
                        "raising for retry (before result yield)",
                        self._parent.session_id,
                        streaming_dur or 0,
                        subtype,
                    )
                    raise RuntimeError(
                        f"API returned empty response (output_tokens=0, "
                        f"duration={(streaming_dur or 0):.1f}s) — likely "
                        f"transient 429/503/timeout "
                        f"(session_id={self._parent.session_id})"
                    )

                # ── Leak dead-end → continue on the SAME warm subprocess ──
                # The turn ended (stop_reason=end_turn) right after the model
                # wrote its tool call as text, so the action never ran and the
                # turn would stop with nothing done. The CLI already self-heals
                # the stop_reason=tool_use shape in-process ("Your tool call was
                # malformed…" → real tool_use); it misses this one. Do the same
                # here: no kill, no respawn, no --resume, and no `result` to the
                # frontend — just send a short corrective message into the live
                # conversation and keep reading the same stream, so the user
                # sees the action run. Bounded by _MAX_LEAK_CONTINUATIONS
                # consecutive tries without a real tool_use (loop guard only —
                # it never cuts off real work). A user Stop is respected.
                if (
                    _leak_tail
                    and _leak_continuations < self._MAX_LEAK_CONTINUATIONS
                    and not self._parent._interrupted
                    and not getattr(self._parent, "_user_stopped_current_turn", False)
                    and self._parent._client is not None
                ):
                    _leak_continuations += 1
                    _leak_tail = False
                    logger.warning(
                        "session_unit.tool_call_leak_continue session_id=%s "
                        "attempt=%d/%d stop_reason=%s — turn ended on a "
                        "text-channel tool call; continuing in-process",
                        self._parent.session_id, _leak_continuations,
                        self._MAX_LEAK_CONTINUATIONS, stop_reason,
                    )
                    self._record_token_usage_bg(message, usage)
                    await self._parent._client.query(self._LEAK_CONTINUATION_PROMPT)
                    response_iter = self._parent._client.receive_response().__aiter__()
                    is_first_message = True
                    continue
                if _leak_tail:
                    logger.warning(
                        "session_unit.tool_call_leak_unrecovered session_id=%s "
                        "continuations=%d — ending turn",
                        self._parent.session_id, _leak_continuations,
                    )

                # Glitch prefix seen but no safe mid-turn boundary came (or the
                # restart budget was used): the transcript still holds it. Flag
                # it so the next send() cleans it before the CLI reads it again.
                if _glitch_pending:
                    self._parent._transcript_poisoned = True
                    logger.warning(
                        "session_unit.tool_call_glitch_deferred_clean session_id=%s "
                        "— transcript flagged for cleaning before the next send",
                        self._parent.session_id,
                    )

                # run_cce6f4b9: the TURN-END git sweep (sweep_turn_delta) was DELETED —
                # live WRITE surfacing is now the event-driven per-tool emit above
                # (_build_file_write_events at each ToolResult), which is session-correct
                # and progressive. Non-parent writes (sub-agent/skill/CLI/hook) surface at
                # PIPELINE FINISH via sweep_run_changes / surface_run_outputs. Nothing to
                # emit here anymore — go straight to the `result` yield.

                yield {
                    "type": "result",
                    "subtype": subtype,
                    "stop_reason": stop_reason,
                    "session_id": self._parent.session_id,
                    "duration_ms": getattr(message, "duration_ms", 0),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "num_turns": getattr(message, "num_turns", 1),
                    "usage": {
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    } if usage else None,
                }

                # ── Persist token usage (fire-and-forget) ─────────
                if usage:
                    try:
                        import database
                        asyncio.get_running_loop().create_task(
                            database.db.record_token_usage(
                                session_id=self._parent.session_id,
                                source="cli",
                                input_tokens=usage.get("input_tokens") or 0,
                                output_tokens=usage.get("output_tokens") or 0,
                                cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                                cache_create_tokens=usage.get("cache_creation_input_tokens") or 0,
                                cost_usd=getattr(message, "total_cost_usd", None),
                                model=self._parent._model_name,
                            )
                        )
                    except Exception:
                        logger.debug("_read_formatted_response: suppressed exception on a fail-safe path", exc_info=True)
                        pass  # fire-and-forget — never break streaming

                # ── Context usage & metadata bridge ────────────────
                result_num_turns = getattr(message, "num_turns", 1) or 1
                for meta_event in self._parent._emit_post_stream_metadata(
                    usage, num_turns=result_num_turns,
                ):
                    yield meta_event

                # ── Health sensor: record turn metrics ─────────────
                # Duration and fresh RSS feed the self-healing system.
                # Uses get_process_rss_mb() for real-time sampling instead
                # of _peak_tree_rss_bytes (which only updates every 60s via
                # LifecycleManager). Falls back to peak if sampling returns 0.
                turn_duration = getattr(message, "duration_ms", 0) or 0
                fresh_rss = get_process_rss_mb(self._parent.pid) if self._parent.pid else 0
                turn_rss = fresh_rss or (self._parent._peak_tree_rss_bytes // (1024 * 1024))
                self._parent._health_sensor.record_turn(
                    latency_ms=float(turn_duration),
                    rss_mb=turn_rss,
                    had_error=False,
                )

                # ── MCP health check (first response only) ────────
                if not self._parent._mcp_health_checked and self._parent._configured_mcps:
                    try:
                        mcp_warning = await self._parent._check_mcp_health()
                        if mcp_warning:
                            yield mcp_warning
                    except Exception as mcp_exc:
                        logger.debug(
                            "session_unit.mcp_health_check_error "
                            "session_id=%s: %s",
                            self._parent.session_id, mcp_exc,
                        )

                # ── Post-interrupt corruption & blank-result retry guards ──
                # MOVED earlier — these now run BEFORE the `result` yield above
                # (a blank/degraded turn must never reach the frontend as a
                # turn-end, or the retry streams into a UI that thinks it's
                # done). See the "Retry guards" block above the yield.

                self._parent._transition(SessionState.IDLE)
                self._parent.last_used = time.time()
                self._parent._retry_count = 0
                # Resume-poison guard: this is the ONE clean-completion point —
                # a real ResultMessage reached the user after the blank/degraded
                # retry guards above did NOT fire. Bless the subprocess so the
                # next send() may reuse it warm (fast path). Every other turn end
                # (interrupt / disconnect / error / max_turns) leaves the flag
                # False (set on STREAMING entry), forcing a recycle-before-reuse.
                self._parent._last_turn_clean = True

                # ── Proactive RSS check (Trigger B: post-turn) ────
                # Now in IDLE — check if process tree RSS is too high.
                # If so, compact → kill → lazy resume on next send().
                try:
                    await self._parent._check_rss_and_proactive_restart()
                except Exception as rss_exc:
                    logger.debug(
                        "session_unit.post_turn_rss_check failed "
                        "(non-fatal): %s", rss_exc,
                    )

                # ── Proactive soft-compaction REMOVED (run_2b1957f8) ──
                # A post-turn LLM /compact at 60% held _client_io for up to
                # 300s, blocking the user's next send() at the turn boundary
                # ("streaming freezes at 60%"). Context is now managed by the
                # CLI's built-in autocompact (autoCompactWindow, 75% of the
                # window — prompt_builder) + manual refresh — neither holds our
                # _client_io lock, so no turn-boundary freeze.

                return

        # Stream ended without a result message.
        if self._parent.state == SessionState.STREAMING:
            # ── Zombie detection ──────────────────────────────────
            # If the stream ended very fast (< 2s) with no content,
            # the subprocess is likely dead (e.g. corrupted after
            # interrupt).  Kill it so the caller's retry logic can
            # respawn a fresh process with --resume.
            streaming_dur = (
                time.time() - self._parent._streaming_start_time
                if self._parent._streaming_start_time else 0.0
            )
            if streaming_dur < 2.0 and not self._parent._content_emitted:
                logger.warning(
                    "session_unit.zombie_detected session_id=%s "
                    "duration=%.3fs content_emitted=False — killing "
                    "subprocess for respawn",
                    self._parent.session_id, streaming_dur,
                )
                await self._parent.kill()
                raise RuntimeError(
                    f"Zombie subprocess detected: stream ended in "
                    f"{streaming_dur:.1f}s with no content "
                    f"(session_id={self._parent.session_id})"
                )

            self._parent._transition(SessionState.IDLE)
            self._parent.last_used = time.time()

    # ── SSE disconnect recovery ─────────────────────────────────────


    # ═══════════════════════════════════════════════════════════════
    # End of migrated methods
    # ═══════════════════════════════════════════════════════════════
