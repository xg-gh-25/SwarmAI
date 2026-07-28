"""session_quality — weekly layer②③ orchestration.

The closed loop, once per week (Sunday, offset from Monday's eval-scheduled):
  sample real sessions → score each (layer③, session_scorer) → for each LOW
  score: record to correction_tracker (drift radar) + harvest a golden draft
  (layer②, session_harvest) → write a weekly report.

SAMPLING 口径 (XG-fixed, NOT configurable — select_sessions):
  a session qualifies if it HAS a correction OR is turn-anomalous
  (turns > 20, or turns == 1 AND has a correction), capped at N=10/week.
  "has a correction" = its session_id appears in corrections.jsonl (which carries
  a session_id field per line — verified). Fixed thresholds, no self-tuning.

All external boundaries are INJECTED seams (scorer/harvester/notifier/db reader)
so the handler is unit-testable with zero Bedrock / DB / disk — same discipline
as eval_scheduled. The value-bearing pure logic is `select_sessions`.

NEVER auto-promotes (harvest_draft lands tier=draft only). Judge calls carry the
eval read-timeout — this is a weekly batch job, NOT an interactive path (avoids
the R9 judge-hang surface).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# XG-fixed constants — deliberately NOT configurable (no self-tuning, no env knob).
_SAMPLE_CAP = 10          # N=10/week
_TURN_ANOMALY_HIGH = 20   # turns > 20 is anomalous
_LOW_SCORE_THRESHOLD = 0.6  # min(goal, tool) < 0.6 → harvest candidate


def select_sessions(
    *,
    session_turns: dict[str, int],
    correction_session_ids: set[str],
) -> list[str]:
    """The sampling 口径 (pure). Returns session_ids to score, capped at N=10.

    A session qualifies if EITHER:
      - it has a correction (session_id ∈ corrections.jsonl), OR
      - it is turn-anomalous: turns > 20, OR (turns == 1 AND has a correction).

    Note turns==1 alone is NOT anomalous (a one-shot Q&A is normal); it only
    qualifies WITH a correction — which the correction branch already covers, so
    the turns==1 rule adds nothing beyond it, kept explicit for the design's
    stated 口径. Dedup is inherent (a set of ids). Deterministic order (sorted)
    so a run is reproducible.
    """
    qualified: set[str] = set()
    for sid, turns in session_turns.items():
        has_corr = sid in correction_session_ids
        long_anom = turns > _TURN_ANOMALY_HIGH
        single_with_corr = (turns == 1 and has_corr)
        if has_corr or long_anom or single_with_corr:
            qualified.add(sid)
    return sorted(qualified)[:_SAMPLE_CAP]


def _read_correction_session_ids(corrections_path: Path, since_ts: Optional[str] = None) -> set[str]:
    """Read distinct session_ids from corrections.jsonl (each line carries a
    session_id — verified). Fail-open: a missing/garbage file → empty set (a
    corrections read must never crash the weekly job)."""
    ids: set[str] = set()
    try:
        if not corrections_path.exists():
            return ids
        for line in corrections_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = entry.get("session_id")
            if sid and (since_ts is None or str(entry.get("ts", "")) >= since_ts):
                ids.add(sid)
    except OSError as exc:
        logger.warning("session_quality: corrections read failed (%s) — 0 corrections", exc)
    return ids


def run_session_quality(
    config: Optional[dict] = None,
    *,
    dry_run: bool = False,
    sampler: Optional[Callable[[], dict]] = None,
    scorer: Optional[Callable[..., dict]] = None,
    harvester: Optional[Callable[..., object]] = None,
    recorder: Optional[Callable[..., None]] = None,
    corrections_path: Optional[Path] = None,
) -> dict:
    """Weekly orchestration. All boundaries injected for tests.

    sampler() → {"session_turns": {sid: turns}, "sessions": {sid: {prompt, response, tool_names}}}
    scorer(prompt, response, tool_names) → session_scorer.score_session output
    harvester(session_id, prompt, score) → session_harvest.harvest_draft output
    recorder(session_id, score) → correction_tracker.record wrapper

    Returns a result dict {status, scored, low, drafts}.
    """
    corrections_path = corrections_path or (
        Path.home() / ".swarm-ai" / "state" / "corrections.jsonl"
    )

    # Wire production defaults lazily (kept out of the test path).
    if sampler is None:
        sampler = _default_sampler
    if scorer is None:
        from core.session_scorer import score_session, default_judge_fn

        def scorer(prompt, response, tool_names):  # noqa: E306
            return score_session(prompt=prompt, response=response,
                                 tool_names=tool_names, judge_fn=default_judge_fn)
    if harvester is None:
        from core.session_harvest import harvest_draft, default_invoke_fn
        from core.eval_service import get_eval_service

        def harvester(session_id, prompt, score):  # noqa: E306
            svc = get_eval_service()
            return harvest_draft(session_id=session_id, prompt=prompt, score=score,
                                 invoke_fn=default_invoke_fn, add_case_fn=svc.add_case)
    if recorder is None:
        from core.evolution.correction_tracker import CorrectionTracker

        def recorder(session_id, score):  # noqa: E306
            CorrectionTracker().record(
                "SESSION_LOW_QUALITY",
                evidence=f"{session_id}: goal={score.get('goal_score')} "
                         f"tool={score.get('tool_score')} — {score.get('reason','')}",
                correction_ref=session_id,  # idempotent by session (no triple-count)
            )

    sample = sampler()
    session_turns = sample.get("session_turns", {})
    sessions = sample.get("sessions", {})
    corr_ids = _read_correction_session_ids(corrections_path)

    selected = select_sessions(session_turns=session_turns, correction_session_ids=corr_ids)

    scored = low = drafts = 0
    low_details: list[dict] = []
    for sid in selected:
        s = sessions.get(sid) or {}
        result = scorer(s.get("prompt", ""), s.get("response", ""), s.get("tool_names", []))
        if result.get("status") in ("skipped", "error"):
            continue
        scored += 1
        worst = min(result.get("goal_score", 1.0), result.get("tool_score", 1.0))
        if worst < _LOW_SCORE_THRESHOLD:
            low += 1
            low_details.append({"session_id": sid, **result})
            if not dry_run:
                recorder(sid, result)
                if harvester(session_id=sid, prompt=s.get("prompt", ""), score=result) is not None:
                    drafts += 1

    return {"status": "success", "scored": scored, "low": low, "drafts": drafts,
            "low_details": low_details, "selected": len(selected)}


def _default_sampler() -> dict:
    """Production sampler: enumerate recent desktop sessions from db.messages,
    count turns, and pull each session's (prompt, response, tool_names). Kept out
    of the test path (tests inject a sampler). Async DB is bridged via asyncio.run
    since the weekly job runs in a plain worker thread."""
    import asyncio
    from database import get_database

    async def _gather():
        db = get_database()
        # Distinct recent sessions — reuse the messages table's session listing.
        # (A dedicated "list sessions in window" query is a future optimization;
        # for N=10 weekly the scan is negligible.)
        return await _enumerate_sessions(db)

    try:
        return asyncio.run(_gather())
    except Exception as exc:  # noqa: BLE001 — sampler is best-effort; never crash the job
        logger.warning("session_quality: sampler failed (%s) — empty sample", exc)
        return {"session_turns": {}, "sessions": {}}


async def _enumerate_sessions(db) -> dict:
    """Read recent sessions → turn counts + (prompt, response, tool_names). Uses
    the existing messages DAO (list_by_session). Isolated for testability."""
    session_turns: dict[str, int] = {}
    sessions: dict[str, dict] = {}
    # sessions table lists ids; messages carry the turns.
    session_rows = await db.sessions.list_all() if hasattr(db, "sessions") else []
    for srow in session_rows[:200]:  # bound the scan
        sid = srow.get("id")
        if not sid:
            continue
        msgs = await db.messages.list_by_session(sid)
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        session_turns[sid] = len(user_msgs)
        first_prompt = user_msgs[0].get("content", "") if user_msgs else ""
        asst = [m for m in msgs if m.get("role") == "assistant"]
        last_resp, tool_names = _extract_response_and_tools(asst)
        sessions[sid] = {"prompt": _as_text(first_prompt), "response": last_resp,
                         "tool_names": tool_names}
    return {"session_turns": session_turns, "sessions": sessions}


def _extract_response_and_tools(assistant_msgs: list[dict]) -> tuple[str, list[str]]:
    """From assistant messages, get the last text response + the ordered tool
    names actually called. Tool trajectory lives in content JSON blocks
    (type=tool_use) — parsed here (content_accumulator is a live serializer, not
    a deserializer, so we json.loads ourselves)."""
    tool_names: list[str] = []
    last_text = ""
    for m in assistant_msgs:
        content = m.get("content", "")
        blocks = _as_blocks(content)
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name"):
                tool_names.append(b["name"])
            elif b.get("type") == "text" and b.get("text"):
                last_text = b["text"]
    return last_text, tool_names


def _as_blocks(content) -> list:
    if isinstance(content, list):
        return content
    if isinstance(content, str) and content.strip().startswith("["):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _as_text(content) -> str:
    """A user message content may be a plain string or a blocks list."""
    if isinstance(content, str) and not content.strip().startswith("["):
        return content
    for b in _as_blocks(content):
        if isinstance(b, dict) and b.get("type") == "text":
            return b.get("text", "")
    return content if isinstance(content, str) else ""
