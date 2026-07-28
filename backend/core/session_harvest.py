"""session_harvest — layer-② session→golden draft harvesting.

WHAT: take a LOW-SCORE real session (surfaced by session_scorer, layer ③) and
draft an assertion-style golden case that captures the failure as a permanent
regression probe. Land it as a DRAFT for human ratification.

WHY (the closed loop): layer ③ discovers "this real session went wrong"; layer ②
crystallizes it into a golden case so the SAME failure is caught forever after.
This is the half Rocky's eval is missing — its online eval finds failures but
never feeds them back into the golden set (the two stay decoupled). Here,
discover → harvest → 4-gate → human-ratify is one loop.

TWIN OF conversation_extract.extract_candidates: same read→prompt→LLM→parse
shape + injected `invoke_fn` boundary, but it reads DESKTOP sessions and carries
NO channel semantics (no direction / sender_tier checks — those are the exact
fields desktop messages lack, conversation_extract.py:57/65).

HARD CONSTRAINTS (design + Gate-1 fixes):
- Draft schema is FIXED so eval_service.add_case's gate_schema + gate_refs accept
  it: eval_method='llm', affected_by=[] (empty → gate_refs passes trivially; a
  harvested draft must NEVER fabricate a DDD ref — a human enriches at promote),
  tier='draft', evaluators=['goal_success'].
- NEVER auto-promote. This module lands DRAFTS ONLY. There is no promote path and
  the word 'promote' must not appear (test_harvest_source_has_no_promote_call).
  The system drafts; the human ratifies (kernel-not-shell).
- Injected boundaries (invoke_fn + add_case_fn) keep it pure + testable.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Boundaries (the ONLY external touch-points — both injected):
#   InvokeFn: (prompt str) -> raw LLM text str
#   AddCaseFn: (case_data dict) -> result dict   (e.g. eval_service.add_case)
InvokeFn = Callable[[str], str]
AddCaseFn = Callable[[dict], dict]

_DRAFT_PROMPT = """A real agent session scored LOW on quality. Draft a golden \
eval case that would CATCH this failure class in the future.

The user asked:
{prompt}

Why it scored low (dimension: {dimension}):
{reason}

Write an assertion-style case: a short title + 2-4 ASSERTIONS describing the \
behavior a CORRECT agent MUST exhibit for this kind of request. Assertions are \
about behavior, not the exact answer text.

Respond ONLY with this JSON:
{{"title": "...", "assertions": ["...", "..."]}}"""


def _draft_skeleton(*, session_id: str, dimension: str) -> dict:
    """The FIXED draft schema every harvested case carries. Every field here is
    one that eval_service.add_case's gates require (gate_schema needs id/category/
    dimension/eval_method/affected_by/evaluators; gate_refs needs affected_by to
    resolve — [] passes trivially). Values are deliberately conservative:
      - eval_method='llm' — assertion-style, judged by goal_success
      - affected_by=[] — HONEST: no fabricated ref; human enriches at promote
      - tier='draft' — never auto-promoted
      - evaluators=['goal_success'] — the assertion judge
    """
    return {
        "id": _draft_id(session_id),
        "category": "session_harvest",
        "dimension": dimension,
        "eval_method": "llm",
        "affected_by": [],
        "evaluators": ["goal_success"],
        "tier": "draft",
    }


def _draft_id(session_id: str) -> str:
    """Deterministic draft id from session_id — re-harvesting the same session
    yields the same id (the dedup key; the design's simplest de-dup)."""
    slug = re.sub(r"[^a-zA-Z0-9]", "", (session_id or "unknown"))[:12]
    return f"GS_HARVEST_{slug}"


def _parse_draft(text: str) -> Optional[dict]:
    """Parse the LLM draft (tolerant of ```-fences). Fail-closed: None on garbage
    or on a draft missing title/assertions (never land an empty draft)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(obj, dict):
        return None
    title = (obj.get("title") or "").strip()
    assertions = [a for a in (obj.get("assertions") or []) if isinstance(a, str) and a.strip()]
    if not title or not assertions:
        return None
    return {"title": title, "assertions": assertions}


def harvest_draft(
    *,
    session_id: str,
    prompt: str,
    score: dict,
    invoke_fn: InvokeFn,
    add_case_fn: AddCaseFn,
) -> Optional[dict]:
    """Draft a golden case from ONE low-score session and land it as a draft.

    Args:
        session_id: the real session this draft derives from (→ deterministic id).
        prompt: the real user prompt that started the session.
        score: session_scorer output — {goal_score, tool_score, dimension, reason}.
        invoke_fn: LLM boundary (prompt→text). Injected; defaults to jobs.bedrock.
        add_case_fn: sink for the draft (e.g. eval_service.add_case). Injected.

    Returns the add_case result on success, or None (fail-closed) when the LLM
    draft is unparseable/empty. NEVER promotes — lands tier='draft' only.
    """
    dimension = score.get("dimension", "capability")
    reason = score.get("reason", "(no reason)")

    draft_prompt = _DRAFT_PROMPT.format(
        prompt=(prompt or "").strip() or "(empty)",
        dimension=dimension,
        reason=reason,
    )
    try:
        raw = invoke_fn(draft_prompt)
    except Exception as exc:  # noqa: BLE001 — LLM boundary; fail-closed
        logger.warning("session_harvest: invoke_fn raised (%s) — no draft", exc)
        return None

    body = _parse_draft(raw)
    if body is None:
        logger.warning("session_harvest: unparseable/empty draft for %s — none landed",
                       session_id)
        return None

    case = _draft_skeleton(session_id=session_id, dimension=dimension)
    case["title"] = body["title"]
    case["assertions"] = body["assertions"]
    case["scenario"] = {"turns": [{"input": (prompt or "").strip()}]}
    case["source"] = f"session_harvest: {session_id} (goal={score.get('goal_score')}, tool={score.get('tool_score')})"

    try:
        return add_case_fn(case)
    except Exception as exc:  # noqa: BLE001 — add_case runs the 4-gate; a reject
        # is expected signal, not a crash. Log + return None (no draft landed).
        logger.warning("session_harvest: add_case rejected draft %s (%s)",
                       case["id"], exc)
        return None


def default_invoke_fn(prompt: str) -> str:
    """Production LLM boundary — mirrors conversation_extract's default (thin
    wrapper over jobs.bedrock.invoke). Kept out of harvest_draft so tests never
    touch Bedrock."""
    from jobs.bedrock import invoke
    text, _in, _out = invoke(prompt, max_tokens=800, temperature=0.2)
    return text
