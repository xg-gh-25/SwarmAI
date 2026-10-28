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
rate, examples}}, metrics: {turns, turn_p50_s, cost_p50_usd}, low_details,
judge_prompt_sha, sampler_error, judge_error, judge_sampled, judge_failed, trend}``.
``judge_error`` is set when every judged session failed (O4/O5 then did not run);
``judge_failed`` of ``judge_sampled`` counts the sampled sessions whose judge call
failed, which O4/O5 leave out; ``judge_prompt_sha`` names the
judge prompt that scored ``low_details`` (a calibration label counts only for it).
``trend`` is one entry per ISO week, sorted, last 12 kept: ``{week, at, rates,
metrics}``; this run replaces this week's entry, so ``trend[-2]`` is the week before
(core.eval_followups reads it that way). Weeks before the first run are filled once
from the session store (``backfilled: true``; O1–O3 and metrics only).
Readers: EvalService.get_session_quality (the Online tab), eval_followups, and the
calibration endpoint (``judge_prompt_sha``).

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

from core.turn_outcome import cost_increase

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


def _read_corrections(path: Path) -> tuple[list[tuple[float, str]], float | None]:
    """``([(ts, session_id)], log_start)``: the USER corrections (type
    user_correction; corrections.jsonl also logs tool failures, which are not a
    correction) and the earliest timestamp of ANY entry, i.e. from when the log
    covers. A missing or garbled file reads as none."""
    out: list[tuple[float, str]] = []
    start: float | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out, None
    for line in lines:
        try:
            entry = json.loads(line)
            ts = float(entry.get("ts"))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            continue
        start = ts if start is None else min(start, ts)
        if entry.get("type") == "user_correction" and entry.get("session_id"):
            out.append((ts, entry["session_id"]))
    return out, start


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


def split_turns(messages: list[dict], since: str, until: str | None = None) -> list[dict]:
    """``[{user, blocks, start, end}]``: each user message created in ``[since,
    until)`` with the assistant blocks that follow it, up to the next user message;
    ``start`` is the user message's time and ``end`` the last assistant message's
    (None when nothing answered)."""
    turns: list[dict] = []
    current: dict | None = None
    for m in messages:
        role = m.get("role")
        at = str(m.get("created_at") or "")
        if role == "user":
            current = None
            if at >= since and (until is None or at < until):
                current = {"user": _text_of(_as_blocks(m.get("content"))), "blocks": [],
                           "start": at, "end": None}
                turns.append(current)
        elif role == "assistant" and current is not None:
            current["blocks"].extend(_as_blocks(m.get("content")))
            current["end"] = at
    return turns


_MAX_TURN_S = 6 * 3600  # longer than this is a tab left open, not a turn


def _turn_seconds(turn: dict) -> float | None:
    try:
        d = (datetime.fromisoformat(turn["end"][:26]) - datetime.fromisoformat(turn["start"][:26])).total_seconds()
    except (TypeError, ValueError, KeyError):
        return None
    return d if 0 < d < _MAX_TURN_S else None


def _p50(values: list[float]) -> float | None:
    import statistics
    return statistics.median(values) if values else None


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


class _Deterministic:
    """O1–O3 and the turn metrics over sessions fed one at a time (``add``), so a
    long history never has to sit in memory at once."""

    def __init__(self, since: str, until: str | None, correction_ids: set[str] | None):
        self.since, self.until, self.correction_ids = since, until, correction_ids
        self.tallies = {cid: _Tally() for cid in CHECKS}
        self.turn_counts: dict[str, int] = {}
        self.durations: list[float] = []

    def add(self, sid: str, session: dict) -> None:
        turns = split_turns(session.get("messages") or [], self.since, self.until)
        if not turns:
            return
        self.turn_counts[sid] = len(turns)
        for turn in turns:
            for cid, check in (("O1_LANGUAGE", check_language), ("O2_SILENT_TOOLS", check_silent_tools)):
                applies, reason = check(turn)
                if applies:
                    self.tallies[cid].add(bool(reason), sid, session, turn["user"], reason)
            secs = _turn_seconds(turn)
            if secs is not None:
                self.durations.append(secs)
        if self.correction_ids is not None:   # None: the corrections log does not cover this window
            corrected = sid in self.correction_ids
            self.tallies["O3_CORRECTIONS"].add(corrected, sid, session, turns[0]["user"],
                                               "the user corrected the agent" if corrected else "")

    def metrics(self, costs: list[float]) -> dict:
        p50 = _p50(self.durations)
        cost = _p50(costs)
        return {"turns": sum(self.turn_counts.values()),
                "turn_p50_s": round(p50) if p50 is not None else None,
                "cost_p50_usd": round(cost, 3) if cost is not None else None}


def score_window(sessions: dict[str, dict], since: str, correction_ids: set[str],
                 scorer: Callable[..., dict], costs: list[float] | None = None) -> dict:
    """``sessions``: ``{sid: {title, messages}}`` for the window; ``costs`` the
    per-turn costs in it. Returns the ``sessions_checked``, ``checks``, ``metrics``,
    ``low_details``, ``judge_error``, ``judge_sampled`` and ``judge_failed`` parts of the
    state. A sampled session whose judge call failed is not in O4/O5: it is counted in
    ``judge_failed`` so the judged share is never read off a silently smaller sample."""
    det = _Deterministic(since, None, correction_ids)
    for sid in sorted(sessions):
        det.add(sid, sessions[sid])
    tallies, turn_counts = det.tallies, det.turn_counts
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
            "checks": {cid: t.out() for cid, t in tallies.items()},
            "metrics": det.metrics(costs or []), "low_details": low,
            "judge_error": judge_error,
            "judge_sampled": len(sample), "judge_failed": len(judge_errors)}


def _week_label(d: datetime) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _trend_entry(label: str, at: str | None, checks: dict, metrics: dict, **extra) -> dict:
    return {"week": label, "at": at,
            "rates": {cid: c["rate"] for cid, c in checks.items() if c["rate"] is not None},
            "metrics": metrics, **extra}


def _with_trend(trend: list[dict], entry: dict) -> list[dict]:
    """``trend`` with ``entry`` replacing any entry for the same week, sorted by
    week, last ``_TREND_WEEKS`` kept."""
    kept = [t for t in trend if isinstance(t, dict) and t.get("week") != entry["week"]]
    return sorted(kept + [entry], key=lambda t: str(t.get("week")))[-_TREND_WEEKS:]


def _missing_weeks(trend: list[dict], now_local: datetime) -> list[tuple[str, datetime, datetime]]:
    """The ``_TREND_WEEKS - 1`` whole ISO weeks before this one that have no trend
    entry: ``[(label, monday 00:00, next monday 00:00)]`` in local time."""
    have = {t.get("week") for t in trend if isinstance(t, dict)}
    monday = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for back in range(_TREND_WEEKS - 1, 0, -1):
        start = monday - timedelta(weeks=back)
        label = _week_label(start)
        if label not in have:
            out.append((label, start, start + timedelta(weeks=1)))
    return out


def _backfill(missing, history, corrections, cost_rows) -> list[dict]:
    """Trend entries for weeks before the first run, from the same session store
    and the same check functions (O1, O2, O3 where the corrections log covers the
    week, and the turn metrics). The judge checks O4/O5 are not backfilled: they
    cost a model call per session and would score old sessions with today's prompt.
    A week with no sessions still gets an entry (no rates, zero turns), so it is not
    read again on the next run."""
    if not missing:
        return []
    entries, log_start = corrections
    weeks = []
    for label, start, end in missing:
        covered = log_start is not None and log_start <= start.timestamp()
        ids = ({sid for ts, sid in entries if start.timestamp() <= ts < end.timestamp()}
               if covered else None)
        weeks.append((label, start, end,
                      _Deterministic(start.isoformat(), end.isoformat(), ids)))

    def consume(sid: str, session: dict) -> None:
        for *_, det in weeks:
            det.add(sid, session)

    history(missing[0][1].isoformat(), consume)
    out = []
    for label, start, end, det in weeks:
        costs = _turn_costs(cost_rows, start.isoformat(), end.isoformat())
        out.append(_trend_entry(label, None, {c: t.out() for c, t in det.tallies.items()},
                                det.metrics(costs), backfilled=True))
    return out


def _turn_costs(rows: list[tuple[str, str, float]], since: str, until: str | None) -> list[float]:
    """Per-turn cost in ``[since, until)`` from ``token_usage`` rows ``(session_id,
    timestamp, cost_usd)`` in insertion order. ``cost_usd`` is the CLI process's
    running total, so a turn's cost is the increase over the previous row of the
    same session; a lower value means a new process started (its value is that
    turn's cost), and so does a session's first row ever. The rows include each
    session's last row before the window (``token_cost_rows``) as its baseline."""
    prev: dict[str, float] = {}
    out: list[float] = []
    for sid, ts, cost in rows:
        try:
            c = float(cost)
        except (TypeError, ValueError):
            continue
        d = cost_increase((sid, prev[sid]) if sid in prev else None, sid, c)
        if d > 0 and str(ts) >= since and (until is None or str(ts) < until):
            out.append(d)
        prev[sid] = c
    return out


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
    history: Optional[Callable[[str, Callable[[str, dict], None]], None]] = None,
    cost_reader: Optional[Callable[[str], list]] = None,
) -> dict:
    """One weekly run. ``sampler(since)`` → ``{"sessions": {sid: {title, messages}}}``
    (or ``{"sampler_error": ...}``); ``scorer(prompt, response, tool_names)`` →
    session_scorer output; ``cost_reader(since)`` → ``token_usage`` rows
    ``(session_id, timestamp, cost_usd)``; ``history(since, consume)`` calls
    ``consume(sid, session)`` for every session with a user message since then
    (the weekly backfill). With an injected ``sampler`` and no ``history`` /
    ``cost_reader``, those parts are off (tests never reach the real database).
    Writes the state file unless ``dry_run``; returns it."""
    from config import get_app_data_dir, get_swarmws

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=WINDOW_DAYS)
    # messages.created_at is local time without a zone; corrections carry epoch seconds
    since = start.astimezone().replace(tzinfo=None).isoformat()
    corrections_path = corrections_path or (get_app_data_dir() / "state" / "corrections.jsonl")
    state_path = state_path or (get_swarmws() / ".context" / ".session-quality-state.json")
    if sampler is None:
        sampler = _default_sampler
        history = history or _default_history
        cost_reader = cost_reader or _default_costs
    from core.session_scorer import JUDGE_PROMPT_SHA
    if scorer is None:
        from core.session_scorer import default_judge_fn, score_session

        def scorer(prompt, response, tool_names):  # noqa: E306
            return score_session(prompt=prompt, response=response, tool_names=tool_names,
                                 judge_fn=default_judge_fn, reason_language=_asked_in(prompt))

    now_local = now.astimezone().replace(tzinfo=None)
    corrections = _read_corrections(corrections_path)
    trend = [t for t in ((_read_state(state_path) or {}).get("trend") or []) if isinstance(t, dict)]
    missing = _missing_weeks(trend, now_local) if history else []
    oldest = missing[0][1].isoformat() if missing else since
    cost_rows: list = []
    if cost_reader:
        try:
            cost_rows = list(cost_reader(oldest))
        except Exception as exc:  # noqa: BLE001 — cost is one metric; the checks still run
            logger.error("session_quality: cost read failed (%s)", exc)

    sample = sampler(since)
    sampler_error = sample.get("sampler_error")
    scored = score_window(sample.get("sessions") or {}, since,
                          {sid for ts, sid in corrections[0] if ts >= start.timestamp()}, scorer,
                          costs=_turn_costs(cost_rows, since, None))
    state = {"version": 3, "last_run": now.isoformat(), "window_days": WINDOW_DAYS, **scored,
             "judge_prompt_sha": JUDGE_PROMPT_SHA, "sampler_error": sampler_error}
    if not sampler_error:
        if missing:
            try:
                for entry in _backfill(missing, history, corrections, cost_rows):
                    trend = _with_trend(trend, entry)
            except Exception as exc:  # noqa: BLE001 — a failed backfill only leaves weeks empty
                logger.error("session_quality: weekly backfill failed (%s)", exc)
        trend = _with_trend(trend, _trend_entry(_week_label(now_local), now.isoformat(),
                                                scored["checks"], scored["metrics"]))
    state["trend"] = trend
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


def _default_history(since: str, consume: Callable[[str, dict], None]) -> None:
    """Every session with a user message at or after ``since``, one at a time
    (its messages are dropped before the next is read)."""
    import asyncio

    from database import get_database

    async def _walk():
        db = get_database()
        for sid in await db.messages.role_counts_by_session("user", since=since):
            consume(sid, {"title": "", "messages": await db.messages.list_by_session(sid)})

    asyncio.run(_walk())


def _default_costs(since: str) -> list[tuple[str, str, float]]:
    import asyncio

    from database import get_database

    return asyncio.run(get_database().token_cost_rows(since))
