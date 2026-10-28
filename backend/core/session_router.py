"""SessionRouter — thin routing layer with RAM-based spawn admission.

Routes chat requests to the correct ``SessionUnit`` by session ID.
Spawn/resume admission is gated SOLELY by ``ResourceMonitor.spawn_budget()``
(real available RAM, with a concurrent-peak penalty) — NOT by a tab-count
ceiling. When RAM is exhausted, requests evict an idle peer or queue until a
slot frees naturally. (R6a, design §9: the ``compute_max_tabs`` UX ceiling is
a frontend concern, no longer consulted for backend arbitration.)

This module contains ONLY routing and cap logic.  No subprocess lifecycle,
prompt building, or hook execution lives here.

Public symbols:

- ``SessionRouter``  — Main class; dispatches to SessionUnits.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §2 SessionRouter
    ``.kiro/specs/dynamic-tab-scaling/design.md`` §2 SessionRouter._acquire_slot()
"""
from __future__ import annotations

import asyncio
import logging
import time
from core.awake_clock import awake_time  # liveness durations exclude sleep
from collections import deque
from contextlib import aclosing
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any, AsyncIterator, Callable, Optional, TYPE_CHECKING
from uuid import uuid4

from . import session_events
from .turn_writer import TurnWriter
from . import transcript_feed  # noqa: F401 — publishes out-of-turn transcript changes
from . import transcript_order
from .session_unit import SessionState, SessionUnit
from .turn_phase import PREPARING as TURN_PHASE_PREPARING, turn_phase_event

if TYPE_CHECKING:
    from .prompt_builder import PromptBuilder
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)

# A turn lists at most this many products (a bulk export should not flood the chat).
MAX_TURN_DELIVERABLES = 12


def _deliverables_block(files: list) -> dict:
    """The turn's ``turn_deliverables`` block: the first MAX_TURN_DELIVERABLES files and,
    when there were more, how many are not listed (``omitted``), so the end card says
    "+N" instead of reading as the whole list (turn_changes does the same)."""
    block = {"type": "turn_deliverables", "files": files[:MAX_TURN_DELIVERABLES]}
    if len(files) > MAX_TURN_DELIVERABLES:
        block["omitted"] = len(files) - MAX_TURN_DELIVERABLES
    return block

# ── SDK multimodal support flag ────────────────────────────────────
# False = always convert image/document blocks to path hints.
# Claude Code CLI does not currently support image/document content blocks
# via stdin JSON.  When SDK support lands, flip this to True.
_SDK_SUPPORTS_MULTIMODAL: bool = False

# ── Pre-response recall (G3: post-first-message injection) ────────
# Activates RecallEngine L2/L3 using the user's actual query.  Runs once per
# session, on the first user message.
#
# CORRECTNESS-FIRST (run_4d06640b): recall runs synchronously to COMPLETION
# before generating — the answer is built on the FULL brain, not a latency-trimmed
# subset. Priority is accurate + capable, NOT first-token speed (user directive).
# Measured ~1-3s; that latency is accepted, correctness > the seconds.
#
# PURE-FILESYSTEM (commit 6540970e, run_e9b8507e, 2026-06-28): recall is now
# keyword/FTS5/BM25 ONLY — the vector/Bedrock-Titan leg was torn out. _recall_for_query
# is called with allow_embed=False (see _maybe_inject_recall). The "right idea,
# different words" blind spot is covered by AGENTIC re-search (footer hint nudges the
# agent to re-grep with synonyms), NOT by an embedding leg. (Prior text here said
# "BOTH legs keyword + vector" — stale lie, the embed leg was already gone. PIT31 class.)
#
# The OLD 400ms daily timeout (run_bbd79e84/e9b15722) SILENTLY dropped recall when
# cold/slow/corpus-grown — a recurring time-bomb. It is replaced by a DISASTER
# timeout that ONLY bounds a recall code-HANG (bug / pathological query), never a
# daily latency judge. For the cap to be a REAL bound (not theater — asyncio
# wait_for cannot cancel a to_thread C-hang), the recall DB connection uses a
# SHORTER busy_timeout (_RECALL_DB_BUSY_TIMEOUT_MS) than the cap, so a sqlite lock
# wait cannot out-hang the cap. On trigger → LOUD (logger.error + metric), because
# silent degradation is exactly why recall was dead for months unnoticed.
_RECALL_DISASTER_TIMEOUT_S = 8.0       # hang-guard only; normal recall ~1-3s
_RECALL_DB_BUSY_TIMEOUT_MS = 5000      # < disaster cap → sqlite lock can't out-hang it
# Recall budget is intentionally lower than the 15K default in recall_engine.py.
# This injection is additive to an already-assembled system prompt (~30-50K),
# so we cap at 8K to avoid pushing context over budget on large sessions.
_RECALL_MAX_TOKENS = 8_000

# recall#5 cap (run_a16d61ad): a zero-keyword opener no longer latches the
# once-per-session guard (so a later substantive message can still recall), but
# after this many keyword-less turns we latch it closed — a session that never
# yields keywords must not re-run the regex extractor every turn forever.
_RECALL_KEYWORD_MISS_CAP = 5

# Single-source prefix for pre-warmed (unadopted) session ids. A prewarm unit is
# an empty-shell subprocess spawned ahead of a channel's first message; it carries
# no conversation. lifecycle_manager.enqueue_hooks guards on this prefix so prewarm
# sessions don't fire the ~11 post-session lifecycle hooks (workspace_auto_commit /
# evolution_maintenance / context_health / …). On adoption the unit's session_id is
# re-keyed to a real id (adopt_prewarmed_unit), so the prefix no longer matches and
# hooks resume normally. Minted at prewarm_channel_session.
PREWARM_SESSION_PREFIX = "prewarm-"

# Recall-degradation counter (run_4d06640b W5): increments whenever recall returns
# empty due to a failure/timeout rather than a genuine no-match. Surfaces silent
# degradation that the old logger.debug+return"" pattern hid for months. Read for
# observability (e.g. a health probe); the LOG is the primary signal, this is the
# aggregate. Reasons: "recall_db_unavailable", "exception:<Type>", "disaster_timeout".
#
# ⚠️ NOT ALL KEYS ARE FAILURES. "empty_with_keywords" (recall ran fine but matched
# nothing — a genuine no-match, expected for novel queries) is INFORMATIONAL, not a
# failure. A health probe / alarm MUST NOT sum this dict as a failure rate — it would
# false-alarm on every legitimate empty recall. Aggregate ONLY the true-failure keys
# (recall_db_unavailable / exception:* / disaster_timeout / leg_failure / inject_exception:*),
# and treat "empty_with_keywords" as a separate signal-quality metric (synonym-miss rate).
_recall_degraded_count: dict[str, int] = {}


def _read_run_map(session_id: str) -> "tuple[str, float] | None":
    """Pure sync read of the ``session_<sid>_run.map`` rendezvous → ``(run_id, ts)``
    or ``None``. Real filesystem I/O, so the async caller MUST dispatch it via
    ``asyncio.to_thread``. Delegates to ``runtime_hooks.read_session_run_map`` — the
    one reader shared with the SubagentStop marker writer. Never raises."""
    from .runtime_hooks import read_session_run_map
    return read_session_run_map(session_id)


async def _rehydrate_pipeline_run_id(ctx: dict, session_id: str) -> None:
    """Keystone (R1.5a-fix): restore ``_active_pipeline_run_id`` into the session
    hook-ctx on a RESUMED turn, so adversarial markers stay run-attributable.

    THE BUG: ``_active_pipeline_run_id`` is set ONLY by the `pipeline_v1.py create` PostToolUse
    hook (``runtime_hooks``). A resumed session never re-runs `pipeline_v1.py create` → the ctx
    key stays unset → markers get ``run_id=None`` and the D7 gate (and the compaction
    briefing) can't attribute them. The ``session_<sid>_run.map`` rendezvous written at
    `pipeline_v1.py create` was never read back into ctx (write-only dead state). This reads it back.

    FRESHNESS, not presence (Gate-1 H1): ``_hook_session_context`` is unit-lifetime —
    it is NEVER reset across turns — so a session that ran run_A then resumes mid-run_B
    would keep stale run_A under a blanket "set only if absent" guard, re-attributing
    run_B's markers to run_A (the IMPROVEMENT.md:119 wrong-run class). So we adopt the
    .map's run_id when ctx has NO run_id OR the .map is strictly NEWER (ts-ordered),
    mirroring the live hook's last-write-wins. The live hook also stamps
    ``_active_pipeline_run_id_ts`` so the two writers order consistently.

    CONCURRENCY (Gate-2 H1): the ``.map`` disk read is dispatched via
    ``asyncio.to_thread`` (see ``_read_run_map``) so it never blocks the send hot
    path; the ctx mutation stays on the loop (single-threaded, no ``await`` between
    the two key writes → the marker reader can never observe a half-updated pair).

    Called at ctx construction (every send); runs BEFORE this turn's own `pipeline_v1.py create`
    hook could fire, so it only ever competes with a PRIOR turn's stale value, never a
    same-turn live write. Idempotent + fail-safe: a missing / corrupt / empty-run_id
    .map is a no-op and NEVER raises.

    KNOWN RESIDUAL (Gate-2 correctness HIGH, tracked as a follow-up — NOT fixed here):
    the ``.map`` is never deleted at run completion, so a session that finished run_A
    and then (post-eviction, empty ctx) runs an adversarial sub-agent before run_B's
    `pipeline_v1.py create` fires will re-hydrate the stale run_A. Blast radius is OVER-credit
    (run_A's completed set gains an expert it didn't need) — it can only RELAX a
    future D7 check, never false-block a commit, and D7 is not yet live. A clean fix
    (run-completion .map invalidation, or a run-active guard) needs the project the
    .map does not carry, so it is out of this keystone's scope."""
    try:
        result = await asyncio.to_thread(_read_run_map, session_id)
        if result is None:
            return
        map_run_id, map_ts = result
        ctx_run_id = ctx.get("_active_pipeline_run_id")
        ctx_ts = ctx.get("_active_pipeline_run_id_ts")
        ctx_ts = float(ctx_ts) if isinstance(ctx_ts, (int, float)) else float("-inf")
        # Adopt the .map iff ctx has nothing, or the .map is strictly newer (H1).
        if not ctx_run_id or map_ts > ctx_ts:
            ctx["_active_pipeline_run_id"] = map_run_id
            ctx["_active_pipeline_run_id_ts"] = map_ts
    except Exception as e:  # fail-safe: attribution read must never break session start
        logger.debug("pipeline_run_id re-hydration skipped: %s", e)


def _clean_blocks_for_persist(blocks: list, *, assistant: bool = True) -> list:
    """Return a NEW blocks list with raw tool-call leaks stripped from every text
    block — the single write-side clean (run_2bdec5c0, re-homed run_719886ee).

    This is the ONE place assistant content is cleaned before it enters data.db,
    so all downstream content→model-prompt readers (context_injector resume,
    session_recall, memory_extractor, summarization) inherit a clean DB for free
    — Gate-1 (run_719886ee) confirmed memory_extractor/summarization read the
    persisted content into a Bedrock prompt WITHOUT their own strip, so deleting
    this chokepoint would re-open the re-induction loop for them. KEEP it.

    Returns the cleaned list so the CALLER can write it back into the yielded
    ``event['content']`` — this closes bug 2d (the former inline clean built a
    cleaned list for the DB but the stream yielded the ORIGINAL ``event`` object,
    so the UI showed the raw leak while a reload showed the cleaned text). With a
    single returned list, persist AND yield carry identical bytes.

    Best-effort: ANY exception degrades to returning the ORIGINAL blocks (+ a
    warning upstream) — crash-safe persistence of partial content outranks
    leak-cleaning (losing content is the worse harm). The strip itself is lossless
    on clean text (run_719886ee fixed the 2a mask corruption), so a clean block is
    returned byte-for-byte.

    ``assistant=True`` also strips the glitch-prefix shape (``strip_glitch_prefix``:
    a corrupted opener word at a line start). It is model output only — a USER may
    legitimately type "count 一下", so the user persist passes ``assistant=False``.
    """
    from .session_utils import strip_glitch_prefix, strip_tool_call_leak

    cleaned: list = []
    for blk in blocks:
        if (
            isinstance(blk, dict)
            and blk.get("type") == "text"
            and isinstance(blk.get("text"), str)
        ):
            text = strip_tool_call_leak(blk["text"])
            if assistant:
                text = strip_glitch_prefix(text)
            cleaned.append({**blk, "text": text})
        else:
            cleaned.append(blk)
    return cleaned


def _record_recall_degraded(reason: str) -> None:
    """Count a recall degradation by reason (loud-on-degradation observability)."""
    try:
        _recall_degraded_count[reason] = _recall_degraded_count.get(reason, 0) + 1
    except Exception:  # noqa: BLE001 — metric must never break recall
        logger.debug("_record_recall_degraded: suppressed exception on a fail-safe path", exc_info=True)
        pass


# DDD runtime-injection counter (run_91bc0651 M2, Gate-2 L1 anti-silent-death):
# a fail-closed detector ("no active project → don't inject") makes "detection
# always fails (bug)" byte-identical to "correctly declined (by design)". WITHOUT
# a positive counter, a broken detector = DDD silently never injects, forever,
# invisibly — the exact dead-path class this whole feature exists to kill. Track
# injected vs declined-by-reason: a 100%-declined rate over many sessions is a
# VISIBLE degradation signal (mirror _record_recall_degraded).
_ddd_inject_count: dict[str, int] = {}


def _record_ddd_inject(outcome: str) -> None:
    """Count a DDD-recall outcome: 'injected' or 'declined:<reason>'."""
    try:
        _ddd_inject_count[outcome] = _ddd_inject_count.get(outcome, 0) + 1
    except Exception:  # noqa: BLE001 — metric must never break recall
        logger.debug("_record_ddd_inject: suppressed exception on a fail-safe path", exc_info=True)
        pass


# ── Degradation readers (run_e9861490) ────────────────────────────────────────
# Both counters above were WRITE-ONLY — incremented but never read anywhere, so a
# silently-degrading recall (empty every session on a real failure) was invisible
# for as long as the daemon ran. This is the READ side that completes the
# loud-on-degradation contract the counters were built for (GUI83: a positive
# counter is only half — it must be READ). Consumed by context_health_hook's
# daily deep-check → session-briefing findings. Cadence note: the counts are
# CUMULATIVE-SINCE-DAEMON-START (module-level, reset on restart); a once-per-day
# read of a cumulative counter is the right window for CHRONIC degradation (the
# months-hidden failure class), NOT per-session transient alerting.
#
# TRUE-FAILURE vs INFORMATIONAL — co-located with the writers so the reason
# strings the _record_* sites emit and the classifier here CANNOT drift.
# A true failure = recall/inject broke (crash/timeout/unavailable). Informational
# = ran fine but matched nothing / declined by design — NEVER counted as failure
# (summing them would false-alarm on every legitimate empty recall; see :98).
_RECALL_TRUE_FAILURE_REASONS: frozenset[str] = frozenset({
    "recall_db_unavailable", "leg_failure", "disaster_timeout", "followup_timeout",
})
# exception-family reasons are dynamic ("exception:<Type>") → prefix-matched.
# Each prefix is listed in FULL: startswith("exception:") does NOT match
# "inject_exception:Foo", so a new family needs its own entry here.
# "flatten_exception:" is a TSCC-panel-side break — recall itself was unaffected
# and the injected block still shipped — but it is a genuine structural defect
# (BucketedRecall shape drift), so it alarms as a failure rather than being filed
# as by-design informational. Under-alarming on a real bug is the worse error.
_RECALL_TRUE_FAILURE_PREFIXES: tuple[str, ...] = (
    "exception:", "inject_exception:", "unified_exception:", "flatten_exception:",
    "toplevel_exception:", "followup_exception:",
)
# INFORMATIONAL (NOT failures): "empty_with_keywords" (genuine no-match),
# "unified_empty_fallback_legacy" (strangler-fig fallback to legacy, expected).
_DDD_TRUE_FAILURE_REASONS: frozenset[str] = frozenset({
    "declined:disaster_timeout",
})
_DDD_TRUE_FAILURE_PREFIXES: tuple[str, ...] = ("declined:exception:",)
# NOT failures: "injected" (success), "declined:no_ddd_hits" (project has no DDD),
# "declined:<signal>" (fail-closed no-active-project — by design).


# Reasons KNOWN to be informational (ran fine / declined by design) — NOT failures,
# but explicitly recognized so the health reader can tell "known-informational" from
# "a reason nobody classified yet". A reason that is neither a true-failure NOR here
# is UNCLASSIFIED → the reader surfaces it (else a future writer's new reason silently
# vanishes from every signal — the dead-signal recursion Gate-2 flagged, run_e9861490).
_RECALL_KNOWN_INFORMATIONAL: frozenset[str] = frozenset({
    "empty_with_keywords", "unified_empty_fallback_legacy",
    # the failure itself is already counted as unified_exception:*
    "unified_failed_fallback_legacy",
    # the session's recall budget (_RECALL_SESSION_CHARS) is spent: a resource cap
    "budget_stopped",
})
_DDD_KNOWN_INFORMATIONAL: frozenset[str] = frozenset({
    "injected", "declined:no_ddd_hits", "injected_pointer_only",
    # dynamic "declined:<signal>" from detect_active_project — by-design declines:
    "declined:no_projects", "declined:ambiguous", "declined:no_signal",
    # project found via the bound repo, no section matched: repo_guidance already
    # gave the agent the DDD documents (design 2026-10-26 D3)
    "pointer_from_repo_guidance",
})
# NOTE: deliberately NOT a blanket "declined:" prefix — a future "declined:db_broke"
# should surface as UNCLASSIFIED (visible), not be swallowed as by-design.
_DDD_KNOWN_INFORMATIONAL_PREFIXES: tuple[str, ...] = ()


def _is_recall_true_failure(reason: str) -> bool:
    return (reason in _RECALL_TRUE_FAILURE_REASONS
            or reason.startswith(_RECALL_TRUE_FAILURE_PREFIXES))


def _system_prompt_cache_to_pass(
    cached: "str | None", *, will_reuse_live: bool
) -> "str | None":
    """Decide what cached system prompt (if any) to hand build_options this turn
    (per-session cache, run_1dc710db).

    Return the cache ONLY on a warm-reuse turn (``will_reuse_live``) — the sole turn
    where options.system_prompt is DISCARDED (send() reuses the live subprocess via
    client.query()), so serving a possibly-stale cache is harmless AND saves the
    ~85K re-assembly. On ANY spawn turn (cold entry / respawn / resume),
    options.system_prompt IS consumed by _spawn() and MUST be a fresh build carrying
    this turn's volatile bits (UI-state/open-file snapshot + datetime tail) — so we
    pass None and build_options assembles fresh. (Gate-2 HIGH run_1dc710db: an
    evicted→respawn turn keeps _sdk_session_id, so it is NOT a cold_resume and would
    otherwise wrongly reuse turn-1's stale UI-state.)"""
    return cached if will_reuse_live else None


def _should_store_system_prompt_cache(
    built_prompt: "object", *, will_reuse_live: bool, needs_context_injection: bool
) -> bool:
    """True iff this turn's system prompt should SEED/refresh the per-session cache
    (run_1dc710db).

    Store any NON-RESUME turn whose prompt is a real non-empty string. The ONE hard
    exclusion is ``needs_context_injection`` — a resume build carries a ONE-SHOT
    prior-conversation block that must never be re-served on a later turn (Gate-2 MED
    run_1dc710db). We deliberately do NOT exclude ``will_reuse_live``: on a warm turn
    whose cache was still empty (every turn of a session whose turn-1 was a resume,
    which otherwise never seeds), build_options rebuilt a full history-free prompt —
    storing THAT seeds the cache so later warm turns finally hit it (closes the
    "resumed session never caches" perf gap, Gate-2 MED#4 run_1dc710db). When a warm
    turn actually REUSED the cache, ``built_prompt`` IS the cache, so storing it back
    is a harmless no-op — and never history-bearing, because the resume exclusion
    still holds. ``will_reuse_live`` is kept in the signature for call-site symmetry
    with ``_system_prompt_cache_to_pass`` and documentation, though not gated on."""
    return (
        not needs_context_injection
        and isinstance(built_prompt, str)
        and bool(built_prompt)
    )


def recall_unclassified_reasons(snapshot: "dict[str, int] | None" = None) -> dict[str, int]:
    """Recall reasons that are NEITHER true-failure NOR known-informational — i.e.
    a reason no one classified. Surfacing these prevents a new writer's reason from
    silently vanishing (dead-signal recursion). Empty in a correctly-maintained tree."""
    snap = get_recall_degraded_snapshot() if snapshot is None else snapshot
    return {
        r: n for r, n in snap.items()
        if not _is_recall_true_failure(r) and r not in _RECALL_KNOWN_INFORMATIONAL
    }


def ddd_unclassified_reasons(snapshot: "dict[str, int] | None" = None) -> dict[str, int]:
    """DDD-inject outcomes that are neither true-failure nor known-informational."""
    snap = get_ddd_inject_snapshot() if snapshot is None else snapshot
    return {
        r: n for r, n in snap.items()
        if not _is_ddd_true_failure(r)
        and r not in _DDD_KNOWN_INFORMATIONAL
        and not r.startswith(_DDD_KNOWN_INFORMATIONAL_PREFIXES)
    }


def _is_ddd_true_failure(outcome: str) -> bool:
    return (outcome in _DDD_TRUE_FAILURE_REASONS
            or outcome.startswith(_DDD_TRUE_FAILURE_PREFIXES))


def get_recall_degraded_snapshot() -> dict[str, int]:
    """Snapshot (copy) of the recall-degradation counts since daemon start.
    Copy, so a caller can't mutate the live counter."""
    return dict(_recall_degraded_count)


def get_ddd_inject_snapshot() -> dict[str, int]:
    """Snapshot (copy) of the DDD-inject outcome counts since daemon start."""
    return dict(_ddd_inject_count)


def recall_true_failure_total(snapshot: "dict[str, int] | None" = None) -> int:
    """Sum ONLY true-failure recall reasons (excludes informational no-match).
    Defaults to the live snapshot."""
    snap = get_recall_degraded_snapshot() if snapshot is None else snapshot
    return sum(n for r, n in snap.items() if _is_recall_true_failure(r))


def ddd_inject_true_failure_total(snapshot: "dict[str, int] | None" = None) -> int:
    """Sum ONLY true-failure DDD-inject outcomes (excludes declined-by-design)."""
    snap = get_ddd_inject_snapshot() if snapshot is None else snapshot
    return sum(n for r, n in snap.items() if _is_ddd_true_failure(r))

# Keyword extraction lives in recall_filter (one copy for chat and pipeline recall,
# run-knowledge §2); the names are kept here for existing callers and tests.
from .recall_filter import (  # noqa: E402,F401
    _CJK_MAX_TERM, _CJK_RUN_RE, _CJK_STOP_RE, _CJK_STOP_WORDS, _STOP_WORDS, _cjk_terms,
    extract_query_keywords as _extract_query_keywords,
)


def _prepend_ui_state_to_query(
    query_content: Any,
    editor_context: Optional[dict],
    should_prefix: bool,
) -> Any:
    """Prefix the request-time UI-state (SENSE) onto the user query for a REUSED
    live subprocess (run_5d460dd5). Pure — no IO, no mutation of inputs.

    Why the query channel (not system_prompt): UI-state normally rides
    ``options.system_prompt`` (``_render_ui_context_section``), but a reused live
    ClaudeSDKClient is only handed ``system_prompt`` at ``_spawn`` — subsequent
    turns send ONLY the query. So for a warm-reuse turn the freshly-built
    system_prompt (carrying THIS turn's canvas) is discarded, and the query is the
    ONLY per-message path that reaches the subprocess. A COLD/spawning turn already
    gets it via system_prompt, so we must NOT double-inject there.

    ``should_prefix`` is the caller's reuse discriminator (state==IDLE AND
    _client is not None AND _last_turn_clean — the exact complement of the
    poison-guard recycle at session_unit.py, which would otherwise respawn and
    re-carry system_prompt → double-inject).

    Returns ``query_content`` UNCHANGED when: not reusing, or the UI-state block is
    empty (no file/canvas/overlay — e.g. channel sessions). For a ``str`` query the
    block is prepended as text; for a multimodal ``list`` it is inserted as a
    leading ``{type:text}`` block (valid before image/document blocks).
    """
    # Strangler: delegate to the generalized dynamic-context prefixer with no
    # recall_block (SENSE-only) — preserves the exact original behavior/signature.
    return _prepend_dynamic_context_to_query(
        query_content, editor_context, recall_block=None, should_prefix=should_prefix,
    )


# ── UI state without piling up (canvas-proprioception.md §10) ─────────────────
# A warm turn's query prefix stays in the model's conversation, so a UI-state block
# identical to the one this subprocess last received is replaced by one line. Both
# lines start with the UI-state header, so strip_injected_blocks removes them.
UI_STATE_UNCHANGED = "## Current UI State\n(unchanged since your previous message)"
UI_STATE_NOTHING = "## Current UI State\nNothing is shown now: no open file, Canvas closed, no overlay."
_RENDER = object()  # "render the UI block yourself" (callers that did not decide)


def _decide_ui_block(unit: Any, editor_context: Optional[dict], warm: bool) -> Optional[str]:
    """The UI-state block to put on THIS turn's query, or None. The one writer and
    reader of ``unit._last_ui_block`` (what this subprocess last received).

    Cold turn → None (the UI state rides the system prompt) and forget, so the next
    warm turn sends the full block once (no assumption about what the spawn
    carried). Warm turn → the rendered block; ``UI_STATE_UNCHANGED`` when it is
    character-for-character the last one; ``UI_STATE_NOTHING`` once when the UI went
    from something to nothing (a silent prefix would leave the old block standing).
    """
    if not warm:
        unit._last_ui_block = None
        return None
    from .prompt_builder import _render_ui_context_section

    block = _render_ui_context_section(editor_context).lstrip("\n") or None
    last = getattr(unit, "_last_ui_block", None)
    if block is None:
        if last is None:
            return None
        block = UI_STATE_NOTHING
    if block == last:
        return UI_STATE_UNCHANGED
    unit._last_ui_block = block
    return block


def _prepend_dynamic_context_to_query(
    query_content: Any,
    editor_context: Optional[dict],
    recall_block: Optional[str],
    should_prefix: bool,
    ui_block: Any = _RENDER,
) -> Any:
    """阶段二 prompt-builder 两分 — prefix the per-turn DYNAMIC segment (recall +
    UI-SENSE) onto the user query for a REUSED live subprocess. Pure — no IO, no
    mutation of inputs. Generalizes ``_prepend_ui_state_to_query`` (which now
    delegates here with ``recall_block=None``).

    Why the query channel (not system_prompt): a reused live ClaudeSDKClient is
    only handed ``system_prompt`` at ``_spawn`` — subsequent turns send ONLY the
    query. So a warm-reuse turn's freshly-built system_prompt (carrying THIS
    turn's recall + canvas) is discarded; the query is the ONLY per-message path
    to the subprocess. A COLD/spawning turn already gets both via system_prompt,
    so we must NOT double-inject there (``should_prefix`` is False on cold).

    ``should_prefix`` is the caller's reuse discriminator (state==IDLE AND
    _client is not None AND _last_turn_clean — the exact complement of the
    poison-guard recycle at session_unit.py, which would otherwise respawn and
    re-carry system_prompt → double-inject).

    Segment order: recall_block FIRST (carrying its verbatim ``[RECALLED]``
    provenance header — the block is passed through UNCHANGED), then the UI-SENSE
    block. Returns ``query_content`` UNCHANGED when: not reusing, or BOTH the
    recall_block and the UI-state block are empty. For a ``str`` query the segment
    is prepended as text; for a multimodal ``list`` it is a leading ``{type:text}``.
    """
    if not should_prefix:
        return query_content
    block = _build_dynamic_prefix_block(editor_context, recall_block, ui_block=ui_block)
    if not block:  # nothing dynamic to report → clean no-op
        return query_content
    if isinstance(query_content, list):
        return [{"type": "text", "text": block}, *query_content]
    return f"{block}\n\n{query_content}"


def _build_dynamic_prefix_block(
    editor_context: Optional[dict],
    recall_block: Optional[str],
    ui_block: Any = _RENDER,
) -> Optional[str]:
    """TSCC/security-scan alignment (run_380413c5) — SSoT builder for the
    recall+UI-SENSE prefix block. Returns the exact block string that
    ``_prepend_dynamic_context_to_query`` prepends (or ``None`` when empty).

    Extracted so the send-site can capture EXACTLY what was delivered onto the
    query (for TSCC ``full_text`` + the security-scan panel) without a fragile
    before/after diff of ``query_content`` — a diff is ambiguous for the
    multimodal ``list`` shape (Gate-1 P1/P2). One source of truth: this builder
    is the only place the block text is assembled; both the prefixer and the
    metadata-capture read it.
    """
    # Lazy import: prompt_builder imports session-layer types; keep it in-function
    # to avoid an import cycle at module load (matches the existing lazy-import
    # pattern for build_agent_config in this module).
    from .prompt_builder import _render_ui_context_section

    parts: list[str] = []
    # recall_block passed through VERBATIM — preserves the [RECALLED] header (AC4).
    if recall_block and recall_block.strip():
        parts.append(recall_block.strip())
    if ui_block is _RENDER:
        ui_block = _render_ui_context_section(editor_context)
    if ui_block:
        # _render_ui_context_section returns a leading "\n\n" — strip for a clean join.
        parts.append(str(ui_block).lstrip("\n"))
    if not parts:
        return None
    return "\n\n".join(parts)


def _build_terminal_prefix_block(terminal_context: Optional[dict]) -> Optional[str]:
    """Run 2 (prompt-injection root-fix) — SSoT builder for the attached-terminal
    UNTRUSTED-DATA block that rides the USER/query channel. Returns the exact block
    string ``_prepend_terminal_to_query`` prepends (or ``None`` when empty).

    Sibling of ``_build_dynamic_prefix_block`` / ``_build_resume_prefix_block``:
    the send-site reads this to capture EXACTLY what was delivered onto the query
    (for TSCC ``full_text`` + the security-scan panel) WITHOUT a fragile
    before/after diff of ``query_content`` (ambiguous for the multimodal list
    shape). One source of truth: both the prefixer and the metadata-capture read it.

    Why the terminal block is delivered on the query channel at all: for Claude,
    ``role: system`` is the non-spoofable operator channel with the WEAKEST
    injection defense; user/tool content is treated as possibly-forged with the
    STRONGEST defense (Anthropic claude-api canonical). Attacker-controllable
    terminal ``buffer_tail`` therefore belongs on the user channel, never the
    system prompt. Rendering (UNTRUSTED boundary + dynamic fence) is the SSoT
    ``prompt_builder.render_terminal_data_block``.
    """
    if not terminal_context:
        return None
    # Lazy import mirrors _build_dynamic_prefix_block's in-function import to
    # avoid the prompt_builder ↔ session-layer import cycle at module load.
    from .prompt_builder import render_terminal_data_block

    buffer_tail = terminal_context.get("buffer_tail", "") or ""
    term_cwd = terminal_context.get("cwd", "") or ""
    block = render_terminal_data_block(buffer_tail, term_cwd)
    return block or None


def _prepend_terminal_to_query(
    query_content: Any,
    terminal_context: Optional[dict],
) -> Any:
    """Run 2 (prompt-injection root-fix) — prefix the attached-terminal
    UNTRUSTED-DATA block onto the user query. Pure — no IO, no mutation of inputs.

    UNLIKE ``_prepend_dynamic_context_to_query`` (recall + UI-SENSE, gated on
    ``should_prefix=_will_reuse_live``) and the resume prefix (gated on
    ``_should_prefix_resume``), the terminal block is prefixed **UNCONDITIONALLY
    — on BOTH the cold-spawn and warm-reuse paths**. That is the whole point of
    Run 2: terminal DATA no longer rides the system prompt on ANY path (the
    build_system_prompt injection was deleted), so the query channel is its ONLY
    delivery — and both cold and warm send ``query_content`` to the subprocess as
    a USER turn (session_unit stream_query wraps it ``role: user``). There is no
    double-inject: the system-prompt sink is gone, so the query prefix is the
    sole carrier on every path.

    Returns ``query_content`` UNCHANGED when there is no terminal block. For a
    ``str`` query the block is prepended as text; for a multimodal ``list`` it is
    a leading ``{type: text}`` block.
    """
    block = _build_terminal_prefix_block(terminal_context)
    if not block:
        return query_content
    if isinstance(query_content, list):
        return [{"type": "text", "text": block}, *query_content]
    return f"{block}\n\n{query_content}"


# resume-context-injection去根 (run_d108b914): the provenance header that wraps a
# resume block when it rides the query channel. The 150K resume block is prior
# conversation history — NOT this turn's user intent. Framing it explicitly as
# quoted history (with a "the actual request follows" boundary) is the
# confabulation guard: without it, a model reading history as a leading user-turn
# block can mistake the recap for the current instruction (AC3). Mirrors the
# [RECALLED] provenance convention recall already uses.
# The strings live in core.injected_blocks so the hooks that strip this block
# from a prompt match exactly what is built here.
from .injected_blocks import RESUME_FOOTER as _RESUME_QUERY_FOOTER  # noqa: E402
from .injected_blocks import RESUME_HEADER as _RESUME_QUERY_HEADER  # noqa: E402


def _should_prefix_resume(is_cold_resume: bool, needs_channel_resume: bool) -> bool:
    """resume-context-injection去根 (run_d108b914) — the resume query-prefix gate.

    Extracted so the highest-severity Gate-2 fix (F1) is unit-testable: the resume
    block must be prefixed on the SAME condition that made build_options stash it —
    ``is_cold_resume OR needs_channel_resume`` (session_router sets
    ``needs_context_injection`` under exactly this disjunction). Gating on
    ``is_cold_resume`` alone DROPS the block on a channel/Slack resume turn under
    flag ON → silent amnesia (the exact bug this refactor fixes). The real send()
    call site references THIS helper, so a revert to cold-only is caught by
    ``test_should_prefix_resume_*`` (mutation-proof), not silently green.
    """
    return bool(is_cold_resume or needs_channel_resume)


def _prepend_resume_to_query(
    query_content: Any,
    resume_block: Optional[str],
    should_prefix: bool,
) -> Any:
    """resume-context-injection去根 (run_d108b914) — prefix the RESUME segment onto
    the user query for a COLD-resume spawning turn. Pure — no IO, no mutation of
    inputs.

    INDEPENDENT of ``_prepend_dynamic_context_to_query`` (recall + UI-SENSE) on
    purpose. The two are ORTHOGONAL by turn state and must not be merged:

    * resume rides the query on a COLD-resume turn (``is_cold_resume`` — state==COLD,
      no live subprocess yet). Historically resume rode ``options.system_prompt``,
      but that 150K volatile block polluted the otherwise-cacheable default prompt
      and drove the #13/#15 fallback amnesia (a session-not-found respawn strips the
      ``resume`` field from the ALREADY-built options → blank respawn). Riding the
      query instead means the fallback — which only edits options, never the query —
      keeps the resume block. Strangler-gated by ``SWARM_RESUME_VIA_QUERY``.
    * recall + UI-SENSE ride the query on a WARM-reuse turn (``_is_warm_reuse`` —
      state==IDLE, live subprocess). These two conditions NEVER hold on the same
      turn (COLD ≠ IDLE), so a single turn's query gets at most one of the two
      prefixes. Reusing ``_prepend_dynamic_context_to_query`` for resume would ALSO
      render SENSE (it unconditionally appends the UI block) — double-injecting a
      SENSE block that is already in the cold turn's system_prompt (Gate-1 F).

    Wraps the resume block in ``_RESUME_QUERY_HEADER`` / ``_RESUME_QUERY_FOOTER``
    (the confabulation guard, AC3). Returns ``query_content`` UNCHANGED when
    ``should_prefix`` is False or the resume block is empty. ``str`` query → text
    prefix; multimodal ``list`` → leading ``{type:text}`` block.
    """
    if not should_prefix:
        return query_content
    block = _build_resume_prefix_block(resume_block)
    if not block:
        return query_content
    if isinstance(query_content, list):
        return [{"type": "text", "text": block}, *query_content]
    return f"{block}\n\n{query_content}"


def _build_resume_prefix_block(resume_block: Optional[str]) -> Optional[str]:
    """TSCC/security-scan alignment (run_380413c5) — SSoT builder for the resume
    prefix block. Returns the exact block ``_prepend_resume_to_query`` prepends
    (header + resume history + footer), or ``None`` when the resume block is empty.

    Sibling of ``_build_dynamic_prefix_block``: the send-site reads this to capture
    the delivered resume text for TSCC ``full_text`` / security-scan, instead of
    diffing ``query_content`` (Gate-1 P1/P2).
    """
    if not resume_block or not resume_block.strip():
        return None
    return (
        f"{_RESUME_QUERY_HEADER}\n\n"
        f"{resume_block.strip()}\n\n"
        f"{_RESUME_QUERY_FOOTER}"
    )


# TSCC/security-scan alignment (run_380413c5): the provenance separator that marks
# where the per-turn query-channel context (resume / recall / SENSE) begins in the
# published full_text. TSCC's "System Prompt" modal + the security-scan panel read
# full_text; before this, full_text = options.system_prompt only, so the
# query-channel blocks (up to ~150K of resume history — the highest PII-risk
# content) were invisible to BOTH panels. Composing full_text = base + separator +
# delivered_prefix makes the actual delivered prompt visible + scannable.
_FULLTEXT_PREFIX_SEPARATOR = (
    "\n\n=== TURN QUERY-CHANNEL CONTEXT "
    "(delivered with the user message this turn) ===\n"
)


def _compose_full_text(base_system_prompt: str, delivered_prefix: Optional[str]) -> str:
    """TSCC/security-scan alignment (run_380413c5) — compose the published
    ``full_text`` from the stable base system prompt + this turn's delivered
    query-channel prefix (resume on a cold-resume turn, recall+SENSE on a warm
    turn, or nothing).

    ALWAYS recomposed from ``base_system_prompt`` (never from a prior turn's
    full_text) so consecutive turns do not COMPOUND prefixes. Empty prefix →
    returns the base VERBATIM (flag-OFF / no-prefix turn is byte-identical to the
    old behavior — the regression lock).
    """
    base = base_system_prompt or ""
    if not delivered_prefix:
        return base
    return f"{base}{_FULLTEXT_PREFIX_SEPARATOR}{delivered_prefix}"


def _is_warm_reuse(unit: Any) -> bool:
    """Single source of the warm-reuse predicate (阶段二 R27 — was duplicated at
    two send() sites: the system-prompt cache gate + the dynamic-context prefix
    gate). A warm turn reuses the LIVE subprocess via ``client.query()``, so
    ``options.system_prompt`` is discarded and per-turn dynamic content (recall +
    UI-SENSE) must ride ``query_content`` instead.

    This is the EXACT COMPLEMENT of the poison-guard recycle in
    ``session_unit.send()`` (IDLE ∧ client ∧ NOT clean ∧ NOT prewarm →
    recycle→COLD→respawn, where system_prompt DOES carry it): within the
    (IDLE ∧ client-alive) domain, warm-reuse ⟺ (last-turn-clean OR prewarm),
    poison-recycle ⟺ (NOT last-turn-clean AND NOT prewarm). Keeping both off this
    ONE predicate makes the two gates provably non-double-injecting / non-dropping.

    Gate-1 F1: a fresh PREWARM unit (never streamed → last_turn_clean=False) is
    warm-reuse-ELIGIBLE — it reuses the pre-spawned live subprocess via query(),
    and 阶段二 routes its first-message recall/SENSE through query_content (not the
    discarded system_prompt), so nothing is lost. It is keyed on the `prewarm-`
    PREFIX, NOT `_sdk_session_id is None`: a first-message turn that ended
    unclean (crash, interrupt) has the identical (clean=False, sdk_session_id=None)
    shape but a normal id — it must recycle, so the prefix is the only safe signal."""
    return (
        unit.state == SessionState.IDLE
        and unit._client is not None
        and (
            unit._last_turn_clean
            or unit.session_id.startswith(PREWARM_SESSION_PREFIX)
            # Gate-1 #5 bridge: an adopted prewarm unit has already LOST the
            # `prewarm-` prefix (adopt_prewarmed_unit re-keyed it), but its first
            # message must still warm-reuse the pre-spawned subprocess. The
            # one-shot flag survives the re-key and is cleared at STREAMING entry.
            or getattr(unit, "_adopted_prewarm_fresh", False)
        )
    )


def _recall_for_query(query: str, max_tokens: int, allow_embed: bool = False) -> str:
    """Run keyword FTS5+BM25 recall across Knowledge + Memory.

    Thin wrapper around RecallEngine. Uses ``open_vec_db()`` for a thread-safe
    sqlite3 connection (this runs in ``asyncio.to_thread``).

    Recall is PURE FTS5+BM25 (the vector/Bedrock-Titan leg was removed — see PRI11:
    FTS5-only, zero-embedding is the intended architecture). ``allow_embed`` is a
    dead, inert parameter retained only for caller-compat; it is ignored. The
    connection uses a SHORT busy_timeout (_RECALL_DB_BUSY_TIMEOUT_MS) so a sqlite
    write-lock wait cannot exceed the caller's disaster cap.

    Graph enrichment: extracts entities from query, queries knowledge graph
    for related entry IDs/titles, appends them to enrich the recall context.

    Returns formatted recalled content or empty string.

    LOUD-ON-DEGRADATION (run_4d06640b W5): an internal failure logs at WARNING
    with a metric, so a degraded recall (sqlite error) is VISIBLE, not silent.
    """
    try:
        from .vec_db import open_vec_db
        from .knowledge_store import KnowledgeStore
        from .recall_engine import RecallEngine

        # Short busy_timeout so a write-lock wait cannot out-hang the caller's
        # disaster cap (run_4d06640b B3 — makes the cap a real bound).
        with open_vec_db(busy_timeout_ms=_RECALL_DB_BUSY_TIMEOUT_MS) as conn:
            if conn is None:
                _record_recall_degraded("recall_db_unavailable")
                logger.warning("_recall_for_query: recall DB connect failed — recall empty")
                return ""

            store = KnowledgeStore(conn)

            # No transcript leg: transcript_chunks was dropped in schema v13.
            # Past conversations are recalled from messages_fts by the unified
            # path's `session` domain. No memory leg either: MemoryRecallStore
            # read a `memory_entries` table nothing has written since the vector
            # subsystem was removed, so it always returned nothing (design
            # 2026-10-26 D4); live MEMORY is recalled by the unified path's
            # context_files leg, its archives by the library index.
            engine = RecallEngine(store)

            recalled = engine.recall_knowledge(query, max_tokens=max_tokens)

            # LOUD one layer down (run_4d06640b Gate-2 HIGH-2): RecallEngine.search
            # swallows per-leg failures to []. An empty result from a LEG FAILURE is
            # indistinguishable from a genuine no-match unless we inspect the engine's
            # error trail — otherwise a fully-degraded recall is silent (the W5 bug,
            # one frame deeper). Surface it: if legs errored, log + metric even when
            # we still return (partial) content.
            leg_errors = getattr(engine, "last_search_errors", None) or []
            if leg_errors:
                _record_recall_degraded("leg_failure")
                logger.warning(
                    "recall leg(s) failed (recall may be degraded): %s",
                    ", ".join(leg_errors[:6]),
                )

            # Graph enrichment: append related entry context
            graph_context = _graph_enrich_recall(query)
            if graph_context and recalled:
                recalled = recalled + "\n\n" + graph_context
            elif graph_context:
                recalled = graph_context

            return recalled
    except Exception as exc:
        # LOUD, not silent (W5): a swallowed failure here is the dead-path class.
        _record_recall_degraded(f"exception:{type(exc).__name__}")
        logger.warning("_recall_for_query failed (recall degraded to empty): %s: %s",
                        type(exc).__name__, exc)
        return ""


def _flatten_recall_hits(result: Any) -> list[dict]:
    """Flatten a BucketedRecall into TSCC's structured per-hit list — the REAL
    hits that were recalled this turn (source + score + domain), NOT a re-run.

    Shape per hit: {domain, source, score, method, text}; the "not shown" notes the
    budget adds (``kind == "omitted"``) are not hits and are left out. Size-bounded by
    the recall caps (max_sections=3/domain) and the 400-character text preview, so
    this is a few KB. Best-effort: a per-hit shape surprise is skipped and a
    structural failure returns the partial list — it never raises, because a
    panel-observability helper must not break the recall leg. It does LOG and
    count that degradation, so a silently shortened list is still visible."""
    hits: list[dict] = []
    # Hit dict shapes VARY per domain (verified against recall_multi.py + graph_store):
    #   library:       {source, heading, content, score}          score in [0,1]
    #   ddd:           {doc, section, score, (content)}            score in [0,1]
    #   context_files: {section, (content)}                       NO score
    #   session:       {text}                                     NO source, NO score
    #   codeintel:     {name, file_path, rank}                    rank = NEGATIVE FTS5, NOT [0,1]
    # Probe the REAL keys, synthesize a source where the domain has none, and only
    # surface a score when it's a real normalized [0,1] value (adversarial HIGH,
    # run_abab234c: old probes gave blank sources / false 0.0 / a negative "BM25"
    # for codeintel under a "[0,1]" label).
    def _source_for(domain: str, h: dict) -> str:
        if h.get("doc") and h.get("section"):
            return f"{h['doc']} § {h['section']}"
        cand = (h.get("source_file") or h.get("source") or h.get("heading")
                or h.get("section") or h.get("name") or h.get("file_path") or "")
        if cand:
            return str(cand)
        if domain == "session":
            return "past session"
        if domain == "codeintel":
            return "code symbol"
        return ""

    def _score_for(h: dict):
        # Only real normalized [0,1] scores. context_files/session have none;
        # codeintel `rank` is a raw negative FTS5 rank — NOT comparable to BM25 [0,1],
        # so it is NOT surfaced as a score (return None -> UI omits the number).
        raw = h.get("recall_score", h.get("score", h.get("fts_score")))
        if raw is None:
            return None
        try:
            return round(float(raw), 3)
        except (TypeError, ValueError):
            return None

    try:
        buckets = getattr(result, "buckets", None) or {}
        layers = getattr(result, "hit_layers", None) or {}
        for domain, bucket in buckets.items():
            method = layers.get(domain, "")
            for h in (bucket or []):
                if not isinstance(h, dict) or h.get("kind") == "omitted":
                    continue        # a "not shown" note names hits; it is not one
                text = (h.get("text") or h.get("content") or h.get("body")
                        or h.get("heading") or "")
                score = _score_for(h)
                hits.append({
                    "domain": str(domain),
                    "source": _source_for(str(domain), h)[:200],
                    "score": score if score is not None else 0.0,
                    "has_score": score is not None,
                    "method": str(method),
                    "text": str(text)[:400],
                })
    except Exception as exc:  # noqa: BLE001 — structuring must never break recall
        # Returning the partial list is right for a panel helper on the chat hot
        # path, but it must not be the module's one SILENT degradation: every
        # other leg here logs and counts. A structural change in BucketedRecall
        # would otherwise just shorten the hit list, and the panel would quietly
        # under-report forever (review run_abab234c, LOW #10).
        _record_recall_degraded(f"flatten_exception:{type(exc).__name__}")
        logger.warning(
            "recall hit flattening failed after %d hit(s) — panel will show a "
            "partial list (recall itself is unaffected): %s: %s",
            len(hits), type(exc).__name__, exc,
        )
        return hits
    return hits


def _unified_recall_body(
    query: str, active_project: Optional[tuple[Optional[str], str]] = None,
    prompt_text: str = "", exclude_session_id: Optional[str] = None,
    deadline: Optional[float] = None, followup: bool = False,
) -> tuple[str, Optional[list[dict]]]:
    """Returns (rendered_body_str, structured_hits | None).

    The STRING is the injectable recall block (unchanged — this is what the model
    receives, via render_recall_body). The STRUCTURED list is the SAME turn's real
    hits (source/score/domain) captured for the TSCC panel — extracted from the
    BucketedRecall that recall_all() already produced, BEFORE it was stringified.
    NOT a re-run: it is the exact object that fed the injected block.

    Callers use the STRING for the empty→fallback strangler-fig check (body falsy
    → legacy path); the structured list is purely additive (None on the failure/
    fallback branches).

    C-full (run_ccd1b6c5): the UNIFIED recall path — one recall_all fan-out
    across ALL 5 domains (context_files / ddd / library / session / codeintel),
    rendered to an injectable body. Replaces the runtime path's old 3-leg subset
    (Library+Transcript+Memory) — it now also surfaces DDD + code symbols, and
    shares ONE code path with the CLI (no more edit-twice drift).

    Active-project detection (for the codeintel domain) runs HERE, inside the
    thread — its list_project_names() does a blocking Path.iterdir(), which must
    NOT run on the event loop (Gate-2 M1). Fail-closed: no project → codeintel
    empty, other domains still recall.

    STRANGLER-FIG (R26): this is the NEW path. On ANY failure or empty result it
    returns "" and the caller FALLS BACK to the legacy _recall_for_query 3-leg
    path — recall never degrades to empty because the new path broke. keyword/
    FTS5-only (allow_embed=False); graph-enrich preserved via render.

    DDD is EXCLUDED here on purpose: the project-DDD leg has a DIFFERENT trigger
    lifecycle — it must fire on a keyword-LESS opener via signal-1 (a user editing
    Projects/<X>/ who types "继续"), so it runs on its OWN pre-keyword-gate guard
    (_ddd_injected). Including ddd here too would DOUBLE-inject it for a keyword
    query with an active project. So unified = the 4 keyword-gated domains;
    ddd stays on its independent leg. (run_ccd1b6c5 M2/M3.)
    """
    # Own try/except so ANY unified-path exception returns "" (→ caller falls
    # back to legacy), not just an empty result. Without this, an exception here
    # escapes to the caller's outer handler which proceeds with ZERO recall —
    # breaking the "recall NEVER degrades to empty because the new path broke"
    # invariant the whole strangler-fig leans on. (Gate-2 C1, run_ccd1b6c5.)
    try:
        from .recall_multi import recall_all, render_recall_body, DOMAINS
        # Active project is RESOLVED ONCE by the caller (_maybe_inject_recall via
        # _resolve_active_project, run_6ebf6479) and passed in — this leg no longer
        # re-detects (was: a second blocking iterdir with a DIFFERENT query =
        # extracted keywords, which could disagree with the DDD leg's project).
        project = active_project[0] if active_project else None
        from .recall_filter import code_like_terms
        # The code-symbol leg (~0.1 s) only ever survives the relevance gate for a
        # code-like term, so do not run it otherwise.
        _code = bool(code_like_terms(query.split()))
        non_ddd = tuple(d for d in DOMAINS if d != "ddd" and (d != "codeintel" or _code))
        result = recall_all(query, project=project, domains=non_ddd, allow_embed=False,
                            exclude_session_id=exclude_session_id, deadline=deadline,
                            prompt_text=prompt_text)
        # Relevance gate + "not already in the prompt" + size budget
        # (core/recall_filter.py): ranking alone never says "nothing relevant".
        from .recall_filter import filter_recall
        result = filter_recall(result, prompt_text, followup=followup)
        graph_context = _graph_enrich_recall(query)
        body = render_recall_body(result, project=project, graph_context=graph_context)
        # Structured hits from the SAME result object (the real recalled hits with
        # scores) — for the TSCC panel. Extracted here, before the caller discards
        # `result`. None-safe: on empty body the caller falls back and ignores this.
        structured = _flatten_recall_hits(result)
        return body, structured
    except Exception as exc:  # noqa: BLE001 — fail to "" so caller falls back to legacy
        _record_recall_degraded(f"unified_exception:{type(exc).__name__}")
        logger.warning("unified recall raised (falling back to legacy 3-leg): %s: %s",
                        type(exc).__name__, exc)
        return None, None


def _graph_enrich_recall(query: str) -> str:
    """Extract entities from query, find graph-connected knowledge entries.

    Returns a short context block of related entry IDs or empty string.
    Best-effort: any failure returns empty (never blocks recall).
    """
    try:
        import re as _re
        from .knowledge_graph import load_graph, query_related_entries

        from config import get_swarmws as _get_swarmws
        graph_path = _get_swarmws() / ".context" / ".knowledge-graph.yaml"
        if not graph_path.exists():
            return ""

        # Extract entities from query
        entities: list[str] = []
        # File patterns
        files = _re.findall(r'\b([\w\-/]+\.(?:py|ts|tsx|rs|sh|md|yaml|json))\b', query)
        entities.extend(f for f in files if len(f) >= 6)
        # MEMORY entry IDs
        ids = _re.findall(r'\b(COE\d+|KD\d+|LL\d+|RC\d+|C\d{3,}|E\d{3,})\b', query)
        entities.extend(ids)
        # Module names (snake_case, ≥8 chars)
        modules = _re.findall(r'\b([\w_]{8,})\b', query)
        entities.extend(m for m in modules if "_" in m and not m.startswith("__"))

        if not entities:
            return ""

        rels = load_graph(graph_path)
        if not rels:
            return ""

        related = query_related_entries(rels, entities)
        if not related:
            return ""

        # Format as a brief context hint (not full content — just IDs for awareness)
        lines = ["## Graph-Connected Knowledge"]
        lines.append(f"Entities in query: {', '.join(entities[:5])}")
        lines.append(f"Related via knowledge graph: {', '.join(related[:10])}")
        return "\n".join(lines)
    except Exception:
        logger.debug("_graph_enrich_recall: suppressed exception on a fail-safe path", exc_info=True)
        return ""


def _resolve_active_project(
    editor_file_path: Optional[str], user_message: str,
    session_bound_project: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Detect the active project ONCE, using the FULL user_message (strongest signal).

    Shared by both recall legs (DDD-inject + unified) so detection — a blocking
    Path.iterdir via list_project_names — runs ONCE per recall instead of twice, and
    both legs agree on the SAME project. Before this, the DDD leg detected from
    ``user_message`` and the unified leg from the extracted ``keywords``: two blocking
    detections per turn that could resolve DIFFERENT projects (DDD injecting project
    A's docs while the codeintel/unified leg scoped to project B). Using the full
    user_message is strictly the stronger signal for signal-3 keyword matching (the
    project name survives; keyword-extraction can strip it). Runs off the event loop
    (caller wraps in ``executors.run_in`` — the iterdir must not block the loop,
    Gate-2 M1). Pure: caches nothing itself; the caller records the result on
    ``unit._active_project``.
    """
    from .recall_multi import detect_active_project
    return detect_active_project(editor_file_path=editor_file_path, query=user_message,
                                 session_bound_project=session_bound_project)


def _deliver_block(options: Any, unit: Any, block: str, should_mutate_system_prompt: bool) -> None:
    """Put an injected block where this turn's model will see it: the system
    prompt on a cold spawn, else the per-turn query-prefix stash (a reused
    subprocess discards system_prompt). Appends, so a DDD block and a recall
    block on the same turn both arrive. Every recall-side injection goes through
    here, so this is where the session's injected size (the budget) is counted."""
    unit._recall_chars = _recall_chars_used(unit) + len(block)
    if should_mutate_system_prompt:
        options.system_prompt = (options.system_prompt or "") + block
    else:
        prev = getattr(unit, "_recall_query_block", None)
        prev = prev if isinstance(prev, str) else ""
        unit._recall_query_block = (prev + "\n\n" + block.strip()) if prev else block.strip()


async def _maybe_inject_ddd(
    user_message: str, options: Any, unit: Any, editor_context: Optional[dict],
    should_mutate_system_prompt: bool,
) -> None:
    """Resolve the session's project and, once one is known, run the DDD leg once.

    Until a project is found ``unit._ddd_injected`` stays False, so each later
    message tries again: signal-2 (the bound repo the session touched,
    ``repo_guidance.session_bound_project``) usually only exists after the
    agent's first tool calls. Never raises; DISASTER-capped like the other legs.
    """
    from core import executors, repo_guidance

    editor_fp = editor_context.get("file_path") if editor_context else None
    bound = repo_guidance.session_bound_project(getattr(unit, "_sdk_session_id", None) or "")
    try:
        project = await asyncio.wait_for(
            executors.run_in("io", _resolve_active_project, editor_fp, user_message, bound),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _record_ddd_inject("declined:disaster_timeout")
        logger.error("active-project detection DISASTER TIMEOUT (>%.0fs) — no project",
                     _RECALL_DISASTER_TIMEOUT_S)
        project = (None, "detect_timeout")
    except Exception as exc:  # noqa: BLE001 — detection must never break recall
        logger.warning("active-project detection failed (proceeding, no project): %s: %s",
                       type(exc).__name__, exc)
        project = (None, "detect_error")
    if project[0] is None and getattr(unit, "_active_project", None):
        return              # keep an earlier result; nothing new to inject
    unit._active_project = project
    if project[0] is None:
        if not unit._recall_injected:
            _record_ddd_inject(f"declined:{project[1]}")
        return
    try:
        await asyncio.wait_for(
            executors.run_in(
                "io", _inject_ddd_for_active_project, options, user_message, project,
                lambda block: _deliver_block(options, unit, block, should_mutate_system_prompt)),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _record_ddd_inject("declined:disaster_timeout")
        logger.error("DDD inject DISASTER TIMEOUT (>%.0fs) — proceeding without DDD",
                     _RECALL_DISASTER_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — additive leg must never break recall
        _record_ddd_inject(f"declined:exception:{type(exc).__name__}")
        logger.warning("DDD inject failed (proceeding without DDD): %s: %s",
                       type(exc).__name__, exc)
    finally:
        unit._ddd_injected = True


# Follow-up recall on a topic change. Recall used to run once per session (the
# first keyword-bearing message), so a session that moved on to another subject
# never recalled anything for it. A later message whose query terms overlap the
# last recall's by less than this (Jaccard) is a new topic and recalls again,
# never repeating text already recalled in it, and never injecting the "no
# match" nudge (the agent already has it from the first recall).
#
# Cost is bounded by what is INJECTED, not by how often recall runs: a fixed
# count of 6 follow-ups skipped 1066 of 1771 replayed messages, 693 of which
# would have injected (design 2026-10-26 D2). A follow-up that finds nothing
# relevant injects nothing; the whole session injects at most
# _RECALL_SESSION_CHARS of recalled text (first recall included).
_RECALL_TOPIC_OVERLAP = 0.2
_RECALL_SESSION_CHARS = 24_000
_RECALL_HISTORY_CHARS = 60_000


def _recall_chars_used(unit: Any) -> int:
    """Recalled characters this session has injected so far (0 if never set)."""
    used = getattr(unit, "_recall_chars", 0)
    return used if isinstance(used, int) else 0


def _remember_recall(unit: Any, keywords: str, recalled: str) -> None:
    """Record the topic (query terms) and text of a recall on the unit."""
    try:
        from .recall_filter import query_terms
        unit._recall_terms = frozenset(query_terms(keywords))
        if recalled:
            hist = (getattr(unit, "_recall_history", "") or "") + "\n\n" + recalled
            unit._recall_history = hist[-_RECALL_HISTORY_CHARS:]
    except Exception:  # noqa: BLE001 -- bookkeeping must never break recall
        logger.debug("_remember_recall: suppressed exception", exc_info=True)


def _is_new_topic(prev: "frozenset | None", terms: "frozenset") -> bool:
    if not terms:
        return False
    if not prev:
        return True
    return len(terms & prev) / len(terms | prev) < _RECALL_TOPIC_OVERLAP


def _channel_recall_allowed(channel_context: Optional[dict]) -> bool:
    """Whether this message may get recall (design
    docs/design/2026-10-25-knowledge-delivery-gaps.md §3.3, G4). A desktop tab (no
    channel_context): yes. A channel message: only the OWNER's private chat
    (``is_owner`` from the gateway's single owner check, ``chat_type == "im"``) — a
    reply anywhere else can be read by other people, so the user's own memory must not
    reach it. Judged per message, never latched."""
    if not channel_context:
        return True
    return bool(channel_context.get("is_owner")) and channel_context.get("chat_type") == "im"


async def _maybe_followup_recall(
    user_message: str, options: Any, unit: Any, should_mutate_system_prompt: bool,
) -> Optional[float]:
    """Recall again when a later message starts a new topic (bounded).

    Same unified path, relevance gate and budget as the first recall. The
    prompt text it dedupes against is the system prompt plus everything recalled
    earlier in this session, so a follow-up only adds what is new. Returns the
    leg ms when it ran, else None. Never raises.
    """
    try:
        if getattr(unit, "is_channel_session", False):
            return None
        used = _recall_chars_used(unit)
        if used >= _RECALL_SESSION_CHARS:
            return None
        keywords = _extract_query_keywords(user_message)
        if not keywords:
            return None
        from .recall_filter import is_code_like, query_terms
        terms = frozenset(query_terms(keywords))
        if len(terms) < 2 and not any(is_code_like(t) for t in keywords.split()):
            return None  # one plain word is not a topic
        if not _is_new_topic(getattr(unit, "_recall_terms", None), terms):
            return None
        from core import executors
        seen = (options.system_prompt or "") + "\n" + (getattr(unit, "_recall_history", "") or "")
        project = getattr(unit, "_active_project", None) or (None, "no_signal")
        t0 = time.perf_counter()
        body, _hits = await asyncio.wait_for(
            executors.run_in("io", _unified_recall_body, keywords, project, seen,
                             getattr(unit, "_app_session_id", None) or unit.session_id,
                             time.monotonic() + _RECALL_DISASTER_TIMEOUT_S, True),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
        ms = (time.perf_counter() - t0) * 1000.0
        from .injected_blocks import RECALL_HEADER, RECALL_HINT, RECALL_PROVENANCE
        block = f"{RECALL_HEADER}\n{RECALL_PROVENANCE}{body}{RECALL_HINT}"
        if body and used + len(block) + 2 > _RECALL_SESSION_CHARS:
            # Resource cap, not a relevance judgment: these hits are not injected,
            # and the session stops follow-up recall from here on (no more runs).
            unit._recall_chars = _RECALL_SESSION_CHARS
            _record_recall_degraded("budget_stopped")
            logger.info("follow-up recall over the session budget (%d + %d > %d chars); "
                        "not injected | keywords=%s", used, len(block),
                        _RECALL_SESSION_CHARS, keywords[:80])
            return ms
        unit._recall_followups = (getattr(unit, "_recall_followups", 0) or 0) + 1
        _remember_recall(unit, keywords, body or "")
        try:
            from core.recall_metrics import record_recall_metric
            record_recall_metric("session_followup", ("followup",), ms,
                                 hit_count=1 if body else 0,
                                 degraded_reason=None if body else "empty_with_keywords")
        except Exception:  # noqa: BLE001
            logger.debug("followup recall metric failed", exc_info=True)
        if not body:
            logger.info("follow-up recall (new topic) matched nothing | keywords=%s | %.0fms",
                        keywords[:80], ms)
            return ms
        _deliver_block(options, unit, f"\n\n{block}", should_mutate_system_prompt)
        logger.info("follow-up recall (new topic #%d) injected: +%d chars into %s | "
                    "keywords=%s | %.0fms", unit._recall_followups, len(body),
                    "system prompt" if should_mutate_system_prompt else "query (warm-reuse)",
                    keywords[:80], ms)
        return ms
    except asyncio.TimeoutError:
        _record_recall_degraded("followup_timeout")
        logger.warning("follow-up recall timed out (>%.0fs); turn proceeds without it",
                       _RECALL_DISASTER_TIMEOUT_S)
        return None
    except Exception as exc:  # noqa: BLE001 -- recall must never reach the send path
        _record_recall_degraded(f"followup_exception:{type(exc).__name__}")
        logger.warning("follow-up recall failed (turn proceeds): %s: %s",
                       type(exc).__name__, exc)
        return None


async def _maybe_inject_recall(
    user_message: str,
    options: Any,
    unit: SessionUnit,
    editor_context: Optional[dict] = None,
    should_mutate_system_prompt: bool = True,
    channel_context: Optional[dict] = None,
) -> Optional[float]:
    """Top-level FAULT-ISOLATION wrapper (A): recall can NEVER crash the builder.

    ``channel_context``: a channel message recalls only per ``_channel_recall_allowed``
    (G4); anything else returns None without touching the once-per-session guard.

    ``should_mutate_system_prompt`` (阶段二 prompt-builder 两分, default True =
    today's behavior): the recall DESTINATION. True (COLD-spawn turn) → append the
    recall block to ``options.system_prompt`` (the spawn carries it). False
    (WARM-reuse turn — the reused subprocess DISCARDS system_prompt) → do NOT touch
    system_prompt; instead stash the block on ``unit._recall_query_block`` for the
    caller to prefix onto ``query_content`` via ``_prepend_dynamic_context_to_query``.
    Passed ``not _will_reuse_live`` by send() — the exact warm-reuse discriminator
    (the complement of the poison-guard recycle). This prevents a silent recall DROP
    on a warm turn (system_prompt written but never sent) without double-injecting.

    The system-prompt builder commits the core context files BEFORE the router
    appends recall (prompt_builder core-commit-first, run_e47c1cfb), so a recall
    failure must degrade to "no recall block", NEVER propagate to the send path.
    The inner body guards its DDD block and its recall leg individually, but the
    BETWEEN-block code (shared detection, keyword extraction, base-token estimate)
    was unguarded — an exception there escaped to send(). This wrapper closes that:
    ANY escape → loud log + degraded metric + LATCH the once-per-session guard (so
    the broken path is not re-run every turn, A2) + return None (recall reported as
    "did not run"; core context untouched). Loud-on-catch preserves W5 (never a
    silent dead recall).
    """
    try:
        return await _maybe_inject_recall_inner(
            user_message=user_message, options=options, unit=unit,
            editor_context=editor_context,
            should_mutate_system_prompt=should_mutate_system_prompt,
            channel_context=channel_context,
        )
    except Exception as exc:  # noqa: BLE001 — recall must never reach the send path
        _record_recall_degraded(f"toplevel_exception:{type(exc).__name__}")
        logger.warning(
            "Recall injection TOP-LEVEL failure (proceeding without recall, core "
            "context intact): %s: %s", type(exc).__name__, exc,
        )
        # Latch so a systematically-failing path (e.g. a broken keyword extractor)
        # is not re-run every single turn. Best-effort — a MagicMock/None unit in a
        # degenerate call must not turn a recall failure into a wrapper failure.
        try:
            unit._recall_injected = True
        except Exception:  # noqa: BLE001
            logger.debug("_maybe_inject_recall: suppressed exception on a fail-safe path", exc_info=True)
            pass
        return None


async def _maybe_inject_recall_inner(
    user_message: str,
    options: Any,
    unit: SessionUnit,
    editor_context: Optional[dict] = None,
    should_mutate_system_prompt: bool = True,
    channel_context: Optional[dict] = None,
) -> Optional[float]:
    """Augment the system prompt with recalled knowledge from the user's query.

    阶段二: when ``should_mutate_system_prompt`` is False (warm-reuse turn), the
    recall block is stashed on ``unit._recall_query_block`` instead of appended to
    ``options.system_prompt`` (which a reused subprocess discards). See the wrapper
    ``_maybe_inject_recall`` docstring for the full rationale.

    CORRECTNESS-FIRST (run_4d06640b): runs the recall leg to COMPLETION
    synchronously before generating, so the answer is built on the FULL brain —
    not a latency-trimmed subset. Priority is accurate + capable, NOT first-token
    speed (user directive). Measured ~1-3s — that latency is ACCEPTED.

    PURE-FILESYSTEM: recall is keyword/FTS5/BM25 ONLY — the vector/Bedrock-embed
    leg was fully removed 2026-08-14 (PRI11). The "right idea, different words"
    blind spot is covered by AGENTIC re-search (the footer hint nudges the agent
    to re-grep with synonyms), NOT by an embedding leg.

    Runs ONCE per session on the first user message (``_recall_injected`` guard).

    Guard rails:
      - Once-per-session flag on ``unit._recall_injected``
      - Channel sessions excluded (quick exchanges don't need deep recall)
      - DISASTER timeout (``_RECALL_DISASTER_TIMEOUT_S``): bounds a recall code-HANG
        only (bug / pathological query) — NOT a daily latency judge. The recall DB
        connection uses a SHORTER busy_timeout so a sqlite lock can't out-hang the
        cap (asyncio wait_for cannot cancel a to_thread C-hang — the cap would be
        theater otherwise; run_4d06640b B3).
      - LOUD on degradation (W5): timeout → logger.error + metric; an internal
        failure inside _recall_for_query → logger.warning + metric. NEVER silent —
        silent empty recall is the exact dead-path class that hid for months.
    """
    # Returns the recall-leg wall-clock (ms) IF recall actually RAN this turn, else
    # None. None means "no fresh recall this turn" — recall runs once per session
    # (_recall_injected guard) and never for channels, so turns 2+ get None. The
    # TTFT probe uses this to label recall as n/a rather than fake a stale/0 value
    # (Gate-1: attributing turn-1 recall to a later turn corrupts the residual math).
    if not _channel_recall_allowed(channel_context):
        return None              # G4: not the owner's private chat; nothing latched
    # Channel sessions: skip recall, set flag
    if unit.is_channel_session:
        unit._recall_injected = True
        return None

    # Dedicated pools for the recall/DDD hot-path (run_c8ad52f8) — keep this
    # session-init blocking work OFF the shared default ThreadPoolExecutor so a
    # multi-session burst cannot starve the readiness sampler → false offline.
    from core import executors

    # ── Project + DDD leg: on every message until a project is found (D3) ──
    # Runs BEFORE the keyword gate: signal-1 (editor path) and signal-2 (bound
    # repo) need no query keywords, so a user who opens with "继续" still gets the
    # project. Until a project is found the leg re-checks each message — signal-2
    # usually appears after the agent's first tool calls, not before message 1.
    if not getattr(unit, "_ddd_injected", False):
        await _maybe_inject_ddd(user_message, options, unit, editor_context,
                                should_mutate_system_prompt)
    _active_project = getattr(unit, "_active_project", None) or (None, "no_signal")

    if unit._recall_injected:
        # The first recall already ran; a later message on a NEW topic gets a
        # bounded follow-up recall (see _maybe_followup_recall).
        return await _maybe_followup_recall(
            user_message, options, unit, should_mutate_system_prompt)

    # Extract keywords — skip if message too short/generic.
    #
    # recall#5 fix (run_a16d61ad): a zero-keyword OPENER must NOT permanently
    # disable recall for the whole session. Previously this branch set
    # _recall_injected=True, so an opening "hi" / "继续" / "ok" (which yields no
    # keywords) burned the once-per-session guard before the user ever asked a
    # substantive question — recall was then dead for the entire session. We now
    # leave the guard FALSE here: the NEXT message gets another chance to extract
    # keywords and run recall once. No loop — the guard is still set True on any
    # successful/empty MATCH branch and for channel sessions, so recall still runs
    # at most once per session, just on the first SUBSTANTIVE message rather than
    # the first message unconditionally.
    keywords = _extract_query_keywords(user_message)
    if not keywords:
        # Bounded retry (recall#5 cap): leave the guard open so a later
        # substantive message can recall — but after _RECALL_KEYWORD_MISS_CAP
        # keyword-less turns, latch it closed so a session that NEVER yields
        # keywords stops re-running the regex extractor every single turn.
        unit._recall_keyword_misses = getattr(unit, "_recall_keyword_misses", 0) + 1
        if unit._recall_keyword_misses >= _RECALL_KEYWORD_MISS_CAP:
            unit._recall_injected = True
        return None

    # Instrumentation (READ-line observability, design §2 P2/P3): measure the
    # recall leg wall-clock AND the base system-prompt token size BEFORE injection,
    # so the post-injection log can report base + recall = TOTAL first-message
    # context + how long the recall leg took. Uses the single CJK-aware estimator
    # (estimate_tokens) — NOT the old `len//4` — so the number matches the
    # assembly-log estimator (design §1: kill the dual-estimator divergence).
    from .context_directory_loader import ContextDirectoryLoader
    _base_tok = ContextDirectoryLoader.estimate_tokens(options.system_prompt or "")
    _t_recall_start = time.perf_counter()
    _recall_ms: Optional[float] = None  # set once the recall leg completes (any outcome)

    try:
        # C-full (run_ccd1b6c5): try the UNIFIED 5-domain path first. STRANGLER-
        # FIG — on empty/failure we fall back to the legacy 3-leg _recall_for_query
        # so recall NEVER degrades to empty because the new path broke.
        # keyword/FTS5-only (allow_embed=False); disaster cap only fires on a hang.
        # The unified leg consumes the SHARED (project, signal) resolved once at the
        # top of this function (run_6ebf6479) — it no longer re-detects; that removes
        # the redundant iterdir AND the DDD-leg/unified-leg project-disagreement.
        # _unified_recall_body now returns (body_str, structured_hits|None). The
        # empty→fallback strangler-fig check stays on the BODY string (unchanged
        # semantics); structured is additive for the TSCC panel (None on fallback).
        _unified_out = await asyncio.wait_for(
            # 'io' pool, not the default one (run_c8ad52f8): FTS5/sqlite recall on
            # the session-init hot path — must not compete for a default-pool worker
            # with bulk work (which would starve the readiness sampler → false offline).
            executors.run_in("io", _unified_recall_body, keywords, _active_project,
                             options.system_prompt or "",
                             getattr(unit, "_app_session_id", None) or unit.session_id,
                             time.monotonic() + _RECALL_DISASTER_TIMEOUT_S),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
        recalled, _structured_hits = _unified_out
        if recalled is None:
            # Fallback to legacy path ONLY when the unified path FAILED. An empty
            # body is a real answer ("nothing relevant passed the relevance gate")
            # and must not be replaced by the ungated legacy legs. Legacy returns
            # a bare string and has no structured hits.
            _record_recall_degraded("unified_failed_fallback_legacy")
            recalled = await asyncio.wait_for(
                executors.run_in("io", _recall_for_query, keywords, _RECALL_MAX_TOKENS, False),
                timeout=_RECALL_DISASTER_TIMEOUT_S,
            )
            _structured_hits = None
        _recall_ms = (time.perf_counter() - _t_recall_start) * 1000.0
        # Recall metrics substrate (run_40091f5c): record ONE sample for this
        # session-prompt recall — total latency + whether it hit. Fire-and-forget
        # (never raises into recall). Flushed to recall_metrics every ~5 min.
        try:
            from core.recall_metrics import record_recall_metric
            from core.recall_multi import DOMAINS
            # Non-ddd unified fan-out (ddd runs on its own path, excluded above).
            _sp_domains = tuple(d for d in DOMAINS if d != "ddd")
            record_recall_metric(
                "session_prompt", _sp_domains,
                _recall_ms, hit_count=(1 if recalled else 0),
                degraded_reason=(None if recalled else "empty_with_keywords"),
            )
        except Exception:  # noqa: BLE001 — metric must never break recall
            logger.debug("_maybe_inject_recall_inner: suppressed exception on a fail-safe path", exc_info=True)
            pass
        if recalled:
            # Append to this options instance only — safe even if options is
            # rebuilt on retry (system_prompt is a plain str, so += makes a new str).
            # Agentic re-search hint (pure-filesystem recall design §3.4 / DoD6):
            # keyword recall has NO vector/semantic leg (by design) — its blind
            # spot is "right idea, different words" (e.g. query "resume 慢" vs
            # stored "cold-start 延迟"). The replacement for vector is NOT an FTS5
            # synonym dictionary, it is AGENTIC re-search: the agent has Read/Grep
            # and can re-query with synonyms / broader terms if the recall below
            # looks thin. This footer makes that affordance explicit (cheap, ~30
            # tokens) so a weak keyword hit doesn't silently become a miss.
            from .injected_blocks import RECALL_HINT as _agentic_hint
            # Provenance prefix (R3 §3.5): mark recalled material as RETRIEVED,
            # not the agent's own reasoning and not new user input. Without this
            # boundary the model can absorb keyword-matched history as if it
            # derived it this turn (a confabulation surface — observed live).
            # Pure text, zero logic cost; the agent treats it as a lead to verify.
            from .injected_blocks import RECALL_HEADER, RECALL_PROVENANCE as _provenance
            _recall_block = (
                f"{RECALL_HEADER}\n{_provenance}{recalled}{_agentic_hint}"
            )
            # COLD-spawn: system_prompt carries it; WARM-reuse: the reused
            # subprocess discards system_prompt, so it goes to the query-prefix
            # stash (阶段二), after any DDD block this turn already put there.
            _deliver_block(options, unit, f"\n\n{_recall_block}", should_mutate_system_prompt)
            _remember_recall(unit, keywords, recalled)
            # Observability (loud-on-success counterpart to loud-on-degradation):
            # recall succeeds SILENTLY otherwise, so "0 recall lines in the log"
            # was ambiguous between "working" and "never ran". This makes a live
            # injection visible. INFO (not DEBUG) — the daemon file handler drops
            # DEBUG.
            #
            # Token count uses the SINGLE CJK-aware estimator (estimate_tokens),
            # NOT the old `len//4` — so this number is consistent with the
            # assembly-log estimator (design §1: dual-estimator divergence killed).
            # The "first-msg total context" line answers the question the prior
            # logging could not: base (11 files + briefing) + recall = the REAL
            # system-prompt size the model sees on message #1, and how long the
            # recall leg took (design §2 P2/P3).
            _recall_tok = ContextDirectoryLoader.estimate_tokens(recalled)
            # TSCC recall snapshot (read-only panel) — fire-and-forget stash of the
            # STRUCTURED hits (source/score/domain) that were ACTUALLY recalled this
            # turn. NO new recall computation and NO re-run: _structured_hits was
            # extracted from the SAME BucketedRecall that produced the injected block
            # above — it is the real session state, not a simulation. `body` is kept
            # ONLY as a fallback for rendering when structured is None (legacy path).
            # O(1) reference assignment (list already built); guarded so a panel
            # stash can never raise into the chat recall leg.
            try:
                unit._recall_snapshot = {
                    "ran": True,
                    "hits": _structured_hits or [],
                    "body": recalled if not _structured_hits else "",
                    "tokens": _recall_tok,
                    "latency_ms": _recall_ms or 0.0,
                    "keywords": list(keywords[:32]),
                }
            except Exception:  # noqa: BLE001 — snapshot must never break recall
                logger.debug("_maybe_inject_recall_inner: suppressed exception on a fail-safe path", exc_info=True)
                pass
            logger.info(
                "recall injected: +%d chars (~%d tok) into %s | keywords=%s",
                len(recalled), _recall_tok,
                # 阶段二: recall rides system_prompt on a cold-spawn turn but the
                # query_content prefix on a warm-reuse turn — log the real target.
                "system prompt" if should_mutate_system_prompt else "query (warm-reuse)",
                keywords[:80],
            )
            logger.info(
                "first-msg context assembled: base=%d tok + recall=%d tok = "
                "TOTAL %d tok | recall_leg=%.0fms",
                _base_tok, _recall_tok, _base_tok + _recall_tok, _recall_ms,
            )
        else:
            # Keyword recall RAN successfully but matched NOTHING. This is not an
            # exception (those are counted inside _recall_for_query), but after the
            # vector leg was retired (pure-filesystem) it IS the load-bearing
            # failure mode: "right idea, different words" now silently returns zero.
            #
            # recall#3 fix (run_a16d61ad): COUNT it. Previously this was INFO-logged
            # only — invisible to any metric — so a recall path that systematically
            # whiffs on synonyms would degrade silently (the exact dead-path class
            # vector used to cover). The counter makes empty-with-keywords a tracked
            # degradation signal, distinct from disaster_timeout / inject_exception.
            _record_recall_degraded("empty_with_keywords")
            # Inject an agentic re-search nudge (DoD6) so a keyword miss prompts the
            # agent to try synonyms itself rather than silently proceeding with zero
            # recall (the keyword-only blind spot).
            from .injected_blocks import RECALL_MISS as _miss_block
            _remember_recall(unit, keywords, "")
            _deliver_block(options, unit, f"\n\n{_miss_block}", should_mutate_system_prompt)
            # Stash a snapshot for the TSCC panel on THIS branch too. Recall ran
            # — it just matched nothing. Without a snapshot the endpoint returns
            # its default (ran=False) and the panel says "no recall this session",
            # which is a different and wrong statement: it hides a systematic
            # keyword miss behind "the feature didn't run" (review run_abab234c,
            # MED #6). ran=True with zero hits is the honest report, and it is
            # what makes the panel's "ran but matched nothing" branch reachable.
            # tokens=0 because nothing was recalled; the re-search nudge appended
            # above is prompt text, not recalled knowledge.
            try:
                unit._recall_snapshot = {
                    "ran": True,
                    "hits": [],
                    "body": "",
                    "tokens": 0,
                    "latency_ms": _recall_ms or 0.0,
                    "keywords": list(keywords[:32]),
                }
            except Exception:  # noqa: BLE001 — snapshot must never break recall
                logger.debug("_maybe_inject_recall_inner: suppressed exception on a fail-safe path", exc_info=True)
                pass
            logger.info(
                "recall ran but matched nothing | keywords=%s | "
                "first-msg context: base=%d tok (no recall) | recall_leg=%.0fms",
                keywords[:80], _base_tok, _recall_ms,
            )
    except asyncio.TimeoutError:
        # The leg (~0.3-0.5 s measured, cold or warm) did not finish within the
        # cap. Every observed case (7, 2026-10-12..22) was process contention, not
        # a hang: the event loop had stalled 3-4 s, prompt assembly took 6-18 s
        # instead of ~1 s, and 20+ threads were busy (dashboard panel scans, cold
        # resumes after a restart) -- the recall thread could not get the GIL.
        # The leg's deadline makes the abandoned worker skip its remaining legs.
        # LOUD and counted: the answer goes out without recall.
        import threading as _threading
        _record_recall_degraded("disaster_timeout")
        try:
            from core.recall_metrics import record_recall_metric
            record_recall_metric(
                "session_prompt", ("timeout",), _RECALL_DISASTER_TIMEOUT_S * 1000.0,
                hit_count=0, degraded_reason="disaster_timeout")
        except Exception:  # noqa: BLE001 — metric must never break recall
            logger.debug("recall timeout metric failed", exc_info=True)
        logger.error(
            "RECALL TIMEOUT (>%.0fs): the recall leg did not finish -- usually CPU/GIL "
            "contention in this process (threads=%d), not a hang; answer proceeds "
            "WITHOUT recall. keywords=%s",
            _RECALL_DISASTER_TIMEOUT_S, _threading.active_count(), keywords[:80],
        )
    except Exception as exc:
        _record_recall_degraded(f"inject_exception:{type(exc).__name__}")
        logger.warning("Recall injection failed (proceeding without recall): %s: %s",
                        type(exc).__name__, exc)

    # DDD injection already ran above (own _ddd_injected guard, before the
    # keyword gate — Gate-2 HIGH fix). Latch keyword-recall's guard.
    unit._recall_injected = True
    # _recall_ms is the leg wall-clock when recall RAN this turn (success or
    # empty-match); it stays None on the disaster-timeout / exception paths (no
    # meaningful leg time to attribute). The caller labels None as "recall=n/a".
    return _recall_ms


# Event types that are the first USER-VISIBLE content token of a turn. thinking_delta
# is included deliberately: under Opus adaptive thinking a turn's first streamed token
# is usually a thinking_delta (streaming_orchestrator.py:804), so a text-only TTFT
# would be systematically LATE (Gate-0 skeptic). NOT included: *_start / session_start
# / assistant / result / tool_use / content_block_stop — none carry a visible token.
_TTFT_FIRST_CONTENT_TYPES = ("text_delta", "thinking_delta")


def _format_ttft_line(
    event_type: str,
    already_recorded: bool,
    ttft_ms: float,
    slot_ms: float,
    recall_ms: Optional[float],
    recall_ran_this_turn: bool,
    retry_count: int,
    sw_overhead_ms: Optional[float] = None,
    spawn_ms: Optional[float] = None,
) -> Optional[str]:
    """Pure decision + formatter for the end-to-end TTFT probe (observability-only).

    Returns the one-line ``TTFT=`` log string when ``event_type`` is the FIRST
    user-visible content token of the turn (and it has not already been recorded),
    else ``None``. This function ONLY formats a string — it never mutates state, so
    the caller owns the once-per-turn latch (``already_recorded``).

    Segments (all measured from the ``run_conversation`` entry t0):
      - ``ttft_ms``  — entry → this first content delta (the headline number)
      - ``slot_ms``  — time spent in slot-acquire/queue (0 when a slot was free)
      - ``recall_ms``— the recall leg IF it ran THIS turn (else labelled ``n/a``:
        recall runs once per session + never for channels, so turn-2+ has no fresh
        recall — showing a stale/0 value would mislead, Gate-1). When present it is
        a SUB-annotation *inside* ``pre_send`` (recall is one of the pre-send legs),
        NOT a peer residual.
      - ``pre_send_ms`` / ``send+infer`` — the router/model boundary split
        (run_332ccfd1). ``sw_overhead_ms`` is the DIRECTLY MEASURED wall time from
        t0 to just before ``unit.send()`` — i.e. ALL router-side per-turn work:
        slot-acquire, user-message DB persist, cold-resume DB reads, prompt
        assembly (``build_options``), multimodal conversion, and recall injection.
        On a clean single-spawn turn (``retry_count == 0``)
        ``send+infer = ttft − pre_send − spawn`` is then the SDK-send + Bedrock
        first-token span. Emitted on EVERY turn (incl. warm ``recall=n/a``)
        whenever ``sw_overhead_ms`` was measured — the case the old recall-only
        residual left opaque. It is a DIRECT measurement, not the old
        ``ttft − slot − recall`` computed residual (that lumped prompt-build+DB into
        the model span and contradicted itself on recall-ran turns — Gate-1 BLOCK).
        ``sw_overhead_ms=None`` (not measured) → the split is omitted (legacy shape,
        backward-compatible with callers that don't pass it).
      - ``spawn`` (run_8149838f) — the SDK ``__aenter__`` subprocess handshake
        (~13-20s measured), relayed from ``session_unit._last_turn_spawn_ms``. On a
        COLD turn the spawn runs INSIDE ``unit.send()`` — AFTER the ``pre_send``
        boundary — so without this subtraction it silently inflated ``send+infer``
        and read as "Bedrock is slow" (TECH.md S2: spawn is the SDK handshake, NOT
        model inference). Surfaced as its own ``spawn=<ms>`` segment ONLY when
        ``spawn_ms`` is a real (>0) measurement AND ``retry_count == 0``; a warm
        turn passes ``None`` (no spawn ran) → no segment, and ``send+infer`` reverts
        to ``ttft − pre_send``. On a RETRIED turn (``retry_count > 0``) the spawn
        subtraction is DELIBERATELY skipped: ``ttft`` then spans multiple failed
        spawns + backoff sleeps but ``_last_turn_spawn_ms`` holds only the LAST
        spawn, so subtracting it would over-attribute (and could render negative) —
        the ``retries=`` segment already flags that turn's raw ttft as meaningless,
        and ``send+infer`` inherits that caveat rather than making a false
        single-spawn claim (Gate-2, run_8149838f).
      - ``retries``  — surfaced ONLY when >0: a retried turn's ttft_ms includes
        5-15s backoff + ``--resume`` respawn, so the raw number is meaningless
        without this note (Gate-1).
    """
    if already_recorded or event_type not in _TTFT_FIRST_CONTENT_TYPES:
        return None
    if recall_ran_this_turn and recall_ms is not None:
        recall_seg = f"recall={recall_ms:.0f}ms"  # sub-leg inside pre_send
    else:
        recall_seg = "recall=n/a"  # did not run this turn — NOT a real 0ms
    if sw_overhead_ms is not None:
        # Direct-measured router/model split. On a clean single-spawn turn
        # send+infer is the residual of measured boundaries (pre_send, and spawn on
        # a cold turn), reflecting the SDK-send + Bedrock first-token span.
        # spawn is subtracted ONLY when a real (>0) handshake was measured (cold
        # turn) AND retry_count == 0: on a retried turn ttft spans multiple failed
        # spawns + backoff but _last_turn_spawn_ms holds only the LAST, so
        # subtracting it would over-attribute / render negative (Gate-2). A warm
        # turn (spawn_ms None/0) also leaves send+infer = ttft − pre_send.
        _spawn = (
            spawn_ms
            if (spawn_ms is not None and spawn_ms > 0 and not retry_count)
            else 0.0
        )
        spawn_seg = f" spawn={_spawn:.0f}ms" if _spawn > 0 else ""
        split_seg = (
            f" pre_send={sw_overhead_ms:.0f}ms"
            f"{spawn_seg}"
            f" send+infer={ttft_ms - sw_overhead_ms - _spawn:.0f}ms"
        )
    else:
        split_seg = ""
    retry_seg = f" retries={retry_count}" if retry_count else ""
    return (
        f"TTFT={ttft_ms:.0f}ms (first-token={event_type}) | "
        f"slot={slot_ms:.0f}ms {recall_seg}{split_seg}{retry_seg}"
    )


def _best_entry_of_section(hit: dict, base, docs_text: dict, keywords: str) -> dict:
    """A section-level hit (no ``content``) as its best-matching unit for *keywords*,
    so the chat pointer names an entry and its line, not a section heading (knowledge
    addressing §2.6: SwarmAI's TECH.md "Architecture" is ~100K characters). The units
    are split exactly as ``core.addressing`` splits them, so the line and the id agree
    with ``knowledge get``. Unchanged when the doc is not known or nothing matches."""
    doc, sec = str(hit.get("doc") or ""), str(hit.get("section") or "")
    text = (docs_text or {}).get(doc)
    if base is None or text is None or sec.startswith("("):
        return hit
    try:
        from core import addressing, memory_index
        from core.ddd_paths import ddd_path
        path = ddd_path(base, doc)
        units = [u for u in addressing._file_units("doc", doc, Path(path), Path(base), text)
                 if u.section == sec and u.live]
        if not units:
            return hit
        raw = memory_index._bm25_scores(keywords, {str(i): u.text for i, u in enumerate(units)})
        best = max(raw.items(), key=lambda kv: kv[1], default=None)   # only scores > 0
        if best is None:
            return hit
        return {**hit, "content": units[int(best[0])].text}
    except Exception:  # noqa: BLE001 -- a pointer at the heading is still a pointer
        logger.debug("chat DDD: best entry of %s § %s not found", doc, sec, exc_info=True)
        return hit


def _ddd_hit_line(project: str, base, docs_text: dict, hit: dict) -> str:
    """One chat DDD hit as a pointer: an entry by its own title (its first line, no
    longer than 160 characters) and where it is, a section by its heading line. Falls
    back to ``doc § section`` when the doc's file is not known."""
    from core.ddd_paths import ddd_path
    from core.recall_filter import unit_title
    from core.recall_multi import _locator
    doc, sec = str(hit.get("doc") or "?"), str(hit.get("section") or "")
    content = str(hit.get("content") or "")
    text = docs_text.get(doc) if docs_text else None
    if base is None or text is None:
        return f"- **{doc}** § {sec}"
    where = _locator(doc, ddd_path(base, doc), text)(sec, content)
    title = unit_title(content) if content.strip() else ""
    if not title and content.strip():
        from core.addressing import Unit
        title = Unit.first_line(content)
    title = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", title)     # the entry's own list marker
    if title:
        return f"- {title[:160]} — {where}"
    return f"- § {sec} — {where}"


def _inject_ddd_for_active_project(
    options: Any,
    user_message: str,
    active_project: Optional[tuple[Optional[str], str]],
    deliver: Optional[Callable[[str], None]] = None,
) -> None:
    """Inject the active project's top DDD sections. FAIL-CLOSED.

    The active project is RESOLVED ONCE by the caller (_maybe_inject_recall via
    _resolve_active_project, run_6ebf6479) and passed in as (project, signal) — this
    leg no longer re-detects (was: its own detect_active_project(query=user_message),
    a second blocking iterdir that could resolve a DIFFERENT project than the unified
    leg's keyword-based detection). Runs in a thread (recall_all does blocking fs
    reads). On a non-confident detection, records a declined-reason and injects
    NOTHING (Gate-2 L1: the counter distinguishes 'correctly declined' from 'detector
    broken').

    ``deliver(block)`` places the block (default: append to
    ``options.system_prompt``). A warm-reuse turn must pass one that writes the
    query-prefix stash instead: the reused subprocess discards system_prompt.
    """
    if deliver is None:
        def deliver(block: str) -> None:
            options.system_prompt = (options.system_prompt or "") + block
    from .recall_multi import (_load_ddd_docs_text, _parse_docs_sections, recall_all)
    from .recall_filter import _coverage, _required, query_terms

    project, signal = active_project if active_project else (None, "no_detection")
    if not project:
        _record_ddd_inject(f"declined:{signal}")
        return

    # Score on the extracted keywords, not the raw message, and keep only
    # sections that actually contain enough of them (the recall relevance rule,
    # core/recall_filter.py). The section scores are normalized to the best
    # match, so the top three were "relevant" for ANY message: "继续" pointed at
    # Design Philosophy and What Failed. A message with no keywords ("继续")
    # gets no sections, only the project pointer below -- that is what the
    # editor-path signal is for.
    keywords = _extract_query_keywords(user_message)
    _ddd_t0 = time.perf_counter()
    ddd_hits: list = []
    base, docs_text = None, {}      # read below when there are keywords; the no-hit pointer uses base
    if keywords:
        result = recall_all(keywords, project=project, domains=("ddd",))
        raw_hits = result.buckets.get("ddd", []) if hasattr(result, "buckets") else []
        base, docs_text = _load_ddd_docs_text(project)
        sections = _parse_docs_sections(docs_text) if docs_text else {}
        terms = query_terms(keywords)
        need = _required(terms)
        for h in raw_hits:
            doc, sec = str(h.get("doc") or ""), str(h.get("section") or "")
            text = str(h.get("content") or "") or str((sections.get(doc) or {}).get(sec) or "")
            if not text and base is not None and sec == "(whole file)":
                try:
                    text = (base / doc).read_text(encoding="utf-8")[:65536]
                except OSError:
                    text = ""
            if _coverage(f"{sec}\n{text}", terms) >= need:
                ddd_hits.append(h if h.get("content") else
                                _best_entry_of_section(h, base, docs_text, keywords))
    _ddd_ms = (time.perf_counter() - _ddd_t0) * 1000.0
    # Recall metrics (run_40091f5c): the ddd leg is a REAL per-prompt daemon recall
    # on its OWN path (excluded from the session_prompt sample) — measure it too, or
    # daemon recall latency is only half-covered. Runs in the io-thread; the metric's
    # threading.Lock makes that safe. Fire-and-forget.
    try:
        from core.recall_metrics import record_recall_metric
        record_recall_metric("session_ddd", ("ddd",), _ddd_ms,
                             hit_count=len(ddd_hits))
    except Exception:  # noqa: BLE001
        logger.debug("_inject_ddd_for_active_project: suppressed exception on a fail-safe path", exc_info=True)
        pass
    if not ddd_hits:
        if signal == "signal2_bound_repo":
            # repo_guidance already told the agent this repo's DDD documents when
            # it touched the repo; a second pointer adds nothing.
            _record_ddd_inject("pointer_from_repo_guidance")
            return
        # The project is known (the user is in it) but no section is about this
        # message: say where its brain is, nothing more (~250 chars).
        if base is None:
            from core.addressing import project_root
            base = project_root(project)
        root = base if base is not None else f"Projects/{project}"
        deliver((
            f"\n\n## Project DDD — [DDD:{project}]\n"
            f"> **[DDD:{project}]** You are working in project {project} "
            f"(detected via {signal}). Its DDD is at `{root}` (layout: SWARMAI.md, My "
            f"Workspace); before a project-specific decision, look it up: "
            f"`\"$SWARM_BACKEND_BIN\" knowledge find --project {project} --query \"<words>\"`.\n"))
        _record_ddd_inject("injected_pointer_only")
        logger.info("DDD detected project=%s (%s): no section matched the message "
                    "-- injected the project pointer only", project, signal)
        return

    # Render the hits with a [DDD:<project>] provenance prefix (distinct token from
    # [RECALLED] — E1/E4 assert on THIS, not string length). Each line says where to
    # read: an entry by its title and file:line, a section by its heading line
    # (knowledge addressing §2.6 -- a bare "TECH.md § Architecture" named a 110K
    # section in a file the agent's cwd may not even contain).
    lines = [_ddd_hit_line(project, base, docs_text, h) for h in ddd_hits]
    block = (
        f"\n\n## Project DDD — [DDD:{project}]\n"
        f"> **[DDD:{project}]** Retrieved from this project's DDD docs "
        f"(detected via {signal}). Read the ones that bear on this before acting "
        f"(at that line, or `\"$SWARM_BACKEND_BIN\" knowledge get --project {project} <path:line>`).\n\n"
        + "\n".join(lines)
    )
    deliver(block)
    _record_ddd_inject("injected")
    logger.info("DDD injected: project=%s signal=%s sections=%d",
                project, signal, len(ddd_hits))

    # Access-decay signal (run_644bfea6): record which ENTRY-level DDD hits we
    # actually surfaced, so ddd_orchestrator's decay engine can keep genuinely-
    # used lessons alive instead of decaying them on age alone. Only entry hits
    # carry `content` (section-pointer hits don't); the content string here
    # INCLUDES the <!-- --> metadata line — entry_anchor_text strips it so the
    # anchor matches the read side (EntryMetadata.raw_text, metadata-free).
    # best-effort: recall must never be blocked by usage bookkeeping.
    try:
        from datetime import date as _date
        from core.ddd_usage import entry_anchor_text, record_ddd_hit
        _today = _date.today()
        for h in ddd_hits:
            content = h.get("content")
            if not content:
                continue
            # entry_anchor_text returns "" for non-trackable (non-bold) lines;
            # record_ddd_hit no-ops on "". Anchor IS the key (no doc/section).
            record_ddd_hit(project, entry_anchor_text(content), _today)
    except Exception:  # noqa: BLE001 — best-effort, never block the recall path
        logger.debug("_inject_ddd_for_active_project: suppressed exception on a fail-safe path", exc_info=True)
        pass


def _get_access_hint(ext: str, filename: str) -> str:
    """Return file-type-specific guidance for how the agent should access the file."""
    ext_lower = ext.lower()
    if ext_lower == ".pdf":
        return "use Read tool to read this PDF"
    elif ext_lower in (".pptx", ".ppt"):
        return "use /s_pptx skill to extract slides and content"
    elif ext_lower in (".docx", ".doc"):
        return "use /s_docx skill to extract text and content"
    elif ext_lower in (".xlsx", ".xls"):
        return "use /s_xlsx skill to extract spreadsheet data"
    elif ext_lower in (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"):
        return "use /s_whisper-transcribe skill to transcribe audio to text"
    elif ext_lower in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return "video file — extract audio first with ffmpeg, then transcribe"
    elif ext_lower in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return "use Read tool to view this image"
    elif ext_lower in (".svg", ".bmp", ".tiff", ".tif", ".heic", ".heif"):
        return "non-native image format — use Read tool to view"
    elif ext_lower in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
                        ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go",
                        ".java", ".sh", ".sql", ".html", ".css", ".toml"):
        return "use Read tool to read this text file"
    else:
        return f"use Read tool to access this file"


# MIME types that Claude API accepts natively in content blocks.
# Everything else must be converted to path hints even when SDK supports multimodal.
_CLAUDE_NATIVE_MIMES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # Documents (PDF only)
    "application/pdf",
}


async def _convert_attachment_blocks(query_content: Any, session_id: Optional[str]) -> Any:
    """The attachments of a message as the CLI can take them: with no SDK multimodal
    support every image / document block becomes a path hint, otherwise only the
    non-native ones (office docs, audio, video). Files go to Attachments/. A send
    and a steer (design chat-steer D2, H6) both deliver through here."""
    if not isinstance(query_content, list):
        return query_content
    if not _SDK_SUPPORTS_MULTIMODAL:
        return await _convert_unsupported_blocks_to_path_hints(query_content, session_id)
    return await _convert_non_native_blocks_to_path_hints(query_content, session_id)


async def _convert_non_native_blocks_to_path_hints(
    content: list[dict],
    session_id: str | None,
) -> list[dict]:
    """Convert non-native image/document blocks when SDK supports multimodal.

    Passes through Claude-native blocks (jpeg/png/gif/webp images, PDF docs)
    and converts everything else (office docs, audio, video, non-native images)
    to path hints via the same save-to-Attachments mechanism.
    """
    converted: list[dict] = []
    non_native: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in ("image", "document"):
            media_type = block.get("source", {}).get("media_type", "")
            if media_type in _CLAUDE_NATIVE_MIMES:
                converted.append(block)  # pass through natively
            else:
                non_native.append(block)
        else:
            converted.append(block)

    if non_native:
        # Reuse the same save-and-hint mechanism for non-native blocks
        hints = await _convert_unsupported_blocks_to_path_hints(
            non_native, session_id,
        )
        converted.extend(hints)

    return converted


async def _convert_unsupported_blocks_to_path_hints(
    content: list[dict],
    session_id: str | None,
) -> list[dict]:
    """Convert image/document content blocks to path hints.

    Saves base64 data to the agent's workspace under
    ``Attachments/{date}/{filename}`` so files are visible in the
    Workspace Explorer and persist across sessions.  The user controls
    cleanup — files are NOT auto-deleted.

    Text blocks are passed through unchanged.

    Args:
        content: List of content block dicts (image, document, or text).
        session_id: The effective session ID for logging.

    Returns:
        A new list with image/document blocks replaced by text path hints.
    """
    import base64
    from uuid import uuid4 as _uuid4

    converted: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in ("image", "document"):
            source = block.get("source", {})
            data = source.get("data", "")
            media_type = source.get("media_type", "")

            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "application/pdf": ".pdf",
                # Office documents
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/msword": ".doc",
                "application/vnd.ms-excel": ".xls",
                # Audio/video
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/wav": ".wav",
                "audio/ogg": ".ogg",
                "audio/flac": ".flac",
                "audio/aac": ".aac",
                "audio/webm": ".weba",
                "video/mp4": ".mp4",
                "video/quicktime": ".mov",
                "video/x-msvideo": ".avi",
                "video/x-matroska": ".mkv",
                "video/webm": ".webm",
                # Text (large text files also come through base64 path now)
                "text/plain": ".txt",
                "text/csv": ".csv",
                "application/csv": ".csv",
                "text/html": ".html",
                "text/markdown": ".md",
                "application/json": ".json",
                "application/xml": ".xml",
                "text/xml": ".xml",
                "application/x-yaml": ".yaml",
            }
            ext = ext_map.get(media_type, ".bin")

            # Save to SwarmWS/Attachments/{date}/ for Workspace Explorer visibility
            from datetime import date as _date
            from core.initialization_manager import initialization_manager

            ws_path = initialization_manager.get_cached_workspace_path()
            if ws_path:
                date_str = _date.today().isoformat()
                attach_dir = Path(ws_path) / "Attachments" / date_str
            else:
                from jobs.paths import SWARMWS as _SWARMWS
                attach_dir = _SWARMWS / "Attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)

            # Preserve original filename if provided by frontend
            original_name = block.get("_filename", "")
            if original_name:
                safe_name = Path(original_name).name
                candidate = attach_dir / safe_name
                if candidate.exists():
                    stem = candidate.stem
                    candidate = attach_dir / f"{stem}_{_uuid4().hex[:6]}{ext}"
                file_path = candidate
            else:
                file_path = attach_dir / f"{_uuid4()}{ext}"

            try:
                decoded = base64.b64decode(data)
                await asyncio.to_thread(file_path.write_bytes, decoded)
                logger.warning(
                    "SDK multimodal fallback: saved %s block to %s (session %s)",
                    block_type, file_path, session_id or "unknown",
                )
                rel_path = file_path.relative_to(ws_path) if ws_path else file_path
                # Generate file-type-specific guidance for the agent
                access_hint = _get_access_hint(ext, file_path.name)
                converted.append({
                    "type": "text",
                    "text": (
                        f"[Attached file: {file_path.name}] "
                        f"saved at {rel_path} — {access_hint}"
                    ),
                })
            except Exception as e:
                logger.error("Failed to save attachment for fallback: %s", e)
                converted.append({
                    "type": "text",
                    "text": f"[Failed to save {block_type} attachment for fallback delivery]",
                })
        else:
            converted.append(block)
    return converted


def _log_canvas_record_failure(fut) -> None:
    """Done-callback of a canvas record write (a concurrent.futures.Future)."""
    if not fut.cancelled() and fut.exception() is not None:
        logger.warning("canvas record write failed: %s", fut.exception())


class TurnOpener:
    """Claims a ``run_conversation`` turn at its admission (docs/design/
    chat-turn-liveness.md S1). Made before the turn's generator; the generator
    calls ``open`` once its user row is written (or, for a drain, at once) and
    before it takes a slot, so from then on the session's activity says a turn is
    in progress (preparing → starting → waiting) though its ``turn_start`` is
    published at ``session_start``, as before. ``open`` is synchronous: the hub's
    check-and-claim is one step, so two sends can never both get the turn."""

    __slots__ = ("turn_id", "source", "turn_client_id", "session_id")

    def __init__(self, *, source: str, turn_client_id: Optional[str]) -> None:
        self.turn_id = uuid4().hex[:12]
        self.source = source
        self.turn_client_id = turn_client_id
        self.session_id: Optional[str] = None

    @property
    def opened(self) -> bool:
        return self.session_id is not None

    def open(self, session_id: str, unit: Any) -> bool:
        """Claim the turn unless the session cannot take one now: a turn of it is
        open or claimed, or a user Stop is still recycling its subprocess. False →
        the caller queues the message (session_pending) instead."""
        if self.opened:
            return True
        if getattr(unit, "_stopping", False) is True:
            return False
        if not session_events.hub().try_claim_turn(session_id, self.turn_id):
            return False
        self.session_id = session_id
        from . import session_state
        session_state.refresh_activity(session_id, unit)
        return True


class SessionRouter:
    """Routes chat requests to SessionUnits with RAM-based spawn admission.

    Spawn admission is gated by ``ResourceMonitor.spawn_budget()`` (real RAM)
    rather than a fixed tab-count ceiling (R6a, design §9). The first tab is
    always granted (alive_count == 0); subsequent spawns require budget.

    Public API surface consumed by ``routers/chat.py``.

    Invariants:

    - Thin layer: lookup + cap enforcement + delegate.
    - Never touches subprocess directly (delegates to SessionUnit).
    - Concurrency cap is the ONLY cross-unit concern.
    - STREAMING/WAITING_INPUT units are NEVER evicted.
    - Existing alive sessions are never killed when the dynamic limit shrinks.
    """

    QUEUE_TIMEOUT: float = 300.0  # 5 min — channel tasks can be complex
    EVICTION_GRACE_SECONDS: int = 300  # 5 min — protect recently-active sessions
    # Max time a queued waker sleeps before re-evaluating, even if no _slot_available
    # wake arrives. Bounds two hazards exposed once the wake path became
    # grace-respecting (force=False): (1) a lost wakeup on the shared _slot_available
    # Event (a peer waker's clear() can swallow a set()), and (2) the case where a
    # within-grace idle becomes STALE while the waker sleeps — without re-polling it
    # would wait the full QUEUE_TIMEOUT instead of evicting the now-stale unit.
    # Re-polling turns "stall up to 300s" into "re-check every WAKE_REPOLL_SECONDS".
    WAKE_REPOLL_SECONDS: float = 5.0

    def __init__(
        self,
        prompt_builder: "PromptBuilder",
        config: Optional["AppConfigManager"] = None,
    ) -> None:
        self._units: dict[str, SessionUnit] = {}
        # the subscription's per-session state snapshot (core/session_state)
        from . import session_events as _se, session_state as _ss
        _se.set_state_provider(lambda sid: _ss.snapshot(self, sid))
        self._prompt_builder = prompt_builder
        self._config = config
        self._lifecycle_manager = None  # Set by session_registry after init
        self._slot_available: asyncio.Event = asyncio.Event()
        self._slot_available.set()  # Initially available
        self._slot_lock: asyncio.Lock = asyncio.Lock()
        self._queue: list[asyncio.Future] = []

        # Root-1 SSOT Phase 2 (L3): serial drain worker. Session ids whose
        # transition reached a clean IDLE are pushed here; a single long-lived
        # consumer coalesce-drains each session's pending messages ONE turn at a
        # time, OUTSIDE any transition stack (F1 — never re-enter send() from
        # within _transition / _on_unit_state_change). Lazily started on first
        # enqueue so it binds to the running event loop.
        self._drain_queue: asyncio.Queue[str] = asyncio.Queue()
        self._drain_worker_task: Optional[asyncio.Task] = None
        self._drain_enqueued: set[str] = set()  # de-dupe: at most one pending entry/session
        # sessions whose message was queued on QUEUE_TIMEOUT: kicked when a slot
        # frees, and the set emptied then. At most one entry per unit in _units
        # (docs/design/chat-turn-liveness.md S1 v4).
        self._awaiting_slot: set[str] = set()
        # sessions whose drained messages went back to pending after a failed
        # delivery: how many delayed retries have been tried. Popped on delivery,
        # on giving up and when the session is gone (docs/design/chat-turn-liveness.md S2).
        self._drain_retries: dict[str, int] = {}
        self._drain_retry_timers: dict[str, asyncio.TimerHandle] = {}
        # CLI-initiated turns (core/cli_stream_reader.py): one sink task per
        # session at a time, running the unit's queued injected turns in order.
        self._injected_tasks: dict[str, asyncio.Task] = {}

        # ── Desktop prewarm pool (方案A, design v2 §4/§5, run_f107f442) ───────
        # Maps a bucket key → the prewarm-prefixed session_id of a pre-spawned,
        # IDLE, baseline-prompt subprocess ready for a desktop tab's first
        # message to ADOPT (skipping the 8-14s cold __aenter__ handshake).
        #   bucket key = (session_type, agent_id, model)
        # Model dim is kept for CORRECTNESS of adopt (wrong model = wrong
        # persona) but only the MAIN bucket is ever warmed (1M models are
        # isomorphic — design §HIGH-1: 校验≠分桶). NO is_prewarm field on
        # SessionUnit — pool MEMBERSHIP is the `prewarm-` prefix, the single
        # server-mint-only authority (IMPROVEMENT.md:1400 REJECT of the field).
        # Both dicts are guarded by self._slot_lock (same as _units mutation).
        # INVARIANT (Gate-1 #4): all desktop tabs in ONE daemon share one
        # working_directory, so the bucket key can omit it safely — a
        # multi-workspace daemon would need it added.
        self._desktop_prewarm_pool: dict[tuple, str] = {}
        # prewarm_session_id → {"ctx_hash": str, "spawned_monotonic": float}
        # for staleness (context files changed since spawn) + TTL bound.
        self._desktop_prewarm_meta: dict[str, dict] = {}
        self._DESKTOP_PREWARM_TTL_S: float = 60.0  # staleness upper bound

        # Load persisted sdk_session_ids for lazy injection at unit creation.
        # Design §2B fix: old restore_session_state() iterated _units which is
        # empty at boot. This caches the mapping; get_or_create_unit() injects.
        self._persisted_sdk_ids: dict[str, str] = {}
        try:
            from .session_state_persistence import load_persisted_state
            from jobs.paths import APP_DATA_DIR

            state_file = APP_DATA_DIR / "session_state.json"
            self._persisted_sdk_ids = load_persisted_state(state_file)
            if self._persisted_sdk_ids:
                logger.info(
                    "Cached %d persisted session identities for lazy injection",
                    len(self._persisted_sdk_ids),
                )
        except Exception as exc:
            logger.debug("Could not load persisted session state: %s", exc)

    # ── Unit management ───────────────────────────────────────────

    @staticmethod
    async def _persist_assistant_blocks(
        session_id: str,
        blocks: list[dict],
        model: str | None,
        label: str = "",
        client_id: str | None = None,
    ) -> list[dict]:
        """Save accumulated assistant content blocks to DB.

        Called from ``finally`` blocks in streaming methods to ensure
        partial content is persisted even on abort or error.

        When ``client_id`` is provided, it is written into the row's
        ``metadata`` (mirroring the user-row path) so the frontend can
        correlate the persisted assistant row back to its optimistic
        placeholder during reconcile (MessageStore._applyMerge keys on the
        ``local-{client_id}-asst`` placeholder id). Without this key the
        placeholder can never be matched and the bubble never finalizes
        (P4 streaming-never-finalizes, run_af36e709). Continuation paths
        that have no client_id in scope pass None and rely on the H2
        turn-end reconcile backstop.

        The DB layer retries transient errors (SQLITE_BUSY) up to 3 times.
        If all retries fail, logs at ERROR level (content may be lost on resume).

        Returns:
            The CLEANED blocks list (leaks stripped) — the SAME bytes written to
            data.db. The caller writes this back into the yielded
            ``event['content']`` so the stream and the DB cannot diverge (bug 2d).
            Returned even on a DB-write failure (the stream still shows the cleaned
            bytes). Returns the input ``blocks`` unchanged when empty or when the
            best-effort clean raised.
        """
        if not blocks:
            return blocks
        from database import db
        # Persist-side tool-call-leak clean (run_2bdec5c0; re-homed run_719886ee):
        # strip raw ‹invoke> leak from each text block BEFORE it reaches data.db,
        # so a persisted leak can never be spliced back into a resumed prompt by
        # ANY downstream content→model-prompt reader. This is the write-side SINGLE
        # chokepoint — all 3 call sites funnel through here (TECH.md "move the
        # invariant to the single write point"). It is DECOUPLED from the streaming
        # kill decision (detect_tool_call_leak): cleaning only changes stored
        # CONTENT, never whether the turn was killed.
        #
        # DO NOT remove as "redundant": Gate-1 (run_719886ee) verified that
        # memory_extractor._format_conversation AND summarization._get_text_content
        # BOTH read persisted content into a Bedrock prompt with NO strip of their
        # own — deleting this chokepoint would re-open the re-induction loop for
        # them. It also catches a case the orchestrator structurally misses: the
        # streaming_orchestrator TextBlock guard gates its strip/kill on
        # detect_tool_call_leak(), which returns FALSE on the TRUNCATED pure-leak
        # form (`court\n‹invoke name="Bash">` with no ‹parameter after, the a0f04b00
        # shape) → that raw text flows through to here. context_injector cleans the
        # resume READ path (incl. old pre-fix rows) as defense-in-depth.
        #
        # The clean is extracted to the module-level _clean_blocks_for_persist and
        # run INLINE (NOT via asyncio.to_thread). It RETURNS the cleaned list, which
        # this function also returns so the caller writes it back into the yielded
        # event['content'] (bug 2d: persist and stream now carry identical bytes —
        # no UI≠DB fork). Best-effort: ANY exception degrades to persisting the
        # ORIGINAL blocks + a warning (crash-safe persistence outranks leak-cleaning).
        #
        # ⚠️ Deliberately INLINE, not asyncio.to_thread: strip_tool_call_leak is
        # linear-time (~25ms for 1MB of crafted input). It WAS quadratic until the
        # single-regex strip was replaced by _find_leak_spans (240KB crafted = 5.1s
        # measured; an earlier "refuted" note here was wrong). The GIL means to_thread buys
        # ZERO parallelism for pure-Python CPU work, while offloading a sync-fast op
        # to the SHARED default executor is the exact 16-worker-saturation anti-pattern
        # COE run_b36c7880 fixed (see data_safety.py "run inline, NOT via to_thread" —
        # that COE's rule explicitly covers the send/streaming hot path this runs on).
        blocks_to_persist = blocks
        try:
            blocks_to_persist = _clean_blocks_for_persist(blocks)
        except Exception as exc:  # noqa: BLE001 — clean must never block persist
            logger.warning(
                "Persist-side leak clean failed for session %s (%s: %s); "
                "persisting original blocks unmodified",
                session_id, type(exc).__name__, exc,
            )
            blocks_to_persist = blocks
        try:
            _metadata = {"client_id": client_id} if client_id else None
            await db.messages.put({
                "id": str(uuid4()),
                "session_id": session_id,
                "role": "assistant",
                "content": blocks_to_persist,
                "model": model,
                "created_at": datetime.now().isoformat(),
                **({"metadata": _metadata} if _metadata else {}),
            })
            # Return the CLEANED blocks so the caller writes them back to the
            # yielded event['content'] — persist and stream carry identical bytes
            # (bug 2d). Returning the list (not a bool) is the Gate-1-required
            # contract; no caller used the former bool return.
            return blocks_to_persist
        except Exception as exc:
            logger.error(
                "Failed to save assistant message%s for session %s: %s "
                "(content may be lost on resume)",
                f" ({label})" if label else "", session_id, exc,
            )
            # On DB failure, still hand back the cleaned blocks: the stream should
            # show the same (cleaned) bytes even if the row did not persist.
            return blocks_to_persist

    @staticmethod
    def _compact_tool_results(blocks: list) -> list[dict]:
        """What a live ``tool_result`` event keeps in data.db: the step's
        status and one-line outcome, never its (possibly huge) content."""
        out: list[dict] = []
        for blk in blocks or []:
            if not (isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("tool_use_id")):
                continue
            compact = {
                "type": "tool_result", "tool_use_id": blk["tool_use_id"],
                "is_error": bool(blk.get("is_error")), "truncated": False,
            }
            if blk.get("outcome"):
                compact["outcome"] = str(blk["outcome"])[:300]
            if blk.get("parent_tool_use_id"):
                compact["parent_tool_use_id"] = blk["parent_tool_use_id"]
            sub = blk.get("subagent")
            if isinstance(sub, dict):
                # An Agent step's final sub-agent outcome (status, steps, duration,
                # summary) — the only place it survives the stream.
                keep = {k: sub[k] for k in ("status", "toolUses", "durationMs") if k in sub}
                if isinstance(sub.get("summary"), str):
                    keep["summary"] = sub["summary"][:300]
                compact["subagent"] = keep
            out.append(compact)
        return out

    # Events after which no assistant row may follow in this stream: pending
    # step results are written on their own row at that point.
    _RESULT_FLUSH_EVENTS = frozenset({"result", "error", "ask_user_question", "cmd_permission_request"})

    # file_changed kinds that are a turn's PRODUCTS (needs_human_review): content
    # (docs, decks, pages, images), knowledge, and a file the session wrote outside
    # SwarmWS (external-diff in a git repo, external-nodiff on a plain FS) — the
    # same kinds the live Canvas rail lists, so the persisted record the session's
    # Canvas is rebuilt from (core/canvas_session) matches what the rail showed.
    # Source edits are the turn_changes card; process / bookkeeping files are noise.
    # One definition of which emit-gate kinds are products (core/turn_products).
    from .turn_products import PRODUCT_FILE_KINDS as _DELIVERABLE_KINDS

    @staticmethod
    def _turn_files_of(unit: object) -> Optional[dict]:
        """The unit's per-turn products dict (SessionUnit._turn_files: reset at
        send() admission, so it spans a question / permission pause), or None for
        a unit without one (no product tracking — never a crash)."""
        files = getattr(unit, "_turn_files", None)
        return files if isinstance(files, dict) else None

    @staticmethod
    def _record_canvas_output(session_id: str, event: dict) -> None:
        """Record a rail-kind file_changed in the session's Canvas record
        (core/session_outputs). SUBMITTED to the single-worker canvas_session pool
        before this event goes on to the client, so a Canvas refresh the client sends
        on it is queued behind the write. Never awaited: the stream does not wait on
        disk, and a failure only costs a Canvas row."""
        from core import session_outputs
        if event.get("kind") not in session_outputs.RAIL_KINDS and event.get("operation") != "deleted":
            return
        try:
            from core import executors
            # pool.submit, not loop.run_in_executor: the write must not depend on
            # this loop being alive when it completes; FIFO order is the pool's.
            fut = executors.get_pool("canvas_session").submit(session_outputs.record, session_id, [dict(event)])
            fut.add_done_callback(_log_canvas_record_failure)
        except Exception:  # noqa: BLE001 — a display aid never breaks the stream
            logger.debug("canvas record submit failed", exc_info=True)

    @classmethod
    def _note_deliverable(cls, files: dict, event: dict) -> None:
        """Remember a file_changed event that is a product of this turn (latest
        operation per file wins — a file written then deleted is not a product)."""
        path = event.get("absolutePath") or event.get("path")
        if not isinstance(path, str) or not path:
            return
        if event.get("operation") == "deleted":
            files.pop(path, None)
            return
        if event.get("kind") in cls._DELIVERABLE_KINDS:
            prev = files.get(path)
            files[path] = {
                "path": str(event.get("path") or path), "absolutePath": path,
                "kind": event["kind"],
                # created vs updated is not known here; "written" is the honest word
                "operation": (prev or {}).get("operation", "written"),
            }

    async def _persist_stream_event(
        self, session_id: str, event: dict, pending: list[dict],
        *, label: str = "", client_id: str | None = None, files: dict | None = None,
        changes: object | None = None,
    ) -> list[dict]:
        """Persist what one streamed event contributes to the turn's rows; return
        events to deliver BEFORE this one.

        - ``assistant``: persisted immediately (crash-safe), PREFIXED with the
          step results that arrived since the last row, so each result sits right
          after its tool_use and the row count does not double. The cleaned
          blocks are written back into ``event['content']`` (stream == DB, bug 2d).
        - ``tool_result``: compacted into ``pending`` (status + outcome only), so
          a reloaded turn shows what each step returned and which failed.
        - ``file_changed`` of a product kind: remembered in ``files``; every
          ``file_changed``, and every Edit/Write tool path, is noted in
          ``changes`` (core/turn_changes).
        - ``result``: the turn's products become one persisted
          ``turn_deliverables`` block, delivered just before the result (so the
          chat shows "what this turn produced" live and after a reload); the
          turn's code diff becomes one ``turn_changes`` block just before it.
        - ``cmd_permission_request``: the prompt is stored as a block of the turn
          (core/permission_prompt), on the row the flush below writes.
        - a terminal event: any ``pending`` results get their own row.
        """
        etype = event.get("type")
        if etype == "file_changed":
            self._record_canvas_output(session_id, event)
            if changes is not None:
                try:
                    changes.note(event)
                except Exception as exc:  # noqa: BLE001 — a display aid
                    logger.debug("turn_changes note failed: %s", exc)
            if files is not None:
                self._note_deliverable(files, event)
            return []
        if etype == "tool_result" and event.get("content"):
            pending.extend(self._compact_tool_results(event["content"]))
            return []
        if etype == "assistant" and event.get("content"):
            if changes is not None:
                # Edit/Write paths: a dot-directory file has no file_changed.
                for blk in event["content"]:
                    try:
                        changes.note_tool(blk)
                    except Exception as exc:  # noqa: BLE001 — a display aid
                        logger.debug("turn_changes note_tool failed: %s", exc)
            n = len(pending)
            cleaned = await self._persist_assistant_blocks(
                session_id, [*pending, *event["content"]], event.get("model"),
                label=label, client_id=client_id,
            )
            pending.clear()
            event["content"] = cleaned[n:]  # the clean is one-to-one per block
            return []
        before: list[dict] = []
        if etype == "cmd_permission_request":
            # The prompt is stored with its turn (core/permission_prompt): a tab
            # that missed the live event (another tab, a reload, a CLI-initiated
            # turn) finds it in the history, never a session silently waiting.
            from .permission_prompt import permission_block
            pblock = permission_block(event)
            if pblock is not None:
                pending.append(pblock)
        if etype == "ask_user_question":
            # The question is a block of the turn, as the prompt above is; its
            # answer is recorded onto it (core/question_prompt) — a reload shows both.
            from .question_prompt import question_block
            qblock = question_block(event)
            if qblock is not None:
                pending.append(qblock)
        if etype == "result" and changes is not None:
            try:
                cblock = await changes.finish()
            except Exception as exc:  # noqa: BLE001 — a display aid never breaks the result
                logger.warning("turn_changes finish failed: %s", exc)
                cblock = None
            if cblock:
                pending.append(cblock)
                before.append({"type": "assistant", "content": [cblock], "model": None})
        if etype == "result" and files:
            block = _deliverables_block(list(files.values()))
            pending.append(block)
            before.append({"type": "assistant", "content": [block], "model": None})
            files.clear()
        if pending and etype in self._RESULT_FLUSH_EVENTS:
            await self._persist_assistant_blocks(
                session_id, list(pending), None,
                label=f"{label or 'turn'}-results", client_id=client_id,
            )
            pending.clear()
        return before

    def get_unit(self, session_id: str) -> Optional[SessionUnit]:
        """Look up a SessionUnit by session_id."""
        return self._units.get(session_id)

    def persisted_sdk_session_id(self, session_id: str) -> Optional[str]:
        """The sdk_session_id persisted for a session that has no unit yet.

        Read-only (unlike ``get_or_create_unit``, which pops the entry when it
        injects it into a new unit). Only meaningful when ``get_unit`` returns
        None — once a unit exists, its own ``_sdk_session_id`` is the truth.
        """
        return self._persisted_sdk_ids.get(session_id)

    def get_or_create_unit(
        self, session_id: str, agent_id: str,
    ) -> SessionUnit:
        """Get existing or create new COLD SessionUnit.

        On creation, injects any persisted sdk_session_id from the cache
        (loaded at __init__ from session_state.json). This enables fast
        --resume after daemon restart without requiring boot-time restore
        on an empty _units dict.
        """
        unit = self._units.get(session_id)
        if unit is None:
            unit = SessionUnit(
                session_id=session_id,
                agent_id=agent_id,
                on_state_change=self._on_unit_state_change,
                on_cli_event=self._on_unit_cli_event,
            )
            # Lazy-inject persisted sdk_session_id (Design §2B fix).
            # pop() ensures one-shot consumption — no stale reuse.
            persisted_id = self._persisted_sdk_ids.pop(session_id, None)
            if persisted_id:
                unit._sdk_session_id = persisted_id
                logger.info(
                    "Injected persisted sdk_session_id for session %s (fast --resume enabled)",
                    session_id,
                )
            self._units[session_id] = unit
            logger.info(
                "session_router.create_unit session_id=%s agent_id=%s",
                session_id, agent_id,
            )
        return unit

    # ── Pre-warm (MeshClaw pattern) ──────────────────────────────

    async def prewarm_channel_session(
        self, agent_id: str, channel_context: Optional[dict] = None,
    ) -> Optional[str]:
        """Pre-warm an IDLE subprocess for the channel owner's first message.

        Spawns a CLI subprocess with the full system prompt so it's ready
        for instant adoption when the first real message arrives.  Eliminates
        ~4s cold-start latency after daemon restart.

        Best-effort: returns the temporary session_id on success, None on
        any failure.  Callers should NOT block on this.

        Parameters
        ----------
        agent_id:
            The agent ID to build config for.
        channel_context:
            Optional channel context dict (channel_type, is_owner, etc.)
            for Slack-specific system prompt sections.  Without this, the
            pre-warmed subprocess lacks Channel Security rules.

        Returns:
            Temporary session_id of the pre-warmed unit, or None.
        """
        from .agent_defaults import build_agent_config

        temp_session_id = f"{PREWARM_SESSION_PREFIX}{uuid4()}"
        unit = SessionUnit(
            session_id=temp_session_id,
            agent_id=agent_id,
            on_state_change=self._on_unit_state_change,
            on_cli_event=self._on_unit_cli_event,
        )
        # NOTE: unit is NOT registered in _units yet — deferred until spawn
        # succeeds.  This prevents the lifecycle reaper from seeing a
        # half-initialized unit during the async spawn window.

        try:
            agent_config = await build_agent_config(agent_id)
            if not agent_config:
                return None

            # Per-channel model override — same logic as run_conversation().
            # Must be applied BEFORE build_options() so resolve_model() picks
            # it up.  Without this, pre-warmed subprocess spawns with the
            # global default model and can't switch after spawn (SDK constraint).
            if channel_context and channel_context.get("model"):
                agent_config["model"] = channel_context["model"]

            # The unit's stable hook context, created HERE so the hooks bind to it: on
            # adoption the first send rewrites its sdk_session_id in place (see
            # run_conversation), and every gate then judges the real session. Built
            # without it, hook_builder fell back to the agent id ("default") for the
            # unit's whole life — gates keyed by session saw the wrong session.
            unit._hook_session_context = {"sdk_session_id": temp_session_id}
            options = await self._prompt_builder.build_options(
                agent_config=agent_config,
                enable_skills=True,
                enable_mcp=True,
                session_context=unit._hook_session_context,
                channel_context=channel_context,
            )

            # Spawn subprocess → COLD → IDLE
            async for event in unit._ensure_spawned(options, self._config):
                if event.get("_abort"):
                    return None

            if unit.state == SessionState.IDLE:
                # Only register after spawn confirms IDLE — prevents reaper
                # from seeing a COLD/DEAD unit during the spawn window
                self._units[temp_session_id] = unit
                logger.info(
                    "session_router.prewarm_complete session_id=%s",
                    temp_session_id,
                )
                return temp_session_id

            # Unexpected state — don't register
            return None
        except Exception as exc:
            logger.warning("session_router.prewarm_failed: %s", exc)
            return None

    async def adopt_prewarmed_unit(
        self, prewarm_session_id: str, real_session_id: str,
    ) -> bool:
        """Re-key a pre-warmed unit to serve a real session.

        Atomically moves the unit from the temporary pre-warm key to the
        real session_id under _slot_lock.  The unit must be IDLE (alive
        subprocess) for adoption to succeed.

        Uses _slot_lock to prevent TOCTOU race when two coroutines
        (e.g., two simultaneous Slack DMs at startup) both try to adopt
        the same pre-warmed unit.

        Returns True on success, False if the unit doesn't exist, died,
        or was evicted.
        """
        async with self._slot_lock:
            unit = self._units.pop(prewarm_session_id, None)
            if unit is None:
                return False

            if unit.state != SessionState.IDLE:
                # Unit died or was evicted — put back and fail
                self._units[prewarm_session_id] = unit
                logger.info(
                    "session_router.adopt_prewarmed_skip state=%s (expected IDLE)",
                    unit.state.value,
                )
                return False

            unit.session_id = real_session_id
            self._units[real_session_id] = unit
            # Gate-1 #5 bridge: re-keying just DESTROYED the `prewarm-` prefix
            # that both warm-reuse exemption sites key on. Set the one-shot flag
            # so this unit's FIRST message still warm-reuses the pre-spawned
            # subprocess instead of hitting poison_guard recycle (which would
            # kill+respawn and defeat the entire prewarm). Cleared at STREAMING
            # entry. This ALSO fixes the latent same-shape bug in the existing
            # channel adopt path (shared primitive — R25 neighborhood).
            unit._adopted_prewarm_fresh = True
            logger.info(
                "session_router.adopt_prewarmed %s → %s",
                prewarm_session_id, real_session_id,
            )
            return True

    # ── Desktop prewarm pool (方案A, run_f107f442) ──────────────────

    @staticmethod
    def _now_monotonic() -> float:
        """Wall-clock-independent clock for TTL (test-overridable)."""
        return time.monotonic()

    @staticmethod
    def _desktop_ctx_hash() -> str:
        """A cheap staleness fingerprint of the context files that
        build_default_system_prompt reads (the .context/*.md set). If it changes
        between prewarm-spawn and adopt, the baseline prompt固化 in the pooled
        subprocess is stale → discard-and-cold. Uses mtime_ns of each governed
        file (fast, no read); missing dir → empty string (no pool, cold path).
        """
        import hashlib
        try:
            # AC6: derive the .context dir from the ACTUAL workspace path (what the
            # prewarm subprocess built its baseline prompt from — prompt_builder
            # reads Path(working_directory)/".context"), NOT a hardcoded
            # ~/.swarm-ai/SwarmWS/.context. The hardcode ignores the SWARM_DATA_DIR
            # / non-default workspace_path escape hatch (config.get_app_data_dir),
            # so under a custom data dir the fingerprint would read the WRONG dir →
            # staleness detection blind (false-fresh adopt or permanent-stale "").
            from core.initialization_manager import initialization_manager
            ctx_dir = Path(initialization_manager.get_cached_workspace_path()) / ".context"
            if not ctx_dir.is_dir():
                return ""
            parts = []
            for p in sorted(ctx_dir.glob("*.md")):
                try:
                    parts.append(f"{p.name}:{p.stat().st_mtime_ns}")
                except OSError:
                    continue
            return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
        except Exception:  # noqa: BLE001 — staleness is best-effort, never fatal
            logger.debug("_desktop_ctx_hash: suppressed exception on a fail-safe path", exc_info=True)
            return ""

    def _desktop_prewarm_enabled(self) -> bool:
        """Strangler flag — default OFF. Flag-off path is byte-identical to the
        pre-prewarm cold spawn (empty pool → adopt intercept is a no-op)."""
        import os
        return os.environ.get("SWARM_DESKTOP_PREWARM", "").lower() in ("1", "true", "yes")

    async def warm_desktop_pool(self, depth: int = 2) -> None:
        """Best-effort: warm the MAIN desktop bucket to `depth` IDLE baseline
        units. No-op unless the strangler flag is on. Never fatal (mirrors
        _prewarm_boto3). Called from the daemon lifespan.

        Main bucket = (desktop, default_agent, default_model) — design §HIGH-1
        warms ONLY the main bucket (1M models isomorphic; other buckets go cold).
        """
        if not self._desktop_prewarm_enabled():
            return
        try:
            from .agent_defaults import DEFAULT_AGENT_ID, resolve_default_model
            agent_id = DEFAULT_AGENT_ID
            model = resolve_default_model() or ""  # global default model
            key = ("desktop", agent_id, model)
            while len([1 for v in self._desktop_prewarm_pool.values()]) < depth:
                have = sum(1 for k in self._desktop_prewarm_pool if k == key)
                if have >= depth:
                    break
                # prewarm_channel_session with channel_context=None → desktop
                # baseline prompt (no channel security section).
                pid = await self.prewarm_channel_session(agent_id, channel_context=None)
                if not pid:
                    break  # spawn failed/blocked — stop, stay cold
                async with self._slot_lock:
                    # Depth-2 pool: allow >1 unit under the same bucket key by
                    # storing the FIRST free slot; a dict keyed by (bucket, i).
                    slot = 0
                    while (key + (slot,)) in self._desktop_prewarm_pool:
                        slot += 1
                    if slot >= depth:
                        # Pool already full for this bucket — release the extra.
                        break
                    self._desktop_prewarm_pool[key + (slot,)] = pid
                    self._desktop_prewarm_meta[pid] = {
                        "ctx_hash": self._desktop_ctx_hash(),
                        "spawned_monotonic": self._now_monotonic(),
                    }
                logger.info("session_router.desktop_prewarm_ready bucket=%s slot=%d id=%s",
                            key, slot, pid)
        except Exception as exc:  # noqa: BLE001 — warm is best-effort
            logger.warning("session_router.desktop_prewarm_failed: %s", exc)

    async def _try_adopt_desktop_pool(
        self, session_id: str, agent_id: str, model: str,
    ) -> bool:
        """Adopt a pooled unit for a real desktop session if a fresh, non-stale,
        bucket-matching IDLE unit exists. Returns True on adopt (unit now in
        _units under session_id), False → caller falls through to cold create.

        Validates bucket = (desktop, agent_id, model) — a mismatch NEVER adopts
        (wrong model/agent = wrong persona, Gate-1 M2). Validates staleness
        (ctx_hash unchanged) + TTL. All under _slot_lock (composes with
        adopt_prewarmed_unit which also takes the lock — see note).
        """
        if not self._desktop_prewarm_enabled():
            return False
        # ── AC1 (CRITICAL) history guard: a session that ALREADY HAS history must
        # NEVER adopt a baseline prewarm subprocess. An adopted unit is IDLE with
        # _sdk_session_id set → is_cold_resume is False (run_conversation :2978) and
        # desktop has no channel_context → needs_channel_resume is False → the resume
        # block is NEVER built → warm-reuse via query() discards the baseline
        # system_prompt → the reopened tab's prior conversation is silently lost.
        # A history-bearing session must fall through to the cold/--resume path.
        # ⚠️ Threshold is >= 1, NOT > 1: this runs at the adopt-intercept, which is
        # BEFORE the current user message is persisted (session_router :2855). So the
        # count here is PRIOR-history rows only — even one prior row means "has
        # history". (The > 1 threshold at :2995/:3034 is for the POST-persist point,
        # where the current message is already counted.)
        from database import db
        if await db.messages.count_by_session(session_id) >= 1:
            logger.info(
                "session_router.desktop_adopt_skip_history session_id=%s "
                "(has prior history → cold/--resume, never adopt)",
                session_id,
            )
            return False
        # Find any slot for this bucket (base key + slot index).
        base = ("desktop", agent_id, model)
        current_hash = self._desktop_ctx_hash()
        now = self._now_monotonic()
        # Snapshot candidate under lock-free read; validate + adopt under lock.
        pool_key = None
        prewarm_id = None
        for k, pid in list(self._desktop_prewarm_pool.items()):
            if k[:3] == base:
                pool_key, prewarm_id = k, pid
                break
        if prewarm_id is None:
            return False
        # SINGLE critical section (Gate-2 fix): validate staleness + claim the
        # pool entry atomically under _slot_lock, so no concurrent adopt can
        # double-claim and no lock-free mutation races the warmer. All pool/meta
        # MUTATIONS live under the lock (the ONLY exceptions are the read-only
        # snapshot above and the sync DEAD-sink callback — which is lock-free BY
        # DESIGN because it runs on the _transition stack that ALREADY holds
        # _slot_lock via _evict_idle; taking it there would re-entrant-deadlock,
        # and a no-await sync callback is atomic in the single-loop anyway — same
        # precedent as enqueue_drain/_slot_available.set()). This method is async
        # and NOT on that stack, so it CAN and DOES take the lock.
        # adopt_prewarmed_unit takes _slot_lock itself → claim + drop here, then
        # call it AFTER releasing (no re-entrancy).
        stale = False
        async with self._slot_lock:
            if self._desktop_prewarm_pool.get(pool_key) != prewarm_id:
                return False  # raced — another adopt took it
            meta = self._desktop_prewarm_meta.get(prewarm_id, {})
            if meta.get("ctx_hash") != current_hash or (
                now - meta.get("spawned_monotonic", 0.0) > self._DESKTOP_PREWARM_TTL_S
            ):
                stale = True
            # Claim it either way (adopt OR discard-stale) so it can't be
            # double-claimed; the unadopted unit is reclaimed by the prefix-exempt
            # kill paths (P-a) — we do NOT block adopt on killing it.
            self._desktop_prewarm_pool.pop(pool_key, None)
            self._desktop_prewarm_meta.pop(prewarm_id, None)
        if stale:
            # AC3: KILL the discarded prewarm unit — do NOT leak it. A
            # prewarm-prefixed unit is EXEMPT from the TTL killer and the orphan
            # reaper (P-a), so the old "reclaimed by the prefix-exempt kill paths"
            # assumption was wrong: nothing reclaims it until memory pressure. Kill
            # it here, outside the _slot_lock critical section above (:2125-2137,
            # released), so kill → _transition → _on_unit_state_change does NOT
            # re-enter _slot_lock (this method is not on the _transition stack).
            stale_unit = self._units.get(prewarm_id)
            if stale_unit is not None:
                try:
                    await stale_unit.kill()
                finally:
                    self._units.pop(prewarm_id, None)
            logger.info("session_router.desktop_prewarm_stale id=%s → cold (killed)", prewarm_id)
            return False
        ok = await self.adopt_prewarmed_unit(prewarm_id, session_id)
        if not ok:
            logger.info("session_router.desktop_adopt_rejected id=%s (not IDLE)", prewarm_id)
        return ok

    def list_units(self) -> list[SessionUnit]:
        """Return all registered SessionUnits."""
        return list(self._units.values())

    @property
    def alive_count(self) -> int:
        """Number of units with alive subprocesses."""
        return sum(1 for u in self._units.values() if u.is_alive)

    def has_active_session(self, session_id: str) -> bool:
        """Check if a session has an alive subprocess."""
        unit = self._units.get(session_id)
        return unit is not None and unit.is_alive

    # ── Slot management ───────────────────────────────────────────

    # ── Pool counts ──────────────────────────────────────────

    @property
    def _channel_alive_count(self) -> int:
        """Number of alive channel session units."""
        return sum(1 for u in self._units.values() if u.is_alive and u.is_channel_session)

    @property
    def _chat_alive_count(self) -> int:
        """Number of alive chat (non-channel) session units.

        NOTE: this INCLUDES unadopted prewarm units — correct for spawn_budget
        (they are real subprocesses consuming real RAM; excluding them would
        under-count RAM and re-open the OOM door). For the sacred-first-tab USER
        check, use `_user_chat_alive_count` instead (AC4)."""
        return sum(1 for u in self._units.values() if u.is_alive and not u.is_channel_session)

    @property
    def _user_chat_alive_count(self) -> int:
        """Alive chat units that are a USER's tab — EXCLUDES unadopted prewarm
        units (AC4). The sacred-first-tab guarantee (":2260") asks "does the user
        already have a chat tab?"; a warm prewarm pool is NOT a user tab, so
        counting it there would silently disable the first-tab grant whenever the
        pool is warm. Budget accounting still uses the prewarm-inclusive counts —
        this split is UX (whose tab) vs RAM (real subprocess), not one number."""
        return sum(
            1 for u in self._units.values()
            if u.is_alive and not u.is_channel_session
            and not u.session_id.startswith(PREWARM_SESSION_PREFIX)
        )

    # ── Slot acquisition ─────────────────────────────────────

    async def _acquire_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire a concurrency slot. Delegates to pool-specific methods.

        Channel units and chat units have separate slot pools:
        - Channel: exactly 1 dedicated slot (serialized)
        - Chat: max_tabs - 1 slots

        Returns:
            "ready" — slot acquired, proceed with send
            "queued" — was queued, now ready
            "timeout" — queue timed out, all slots busy
        """
        # Fast path: already alive — no slot needed
        if requesting_unit.is_alive:
            return "ready"

        if requesting_unit.is_channel_session:
            return await self._acquire_channel_slot(requesting_unit)
        return await self._acquire_chat_slot(requesting_unit)

    async def _acquire_channel_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire the dedicated channel slot (exactly 1).

        If another channel is IDLE → evict it.
        If another channel is STREAMING → queue with timeout.
        Never touches chat slots.
        """
        from .resource_monitor import resource_monitor

        async with self._slot_lock:
            if self._channel_alive_count == 0:
                # Channel slot is free
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if not budget.can_spawn and self.alive_count > 0:
                    # Try evicting an idle channel first
                    if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                        resource_monitor.invalidate_cache()
                return "ready"

            # Another channel unit is alive — try evicting if IDLE
            if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                return "ready"

        # Channel slot occupied by a protected (STREAMING) unit — queue
        deadline = time.monotonic() + self.QUEUE_TIMEOUT
        logger.info(
            "session_router: channel slot busy, queuing %s (timeout=%.0fs)",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                self._slot_available.clear()
                await asyncio.wait_for(
                    self._slot_available.wait(), timeout=remaining,
                )
            except asyncio.TimeoutError:
                break

            async with self._slot_lock:
                if self._channel_alive_count == 0:
                    return "queued"
                if await self._evict_idle(exclude=requesting_unit, channel_only=True):
                    return "queued"

        logger.warning(
            "session_router: channel queue timeout for session %s after %.0fs",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        return "timeout"

    async def _acquire_chat_slot(self, requesting_unit: SessionUnit) -> str:
        """Acquire a chat slot from the chat pool (max_tabs - 1).

        Never touches the dedicated channel slot.
        """
        from .resource_monitor import resource_monitor

        _needs_queue = False
        evicted_for_this_wait = False  # at most one eviction per wait (below)
        async with self._slot_lock:
            # R6a (§9.5): backend admission is gated SOLELY by spawn_budget
            # (real RAM), NOT by compute_max_tabs (a frontend UX constant).
            # spawn_budget's alive_count penalty (_CONCURRENT_PENALTY_FACTOR)
            # remains the COE05 simultaneous-peak floor — unchanged. The frontend
            # still bounds tab COUNT via MAX_TABS_HARD_CEILING; the backend only
            # answers "is there RAM to spawn."
            #
            # First CHAT tab is sacred — always grant the user's only chat
            # session (a pessimistic budget must never deadlock it). Keyed on the
            # CHAT pool: a lone alive CHANNEL session must NOT send the first
            # chat tab to budget-denied→queue→QUEUE_TIMEOUT, because the
            # chat-scoped _evict_idle cannot evict the channel to rescue it
            # (REVIEW 4.1). This mirrors _acquire_channel_slot's
            # `_channel_alive_count == 0` branch EXACTLY: still check budget,
            # try to reclaim same-pool RAM by evicting a chat-idle peer if
            # denied, then grant unconditionally — but LOG when granting under a
            # denied budget so the (accepted) OOM door is observable, never
            # silent (adversarial #2). Chat-scoped eviction can't touch the
            # channel, so we never force the user's first tab to wait for RAM it
            # cannot free.
            if self._user_chat_alive_count == 0:
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if not budget.can_spawn and self.alive_count > 0:
                    if await self._evict_idle(exclude=requesting_unit):
                        resource_monitor.invalidate_cache()
                    else:
                        logger.warning(
                            "session_router: granting first chat tab under "
                            "denied budget (no chat-idle to evict) "
                            "session_id=%s reason=%s",
                            requesting_unit.session_id, budget.reason,
                        )
                return "ready"

            # spawn_budget is the gate. Every eviction that leads to a grant
            # re-checks the budget after it (here and on the queue's wake path).
            # Two grants ignore a denied budget, both logged: the user's first chat
            # tab (above), and the queue timeout below, the sole anti-starvation
            # guarantee, which grants after a force-evict, or without one when this
            # wait already evicted and nothing is left to evict.
            budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
            if budget.can_spawn:
                return "ready"

            logger.warning(
                "session_router: spawn budget denied "
                "session_id=%s reason=%s",
                requesting_unit.session_id, budget.reason,
            )
            # Budget denied — try to free RAM by evicting an IDLE peer, then
            # RE-CHECK budget before granting. Eviction within grace is refused
            # here (force=False); the queue+timeout path below is the only
            # escalation to force=True.
            if await self._evict_idle(exclude=requesting_unit):
                evicted_for_this_wait = True
                resource_monitor.invalidate_cache()
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if budget.can_spawn:
                    return "ready"
            # Grace period blocked eviction, or eviction didn't free enough RAM —
            # queue and wait for a slot to free naturally before force-killing.
            # Eviction cost (800K token context lost) >> queue cost (60s wait).
            # Evidence: 28 exit-9 kills in 24h from immediate force.
            _needs_queue = True

        # All chat slots occupied OR budget-denied with grace block — queue with deadline
        deadline = time.monotonic() + self.QUEUE_TIMEOUT
        reason = "budget_denied_grace_block" if _needs_queue else "all_slots_occupied"
        logger.info(
            "session_router: queuing session %s reason=%s (timeout=%.0fs)",
            requesting_unit.session_id, reason, self.QUEUE_TIMEOUT,
        )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # deadline exceeded

            try:
                self._slot_available.clear()
                # Cap the wait at WAKE_REPOLL_SECONDS so a lost wakeup (shared
                # Event clear/set race) or a within-grace idle that ages into
                # stale does not leave the waker asleep until the full deadline.
                # On repoll timeout we loop and re-evaluate the slot/evict state
                # rather than giving up — only the OUTER deadline (remaining<=0)
                # breaks to the force=True last resort.
                await asyncio.wait_for(
                    self._slot_available.wait(),
                    timeout=min(remaining, self.WAKE_REPOLL_SECONDS),
                )
            except asyncio.TimeoutError:
                # Inner repoll elapsed but outer deadline may remain — loop back
                # to re-check. The top-of-loop `remaining <= 0` guard handles the
                # true deadline; this except no longer means "give up".
                continue

            # Re-check under lock after wake. Use force=FALSE here: a wake fires
            # whenever ANY session goes idle (_on_unit_state_change sets
            # _slot_available on streaming→idle). If we forced here, a queued
            # waker would force-kill a peer that *just* went idle (within grace)
            # the instant it answered — the 2026-06-21 incident: a queued
            # reconnect session force-killed a user's warm tab 66ms after it
            # answered (idle 0s), then the user waited 2m28s to re-acquire.
            # force=False lets the grace filter protect freshly-idled tabs; the
            # waker still progresses by evicting any STALE (>grace) idle, or via
            # the timeout last-resort below. Eviction cost (warm context lost +
            # cold resume) >> a bounded wait.
            # At most ONE eviction per wait, and the budget is re-checked after
            # it: when the memory pressure comes from outside SwarmAI an eviction
            # frees little, and evicting on every wake would empty the IDLE tabs
            # one by one and then grant anyway.
            async with self._slot_lock:
                # R6a: budget is the sole gate (no compute_max_tabs ceiling).
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if budget.can_spawn:
                    return "queued"
                if not evicted_for_this_wait and await self._evict_idle(
                        exclude=requesting_unit, force=False):
                    evicted_for_this_wait = True
                    resource_monitor.invalidate_cache()
                    budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                    if budget.can_spawn:
                        return "queued"
            # Slot claimed by another coroutine, or still short — loop back to wait

        # Queue timed out — last resort: force-evict the longest-idle session
        # even if within grace period. Better than failing the user request.
        # MUST stay force=True — this is the SOLE anti-starvation guarantee once
        # the per-wake path (above) was made grace-respecting. Do not soften.
        async with self._slot_lock:
            # A waiter whose own eviction (above) took the last stale IDLE unit
            # still gets its grant: force=True may find nothing left, and the
            # eviction was paid for. The fresh force-evict is tried first.
            if await self._evict_idle(exclude=requesting_unit, force=True) \
                    or evicted_for_this_wait:
                resource_monitor.invalidate_cache()
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                logger.info(
                    "session_router: queue timeout force-evict succeeded for %s "
                    "(granted as last resort; budget can_spawn=%s reason=%s)",
                    requesting_unit.session_id, budget.can_spawn, budget.reason,
                )
                return "queued"

        logger.warning(
            "session_router: queue timeout for session %s after %.0fs",
            requesting_unit.session_id, self.QUEUE_TIMEOUT,
        )
        return "timeout"

    async def _evict_idle(
        self, exclude: SessionUnit, *, channel_only: bool = False,
        force: bool = False,
    ) -> bool:
        """Evict the oldest IDLE unit to free a slot.

        Returns True if a unit was evicted, False if none eligible.
        Only evicts units in IDLE state — STREAMING and WAITING_INPUT are
        protected (Rule 3).

        R6 Step C (§9.9): for CHAT eviction, only ORPHAN sessions (owned by no
        live window — not in open_tabs.json) are eligible. A window-owned chat
        tab is NEVER evicted to make room for another tab (Multi-Tab Isolation
        first principle). So `force=True` no longer means "kill any peer" — it
        means "ignore the grace window AMONG ORPHANS". When every idle chat
        session is owned, eviction refuses (returns False) and the requester
        queues. This makes cross-tab eviction structurally impossible while
        preserving anti-starvation against unowned squatters.

        When *channel_only* is True, only channel IDLE units are eligible
        (used when acquiring a channel slot); the orphan filter does NOT apply
        (the channel pool is a 1-slot daemon-owned pool with no window).

        Grace period (chat only, not channel):
        - Sessions idle < EVICTION_GRACE_SECONDS are protected from eviction
          unless *force* is True. This prevents ping-pong eviction when all
          chat slots are occupied by recently-active tabs.
        - *force=True* bypasses the grace period — used after queue timeout
          as a last resort.
        - Channel eviction (channel_only=True) always ignores grace period
          because there's exactly 1 channel slot with no user-visible tab.

        Fires lifecycle hooks before killing (Gap 1 fix) so that
        DailyActivity extraction, auto-commit, and distillation run
        for the evicted session's conversation.
        """
        idle_units = [
            u for u in self._units.values()
            if u.state == SessionState.IDLE
            # a live background task / queued CLI turn (I5): protected on the channel
            # pool; chat eviction only takes orphans, whose turn no window would see
            and not (u.has_background_work and u.is_channel_session)
            and u is not exclude
            and u.is_channel_session == channel_only
            # a turn admitted on it (claimed) or a Stop recycling it (S1 v4)
            and not self.is_busy(u.session_id)
        ]
        if not idle_units:
            return False

        # R6 Step C (§9.9): chat eviction may reclaim ONLY orphan sessions —
        # ones owned by NO live window (not in open_tabs.json). A session a user
        # still has open in a tab is NEVER force-killed to make room for another
        # tab; that is the cross-tab eviction the Multi-Tab Isolation first
        # principle forbids. When RAM is genuinely full of OWNED tabs, the
        # requester queues (and the user is asked which tab to close) — the
        # system never picks a victim. Channel eviction (1-slot pool, no window)
        # is exempt from this filter — it has its own isolation semantics.
        #
        # Fail-safe: if ownership is unknowable (open_tabs missing/unreadable →
        # None), evict NOTHING this call rather than guessing — a read error must
        # never be misread as "no tabs open → all evictable". The orphan reaper
        # (lifecycle_manager._check_orphan_sessions) is the periodic backstop.
        if not channel_only:
            from routers.settings import owned_session_ids
            owned = owned_session_ids()
            if owned is None:
                logger.info(
                    "session_router.evict_blocked: tab ownership unknowable "
                    "(open_tabs unreadable) — refusing chat eviction this call",
                )
                return False
            orphan_units = [u for u in idle_units if u.session_id not in owned]
            if not orphan_units:
                logger.info(
                    "session_router.evict_blocked: all %d idle chat sessions are "
                    "window-owned — refusing cross-tab eviction (R6 isolation)",
                    len(idle_units),
                )
                return False
            idle_units = orphan_units

        # Grace period: for chat eviction (not channel), filter out
        # sessions that have been idle less than EVICTION_GRACE_SECONDS
        # unless force=True (queue timeout fallback).
        now = awake_time()  # unit.last_used is on the awake clock (core/awake_clock)
        if not channel_only and not force:
            stale_units = [
                u for u in idle_units
                if (now - u.last_used) >= self.EVICTION_GRACE_SECONDS
            ]
            if not stale_units:
                # All sessions are "hot" (recently active) — refuse eviction.
                # Caller should queue and retry, or use force=True.
                logger.info(
                    "session_router.evict_blocked: all %d idle sessions within "
                    "grace period (%ds), refusing eviction",
                    len(idle_units), self.EVICTION_GRACE_SECONDS,
                )
                return False
            idle_units = stale_units

        # P-a AC2: DOWNGRADE (not exempt) an unadopted prewarm unit to the
        # lowest-priority eviction candidate. On a GRACEFUL attempt (force=False)
        # spare the prewarm whenever a NON-prewarm candidate exists (kill that
        # instead); if the ONLY candidates are prewarm units, refuse here so the
        # caller QUEUES — the queue-timeout escalation re-enters with force=True,
        # which does NOT run this block and CAN kill the prewarm. This preserves
        # the force=True SOLE anti-starvation guarantee (XG: "B 不能 regression":
        # a prewarm is a luxury that yields to a real slot demand, but is never
        # removed from the orphan set — that would let a user's tab starve).
        # Channel eviction (channel_only) never sees a prewarm unit
        # (is_channel_session=False), so this is chat-only by construction.
        if not force:
            non_prewarm = [
                u for u in idle_units
                if not u.session_id.startswith(PREWARM_SESSION_PREFIX)
            ]
            if non_prewarm:
                idle_units = non_prewarm  # spare prewarm, evict a real orphan
            else:
                # All remaining candidates are prewarm → refuse graceful eviction
                # so the caller queues; force=True (which skips this whole block)
                # will reclaim the prewarm if the slot is truly needed. (idle_units
                # is guaranteed non-empty here — the empty cases returned above.)
                logger.info(
                    "session_router.evict_deferred: only prewarm units evictable "
                    "on graceful attempt — deferring to queue+force (anti-starvation)",
                )
                return False

        # Resource-aware eviction: prefer the unit consuming the most
        # memory (RSS) so the freed slot gives maximum headroom for the
        # incoming spawn.  Falls back to oldest-idle when metrics are
        # unavailable (e.g. psutil not installed).
        def _eviction_key(u: SessionUnit) -> tuple:
            metrics = getattr(u, "_last_metrics", None)
            rss = metrics.rss_bytes if metrics else 0
            # Primary: highest RSS first (negative for descending sort)
            # Secondary: oldest idle first (ascending last_used)
            return (-rss, u.last_used)

        idle_units.sort(key=_eviction_key)
        victim = idle_units[0]
        logger.info(
            "session_router.evict session_id=%s (idle %.0fs%s)",
            victim.session_id,
            now - victim.last_used,
            ", forced" if force else "",
        )

        # Fire hooks before killing — Gap 1 fix
        if self._lifecycle_manager and not victim._hooks_enqueued:
            await self._lifecycle_manager.enqueue_hooks_for_unit(victim)
            victim._hooks_enqueued = True

        await victim.kill()
        return True

    def _on_unit_state_change(
        self, session_id: str, old_state: SessionState, new_state: SessionState,
    ) -> None:
        """Callback from SessionUnit state transitions.

        When a unit transitions from a protected state to IDLE or COLD,
        signal the slot_available event so queued requests can proceed.
        """
        if old_state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            if new_state in (SessionState.IDLE, SessionState.COLD, SessionState.DEAD):
                self._slot_available.set()
        # A slot freed (a live unit stopped streaming, or its process ended): the
        # sessions whose message timed out waiting for one get a try now.
        _alive = (SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT)
        if (getattr(self, "_awaiting_slot", None)
                and old_state in _alive and new_state != old_state
                and (new_state in (SessionState.COLD, SessionState.DEAD)
                     or old_state != SessionState.IDLE)):
            waiting, self._awaiting_slot = self._awaiting_slot - {session_id}, set()
            for s in waiting:
                self.kick(s)

        # The session's state, pushed to its tabs as it changes (core/session_state).
        if not session_id.startswith(PREWARM_SESSION_PREFIX):
            from . import session_state
            session_state.refresh_activity(session_id, self._units.get(session_id))
            session_state.publish(self, session_id)

        # H2 single-sink desktop-pool cleanup (方案A, run_f107f442): EVERY kill
        # path (evict / TTL / orphan / crash / poison / shutdown) funnels through
        # _transition(DEAD) → here. Keyed on the `prewarm-` PREFIX (no is_prewarm
        # field, IMPROVEMENT.md:1400). One point covers all kill paths — an
        # unadopted pooled unit that dies leaves no dangling pool/meta entry.
        #
        # ⚠️ INTENTIONALLY LOCK-FREE (Gate-2 verified, NOT a protocol violation):
        # this callback fires SYNCHRONOUSLY from SessionUnit._transition, and
        # _evict_idle calls `await victim.kill()` → _transition(DEAD) → here WHILE
        # HOLDING _slot_lock. Taking _slot_lock here would be a re-entrant
        # asyncio-lock DEADLOCK (asyncio.Lock is non-reentrant). It is safe
        # without the lock because: (a) a no-await sync callback is atomic in the
        # single-thread event loop — no coroutine (incl. the warmer, which only
        # yields at its own `await`) can interleave between the snapshot and the
        # pops; (b) the `list(...)` snapshot prevents dict-changed-during-iteration.
        # Same lock-free-by-design precedent as `_slot_available.set()` +
        # `enqueue_drain` above (the F1 no-re-enter-send rule). _transition is
        # never invoked from a worker thread (only run_in_executor for RSS/kill
        # subprocess work, never for a transition).
        if new_state == SessionState.DEAD and session_id.startswith(PREWARM_SESSION_PREFIX):
            for k in [k for k, v in list(self._desktop_prewarm_pool.items()) if v == session_id]:
                self._desktop_prewarm_pool.pop(k, None)
            self._desktop_prewarm_meta.pop(session_id, None)

        # Root-1 SSOT Phase 2 (L3): a transition INTO a clean IDLE is the trigger
        # to drain any pending messages that arrived while the session was busy.
        # Enqueue is non-blocking (put_nowait) and takes NO lock — this runs on
        # the streaming/hook stack, so we must NEVER drain (call send()) inline
        # here (F1 deadlock/recursion). The serial worker does the actual drain.
        if new_state == SessionState.IDLE:
            # pending messages, and a CLI-initiated turn that arrived while the unit
            # was busy, run now (drain_pending / the sink decide from the state)
            self.kick(session_id)

    # ── CLI-initiated turns (core/cli_stream_reader.py) ───────────

    def _on_unit_cli_event(self, session_id: str, kind: str) -> None:
        """Callback from a unit's stream reader (runs on the reader's task).

        ``injected``: the CLI started a turn nobody claimed → run it through the
        sink. ``idle``: the CLI is quiescent again → pending messages the send
        gate deferred may be delivered. Both are non-blocking enqueues (F1)."""
        if kind == "injected":
            self.enqueue_injected(session_id)
        elif kind == "idle":
            self.enqueue_drain(session_id)
        elif kind == "background_agent":
            self._publish_background_agents(session_id)
        elif kind == "activity":
            # the unit's Stop flag changed (docs/design/chat-turn-liveness.md S1)
            from . import session_state
            session_state.refresh_activity(session_id, self._units.get(session_id))
        elif kind == "stop_done":
            # a user Stop recycled the subprocess to COLD: what was queued meanwhile
            # goes now (drain_pending decides from the state; a turn still open
            # kicks again at its end)
            if session_events.hub().take_waiters(session_id):
                self.kick(session_id)

    def _publish_background_agents(self, session_id: str) -> None:
        """A background agent changed status (core/background_agents.py): publish the
        session's agents to the window's event stream (a ``background_agents``
        envelope outside any turn), and write a final status into its stored Agent
        result so a reload shows it."""
        unit = self._units.get(session_id)
        from .background_agents import BackgroundAgents
        reg = getattr(unit, "background_agents", None)
        changes = getattr(unit, "_bg_agent_changes", None)
        if not isinstance(reg, BackgroundAgents) or not isinstance(changes, deque):
            return
        try:
            session_events.hub().publish(
                session_id, {"type": "background_agents", "agents": reg.snapshot()}, turn_id="")
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.warning("session_router.background_agents_publish_failed session_id=%s: %s",
                           session_id, exc)
        while changes:
            entry = changes.popleft()
            if entry.get("status") == "running":
                continue
            sub = {k: entry[k] for k in ("status", "durationMs", "toolUses", "summary") if k in entry}
            sub["background"] = True  # it ran in the background (the row says so after a reload)
            try:
                asyncio.ensure_future(self._store_background_agent(session_id, entry["toolUseId"], sub))
            except RuntimeError:
                pass  # no running loop (sync test)

    async def _store_background_agent(self, session_id: str, tool_use_id: str, sub: dict) -> None:
        try:
            from database import db
            await db.messages.update_agent_result(session_id, tool_use_id, sub)
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.warning("session_router.background_agent_store_failed session_id=%s: %s",
                           session_id, exc)

    def enqueue_injected(self, session_id: str) -> None:
        """Start the session's sink task unless one is running. Non-blocking."""
        unit = self._units.get(session_id)
        queued = getattr(unit, "_injected_turns", None)
        # A real deque only: a test double for a unit is never driven here.
        if not isinstance(queued, deque) or not queued:
            return
        task = self._injected_tasks.get(session_id)
        if task is not None and not task.done():
            return
        try:
            self._injected_tasks[session_id] = asyncio.ensure_future(
                self._run_injected_turns(session_id))
        except RuntimeError:
            # No running loop (sync unit test): re-driven on the next IDLE edge.
            self._injected_tasks.pop(session_id, None)

    async def _run_injected_turns(self, session_id: str) -> None:
        """Run the unit's queued CLI-initiated turns, one at a time, while it is
        in a clean IDLE. A turn left queued (the unit got busy) is re-driven by
        the next IDLE edge. Never raises out."""
        try:
            while True:
                unit = self._units.get(session_id)
                if unit is None:
                    return
                # one turn at a time: an open turn's end (or a Stop's) re-drives
                # this (docs/design/chat-turn-liveness.md S1)
                if self.is_busy(session_id):
                    return
                ts = unit.take_injected_turn()
                if ts is None:
                    return
                await self.run_injected_turn(unit, ts)
        except Exception as exc:  # noqa: BLE001 — a background sink never raises out
            logger.warning("session_router.injected_sink_error session_id=%s: %s",
                           session_id, exc, exc_info=True)

    async def run_injected_turn(self, unit: SessionUnit, ts: Any) -> None:
        """Persist one CLI-initiated turn (a background task finished and the CLI
        answered it on its own).

        The unit goes IDLE → STREAMING (``begin_injected_turn``), so the tab's
        streaming-state poll shows the spinner and a user message arriving now is
        queued; the frames go through the same ``_read_formatted_response`` and
        ``_persist_stream_event`` as a user turn, under the label ``injected``
        and a fresh client_id; the orchestrator's result handling returns the
        unit to IDLE. A question or permission inside the turn pauses it in
        WAITING_INPUT exactly as a user turn (its continuation reuses the turn's
        client_id). A failure follows the one policy of every turn
        (``SessionUnit.read_with_recovery``, docs/design/chat-turn-liveness.md S2):
        restarted with --resume up to the retry limit, each try and the final
        give-up written with the turn; the ``background_finished`` line is written
        once, before the read, and a retry never writes it again."""
        from .cli_stream_reader import TurnAbandoned
        session_id = unit.session_id
        client_id = f"injected-{uuid4()}"
        unit.begin_injected_turn(ts, client_id)
        logger.info("session_router.injected_turn_start session_id=%s client_id=%s",
                    session_id, client_id)
        row_key = f"{client_id}-asst"
        writer = TurnWriter(self, session_id, row_key=row_key, label="injected")

        async def persisted_frames() -> AsyncIterator[dict]:
            # What this CLI turn answers: the background agents that finished since
            # the last one (one line at its top), persisted like any block.
            from .background_agents import BackgroundAgents
            _bg = getattr(unit, "background_agents", None)
            finished = _bg.take_unannounced() if isinstance(_bg, BackgroundAgents) else []
            if finished:
                block = {"type": "background_finished", "agents": [
                    {k: a[k] for k in ("toolUseId", "description", "status", "durationMs", "toolUses", "summary") if k in a}
                    for a in finished]}
                event0 = {"type": "assistant", "content": [block], "model": None}
                await writer.write(event0)
                yield event0
            # Persist first, then hand the event on to be published, so a tab
            # that reloads on turn_end finds every row it was shown live.
            async for event in unit.read_with_recovery():
                if not isinstance(event, dict) or event.get("_abort"):
                    continue
                extras = await writer.write(
                    event, files=self._turn_files_of(unit),
                    changes=getattr(unit, "_turn_changes", None),
                )
                # the turn's products (turn_deliverables / turn_changes) come
                # before its result, as on the other turn paths
                for _extra in extras:
                    yield _extra
                yield event

        try:
            async with aclosing(self._published_turn(
                persisted_frames(), session_id=session_id, turn_client_id=row_key,
                source="injected", start_on_session_start=False, writer=writer,
            )) as turn:
                async for _published in turn:
                    pass
        except TurnAbandoned as exc:
            logger.warning("session_router.injected_turn_abandoned session_id=%s: %s",
                           session_id, exc)
            if unit.state == SessionState.STREAMING:
                unit._transition(SessionState.IDLE)
        except Exception as exc:  # noqa: BLE001
            # not retriable: read_with_recovery already sent the unit to COLD
            logger.warning("session_router.injected_turn_failed session_id=%s: %s: %s",
                           session_id, type(exc).__name__, exc)
            if unit.state == SessionState.STREAMING:   # an error outside the read
                await unit._crash_to_cold_async(clear_identity=False)
        logger.info("session_router.injected_turn_end session_id=%s state=%s",
                    session_id, unit.state.value)

    # ── Drain worker (Root-1 SSOT Phase 2, L3) ────────────────────

    def enqueue_drain(self, session_id: str) -> None:
        """Signal that ``session_id`` may have pending messages to drain.

        Non-blocking, lock-free, idempotent (a session already queued is not
        re-added). Lazily starts the serial drain worker on first call so it
        binds to the running loop. Safe to call from inside ``_transition``.
        """
        if session_id in self._drain_enqueued:
            return
        try:
            self._drain_enqueued.add(session_id)
            self._drain_queue.put_nowait(session_id)
        except Exception as exc:  # noqa: BLE001
            # The discard correctly un-marks the session so a later enqueue can retry,
            # but silently: this session simply never drains, and the only symptom is
            # work that quietly did not happen.
            logger.warning("could not enqueue drain for session %s: %s",
                           session_id, exc)
            self._drain_enqueued.discard(session_id)
            return
        if self._drain_worker_task is None or self._drain_worker_task.done():
            try:
                self._drain_worker_task = asyncio.ensure_future(self._drain_worker())
            except RuntimeError:
                # No running loop (e.g. unit tests calling enqueue synchronously
                # without a loop) — the worker will be started on a later enqueue
                # from within an async context.
                self._drain_worker_task = None

    async def _drain_worker(self) -> None:
        """Long-lived single consumer: drains ONE session at a time, serially,
        OUTSIDE any transition stack (F1). Never raises out — a per-session drain
        failure is logged and the loop continues."""
        while True:
            session_id = await self._drain_queue.get()
            self._drain_enqueued.discard(session_id)
            try:
                await self.drain_pending(session_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "session_router.drain_worker_error session_id=%s: %s",
                    session_id, exc,
                )
            finally:
                self._drain_queue.task_done()

    async def drain_pending(self, session_id: str) -> None:
        """Coalesce-drain the whole pending set for one session as ONE turn.

        Precondition (ALL must hold, else no-op — re-driven on the next IDLE edge):
          - the unit exists, is alive, and is in a clean IDLE
          - NO outstanding tool_use (F3 — never start a new turn while an
            AskUserQuestion / cmd_permission awaits its tool_result)

        The whole pending set is CLAIMED atomically (claim_pending_batch: sent
        stays 0, claimed_at set). Outside any lock, combine_pending merges the set
        into one payload and send() delivers it; on success mark_sent_batch flips
        the set to sent=1, on failure rollback_claim_batch returns it to pending
        so it re-coalesces on the next IDLE (F4 — no message lost).
        """
        from . import session_pending

        unit = self._units.get(session_id)
        if unit is None:
            return
        # Level-triggered (docs/design/chat-turn-liveness.md S1, v4 RC-B): decided
        # from the session's state now, never from which event asked. A busy session
        # (a turn open or claimed, a user Stop recycling it, the unit streaming /
        # waiting) is left alone: that turn's end, or the Stop's, kicks again. A COLD
        # unit is drained too (run_conversation spawns it with --resume).
        if self.is_busy(session_id):
            # the turn / Stop that holds it kicks again when it ends
            session_events.hub().note_waiter(session_id)
            return
        if unit.state not in (SessionState.IDLE, SessionState.COLD):
            return
        if unit.has_outstanding_tool_use:
            return  # F3: a question is awaiting its answer — do not inject a turn
        if not unit.cli_quiescent:
            return  # the CLI runs a turn of its own; its idle report re-drives this

        claimed = await session_pending.claim_pending_batch(session_id)
        if not claimed:
            return

        user_text, content = session_pending.combine_pending(claimed)
        terminal = session_pending.combine_terminal(claimed)
        seqs = [c.pending_seq for c in claimed]

        # Degenerate guard (Gate-2 F1): a corrupt/empty pending row combines to
        # (None, None). Delivering it hits run_conversation's EMPTY_MESSAGE branch,
        # which would roll back → re-claim → same corrupt row → infinite drain loop.
        # Drop such rows as terminally undeliverable (mark sent so they leave the
        # queue) with a loud log — never silently, never loop.
        if not user_text and not content:
            await session_pending.mark_sent_batch(session_id, seqs)
            self._forget_drain_retry(session_id)
            logger.error(
                "session_router.drain_dropped_corrupt session_id=%s seqs=%s — "
                "pending rows combined to empty payload; marked sent to avoid a "
                "drain loop (message content was unrecoverable)",
                session_id, seqs,
            )
            return

        try:
            # Deliver the coalesced turn. The rows already exist in the DB
            # (sent=0, claimed); run_conversation re-persisting a user row is
            # acceptable here because the claimed rows are flipped to sent=1 on
            # success — the drained turn IS this conversation turn.
            agen = self.run_conversation(
                agent_id=unit.agent_id,
                user_message=user_text,
                content=content,
                session_id=session_id,
                terminal_context=terminal,
                _drained_pending=True,
            )
            # CRITICAL (Gate-2 F1): run_conversation reports many failures as
            # YIELDED error events (SESSION_BUSY on a drain-vs-user race,
            # EMPTY_MESSAGE on a degenerate combine), not raised exceptions — it
            # yields and returns normally. We must NOT mark_sent on those: doing so
            # flips claimed→sent for a turn that never reached the subprocess →
            # permanent message loss. Gate mark_sent on an observed terminal
            # `result` AND the absence of any `error` event; otherwise roll back so
            # the set re-coalesces on the next clean IDLE (F4 — never lose a msg).
            delivered_ok = False
            saw_error: Optional[str] = None
            saw_terminal = False
            async for _evt in agen:
                _t = _evt.get("type") if isinstance(_evt, dict) else None
                if _t == "assistant" and seqs and _evt.get("content"):
                    # The model is answering the drained messages: they were
                    # delivered. Marked sent NOW (not at `result`): a row of the
                    # answer is already in the record, and a crash before `result`
                    # must neither hide the messages it answers nor send them again.
                    await self._finalize_pending_delivery(
                        session_id, seqs, delivered_ok=True, saw_error=None, unit=unit)
                    seqs = []
                    continue
                if _t == "error":
                    saw_error = (_evt.get("code") or "error") if isinstance(_evt, dict) else "error"
                    # A permanent (non-retriable) error carries terminal=True — the
                    # finalize must terminally-drop it, not re-drive it forever.
                    if isinstance(_evt, dict) and _evt.get("terminal"):
                        saw_terminal = True
                elif _t == "result":
                    delivered_ok = True
            await self._finalize_pending_delivery(
                session_id, seqs, delivered_ok=delivered_ok, saw_error=saw_error,
                terminal=saw_terminal, unit=unit,
            )
        except Exception as exc:
            await session_pending.rollback_claim_batch(session_id, seqs)
            logger.warning(
                "session_router.drain_send_failed session_id=%s seqs=%s: %s — "
                "rolled back to pending",
                session_id, seqs, exc,
            )
            self._retry_drain_later(session_id)

    def _forget_drain_retry(self, session_id: str) -> None:
        """Drop a session's delayed-drain count and cancel its timer (delivered,
        given up, cancelled)."""
        getattr(self, "_drain_retries", {}).pop(session_id, None)
        t = getattr(self, "_drain_retry_timers", {}).pop(session_id, None)
        if t is not None:
            t.cancel()

    #: the waits before each delayed retry of a failed drain (the send itself has
    #: already retried with --resume); then the rows stay queued, shown and
    #: cancellable, until the next send or IDLE edge
    _DRAIN_RETRY_DELAYS = (15.0, 60.0, 240.0)

    def _retry_drain_later(self, session_id: str) -> None:
        """Messages a drain claimed went back to pending (the delivery failed):
        try them again after a growing wait, a bounded number of times
        (docs/design/chat-turn-liveness.md S2). Before this they waited for the
        next IDLE edge or send, which may never come. Never raises."""
        retries = getattr(self, "_drain_retries", None)
        timers = getattr(self, "_drain_retry_timers", None)
        if retries is None or timers is None:
            return
        n = retries.get(session_id, 0)
        if n >= len(self._DRAIN_RETRY_DELAYS):
            self._forget_drain_retry(session_id)
            logger.warning("session_router.drain_retry_gave_up session_id=%s after=%d — "
                           "the messages stay queued", session_id, n)
            return
        retries[session_id] = n + 1

        def fire() -> None:
            timers.pop(session_id, None)
            if session_id not in self._units:
                retries.pop(session_id, None)
                return
            self.kick(session_id)
        old = timers.pop(session_id, None)
        if old is not None:
            old.cancel()
        try:
            timers[session_id] = asyncio.get_running_loop().call_later(
                self._DRAIN_RETRY_DELAYS[n], fire)
        except RuntimeError:
            retries.pop(session_id, None)   # no loop: nothing to schedule on
        logger.info("session_router.drain_retry_scheduled session_id=%s attempt=%d in=%.0fs",
                    session_id, n + 1, self._DRAIN_RETRY_DELAYS[n])

    async def _hand_back_send(
        self, client_id: Optional[str], session_id: str, agent_id: str,
        user_message: Optional[str], content: Optional[list], user_message_id: Optional[str],
        terminal_context: Optional[dict] = None,
    ) -> dict:
        """The server could not keep this send (its row could not be made pending):
        hand it back for the client to send again — with its client_id, so the
        resend is the same send, and its terminal output, so the resend carries it.
        The written row, never delivered, is removed and
        the ledger released (core/send_requests.release), so that resend runs once
        and the transcript holds the message once."""
        from . import send_requests
        if user_message_id:
            try:
                from database import db
                await db.messages.delete(user_message_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("session_router.hand_back_row_delete_failed session_id=%s msg=%s: %s",
                             session_id, user_message_id, exc)
        if client_id:
            await send_requests.release(client_id)
        payload = {"sessionId": session_id, "agentId": agent_id, "userMessage": user_message,
                   "content": content, "clientId": client_id}
        tail = (terminal_context or {}).get("buffer_tail")
        if isinstance(tail, str) and tail:
            payload["terminalContext"] = {"bufferTail": tail,
                                          "cwd": (terminal_context or {}).get("cwd") or ""}
        return payload

    async def _finalize_pending_delivery(
        self,
        session_id: str,
        seqs: list[int],
        *,
        delivered_ok: bool,
        saw_error: Optional[str],
        terminal: bool = False,
        unit: Optional[Any] = None,
    ) -> None:
        """Shared exactly-once close-out for a CLAIMED pending set (run_25d2b7b8).

        ONE lifecycle, two triggers: both the serial IDLE-edge drain
        (``drain_pending``) and the new cold-spawn coalesce path
        (``run_conversation``) call this after observing the delivery outcome of
        the coalesced turn, so the F4 mark/rollback decision lives in exactly one
        place and cannot drift between the two triggers.

        Operates ONLY on the claimed pending ``seqs`` — NEVER the arriving user
        row (Gate-1 constraint #2: the cold-spawn path is a normal send that
        persists the new message itself; mark_sent/rollback must not touch it).

        - delivered (``result`` observed AND no ``error`` event) → ``mark_sent_batch``
          flips the claimed set sent=1 (guarded by ``claimed_at IS NOT NULL`` so a
          concurrently-reopened row is a no-op).
        - TERMINAL error (``terminal=True`` — a PERMANENT, non-retriable failure the
          orchestrator already classified past ``_is_retriable_error``, e.g. a 400
          geo-block) → ``mark_sent_batch`` + loud log: terminally-undeliverable. We do
          NOT roll back, because re-sending reproduces the SAME error — rolling back
          re-drives it on every IDLE edge forever (the poison_guard 764x-recycle
          engine, run_29959bcd). Mirrors the degenerate-empty-payload guard in
          ``drain_pending`` which also mark_sents + loud-logs to break an identical
          loop. The error event was already yielded to the user upstream, so the
          failure is surfaced — mark_sent here only stops the infinite retry, it does
          not hide anything.
        - otherwise (TRANSIENT error / no result) → ``rollback_claim_batch`` returns the
          set to pending (sent=0, claimed_at cleared) so it re-coalesces later; no
          message is lost. This preserves the existing exponential-retry behavior for
          genuinely transient failures (throttle/5xx/timeout).
        """
        from . import session_pending
        if not seqs:
            return
        if delivered_ok and saw_error is None:
            await session_pending.mark_sent_batch(session_id, seqs)
            self._forget_drain_retry(session_id)
            logger.info(
                "session_router.pending_delivered session_id=%s count=%d seqs=%s",
                session_id, len(seqs), seqs,
            )
            # Record the delivered seqs on the unit so the streaming-state read
            # API (L5) can surface "these pending rows were delivered" to the
            # frontend mirror. Best-effort; FE also learns via count→0.
            if unit is not None:
                try:
                    unit._last_drained_seqs = list(seqs)
                except Exception:
                    logger.debug("_finalize_pending_delivery: suppressed exception on a fail-safe path", exc_info=True)
                    pass
        elif saw_error is not None and terminal:
            # Terminally-undeliverable: a permanent, non-retriable error. mark_sent
            # (NOT rollback) so the row leaves the pending queue and is never
            # re-drained — this is the cut that stops the infinite re-drive loop.
            await session_pending.mark_sent_batch(session_id, seqs)
            self._forget_drain_retry(session_id)
            logger.warning(
                "session_router.pending_terminally_undeliverable session_id=%s "
                "seqs=%s error=%s — permanent (non-retriable) failure; marked sent "
                "to stop infinite re-drive (error already surfaced to user)",
                session_id, seqs, saw_error,
            )
        else:
            await session_pending.rollback_claim_batch(session_id, seqs)
            logger.warning(
                "session_router.pending_not_delivered session_id=%s seqs=%s "
                "error=%s delivered_ok=%s — rolled back to pending",
                session_id, seqs, saw_error, delivered_ok,
            )
            self._retry_drain_later(session_id)

    async def _claim_cold_spawn_pending(
        self, unit: Any, session_id: str,
    ) -> list:
        """Claim the pending set for a turn that is ABOUT to cold-spawn (run_25d2b7b8).

        The IDLE-only serial drain worker (``enqueue_drain`` on the IDLE
        transition) loses a TOCTOU race on the self-heal path: when a user resend
        re-spawns a COLD session, ``send()`` passes through a TRANSIENT IDLE and
        advances to STREAMING before the serial worker gets the loop, so
        ``drain_pending``'s ``state != IDLE`` guard no-ops and the row strands.
        This claims the set at the reliable point — the router, before
        ``unit.send()`` — so the imminent send can coalesce it into its own turn.

        No-op (returns ``[]``) unless the unit is genuinely about to cold-spawn
        (``state == COLD``): on an IDLE/STREAMING/WAITING_INPUT unit the existing
        IDLE-edge drain owns delivery, so claiming here would double-handle.

        Exactly-once: ``claim_pending_batch`` is atomic (sent=0 AND
        claimed_at IS NULL under the per-session lock); if the serial worker
        already claimed, this gets ``[]`` and vice-versa — never a double-deliver.
        """
        from . import session_pending
        if unit is None or getattr(unit, "state", None) != SessionState.COLD:
            return []
        return await session_pending.claim_pending_batch(session_id)

    def _merge_pending_into_query(self, claimed: list, query_content: Any) -> Any:
        """Merge a claimed pending set INTO the arriving turn's query_content
        (run_25d2b7b8), pending FIFO FIRST, the arriving message LAST.

        Latest-last mirrors ``combine_pending``'s product decision: a correction
        reads as the most-salient (final) instruction. Text and multimodal are
        handled the same way ``combine_pending`` does — a text-only set joins with
        ``\\n\\n``; any multimodal row flattens the whole thing to a block list.

        The arriving ``query_content`` is appended as the final element so the two
        shapes compose: str + str → joined text; any list present → block list.
        """
        from . import session_pending
        if not claimed:
            return query_content
        pending_text, pending_blocks = session_pending.combine_pending(claimed)
        arriving_is_list = isinstance(query_content, list)
        # Multimodal on either side → unify to a block list (pending first, last).
        if pending_blocks is not None or arriving_is_list:
            blocks: list[dict] = []
            if pending_blocks is not None:
                blocks.extend(pending_blocks)
            elif pending_text:
                blocks.append({"type": "text", "text": pending_text})
            if arriving_is_list:
                blocks.extend(query_content)
            elif isinstance(query_content, str) and query_content:
                blocks.append({"type": "text", "text": query_content})
            return blocks
        # Pure text path: pending first, arriving last.
        arriving_text = query_content if isinstance(query_content, str) else ""
        if pending_text and arriving_text:
            return f"{pending_text}\n\n{arriving_text}"
        return pending_text or arriving_text

    # ── Public API ────────────────────────────────────────────────

    # ── Session event publishing (core/session_events) ────────────────

    async def _published_turn(
        self,
        agen: AsyncIterator[dict],
        *,
        session_id: Optional[str],
        turn_client_id: Optional[str],
        source: str,
        start_on_session_start: bool,
        writer: TurnWriter,
        opener: Optional["TurnOpener"] = None,
    ) -> AsyncIterator[dict]:
        """Run one turn's event generator and publish every event of the turn.

        The single place every router turn goes through (run_conversation,
        continue_with_answer, continue_with_cmd_permission, run_injected_turn),
        so a subscribed tab sees every turn of its session no matter who started
        it. The turn starts at the turn's ``session_start`` when
        ``start_on_session_start`` (run_conversation: events before it, such as
        ``queued`` or a SESSION_BUSY rejection, are replies to the POST that sent
        them and are yielded unstamped), else at the first event. From then on
        ``turn_start``, every event and, in ``finally``, ``turn_end`` are
        published; the caller receives the stamped envelopes (``turnId``,
        ``eventSeq``) so a POST stream can bind its turn. The inner generator is
        closed with ``aclosing`` whenever this one is, so its ``finally`` blocks
        (pending rollback, slot release) run exactly as when it was iterated
        directly.

        ``writer`` is the turn's ``TurnWriter`` (the inner generator writes each
        event through it before yielding it): closed here, in ``finally`` and
        before ``turn_end`` is published, however the turn ends — so what the
        turn still held (step results waiting for the next assistant row) is
        written, and is in the DB when a tab reloads on ``turn_end``. Before it, a
        started turn's ``turn_outcome`` block (core/turn_outcome: how the turn ended,
        from the events published here and an exception leaving the turn) is
        written through the same writer and published as one more assistant event
        (through the hub only when the turn ended by an exception or a close); a turn
        that never started writes none.

        ``opener`` (run_conversation, docs/design/chat-turn-liveness.md S1): the
        inner generator claims the turn itself at admission (``TurnOpener.open``,
        before the slot), so from then on the session's activity shows a turn in
        progress; until the turn starts its ``turn_phase`` events move the claim's
        phase (they still reach only the POST, as before). ``accepted`` and any
        event carrying ``_post_only`` are replies to the POST alone and are never
        published. The claim ends at the turn's ``turn_end``, or here when the turn
        never started (queued, rejected, cancelled).

        The session's activity is refreshed whenever the turn's phase changes, and
        the turn's end re-drives the session's pending messages and CLI-initiated
        turns (both wait for no turn to be open or claimed).
        """
        from . import session_state as _ss
        from .turn_outcome import OutcomeRecorder
        hub = session_events.hub()
        turn_id = opener.turn_id if opener is not None else uuid4().hex[:12]
        sid = session_id
        started = False
        outcome = OutcomeRecorder()   # how the turn ended: written at its end
        outcome_written = False

        def unit_of(s):
            return getattr(self, "_units", {}).get(s)

        def publish(ev: dict) -> dict:
            before = hub.open_phase(sid)
            env = hub.publish(sid, ev, turn_id=turn_id, turn_client_id=turn_client_id)
            if hub.open_phase(sid) != before:
                _ss.refresh_activity(sid, unit_of(sid))
            return env

        async with aclosing(agen) as inner:
            try:
                async for ev in inner:
                    if not isinstance(ev, dict):
                        yield ev
                        continue
                    if ev.get("type") == "accepted" or ev.pop("_post_only", False):
                        yield ev
                        continue
                    if not started:
                        if (opener is not None and opener.opened
                                and ev.get("type") == "turn_phase"
                                and hub.note_phase(opener.session_id, turn_id, ev.get("phase"))):
                            _ss.refresh_activity(opener.session_id, unit_of(opener.session_id))
                        if start_on_session_start and ev.get("type") != "session_start":
                            yield ev
                            continue
                        sid = ev.get("sessionId") or ev.get("session_id") or sid
                        if sid is None:
                            yield ev
                            continue
                        started = True
                        writer.begin(sid)
                        yield publish({"type": "turn_start", "source": source})
                    outcome.note(ev, getattr(unit_of(sid), "pid", None)
                                 if ev.get("type") == "result" else None)
                    yield publish(ev)
                if started:
                    # the turn ran to its end: the outcome goes out like any block of
                    # the turn, so a live tab holds what a reload shows
                    block = await self._write_turn_outcome(writer, sid, outcome)
                    outcome_written = True   # after the write: a cancel inside it retries below
                    if block is not None:
                        yield publish({"type": "assistant", "content": [block], "model": None})
            except Exception as exc:
                # an unexpected exception: the SSE wrapper builds its error frame
                # outside this turn, so the outcome names the crash here
                outcome.crashed(exc)
                raise
            finally:
                try:
                    if started and not outcome_written:
                        # an exception or a close: nothing can be yielded any more, so
                        # the block reaches subscribed tabs through the hub only (the
                        # writer writes one outcome per turn, so a retry never doubles)
                        block = await self._write_turn_outcome(writer, sid, outcome)
                        if block is not None:
                            publish({"type": "assistant", "content": [block], "model": None})
                finally:
                    # however the outcome write went (a second cancel can land in it),
                    # the turn is closed, ended and its session released
                    await writer.close()
                    ended: set = set()
                    if started:
                        publish({"type": "turn_end"})   # ends the claim of this turn too
                        ended.add(sid)
                    if opener is not None and opener.opened and opener.session_id not in ended:
                        # claimed, never started (queued, rejected, cancelled)
                        hub.release_claim(opener.session_id, turn_id)
                        _ss.refresh_activity(opener.session_id, unit_of(opener.session_id))
                        ended.add(opener.session_id)
                    # the session is free of this turn: if messages were queued while it
                    # held the session, they go now (one flag, set wherever a message is
                    # queued for being busy; no other trigger is needed — v4 RC-B)
                    for s in ended:
                        if hub.take_waiters(s):
                            self.kick(s)

    async def _write_turn_outcome(self, writer: TurnWriter, session_id: Optional[str],
                                  outcome: object) -> Optional[dict]:
        """Write the started turn's ``turn_outcome`` block (core/turn_outcome), with
        what the turn did to each repository its session declared (core/task_repos);
        returns it, or None when it was not written. A failure is logged and never
        holds the turn's end."""
        try:
            repos = await self._task_repo_facts(session_id)
            block = outcome.block(repos)   # type: ignore[attr-defined]
            return block if await writer.record_outcome(block) else None
        except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
            logger.error("turn_outcome.write_failed session_id=%s: %s", session_id, exc)
            return None

    #: hang guard for reading every declared repository at a turn end
    TASK_REPO_FACTS_TIMEOUT_S = 30

    async def _task_repo_facts(self, session_id: Optional[str]) -> Optional[list]:
        """Facts of the repositories the session's dispatch declared
        (``sessions.metadata.task_repos``), or None when it declared none."""
        if not session_id:
            return None
        from database import db
        from . import executors, task_repos
        row = await db.sessions.get(session_id)
        meta = (row or {}).get("metadata")
        declared = meta.get("task_repos") if isinstance(meta, dict) else None
        if not declared:
            return None
        try:
            repos = task_repos.parse(declared)
        except task_repos.TaskRepoError as exc:
            logger.warning("task_repos: session %s carries an invalid list: %s", session_id, exc)
            return None
        from .project_registry import get_swarmws
        since = None                       # the session's start: where each base is read
        try:
            since = datetime.fromisoformat(str(row.get("created_at"))).timestamp()
        except (TypeError, ValueError):
            logger.warning("task_repos: session %s has no readable start time", session_id)
        try:
            return await asyncio.wait_for(
                executors.run_in("turn_changes", task_repos.repo_facts, str(get_swarmws()),
                                 repos, since),
                self.TASK_REPO_FACTS_TIMEOUT_S)
        except asyncio.TimeoutError:
            return [{"url": r["url"], "ref": r["ref"], "dir": r["dir"], "present": None,
                     "error": task_repos.ERR_TIMEOUT} for r in repos]

    async def run_conversation(
        self,
        agent_id: str,
        user_message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        session_id: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        channel_context: Optional[dict] = None,
        editor_context: Optional[dict] = None,
        terminal_context: Optional[dict] = None,
        agent_config: Optional[dict] = None,
        client_id: Optional[str] = None,
        _drained_pending: bool = False,
        project_agent: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """Entry point for chat requests: one turn, published to the session hub.

        ``project_agent``: the pin of a NEW session that works under a Brain's Project
        Agent (core.project_agent_session.make_pin); its hooks enforce it from the
        first tool call and it is recorded on the session.

        A turn without a client_id of its own (drain, channel, task, skill
        creator) gets a server one, so its persisted assistant rows and a
        subscribed tab's live bubble share the ``{client_id}-asst`` key (no
        duplicate bubble at the turn-end reconcile). Safe for the continuation
        stash: ``_run_conversation_impl`` writes ``unit._turn_client_id`` only
        after send() admitted the turn. See ``_published_turn`` for what is
        published; ``_run_conversation_impl`` does the turn.
        """
        source = "drain" if _drained_pending else ("channel" if channel_context else "chat")
        if client_id is None:
            client_id = f"{source}-{uuid4().hex}"
        writer = TurnWriter(self, session_id, row_key=f"{client_id}-asst")
        opener = TurnOpener(source=source,
                            turn_client_id=f"{client_id}-asst" if client_id else None)
        agen = self._run_conversation_impl(
            agent_id=agent_id, user_message=user_message, content=content,
            session_id=session_id, enable_skills=enable_skills, enable_mcp=enable_mcp,
            channel_context=channel_context, editor_context=editor_context,
            terminal_context=terminal_context, agent_config=agent_config,
            client_id=client_id, _drained_pending=_drained_pending, writer=writer,
            opener=opener, project_agent=project_agent,
        )
        async with aclosing(self._published_turn(
            agen, session_id=session_id,
            turn_client_id=f"{client_id}-asst" if client_id else None,
            source=source,
            start_on_session_start=True,
            writer=writer,
            opener=opener,
        )) as turn:
            async for ev in turn:
                yield ev

    def is_busy(self, session_id: str) -> bool:
        """THE "is this session busy" check (docs/design/chat-turn-liveness.md S1,
        v4 RC-A): a turn of it is open or claimed (hub), a user Stop is recycling it,
        or its unit streams / waits for input. Every path that would start a turn on
        the session, or kill / evict / purge / release its unit, asks this — the unit
        state alone does not see a turn admitted but not yet sending."""
        unit = getattr(self, "_units", {}).get(session_id)
        if unit is not None:
            if unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
                return True
            if getattr(unit, "_stopping", False) is True:
                return True
        return session_events.hub().open_turn_id(session_id) is not None

    def queued_while_busy(self, session_id: str) -> None:
        """A message of the session was just made pending because the session was
        busy (docs/design/chat-turn-liveness.md S1 v4 RC-B). Whatever holds it kicks
        the drain when it ends (``take_waiters`` at a turn's / claim's end, a Stop's
        end); and if it already ended while the row was converted, kick now."""
        hub = session_events.hub()
        hub.note_waiter(session_id)
        self.publish_queue(session_id)
        if not self.is_busy(session_id) and hub.take_waiters(session_id):
            self.kick(session_id)

    def publish_queue(self, session_id: str) -> None:
        """The session's queue changed: push its state (it carries the queue) to its
        tabs, which show the queue from it (docs/design/chat-turn-liveness.md S3).
        Scheduled, never raises (core/session_state.publish)."""
        from . import session_state
        session_state.publish(self, session_id)

    def kick(self, session_id: str) -> None:
        """The session may be free now: try its pending messages and its queued
        CLI-initiated turns (docs/design/chat-turn-liveness.md S1, v4 RC-B). One
        call for every such moment (a turn's end, a released claim, a Stop's end, an
        IDLE edge, a message turned pending); both workers decide from the state
        then, so a kick that comes too early costs one check. Non-blocking (F1).
        (getattr: tests build a router with __new__.)"""
        if getattr(self, "_units", None) is None:
            return
        self.enqueue_drain(session_id)
        self.enqueue_injected(session_id)

    # ── Steer: a message written into the running turn (design chat-steer) ──

    STEER_SETTING = "steer_busy_send"   # app config, on by default (app_config_manager)

    @classmethod
    def _steer_enabled(cls) -> bool:
        try:
            from .app_config_manager import AppConfigManager
            return AppConfigManager.instance().get(cls.STEER_SETTING, True) is True
        except Exception as e:  # noqa: BLE001 — config unreadable: off
            # debug, not warning: this runs on every busy send and state publish
            logger.debug("steer setting unreadable, steer off: %s", e)
            return False

    def _steer_blocker(self, unit: Any) -> Optional[str]:
        """Why a message to this unit cannot be steered into its running turn now,
        or None (design chat-steer D1). Synchronous: the write path checks it again
        right before the claim, with no await in between (H4). The reason is also
        the queued message's ``queuedReason`` (D7): ``question`` / ``permission``
        (answer that first) or ``busy``."""
        from .session_unit import SessionState
        if unit is None or getattr(unit, "state", None) != SessionState.STREAMING:
            return "busy"
        reader = getattr(unit, "_stream_reader", None)
        if reader is None or reader.error is not None or getattr(unit, "_client", None) is None:
            return "busy"
        # the turn's read loop will still read a steer (D6); and it is a user turn,
        # not one the CLI started (no steer in an injected turn, C)
        if getattr(unit, "_steer_accepting", False) is not True:
            return "busy"
        ts = getattr(unit, "_turn_stream", None)
        if ts is None or getattr(ts, "kind", None) == "injected":
            return "busy"
        if any(getattr(c, "kind", None) != "steer" for c in reader._claims.values()):
            return "busy"   # another message written and not yet read (a continuation)
        if unit.has_outstanding_tool_use:
            q = getattr(unit, "_pending_question", None) or {}
            return "permission" if q.get("request_id") else "question"
        # a Stop clicked: its receipt is being written before interrupt() marks
        # the unit stopping -- a steer now would go to a process about to be recycled
        stops = self.__dict__.get("_stops_in_flight") or ()
        if getattr(unit, "session_id", None) in stops or getattr(unit, "_stop_receipt_at", None) is not None:
            return "busy"
        if (getattr(unit, "_stopping", False) or getattr(unit, "_interrupted", False)
                or getattr(unit, "_tool_hang_interrupt_at", None) is not None
                or getattr(unit, "_retrying", False) or getattr(unit, "_graceful_wrap_pending", False)):
            return "busy"
        return None

    async def _try_steer(self, unit: Any, session_id: str, row_id: Optional[str],
                         client_id: Optional[str], content: Any,
                         editor_context: Optional[dict],
                         terminal_context: Optional[dict]) -> bool:
        """Write the message into the running turn (design chat-steer D2). True: it
        is on the CLI's stdin as a ``priority: next`` message and its row is a steer
        in flight; False: nothing written, the caller queues it as before."""
        from . import session_pending
        if not (row_id and client_id and content) or not self._steer_enabled():
            return False
        if self._steer_blocker(unit) is not None:
            return False
        # the message as the CLI takes it, with the prefixes a send carries on a live
        # process (D2 table): attachments, UI state, terminal outermost; no recall
        query = await _convert_attachment_blocks(content, session_id)
        ui_block = _decide_ui_block(unit, editor_context, True)
        query = _prepend_dynamic_context_to_query(query, editor_context, recall_block=None,
                                                  should_prefix=True, ui_block=ui_block)
        query = _prepend_terminal_to_query(query, terminal_context)
        prefix = "\n\n".join(p for p in (_build_terminal_prefix_block(terminal_context),
                                          _build_dynamic_prefix_block(editor_context, None,
                                                                      ui_block=ui_block)) if p)
        try:
            inflight = await session_pending.mark_steer_inflight(
                session_id, row_id, terminal_context=terminal_context)
        except Exception as exc:  # noqa: BLE001 — queue it as before
            logger.warning("session_router.steer_mark_failed session_id=%s: %s", session_id, exc)
            inflight = False
        if not inflight:
            unit._last_ui_block = None   # the CLI did not get that block (L4)
            return False
        async with unit._client_io:
            why = self._steer_blocker(unit)        # again, now: no await from here to the claim
            if why is not None:
                logger.info("session_router.steer_recheck_queued session_id=%s why=%s", session_id, why)
                await self._release_steer_row(unit, session_id, row_id, kick=False)
                return False
            reader = unit._stream_reader
            msg_uuid = str(uuid4())
            preview = (content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"))[:200]
            unit._steer_rows[msg_uuid] = (row_id, client_id, preview)
            unit._steer_hook = self._on_steer_event
            reader.claim(msg_uuid, "steer", client_id=client_id)
            # the SDK user-message shape write_user_message sends, plus priority
            message = {"type": "user", "message": dict(role="user", content=query),
                       "parent_tool_use_id": None, "uuid": msg_uuid, "priority": "next"}

            async def _one():
                yield message
            try:
                await unit._client.query(_one())
            except Exception as exc:  # noqa: BLE001 — not written: queue it (T8)
                logger.warning("session_router.steer_write_failed session_id=%s: %s", session_id, exc)
                reader.drop_claim(msg_uuid)
                unit._steer_rows.pop(msg_uuid, None)
                await self._release_steer_row(unit, session_id, row_id, kick=False)
                return False
        if prefix:
            unit._steer_query_prefixes.append(prefix)
        logger.info("session_router.steer_written session_id=%s uuid=%s client_id=%s",
                    session_id, msg_uuid, client_id)
        self.publish_queue(session_id)   # the snapshot's `steering` list changed
        return True

    async def _release_steer_row(self, unit: Any, session_id: str, row_id: str, *,
                                 kick: bool = True) -> Optional[int]:
        """A steer that was never read goes back to the queue (D2): its row gets a
        seq, the UI block it carried is forgotten (L4), and the drain is kicked."""
        from . import session_pending
        unit._last_ui_block = None
        seq = await session_pending.release_steer(session_id, row_id)
        if seq is not None and kick:
            self.queued_while_busy(session_id)
        return seq

    def _on_steer_event(self, unit: Any, kind: str, uuid: str, outcome: str,
                        echo_seen: bool) -> None:
        """The reader saw a steer's echo, or its end (its task: schedule, never block).
        Echo: the row is history at the echo time -- before the Stop receipt when the
        CLI read it while this turn was being stopped (T29). End without an echo: the
        row goes back to the queue (D2, D8)."""
        entry = unit._steer_rows.get(uuid)
        if entry is None:
            return
        row_id, client_id, _preview = entry
        session_id = unit.session_id
        if kind == "echo":
            echo_at = datetime.now()
            stop_at = getattr(unit, "_stop_receipt_at", None)
            if stop_at is not None and echo_at >= stop_at:
                echo_at = stop_at - timedelta(microseconds=1)
            logger.info("session_router.steer_read session_id=%s client_id=%s outcome=%s",
                        session_id, client_id, outcome)
            self._spawn_steer_task(self._confirm_steer(session_id, row_id, echo_at.isoformat(), outcome))
            return
        unit._steer_rows.pop(uuid, None)
        if not echo_seen:
            logger.info("session_router.steer_never_read session_id=%s client_id=%s outcome=%s",
                        session_id, client_id, outcome)
            self._spawn_steer_task(self._release_steer_row(unit, session_id, row_id))
        self.publish_queue(session_id)

    async def _confirm_steer(self, session_id: str, row_id: str, echo_at: str, outcome: str) -> None:
        from . import session_pending
        # The row must not stay in flight once read (H5): startup would queue it again
        # and it would be sent twice. Retried with backoff (the DB layer already
        # retries "database is locked"); a daemon dying inside this window is the
        # accepted residual risk (design D2).
        for attempt, delay in enumerate((0.0, 0.2, 0.5, 1.0, 2.0), 1):
            if delay:
                await asyncio.sleep(delay)
            try:
                if not await session_pending.confirm_steer(session_id, row_id, echo_at, outcome):
                    logger.error("session_router.steer_confirm_no_row session_id=%s row=%s",
                                 session_id, row_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("session_router.steer_confirm_failed session_id=%s row=%s attempt=%d: %s",
                             session_id, row_id, attempt, exc)

    _STEER_TASKS: "set[asyncio.Task]" = set()

    @classmethod
    def _spawn_steer_task(cls, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        cls._STEER_TASKS.add(task)
        task.add_done_callback(cls._STEER_TASKS.discard)

    async def _queue_admitted_send(self, receipt: dict, session_id: str,
                                   persisted_msg_id: Optional[str],
                                   terminal_context: Optional[dict]) -> list[dict]:
        """A send whose session could not open a turn (docs/design/
        chat-turn-liveness.md S1): its user row becomes a pending message and the
        receipt says ``queued``. Returns the POST's events: the receipt, then the
        same SESSION_BUSY reply (pendingSeq / pendingId) a busy unit has always
        given, so a client that waits on it behaves as before. When the pending
        conversion fails, only the receipt (not queued) and the caller runs the
        send as before."""
        from . import session_pending
        from .session_utils import _build_error_event
        seq: Optional[int] = None
        if persisted_msg_id:
            try:
                seq = await session_pending.mark_pending_by_id(
                    session_id, persisted_msg_id, terminal_context=terminal_context)
            except Exception as exc:  # noqa: BLE001 — degrade to the old path
                logger.warning("session_router.admission_pending_failed session_id=%s: %s",
                               session_id, exc)
        if seq is None:
            return [receipt]
        receipt["queued"] = True
        receipt["pendingSeq"] = seq
        logger.info("session_router.admission_queued session_id=%s msg=%s seq=%s",
                    session_id, persisted_msg_id, seq)
        self.queued_while_busy(session_id)
        busy = _build_error_event(
            code="SESSION_BUSY",
            message="Another turn of this session is running; your message is queued.",
            suggested_action="It is sent automatically when the session is free.")
        busy.update({"pendingSeq": seq, "pendingId": persisted_msg_id, "_post_only": True})
        return [receipt, busy]

    async def _run_conversation_impl(
        self,
        agent_id: str,
        user_message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        session_id: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        channel_context: Optional[dict] = None,
        editor_context: Optional[dict] = None,
        terminal_context: Optional[dict] = None,
        agent_config: Optional[dict] = None,
        client_id: Optional[str] = None,
        _drained_pending: bool = False,
        *,
        writer: TurnWriter,
        opener: Optional[TurnOpener] = None,
        project_agent: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """One chat turn (called through ``run_conversation``).

        ``_drained_pending`` (internal): set by the drain worker when delivering
        a coalesced pending turn. The rows already exist in the DB (sent=0,
        claimed); the worker flips them to sent=1 after delivery, so the pre-slot
        re-persist below is skipped to avoid a duplicate user row.

        1. Get or create SessionUnit
        2. Build options via PromptBuilder
        3. Acquire slot (evict IDLE if needed, queue if full)
        4. Delegate to SessionUnit.send()
        5. Yield SSE events
        """
        from .session_utils import _build_error_event

        # ── TTFT probe (observability-only, run_ad19fd5b) ──────────────────
        # End-to-end time-to-first-token: t0 at the true per-turn entry (BEFORE
        # slot-acquire + recall — the two biggest controllable pre-generation
        # delays, Gate-0 skeptic), first-token captured in the stream loop below.
        # All three are plain locals — no cross-object state, no effect on control
        # flow (pure observability). Segment accumulators: slot + recall (this turn).
        _ttft_t0 = time.perf_counter()
        _ttft_recorded = False
        _ttft_slot_ms = 0.0
        _ttft_recall_ms: Optional[float] = None

        # Resolve session_id — use provided or generate
        if session_id is None:
            session_id = str(uuid4())
        elif session_id.startswith(PREWARM_SESSION_PREFIX):
            # SECURITY (Gate-2): the `prewarm-` prefix is a RESERVED trust boundary —
            # it grants 4 lifecycle exemptions (orphan-reaper skip, TTL skip,
            # poison_guard skip, warm-reuse eligibility). ONLY prewarm_channel_session
            # may mint it (server-side uuid4). A client-supplied session_id starting
            # with it would inherit those exemptions on a NORMAL unit → an un-reapable
            # subprocess + (turn 2+) a poison_guard zombie-reuse bypass. Reject at the
            # inbound boundary (mirrors the reserved-prefix guard in core/skills.py),
            # so the prefix stays authoritative BECAUSE it can only be minted here.
            raise ValueError(
                f"session_id must not use the reserved '{PREWARM_SESSION_PREFIX}' "
                "prefix (server-reserved for pre-warmed sessions)"
            )

        # One send is one message (core/send_requests, design S2): a send that
        # carries a user message reserves its client_id FIRST — before a unit is
        # created or adopted, a session stored or a row written. The same
        # client_id again gets the first one's receipt and nothing else happens.
        # A drained turn delivers rows that exist already; it reserves nothing.
        from . import send_requests
        reserved_send: Optional[str] = None
        if (content or user_message) and client_id and not _drained_pending:
            reservation = await send_requests.reserve(client_id, session_id)
            if not reservation.taken:
                yield await send_requests.receipt(reservation.record or {})
                return
            reserved_send = client_id

        # ── Desktop prewarm adopt-intercept (方案A, run_f107f442) ────────────
        # A brand-new DESKTOP tab first message (no channel_context, session_id
        # not yet a live unit) may ADOPT a pooled baseline subprocess, skipping
        # the 8-14s cold __aenter__ handshake. Flag-gated + desktop-only +
        # bucket/staleness-validated; on any miss it falls through to the
        # unchanged cold get_or_create_unit path (byte-identical, AC8). Done
        # BEFORE get_or_create_unit so the adopted unit (re-keyed into _units
        # under session_id) is what get_or_create_unit then returns — one atomic
        # get-or-adopt-or-create (Gate-1 #2: no TOCTOU double-create).
        if (
            self._desktop_prewarm_enabled()
            and channel_context is None
            and session_id not in self._units
        ):
            try:
                from .agent_defaults import resolve_default_model
                _model = (agent_config or {}).get("model") or resolve_default_model() or ""
                await self._try_adopt_desktop_pool(session_id, agent_id, _model)
            except Exception as exc:  # noqa: BLE001 — adopt is best-effort, never fatal
                logger.warning("session_router.desktop_adopt_intercept_failed: %s", exc)

        unit = self.get_or_create_unit(session_id, agent_id)

        # Tag channel sessions so slot isolation works correctly.
        # channel_context is only set by ChannelGateway, never by chat tabs.
        # Owner messages bypass the channel slot — they use the chat pool
        # so they're never queued behind other users' channel requests.
        is_owner = channel_context.get("is_owner", False) if channel_context else False
        if channel_context and not is_owner and not unit.is_channel_session:
            unit.is_channel_session = True
        # Sticky "this unit is driven by a channel" flag — set for ANY
        # channel_context (owner OR non-owner), unlike is_channel_session which
        # excludes owners for slot-pool routing. `not _has_channel_context` is
        # the ONLY correct "true local desktop tab" test. Load-bearing for the
        # open-canvas-file abs-path gate (C041): owner-over-channel must NOT be
        # treated as local desktop. Sticky so a mid-lifecycle non-channel resume
        # can't clear it.
        if channel_context and not unit._has_channel_context:
            unit._has_channel_context = True

        # Sync the HealthSensor turn threshold to the channel CLI ceiling for
        # ALL channel sessions — keyed off channel_context, NOT the is_channel_session
        # slot flag. prompt_builder clamps the CLI to CHANNEL_MAX_TURNS for any
        # channel_context regardless of is_owner (it does not check is_owner), so
        # owner DMs ALSO run at 100. The slot flag deliberately excludes owners (for
        # pool routing), but the turn threshold must NOT — otherwise an owner channel
        # session keeps _max_turns=500 while the CLI dies at 100, making the
        # turn_approaching heal + channel wrap-up structurally unreachable (the exact
        # decoupled-threshold bug this fix exists to close).
        if channel_context:
            from .session_healing import CHANNEL_MAX_TURNS
            unit._health_sensor.set_max_turns(CHANNEL_MAX_TURNS)

        # ── Persist user message BEFORE slot acquisition ──
        # Critical: If slot acquisition times out (QUEUE_TIMEOUT), the
        # method returns early.  The user message MUST already be in DB
        # so that cold resume (Mechanism B) can inject it later.
        # Without this, the 3rd tab's message is silently lost.
        from database import db
        from .session_manager import session_manager

        user_content = content if content else (
            [{"type": "text", "text": user_message}] if user_message else None
        )
        # Track the persisted row id so that, if the slot check rejects this
        # send (QUEUE_TIMEOUT / SESSION_BUSY), we can convert THIS exact row to a
        # pending message (Root-1 SSOT Phase 2 L2) instead of deleting it — the
        # drain worker then delivers it when the session next reaches IDLE.
        persisted_msg_id: Optional[str] = None
        if user_content and not _drained_pending:
            title = (user_message or "Chat")[:50]
            row_written = False
            try:
                await session_manager.store_session(session_id, agent_id, title)
                if project_agent is not None:
                    # recorded before the slot wait: a send that is queued (and then
                    # delivered by the drain, which carries no pin) is still pinned
                    from .project_agent_session import record_pin
                    if not await record_pin(session_id, project_agent):
                        raise RuntimeError("the Project Agent pin could not be recorded")
                _msg_metadata = {"client_id": client_id} if client_id else None
                persisted_msg_id = str(uuid4())
                # Strip raw tool-call leaks before persisting — symmetric with the
                # assistant path (_clean_blocks_for_persist at the assistant put).
                # The ONLY way a raw ‹invoke> body reaches data.db is a USER message
                # (a user re-feeding a prior turn's on-screen leak remnant); without
                # this, resume splices it back into the prompt and re-induces the
                # leak. Lossless on clean text; fenced/inline discussion preserved.
                user_content = _clean_blocks_for_persist(user_content, assistant=False)
                # after every row a running Stop writes (core/transcript_order)
                async with transcript_order.user_row(session_id):
                    await db.messages.put({
                        "id": persisted_msg_id,
                        "session_id": session_id,
                        "role": "user",
                        "content": user_content,
                        "model": None,
                        "created_at": datetime.now().isoformat(),
                        **({"metadata": _msg_metadata} if _msg_metadata else {}),
                    })
                row_written = True
                if reserved_send:
                    await send_requests.persisted(reserved_send, persisted_msg_id)
            except Exception as exc:
                persisted_msg_id = None
                if project_agent is not None:
                    # the rules of a Project Agent session must survive a restart, so
                    # a session whose pin is not on its record does not start
                    logger.error("Project Agent session %s not started: %s", session_id, exc)
                    yield _build_error_event(
                        code="PROJECT_AGENT_NOT_RECORDED",
                        message="This Project Agent session could not be recorded; try again.",
                    )
                    return
                # Non-fatal: proceed even if persist fails.  The message
                # will still be sent to the agent (just not in DB for
                # future cold resume).  Log at ERROR so it's visible.
                logger.error(
                    "Failed to persist user message for session %s: %s",
                    session_id, exc,
                )
            finally:
                # No user row (an error, or the request cancelled before it): the
                # reservation is released, so a retry of this client_id runs.
                if reserved_send and not row_written:
                    await send_requests.failed(reserved_send)
            if row_written:
                # The acceptance receipt (design S1 3.4 / S2): the first thing this
                # send's POST gets once the message is the server's — its session
                # and its row. Unpublished (before session_start, not part of the
                # turn). From here the server owns the message: whatever comes next
                # (SESSION_BUSY, QUEUE_TIMEOUT, a dropped stream) it is kept, and the
                # client never removes or re-sends it.
                receipt = send_requests.accepted(client_id, session_id, persisted_msg_id)
                # Admission (docs/design/chat-turn-liveness.md S1): open this
                # session's turn now, before the slot, so the wait from here to the
                # first token is a visible turn. A session that cannot take one (a
                # turn of it is open, or a Stop is still recycling it) queues the
                # message instead; that turn's end, or the Stop's, drains it --
                # unless it can go into the running turn now (a steer, design
                # docs/design/2026-10-24-chat-steer.md D1/D2).
                if opener is not None and not opener.open(session_id, unit):
                    if not channel_context and await self._try_steer(
                            unit, session_id, persisted_msg_id, client_id,
                            content if content else user_message,
                            editor_context, terminal_context):
                        receipt["steer"] = True
                        yield receipt
                        return
                    if self._steer_enabled():   # off: the receipt keeps its shape
                        receipt["queuedReason"] = self._steer_blocker(unit) or "busy"
                    queued_events = await self._queue_admitted_send(
                        receipt, session_id, persisted_msg_id, terminal_context)
                    for _qev in queued_events:
                        yield _qev
                    if receipt.get("queued"):
                        return
                    # the pending conversion failed: run the send as before
                    # (unit.send decides; a busy unit answers SESSION_BUSY)
                else:
                    yield receipt
            elif opener is not None and not opener.open(session_id, unit):
                # The row could not be written and another turn holds the session:
                # nothing to queue, so the send is handed back (one turn at a time
                # holds even when the database does not).
                yield {**_build_error_event(code="SESSION_BUSY",
                                            message="Another turn of this session is running."),
                       "retryPayload": await self._hand_back_send(
                           client_id, session_id, agent_id, user_message, content, None,
                           terminal_context),
                       "_post_only": True}
                return
        elif _drained_pending and opener is not None and not opener.open(session_id, unit):
            # a drain that lost the race to another turn: the drain worker rolls the
            # claimed set back (it sees this error) and the winner's end kicks again
            session_events.hub().note_waiter(session_id)
            yield {**_build_error_event(code="SESSION_BUSY",
                                        message="Another turn of this session is running."),
                   "_post_only": True}
            return

        # Acquire concurrency slot — may queue with SSE indicator
        # Check if we need to queue BEFORE blocking, so we can emit the
        # queued event immediately (user sees "Waiting..." not silence)
        from .resource_monitor import resource_monitor as _rm_check
        # R6a: the queued-indicator precheck mirrors _acquire_chat_slot's real
        # gate — spawn_budget (RAM), not the compute_max_tabs UX ceiling. We will
        # need to queue iff: not already alive, RAM can't admit a fresh spawn,
        # AND no evictable IDLE peer exists to free a slot.
        # Mirror _evict_idle's candidate filter: a unit _evict_idle would not
        # take must not count as a free-able slot here — otherwise we skip the
        # "queued" indicator and then fail to evict, falling through to
        # QUEUE_TIMEOUT without ever telling the user they were waiting.
        _precheck_budget = _rm_check.spawn_budget(alive_count=self.alive_count)
        needs_queue = (
            not unit.is_alive
            and self.alive_count > 0
            and not _precheck_budget.can_spawn
            and not any(
                u.state == SessionState.IDLE
                and not (u.has_background_work and u.is_channel_session)
                and u is not unit
                for u in self._units.values()
            )
        )
        if needs_queue:
            yield {"type": "queued", "position": 1, "estimatedWaitMs": self.QUEUE_TIMEOUT * 1000}

        _ttft_slot_t = time.perf_counter()
        slot_result = await self._acquire_slot(unit)
        _ttft_slot_ms = (time.perf_counter() - _ttft_slot_t) * 1000.0  # incl. queue wait
        if slot_result == "timeout":
            error_event = _build_error_event(
                code="QUEUE_TIMEOUT",
                message="All chat slots are busy. Please wait a moment and try again.",
                suggested_action="Your message is saved and will be sent automatically when a slot opens.",
            )
            # Root-1 SSOT Phase 2 (L2): unify QUEUE_TIMEOUT with SESSION_BUSY onto
            # the same pending-message path. Convert the pre-slot persisted row to
            # pending (sent=0) so the drain worker delivers it when a slot frees —
            # the message is durably queued server-side, not just handed back to
            # the frontend. retryPayload retained ONLY as the persist-failed fallback.
            queue_pending_seq: Optional[int] = None
            if user_content and persisted_msg_id:
                try:
                    from . import session_pending
                    queue_pending_seq = await session_pending.mark_pending_by_id(
                        session_id, persisted_msg_id, terminal_context=terminal_context,
                    )
                except Exception as pend_exc:
                    logger.warning(
                        "session_router.queue_mark_pending_failed session_id=%s: %s",
                        session_id, pend_exc,
                    )
            if queue_pending_seq is not None:
                error_event["pendingSeq"] = queue_pending_seq
                error_event["pendingId"] = persisted_msg_id
                # nothing of this session frees it (it never got a slot): the next
                # slot that frees kicks it (docs/design/chat-turn-liveness.md S1 v4)
                self._awaiting_slot.add(session_id)
                self.publish_queue(session_id)
            else:
                error_event["retryPayload"] = await self._hand_back_send(
                    client_id, session_id, agent_id, user_message, content, persisted_msg_id,
                    terminal_context)
            error_event["_post_only"] = True   # a reply to this POST, not turn content
            yield error_event
            return

        # Pre-first-token phase for the UI (core/turn_phase.py): the slot is held,
        # prompt build / DB reads / recall run next. Display only.
        yield turn_phase_event(TURN_PHASE_PREPARING)

        # Build query content
        query_content: Any
        if content and len(content) > 0:
            query_content = content
        elif user_message:
            query_content = user_message
        else:
            yield _build_error_event(
                code="EMPTY_MESSAGE",
                message="No message content provided.",
            )
            return

        # Build SDK options via PromptBuilder
        if agent_config is None:
            from .agent_defaults import build_agent_config
            agent_config = await build_agent_config(agent_id)

        # Per-channel model override: channel_context["model"] takes precedence
        # over the global default_model. This allows Slack to use Sonnet while
        # chat tabs use Opus (or vice versa).
        if channel_context and channel_context.get("model"):
            agent_config["model"] = channel_context["model"]

        # Detect cold-start resume (Mechanism B): subprocess is gone but
        # session has prior messages in DB (app restarted or session evicted).
        # Set context injection flags so build_system_prompt() injects
        # prior conversation into the system prompt.
        #
        # Why _sdk_session_id is None here:
        #   On cold resume the CLI subprocess has been killed (app restart or
        #   eviction).  The SDK session ID only exists while a subprocess is
        #   alive — it's assigned by SessionUnit._spawn() and cleared on kill.
        #   A None value distinguishes cold resume (Mechanism B: inject prior
        #   conversation into system prompt) from live resume (Mechanism A:
        #   pass resume=sdk_session_id to the SDK so the CLI restores its own
        #   conversation state).  See also: resume_session_id kwarg below.
        is_cold_resume = (
            unit.state == SessionState.COLD
            and unit._sdk_session_id is None
            and session_id is not None
        )
        # Channel sessions (Slack DM): prewarm spawns a fresh subprocess
        # with _sdk_session_id set but ZERO conversation history.  After
        # daemon restart the subprocess is new — it doesn't know about
        # prior turns.  Detect this by checking if the DB session has
        # prior messages that the current subprocess hasn't seen.
        needs_channel_resume = False
        if (
            channel_context
            and not is_cold_resume
            and session_id
            and not unit._channel_history_injected
        ):
            msg_count_db = await db.messages.count_by_session(session_id)
            # >1 because current message was already persisted above.
            # If DB has more messages than just this one, the subprocess
            # is missing context (prewarm or daemon restart).
            if msg_count_db > 1:
                needs_channel_resume = True
                logger.info(
                    "channel_resume_detected session_id=%s db_msgs=%d "
                    "— subprocess has no history, injecting",
                    session_id, msg_count_db,
                )
        # Log cold resume detection inputs for diagnostics (COE: 2026-04-02
        # resume context silently skipped — no visibility into why).
        logger.info(
            "cold_resume_check session_id=%s state=%s sdk_session=%s "
            "→ is_cold_resume=%s channel_resume=%s",
            session_id,
            unit.state.value if unit.state else "None",
            "set" if unit._sdk_session_id else "None",
            is_cold_resume,
            needs_channel_resume,
        )
        # Channel TTL rotation: the gateway created a fresh session_id but
        # the prior session's messages should carry forward for continuity.
        # prior_session_id is set by gateway._resolve_session on TTL rotation.
        prior_session_id = (
            channel_context.get("prior_session_id") if channel_context else None
        )
        if is_cold_resume or needs_channel_resume:
            # Check current session first (normal cold resume: app restart)
            resume_from = session_id
            msg_count = await db.messages.count_by_session(session_id)
            if msg_count <= 1 and prior_session_id:
                # TTL rotation: new session has no history, but the old one does.
                # Inject prior session's conversation for continuity.
                prior_count = await db.messages.count_by_session(prior_session_id)
                if prior_count > 0:
                    resume_from = prior_session_id
                    msg_count = prior_count + 1  # ensure > 1 check passes
            # msg_count > 1 because the current user message was already
            # persisted above (before slot acquisition).  A truly new session
            # has exactly 1 message (the one we just saved).  Cold resume
            # requires at least 2 (prior conversation + current message).
            logger.info(
                "cold_resume_decision session_id=%s msg_count=%d "
                "→ injecting=%s (cold=%s channel=%s)",
                session_id, msg_count, msg_count > 1,
                is_cold_resume, needs_channel_resume,
            )
            if msg_count > 1:
                agent_config["needs_context_injection"] = True
                agent_config["resume_app_session_id"] = resume_from
                unit._channel_history_injected = True
                # Insert resume boundary marker so frontend can render a
                # divider between old messages and the new interaction.
                # Without this, prior session messages appear as current
                # blue bubbles — confusing the user (BUG: 2026-06-17).
                await db.messages.put({
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "role": "system",
                    "content": [{"type": "resume_boundary", "text": "Session resumed"}],
                    "model": None,
                    "created_at": datetime.now().isoformat(),
                })
                yield {"type": "session_resuming", "sessionId": session_id}

        # resume_session_id is the SDK's own session ID for Mechanism A (live
        # resume).  On cold resume this is always None — that's correct: the
        # subprocess is dead, so there's no SDK session to resume.  Instead,
        # cold resume injects prior conversation via system prompt (Mechanism B).
        # Use a STABLE mutable dict for session_context so hook closures
        # (dangerous_command_gate, pre_compact_hook) always see the current
        # session_id — even when the subprocess is reused across sends.
        # On first call, create the dict and store it on the unit.
        # On subsequent calls, update the existing dict in-place.
        if unit._hook_session_context is None:
            unit._hook_session_context = {"sdk_session_id": session_id}
        else:
            unit._hook_session_context["sdk_session_id"] = session_id
        # Keystone (R1.5a-fix): a resumed session never re-runs `pipeline_v1.py create`, so restore
        # _active_pipeline_run_id from the session_<sid>_run.map rendezvous here (every
        # send, BEFORE this turn's own `pipeline_v1.py create` hook could fire). Freshness-ordered +
        # fail-safe — see _rehydrate_pipeline_run_id. Without it, resumed-session
        # adversarial markers get run_id=None and D7 can't attribute them. The .map
        # disk read is off-loop (asyncio.to_thread); the ctx mutate stays on the loop.
        await _rehydrate_pipeline_run_id(unit._hook_session_context, session_id)

        # PER-SESSION SYSTEM-PROMPT CACHE (run_1dc710db): a chat-tab session's
        # system prompt is essentially constant for its life; re-assembling the full
        # ~85K (build_session_briefing ~1.1s) every user message is waste. But the
        # ONLY turn where the assembled prompt is discarded — and therefore the only
        # turn where reusing a possibly-stale cache is HARMLESS — is a WARM reuse
        # (send() reuses the live subprocess via client.query(); options.system_prompt
        # is never delivered). On ANY turn that SPAWNS (cold entry / respawn after
        # eviction-or-crash / cold-or-channel resume), options.system_prompt IS
        # consumed by _spawn(), so it MUST be a FRESH build carrying THIS turn's
        # volatile bits — the UI-state / open-file snapshot (_render_ui_context_section,
        # agent SENSE) and the datetime tail — which a turn-1 cache would serve stale
        # (Gate-2 HIGH, run_1dc710db: an evicted→respawn turn keeps _sdk_session_id so
        # is_cold_resume is False, and _prepend_ui_state_to_query is skipped for COLD
        # because it assumes system_prompt carries the fresh UI-state).
        #
        # So gate cache-reuse on _will_reuse_live (the exact warm-reuse predicate,
        # the complement of every spawn path — mirrors _prepend_ui_state_to_query's
        # gate below) — NOT merely "not a resume turn". And only STORE the cache from
        # a NON-resume build: a resume build carries a one-shot history block that
        # must not be re-served on a later turn (Gate-2 MED).
        _will_reuse_live = _is_warm_reuse(unit)
        _cache_in = _system_prompt_cache_to_pass(
            unit._cached_system_prompt, will_reuse_live=_will_reuse_live
        )
        options = await self._prompt_builder.build_options(
            agent_config=agent_config,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            resume_session_id=unit._sdk_session_id,
            session_context=unit._hook_session_context,
            channel_context=channel_context,
            editor_context=editor_context,
            terminal_context=terminal_context,
            extra_mcps=unit._extra_mcps or None,
            cached_system_prompt=_cache_in,
        )
        # resume-context-injection去根 (run_d108b914): transfer the resume block that
        # build_options stashed on agent_config (only on a cold-resume turn, when
        # SWARM_RESUME_VIA_QUERY is on) onto the unit, so the query-prefix below can
        # carry it. This assignment IS the per-turn clear: agent_config is rebuilt
        # every send, so a non-cold turn's .get() returns None and wipes any prior
        # turn's stash. (The _cleanup_internal reset covers a recycle mid-send.)
        unit._resume_query_block = agent_config.get("_resume_query_block")
        # Seed/refresh the cache ONLY from a fresh, non-resume build — that is a
        # complete prompt safe to reuse on a later warm turn. Never store a resume
        # build (its injected history block is one-shot) and never re-store the
        # cache we just reused (no-op churn).
        if _should_store_system_prompt_cache(
            options.system_prompt,
            will_reuse_live=_will_reuse_live,
            needs_context_injection=bool(agent_config.get("needs_context_injection")),
        ):
            unit._cached_system_prompt = options.system_prompt

        # System prompt metadata for the TSCC viewer. This metadata describes the
        # prompt we just BUILT — which is not necessarily the prompt that gets
        # SENT: send() reuses a live subprocess on turn 2+ and discards
        # options.system_prompt entirely. So the authoritative publish happens at
        # delivery, in SessionUnit._spawn(), which is the only consumer of
        # options.system_prompt (it also folds in the recall block appended
        # below, after this point).
        #
        # Here we only (a) hand the unit the pending metadata to publish on its
        # next spawn, and (b) seed the registry when the session has NO entry yet,
        # so the panel is not blank during a cold start's spawn window. The seed
        # is deliberately write-if-absent: overwriting is what destroyed turn 1's
        # real prompt with a rebuilt, never-sent one from turn 2 onward.
        _spm = agent_config.get("_system_prompt_metadata")
        if _spm and session_id:
            from . import session_registry
            unit._pending_prompt_metadata = _spm
            session_registry.system_prompt_metadata.setdefault(session_id, _spm)

        # Delegate to SessionUnit — stream response

        # ── Attachment persistence: save base64 files to Attachments/ ──
        # Claude CLI doesn't support multimodal content blocks via stdin.
        # Convert image/document blocks to text path hints, saving the file
        # data to SwarmWS/Attachments/{date}/ so they're browsable in the
        # Workspace Explorer and persist for the user.
        #
        # When _SDK_SUPPORTS_MULTIMODAL is True, we STILL convert non-native
        # blocks (office docs, audio, video) — Claude API only accepts
        # native images (jpeg/png/gif/webp) and PDF documents natively.
        query_content = await _convert_attachment_blocks(query_content, session_id)

        # ── Resolve user text once (used by recall injection + shadow) ──
        _user_text = user_message or (
            query_content if isinstance(query_content, str) else ""
        )

        # ── G3: Pre-response recall injection ─────────────────────
        # Inject recalled knowledge based on user's actual first message.
        # Replaces the old proactive-keyword recall (in prompt_builder.py)
        # which used generic focus keywords before the user typed.
        # 阶段二 prompt-builder 两分: clear any prior turn's recall stash on EVERY
        # turn — BEFORE the _user_text guard. Recall runs once per session (turn 1),
        # so if the clear lived inside `if _user_text:` a later empty-text WARM turn
        # (multimodal-only / no text payload) would skip the clear AND skip recall,
        # leaking turn-1's stashed block into this turn's query prefix below
        # (Gate-2 CRITICAL, run_f638ebc3). Clearing here makes the stash strictly
        # per-turn: set only when recall actually runs+hits on THIS turn.
        unit._recall_query_block = None
        if _user_text:
            # Return value is the recall-leg ms IF recall ran THIS turn, else None
            # (turn 2+ / channel / keyword-miss). Feeds the TTFT probe's recall
            # segment — None → labelled n/a, never faked as 0 (Gate-1).
            # recall DESTINATION follows the same warm-reuse discriminator as the
            # SENSE prefix below. COLD-spawn (_will_reuse_live False) → recall into
            # options.system_prompt (spawn carries it). WARM-reuse (True) → recall
            # discards system_prompt, so _maybe_inject_recall stashes the block on
            # unit._recall_query_block for the query_content prefix below.
            _ttft_recall_ms = await _maybe_inject_recall(
                user_message=_user_text,
                options=options,
                unit=unit,
                editor_context=editor_context,
                should_mutate_system_prompt=not _will_reuse_live,
                channel_context=channel_context,
            )
            # Copy the recall snapshot to the registry for the read-only TSCC panel.
            # Guarded + best-effort: a panel-observability copy must never perturb
            # the send path.
            #
            # full_text needs NO refresh here. _maybe_inject_recall has just
            # appended the "## Recalled Knowledge" block to options.system_prompt,
            # and _spawn() publishes full_text off that same object at delivery —
            # so the block is included when the prompt is actually sent, and no
            # stale value is written when it is not. An earlier version tried to
            # re-point full_text here, gated on `_ttft_recall_ms is not None`; the
            # gate was moot because the build-time write above had already
            # overwritten turn 1's value (review run_abab234c, HIGH #1).
            try:
                _rsnap = getattr(unit, "_recall_snapshot", None)
                if _rsnap and session_id:
                    from . import session_registry
                    session_registry.recall_snapshot[session_id] = _rsnap
                # Arm the snapshot for re-publication if a recycle inside this
                # same send tears the subprocess down (teardown drops the registry
                # entry, because at that instant no prompt is in force). Armed
                # ONLY when recall ran this turn — `_ttft_recall_ms is not None`
                # is exactly that condition — so a respawn on some later turn
                # cannot resurrect a stale snapshot next to a prompt that has no
                # recall block. Cannot be replaced by the immediate copy above:
                # that copy is what a warm-reuse turn (recall after a zero-keyword
                # opener, which never spawns) depends on.
                unit._pending_recall_snapshot = (
                    _rsnap if _ttft_recall_ms is not None else None
                )
            except Exception:  # noqa: BLE001 — observability copy must never break send
                logger.debug("run_conversation: suppressed exception on a fail-safe path", exc_info=True)
                pass

        # G3 shadow recall REMOVED — recall is already live (wired into
        # prompt assembly via runtime_hooks). Shadow validation data is no
        # longer needed. See: 2026-05-02-evolution-activation-design.md.

        # ── SENSE for a REUSED live subprocess (run_5d460dd5) ──────────────
        # UI-state (canvas/overlay) rode options.system_prompt, but a warm-reuse
        # turn discards the rebuilt system_prompt (the SDK client only gets it at
        # _spawn). So for a reuse turn, deliver THIS turn's UI-state via the query
        # channel — the only per-message path to a live subprocess. Gate mirrors
        # send()'s poison-guard: reuse ⟺ IDLE AND client alive AND last turn clean
        # (a non-clean IDLE recycles→COLD→respawn inside send(), where system_prompt
        # DOES carry it → prefixing there would double-inject). COLD/spawn: no prefix.
        _will_reuse_live = _is_warm_reuse(unit)
        # ONE decision for the delivered prefix and the TSCC capture below (§10).
        _ui_block = _decide_ui_block(unit, editor_context, _will_reuse_live)
        # 阶段二: the dynamic segment = recall (stashed above on a warm turn) +
        # UI-SENSE, prefixed onto the query for a reused subprocess (which discards
        # system_prompt). recall_query_block is None on a cold turn (recall wrote
        # system_prompt instead) or when no recall ran → clean SENSE-only prefix.
        query_content = _prepend_dynamic_context_to_query(
            query_content, editor_context,
            recall_block=getattr(unit, "_recall_query_block", None),
            should_prefix=_will_reuse_live,
            ui_block=_ui_block,
        )
        # resume-context-injection去根 (run_d108b914): INDEPENDENT resume-only prefix,
        # carrying the resume block that build_options stashed this turn.
        #
        # Gate = (is_cold_resume OR needs_channel_resume) — the SAME condition that
        # SET needs_context_injection above (:2685 `if is_cold_resume or
        # needs_channel_resume:`), which is what makes build_options stash the block.
        # Gating only on is_cold_resume (Gate-2 HIGH, run_d108b914) would DROP the
        # block on a channel/Slack resume turn under flag ON — silent amnesia, the
        # exact bug this refactor fixes. For a channel-resume-over-prewarm turn the
        # unit is IDLE/warm so system_prompt is discarded anyway → the query is the
        # ONLY correct delivery path.
        #
        # No double-inject with the recall/SENSE prefix above: that prefix carries
        # THIS-turn recall + UI-SENSE; this one carries PRIOR-conversation history —
        # distinct content. They co-occur only on a channel-resume warm turn, where
        # both correctly ride the query (system_prompt discarded). recall/SENSE are
        # never rendered here (resume-only fn), so neither is duplicated.
        query_content = _prepend_resume_to_query(
            query_content,
            getattr(unit, "_resume_query_block", None),
            should_prefix=_should_prefix_resume(is_cold_resume, needs_channel_resume),
        )
        # Run 2 (prompt-injection root-fix): prefix the attached-terminal
        # UNTRUSTED-DATA block UNCONDITIONALLY — on BOTH cold-spawn and warm-reuse.
        # Unlike recall/SENSE (gated _will_reuse_live) and resume (gated
        # _should_prefix_resume), terminal has NO system-prompt sink anymore (the
        # build_system_prompt injection was deleted), so the query channel is its
        # ONLY carrier on every path — hence unconditional, no double-inject. This
        # runs LAST so the terminal block is the OUTERMOST (leading) prefix; the
        # _prefix_parts capture below mirrors that order (terminal inserted first).
        query_content = _prepend_terminal_to_query(query_content, terminal_context)

        # TSCC/security-scan alignment (run_380413c5): capture the EXACT
        # query-channel prefix delivered this turn, via the SSoT block-builders (the
        # same functions the two prefixers use — NOT a fragile before/after diff of
        # query_content, which is ambiguous for the multimodal list shape; Gate-1
        # P1/P2). Gates MUST mirror the prefixers above so the captured block is
        # exactly what was prepended, in the SAME order:
        #   - terminal (Run 2) = UNCONDITIONAL on both paths — it has no
        #     system-prompt sink, so it ALWAYS rides the query; captured FIRST
        #     because _prepend_terminal_to_query runs LAST and is thus the
        #     OUTERMOST/leading prefix in the delivered query_content.
        #   - dynamic (recall + UI-SENSE) = gated _will_reuse_live.
        #   - resume = gated _should_prefix_resume(...).
        # The dynamic/resume PAIR is still "at most one fires" (COLD-resume vs
        # warm-reuse are mutually exclusive; a channel-resume-over-prewarm warm
        # turn can carry both — join them). Terminal is ORTHOGONAL to that pair —
        # it co-occurs with whichever of them fires (or neither, on a plain cold
        # non-resume spawn, which is exactly the case the old gated-only capture
        # left with full_text=None and the terminal DATA invisible to the
        # security-scan panel — Gate-1 BLOCK, now fixed). Written UNCONDITIONALLY
        # (None when nothing fires) so a stale prior-turn prefix never leaks (P3).
        _prefix_parts: list[str] = []
        _term = _build_terminal_prefix_block(terminal_context)
        if _term:
            _prefix_parts.append(_term)
        if _will_reuse_live:
            _dyn = _build_dynamic_prefix_block(
                editor_context, getattr(unit, "_recall_query_block", None),
                ui_block=_ui_block,
            )
            if _dyn:
                _prefix_parts.append(_dyn)
        if _should_prefix_resume(is_cold_resume, needs_channel_resume):
            _res = _build_resume_prefix_block(getattr(unit, "_resume_query_block", None))
            if _res:
                _prefix_parts.append(_res)
        unit._delivered_query_prefix = "\n\n".join(_prefix_parts) if _prefix_parts else None

        # Stream response — persist each assistant message IMMEDIATELY.
        #
        # Why incremental (not accumulate-then-flush):
        #   SIGKILL (macOS jetsam / OOM) is non-catchable — Python's `finally`
        #   block does NOT execute.  If we only persist at stream end, all
        #   in-flight assistant content (text, tool_use, tool_result) is lost
        #   when the process is killed.  By persisting each AssistantMessage
        #   as it arrives, we guarantee crash recovery up to the last emitted
        #   message.  The cost is one small DB write per assistant turn — a
        #   typical conversation has 5-15 of these, each <10KB.
        # ── TTFT: mark the router/model boundary (run_332ccfd1) ──
        # Everything from t0 to HERE is router-side per-turn overhead (slot,
        # user-msg DB persist, cold-resume reads, build_options prompt assembly,
        # multimodal conversion, recall injection). Measured DIRECTLY so the TTFT
        # line can split pre_send vs send+infer on EVERY turn — including warm
        # (recall=n/a) turns the old recall-only residual left opaque. Pure local,
        # no control-flow effect (observability-only, same contract as _ttft_t0).
        _ttft_presend_ms = (time.perf_counter() - _ttft_t0) * 1000.0

        # ── Cold-spawn pending coalesce (run_25d2b7b8) ──────────────────────
        # The server-side pending queue is drained ONLY on a SessionState.IDLE
        # transition edge (enqueue_drain). A session that reaches COLD WITHOUT a
        # clean IDLE edge — e.g. a TOOL_CALL_LEAK_UNRECOVERED terminal
        # (streaming→dead→cold) — strands its pending rows: the self-heal path
        # (this very resend re-spawning COLD→IDLE→STREAMING) is a TOCTOU race the
        # serial drain worker loses (by the time it runs, send() has advanced the
        # unit past IDLE → drain_pending's state!=IDLE guard no-ops). So claim the
        # pending set HERE — at the router, before unit.send() spawns — and
        # COALESCE it into THIS turn's query_content (pending FIFO first, the
        # arriving message last). unit.state is still COLD at this point (the
        # COLD→IDLE→STREAMING spawn happens INSIDE unit.send()), which is exactly
        # the window _claim_cold_spawn_pending requires. Exactly-once vs the serial
        # worker is held by claim_pending_batch atomicity; delivery is confirmed
        # (mark_sent) / rolled back by _finalize_pending_delivery AFTER the stream.
        # NOT a _drained_pending turn: this is a normal send that persists its OWN
        # arriving row — the finalize touches ONLY the claimed pending seqs, never
        # the arriving row (Gate-1 constraint #2). The ONLY exclusion is a
        # `_drained_pending` turn: that is the serial IDLE-edge drain worker's own
        # nested run_conversation, which has ALREADY claimed its set — re-claiming
        # here would double-handle it. A channel/desktop cold-spawn resend is NOT
        # excluded: a claimed pending row is the same user-content category whether
        # the resend arrives from a tab or a channel, so coalescing is correct for
        # both. Steady-state (nothing claimed) returns [] fast → zero added cost.
        _coalesced_pending_seqs: list[int] = []
        try:
            if not _drained_pending:
                _claimed_pending = await self._claim_cold_spawn_pending(unit, session_id)
                if _claimed_pending:
                    query_content = self._merge_pending_into_query(
                        _claimed_pending, query_content,
                    )
                    # The claimed rows' own terminal output (the arriving turn's is
                    # already prefixed above): same fenced UNTRUSTED block, leading.
                    from . import session_pending as _sp
                    query_content = _prepend_terminal_to_query(
                        query_content, _sp.combine_terminal(_claimed_pending))
                    # MULTIMODAL CONVERSION (Gate-2 MEDIUM, 2nd adversarial round):
                    # the arriving message was block→path-hint converted at ~3509,
                    # BEFORE this merge. If a claimed pending row carries multimodal
                    # blocks, _merge_pending_into_query flattens the whole turn to a
                    # block list — so the pending blocks would reach the SDK UNCON-
                    # VERTED (an unsupported image/doc → SDK/API rejection) unless we
                    # re-run the identical conversion over the merged result. Re-run
                    # it here; it is idempotent on already-converted (path-hint/text)
                    # blocks, so the arriving portion is untouched.
                    if isinstance(query_content, list):
                        if not _SDK_SUPPORTS_MULTIMODAL:
                            query_content = await _convert_unsupported_blocks_to_path_hints(
                                query_content, session_id,
                            )
                        else:
                            query_content = await _convert_non_native_blocks_to_path_hints(
                                query_content, session_id,
                            )
                    _coalesced_pending_seqs = [c.pending_seq for c in _claimed_pending]
                    logger.info(
                        "session_router.cold_spawn_coalesce session_id=%s "
                        "count=%d seqs=%s — merged pending into cold-spawn turn",
                        session_id, len(_coalesced_pending_seqs),
                        _coalesced_pending_seqs,
                    )
        except Exception as _coalesce_exc:  # noqa: BLE001 — never block the turn
            # A claim failure must not strand the turn; the pending rows stay
            # sent=0 (unclaimed, since claim is atomic) and the serial worker
            # remains their fallback owner. Log loud, proceed with the plain turn.
            logger.warning(
                "session_router.cold_spawn_coalesce_failed session_id=%s: %s — "
                "proceeding without coalesce (pending stays queued)",
                session_id, _coalesce_exc,
            )
            _coalesced_pending_seqs = []

        _coalesce_delivered_ok = False
        _coalesce_saw_error: Optional[str] = None
        _coalesce_saw_terminal = False
        try:
            async for event in unit.send(
                query_content=query_content,
                options=options,
                app_session_id=session_id,
                config=self._config,
            ):
                # Observe the coalesced pending set's delivery outcome (F4): a
                # terminal `result` with no `error` → mark_sent; else roll back.
                # Only tracked when we actually claimed something this turn.
                if _coalesced_pending_seqs:
                    _ev_t = event.get("type") if isinstance(event, dict) else None
                    if _ev_t == "assistant" and _coalesce_saw_error is None and event.get("content"):
                        # answered: delivered (see drain_pending)
                        _seqs_now = _coalesced_pending_seqs
                        _coalesced_pending_seqs = []
                        await self._finalize_pending_delivery(
                            session_id, _seqs_now, delivered_ok=True, saw_error=None,
                            terminal=False, unit=unit,
                        )
                    elif _ev_t == "error":
                        _coalesce_saw_error = (
                            (event.get("code") or "error")
                            if isinstance(event, dict) else "error"
                        )
                        # A permanent (non-retriable) error carries terminal=True —
                        # mirror the serial-drain capture so this coalesce path ALSO
                        # terminally-drops it instead of re-driving forever.
                        if isinstance(event, dict) and event.get("terminal"):
                            _coalesce_saw_terminal = True
                    elif _ev_t == "result":
                        _coalesce_delivered_ok = True
                        # FINALIZE-ON-RESULT, not after the loop (Gate-2 HIGH,
                        # 2nd adversarial round): a terminal `result` is observed
                        # here, but the `result` frame is then handed to the
                        # downstream `yield event` below. If the SSE consumer
                        # disconnects AT that yield, GeneratorExit (BaseException,
                        # not caught by `except Exception`) skips the post-loop
                        # finalize at 3907 → the `finally` sees a non-empty set and
                        # ROLLS BACK an ALREADY-DELIVERED set → the user re-sends and
                        # the same content double-delivers. Marking sent the instant
                        # `result` is seen (BEFORE yielding the frame) closes that
                        # window: once delivery is confirmed the set is sent=1 and no
                        # later disconnect can reopen it. Clear first so neither the
                        # post-loop finalize nor the `finally` double-acts.
                        _seqs_now = _coalesced_pending_seqs
                        _coalesced_pending_seqs = []
                        await self._finalize_pending_delivery(
                            session_id, _seqs_now,
                            delivered_ok=True,
                            saw_error=_coalesce_saw_error,
                            terminal=_coalesce_saw_terminal, unit=unit,
                        )
                # Stash the turn client_id on the unit so continuation paths
                # (answer/permission), which run on THIS same unit, reuse it to key
                # their persisted rows with the SAME `{client_id}-asst` as the
                # main-path rows — otherwise a continuation row is keyless and a
                # reconcile-tail cut landing on it duplicates the bubble (run_9bbf1761).
                #
                # WHY INSIDE THE LOOP, GUARDED (Gate-2 BLOCK fix): a pre-loop
                # `unit._turn_client_id = client_id` overwrites the stash BEFORE
                # unit.send()'s WAITING_INPUT guard runs — so a NEW message sent
                # while an earlier turn's question is still pending (multi-tab /
                # eager typing) rewrites the stash to the WRONG turn's cid, then
                # send() raises SessionBusyError and never restores it → answering
                # the original question keys the row to the wrong turn → dup again.
                # Writing here means the stash only updates once send() has ADMITTED
                # this turn (first streamed event past the busy guard). And the
                # `if client_id` guard prevents a drain/channel turn (client_id=None)
                # from CLOBBERING a still-valid stash from the turn that owns the
                # open question.
                # Every turn carries a client_id now (run_conversation gives a
                # drain / channel / task turn a server one), so each admitted turn
                # stamps its own cid here; send() reset the stash at admission
                # (session_unit.py — the _turn_client_id lifecycle reset). No stale
                # inheritance is possible, so no explicit clear is needed here. The
                # `if client_id` guard also stops an intruding WAITING_INPUT send from
                # clobbering the open turn's stash (that send raises before this loop).
                if client_id and unit._turn_client_id != client_id:
                    unit._turn_client_id = client_id
                # the segment key starts as the turn's own and is never written back
                # here once a steer moved it (design chat-steer D4, H1)
                if client_id and getattr(unit, "_turn_row_key", None) is None:
                    unit._turn_row_key = client_id

                # ── TTFT probe: record the first user-visible content token ──
                # Observability-only. Wrapped in a broad try/except so a formatting
                # or attribute error can NEVER raise inside the stream loop and abort
                # the turn (Gate-1 point 3 — the one way "pure observability" could
                # become false). _format_ttft_line is a pure decision (returns a line
                # only on the FIRST text/thinking delta, else None); the latch lives
                # here so it fires exactly once per turn.
                if not _ttft_recorded:
                    try:
                        _ttft_line = _format_ttft_line(
                            event_type=event.get("type", ""),
                            already_recorded=_ttft_recorded,
                            ttft_ms=(time.perf_counter() - _ttft_t0) * 1000.0,
                            slot_ms=_ttft_slot_ms,
                            recall_ms=_ttft_recall_ms,
                            recall_ran_this_turn=_ttft_recall_ms is not None,
                            retry_count=getattr(unit, "_retry_count", 0) or 0,
                            sw_overhead_ms=_ttft_presend_ms,
                            spawn_ms=getattr(unit, "_last_turn_spawn_ms", None),
                        )
                        if _ttft_line is not None:
                            _ttft_recorded = True
                            logger.info("%s | session_id=%s", _ttft_line, session_id)
                    except Exception as _ttft_err:  # noqa: BLE001 — never break stream
                        logger.debug("TTFT probe skipped: %s", _ttft_err)

                # Persist assistant content blocks immediately — crash-safe.
                # The assistant row's correlation key is the turn client_id with
                # an "-asst" suffix, matching the frontend's assistant placeholder
                # id (local-{client_id}-asst). The suffix is REQUIRED: the user
                # row already carries the bare client_id, so a bare key here would
                # collide with the user placeholder in MessageStore._applyMerge,
                # leaving the assistant placeholder unmatched → duplicate bubble.
                # The cleaned blocks are written back to event['content'] so the
                # yielded stream carries the SAME bytes persisted to the DB (bug
                # 2d — no UI≠DB fork); step results ride along (compact).
                _before = await writer.write(
                    event, session_id=session_id,
                    # The unit's (reset by send() at admission): a pause for a
                    # question / permission ends this loop without a `result`, and
                    # the continuation loop finishes the same turn's products.
                    files=self._turn_files_of(unit), changes=getattr(unit, "_turn_changes", None),
                )

                # Echo client_id in result event for frontend dedup (AC2)
                if client_id and event.get("type") == "result":
                    event["client_id"] = client_id

                # Restart silent-history-loss fix (run_af851c26; two-frame move
                # run_2d2e3258). The session-not-found fallback flags
                # `history_dropped` on its ERROR frame (NOT the `_abort` sentinel
                # — a combined frame would die in send()'s _abort guard before
                # reaching here, so history_dropped rides the FORWARDED frame).
                # This side-effect runs on the forward path (like the
                # assistant-persist above), BEFORE `yield event`, so the row is
                # durably saved and THEN the SESSION_RECOVERING frame is delivered
                # to the user. Convert the already-persisted user row to pending
                # (sent=0) — not left sent=1, which the drain worker skips → the
                # message would be stranded. queued_while_busy below notes it as a
                # waiter, so this turn's end kicks the drain, which takes a COLD unit
                # (drain_pending) and sends the row on a cold resume that re-injects
                # DB history: delivery is automatic when this turn ends, not on the
                # user's next interaction (test_turn_liveness_p1, history_dropped).
                # Same save mechanism as the SessionBusyError path below.
                if (
                    event.get("history_dropped")
                    and user_content
                    and persisted_msg_id
                ):
                    try:
                        from . import session_pending
                        _seq = await session_pending.mark_pending_by_id(
                            session_id, persisted_msg_id, terminal_context=terminal_context,
                        )
                        logger.info(
                            "session_router.resume_recover_pending "
                            "session_id=%s msg=%s seq=%s",
                            session_id, persisted_msg_id, _seq,
                        )
                        # this turn's end drains it on the COLD unit (--resume
                        # re-injects history); docs/design/chat-turn-liveness.md S1
                        self.queued_while_busy(session_id)
                    except Exception:
                        logger.exception(
                            "session_router.resume_recover_pending_failed "
                            "session_id=%s msg=%s",
                            session_id, persisted_msg_id,
                        )

                # {_abort} is an INTERNAL caller sentinel (send()/_ensure_spawned
                # emit it to signal "stop consuming" after a clean bail — e.g. the
                # session-not-found fallback yields its SESSION_RECOVERING error
                # frame THEN a bare {_abort}). Every other consumer (prewarm,
                # retry) intercepts it; this loop must too, or it forwards a
                # typeless `data: {"_abort": true}` frame down the SSE stream. The
                # user-facing error was already yielded on the frame(s) before the
                # sentinel, so terminating here delivers the error and drops only
                # the sentinel. (Gate-2 correctness finding, run_c9fa2382.)
                if event.get("_abort"):
                    # Internal bail before/after a user-facing error frame. Finalize
                    # the coalesced pending set so a claimed-but-undelivered set is
                    # rolled back (not stranded in the claimed phase) before we stop.
                    if _coalesced_pending_seqs:
                        await self._finalize_pending_delivery(
                            session_id, _coalesced_pending_seqs,
                            delivered_ok=_coalesce_delivered_ok,
                            saw_error=_coalesce_saw_error or "aborted",
                            terminal=_coalesce_saw_terminal, unit=unit,
                        )
                        _coalesced_pending_seqs = []
                    return

                for _extra in _before:
                    yield _extra
                yield event
            # Stream completed normally — finalize the coalesced pending set
            # (mark_sent on an observed result + no error, else roll back). ONE
            # lifecycle shared with the serial drain worker (_finalize_pending_delivery).
            # Clear the tracker FIRST so a raise INSIDE finalize (e.g. a partial
            # mark_sent that commits some rows then throws) does NOT fall through to
            # the `finally` and double-act on the SAME seqs (Gate-2 HIGH #2: a
            # delivered set rolled back → double-delivery on the next cold-spawn).
            # finalize owns its own outcome; the finally is ONLY for sets that never
            # reached a finalize call at all.
            if _coalesced_pending_seqs:
                _seqs_to_finalize = _coalesced_pending_seqs
                _coalesced_pending_seqs = []
                await self._finalize_pending_delivery(
                    session_id, _seqs_to_finalize,
                    delivered_ok=_coalesce_delivered_ok,
                    saw_error=_coalesce_saw_error,
                    terminal=_coalesce_saw_terminal, unit=unit,
                )
        except Exception as send_err:
            # SessionBusyError: session is actively streaming, reject new send.
            # Yield structured error so frontend can queue the message.
            # NOTE: a claimed-but-unfinalized coalesced set is rolled back by the
            # `finally` below (covers this Exception path AND the BaseException
            # paths — GeneratorExit on SSE disconnect, CancelledError — that an
            # `except Exception` cannot catch, Gate-2 HIGH #1). Do NOT roll back
            # here: that would race the finally's single-owner rollback.
            from .exceptions import SessionBusyError
            if isinstance(send_err, SessionBusyError):
                logger.info(
                    "session_router.session_busy session_id=%s — "
                    "yielding SESSION_BUSY error to frontend",
                    session_id,
                )
                # Root-1 SSOT Phase 2 (L2): the message is NO LONGER deleted.
                # The row persisted before slot acquisition (persisted_msg_id) is
                # converted to a pending message (sent=0) owned by the server-side
                # drain worker, which delivers it when the session next reaches a
                # clean IDLE. This makes the SessionBusyError text TRUE ("saved and
                # will be sent automatically") and removes the frontend's burden of
                # being the durability owner. The cold-resume sent=1 filter (L0/L1)
                # keeps the pending row out of replayed context until it drains.
                pending_seq: Optional[int] = None
                if user_content and persisted_msg_id:
                    try:
                        from . import session_pending
                        pending_seq = await session_pending.mark_pending_by_id(
                            session_id, persisted_msg_id, terminal_context=terminal_context,
                        )
                        # the unit itself was busy: its IDLE edge (or the CLI's idle
                        # report) kicks the drain; a turn of it ending kicks too
                        session_events.hub().note_waiter(session_id)
                        self.publish_queue(session_id)
                        logger.info(
                            "session_router.session_busy_pending session_id=%s "
                            "msg=%s seq=%s",
                            session_id, persisted_msg_id, pending_seq,
                        )
                    except Exception as pend_exc:
                        logger.warning(
                            "session_router.mark_pending_failed session_id=%s: %s",
                            session_id, pend_exc,
                        )
                busy_event = _build_error_event(
                    code="SESSION_BUSY",
                    message=str(send_err.message),
                    suggested_action=str(send_err.suggested_action),
                )
                # Truthful: the message is durably queued server-side and will be
                # auto-drained. We surface the pending id/seq so the mirror can
                # show "queued" deterministically. retryPayload is retained as a
                # last-resort fallback ONLY when the pending persist failed
                # (pending_seq is None) — otherwise the server owns delivery and
                # a frontend re-send would double-deliver (the drain handles it).
                if pending_seq is not None:
                    busy_event["pendingSeq"] = pending_seq
                    busy_event["pendingId"] = persisted_msg_id
                else:
                    busy_event["retryPayload"] = await self._hand_back_send(
                        client_id, session_id, agent_id, user_message, content, persisted_msg_id,
                        terminal_context)
                busy_event["_post_only"] = True   # a reply to this POST, not turn content
                yield busy_event
                return
            raise  # Re-raise non-SessionBusyError exceptions
        finally:
            # SINGLE-OWNER safety net for a claimed-but-unfinalized coalesced set
            # (Gate-2 HIGH #1). run_conversation is an async generator; if the SSE
            # consumer disconnects mid-turn the generator is CLOSED → GeneratorExit
            # (a BaseException, NOT caught by `except Exception`) raises at the
            # suspended `yield event`. CancelledError is the same shape. Without
            # this finally, the claimed set stays sent=0/claimed_at=SET forever —
            # the serial drain (claim requires claimed_at IS NULL) can NEVER re-pick
            # it → permanent silent message loss (STEERING #20) of exactly the
            # messages this fix exists to deliver. Every path that FINALIZED cleared
            # _coalesced_pending_seqs first, so a non-empty value here means NO
            # finalize ran (abandoned generator / cancellation / pre-finalize raise)
            # → roll back so the set re-coalesces on the next cold-spawn / IDLE edge.
            if _coalesced_pending_seqs:
                try:
                    from . import session_pending
                    await session_pending.rollback_claim_batch(
                        session_id, _coalesced_pending_seqs,
                    )
                    logger.warning(
                        "session_router.cold_spawn_coalesce_abandoned session_id=%s "
                        "seqs=%s — turn ended without finalize (disconnect/cancel/"
                        "error); rolled back to pending (no message lost)",
                        session_id, _coalesced_pending_seqs,
                    )
                except Exception:
                    logger.exception(
                        "session_router.cold_spawn_rollback_failed session_id=%s "
                        "seqs=%s", session_id, _coalesced_pending_seqs,
                    )

    async def interrupt_session(self, session_id: str) -> dict:
        """A user Stop (POST /chat/stop): stop the running turn, or with no turn
        running, the session's background agents; either way with a stop receipt.

        The receipt (core/turn_stop) is built from what runs at this instant and
        persisted BEFORE the stop is awaited, so a message sent meanwhile is stored
        after it (the receipt never lands on the next turn). The turn's changes and
        products so far are computed while the stop runs."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        # One Stop per session at a time (2026-10-24: one slow Stop plus the user's
        # repeated clicks wrote 5 identical receipts). Taken before the first await.
        in_flight = self.__dict__.setdefault("_stops_in_flight", set())
        if session_id in in_flight:
            return {"success": True, "message": "Stop already in progress",
                    "subprocess_alive": unit.is_alive, "receipt": []}
        in_flight.add(session_id)
        try:
            return await self._interrupt_session(session_id, unit)
        finally:
            in_flight.discard(session_id)

    async def _interrupt_session(self, session_id: str, unit: SessionUnit) -> dict:
        """``interrupt_session`` once its Stop holds the session."""
        # a retry between attempts leaves the unit COLD while its turn still runs
        retrying = getattr(unit, "_retrying", False) is True
        turn_running = retrying or unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT)
        # Every row this Stop writes comes before a message sent meanwhile
        # (core/transcript_order): held from the receipt's flush to the products.
        async with transcript_order.stop_writes(session_id):
            return await self._interrupt_session_writes(session_id, unit, retrying, turn_running)

    async def _interrupt_session_writes(self, session_id: str, unit: SessionUnit,
                                        retrying: bool, turn_running: bool) -> dict:
        stop_rows = await self._persist_stop_receipt(session_id, unit)
        products = (asyncio.ensure_future(self._turn_products_at_stop(unit, stop_rows))
                    if stop_rows and turn_running else None)
        bg = getattr(unit, "background_agents", None)
        if stop_rows and bg is not None:
            bg.user_stop(True)  # what the process takes down is "stopped", not "lost"
        try:
            if turn_running:
                interrupted = await unit.interrupt()
                message = ("Interrupted" if interrupted or retrying
                           else "Killed (interrupt timed out)")
            else:
                stopped = await unit.stop_background() if stop_rows else False
                message = "Stopped background agents" if stopped else "Nothing running"
        finally:
            if bg is not None:
                bg.user_stop(False)
        extra = await products if products is not None else []
        if extra:
            extra = await self._persist_assistant_blocks(
                session_id, extra, None, label="turn_stop-products",
                client_id=self._segment_asst_key(unit),
            )
        # PIT01 recycle: a user Stop recycles the subprocess to COLD, so a stopped
        # turn does not imply a live process. Re-read is_alive for liveness.
        return {
            "success": True,
            "message": message,
            "subprocess_alive": unit.is_alive,
            "receipt": [*extra, *stop_rows],
        }

    async def _persist_stop_receipt(self, session_id: str, unit: SessionUnit) -> list[dict]:
        """The ``turn_stop`` row for a Stop about to run (core/turn_stop), persisted
        like any assistant row (a reload shows it) and returned for the
        ``/chat/stop`` response (the frontend aborted the stream before calling
        stop, so the response is the live route). A display aid: [] when nothing is
        running or on any failure, never fails the stop."""
        try:
            block = unit.begin_stop_receipt()
        except Exception:  # noqa: BLE001
            logger.debug("stop receipt failed session=%s", session_id, exc_info=True)
            return []
        if not block:
            return []
        # after what the running turn has shown but not yet written (a step's result)
        running = TurnWriter.running(session_id)
        if running is not None:
            await running.flush()
        # a steer the CLI reads from here on belongs to the stopped turn: its row is
        # placed before this receipt, not between it and the products (T29)
        unit._stop_receipt_at = datetime.now()
        return await self._persist_assistant_blocks(
            session_id, [block], None, label="turn_stop",
            client_id=self._segment_asst_key(unit),
        )

    async def _turn_products_at_stop(self, unit: SessionUnit, stop_rows: list[dict] | None = None) -> list[dict]:
        """What the stopped turn changed and produced so far (``turn_changes`` /
        ``turn_deliverables``): otherwise only written at a ``result``, which a
        stopped turn never gets. The files a stopped Bash may have written (the
        receipt's ``writes``) join the changes: a stopped call never reports its
        writes itself. [] on any failure."""
        blocks: list[dict] = []
        changes = getattr(unit, "_turn_changes", None)
        if changes is not None:
            for row in stop_rows or []:
                for call in (row.get("calls") or []) if row.get("type") == "turn_stop" else []:
                    for path in call.get("writes") or []:
                        try:
                            changes.note({"absolutePath": path, "operation": "written"})
                        except Exception as exc:  # noqa: BLE001 — a display aid
                            logger.debug("turn_changes note at stop failed: %s", exc)
            try:
                cblock = await changes.finish()
                if cblock:
                    blocks.append(cblock)
            except Exception as exc:  # noqa: BLE001 — a display aid
                logger.debug("turn_changes finish at stop failed: %s", exc)
        files = self._turn_files_of(unit)
        if files:
            blocks.append(_deliverables_block(list(files.values())))
            files.clear()
        return blocks

    async def stop_task(self, session_id: str, task_id: str) -> dict:
        """Stop one sub-agent of a session's running turn (SessionUnit.stop_task).

        ``{"status": "stopped"}`` / ``{"status": "refused", "message": …}`` (not a task
        of this session's running turn, or the SDK did not take it) /
        ``{"status": "not_found"}`` (no such session)."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"status": "not_found"}
        out = await unit.stop_task(task_id)
        if out.get("stopped"):
            return {"status": "stopped"}
        return {"status": "refused", "message": out.get("reason") or "not stopped"}

    async def release_session(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> dict:
        """Free a session's concurrency slot on tab close (R6b).

        This is the on-demand equivalent of ``_check_ttl``'s release recipe:
        kill the subprocess (slot freed — ``alive_count`` counts ``is_alive``)
        and clear per-session module state.  It does **NOT** delete DB messages,
        so the conversation survives and the user can reopen it from history.

        Safety (Gate-1 + adversarial findings — see test_session_release.py):

        - **Channel sessions** → ``skipped_channel``.  They persist for the
          daemon's life and are not owned by any chat tab; mirror ``_check_ttl``.
        - **IDLE unit** → kill + ``_release_session_state``.  This is the orphan
          the feature fixes (a closed idle tab that would otherwise hold a slot
          until the 12h TTL).
        - **Active state (STREAMING / WAITING_INPUT)** — never raw-``kill()``:
          the running turn belongs to the session, not to the closed tab (a client
          disconnect no longer stops it), and a raw kill could destroy a
          freshly-reused slot. Without ``force`` → ``skipped_active`` (the turn
          finishes; the IDLE unit is then reaped like any orphan).  With
          ``force=True`` (user confirmed closing a
          streaming tab) → ``interrupt()`` (generation-safe), then ``kill()`` the
          settled-IDLE unit to actually free the slot.

        Re-adopt protection (a new ``send()`` reclaiming the slot between close and
        release): the active-state check above skips a re-adopted STREAMING unit,
        and ``interrupt()`` carries its OWN ``_send_generation`` stale-guard that
        bails if the generation advanced mid-interrupt.  No router-level generation
        token is needed (and the frontend has none to pass at close time).

        Returns a status dict; always best-effort (never raises to the caller).
        """
        from .lifecycle_manager import LifecycleManager

        unit = self.get_unit(session_id)
        if unit is None:
            return {"status": "not_found", "alive_count": self.alive_count}

        # Channel sessions (Slack, etc.) are exempt — they persist for the
        # daemon's life and are NOT owned by any chat tab.  _check_ttl exempts
        # them too; this endpoint, fired on chat-tab close, must be no more
        # aggressive than the TTL reaper.  A chat UI has no authority to reap a
        # channel agent, even with force.
        if unit.is_channel_session:
            return {"status": "skipped_channel", "alive_count": self.alive_count}

        # A turn admitted but not yet sending (claimed: cold-starting, waiting for a
        # slot) or a Stop still recycling: nothing to interrupt yet, and dropping the
        # session's state would drop the claim under the running turn
        # (docs/design/chat-turn-liveness.md S1 v4). Left alone, even with force: the
        # turn runs to its end (as a detached one does) and the unit is reaped later.
        if (unit.state not in (SessionState.STREAMING, SessionState.WAITING_INPUT)
                and self.is_busy(session_id)):
            return {"status": "skipped_active", "alive_count": self.alive_count}

        # Active states are never raw-killed: the turn is still running.
        if unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            if not force:
                return {"status": "skipped_active", "alive_count": self.alive_count}
            # Confirmed close of an active tab → generation-safe interrupt
            # FIRST (it bails out if a new send() bumped the generation while
            # awaiting, protecting a re-adopted slot).  interrupt()'s success
            # path transitions STREAMING→IDLE but leaves the subprocess ALIVE —
            # so it does NOT free the slot on its own.  We must kill the now-idle
            # unit to actually release the concurrency slot (the whole point of
            # R6b).  kill() is idempotent: if interrupt already killed (timeout
            # fallback → COLD), this short-circuits.
            await unit.interrupt()
            if unit.is_alive:
                await unit.kill()
            LifecycleManager._release_session_state(session_id)
            logger.info(
                "session_router.release interrupted+freed active session_id=%s "
                "alive_count=%d",
                session_id, self.alive_count,
            )
            return {"status": "released", "alive_count": self.alive_count}

        # IDLE / COLD / DEAD → free the slot. kill() is idempotent and
        # short-circuits when already COLD/DEAD, so this is safe on any of them.
        if unit.is_alive:
            await unit.kill()
        LifecycleManager._release_session_state(session_id)
        logger.info(
            "session_router.release freed slot session_id=%s alive_count=%d",
            session_id, self.alive_count,
        )
        return {"status": "released", "alive_count": self.alive_count}

    def _continuation_client_id(self, session_id: str) -> Optional[str]:
        """What a continuation (answer / permission) publishes as its turn: the
        turn's identity, unchanged by a steer (design chat-steer D4, L5)."""
        unit = self.get_unit(session_id)
        tcid = getattr(unit, "_turn_client_id", None) if unit is not None else None
        return f"{tcid}-asst" if tcid else None

    @staticmethod
    def _turn_row_key_of(unit: Any) -> Optional[str]:
        """The key of the running turn's current segment: the client_id of the user
        bubble that opened it (the sender's, or the steer's after a steer, design
        chat-steer D4). Rows written into the turn from outside its writer (Stop
        receipt, Stop products) and a continuation's rows use ``{key}-asst``
        (``_segment_asst_key``)."""
        if unit is None:
            return None
        key = getattr(unit, "_turn_row_key", None)
        return key if isinstance(key, str) and key else getattr(unit, "_turn_client_id", None)

    @classmethod
    def _segment_asst_key(cls, unit: Any) -> Optional[str]:
        """The ``client_id`` of an assistant row in the turn's current segment, the
        key its writer uses. Never the bare segment key: that is the user bubble's
        (the turn's sender or the steer), and the frontend's reconcile matches a
        client_id regardless of role (frontend design 2026-10-25 B2)."""
        key = cls._turn_row_key_of(unit)
        return f"{key}-asst" if key else None

    def _continuation_row_key(self, session_id: str) -> Optional[str]:
        return self._segment_asst_key(self.get_unit(session_id))

    async def continue_with_answer(
        self, session_id: str, answer: str,
        tool_use_id: str | None = None, answered_by: str = "person",
    ) -> AsyncIterator[dict]:
        """Continue a turn with the user's answer, published to the session hub.

        No unit → the SESSION_NOT_FOUND reply goes to the caller only (no turn)."""
        row_key = self._continuation_client_id(session_id)
        writer = TurnWriter(self, session_id, row_key=self._continuation_row_key(session_id),
                            label="answer")
        if self.get_unit(session_id) is None:
            async for ev in self._continue_with_answer_impl(session_id, answer, tool_use_id=tool_use_id,
                                                            answered_by=answered_by, writer=writer):
                yield ev
            return
        async with aclosing(self._published_turn(
            self._continue_with_answer_impl(session_id, answer, tool_use_id=tool_use_id,
                                            answered_by=answered_by, writer=writer),
            session_id=session_id,
            turn_client_id=row_key,
            source="answer", start_on_session_start=False, writer=writer,
        )) as turn:
            async for ev in turn:
                yield ev

    async def _continue_with_answer_impl(
        self, session_id: str, answer: str,
        tool_use_id: str | None = None, answered_by: str = "person",
        *, writer: TurnWriter,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_answer().

        Persists each assistant message immediately (crash-safe).

        Args:
            session_id: The session to continue.
            answer: JSON-encoded answer text.
            tool_use_id: The AskUserQuestion tool_use block ID so the CLI
                links this response back to the correct tool call.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        # The answer onto the stored question (core/question_prompt): before the
        # turn goes on, so a reload during the continuation shows it answered.
        if tool_use_id:
            from .question_prompt import record_answer
            await record_answer(session_id, tool_use_id, answer)

        # The writer's row key is the originating turn's client_id (stashed by
        # send_message), so this continuation row carries the SAME
        # `{client_id}-asst` key as the turn's main-path rows — keeping the
        # merged bubble correlatable wherever a reconcile-tail cut lands
        # (run_9bbf1761). Written cleaned → stream == DB bytes (bug 2d).
        async for event in unit.continue_with_answer(answer, tool_use_id=tool_use_id,
                                                     answered_by=answered_by):
            for _extra in await writer.write(
                event, files=self._turn_files_of(unit), changes=getattr(unit, "_turn_changes", None),
            ):
                yield _extra
            yield event

    async def continue_with_cmd_permission(
        self, session_id: str, request_id: str, allowed: bool,
    ) -> AsyncIterator[dict]:
        """Continue a turn after a permission decision, published to the session hub.

        No unit → the SESSION_NOT_FOUND reply goes to the caller only (no turn)."""
        row_key = self._continuation_client_id(session_id)
        writer = TurnWriter(self, session_id, row_key=self._continuation_row_key(session_id),
                            label="permission")
        if self.get_unit(session_id) is None:
            async for ev in self._continue_with_cmd_permission_impl(session_id, request_id, allowed,
                                                                    writer=writer):
                yield ev
            return
        async with aclosing(self._published_turn(
            self._continue_with_cmd_permission_impl(session_id, request_id, allowed, writer=writer),
            session_id=session_id,
            turn_client_id=row_key,
            source="permission", start_on_session_start=False, writer=writer,
        )) as turn:
            async for ev in turn:
                yield ev

    async def _continue_with_cmd_permission_impl(
        self, session_id: str, request_id: str, allowed: bool,
        *, writer: TurnWriter,
    ) -> AsyncIterator[dict]:
        """Delegate to SessionUnit.continue_with_permission().

        Persists each assistant message immediately (crash-safe).
        """
        unit = self.get_unit(session_id)
        if unit is None:
            from .session_utils import _build_error_event
            yield _build_error_event(
                code="SESSION_NOT_FOUND",
                message=f"Session {session_id} not found",
            )
            return

        # The unit records the decision on the stored prompt once the hook has
        # it (core/permission_prompt) -- never for a decision that went nowhere.
        # Row key: the originating turn's client_id, as in continue_with_answer.
        async for event in unit.continue_with_permission(request_id, allowed):
            for _extra in await writer.write(
                event, files=self._turn_files_of(unit), changes=getattr(unit, "_turn_changes", None),
            ):
                yield _extra
            yield event

    async def compact_session(
        self, session_id: str, instructions: Optional[str] = None,
    ) -> dict:
        """Delegate to SessionUnit.compact()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        return await unit.compact(instructions)

    async def refresh_session(self, session_id: str) -> dict:
        """Refresh a session's context by killing subprocess + dropping resume id.

        User-triggered "same-tab restart": kills the subprocess and DROPS
        _sdk_session_id (via refresh_context → clear_identity=True), so the next
        send() is a cold resume that injects a STRUCTURED conversation summary
        (mechanism B) instead of replaying the full transcript via --resume. This
        is what sheds a bloated conversation. Only works when session is IDLE
        (not streaming).

        Returns dict with success status and message.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}

        if unit.state in (SessionState.STREAMING, SessionState.WAITING_INPUT):
            return {
                "success": False,
                "message": "Cannot refresh while the AI is active. Stop or answer the pending question first.",
            }

        try:
            await unit.refresh_context()
            return {
                "success": True,
                "message": "Context refreshed. Next message will resume with summary.",
            }
        except Exception as exc:
            logger.error("refresh_session failed for %s: %s", session_id, exc)
            return {"success": False, "message": str(exc)}

    async def enable_mcp_for_session(
        self, session_id: str, mcp_name: str,
    ) -> dict:
        """Activate a deferred MCP for a session via kill+respawn.

        The session must be IDLE (not streaming). Kills the subprocess so
        the next ``send()`` spawns fresh with the updated MCP list.
        The caller is responsible for updating the MCP config (e.g. changing
        the entry's tier from ``ondemand`` to ``always`` for this session).

        Returns dict with success status and message.
        """
        unit = self.get_unit(session_id)
        if unit is None:
            return {
                "success": False,
                "message": f"Session {session_id} not found",
            }
        try:
            await unit.reclaim_for_mcp_swap(mcp_name=mcp_name)
            logger.info(
                "Reclaimed session %s for MCP swap (requested: %s)",
                session_id, mcp_name,
            )
            return {
                "success": True,
                "message": f"Session reclaimed for MCP '{mcp_name}'. "
                           f"Next message will spawn with updated MCPs.",
            }
        except RuntimeError as exc:
            return {"success": False, "message": str(exc)}

    async def kill_rotated_channel_session(self, old_session_id: str) -> None:
        """Kill a channel SessionUnit that was rotated out by the gateway.

        Called by ChannelGateway._resolve_session() after TTL rotation creates
        a new session.  The old SessionUnit is no longer referenced by any
        channel_session row, but remains in-memory with is_channel_session=True
        — making it TTL-immune and potentially a zombie resource leak.

        No-op if the session doesn't exist or is already COLD/DEAD.
        """
        unit = self._units.get(old_session_id)
        if unit is None:
            return  # Not in router — already cleaned up
        if not unit.is_alive:
            return  # Already COLD/DEAD — no subprocess to kill
        logger.info(
            "session_router.kill_rotated_channel session_id=%s state=%s — "
            "cleaning up rotated channel session",
            old_session_id, unit.state.value,
        )
        await unit.kill()

    async def disconnect_all(self) -> None:
        """Kill all alive SessionUnits. Called at shutdown.

        Fires hooks before killing each unit so DailyActivity, auto-commit,
        and distillation run for every active conversation.

        Design §2C: Persists session state BEFORE killing so that sdk_session_ids
        survive daemon restart (enables fast --resume instead of cold resume).
        clear_session_identity() is intentionally NOT called — identity is
        preserved in the state file for restore on next startup.
        """
        # Root-1 SSOT Phase 2 (L3): cancel the serial drain worker so it doesn't
        # outlive the event loop on shutdown (a forever-blocking queue.get()).
        for _sid in list(getattr(self, "_drain_retry_timers", {})):
            self._forget_drain_retry(_sid)   # no timer may restart the worker below
        if self._drain_worker_task is not None and not self._drain_worker_task.done():
            self._drain_worker_task.cancel()
            try:
                await self._drain_worker_task
            except (asyncio.CancelledError, Exception):
                logger.debug("disconnect_all: suppressed exception on a fail-safe path", exc_info=True)
                pass
            self._drain_worker_task = None

        # Persist IDLE session identities for fast resume after restart (§2B/2C)
        try:
            from .session_state_persistence import persist_session_state
            from jobs.paths import APP_DATA_DIR

            state_file = APP_DATA_DIR / "session_state.json"
            # Merge unconsumed cached IDs so they survive a second restart.
            count = persist_session_state(
                self._units, state_file, pending_ids=self._persisted_sdk_ids,
            )
            if count > 0:
                logger.info("Persisted %d session identities before shutdown", count)
        except Exception as exc:
            logger.warning("Failed to persist session state on shutdown: %s", exc)

        alive = [u for u in self._units.values() if u.is_alive]
        logger.info("session_router.disconnect_all: killing %d alive units", len(alive))
        for unit in alive:
            try:
                # Fire hooks before killing (shutdown fix)
                if self._lifecycle_manager and not unit._hooks_enqueued:
                    await self._lifecycle_manager.enqueue_hooks_for_unit(unit)
                    unit._hooks_enqueued = True
                await unit.kill()
                # NOTE: clear_session_identity() intentionally REMOVED (Design §2C).
                # sdk_session_id is preserved in session_state.json for fast resume.
            except Exception as exc:
                logger.warning(
                    "Failed to kill unit %s during disconnect_all: %s",
                    unit.session_id, exc,
                )



# G3 Shadow Recall REMOVED — recall is live, shadow validation no longer needed.
# See: Knowledge/Designs/2026-05-02-evolution-activation-design.md
