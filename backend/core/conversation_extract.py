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

Output ONLY a JSON object: {{"candidates": [ ... ]}}. If nothing qualifies,
output {{"candidates": []}} — that is the EXPECTED result for most conversations.

Conversation:
{transcript}
"""


def _parse_candidates(text: str) -> list[dict]:
    """Parse the LLM JSON response (tolerant of markdown fences)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        logger.warning("conversation_extract: unparseable LLM response — 0 candidates")
        return []
    cands = obj.get("candidates", []) if isinstance(obj, dict) else []
    # Fail-closed hygiene: drop any candidate missing content or evidence.
    return [
        c for c in cands
        if isinstance(c, dict) and (c.get("content") or "").strip()
        and (c.get("evidence") or "").strip()
    ]


def extract_candidates(
    rows: list[dict],
    project: str,
    *,
    invoke_fn=None,
) -> list[dict]:
    """Extract DDD candidates from a batch of channel_messages rows.

    Args:
        rows: channel_messages rows (from list_by_session) for one conversation.
        project: the project these candidates are (tentatively) for.
        invoke_fn: LLM boundary (prompt -> (text, in_tok, out_tok)). Injectable
            for tests; defaults to jobs.bedrock.invoke. This is the ONLY external
            boundary — everything else is pure.

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

    if invoke_fn is None:
        from jobs.bedrock import invoke as invoke_fn  # noqa: N806

    prompt = _build_prompt(msgs, project)
    try:
        content, _in_tok, _out_tok = invoke_fn(
            prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2
        )
    except Exception as exc:  # noqa: BLE001 — LLM boundary; fail-closed to 0 candidates
        logger.warning("conversation_extract: LLM invoke failed (%s) — 0 candidates", exc)
        return []

    candidates = _parse_candidates(content)
    logger.info("conversation_extract: %d candidate(s) from %d authorized msgs",
                len(candidates), len(msgs))
    return candidates
