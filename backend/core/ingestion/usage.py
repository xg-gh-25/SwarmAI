"""Usage bookkeeping for Cognitive Distillation Flow units.

- ``record_usage`` appends a server-observed use against the active unit head.
- ``record_usage_async`` queues bookkeeping on the bounded I/O executor.
- ``last_used`` returns the newest observed use or the unit write time.
- ``collapse_old_usage`` retains one historical row per anchor after 90 days.
"""
from __future__ import annotations

import logging
import functools
import sqlite3
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

USAGE_KINDS = frozenset({"recall_injected", "cited_as_evidence", "read_by_agent"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_anchor(conn: sqlite3.Connection, anchor: str) -> str | None:
    """Resolve a superseded anchor to its unique active head; fail closed."""
    from .units import Supersedes, active_head

    rows = conn.execute(
        "SELECT subject_anchor,object_anchor,inactive FROM ingestion_relations "
        "WHERE predicate='supersedes'"
    ).fetchall()
    relations = [
        Supersedes(str(row[0]), str(row[1]), inactive=bool(row[2]))
        for row in rows
    ]
    return active_head(anchor, relations)


def record_usage(
    anchor: str,
    kind: str,
    session_id: str,
    *,
    at: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Append one usage event. Invalid input and storage errors fail open."""
    if not anchor or kind not in USAGE_KINDS:
        return
    owned = conn is None
    try:
        if conn is None:
            from .store import connect_rw
            conn = connect_rw(busy_timeout_ms=500)
        head = _active_anchor(conn, anchor)
        if not head:
            return
        conn.execute(
            "INSERT INTO unit_usage(anchor,kind,session_id,at) VALUES(?,?,?,?)",
            (head, kind, str(session_id or ""), at or _now()),
        )
        if owned:
            conn.commit()
    except (OSError, sqlite3.Error, ValueError):
        logger.debug("CDF usage bookkeeping failed", exc_info=True)
    finally:
        if owned and conn is not None:
            conn.close()


def _log_future(future: Future[Any]) -> None:
    try:
        future.result()
    except Exception:
        logger.debug("CDF asynchronous usage bookkeeping failed", exc_info=True)


def record_usage_async(anchor: str, kind: str, session_id: str, *, at: str | None = None) -> None:
    """Queue one usage event and return immediately; never raise into a caller."""
    try:
        from core import executors
        future = executors.get_pool("io").submit(
            record_usage, anchor, kind, session_id, at=at,
        )
        future.add_done_callback(_log_future)
    except Exception:
        logger.debug("CDF usage dispatch failed", exc_info=True)


def record_many_async(anchors: Iterable[str], kind: str, session_id: str) -> None:
    """Queue distinct exact anchors; empty and duplicate identities are skipped."""
    for anchor in dict.fromkeys(str(value) for value in anchors if value):
        record_usage_async(anchor, kind, session_id)


def _unit_written_at(conn: sqlite3.Connection, anchor: str) -> str | None:
    identity = anchor.rsplit("#", 1)[-1]
    if identity.startswith("cand:"):
        unit_id = identity[len("cand:"):]
        row = conn.execute(
            "SELECT updated_at FROM ingestion_writes WHERE unit_id=? AND step='done' "
            "ORDER BY updated_at DESC LIMIT 1",
            (unit_id,),
        ).fetchone()
        if row is not None:
            return str(row[0])
    row = conn.execute(
        "SELECT observed_at FROM evidence_roots WHERE root_kind='corpus_unit' AND locator=? "
        "ORDER BY observed_at DESC LIMIT 1",
        (anchor,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def last_used(conn: sqlite3.Connection, anchor: str) -> str | None:
    """Return the active head's newest use, falling back to its write time."""
    head = _active_anchor(conn, anchor)
    if not head:
        return None
    row = conn.execute(
        "SELECT at FROM unit_usage WHERE anchor=? ORDER BY at DESC,rowid DESC LIMIT 1",
        (head,),
    ).fetchone()
    return str(row[0]) if row is not None else _unit_written_at(conn, head)


def collapse_old_usage(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """Delete all but the newest row per anchor among rows older than 90 days."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=90)).isoformat()
    cursor = conn.execute(
        """DELETE FROM unit_usage WHERE rowid IN (
             SELECT rowid FROM (
               SELECT rowid,ROW_NUMBER() OVER (
                 PARTITION BY anchor ORDER BY at DESC,rowid DESC
               ) AS position
               FROM unit_usage WHERE at < ?
             ) WHERE position > 1
           )""",
        (cutoff,),
    )
    conn.commit()
    return int(cursor.rowcount)


@functools.lru_cache(maxsize=64)
def _unit_spans(path_str: str, _mtime_ns: int, _size: int) -> tuple[tuple[str, int, int, str], ...]:
    """``(anchor, first line, last line, text)`` of every unit of one file version.
    Cached per (path, mtime, size): every Read/Grep hit re-parsed the whole file and
    counted its newlines once per unit (about 12 ms of GIL per hit on MEMORY.md)."""
    from bisect import bisect_right
    from pathlib import Path
    from config import get_swarmws
    from .units import parse_units

    target = Path(path_str)
    raw = target.read_bytes()
    breaks = [i for i, byte in enumerate(raw) if byte == 10]          # "\n" offsets
    out = []
    for unit in parse_units(target, root=get_swarmws()):
        out.append((unit.anchor, bisect_right(breaks, unit.byte_start - 1) + 1,
                    bisect_right(breaks, unit.byte_end - 1) + 1, unit.text))
    return tuple(out)


def _spans(path: Any) -> tuple[tuple[str, int, int, str], ...]:
    from pathlib import Path
    from .materializer import is_governed

    target = Path(path).expanduser().resolve(strict=False)
    if not target.is_file() or not is_governed(target):
        return ()
    stat = target.stat()
    return _unit_spans(str(target), stat.st_mtime_ns, stat.st_size)


def _units_overlapping_lines(path: Any, start_line: int, end_line: int | None) -> list[str]:
    """Return exact parse-unit anchors overlapping a 1-based line interval."""
    spans = _spans(path)
    high = end_line if end_line is not None else max((last for _a, _f, last, _t in spans), default=0)
    return [anchor for anchor, first, last, _text in spans if first <= high and last >= start_line]


def units_in_content(path: Any, content: str) -> list[str]:
    """Return units whose complete bytes occur in recalled content."""
    if not content:
        return []
    return [anchor for anchor, _f, _l, text in _spans(path) if text in content]


def record_recalled_result(result: Any, session_id: str, *, project: str | None = None) -> None:
    """Record only complete, exact CDF units present in an injected recall result."""
    if not session_id or result is None:
        return
    try:
        from config import get_swarmws
        root = get_swarmws()
        anchors: list[str] = []
        for domain, hits in (getattr(result, "buckets", {}) or {}).items():
            for hit in hits or []:
                if not isinstance(hit, Mapping):
                    continue
                content = str(hit.get("content") or "")
                if not content:
                    continue
                path = None
                if domain == "context_files":
                    path = root / ".context" / "MEMORY.md"
                elif domain == "library":
                    source = str(hit.get("source") or hit.get("source_file") or "")
                    path = root / "Knowledge" / source if source else None
                elif domain == "ddd" and project:
                    # the doc a DDD hit came from (2-understanding/<doc>, ddd_paths)
                    from core.ddd_paths import ddd_path
                    doc = str(hit.get("doc") or "")
                    path = ddd_path(root / "Projects" / project, doc) if doc else None
                if path is not None:
                    anchors.extend(units_in_content(path, content))
        record_many_async(anchors, "recall_injected", session_id)
    except Exception:
        logger.debug("CDF recall usage mapping failed", exc_info=True)


def _grep_line_hits(value: Any) -> list[tuple[str, int]]:
    """Extract exact path/line pairs from structured or conventional Grep output."""
    import re

    hits: list[tuple[str, int]] = []
    if isinstance(value, Mapping):
        path = value.get("file_path") or value.get("path") or value.get("file")
        line = value.get("line_number") or value.get("line")
        if isinstance(path, str) and isinstance(line, int):
            hits.append((path, line))
        for nested in value.values():
            hits.extend(_grep_line_hits(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            hits.extend(_grep_line_hits(nested))
    elif isinstance(value, str):
        for text_line in value.splitlines():
            match = re.match(r"^(.+?):(\d+)(?::|-)", text_line)
            if match:
                hits.append((match.group(1), int(match.group(2))))
    return hits


def _observe_tool_read(input_data: Mapping[str, Any], session_id: str) -> None:
    """Map one completed Read/Grep call to exact governed units."""
    from pathlib import Path
    from config import get_swarmws

    tool = str(input_data.get("tool_name") or "")
    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, Mapping):
        return
    cwd = Path(str(input_data.get("cwd") or get_swarmws()))
    anchors: list[str] = []
    if tool == "Read":
        raw_path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(raw_path, str) and raw_path:
            path = Path(raw_path)
            path = path if path.is_absolute() else cwd / path
            offset = max(0, int(tool_input.get("offset") or 0))
            limit = tool_input.get("limit")
            end = offset + max(1, int(limit)) if limit is not None else None
            anchors.extend(_units_overlapping_lines(path, offset + 1, end))
    elif tool == "Grep":
        base_value = tool_input.get("path") or cwd
        base = Path(str(base_value))
        base = base if base.is_absolute() else cwd / base
        for raw_path, line in _grep_line_hits(input_data.get("tool_response")):
            path = Path(raw_path)
            if not path.is_absolute():
                path = (base if base.is_dir() else base.parent) / path
            anchors.extend(_units_overlapping_lines(path, line, line))
    record_many_async(anchors, "read_by_agent", session_id)


# A Bash command can read a governed file only if it names one of these roots (or a file
# under them, relative to a directory under them). Checked on the event loop for every
# Bash call, so it is a plain substring test; the real resolution runs in the pool.
_BASH_ROOT_MARKS = ("Projects/", "Knowledge/", ".context/", "MEMORY.md", "EVOLUTION.md",
                    "TIMELINE.md", "STEERING.md", "USER.md", "TOOLS.md")


_MAX_BASH_STDOUT = 1_000_000      # characters of stdout matched; the rest is not looked at
_SEPARATORS = {";", "&&", "||", "|", "&"}
_WRITE_OPS = {">", ">>", "tee"}


def _bash_paths(command: str, cwd: Any) -> tuple[list[Any], list[Any]]:
    """``(read, bases)``: the governed files *command* names as something it reads, resolved,
    once each, and the directories it resolved them in. A ``cd`` resolves against the
    directory the one before it left, and the latest one is tried first. A file named as
    the target of a redirect or ``tee`` is written, not read."""
    import shlex
    from pathlib import Path
    from .materializer import is_governed

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    bases = [Path(str(cwd)).expanduser()]
    found: list[Any] = []
    previous = ""
    for i, token in enumerate(tokens):
        if previous == "cd" and token not in _SEPARATORS:
            target = Path(token).expanduser()
            bases.append(target if target.is_absolute() else bases[-1] / target)
        elif (previous not in _WRITE_OPS and not (previous == "-a" and i > 1 and tokens[i - 2] == "tee")
              and token and not token.startswith("-") and token not in _SEPARATORS
              and not any(ch in token for ch in "*?$`<>")):
            raw = Path(token).expanduser()
            for base in ([None] if raw.is_absolute() else reversed(bases)):
                candidate = raw if base is None else base / raw
                try:
                    if candidate.is_file() and is_governed(candidate):
                        resolved = candidate.resolve()
                        if resolved not in found:
                            found.append(resolved)
                        break
                except OSError:
                    continue
        previous = token
    return found, bases


def _single_grep_n(command: str) -> bool:
    """Whether *command* is one grep / rg call with line numbers and no other command:
    only then is a bare ``N:`` line of its output a line of the one file it reads."""
    import shlex
    if any(sep in command for sep in (";", "&&", "||", "|", "\n")):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or tokens[0] not in {"grep", "egrep", "rg"}:
        return False
    flags = [t for t in tokens[1:] if t.startswith("-") and not t.startswith("--")]
    return "--line-number" in tokens or any("n" in f[1:] for f in flags)


def _observe_bash_read(input_data: Mapping[str, Any], session_id: str) -> None:
    """Map one completed Bash call to the governed units its stdout shows whole.

    Most reads of brain files go through Bash (``sed -n``, ``cat``, ``grep -n``), not
    Read. A unit counts as read when its body (without its own metadata comments, at
    least ``withdrawn.MIN_RESTATED`` characters, not a heading alone) is whole in stdout,
    or a ``path:N:`` grep line names it (the path resolved as the command resolved its
    files), or, for one ``grep -n`` / ``rg -n`` call on one file, a bare ``N:`` line
    within that file's lines does. Half a unit, a file the command writes, and stdout past
    ``_MAX_BASH_STDOUT`` are not reads."""
    import re
    from pathlib import Path
    from config import get_swarmws
    from .withdrawn import MIN_RESTATED

    tool_input = input_data.get("tool_input") or {}
    command = str(tool_input.get("command") or "") if isinstance(tool_input, Mapping) else ""
    response = input_data.get("tool_response")
    stdout = str(response.get("stdout") or "") if isinstance(response, Mapping) else str(response or "")
    if not command or not stdout:
        return
    stdout = stdout[:_MAX_BASH_STDOUT]
    paths, bases = _bash_paths(command, input_data.get("cwd") or get_swarmws())
    anchors: list[str] = []
    for path in paths:
        for anchor, _first, _last, text in _spans(path):
            # the unit's body without its own metadata comments: a range that stops
            # before the `<!-- cand:... -->` line still read the whole entry
            body = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
            lines = [line for line in body.splitlines() if line.strip()]
            if (len(body) >= MIN_RESTATED and not all(line.lstrip().startswith("#") for line in lines)
                    and body in stdout):
                anchors.append(anchor)
    for raw_path, line in _grep_line_hits(stdout):
        raw = Path(raw_path).expanduser()
        for base in ([None] if raw.is_absolute() else reversed(bases)):
            candidate = raw if base is None else base / raw
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in paths:
                anchors.extend(_units_overlapping_lines(resolved, line, line))
                break
    if len(paths) == 1 and _single_grep_n(command):
        total = max((last for _a, _f, last, _t in _spans(paths[0])), default=0)
        for text_line in stdout.splitlines():
            match = re.match(r"^(\d+)[:-]", text_line)
            if match and 1 <= int(match.group(1)) <= total:
                number = int(match.group(1))
                anchors.extend(_units_overlapping_lines(paths[0], number, number))
    record_many_async(list(dict.fromkeys(anchors)), "read_by_agent", session_id)


def create_usage_observer(session_context: Mapping[str, Any]):
    """Create an inject-nothing, deny-nothing, non-blocking Read/Grep/Bash observer.

    A Bash call is handed to the pool only when its command names a governed root
    (``_BASH_ROOT_MARKS``); every other Bash call returns at once."""
    session_id = str(
        session_context.get("app_session_id")
        or session_context.get("sdk_session_id")
        or ""
    )

    async def observer(input_data: Any, _tool_use_id: Any, _context: Any) -> dict:
        try:
            tool = input_data.get("tool_name") if isinstance(input_data, Mapping) else None
            job = None
            if tool in {"Read", "Grep"}:
                job = _observe_tool_read
            elif tool == "Bash":
                tool_input = input_data.get("tool_input") or {}
                command = str(tool_input.get("command") or "") if isinstance(tool_input, Mapping) else ""
                if any(mark in command for mark in _BASH_ROOT_MARKS):
                    job = _observe_bash_read
            if job is not None:
                from core import executors
                future = executors.get_pool("io").submit(job, input_data, session_id)
                future.add_done_callback(_log_future)
        except Exception:
            logger.debug("CDF read-usage observer failed", exc_info=True)
        return {}

    return observer
