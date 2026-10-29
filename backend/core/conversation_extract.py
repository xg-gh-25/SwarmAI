"""Conversation → DDD extractor (capability C, DoD2, run_e346b8ed).

The CONSERVATIVE judgment layer of capability C. Reads a bounded batch of
authorized group-channel messages and asks an LLM to extract ONLY settled,
owner-ratified conclusions worth sedimenting into a project's DDD — emitting a
FEW high-signal candidates or NONE.

Division of responsibility (the reframe that shaped this design):
  - "Is this a settled decision vs chatter?" is inherently LLM-semantic — it
    lives HERE, in a conservative prompt that defaults to emitting nothing.
  - "A wrong extraction can never auto-write DDD" is STRUCTURAL — it lives in
    ddd_cultivation._cultivate_proposals (the source_stage=="conversation"
    guard). This module never writes DDD; it only produces candidates that
    cultivate_from_conversation routes through the human-gate.

宁缺毋滥 (DEC19: False > Stale > Imperfect): the extractor's default is REJECT.
A missed team decision is safe; a wrong DDD entry poisons an authoritative
substrate. Every knob here (tier gate, owner-ratification, conservative prompt)
biases toward emitting less.

Two structural gates enforced in code (NOT left to the prompt):
  1. TIER RE-ASSERT ON READ — only OWNER/TRUSTED inbound messages are fed to the
     LLM, re-checking the stored sender_tier even though the A-write path already
     filtered. Defense-in-depth: never trust the store alone.
  2. OWNER-RATIFICATION PRECONDITION (§9-D3) — if the owner never spoke in the
     batch, there is nothing XG could have ratified in-thread, so we return []
     WITHOUT calling the LLM. Team agreement without the owner is deliberately
     not captured.
"""

import json
import logging
import re

from core.project_registry import DDD_CANONICAL_DOCS

logger = logging.getLogger(__name__)

# Authorized tiers whose messages may enter the extractor (mirrors the A/B read
# gate `channels.gateway._AUTH` — same OWNER/TRUSTED set; re-declared here to
# avoid importing the channels layer into core, but semantically identical).
_AUTHORIZED_TIERS = {"owner", "trusted"}
_OWNER_TIER = "owner"

# Cap the batch handed to the LLM (bounded cost + keeps the prompt focused).
MAX_BATCH_MESSAGES = 200
MAX_OUTPUT_TOKENS = 1536


def _authorized_inbound(rows: list[dict]) -> list[dict]:
    """Tier re-assert on READ: keep only inbound messages from authorized senders.

    A row's sender_tier lives in its metadata JSON (stamped by the single
    channels.gateway._sender_metadata source). Fail-closed: a missing/unknown
    tier is dropped (never trust absence).
    """
    out: list[dict] = []
    for r in rows:
        if r.get("direction") != "inbound":
            continue
        meta = r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if meta.get("sender_tier") not in _AUTHORIZED_TIERS:
            continue
        out.append({
            "sender_tier": meta.get("sender_tier"),
            "sender": meta.get("sender_display_name") or "unknown",
            "text": r.get("content") or "",
            "ts": r.get("created_at"),
        })
    return out


def _owner_participated(msgs: list[dict]) -> bool:
    """§9-D3 precondition: the owner must have spoken in this batch, else there
    is nothing XG could have ratified in-thread → extract nothing."""
    return any(m.get("sender_tier") == _OWNER_TIER for m in msgs)


def _build_prompt(msgs: list[dict], project: str) -> str:
    """The conservative extraction prompt — defaults to emitting nothing."""
    transcript = "\n".join(
        f"[{m['sender']} · {m['sender_tier']}] {m['text']}" for m in msgs
    )
    return f"""You extract SETTLED, OWNER-RATIFIED conclusions from a team
conversation into candidate DDD (Domain-Driven Design) entries for project
"{project}". You are DELIBERATELY CONSERVATIVE: 宁缺毋滥 — a wrong extraction is
worse than a missing one. When in doubt, extract NOTHING.

A candidate qualifies ONLY if ALL are true:
  1. It is an EXPLICIT conclusion — a decision, requirement, or constraint —
     NOT an open question, a "maybe", a proposal still under discussion, or
     brainstorming.
  2. The OWNER (tier=owner) CONCURRED IN-THREAD. A conclusion the owner did not
     explicitly agree to does NOT qualify, even if others agreed.
  3. It names a CONCRETE, actionable change (a convention, a requirement, a
     decision) — not a vague direction.

For each qualifying candidate output an object with:
  - "content": the DDD entry, 1-3 sentences, self-contained.
  - "target_doc": one of PRODUCT.md | TECH.md | IMPROVEMENT.md | PROJECT.md
     (your best SUGGESTION — a human confirms/re-targets it later).
  - "target_section": a plausible section name in that doc.
  - "evidence": the VERBATIM conversation line(s) that prove owner ratification.
  - "confidence": 0.0-1.0.

Record them with the candidates tool. If nothing qualifies, record an empty list —
that is the EXPECTED result for most conversations.

Conversation:
{transcript}
"""


# The answer, shaped by the API (jobs.bedrock.structured(); design
# docs/design/2026-10-28-structured-model-output.md). A candidate's fields are not
# required here: one missing content or evidence is dropped below, alone, rather than
# making the whole answer invalid.
_CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object",
        "properties": {"content": {"type": "string"}, "target_doc": {"type": "string"},
                       "target_section": {"type": "string"}, "evidence": {"type": "string"},
                       "confidence": {"type": "number"}}}}},
    "required": ["candidates"],
}


def _ask(prompt: str) -> dict:
    """The model's {"candidates": [...]} for *prompt* (the default ask_fn).
    Raises jobs.bedrock.StructuredOutputError or a Bedrock error."""
    from jobs.bedrock import get_model_id, structured
    return structured(prompt, schema=_CANDIDATES_SCHEMA, model_id=get_model_id(), feature="conversation.extract",
                      max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2, name="candidates",
                      description="Record the owner-ratified DDD candidates.",
                      record_usage=True)


def _clean_candidates(cands: list) -> list[dict]:
    """The candidates of an answer that may be written: untrusted model output, so each
    is checked here (see below)."""
    # Fail-closed hygiene: drop any candidate missing content or evidence.
    #
    # target_doc is ALSO validated here, because the model's output is untrusted DATA:
    # the prompt above asks for one of the four canonical docs, but a prompt is a
    # suggestion, not a control. Unvalidated, the field flowed verbatim into a stored
    # proposal and the write followed it — and the human approve step reviews the
    # CONTENT, not the destination, so the approval click became the delivery step.
    #
    # Only a PRESENT-and-non-canonical value is rejected. An ABSENT target_doc is
    # legitimate: the conversation path defaults it downstream, and several extractions
    # legitimately omit it. Membership is case-SENSITIVE on purpose — on a
    # case-insensitive filesystem "tech.md" would resolve to a different, unmanaged
    # file than the canonical "TECH.md", so normalising the case here would launder
    # exactly the value that needs rejecting.
    kept = [
        c for c in cands
        if isinstance(c, dict) and (c.get("content") or "").strip()
        and (c.get("evidence") or "").strip()
        and (c.get("target_doc") is None or c["target_doc"] in DDD_CANONICAL_DOCS)
    ]
    for c in kept:
        c["content"] = _strip_self_assigned_keep_type(c["content"])
    return kept


#: A leading ``[type] `` tag on a lesson body. The cultivated-bullet writer CONSUMES
#: such a tag and prefers it over its own classification — a deliberate feature for
#: authored lessons (its docstring: "it was a deliberate tag").
_TYPE_TAG_RE = re.compile(r"^\s*\[([a-z][a-z\-]{2,20})\]\s+")


def _strip_self_assigned_keep_type(content: str) -> str:
    """Drop a leading ``[keep-class-type]`` tag from UNTRUSTED lesson content.

    Honouring the content's own type tag is intentional for authored lessons, but on
    this path the body is emitted by an LLM reading channel text, so the tag is
    attacker-chosen. The four keep-class types (principle/correction/decision/model)
    are not cosmetic: ``retire_entry`` refuses a keep-class target unless a curator
    passes ``force=True``. Measured — the same lesson tagged ``[correction]`` refuses
    to retire, while the identical body left untagged classifies as ``guideline`` and
    retires normally. So a self-assigned keep-class tag converts an ordinary entry into
    one that costs a deliberate override to remove.

    Only the TAG is dropped, never the body: the writer then classifies the text on its
    merits (the same call it makes for the ~all candidates that carry no tag), so a
    lesson that genuinely reads as a correction can still BE one — it just cannot
    DECLARE itself one from untrusted input. Non-keep tags pass through untouched,
    keeping the feature intact for every type that carries no removal protection.

    This belongs at the same boundary that validates ``target_doc``, and for the same
    reason: the prompt asking for well-behaved output is a suggestion, not a control.
    """
    m = _TYPE_TAG_RE.match(content)
    if not m:
        return content
    from core.ddd_entry_lifecycle import _KEEP_TYPES

    if m.group(1) not in _KEEP_TYPES:
        return content
    return content[m.end():]


def extract_candidates(
    rows: list[dict],
    project: str,
    *,
    ask_fn=None,
) -> list[dict]:
    """Extract DDD candidates from a batch of channel_messages rows.

    Args:
        rows: channel_messages rows (from list_by_session) for one conversation.
        project: the project these candidates are (tentatively) for.
        ask_fn: LLM boundary (prompt -> the answer dict {"candidates": [...]},
            raising jobs.bedrock.StructuredOutputError for an unusable reply).
            Injectable for tests; defaults to _ask (jobs.bedrock.structured). This
            is the ONLY external boundary — everything else is pure.

    Returns:
        A (possibly empty) list of candidate dicts ready for
        cultivate_from_conversation. Returns [] WITHOUT calling the LLM when
        there are no authorized messages or the owner did not participate.
    """
    msgs = _authorized_inbound(rows)
    if not msgs:
        logger.info("conversation_extract: no authorized inbound messages — 0 candidates")
        return []
    if not _owner_participated(msgs):
        # §9-D3: no owner in-thread → nothing ratifiable → extract nothing.
        logger.info("conversation_extract: owner did not participate — 0 candidates (D3)")
        return []

    if len(msgs) > MAX_BATCH_MESSAGES:
        msgs = msgs[-MAX_BATCH_MESSAGES:]  # keep the most recent (the conclusion end)

    from jobs.bedrock import StructuredOutputError

    prompt = _build_prompt(msgs, project)
    try:
        answer = (ask_fn or _ask)(prompt)
    except StructuredOutputError as exc:
        logger.warning("conversation_extract: LLM reply %s (%s) — 0 candidates", exc.kind, exc.detail)
        return []
    except Exception as exc:  # noqa: BLE001 — LLM boundary; fail-closed to 0 candidates
        logger.warning("conversation_extract: LLM invoke failed (%s) — 0 candidates", exc)
        return []

    candidates = _clean_candidates(answer.get("candidates") or [])
    logger.info("conversation_extract: %d candidate(s) from %d authorized msgs",
                len(candidates), len(msgs))
    return candidates
