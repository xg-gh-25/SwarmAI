"""Retry Manager — owns retry, OOM recovery, and buffer overflow logic.

Extracted from session_unit.py as part of the strangler-fig refactoring.
This module contains retry/recovery orchestration (~530 LOC):
- _retry_with_resume(): exponential backoff retry with --resume
- _handle_buffer_overflow(): 10MB JSONRPC buffer overflow recovery
- _inject_abandon_continuation(): context injection on retry timeout

Architecture:
- RetryManager holds a parent reference to SessionUnit
- All SessionUnit state accessed via self._parent.X
- Module-level session_unit globals accessed via lazy import

Design doc: Knowledge/Designs/2026-06-18-session-unit-strangler-fig-extraction-design.md
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

if TYPE_CHECKING:
    from .session_unit import SessionUnit

logger = logging.getLogger(__name__)

# Pessimistic peer count used for the spawn_budget concurrent-peak penalty when
# the live session count cannot be read (registry unavailable). Must be > 0 so a
# retry storm with a broken registry still gets OOM-penalized rather than each
# session resuming penalty-free (REVIEW 5.2, run_6ea35431). Sized to the historic
# tab ceiling so the penalty matches a realistically-full machine.
_PESSIMISTIC_ALIVE_FALLBACK = 4


class RetryManager:
    """Owns retry, OOM recovery, and buffer overflow logic.

    Architecture:
    - Holds a reference to the parent SessionUnit
    - All SessionUnit state accessed via self._parent.X
    - Module-level vars (_spawn_lock, _oom_cooldown_until) accessed via
      lazy import of session_unit module to avoid circular imports

    Entry points:
    - retry_with_resume(): called by SessionUnit.send() on retriable errors
    - handle_buffer_overflow(): called by send() on 10MB overflow

    Internal:
    - _inject_abandon_continuation(): enriches retry query on timeout-abandon
    """

    def __init__(self, parent: "SessionUnit") -> None:
        self._parent = parent

    async def _handle_buffer_overflow(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        config: Optional[Any],
        error_str: str,
    ) -> AsyncIterator[dict]:
        """Recover from CLI 10MB JSONRPC buffer overflow.

        Respawns with ``--resume`` and injects a progressive-processing
        instruction so the agent fetches items one-at-a-time.

        Yields stream events on success, or an error event + ``_abort``
        sentinel on spawn failure.  Yields ``_recovered: True`` as final
        event on success so the caller knows to return.

        Does NOT increment ``_retry_count`` — buffer overflow is strategy
        correction, not a transient failure.
        """
        from .session_utils import (
            _build_error_event,
            _sanitize_sdk_error,
        )
        from .session_unit import SessionState

        logger.warning(
            "session_unit.buffer_overflow session_id=%s — "
            "will inject progressive processing recovery",
            self._parent.session_id,
        )
        self._parent._buffer_overflow_recovery = True
        resume_sid = self._parent._sdk_session_id
        await self._parent._crash_to_cold_async()
        # No fixed sleep needed — _crash_to_cold_async() calls _force_kill()
        # which polls for process exit before returning.

        retry_options = self._parent._build_retry_options(options, resume_sid)
        try:
            await self._parent._spawn(retry_options, config)
        except Exception as spawn_exc:
            # Capture traceback immediately — awaits in async generators
            # can clear sys.exc_info() before format_exc() runs.
            spawn_tb = traceback.format_exc()
            await self._parent._crash_to_cold_async(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(str(spawn_exc))
            yield _build_error_event(
                code="SPAWN_FAILED",
                message=friendly,
                detail=spawn_tb,
                suggested_action=suggested,
            )
            yield {"_abort": True}
            return

        self._parent._transition(SessionState.STREAMING)

        # Build recovered query with progressive-processing instruction
        recovery_prefix = (
            "[System: Your previous tool call returned a response "
            "exceeding the 10MB buffer limit. Use progressive "
            "processing for this task:\n"
            "- Fetch items ONE at a time (never batch multiple "
            "files/images in a single tool call)\n"
            "- After each fetch, extract key findings as compact text\n"
            "- After all items processed, synthesize your findings\n"
            "- For large text files, use offset/limit to read in "
            "chunks of 500 lines\n"
            "- If you already processed some items before the error, "
            "continue where you left off — do not re-fetch items "
            "you already analyzed\n"
            "Do not attempt to fetch all items in a single tool "
            "call again.]\n\n"
        )
        if isinstance(query_content, str):
            recovered_query = recovery_prefix + query_content
        elif isinstance(query_content, list):
            recovered_query = [
                {"type": "text", "text": recovery_prefix},
                *query_content,
            ]
        else:
            recovered_query = recovery_prefix + str(query_content)

        try:
            async for event in self._parent._streaming_orchestrator.stream_query(recovered_query):
                yield event
            yield {"_recovered": True}
        except Exception as recovery_exc:
            # Recovery failed — propagate the NEW exception details back
            # to send() so the retry check uses the recovery error, not
            # the original "maximum buffer size" string.
            logger.warning(
                "Buffer overflow recovery failed for session %s: %s",
                self._parent.session_id, str(recovery_exc)[:200],
            )
            yield {
                "_fallthrough_error": str(recovery_exc),
                "_fallthrough_tb": traceback.format_exc(),
            }

    async def _handle_tool_call_leak(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        config: Optional[Any],
        error_str: str,
    ) -> AsyncIterator[dict]:
        """Recover from a tool-call leak with ONE corrective --resume (run_37008f2d).

        A "leak" = the model emitted tool-call syntax as plain text instead of a
        real tool_use; the orchestrator detected it, dropped the block, killed the
        subprocess, and raised a retriable RuntimeError. The OLD path sent that to
        ``_retry_with_resume`` which did a BARE --resume — replaying the SDK's
        poisoned transcript verbatim, so the model re-leaked from the same priming
        (log e9d7c08d: two consecutive leaks on one resume id). This handler is the
        bounded replacement: inject a DESCRIPTIVE correction into the next query,
        then --resume ONCE.

        HONEST framing (Gate-1 SSA): --resume restores the SDK's own conversation
        history, which still contains the intact poisoned assistant turn; the
        correction below is a NEW user message appended AFTER it. So this REDUCES
        re-leak probability (gives the model an explicit nudge), it does NOT
        PREVENT it (we cannot elide the poisoned turn — the SDK --resume exposes no
        such hook). The hard bound is in send()'s dispatcher: a 2nd consecutive
        leak does NOT come back here — it goes to a clean terminal.

        Mirrors ``_handle_buffer_overflow``: set the recovery flag FIRST (so a
        re-leak during this stream is recognized as the 2nd leak), crash-to-cold
        keeping the sdk_session_id, respawn, inject, stream. Does NOT increment
        ``_retry_count`` — this is a strategy correction, not a transient retry.
        """
        from .session_utils import (
            _build_error_event,
            _sanitize_sdk_error,
        )
        from .session_unit import SessionState

        logger.warning(
            "session_unit.tool_call_leak_recovery session_id=%s — "
            "injecting one corrective-resume (model emitted tool-call as text)",
            self._parent.session_id,
        )
        # Set the flag BEFORE crash/spawn/stream (mirror _handle_buffer_overflow:88).
        # A re-leak that propagates back to send() during this recovery stream MUST
        # see the flag True so the dispatcher routes it to the clean terminal, not
        # back into this handler — otherwise the bound is lost (Gate-1 check #1/#3).
        self._parent._tool_call_leak_recovery = True
        resume_sid = self._parent._sdk_session_id
        await self._parent._crash_to_cold_async()  # keep identity → --resume

        retry_options = self._parent._build_retry_options(options, resume_sid)
        try:
            await self._parent._spawn(retry_options, config)
        except Exception as spawn_exc:
            spawn_tb = traceback.format_exc()
            await self._parent._crash_to_cold_async(clear_identity=True)
            friendly, suggested = _sanitize_sdk_error(str(spawn_exc))
            yield _build_error_event(
                code="SPAWN_FAILED",
                message=friendly,
                detail=spawn_tb,
                suggested_action=suggested,
            )
            yield {"_abort": True}
            return

        self._parent._transition(SessionState.STREAMING)

        # Purely DESCRIPTIVE correction (AC2): it MUST NOT contain tool-call leak
        # syntax, or it would re-pollute the very context that caused the leak (the
        # root cause is literal tool-call syntax in-context). Describe the corrective
        # action in prose — do not show the syntax.
        recovery_prefix = (
            "[System: Your previous response emitted a tool call as plain text "
            "instead of an actual tool invocation, so it did not execute. Re-issue "
            "that tool call as a REAL structured tool use (use the proper tool-call "
            "mechanism, not text). Do not write tool-call markup as message text.]\n\n"
        )
        if isinstance(query_content, str):
            recovered_query = recovery_prefix + query_content
        elif isinstance(query_content, list):
            recovered_query = [
                {"type": "text", "text": recovery_prefix},
                *query_content,
            ]
        else:
            recovered_query = recovery_prefix + str(query_content)

        try:
            async for event in self._parent._streaming_orchestrator.stream_query(recovered_query):
                yield event
            yield {"_recovered": True}
        except Exception as recovery_exc:
            # Recovery stream raised. Propagate so send()'s except re-evaluates —
            # if it RE-LEAKED, the dispatcher sees the flag (now True) and routes
            # to the clean terminal; a non-leak error flows to the normal retry.
            logger.warning(
                "Tool-call leak recovery failed for session %s: %s",
                self._parent.session_id, str(recovery_exc)[:200],
            )
            yield {
                "_fallthrough_error": str(recovery_exc),
                "_fallthrough_tb": traceback.format_exc(),
            }

    async def _retry_with_resume(
        self,
        query_content: Any,
        options: ClaudeAgentOptions,
        config: Optional[Any],
        initial_error_str: str,
        initial_tb_str: str,
    ) -> AsyncIterator[dict]:
        """Retry loop with failure-aware backoff and ``--resume``.

        Handles failure-type-aware backoff (OOM → exponential 30/60/120s,
        rate limit → wait for reset, else → exponential), global OOM
        cooldown to prevent parallel retry storms, spawn budget re-check
        after backoff, and ``--resume`` flag for conversation context
        restoration.

        Yields stream events on success.  Yields error event + ``_abort``
        sentinel when all retries are exhausted or resources denied.

        On success, the generator returns normally (caller should also
        return to exit ``send()``).  The ``_retry_count`` is managed
        here and reset to 0 in ``_read_formatted_response`` on success.
        """
        from .session_utils import (
            FailureType,
            _build_error_event,
            _is_retriable_error,
            _sanitize_sdk_error,
            classify_failure,
            compute_backoff,
        )

        # Lazy imports to access module-level globals without circular import
        import core.session_unit as session_unit_mod
        from core.session_unit import SessionState

        error_str = initial_error_str
        # Capture SDK session ID before cleanup for --resume
        resume_session_id = self._parent._sdk_session_id
        _consecutive_timeouts = 0
        # Once-only guard *within this retry loop invocation*: prevents
        # duplicate continuation injection across multiple retry iterations
        # after the abandon transition (Property 3).  Resets naturally on
        # each new send() call since _retry_with_resume is re-entered fresh.
        _continuation_injected = False

        _tb_str = initial_tb_str or ""
        while (
            _is_retriable_error(error_str, _tb_str)
            and self._parent._retry_count < self._parent.MAX_RETRY_ATTEMPTS
        ):
            self._parent._retry_count += 1

            # ── Structured failure classification ─────────────
            # Consume the recycle-kill marker: it applies ONLY to this kill's
            # error. A fast recycle's -9 → ZOMBIE (~0.5s respawn); a subsequent
            # failure on the respawn is classified on its own merits.
            _recycle = self._parent._recycle_kill_pending
            self._parent._recycle_kill_pending = False
            failure_type, failure_meta = classify_failure(
                error_str, self._parent._hook_session_context,
                recycle_kill=_recycle,
            )
            self._parent._last_error_type = failure_type.value

            # ── Fix 3: Per-session OOM counter (persists across send()) ──
            if failure_type == FailureType.OOM:
                self._parent._consecutive_oom_kills += 1

                # OOM cooldown is handled by session_unit_mod._oom_cooldown_until (global,
                # module-level in session_unit). spawn_budget checks memory
                # numbers only — no duplicate cooldown in resource_monitor.

                # ── Fix 5: Notify frontend about OOM ─────────────
                yield {
                    "type": "status",
                    "message": (
                        f"Memory pressure detected — the AI process was killed by the system "
                        f"(attempt {self._parent._consecutive_oom_kills}). "
                        f"Close unused tabs or apps to free memory."
                    ),
                    "code": "OOM_DETECTED",
                }

                # Stop retrying after too many consecutive OOMs.
                # R3e (M4): the give-up DECISION (keep retrying w/ --resume vs
                # drop identity) routes through the one recovery authority
                # (GracefulEscalationPolicy). The caller owns the OOM counter +
                # limit; the Coordinator owns the ladder verdict:
                #   attempt <= limit-1 → PROCEED_KILL      (cooldown + retry, keep --resume)
                #   attempt >  limit-1 → PROCEED_KILL_HARD (give up, drop identity)
                from .session_healing import RecoveryVerdict as _RV
                _oom_decision = self._parent._recovery_coordinator.decide_graceful(
                    trigger="oom",
                    enabled=True,
                    user_stopped=self._parent._user_stopped_current_turn,
                    state=self._parent.state.value,
                    attempt=self._parent._consecutive_oom_kills,
                    threshold=self._parent._OOM_KILL_LIMIT - 1,
                    base=_RV.PROCEED_KILL,
                    escalated=_RV.PROCEED_KILL_HARD,
                )
                # NOTE on counter asymmetry vs tool-hang: the OOM counter
                # (_consecutive_oom_kills, incremented above) is INTENTIONALLY
                # NOT backed out on a SKIP/base verdict — unlike the tool-hang
                # episode counter. Memory pressure is a real physical event
                # regardless of user intent: an OOM that happened, happened, and
                # must count toward the give-up limit even if the user also
                # stopped the turn. On SKIP (user_stopped) or base PROCEED_KILL,
                # we correctly fall through to the cooldown + retry path below;
                # only PROCEED_KILL_HARD (>= limit) takes the destructive give-up.
                if _oom_decision.verdict is _RV.PROCEED_KILL_HARD:
                    logger.warning(
                        "session_unit: %d consecutive OOM kills for session %s — "
                        "giving up (system cannot sustain this session)",
                        self._parent._consecutive_oom_kills, self._parent.session_id,
                    )
                    await self._parent._crash_to_cold_async(clear_identity=True)
                    yield _build_error_event(
                        code="OOM_LIMIT_REACHED",
                        message=(
                            "The AI service keeps running out of memory. "
                            "Close other tabs and apps to free memory, "
                            "then try again."
                        ),
                        suggested_action=(
                            "Close idle chat tabs, quit memory-heavy apps "
                            "(Chrome, Slack), then send your message again."
                        ),
                    )
                    yield {"_abort": True}
                    return

                # ── Fix 2: Global OOM cooldown ────────────────────
                # Set a global cooldown so OTHER sessions also wait.
                # Protected by session_unit_mod._spawn_lock to prevent TOCTOU: two sessions
                # both reading cooldown < now, both spawning, both dying.
                cooldown_secs = min(
                    session_unit_mod._OOM_COOLDOWN_BASE * (2 ** (self._parent._consecutive_oom_kills - 1)),
                    session_unit_mod._OOM_COOLDOWN_CAP,
                )
                async with session_unit_mod._spawn_lock:
                    session_unit_mod._oom_cooldown_until = time.monotonic() + cooldown_secs
                logger.info(
                    "session_unit: global OOM cooldown set for %.0fs "
                    "(session=%s, consecutive_ooms=%d)",
                    cooldown_secs, self._parent.session_id,
                    self._parent._consecutive_oom_kills,
                )

            # Track consecutive timeouts to abandon --resume
            if failure_type == FailureType.TIMEOUT:
                _consecutive_timeouts += 1
            else:
                _consecutive_timeouts = 0

            # ── Circuit breaker: stop retrying if structurally doomed ──
            # High context + repeated timeouts = model inference time exceeds
            # our timeout cap. Retrying produces the same result every time.
            # NOTE: This check is intentionally BEFORE the abandon-continuation
            # injection below. For >1M context sessions, injecting 30K more
            # tokens is counterproductive — the session is structurally doomed
            # regardless of context preservation. The circuit breaker emits
            # CONTEXT_TOO_LARGE and stops; no injection occurs.
            context_tokens = getattr(self, "_last_known_context_tokens", 0) or 0
            if session_unit_mod.should_circuit_break_timeout(_consecutive_timeouts, context_tokens):
                logger.warning(
                    "session_unit.circuit_breaker session_id=%s "
                    "context=%d tokens, consecutive_timeouts=%d — "
                    "stopping retry (structurally doomed)",
                    self._parent.session_id, context_tokens, _consecutive_timeouts,
                )
                yield session_unit_mod.build_context_too_large_event(context_tokens, _consecutive_timeouts)
                # Exit retry loop — let session go IDLE, user sees the error
                break

            # After 2 consecutive timeouts with --resume, the resume target
            # is likely broken.  Abandon resume and start fresh.
            # Before abandoning, attempt to inject an enriched conversation
            # continuation into query_content so the blank respawn preserves
            # context (Resume-Fallback Context Preservation fix).
            if _consecutive_timeouts >= 2 and resume_session_id:
                injected = False
                if not _continuation_injected and self._parent._app_session_id:
                    query_content, injected = await self._inject_abandon_continuation(
                        query_content,
                    )
                    _continuation_injected = injected
                logger.warning(
                    "session_unit: %d consecutive timeouts with --resume, "
                    "abandoning resume for session %s (context_injected=%s)",
                    _consecutive_timeouts, self._parent.session_id, injected,
                )
                resume_session_id = None

            # Failure-type-aware backoff:
            # OOM → exponential 30/60/120s, Rate limit → wait for reset
            backoff = compute_backoff(
                failure_type, failure_meta,
                self._parent._retry_count, self._parent.RETRY_BACKOFF_SECONDS,
            )

            # ── Fix 2: Respect global OOM cooldown ────────────────
            # If another session set a cooldown, wait at least that long.
            # Read under session_unit_mod._spawn_lock to prevent TOCTOU with the write side.
            async with session_unit_mod._spawn_lock:
                now = time.monotonic()
                oom_deadline = session_unit_mod._oom_cooldown_until
            if oom_deadline > now:
                remaining_cooldown = oom_deadline - now
                if remaining_cooldown > backoff:
                    logger.info(
                        "session_unit: extending backoff %.0fs → %.0fs "
                        "(global OOM cooldown, session=%s)",
                        backoff, remaining_cooldown, self._parent.session_id,
                    )
                    backoff = remaining_cooldown

            logger.info(
                "Retry %d/%d for session %s after %.1fs backoff "
                "(resume=%s, failure=%s, meta=%s)",
                self._parent._retry_count,
                self._parent.MAX_RETRY_ATTEMPTS,
                self._parent.session_id,
                backoff,
                resume_session_id,
                failure_type.value,
                {k: v for k, v in failure_meta.items() if k != "message"},
            )

            yield {
                "type": "status",
                "message": f"Reconnecting (attempt {self._parent._retry_count}/{self._parent.MAX_RETRY_ATTEMPTS})...",
                "code": "RETRY_SPAWN",
            }

            await self._parent._crash_to_cold_async()

            # Clear hook failure context after reading
            if self._parent._hook_session_context:
                self._parent._hook_session_context.pop("_last_notification", None)
                self._parent._hook_session_context.pop("_stop_info", None)

            await asyncio.sleep(backoff)

            # Re-check spawn budget after backoff. Retries bypass
            # session_router._acquire_slot(), so we must re-enforce the OOM
            # guard here to prevent cascades from simultaneous CLI processes
            # (COE: 2026-04-12).
            # R6a: the guard is spawn_budget (real RAM), NOT compute_max_tabs
            # (a frontend UX ceiling). A crashed tab must resume whenever RAM
            # allows, regardless of how many peers are alive — refusing resume
            # on peer count was a direct context-loss source (design §9.3).
            # spawn_budget's alive_count penalty remains the COE05 floor.
            try:
                from .resource_monitor import resource_monitor

                # Count alive sessions for the spawn_budget concurrent penalty.
                # Two distinct "can't read the count" cases need OPPOSITE defaults
                # (REVIEW 5.2 + adversarial #4):
                #   • router is None → the registry isn't initialized yet
                #     (daemon startup / restart window). Peers are necessarily
                #     FEW (~0) at that point, so _alive=0 is correct. Defaulting
                #     to pessimistic-4 here triples the spawn cost and WRONGLY
                #     aborts a healthy resume on a large-RAM box during a restart.
                #   • router.alive_count RAISES → genuine mid-operation fault; we
                #     truly don't know the count, so apply the pessimistic floor
                #     so a retry storm can't collapse the COE05 penalty to
                #     penalty-free (each session resuming believing alive=0 =
                #     the 2026-04-12 cascade).
                _alive = 0
                try:
                    from . import session_registry
                    router = session_registry.session_router
                    if router is not None:
                        _alive = router.alive_count
                    # router is None → registry not up → _alive stays 0 (few peers)
                except Exception as exc:
                    _alive = _PESSIMISTIC_ALIVE_FALLBACK
                    logger.warning(
                        "Retry alive-count read failed, assuming %d peers for "
                        "OOM penalty: %s", _PESSIMISTIC_ALIVE_FALLBACK, exc,
                    )

                budget = resource_monitor.spawn_budget(alive_count=_alive)
                if not budget.can_spawn:
                    logger.warning(
                        "Retry %d aborted: %s "
                        "post-backoff session_id=%s",
                        self._parent._retry_count, budget.reason,
                        self._parent.session_id,
                    )
                    await self._parent._crash_to_cold_async(clear_identity=True)
                    yield _build_error_event(
                        code="RESOURCE_EXHAUSTED",
                        message=(
                            "Not enough memory to restart the AI service. "
                            "Close unused tabs or apps to free memory."
                        ),
                        suggested_action=(
                            "Close idle chat tabs to free memory, "
                            "then send your message again."
                        ),
                    )
                    yield {"_abort": True}
                    return
            except Exception:
                pass  # Budget check failed — proceed with retry

            retry_options = self._parent._build_retry_options(
                options, resume_session_id,
            )

            try:
                await self._parent._spawn(retry_options, config)
            except Exception as spawn_exc:
                spawn_tb = traceback.format_exc()
                error_str = str(spawn_exc)
                _tb_str = spawn_tb
                if _is_retriable_error(error_str, spawn_tb):
                    logger.warning(
                        "Retry %d spawn failed (retriable): %s",
                        self._parent._retry_count, error_str[:120],
                    )
                    continue
                else:
                    await self._parent._crash_to_cold_async(clear_identity=True)
                    friendly, suggested = _sanitize_sdk_error(error_str)
                    yield _build_error_event(
                        code="SPAWN_FAILED",
                        message=friendly,
                        detail=spawn_tb,
                        suggested_action=suggested,
                    )
                    yield {"_abort": True}
                    return

            self._parent._active_agent_tools = {}  # Clear ghost entries from crashed attempt
            self._parent._open_tool_uses = {}  # Clear stale open-tool tracking (run_fb6e94a9)
            self._parent._transition(SessionState.STREAMING)

            try:
                async for event in self._parent._streaming_orchestrator.stream_query(query_content):
                    yield event
                # Success — reset OOM counter
                self._parent._consecutive_oom_kills = 0
                return
            except Exception as retry_exc:
                error_str = str(retry_exc)
                logger.warning(
                    "Retry %d failed for session %s: %s",
                    self._parent._retry_count,
                    self._parent.session_id,
                    error_str[:200],
                )
                continue

        # All retries exhausted
        await self._parent._crash_to_cold_async(clear_identity=True)
        yield _build_error_event(
            code="ALL_RETRIES_EXHAUSTED",
            message=(
                "The AI service couldn't start after multiple attempts. "
                "This is usually temporary."
            ),
            suggested_action=(
                "Your conversation is saved. Wait a moment, "
                "then send your message again."
            ),
        )
        yield {"_abort": True}

    async def _inject_abandon_continuation(
        self,
        query_content: Any,
    ) -> tuple[Any, bool]:
        """Build enriched continuation and prepend to query_content (once).

        Called when the retry loop abandons --resume after consecutive timeouts.
        Reuses the same ``build_resume_context`` engine as Mechanism B (cold
        resume) to build a conversation summary from DB messages.

        Returns (possibly-modified query_content, injected: bool).
        Never raises — on any failure returns (query_content, False) so the
        retry loop degrades to today's blank respawn behavior.
        """
        try:
            from .context_injector import build_resume_context

            # Conservative budget: min(10% of model window, 30K tokens).
            # We're injecting into the user-turn channel, not the system
            # prompt, so keep it small to avoid hitting autocompact or
            # approaching the circuit-breaker threshold.
            model_window = 200_000  # safe default
            if self._parent._model_name:
                # Attempt to resolve actual window from model name
                try:
                    from .prompt_builder import PromptBuilder
                    model_window = PromptBuilder.get_model_context_window(
                        self._parent._model_name
                    )
                except Exception:
                    pass  # use default
            token_budget = min(int(model_window * 0.1), 30_000)

            # Timeout guard: build_resume_context does async DB reads.
            # If DB is locked/slow, we must not hang the retry loop.
            continuation = await asyncio.wait_for(
                build_resume_context(
                    self._parent._app_session_id,
                    model_context_window=model_window,
                    token_budget=token_budget,
                ),
                timeout=5.0,
            )

            if not continuation or not continuation.strip():
                logger.info(
                    "session_unit.abandon_continuation_empty session_id=%s",
                    self._parent.session_id,
                )
                return query_content, False  # no history → blank respawn (3.5)

            # Guard against double injection: if query_content already carries
            # a heal-checkpoint continuation (prepended by send() at spawn time),
            # skip — the existing context is sufficient and stacking two
            # preambles wastes tokens without adding value.
            _CONTINUATION_SEPARATOR = "\n\n---\n\n"
            if isinstance(query_content, str) and _CONTINUATION_SEPARATOR in query_content:
                logger.info(
                    "session_unit.abandon_continuation_skipped_already_enriched "
                    "session_id=%s",
                    self._parent.session_id,
                )
                return query_content, False

            logger.info(
                "session_unit.abandon_continuation_injected session_id=%s "
                "approx_tokens=%d",
                self._parent.session_id, len(continuation) // 4,
            )

            # resume-context-injection去根 (run_d108b914, AC8): wrap the continuation
            # in the SAME provenance header/footer the cold-resume query path uses,
            # so ALL resume-via-query channels frame history uniformly as background,
            # not this-turn intent (confabulation guard).
            #
            # STRANGLER-GATED by SWARM_RESUME_VIA_QUERY (Gate-2 MED, run_d108b914):
            # this abandon-continuation path fires on retry timeouts INDEPENDENTLY of
            # the flag, so wrapping unconditionally would change prod behavior while
            # the flag is OFF — breaking the "flag OFF = byte-identical" invariant.
            # Gate it so flag-OFF is a true no-op; the header lands only when the
            # cold-resume query path is also live. Guarded import (never break the
            # retry loop on an import hiccup — this path must degrade gracefully).
            import os as _os
            if _os.environ.get("SWARM_RESUME_VIA_QUERY", "").lower() == "true":
                try:
                    from .session_router import (
                        _RESUME_QUERY_HEADER, _RESUME_QUERY_FOOTER,
                    )
                    continuation = (
                        f"{_RESUME_QUERY_HEADER}\n\n{continuation}\n\n"
                        f"{_RESUME_QUERY_FOOTER}"
                    )
                except Exception:  # noqa: BLE001 — header is best-effort, never fatal
                    pass

            # Prepend continuation — mirrors _heal_checkpoint shape
            if isinstance(query_content, str):
                return f"{continuation}{_CONTINUATION_SEPARATOR}{query_content}", True
            # Multimodal list: prepend a text block (check for existing enrichment)
            if (
                isinstance(query_content, list)
                and len(query_content) >= 2
                and isinstance(query_content[0], dict)
                and query_content[0].get("type") == "text"
                and _CONTINUATION_SEPARATOR in (query_content[0].get("text") or "")
            ):
                logger.info(
                    "session_unit.abandon_continuation_skipped_already_enriched "
                    "session_id=%s (multimodal)",
                    self._parent.session_id,
                )
                return query_content, False
            return [{"type": "text", "text": continuation}, *query_content], True

        except asyncio.TimeoutError:
            logger.warning(
                "session_unit.abandon_continuation_timeout session_id=%s "
                "— build_resume_context exceeded 5s",
                self._parent.session_id,
            )
            return query_content, False
        except Exception as exc:
            logger.warning(
                "session_unit.abandon_continuation_failed session_id=%s err=%s",
                self._parent.session_id, str(exc)[:200],
            )
            return query_content, False


