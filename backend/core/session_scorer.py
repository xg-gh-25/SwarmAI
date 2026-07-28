"""session_scorer — layer-③ real-session quality scoring.

WHAT: score a REAL past session (a prompt + the agent's actual response + the
tool trajectory it actually took) on two axes — goal attainment + tool
selection — and attribute a cognitive dimension.

WHY THIS IS NOT eval_llm_judge (the key design distinction, verified against
eval_runner.py:1034): `eval_llm_judge` is STATIC CONTRACT ANALYSIS — it asks the
judge "WOULD the agent's current rules produce compliant behavior for this
scenario?" (it never looks at a real output). Layer ③ is the opposite end of the
same pipeline: the session ALREADY HAPPENED; we judge the ACTUAL response +
trajectory that occurred. Same reason Rocky's online eval scores real session
spans rather than golden answers. So this cannot reuse eval_llm_judge — it needs
its own thin judge call over the real output.

DESIGN: a PURE function with the judge boundary INJECTED (`judge_fn`). Tests pass
a fake judge (no Bedrock). Production passes a thin wrapper over
`jobs.bedrock.converse_with_retry` (see `default_judge_fn`). This mirrors
conversation_extract.extract_candidates' injectable `invoke_fn` boundary.

FAIL-CLOSED: a malformed judge response never fabricates a passing score — it
returns status="error" with no trusted score (P2: done = tried to break it).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The 6 canonical dimensions (SSOT: eval_runner.DIMENSIONS). The judge attributes
# a low-scoring session to one of these so a harvested draft lands with the right
# dimension. Kept as a frozenset for validation, not re-derived from eval_runner
# to avoid a scripts<-core import cycle (the same reason golden_case_validator
# hand-mirrors its evaluator set).
_VALID_DIMENSIONS = frozenset(
    {"factual", "capability", "compliance", "judgment", "utility", "recovery",
     # golden_set.yaml uses the longer aliases for some — accept both
     "factual_accuracy", "judgment_quality", "context_utility"}
)

# JudgeFn: takes a fully-rendered judge prompt (str), returns the model's raw
# text (str). This is the ONLY external boundary — everything else is pure.
JudgeFn = Callable[[str], str]

_JUDGE_PROMPT = """You are scoring a REAL agent session that ALREADY HAPPENED. \
Judge the ACTUAL response and tool trajectory below — NOT what the agent's rules \
say it should do. This is real behavior, not a contract.

USER ASKED:
{prompt}

AGENT ACTUALLY RESPONDED:
{response}

TOOLS THE AGENT ACTUALLY CALLED (in order):
{tools}

Score two axes, each 0.0-1.0:
- goal_score: did the agent ACHIEVE what the user asked? (1.0 = fully met, \
0.0 = ignored/wrong)
- tool_score: were the tools well-CHOSEN for the task? (1.0 = right tools, \
0.0 = wrong/missing/wasteful tools)

Attribute the PRIMARY weakness to ONE dimension: factual (memory wrong), \
capability (a subsystem/tool failed to do its job), compliance (broke a rule), \
judgment (bad decision), utility (didn't use available knowledge), recovery \
(failed to recover from an error).

Respond ONLY with this JSON:
{{"goal_score": 0.0, "tool_score": 0.0, "dimension": "capability", "reason": "one line"}}"""


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of the judge's text. Fail-closed: return
    None on anything unparseable (never guess a score)."""
    if not text:
        return None
    # direct parse first
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    # fallback: first {...} block (judges sometimes wrap in prose/```)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _clamp01(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


def score_session(
    *,
    prompt: str,
    response: str,
    tool_names: list[str],
    judge_fn: JudgeFn,
) -> dict:
    """Score one real session on goal + tool axes via the injected judge.

    Returns a dict:
      - success: {"goal_score": float, "tool_score": float, "dimension": str,
                  "reason": str}
      - skipped: {"status": "skipped", "notes": "..."} when prompt is empty
      - error:   {"status": "error", "notes": "...", "goal_score": None} when the
                 judge output is unusable (FAIL-CLOSED — no fabricated pass)
    """
    if not (prompt or "").strip():
        return {"status": "skipped", "notes": "empty prompt — nothing to score"}

    tools_rendered = ", ".join(tool_names) if tool_names else "(no tools called)"
    judge_prompt = _JUDGE_PROMPT.format(
        prompt=prompt.strip(),
        response=(response or "(empty response)").strip(),
        tools=tools_rendered,
    )

    try:
        raw = judge_fn(judge_prompt)
    except Exception as exc:  # noqa: BLE001 — judge boundary; fail-closed
        logger.warning("session_scorer: judge_fn raised (%s) — fail-closed", exc)
        return {"status": "error", "notes": f"judge call failed: {exc}",
                "goal_score": None}

    obj = _extract_json(raw)
    if obj is None:
        logger.warning("session_scorer: judge returned unparseable output — fail-closed")
        return {"status": "error", "notes": "judge output not parseable as JSON",
                "goal_score": None}

    goal = _clamp01(obj.get("goal_score"))
    tool = _clamp01(obj.get("tool_score"))
    if goal is None or tool is None:
        return {"status": "error", "notes": "judge output missing/invalid scores",
                "goal_score": None}

    dim = obj.get("dimension", "capability")
    if dim not in _VALID_DIMENSIONS:
        dim = "capability"  # default; never trust an out-of-vocab dimension

    return {
        "goal_score": goal,
        "tool_score": tool,
        "dimension": dim,
        "reason": str(obj.get("reason", "")).strip() or "(no reason given)",
    }


def default_judge_fn(judge_prompt: str) -> str:
    """Production judge boundary — thin wrapper over converse_with_retry using
    the pinned eval judge model. Kept OUT of score_session so tests never touch
    Bedrock. Region + model resolution mirror eval_runner.eval_llm_judge."""
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from jobs.bedrock import converse_with_retry
    from scripts.eval_runner import _get_judge_model, _JUDGE_READ_TIMEOUT, _JUDGE_MAX_ATTEMPTS

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    resp = converse_with_retry(
        messages=[{"role": "user", "content": [{"text": judge_prompt}]}],
        system=[{"text": "You are a precise session-quality judge. Respond only with the requested JSON."}],
        inference_config={"maxTokens": 500, "temperature": 0.0},
        model_id=_get_judge_model(),
        region=region,
        read_timeout=_JUDGE_READ_TIMEOUT,
        max_attempts=_JUDGE_MAX_ATTEMPTS,
    )
    # converse() response shape: output.message.content[0].text
    try:
        return resp["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""
