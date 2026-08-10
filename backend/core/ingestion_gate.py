"""Unified cognitive Ingestion Gate — the single admission chokepoint every
cognitive store's write-trigger funnels through (SOUL P8's structural end-state).

Design: Projects/SwarmAI/2-understanding/knowledge/designs/2026-08-10-unified-ingestion-gate-design.md

WHY THIS MODULE EXISTS (P8 "One Brain, Many Doors"): the 4 cognitive stores
(DDD / MEMORY / EVOLUTION / KNOWLEDGE) had 7 ingestion triggers with INCONSISTENT
admission gates — some ran an adversarial band, some a confident-only prompt, some
a regex, some NOTHING, and several bypassed their own main gate. That drift is the
failure P8 forbids. This leaf module is the one place a change to admission logic
lands, so "change one door, consider all" is enforced by construction.

LAYERING (Gate-1 round-4, code-verified):
  • gate = DECISION layer (returns auto/review/discard) — this module.
  • apply_to_ddd / _run_locked_write = EXECUTION layer — unchanged, called only
    on verdict==auto by the caller. The gate does NOT carry apply status.

NOISE IS TWO LAYERS (Gate-1 round-2 ⓐ — do NOT merge, strictness differs):
  • structural_noise(text)  — table/monologue/emoji fragments. ALL stores. NO
    length floor. == the semantics of extraction_patterns.is_noise_entry (which
    MEMORY summarization/distillation already rely on to keep short decisions).
  • ddd_value_floor(text)    — ≥5-word floor + instance-log + narration +
    machine-broadcast. DDD ONLY (the `confident` tier). Merging this into the
    all-store noise tier would silently drop short MEMORY decision fragments.

This module is a LEAF: it imports only other leaf modules (extraction_patterns,
ddd_entry_lifecycle — both stdlib-only at import time) + lazy in-function imports
for DDD-only tiers, so ddd_cultivation/memory_extractor/distillation_hook can all
import it without a cycle. The noise primitives below are the SSOT; ddd_cultivation
re-exports them (C3) so its existing callers are unaffected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ── Constants (SSOT for the noise primitives; ddd_cultivation re-exports) ──────
MIN_LESSON_LENGTH = 30

# CJK codepoint presence — a CJK-heavy fragment clears the word-floor on char length.
_CJK_RE = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿＀-￯]")

# ── ddd_value_floor primitives (moved from ddd_cultivation — the ≥5-word floor set) ──
# Instance-log / slip signatures: an EVENT record, not a generalizable lesson.
# Anchored to LINE-START so prose merely MENTIONING "exits 0" mid-sentence survives.
_INSTANCE_LOG_RE = re.compile(
    r"^\s*(stdout|stderr|exit|exit_code|EXIT)\s*[:=]"
    r"|^\s*run_[0-9a-f]{6,}\b"
    r"|^\s*\S+ completed in \d"
    r"|^\s*exits? \d"
    r"|^\s*returncode[:=\s]*\d",
    re.IGNORECASE,
)

# First-person-SINGULAR intent / meta-cognition narration — process-chatter.
# Deliberately narrow: only singular intent + explicit chatter (plural "we should"
# is a legitimate lesson voice). Anchored at START.
_NARRATION_RE = re.compile(
    r"^\s*(?:-\s*)?"
    r"(?:i['’]?(?:ll|m| will| have| need| think| want)\b"
    r"|let me\b|let['’]?s\b"
    r"|this crosses\b|enough to\b|now (?:i|let)\b"
    r"|(?:ok|okay|alright|great|perfect)[,!. ]"
    r"|going to\b|about to\b)",
    re.IGNORECASE,
)

# Machine-broadcast openers (code_intel drift observations, not lessons). Requires
# the MACHINE SHAPE punctuation, not just opening words, so a human lesson that
# merely opens "Architecture change detected requires…" is NOT dropped.
_MACHINE_BROADCAST_RE = re.compile(
    r"^\s*(?:-\s*)?(?:"
    r"architecture change detected\s*:"
    r"|undocumented module\s+`[^`]+`\s*\(\d+\s+functions?\)"
    r"|symbol\s+`[^`]+`\s+referenced in tech\.md but not found"
    r")",
    re.IGNORECASE,
)


def _is_quality_lesson(lesson: str) -> bool:
    """True if `lesson` is a generalizable, well-formed lesson (≥5-word floor set).

    Rejects instance-logs, narration, and sub-sentence fragments. Errs toward
    ACCEPTING when ambiguous (knowledge loss > noise). Byte-for-byte the semantics
    of the former ddd_cultivation.is_quality_lesson (moved here as a leaf primitive).
    """
    stripped = lesson.strip()
    if not stripped:
        return False
    if _INSTANCE_LOG_RE.search(stripped):
        return False
    if _NARRATION_RE.search(stripped):
        return False
    words = stripped.split()
    if len(words) < 5:
        if _CJK_RE.search(stripped) and len(stripped) >= MIN_LESSON_LENGTH:
            return True
        return False
    return True


def ddd_value_floor(text: str) -> bool:
    """True if `text` is NOISE by the DDD value floor (the `confident` tier, DDD-only).

    = machine-broadcast OR fails the ≥5-word/instance-log/narration quality floor.
    This is the STRICTER gate (the former ddd_cultivation.is_noise minus its empty
    check). DDD lessons must clear this; MEMORY short fragments must NOT be subjected
    to it (Gate-1 ⓐ). Returns True == reject.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _MACHINE_BROADCAST_RE.match(stripped):
        return True
    if not _is_quality_lesson(stripped):
        return True
    return False


def structural_noise(text: str) -> bool:
    """True if `text` is STRUCTURAL noise (all-store tier, NO length floor).

    == the semantics of extraction_patterns.is_noise_entry: table fragments,
    agent monologue, emoji-prefix status markers. NO ≥5-word floor, NO
    machine-broadcast (those are the DDD-only value floor). Reused from the
    extraction_patterns leaf so MEMORY behavior is byte-identical to today.
    Returns True == reject.
    """
    if not text or not text.strip():
        return True
    from core.extraction_patterns import is_noise_entry
    return is_noise_entry(text)


# ── is_noise SSOT (re-exported by ddd_cultivation for its existing callers) ────
def is_noise(text: str) -> "tuple[bool, str]":
    """SSOT DDD noise gate (structural + value-floor combined). Returns (is_noise, reason).

    reason ∈ {"empty", "machine_broadcast", "instance_log_or_fragment", ""}.
    This preserves the exact former ddd_cultivation.is_noise contract (DDD callers
    depend on the 2-tuple + reason token). MEMORY/EVOLUTION do NOT use this — they
    use structural_noise (no value floor).
    """
    if not text or not text.strip():
        return (True, "empty")
    if _MACHINE_BROADCAST_RE.match(text.strip()):
        return (True, "machine_broadcast")
    if not _is_quality_lesson(text):
        return (True, "instance_log_or_fragment")
    return (False, "")


# ── self_adversarial_judge — the LLM refute tier (run_8dea0dd5 design) ─────────
# Value is STANCE not intelligence (EVOLUTION META-CORRECTION 2026-08-04): same
# model, zero-context, refute goal. FAIL-CLOSED: any error/timeout/unparseable/empty
# → "suspect" (→ review upstream), NEVER "pass". Reuses the Bedrock Sonnet call shape
# (get_client + invoke_model + timeout config) via _judge_client — NOT LlmRefreshProposer
# (that class carries throttle/citation state + is HIGH-risk; we borrow the call layer).
_JUDGE_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(pass|suspect|noise)\b", re.IGNORECASE | re.MULTILINE)
_JUDGE_MAX_TOKENS = 256

# ── Judge fan-out budget (fail-closed rolling-window rate limit) ──────────────
# The judge tier does a SERIAL Bedrock call per candidate. distillation runs on
# EVERY session close (UNDISTILLED_THRESHOLD=0) over a 30-day scan and can present
# hundreds of candidates (lessons + decisions + corrections + competence), so an
# unbounded fan-out = a serial Bedrock storm (minutes of wall-clock, throttle risk)
# on a hot write path. This caps judge INVOCATIONS in a rolling wall-clock window;
# over-budget candidates fail-closed to "review" (recoverable — sedimented by the
# caller, never dropped) WITHOUT issuing a Bedrock call. A time WINDOW (not a
# monotonic counter) is deliberate: it self-heals with no caller-side reset, so a
# caller that forgets to reset can't permanently wedge the judge (the "declared but
# not enforced" trap). Env-overridable for tests / ops.
import os as _os
import time as _time
try:
    _JUDGE_BUDGET_MAX = int(_os.environ.get("SWARM_JUDGE_BUDGET_MAX", "60"))
except ValueError:
    _JUDGE_BUDGET_MAX = 60
try:
    _JUDGE_BUDGET_WINDOW_S = float(_os.environ.get("SWARM_JUDGE_BUDGET_WINDOW_S", "300"))
except ValueError:
    _JUDGE_BUDGET_WINDOW_S = 300.0
# Timestamps (monotonic seconds) of recent judge invocations within the window.
_judge_call_times: list[float] = []


def _judge_budget_available() -> bool:
    """True if a judge Bedrock call is within the rolling-window budget.

    Prunes timestamps older than the window, then admits iff the in-window count
    is under _JUDGE_BUDGET_MAX. Records the timestamp on admit. Not thread-safe by
    design — the distillation fan-out is serial; a stray concurrent caller can at
    worst admit a few extra calls, never fewer (fail-toward-review stays safe).
    """
    if _JUDGE_BUDGET_MAX <= 0:  # 0/negative = judge disabled entirely (all → review)
        return False
    now = _time.monotonic()
    cutoff = now - _JUDGE_BUDGET_WINDOW_S
    # Prune in place (list stays small: bounded by MAX).
    while _judge_call_times and _judge_call_times[0] < cutoff:
        _judge_call_times.pop(0)
    if len(_judge_call_times) >= _JUDGE_BUDGET_MAX:
        return False
    _judge_call_times.append(now)
    return True

_JUDGE_PROMPT = """You are a skeptic reviewing ONE candidate knowledge entry for a project's brain.
You have ZERO context on why it was written. Your job is to REFUTE it, not trust it.

CANDIDATE (to be written under §{section}):
{text}

EXISTING NEIGHBOR ENTRIES (contradiction check only):
{neighbors}

Answer, defaulting to skepticism:
1. ACCURATE? factually plausible + internally consistent, or dubious?
2. FALSIFIABLE / LOAD-BEARING? a real reusable judgment, or vague/tautological/instance-noise?
3. NOISE? a machine broadcast, log fragment, or narration with no lesson?
4. CONTRADICTS? directly contradicts a neighbor without justification?

Output EXACTLY two lines:
VERDICT: pass|suspect|noise
REASON: <one sentence>

Rules: "pass" ONLY if it survives all four. Any real doubt → "suspect". Machine/fragment/empty → "noise".
When uncertain between pass and suspect, choose suspect (a human will look)."""


def _judge_client():
    """Return a timeout-scoped Bedrock client + model id. Isolated for mockability.

    Uses jobs.bedrock.build_timeout_client() — the credential SSOT's throwaway,
    fail-fast client that INJECTS pre-resolved credentials (the boto3 default
    chain / credential_process resolves FALSE under launchd, which is exactly how
    this daemon runs). The prior implementation called get_client() only to read
    region_name and then built a raw boto3.client() on the default chain, so under
    launchd every judge call raised → fail-closed to "suspect" → every lesson HELD,
    silently. build_timeout_client gives read=25s/1-attempt fail-fast + real creds.
    """
    from jobs.bedrock import build_timeout_client
    try:
        from core.app_config_manager import AppConfigManager
        model_id = (AppConfigManager.instance().get("bedrock_model_map") or {}).get(
            "claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6")
    except Exception:
        model_id = "us.anthropic.claude-sonnet-4-6"
    client = build_timeout_client(read_timeout=25, max_attempts=1)
    return client, model_id


def self_adversarial_judge(text: str, section: str, neighbors: list) -> "tuple[str, str]":
    """Zero-context refute judge. Returns (verdict, reason), verdict ∈ {pass, suspect, noise}.

    FAIL-CLOSED: any exception / timeout / unparseable / empty response → ("suspect", ...),
    never "pass" — an un-refuted auto-write into the brain is the exact risk this exists
    to remove. Neighbors are for contradiction detection only and should EXCLUDE prior
    self_adversarial-admitted entries (caller's responsibility) to avoid self-reinforcement.
    """
    import json as _json
    try:
        client, model_id = _judge_client()
        neighbor_txt = "\n".join(f"- {n}" for n in (neighbors or [])[:8]) or "(none)"
        prompt = _JUDGE_PROMPT.format(section=section, text=text, neighbors=neighbor_txt)
        resp = client.invoke_model(
            modelId=model_id, contentType="application/json", accept="application/json",
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": _JUDGE_MAX_TOKENS, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        result = _json.loads(resp["body"].read())
        content = result.get("content", [])
        out = content[0]["text"] if content and content[0].get("type") == "text" else ""
        m = _JUDGE_VERDICT_RE.search(out or "")
        if not m:
            return ("suspect", "unparseable_or_empty")
        return (m.group(1).lower(), "judged")
    except Exception as e:  # noqa: BLE001 — fail-closed, never pass on error
        return ("suspect", f"judge_error:{type(e).__name__}")


def keep_type_holdback(text: str) -> "tuple[bool, str]":
    """True if `text` is a KEEP_TYPE lesson (principle/correction/decision/model) that
    must be HELD BACK from auto-write (permanent, decay-immune → wrong auto-commit is
    forever). Reuses route_lesson_type SSOT (section is None iff KEEP_TYPE). Returns
    (held, entry_type). Orthogonal to protected_zone (entry-TYPE vs doc-location)."""
    try:
        from core.ddd_entry_lifecycle import route_lesson_type
        section, etype = route_lesson_type(text)
        return (section is None, etype)
    except Exception:  # noqa: BLE001 — fail toward holding back (safe for permanent knowledge)
        return (True, "holdback_error")


# ── GateVerdict — DECISION only (no apply status; gate ≠ execution) ────────────
@dataclass
class GateVerdict:
    """The gate's decision. verdict ∈ {auto, review, discard}. Carries which tiers
    ran + a stable reason token — NOT apply_to_ddd's status vocabulary (that stays
    in the execution layer the caller invokes only when verdict==auto)."""
    verdict: str
    tiers_run: list = field(default_factory=list)
    reason: str = ""


# ── TRIGGER_TIERS — the declaration table (SSOT for which tiers each door runs) ─
# protected_zone is NOT here (Gate-1 round3 HIGH): it stays as the caller's
# pre-drop in _cultivate_proposals so it can sink to protected-zone-candidates.jsonl.
# confident (=ddd_value_floor) is DDD-only (Gate-1 ⓐ): MEMORY short fragments must
# survive. trust/magnitude are DDD-only (need context['proposal']/['run_id']).
#
# ⚠️ C7 HONESTY NOTE (run_0d60e04e — post-C4 code-verified reality):
#   • MEMORY + EVOLUTION triggers are DISPATCHER-SERVED: production callers pass these
#     strings to ingestion_gate() (distillation_hook _admit_memory_lesson /
#     _gate_evolution_entries). These are the LIVE dispatcher contract.
#   • DDD triggers (ddd_*) are served by admission_band (the DDD decision tree), NOT the
#     dispatcher — no caller passes a "ddd_*" string to ingestion_gate(). They are kept
#     here as the DDD tier SPEC (what admission_band implements), NOT as live dispatch rows.
#   • ddd_orch_llm_refresh / ddd_orch_mechanical are CARVE-OUTS: C4 code-verified the
#     orchestrator paths are value-refresh (current→proposed replace), NOT ingestion —
#     they are NEVER gated. Listed for the record with a carve-out marker, never dispatched.
TRIGGER_TIERS: dict[str, list[str]] = {
    # ── DISPATCHER-SERVED (live: passed to ingestion_gate() in production) ──
    # AUTONOMY-FIRST (run_86f44f35): keep_type_holdback REMOVED from the distill triggers.
    # It was the MEMORY/EVOLUTION-side "protected zone" — it short-circuited a KEEP_TYPE
    # (principle/correction/decision/model) to review BEFORE the judge ran, so a
    # judge-worthy permanent lesson could never auto-write. Per XG directive the judge is
    # the sole authority: pass → auto (any type), non-pass → discard. No type is held back.
    # MEMORY — NO confident (no ≥5-word floor so short fragments survive).
    "memory_distill":       ["noise", "judge", "dedup"],
    "memory_save_button":   ["noise", "dedup"],
    "memory_persist":       ["dedup"],
    # EVOLUTION
    "evolution_distill":    ["noise", "judge", "dedup"],
    "evolution_persist":    ["dedup"],
    # ── DDD SPEC (served by admission_band, NOT this dispatcher — kept for the record) ──
    "ddd_reflect":          ["noise", "trust", "judge", "confident", "magnitude", "dedup"],
    "ddd_session_signal":   ["noise", "trust", "judge", "confident", "magnitude", "dedup"],
    "ddd_writeback":        ["noise", "trust", "judge", "confident", "magnitude", "dedup"],
    "ddd_conversation":     ["human"],
    # ── CARVE-OUTS (C4: value-refresh, NOT ingestion — NEVER gated/dispatched) ──
    "ddd_orch_llm_refresh": ["noise", "confident", "magnitude", "dedup"],  # carve-out (never dispatched)
    "ddd_orch_mechanical":  ["noise", "confident", "dedup"],               # carve-out (never dispatched)
}

# The triggers this dispatcher actually SERVES (a caller passes them to ingestion_gate).
# DDD triggers go through admission_band; orchestrator triggers are carve-outs. This set
# lets the dispatcher fail-closed on a trigger that isn't a live dispatch target.
_DISPATCHER_TRIGGERS = frozenset({
    "memory_distill", "memory_save_button", "memory_persist",
    "evolution_distill", "evolution_persist",
})

# Tiers implemented in C1+C2. trust/magnitude are C3 (DDD-only, need context['proposal']
# /['run_id']) — until then treated as no-op PASS (they only tighten toward review,
# and the DDD path's real trust still lives in admission_band until C3 migrates it).
_IMPLEMENTED_TIERS = frozenset({"noise", "confident", "dedup", "human",
                                "judge", "keep_type_holdback"})


def ingestion_gate(
    text: str,
    store: str,
    trigger: str,
    context: dict[str, Any] | None = None,
    *,
    dedup_fn: Callable[[str], bool] | None = None,
) -> GateVerdict:
    """Decide auto / review / discard for one candidate entry.

    Runs the tiers declared for `trigger` in order; the first tier that decides
    discard/review short-circuits. FAIL-CLOSED: an unknown trigger, or any tier
    raising, → review (never auto — an un-gated auto-write into the brain is the
    exact risk this gate exists to remove).

    context carries store-specific payload consumed by DDD-only tiers (C2/C3):
      DDD: {"proposal": <dict|CultivationProposal>, "project_dir": Path, "run_id": str}
      MEMORY/EVOLUTION: {"section": str}
    """
    context = context or {}
    tiers = TRIGGER_TIERS.get(trigger)
    if tiers is None:
        return GateVerdict("review", [], f"unknown_trigger:{trigger}")
    # C7 (run_0d60e04e): the dispatcher SERVES only MEMORY/EVOLUTION triggers. A DDD trigger
    # (served by admission_band) or an orchestrator carve-out (value-refresh, never gated)
    # reaching HERE means a caller wired it to the wrong path — fail-closed to review rather
    # than run a DDD tier-spec through the store-agnostic dispatcher. Makes _DISPATCHER_TRIGGERS
    # a REAL guard (was documentation-only), not just a comment (P7: enforce, don't narrate).
    if trigger not in _DISPATCHER_TRIGGERS:
        return GateVerdict("review", [], f"non_dispatcher_trigger:{trigger}")

    section = context.get("section", "")
    neighbors = context.get("neighbors", [])
    ran: list[str] = []
    try:
        for tier in tiers:
            if tier not in _IMPLEMENTED_TIERS:
                continue  # C3 tier (trust/magnitude) — no-op PASS until implemented (fail-safe)
            ran.append(tier)

            if tier == "noise":
                # ALL stores: structural noise. DDD ALSO gets the value floor via the
                # separate `confident` tier below — so DDD keeps machine-broadcast+floor,
                # MEMORY keeps only structural (short fragments survive). Gate-1 ⓐ.
                if structural_noise(text):
                    return GateVerdict("discard", ran, "noise:structural")

            elif tier == "confident":
                # DDD-only value floor (≥5-word + instance-log + narration + machine-broadcast).
                if ddd_value_floor(text):
                    return GateVerdict("discard", ran, "noise:ddd_value_floor")

            elif tier == "keep_type_holdback":
                # KEEP_TYPES (principle/correction/decision/model) → review (permanent write).
                held, _etype = keep_type_holdback(text)
                if held:
                    return GateVerdict("review", ran, "keep_type_holdback")

            elif tier == "judge":
                # Self-adversarial refute. pass → continue; suspect → review; noise → discard.
                # FAIL-CLOSED inside self_adversarial_judge (error → suspect → review).
                # BUDGET (fan-out cap): the judge does a serial Bedrock call per candidate;
                # once the rolling-window budget is spent, hold the REMAINING candidates
                # for review WITHOUT a Bedrock call — recoverable (sedimented upstream),
                # never dropped, and it caps a session-close storm at _JUDGE_BUDGET_MAX
                # calls / window instead of hundreds. Fail-toward-review keeps it safe.
                if not _judge_budget_available():
                    return GateVerdict("review", ran, "judge:budget_exhausted")
                # PROPAGATE jr (judge reason) into the verdict so a judge INFRA failure
                # ("judge_error:*") is distinguishable downstream from a genuine content
                # holdback ("judged"). Before, the reason was hardcoded "judge:suspect",
                # making a fully-dead judge (fail-closed → everything HELD) invisible in
                # the log — the silent-death twin of any fail-closed design.
                verdict, jr = self_adversarial_judge(text, section, neighbors)
                if verdict == "noise":
                    return GateVerdict("discard", ran, f"judge:noise:{jr}")
                if verdict == "suspect":
                    return GateVerdict("review", ran, f"judge:suspect:{jr}")
                # verdict == "pass" → continue (does NOT bypass downstream tiers)

            elif tier == "human":
                return GateVerdict("review", ran, "human_gated")

            elif tier == "dedup":
                if dedup_fn is not None and dedup_fn(text):
                    return GateVerdict("discard", ran, "duplicate")
                # no dedup_fn wired here → pass (dedup also enforced downstream at
                # apply_to_ddd / locked_write dedup=True, so this is not the sole guard)

        # All declared implemented tiers passed. The auto/review split for DDD trust
        # still lives in admission_band until C3; at C1/C2 a fully-passing candidate is
        # provisionally "auto" for the tiers that exist. C3 tightens this.
        return GateVerdict("auto", ran, "passed_tiers")
    except Exception as e:  # noqa: BLE001 — gate must never crash a write path
        return GateVerdict("review", ran, f"gate_error:{type(e).__name__}")
