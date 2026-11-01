"""How often a person stepped into a local pipeline run (design
docs/design/2026-11-01-brain-eval-and-runs.md §3).

An intervention is a time the run needed the person to go on, or to go right:

- **answer** — the person answered a decision the run asked (``human_answer``);
- **correction** — a message the person sent into the run's session while it ran (the
  same measure as a Hive run's ``said``, ``core/swarm_out_results``). A message that only
  asks "where are you" counts too: nothing here judges what a message meant;
- **rescue** — the run could not land and the person landed it (``run-land``, landing
  ``human_override``), or confirmed it had already landed (``landed_as`` by a person).

Not interventions: the oversight the product keeps on purpose — approving or refusing a
dangerous command, accepting or discarding a deliverable, accepting a finding, a Judge's
or a reviewer's verdict. On a Hive, what the dispatching agent answered is its own and is
not counted here (``host_decisions``).

Public:
- ``windows(raw)``      — the (session id, start, end) spans the run was driven in;
- ``corrections(raw)``  — the person's messages in those spans, ``{at, text}`` (None when unreadable);
- ``messages(raw)``     — how many (None when unreadable);
- ``count(raw, ...)``   — ``{answers, corrections, rescues, total}``;
- ``details(raw, events)`` — each answer, correction and rescue, for the big card.
``raw`` is a run's projection (``run.json`` / the engine's read model).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

#: the prompt the Pipelines view puts in the owner session to continue a run
#: (core/pipeline_human.continue_prompt): sending it is not the person steering
CONTINUE_PREFIX = "Continue pipeline run "
#: the note the Pipelines view puts in front of it after a decision was answered there
#: (desktop PipelineRunCard): the answer is already counted, as an answer
ANSWER_PREFIX = "I answered decision "
_NOT_STEERING = (CONTINUE_PREFIX, ANSWER_PREFIX)


def _utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def windows(raw: Mapping[str, Any], *, now: datetime | None = None) -> list[tuple[str, datetime, datetime]]:
    """Each session that drove the run and when: the earlier owners from
    ``session_history`` (each until it was taken over), then the current
    ``session_id`` until the run finished (or now)."""
    start = _utc(raw.get("created_at"))
    if start is None:
        return []
    end = _utc(raw.get("finished_at")) or now or datetime.now(timezone.utc)
    out: list[tuple[str, datetime, datetime]] = []
    cursor = start
    for row in raw.get("session_history") or []:
        if not isinstance(row, Mapping):
            continue
        until = _utc(row.get("until"))
        sid = row.get("session_id")
        if isinstance(sid, str) and sid and until is not None and until > cursor:
            out.append((sid, cursor, min(until, end)))
            cursor = until
    sid = raw.get("session_id")
    if isinstance(sid, str) and sid and end > cursor:
        out.append((sid, cursor, end))
    return out


def _local_naive(dt: datetime) -> str:
    """``messages.created_at`` is written in local time without a zone."""
    return dt.astimezone().replace(tzinfo=None).isoformat()


def _is_steer(content: Any) -> bool:
    """A message the person typed: it has a text block that is not the continue prompt."""
    try:
        blocks = json.loads(content) if isinstance(content, str) and content.startswith("[") else None
    except ValueError:
        blocks = None
    if blocks is None:
        text = content if isinstance(content, str) else ""
        return bool(text.strip()) and not text.startswith(_NOT_STEERING)
    texts = [b.get("text", "") for b in blocks if isinstance(b, Mapping) and b.get("type") == "text"]
    return any(t.strip() and not t.startswith(_NOT_STEERING) for t in texts)


def _db_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "data.db"


def _text_of(content: Any) -> str:
    """The typed text of a message (its text blocks), for a correction's one line."""
    try:
        blocks = json.loads(content) if isinstance(content, str) and content.startswith("[") else None
    except ValueError:
        blocks = None
    if blocks is None:
        return content if isinstance(content, str) else ""
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, Mapping) and b.get("type") == "text")


def corrections(raw: Mapping[str, Any], *, db_path: Path | None = None,
                now: datetime | None = None) -> list[dict[str, str]] | None:
    """The person's messages sent into the run's sessions while it ran, oldest first, as
    ``{at, text}`` (``at`` as the chat store keeps it: local time); None when the chat
    store cannot be read or the run never recorded its start (never [] for "unknown")."""
    if _utc(raw.get("created_at")) is None:
        return None              # a run that never recorded its start: unknown
    spans = windows(raw, now=now)
    if not spans:
        return []
    path = db_path or _db_path()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        out: list[dict[str, str]] = []
        for sid, start, end in spans:
            rows = conn.execute(
                "SELECT content, created_at FROM messages WHERE session_id=? AND role='user' "
                "AND created_at > ? AND created_at <= ? ORDER BY created_at",
                (sid, _local_naive(start), _local_naive(end))).fetchall()
            out += [{"at": str(at), "text": " ".join(_text_of(content).split())[:200]}
                    for content, at in rows if _is_steer(content)]
        return out
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def messages(raw: Mapping[str, Any], *, db_path: Path | None = None,
             now: datetime | None = None) -> int | None:
    """How many corrections (``corrections``); None when unknown."""
    rows = corrections(raw, db_path=db_path, now=now)
    return None if rows is None else len(rows)


def _landed_by_person(raw: Mapping[str, Any]) -> bool:
    """The run's override landing (``human_override``) was the person's, not the
    dispatching agent's: the fold's ``landed_by``. A projection written before it was kept
    has none: a local run has no dispatching agent, so it was the person's."""
    return raw.get("landing") == "human_override" and raw.get("landed_by", "person") == "person"


def _confirmed_by_person(raw: Mapping[str, Any]) -> bool:
    """The run's ``landed_as`` was a person's confirmation: the fold's
    ``landed_as_request``; on an older projection, a consumed ``confirm_landed``
    authorization (a request nobody approved is not one)."""
    if not raw.get("landed_as"):
        return False
    if raw.get("landed_as_request"):
        return True
    consumed = raw.get("auth_consumed") if isinstance(raw.get("auth_consumed"), Mapping) else {}
    return "landed_as_request" not in raw and any(
        isinstance(c, Mapping) and c.get("action") == "confirm_landed" for c in consumed.values())


def _rescues(raw: Mapping[str, Any]) -> int:
    """A person landed the run (``_landed_by_person``) or confirmed it had landed
    (``_confirmed_by_person``)."""
    return int(_landed_by_person(raw)) + int(_confirmed_by_person(raw))


def count(raw: Mapping[str, Any], *, corrections: int | None = None) -> dict[str, int | None]:
    """``{answers, corrections, rescues, total}``. ``human_decisions`` in the projection
    holds the person's answers and the person's ``run-land``: the landing is a rescue, so
    it is taken out of the answers. ``corrections`` None (the chat store unreadable)
    makes ``total`` None: an unknown is never shown as a smaller number."""
    human = raw.get("human_decisions")
    human = human if isinstance(human, int) and not isinstance(human, bool) else 0
    answers = max(0, human - int(_landed_by_person(raw)))
    rescues = _rescues(raw)
    total = None if corrections is None else answers + corrections + rescues
    return {"answers": answers, "corrections": corrections, "rescues": rescues, "total": total}


def for_run(raw: Mapping[str, Any], *, db_path: Path | None = None,
            now: datetime | None = None) -> dict[str, int | None]:
    """``count`` with the corrections read from the chat store."""
    return count(raw, corrections=messages(raw, db_path=db_path, now=now))


def details(raw: Mapping[str, Any], events: list[Mapping[str, Any]], *,
            question: Any = None, db_path: Path | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    """Each time a person stepped in, for the run's big card: ``answers`` (the person's
    ``human_answer`` events: when, which request, the option, and the question when
    *question*\(request_id) gives it), ``corrections`` (``corrections``; None when
    unknown) and ``rescues`` (a person's override landing or confirmed landed-as). The
    counts stay ``count``'s; this is the list behind them."""
    answers, rescues = [], []
    for e in events:
        kind, payload = e.get("kind"), e.get("payload") if isinstance(e.get("payload"), Mapping) else {}
        if kind == "human_answer":
            rid = payload.get("request_id")
            q = None
            if callable(question) and isinstance(rid, str):
                try:
                    q = question(rid)
                except Exception:  # noqa: BLE001 -- a question that cannot be read leaves it out
                    q = None
            answers.append({"at": e.get("at"), "request_id": rid, "option_id": payload.get("option_id"),
                            "question": q})
        elif payload.get("landing") == "human_override":
            # the same predicates as ``count``: the list and the counts never disagree
            if _landed_by_person(raw):
                rescues.append({"at": e.get("at"), "kind": "run_land"})
        elif kind == "landed_as" and payload.get("request_id") and _confirmed_by_person(raw):
            rescues.append({"at": e.get("at"), "kind": "confirm_landed"})
    return {"answers": answers, "corrections": corrections(raw, db_path=db_path, now=now),
            "rescues": rescues}
