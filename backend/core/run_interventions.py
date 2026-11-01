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
- ``messages(raw)``     — the person's messages in those spans (None when unreadable);
- ``count(raw, ...)``   — ``{answers, corrections, rescues, total}``.
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
        return bool(text.strip()) and not text.startswith(CONTINUE_PREFIX)
    texts = [b.get("text", "") for b in blocks if isinstance(b, Mapping) and b.get("type") == "text"]
    return any(t.strip() and not t.startswith(CONTINUE_PREFIX) for t in texts)


def _db_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "data.db"


def messages(raw: Mapping[str, Any], *, db_path: Path | None = None,
             now: datetime | None = None) -> int | None:
    """The person's messages sent into the run's sessions while it ran; None when the
    chat store cannot be read (never a 0 for "unknown")."""
    if _utc(raw.get("created_at")) is None:
        return None              # a run that never recorded its start: unknown
    spans = windows(raw, now=now)
    if not spans:
        return 0
    path = db_path or _db_path()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        total = 0
        for sid, start, end in spans:
            rows = conn.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='user' "
                "AND created_at > ? AND created_at <= ?",
                (sid, _local_naive(start), _local_naive(end))).fetchall()
            total += sum(1 for (content,) in rows if _is_steer(content))
        return total
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _rescues(raw: Mapping[str, Any]) -> int:
    """A person landed the run (``human_override`` authorized by the person, not the
    dispatching agent) or confirmed it had landed (``landed_as`` with a person's
    ``confirm_landed`` request)."""
    n = 0
    if raw.get("landing") == "human_override":
        grants = raw.get("auth_grants") if isinstance(raw.get("auth_grants"), Mapping) else {}
        by_host = any(isinstance(g, Mapping) and g.get("authorized_by") == "host" for g in grants.values())
        n += 0 if by_host else 1
    if raw.get("landed_as"):
        requests = raw.get("land_requests") if isinstance(raw.get("land_requests"), Mapping) else {}
        if any(isinstance(r, Mapping) and r.get("action") == "confirm_landed" for r in requests.values()):
            n += 1
    return n


def count(raw: Mapping[str, Any], *, corrections: int | None = None) -> dict[str, int | None]:
    """``{answers, corrections, rescues, total}``. ``human_decisions`` in the projection
    holds the person's answers and the person's ``run-land``: the landing is a rescue, so
    it is taken out of the answers. ``corrections`` None (the chat store unreadable)
    makes ``total`` None: an unknown is never shown as a smaller number."""
    human = raw.get("human_decisions")
    human = human if isinstance(human, int) and not isinstance(human, bool) else 0
    landed_by_person = 1 if raw.get("landing") == "human_override" and _rescues(
        {"landing": "human_override", "auth_grants": raw.get("auth_grants")}) else 0
    answers = max(0, human - landed_by_person)
    rescues = _rescues(raw)
    total = None if corrections is None else answers + corrections + rescues
    return {"answers": answers, "corrections": corrections, "rescues": rescues, "total": total}


def for_run(raw: Mapping[str, Any], *, db_path: Path | None = None,
            now: datetime | None = None) -> dict[str, int | None]:
    """``count`` with the corrections read from the chat store."""
    return count(raw, corrections=messages(raw, db_path=db_path, now=now))
