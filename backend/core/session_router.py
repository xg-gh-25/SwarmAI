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
from datetime import datetime
from pathlib import Path
import re
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING
from uuid import uuid4

from .session_unit import SessionState, SessionUnit

if TYPE_CHECKING:
    from .prompt_builder import PromptBuilder
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)

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


def _record_recall_degraded(reason: str) -> None:
    """Count a recall degradation by reason (loud-on-degradation observability)."""
    try:
        _recall_degraded_count[reason] = _recall_degraded_count.get(reason, 0) + 1
    except Exception:  # noqa: BLE001 — metric must never break recall
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
    "recall_db_unavailable", "leg_failure", "disaster_timeout",
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
    "toplevel_exception:",
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
})
_DDD_KNOWN_INFORMATIONAL: frozenset[str] = frozenset({
    "injected", "declined:no_ddd_hits",
    # dynamic "declined:<signal>" from detect_active_project — by-design declines:
    "declined:no_projects", "declined:ambiguous", "declined:no_signal",
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

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "this", "that", "with", "from", "what", "when", "where",
    "which", "about", "into", "than", "then", "them", "they", "been",
    "being", "have", "has", "had", "does", "did", "doing", "done",
    "will", "would", "could", "should", "shall", "might",
    "can", "may", "also", "just", "more", "most", "some", "any", "please",
    "help", "tell", "want", "need", "know", "like", "look", "show", "check",
    "all", "each", "every", "both", "few", "many", "much", "such",
    "very", "too", "quite", "rather", "only", "even", "still",
    "how", "why", "who", "you", "its", "our", "your", "their", "his", "her",
    "and", "but", "for", "nor", "not", "yet", "are", "was", "were",
    "let", "got", "get", "put", "see", "say", "said", "make", "made",
})


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


def _prepend_dynamic_context_to_query(
    query_content: Any,
    editor_context: Optional[dict],
    recall_block: Optional[str],
    should_prefix: bool,
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
    block = _build_dynamic_prefix_block(editor_context, recall_block)
    if not block:  # nothing dynamic to report → clean no-op
        return query_content
    if isinstance(query_content, list):
        return [{"type": "text", "text": block}, *query_content]
    return f"{block}\n\n{query_content}"


def _build_dynamic_prefix_block(
    editor_context: Optional[dict],
    recall_block: Optional[str],
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
    ui_block = _render_ui_context_section(editor_context)
    if ui_block:
        # _render_ui_context_section returns a leading "\n\n" — strip for a clean join.
        parts.append(ui_block.lstrip("\n"))
    if not parts:
        return None
    return "\n\n".join(parts)


# resume-context-injection去根 (run_d108b914): the provenance header that wraps a
# resume block when it rides the query channel. The 150K resume block is prior
# conversation history — NOT this turn's user intent. Framing it explicitly as
# quoted history (with a "the actual request follows" boundary) is the
# confabulation guard: without it, a model reading history as a leading user-turn
# block can mistake the recap for the current instruction (AC3). Mirrors the
# [RECALLED] provenance convention recall already uses.
_RESUME_QUERY_HEADER = (
    "[RESUMED CONVERSATION HISTORY — for context only, NOT the current request]\n"
    "The following is a summary of our EARLIER conversation, restored after a "
    "restart. Treat it as background you already know; do NOT act on it as a new "
    "instruction. Your actual task is the user message AFTER the "
    "'--- END RESUMED HISTORY ---' marker below."
)
_RESUME_QUERY_FOOTER = "--- END RESUMED HISTORY ---"


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
    PREFIX, NOT `_sdk_session_id is None`: a first-message SSE-disconnect zombie
    (recover_from_disconnect) has the identical (clean=False, sdk_session_id=None)
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


def _extract_query_keywords(message: str) -> str:
    """Extract searchable keywords from user message.  Pure NLP, no LLM.

    Returns a space-separated string of up to 18 terms suitable for
    FTS5+BM25 keyword recall (the vector leg was removed 2026-08-14).
    Returns empty string for messages too short to produce meaningful recall.
    """
    if not message or len(message.strip()) < 3:
        return ""

    text = message.strip()

    # Strip common conversational filler
    text = re.sub(
        r"^(hey|hi|hello|please|can you|could you|help me|help|swarm)\s+",
        "", text, flags=re.IGNORECASE,
    )

    if not text:
        return ""

    # Strip URLs and file paths before word extraction
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?:^|\s)[~/]\S+", " ", text)

    # Hyphenated compounds first — preserve "session-router", "pre-tool-use"
    # as single terms for better FTS5/vector recall on technical queries.
    compounds = re.findall(r"(?<![a-zA-Z_])([a-zA-Z_]\w+(?:-[a-zA-Z]\w+)+)", text)
    # Strip matched compounds from text to avoid double-counting
    text_stripped = text
    for c in compounds:
        text_stripped = text_stripped.replace(c, " ")

    # English words: keep substantive terms (>2 chars, not stop words).
    # Use [a-zA-Z_] anchor instead of \b — \b doesn't fire at CJK/ASCII
    # boundaries (e.g. "的Memory" misses "Memory" with \b).
    words = [
        w for w in re.findall(r"(?<![a-zA-Z_])([a-zA-Z_]\w{2,})(?!\w)", text_stripped)
        if w.lower() not in _STOP_WORDS
    ]

    # CJK + Kana + Hangul: keep contiguous runs as natural search terms
    cjk = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uac00-\ud7af]+", text)

    combined = compounds[:3] + words[:10] + cjk[:5]
    return " ".join(combined) if combined else ""


def _recall_for_query(query: str, max_tokens: int, allow_embed: bool = False) -> str:
    """Run keyword FTS5+BM25 recall across Knowledge + Transcript + Memory.

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

            # Include TranscriptStore for verbatim conversation recall (L3)
            additional_stores = []
            try:
                from .transcript_indexer import TranscriptStore
                ts = TranscriptStore(conn)
                ts.ensure_tables()
                additional_stores.append(ts)
            except Exception:
                pass  # Transcript recall unavailable — Knowledge-only is fine

            # Include MemoryRecallStore so the user's real query also recalls
            # MEMORY.md entries. Memory had NO keyword leg before this adapter
            # (memory has no fts table); the adapter provides a real one.
            try:
                from .memory_recall_store import MemoryRecallStore
                additional_stores.append(MemoryRecallStore(conn))
            except Exception:
                pass  # Memory recall unavailable — other domains still work

            engine = RecallEngine(store, additional_stores=additional_stores)

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

    Shape per hit: {domain, source, score, method, text}. Size-bounded by the
    recall caps already in recall_multi (max_sections=3/domain, content truncated),
    so this is a few KB. Best-effort: a per-hit shape surprise is skipped and a
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
                if not isinstance(h, dict):
                    continue
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
        non_ddd = tuple(d for d in DOMAINS if d != "ddd")
        result = recall_all(query, project=project, domains=non_ddd, allow_embed=False)
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
        return "", None


def _graph_enrich_recall(query: str) -> str:
    """Extract entities from query, find graph-connected knowledge entries.

    Returns a short context block of related entry IDs or empty string.
    Best-effort: any failure returns empty (never blocks recall).
    """
    try:
        import re as _re
        from pathlib import Path as _Path
        from .knowledge_graph import load_graph, query_related_entries

        graph_path = _Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".knowledge-graph.yaml"
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
        return ""


def _resolve_active_project(
    editor_file_path: Optional[str], user_message: str,
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
    return detect_active_project(editor_file_path=editor_file_path, query=user_message)


async def _maybe_inject_recall(
    user_message: str,
    options: Any,
    unit: SessionUnit,
    editor_context: Optional[dict] = None,
    should_mutate_system_prompt: bool = True,
) -> Optional[float]:
    """Top-level FAULT-ISOLATION wrapper (A): recall can NEVER crash the builder.

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
            pass
        return None


async def _maybe_inject_recall_inner(
    user_message: str,
    options: Any,
    unit: SessionUnit,
    editor_context: Optional[dict] = None,
    should_mutate_system_prompt: bool = True,
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
    if unit._recall_injected:
        return None

    # Channel sessions: skip recall, set flag
    if unit.is_channel_session:
        unit._recall_injected = True
        return None

    # Dedicated pools for the recall/DDD hot-path (run_c8ad52f8) — keep this
    # session-init blocking work OFF the shared default ThreadPoolExecutor so a
    # multi-session burst cannot starve the readiness sampler → false offline.
    from core import executors

    _editor_fp = editor_context.get("file_path") if editor_context else None

    # ── Shared active-project detection (B, run_6ebf6479) — resolve ONCE ──────────
    # Both recall legs need the active project. Historically each detected it
    # SEPARATELY and with DIFFERENT inputs (DDD leg: full user_message; unified leg:
    # extracted keywords) → two blocking iterdirs per turn that could resolve
    # DIFFERENT projects. Resolve it ONCE here (full user_message = strongest
    # signal), off the event loop, and cache on unit._active_project so both legs
    # read the SAME (project, signal). Reset with the other recall guards in
    # SessionUnit._cleanup_internal. Fail-soft: on any detection error, fall back to
    # (None, "detect_error") — recall degrades to no-project, never crashes.
    try:
        _active_project = await asyncio.wait_for(
            executors.run_in("io", _resolve_active_project, _editor_fp, user_message),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _record_ddd_inject("declined:disaster_timeout")
        logger.error("active-project detection DISASTER TIMEOUT (>%.0fs) — no project",
                     _RECALL_DISASTER_TIMEOUT_S)
        _active_project = (None, "detect_timeout")
    except Exception as exc:  # noqa: BLE001 — detection must never break recall
        logger.warning("active-project detection failed (proceeding, no project): %s: %s",
                        type(exc).__name__, exc)
        _active_project = (None, "detect_error")
    try:
        unit._active_project = _active_project
    except Exception:  # noqa: BLE001 — cache write must never break recall
        pass

    # ── DDD runtime injection (run_91bc0651 M2) — runs BEFORE the keyword gate ──
    # signal-1 (editor file path) is DETERMINISTIC and needs NO query keywords, so
    # it must NOT be gated behind the keyword-miss early-return below (Gate-2 HIGH:
    # a user editing Projects/<X>/ who opens with "继续"/"hi" yields no keywords →
    # the old placement skipped DDD entirely, defeating the headline use case).
    # Own once-guard (_ddd_injected) so a sub-cap keyword-miss doesn't re-run it.
    # It consumes the SHARED detection above (no re-detect) — trigger TIMING is
    # unchanged (still pre-keyword-gate); only the redundant detect CALL is removed.
    if not getattr(unit, "_ddd_injected", False):
        try:
            await asyncio.wait_for(
                # Dedicated 'io' pool, NOT the default one (run_c8ad52f8): DDD
                # injection is a fs-scan + FTS5 read on the session-init hot path
                # — the SAME instant briefing runs. On the shared default pool a
                # multi-session burst starves the readiness sampler's own default-
                # pool needs; the dedicated pool insulates it. (The missed half of
                # the July hot-path increment — briefing was migrated, this wasn't.)
                executors.run_in(
                    "io",
                    _inject_ddd_for_active_project,
                    options,
                    user_message,
                    _active_project,
                ),
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
            executors.run_in("io", _unified_recall_body, keywords, _active_project),
            timeout=_RECALL_DISASTER_TIMEOUT_S,
        )
        recalled, _structured_hits = _unified_out
        if not recalled:
            # Fallback to legacy 3-leg path (strangler-fig safety net). Legacy
            # returns a bare string and has no structured hits → _structured_hits
            # stays None (the TSCC Recall tab shows the rendered body without cards).
            _record_recall_degraded("unified_empty_fallback_legacy")
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
            _agentic_hint = (
                "\n\n_(Recall above is keyword/FTS-based — no semantic match. If it "
                "looks thin or off-topic for your task, re-search yourself: Grep "
                "`Knowledge/` (incl. `Archives/`) with synonyms or broader terms.)_"
            )
            # Provenance prefix (R3 §3.5): mark recalled material as RETRIEVED,
            # not the agent's own reasoning and not new user input. Without this
            # boundary the model can absorb keyword-matched history as if it
            # derived it this turn (a confabulation surface — observed live).
            # Pure text, zero logic cost; the agent treats it as a lead to verify.
            _provenance = (
                "> **[RECALLED]** The block below is keyword/FTS-retrieved prior "
                "context — NOT this turn's reasoning and NOT new user input. Treat "
                "it as a lead to verify against source, not an established fact.\n\n"
            )
            _recall_block = (
                f"## Recalled Knowledge\n{_provenance}{recalled}{_agentic_hint}"
            )
            if should_mutate_system_prompt:
                # COLD-spawn: system_prompt carries it (the spawn delivers it).
                options.system_prompt = (
                    options.system_prompt + f"\n\n{_recall_block}"
                )
            else:
                # WARM-reuse: system_prompt is discarded by the reused subprocess —
                # stash the block for the caller to prefix onto query_content (阶段二).
                unit._recall_query_block = _recall_block
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
            _miss_block = (
                "## Recalled Knowledge\n_(Keyword recall found no direct match "
                "for this query. If prior context likely exists under different "
                "wording, Grep `Knowledge/` (incl. `Archives/`) with synonyms.)_"
            )
            if should_mutate_system_prompt:
                options.system_prompt = (
                    options.system_prompt + f"\n\n{_miss_block}"
                )
            else:
                unit._recall_query_block = _miss_block
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
                pass
            logger.info(
                "recall ran but matched nothing | keywords=%s | "
                "first-msg context: base=%d tok (no recall) | recall_leg=%.0fms",
                keywords[:80], _base_tok, _recall_ms,
            )
    except asyncio.TimeoutError:
        # DISASTER: recall hung past the cap. This should NEVER happen in normal
        # operation (recall is ~1-3s, cap is 8s) — if it fires, recall code has a
        # bug. LOUD so it is caught immediately, not silently dead for months.
        _record_recall_degraded("disaster_timeout")
        logger.error(
            "RECALL DISASTER TIMEOUT (>%.0fs) — recall code likely hung; "
            "answer proceeds WITHOUT recall. keywords=%s",
            _RECALL_DISASTER_TIMEOUT_S, keywords[:80],
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
        ``send+infer = ttft − pre_send`` is then the SDK-send + spawn (cold turns)
        + Bedrock first-token span. Emitted on EVERY turn (incl. warm ``recall=n/a``)
        whenever ``sw_overhead_ms`` was measured — the case the old recall-only
        residual left opaque. It is a DIRECT measurement, not the old
        ``ttft − slot − recall`` computed residual (that lumped prompt-build+DB into
        the model span and contradicted itself on recall-ran turns — Gate-1 BLOCK).
        ``sw_overhead_ms=None`` (not measured) → the split is omitted (legacy shape,
        backward-compatible with callers that don't pass it).
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
        # Direct-measured router/model split. send+infer is the residual of a
        # SINGLE measured boundary (pre_send), so there is exactly one residual —
        # nothing can contradict it.
        split_seg = (
            f" pre_send={sw_overhead_ms:.0f}ms"
            f" send+infer={ttft_ms - sw_overhead_ms:.0f}ms"
        )
    else:
        split_seg = ""
    retry_seg = f" retries={retry_count}" if retry_count else ""
    return (
        f"TTFT={ttft_ms:.0f}ms (first-token={event_type}) | "
        f"slot={slot_ms:.0f}ms {recall_seg}{split_seg}{retry_seg}"
    )


def _inject_ddd_for_active_project(
    options: Any,
    user_message: str,
    active_project: Optional[tuple[Optional[str], str]],
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
    """
    from .recall_multi import recall_all

    project, signal = active_project if active_project else (None, "no_detection")
    if not project:
        _record_ddd_inject(f"declined:{signal}")
        return

    _ddd_t0 = time.perf_counter()
    result = recall_all(user_message, project=project, domains=("ddd",))
    _ddd_ms = (time.perf_counter() - _ddd_t0) * 1000.0
    ddd_hits = result.buckets.get("ddd", []) if hasattr(result, "buckets") else []
    # Recall metrics (run_40091f5c): the ddd leg is a REAL per-prompt daemon recall
    # on its OWN path (excluded from the session_prompt sample) — measure it too, or
    # daemon recall latency is only half-covered. Runs in the io-thread; the metric's
    # threading.Lock makes that safe. Fire-and-forget.
    try:
        from core.recall_metrics import record_recall_metric
        record_recall_metric("session_ddd", ("ddd",), _ddd_ms,
                             hit_count=len(ddd_hits))
    except Exception:  # noqa: BLE001
        pass
    if not ddd_hits:
        _record_ddd_inject("declined:no_ddd_hits")
        logger.info("DDD detected project=%s (%s) but 0 DDD sections matched",
                    project, signal)
        return

    # Render top sections with a [DDD:<project>] provenance prefix (distinct
    # token from [RECALLED] — E1/E4 assert on THIS, not string length).
    lines = [
        f"- **{h.get('doc', '?')}** § {h.get('section', '?')}"
        for h in ddd_hits
    ]
    block = (
        f"\n\n## Project DDD — [DDD:{project}]\n"
        f"> **[DDD:{project}]** Retrieved from this project's DDD docs "
        f"(detected via {signal}). Read the cited section(s) for authoritative "
        f"project context before acting.\n\n"
        + "\n".join(lines)
    )
    options.system_prompt = (options.system_prompt or "") + block
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
    ) -> bool:
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
        If all retries fail, logs at ERROR level and returns False so the
        caller can notify the frontend.

        Returns:
            True if persisted successfully, False on failure.
        """
        if not blocks:
            return True
        from database import db
        try:
            _metadata = {"client_id": client_id} if client_id else None
            await db.messages.put({
                "id": str(uuid4()),
                "session_id": session_id,
                "role": "assistant",
                "content": blocks,
                "model": model,
                "created_at": datetime.now().isoformat(),
                **({"metadata": _metadata} if _metadata else {}),
            })
            return True
        except Exception as exc:
            logger.error(
                "Failed to save assistant message%s for session %s: %s "
                "(content may be lost on resume)",
                f" ({label})" if label else "", session_id, exc,
            )
            return False

    def get_unit(self, session_id: str) -> Optional[SessionUnit]:
        """Look up a SessionUnit by session_id."""
        return self._units.get(session_id)

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

            options = await self._prompt_builder.build_options(
                agent_config=agent_config,
                enable_skills=True,
                enable_mcp=True,
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
            ctx_dir = Path.home() / ".swarm-ai" / "SwarmWS" / ".context"
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
            logger.info("session_router.desktop_prewarm_stale id=%s → cold", prewarm_id)
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
        """Number of alive chat (non-channel) session units."""
        return sum(1 for u in self._units.values() if u.is_alive and not u.is_channel_session)

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
            if self._chat_alive_count == 0:
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

            # spawn_budget is the UNCONDITIONAL gate. There is no budget-free
            # spawn path: every "evict → ready" branch re-checks budget after
            # eviction (Gate-1 WARN, run_6ea35431 — the old chat_max-full
            # fall-through returned 'ready' without re-validating budget).
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
            async with self._slot_lock:
                # R6a: budget is the sole gate (no compute_max_tabs ceiling).
                budget = resource_monitor.spawn_budget(alive_count=self.alive_count)
                if budget.can_spawn:
                    return "queued"
                if await self._evict_idle(exclude=requesting_unit, force=False):
                    return "queued"
            # Slot claimed by another coroutine — loop back to wait

        # Queue timed out — last resort: force-evict the longest-idle session
        # even if within grace period. Better than failing the user request.
        # MUST stay force=True — this is the SOLE anti-starvation guarantee once
        # the per-wake path (above) was made grace-respecting. Do not soften.
        async with self._slot_lock:
            if await self._evict_idle(exclude=requesting_unit, force=True):
                logger.info(
                    "session_router: queue timeout force-evict succeeded for %s",
                    requesting_unit.session_id,
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
          3 chat slots are occupied by recently-active tabs.
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
            and not u.is_post_disconnect_flushing  # Option B-soft: don't kill a long turn finishing post-disconnect
            and u is not exclude
            and u.is_channel_session == channel_only
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
        now = time.time()
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
            self.enqueue_drain(session_id)

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
        if unit is None or not unit.is_alive or unit.state != SessionState.IDLE:
            return
        if unit.has_outstanding_tool_use:
            return  # F3: a question is awaiting its answer — do not inject a turn

        claimed = await session_pending.claim_pending_batch(session_id)
        if not claimed:
            return

        user_text, content = session_pending.combine_pending(claimed)
        seqs = [c.pending_seq for c in claimed]

        # Degenerate guard (Gate-2 F1): a corrupt/empty pending row combines to
        # (None, None). Delivering it hits run_conversation's EMPTY_MESSAGE branch,
        # which would roll back → re-claim → same corrupt row → infinite drain loop.
        # Drop such rows as terminally undeliverable (mark sent so they leave the
        # queue) with a loud log — never silently, never loop.
        if not user_text and not content:
            await session_pending.mark_sent_batch(session_id, seqs)
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
            async for _evt in agen:
                _t = _evt.get("type") if isinstance(_evt, dict) else None
                if _t == "error":
                    saw_error = (_evt.get("code") or "error") if isinstance(_evt, dict) else "error"
                elif _t == "result":
                    delivered_ok = True
            if delivered_ok and saw_error is None:
                await session_pending.mark_sent_batch(session_id, seqs)
                logger.info(
                    "session_router.drained session_id=%s count=%d seqs=%s",
                    session_id, len(seqs), seqs,
                )
                # Record the drained seqs on the unit so the streaming-state read
                # API (L5) can surface "these pending rows were delivered" to the
                # frontend mirror (2A). Best-effort; FE also learns via count→0.
                try:
                    unit._last_drained_seqs = list(seqs)
                except Exception:
                    pass
            else:
                # Not actually delivered (error event or no result) — roll back to
                # pending so it re-coalesces, exactly like the raised-exception path.
                await session_pending.rollback_claim_batch(session_id, seqs)
                logger.warning(
                    "session_router.drain_not_delivered session_id=%s seqs=%s "
                    "error=%s delivered_ok=%s — rolled back to pending",
                    session_id, seqs, saw_error, delivered_ok,
                )
        except Exception as exc:
            await session_pending.rollback_claim_batch(session_id, seqs)
            logger.warning(
                "session_router.drain_send_failed session_id=%s seqs=%s: %s — "
                "rolled back to pending",
                session_id, seqs, exc,
            )

    # ── Public API ────────────────────────────────────────────────

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
    ) -> AsyncIterator[dict]:
        """Entry point for chat requests.

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
            try:
                await session_manager.store_session(session_id, agent_id, title)
                _msg_metadata = {"client_id": client_id} if client_id else None
                persisted_msg_id = str(uuid4())
                await db.messages.put({
                    "id": persisted_msg_id,
                    "session_id": session_id,
                    "role": "user",
                    "content": user_content,
                    "model": None,
                    "created_at": datetime.now().isoformat(),
                    **({"metadata": _msg_metadata} if _msg_metadata else {}),
                })
            except Exception as exc:
                persisted_msg_id = None
                # Non-fatal: proceed even if persist fails.  The message
                # will still be sent to the agent (just not in DB for
                # future cold resume).  Log at ERROR so it's visible.
                logger.error(
                    "Failed to persist user message for session %s: %s",
                    session_id, exc,
                )

        # Acquire concurrency slot — may queue with SSE indicator
        # Check if we need to queue BEFORE blocking, so we can emit the
        # queued event immediately (user sees "Waiting..." not silence)
        from .resource_monitor import resource_monitor as _rm_check
        # R6a: the queued-indicator precheck mirrors _acquire_chat_slot's real
        # gate — spawn_budget (RAM), not the compute_max_tabs UX ceiling. We will
        # need to queue iff: not already alive, RAM can't admit a fresh spawn,
        # AND no evictable IDLE peer exists to free a slot.
        # Mirror _evict_idle's candidate filter: a unit that is IDLE but still
        # generating after an SSE disconnect is NOT evictable, so it must not
        # count as a free-able slot here — otherwise we skip the "queued"
        # indicator and then fail to evict, falling through to QUEUE_TIMEOUT
        # without ever telling the user they were waiting.
        _precheck_budget = _rm_check.spawn_budget(alive_count=self.alive_count)
        needs_queue = (
            not unit.is_alive
            and self.alive_count > 0
            and not _precheck_budget.can_spawn
            and not any(
                u.state == SessionState.IDLE
                and not u.is_post_disconnect_flushing
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
                        session_id, persisted_msg_id,
                    )
                except Exception as pend_exc:
                    logger.warning(
                        "session_router.queue_mark_pending_failed session_id=%s: %s",
                        session_id, pend_exc,
                    )
            if queue_pending_seq is not None:
                error_event["pendingSeq"] = queue_pending_seq
                error_event["pendingId"] = persisted_msg_id
            else:
                error_event["retryPayload"] = {
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "userMessage": user_message,
                    "content": content,
                }
            yield error_event
            return

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
        if isinstance(query_content, list):
            if not _SDK_SUPPORTS_MULTIMODAL:
                # Convert ALL image/document blocks to path hints
                query_content = await _convert_unsupported_blocks_to_path_hints(
                    query_content, session_id,
                )
            else:
                # SDK supports multimodal — only convert non-native blocks
                query_content = await _convert_non_native_blocks_to_path_hints(
                    query_content, session_id,
                )

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
        # 阶段二: the dynamic segment = recall (stashed above on a warm turn) +
        # UI-SENSE, prefixed onto the query for a reused subprocess (which discards
        # system_prompt). recall_query_block is None on a cold turn (recall wrote
        # system_prompt instead) or when no recall ran → clean SENSE-only prefix.
        query_content = _prepend_dynamic_context_to_query(
            query_content, editor_context,
            recall_block=getattr(unit, "_recall_query_block", None),
            should_prefix=_will_reuse_live,
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

        # TSCC/security-scan alignment (run_380413c5): capture the EXACT
        # query-channel prefix delivered this turn, via the SSoT block-builders (the
        # same functions the two prefixers use — NOT a fragile before/after diff of
        # query_content, which is ambiguous for the multimodal list shape; Gate-1
        # P1/P2). Gates MUST mirror the two prefixers above so the captured block is
        # exactly what was prepended: dynamic = _will_reuse_live, resume =
        # _should_prefix_resume(...). On any turn at most one fires (COLD-resume vs
        # warm-reuse are mutually exclusive; a channel-resume-over-prewarm warm turn
        # can carry both — join them). Written UNCONDITIONALLY (None when neither
        # fires) so a stale prior-turn prefix never leaks into full_text (Gate-1 P3).
        _prefix_parts: list[str] = []
        if _will_reuse_live:
            _dyn = _build_dynamic_prefix_block(
                editor_context, getattr(unit, "_recall_query_block", None),
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
        try:
            async for event in unit.send(
                query_content=query_content,
                options=options,
                app_session_id=session_id,
                config=self._config,
            ):
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
                # A keyed turn stamps its cid here; a keyless turn (drain / channel)
                # leaves the stash at the None that send() reset it to at admission
                # (session_unit.py — the _turn_client_id lifecycle reset). No stale
                # inheritance is possible, so no explicit clear is needed here. The
                # `if client_id` guard also stops an intruding WAITING_INPUT send from
                # clobbering the open turn's stash (that send raises before this loop).
                if client_id and unit._turn_client_id != client_id:
                    unit._turn_client_id = client_id

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
                if event.get("type") == "assistant" and event.get("content"):
                    await self._persist_assistant_blocks(
                        session_id, event["content"], event.get("model"),
                        client_id=f"{client_id}-asst" if client_id else None,
                    )

                # Echo client_id in result event for frontend dedup (AC2)
                if client_id and event.get("type") == "result":
                    event["client_id"] = client_id

                # {_abort} is an INTERNAL caller sentinel (send()/_ensure_spawned
                # emit it to signal "stop consuming" after a clean bail — e.g. the
                # dead→streaming state-flip guard yields a SESSION_BUSY error event
                # THEN {_abort}). Every other consumer (prewarm, retry) intercepts
                # it; this loop must too, or it forwards a typeless
                # `data: {"_abort": true}` frame down the SSE stream. The
                # user-facing error was already yielded on the line(s) before the
                # sentinel, so terminating here delivers the error and drops only
                # the sentinel. (Gate-2 correctness finding, run_c9fa2382.)
                if event.get("_abort"):
                    return

                yield event
        except Exception as send_err:
            # SessionBusyError: session is actively streaming, reject new send.
            # Yield structured error so frontend can queue the message.
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
                            session_id, persisted_msg_id,
                        )
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
                    busy_event["retryPayload"] = {
                        "sessionId": session_id,
                        "agentId": agent_id,
                        "userMessage": user_message,
                        "content": content,
                    }
                yield busy_event
                return
            raise  # Re-raise non-SessionBusyError exceptions

    async def interrupt_session(self, session_id: str) -> dict:
        """Delegate to SessionUnit.interrupt()."""
        unit = self.get_unit(session_id)
        if unit is None:
            return {"success": False, "message": f"Session {session_id} not found"}
        interrupted = await unit.interrupt()
        # PIT01 recycle: a user Stop now recycles the poisoned subprocess to COLD,
        # so `interrupted=True` (the turn was stopped) no longer implies the
        # process is alive. Re-read is_alive for the accurate liveness signal.
        return {
            "success": True,
            "message": "Interrupted" if interrupted else "Killed (interrupt timed out)",
            "subprocess_alive": unit.is_alive,
        }

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
          that races ``_recover_streaming_on_disconnect`` (which the SSE abort on
          tab close already triggers) and could destroy a freshly-reused slot.
          Without ``force`` → ``skipped_active`` (leave it alone — the abort path
          handles the slot).  With ``force=True`` (user confirmed closing a
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

        # Active states are never raw-killed (races disconnect recovery).
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

    async def continue_with_answer(
        self, session_id: str, answer: str,
        tool_use_id: str | None = None,
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

        async for event in unit.continue_with_answer(answer, tool_use_id=tool_use_id):
            if event.get("type") == "assistant" and event.get("content"):
                await self._persist_assistant_blocks(
                    session_id, event["content"], event.get("model"),
                    label="answer",
                    # Reuse the originating turn's client_id (stashed by
                    # send_message) so this continuation row carries the SAME
                    # `{client_id}-asst` key as the turn's main-path rows — keeping
                    # the merged bubble correlatable no matter where a reconcile-tail
                    # cut lands (run_9bbf1761).
                    client_id=(f"{unit._turn_client_id}-asst" if unit._turn_client_id else None),
                )
            yield event

    async def continue_with_cmd_permission(
        self, session_id: str, request_id: str, allowed: bool,
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

        async for event in unit.continue_with_permission(request_id, allowed):
            if event.get("type") == "assistant" and event.get("content"):
                await self._persist_assistant_blocks(
                    session_id, event["content"], event.get("model"),
                    label="permission",
                    # Reuse the originating turn's client_id (stashed by
                    # send_message) — same rationale as continue_with_answer above
                    # (run_9bbf1761): keeps the continuation row keyed to the turn.
                    client_id=(f"{unit._turn_client_id}-asst" if unit._turn_client_id else None),
                )
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
        if self._drain_worker_task is not None and not self._drain_worker_task.done():
            self._drain_worker_task.cancel()
            try:
                await self._drain_worker_task
            except (asyncio.CancelledError, Exception):
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
