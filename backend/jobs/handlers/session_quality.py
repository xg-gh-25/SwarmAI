"""session_quality — online eval scoring of the owner's real sessions (design
docs/design/2026-10-26-eval-as-product.md §3.5, the checks O1–O5 in §3.8).

Runs weekly on the user's own machine and reads only that user's sessions from the
last ``WINDOW_DAYS`` days. There is no expected answer: it checks whether rules held
and tasks got done in real use.

- Deterministic checks over every turn of every session in the window, with the
  same check functions the offline behavior cases use:
  - O1_LANGUAGE: the reply is in the user's language (R19; ``reply_cjk`` /
    ``eval_runner.prose_language``);
  - O2_SILENT_TOOLS: no text before a tool call except the opening restatement (R20;
    ``no_pre_tool_text`` over ``scenario_runner.pre_tool_text_lengths``);
  - O3_CORRECTIONS: the share of sessions in which the user corrected the agent.
- Judge checks on a sample of at most ``_SAMPLE_CAP`` sessions (ones with a user
  correction or an unusual number of turns first), via ``session_scorer``, each on
  one exchange (the first answered question in the window, with its own answer and
  tools):
  - O4_GOAL: was what the user asked for delivered;
  - O5_TOOLS: were the tools chosen sensibly.

The result is written to ``<workspace>/.context/.session-quality-state.json``
(gitignored: it holds session titles, snippets and reasons for the owner):
``{version, last_run, window_days, sessions_checked, checks: {id: {checked, failed,
rate, examples}}, low_details, sampler_error, judge_error, trend}``; ``judge_error``
is set when every judged session failed (O4/O5 then did not run). ``trend`` gets this week's
rates appended before the file is written (one entry per ISO week, last 12 kept), so
``trend[-2]`` is the week before (core.eval_followups reads it that way).
Readers: EvalService.get_session_quality (the Online tab) and eval_followups.

A broken sampler is recorded as ``sampler_error`` and is not shown as zero; an empty
window gives ``sessions_checked: 0``. All boundaries are injected for tests.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
_SAMPLE_CAP = 10          # sessions sent to the judge per run
_TURN_ANOMALY_HIGH = 20   # more user turns than this is unusual
_LOW_SCORE_THRESHOLD = 0.6
_TREND_WEEKS = 12
_EXAMPLES = 3
_TITLE_MAX, _SNIPPET_MAX, _REASON_MAX = 80, 160, 240
CHECKS = ("O1_LANGUAGE", "O2_SILENT_TOOLS", "O3_CORRECTIONS", "O4_GOAL", "O5_TOOLS")


# ── sampling ──────────────────────────────────────────────────────────────────

def select_sessions(*, session_turns: dict[str, int], correction_session_ids: set[str]) -> list[str]:
    """The judge sample: sessions with a user correction, then those with more than
    ``_TURN_ANOMALY_HIGH`` user turns, capped at ``_SAMPLE_CAP``. Deterministic order
    so a run is reproducible."""
    corrected = sorted(s for s in session_turns if s in correction_session_ids)
    long = sorted(s for s, n in session_turns.items()
                  if n > _TURN_ANOMALY_HIGH and s not in correction_session_ids)
    return (corrected + long)[:_SAMPLE_CAP]


def _read_correction_session_ids(path: Path, since_epoch: float) -> set[str]:
    """Sessions in which the USER corrected the agent (type user_correction) at or
    after ``since_epoch``. corrections.jsonl also logs tool failures, which are not a
    correction. A missing or garbled file reads as none."""
    ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ids
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user_correction":
            continue
        try:
            ts = float(entry.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts >= since_epoch and entry.get("session_id"):
            ids.add(entry["session_id"])
    return ids


# ── turns and checks ──────────────────────────────────────────────────────────

def _as_blocks(content) -> list:
    if isinstance(content, list):
        return content
    if isinstance(content, str) and content.strip().startswith("["):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None    # plain text that happens to start with "[", e.g. "[Attached …]"
        if isinstance(parsed, list):
            return parsed
    return [{"type": "text", "text": content}] if isinstance(content, str) and content else []


def _text_of(blocks: list) -> str:
    return "\n".join(b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))


def split_turns(messages: list[dict], since: str) -> list[dict]:
    """``[{user, blocks}]``: each user message created at or after ``since`` with the
    assistant blocks that follow it, up to the next user message."""
    turns: list[dict] = []
    current: dict | None = None
    for m in messages:
        role = m.get("role")
        if role == "user":
            current = None
            if str(m.get("created_at") or "") >= since:
                current = {"user": _text_of(_as_blocks(m.get("content"))), "blocks": []}
                turns.append(current)
        elif role == "assistant" and current is not None:
            current["blocks"].extend(_as_blocks(m.get("content")))
    return turns


def _final_text(blocks: list) -> str:
    """The turn's answer: its last text block, as the offline check reads the
    agent's final text (not the narration between tool calls)."""
    texts = [t for t in (_text_of([b]) for b in blocks if isinstance(b, dict)) if t.strip()]
    return texts[-1] if texts else ""


# Lines the app itself wraps around what the user sent ("[Attached terminal output]",
# "[File Review: `path`]", "Selected text:"): not the user's words, so not their language.
_WRAPPER_LINE = re.compile(r"^\s*(\[[^\]\n]{1,300}\]|Selected text:)\s*$", re.M)


def _asked_in(text: str) -> str | None:
    """The user's language. A Chinese question that pastes an English log is still a
    Chinese question (R19: CJK in, CJK out), so two CJK characters outside code make
    it Chinese; otherwise English words make it English. The app's own wrapper lines
    are left out; a message that is only wrappers has no language."""
    from scripts.eval_runner import prose_counts

    cjk, latin = prose_counts(_WRAPPER_LINE.sub(" ", text or ""))
    if cjk >= 2:
        return "cjk"
    return "latin" if latin else None


def check_language(turn: dict) -> tuple[bool, str]:
    """(applies, failure reason or ""). R19: the reply follows the user's language."""
    from scripts.eval_runner import _behavior_check_failures, prose_language

    asked, reply = _asked_in(turn["user"]), _final_text(turn["blocks"])
    if asked is None or not reply.strip():
        return False, ""
    if asked == "cjk":
        failed = _behavior_check_failures(["reply_cjk"], reply, [])
        return True, ("asked in Chinese, answered otherwise" if failed else "")
    return True, ("asked in English, answered in Chinese" if prose_language(reply) == "cjk" else "")


def check_silent_tools(turn: dict) -> tuple[bool, str]:
    """(applies, failure reason or ""). R20: no text before a tool call except the
    opening restatement."""
    from scripts.eval_runner import _behavior_check_failures
    from scripts.scenario_runner import pre_tool_text_lengths

    lengths = pre_tool_text_lengths(turn["blocks"])
    if not lengths:
        return False, ""
    failed = _behavior_check_failures(["no_pre_tool_text"], "", lengths)
    return True, (f"text before tool calls: {lengths}" if failed else "")


# ── scoring ───────────────────────────────────────────────────────────────────

class _Tally:
    def __init__(self):
        self.checked = self.failed = 0
        self.examples: list[dict] = []

    def add(self, failed: bool, sid: str, session: dict, snippet: str, reason: str):
        self.checked += 1
        if not failed:
            return
        self.failed += 1
        if len(self.examples) < _EXAMPLES and all(e["session_id"] != sid for e in self.examples):
            self.examples.append({"session_id": sid, "title": (session.get("title") or "")[:_TITLE_MAX],
                                  "snippet": (snippet or "")[:_SNIPPET_MAX], "reason": reason[:_REASON_MAX]})

    def out(self) -> dict:
        return {"checked": self.checked, "failed": self.failed,
                "rate": round(self.failed / self.checked, 3) if self.checked else None,
                "examples": self.examples}


def score_window(sessions: dict[str, dict], since: str, correction_ids: set[str],
                 scorer: Callable[..., dict]) -> dict:
    """``sessions``: ``{sid: {title, messages}}`` for the window. Returns the
    ``sessions_checked``, ``checks`` and ``low_details`` parts of the state."""
    tallies = {cid: _Tally() for cid in CHECKS}
    turn_counts: dict[str, int] = {}
    for sid in sorted(sessions):
        session = sessions[sid]
        turns = split_turns(session.get("messages") or [], since)
        if not turns:
            continue
        turn_counts[sid] = len(turns)
        for turn in turns:
            for cid, check in (("O1_LANGUAGE", check_language), ("O2_SILENT_TOOLS", check_silent_tools)):
                applies, reason = check(turn)
                if applies:
                    tallies[cid].add(bool(reason), sid, session, turn["user"], reason)
        corrected = sid in correction_ids
        tallies["O3_CORRECTIONS"].add(corrected, sid, session, turns[0]["user"],
                                      "the user corrected the agent" if corrected else "")
    low: list[dict] = []
    judge_errors: list[str] = []
    sample = select_sessions(session_turns=turn_counts, correction_session_ids=correction_ids)
    for sid in sample:
        session = sessions[sid]
        turns = split_turns(session.get("messages") or [], since)
        # The judge sees ONE exchange: a question with its own answer and tools. The
        # first question paired with the session's last answer reads as "delivered
        # something else" in any multi-turn session.
        turn = next((t for t in turns if t["user"].strip() and _final_text(t["blocks"]).strip()), turns[0])
        tools = [b.get("name") for b in turn["blocks"] if isinstance(b, dict) and b.get("type") == "tool_use"]
        result = scorer(turn["user"], _final_text(turn["blocks"]), tools)
        if result.get("status") == "error":
            judge_errors.append(str(result.get("notes") or "judge error"))
            continue
        if result.get("status") == "skipped":
            continue
        goal, tool = result.get("goal_score", 1.0), result.get("tool_score", 1.0)
        reason = str(result.get("reason") or "")
        tallies["O4_GOAL"].add(goal < _LOW_SCORE_THRESHOLD, sid, session, turn["user"], reason)
        tallies["O5_TOOLS"].add(tool < _LOW_SCORE_THRESHOLD, sid, session, turn["user"], reason)
        if min(goal, tool) < _LOW_SCORE_THRESHOLD:
            low.append({"session_id": sid, "title": (session.get("title") or "")[:_TITLE_MAX],
                        "goal_score": goal, "tool_score": tool, "dimension": result.get("dimension"),
                        "reason": reason[:_REASON_MAX]})
    # Every judged session failing is a fault, not "no data" (the judge is down).
    judge_error = (f"{len(judge_errors)}/{len(sample)} judged sessions failed: "
                   f"{judge_errors[0].split(':', 1)[0][:80]}"
                   if sample and len(judge_errors) == len(sample) else None)
    return {"sessions_checked": len(turn_counts),
            "checks": {cid: t.out() for cid, t in tallies.items()}, "low_details": low,
            "judge_error": judge_error}


def _with_trend(previous: dict | None, checks: dict, now: datetime) -> list[dict]:
    year, week, _ = now.isocalendar()
    label = f"{year}-W{week:02d}"
    trend = [t for t in (previous or {}).get("trend") or [] if isinstance(t, dict) and t.get("week") != label]
    trend.append({"week": label, "at": now.isoformat(),
                  "rates": {cid: c["rate"] for cid, c in checks.items() if c["rate"] is not None}})
    return trend[-_TREND_WEEKS:]


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_session_quality(
    config: Optional[dict] = None,
    *,
    dry_run: bool = False,
    sampler: Optional[Callable[[str], dict]] = None,
    scorer: Optional[Callable[..., dict]] = None,
    corrections_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    """One weekly run. ``sampler(since)`` → ``{"sessions": {sid: {title, messages}}}``
    (or ``{"sampler_error": ...}``); ``scorer(prompt, response, tool_names)`` →
    session_scorer output. Writes the state file unless ``dry_run``; returns it."""
    from config import get_app_data_dir, get_swarmws

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=WINDOW_DAYS)
    # messages.created_at is local time without a zone; corrections carry epoch seconds
    since = start.astimezone().replace(tzinfo=None).isoformat()
    corrections_path = corrections_path or (get_app_data_dir() / "state" / "corrections.jsonl")
    state_path = state_path or (get_swarmws() / ".context" / ".session-quality-state.json")
    sampler = sampler or _default_sampler
    if scorer is None:
        from core.session_scorer import default_judge_fn, score_session

        def scorer(prompt, response, tool_names):  # noqa: E306
            return score_session(prompt=prompt, response=response,
                                 tool_names=tool_names, judge_fn=default_judge_fn)

    sample = sampler(since)
    sampler_error = sample.get("sampler_error")
    scored = score_window(sample.get("sessions") or {}, since,
                          _read_correction_session_ids(corrections_path, start.timestamp()), scorer)
    state = {"version": 2, "last_run": now.isoformat(), "window_days": WINDOW_DAYS, **scored,
             "sampler_error": sampler_error}
    state["trend"] = (_read_state(state_path) or {}).get("trend", []) if sampler_error \
        else _with_trend(_read_state(state_path), scored["checks"], now)
    if not dry_run:
        try:
            _write_state(state_path, state)
        except OSError as exc:
            logger.error("session_quality: state write failed: %s", exc)
            state["sampler_error"] = sampler_error or f"state write failed: {type(exc).__name__}"
    return state


def _default_sampler(since: str) -> dict:
    """The sessions with a user message at or after ``since``: one indexed aggregate,
    then each session's messages and title. A failure is returned as
    ``sampler_error``, never as an empty week."""
    import asyncio

    from database import get_database

    async def _gather():
        db = get_database()
        ids = await db.messages.role_counts_by_session("user", since=since)
        sessions = {}
        for sid in ids:
            row = await db.sessions.get(sid) or {}
            sessions[sid] = {"title": row.get("title") or "",
                             "messages": await db.messages.list_by_session(sid)}
        return {"sessions": sessions}

    try:
        return asyncio.run(_gather())
    except Exception as exc:  # noqa: BLE001 — recorded, shown as "did not run"
        logger.error("session_quality: sampler FAILED (%s: %s)", type(exc).__name__, exc)
        return {"sessions": {}, "sampler_error": f"{type(exc).__name__}: {exc}"}
