"""Read-only queries for the Cognitive Distillation Flow dashboard.

- ``flow_overview`` returns bounded unit-level audit counts.
- ``list_items`` and ``item_detail`` expose recent candidates safely.
- ``daily_stats`` and ``health`` expose aggregate history and health.
- ``drain_access_log`` lets the ingestion worker persist queued detail reads.
- ``brain_changes`` / ``brain_change_summary``: what was added, overridden, withdrawn
  (and the judge's unresolved conflicts) in the main cognition files, from the records.
- ``corrections`` / ``read_corrections``: every correction the after-session review
  recorded, with its session, the skill it names and whether it repeats an earlier one —
  the one source of "the user corrected the agent" (O3, skill health, the repeat rate).
"""
from __future__ import annotations

from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

from config import get_swarmws
from core.memory_guard import MemoryGuard
from .api import connect_ro, ingestion_db_path

_WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
_MAX_FIELD_BYTES = 16 * 1024
_ACCESS_LOG: deque[tuple[str, str, str]] = deque(maxlen=512)
_ACCESS_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _clip_utf8(value: str, maximum: int = _MAX_FIELD_BYTES) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= maximum:
        return value
    return raw[:maximum].decode("utf-8", errors="ignore")


def _page_utf8(value: str, offset: int, maximum: int = _MAX_FIELD_BYTES) -> tuple[str, int | None, int]:
    raw = value.encode("utf-8")
    start = min(max(0, offset), len(raw))
    end = min(len(raw), start + maximum)
    while end > start:
        try:
            text = raw[start:end].decode("utf-8")
            break
        except UnicodeDecodeError:
            end -= 1
    else:
        text = ""
    return text, (end if end < len(raw) else None), len(raw)


def _sanitize_text(value: Any, *, offset: int = 0, pages: list[dict[str, Any]] | None = None,
                   path: str = "$") -> Any:
    if not isinstance(value, str):
        return value
    clean = MemoryGuard().scan(value).sanitized_content
    text, next_offset, total = _page_utf8(clean, offset)
    if total <= _MAX_FIELD_BYTES:
        return clean
    page = {"text": text, "offset": offset, "next_offset": next_offset,
            "total_bytes": total, "path": path}
    if pages is not None:
        pages.append(page)
    return page


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, name: str) -> bool:
    return name in {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _sanitize_tree(value: Any, *, offset: int = 0,
                   pages: list[dict[str, Any]] | None = None, path: str = "$") -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize_tree(v, offset=offset, pages=pages,
                                       path=f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_tree(v, offset=offset, pages=pages,
                               path=f"{path}[{index}]") for index, v in enumerate(value)]
    return _sanitize_text(value, offset=offset, pages=pages, path=path)


def _group(rows: list[sqlite3.Row], key: str) -> dict[str, int]:
    return {str(row[key] if row[key] not in (None, "") else "-"): int(row["count"]) for row in rows}


def flow_overview(window: str = "24h") -> dict[str, Any]:
    """Return unit-level counts for one of the three bounded windows."""
    if window not in _WINDOWS:
        raise ValueError("window must be one of 24h, 7d, 30d")
    since = _iso(_utc_now() - _WINDOWS[window])
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        base = """FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id
                  WHERE i.created_at >= ?"""
        by_state = conn.execute(f"SELECT u.state AS k, COUNT(*) AS count {base} GROUP BY u.state", (since,)).fetchall()
        by_lane = conn.execute(f"SELECT i.lane AS k, COUNT(*) AS count {base} GROUP BY i.lane", (since,)).fetchall()
        by_source = conn.execute(f"SELECT i.source AS k, COUNT(*) AS count {base} GROUP BY i.source", (since,)).fetchall()
        by_reason = conn.execute(f"SELECT COALESCE(u.reason,'-') AS k, COUNT(*) AS count {base} GROUP BY COALESCE(u.reason,'-')", (since,)).fetchall()
        stage_row = conn.execute(f"""SELECT
            COUNT(*) AS inbox,
            COUNT(*) AS organized,
            SUM(CASE WHEN json_type(u.decision,'$.card0') IS NOT NULL THEN 1 ELSE 0 END) AS card_0,
            SUM(CASE WHEN json_type(u.decision,'$.card1') IS NOT NULL THEN 1 ELSE 0 END) AS card_1,
            SUM(CASE WHEN json_type(u.decision,'$.card2') IS NOT NULL THEN 1 ELSE 0 END) AS card_2,
            SUM(CASE WHEN json_array_length(json_extract(u.decision,'$.rules')) > 0 THEN 1 ELSE 0 END) AS destination_rules,
            SUM(CASE WHEN json_type(u.decision,'$.card3') IS NOT NULL THEN 1 ELSE 0 END) AS card_3,
            SUM(CASE WHEN u.state IN ('writing','written') THEN 1 ELSE 0 END) AS write
            {base}""", (since,)).fetchone()
        total = int(stage_row["inbox"] or 0)
        state_counts = _group(by_state, "k")
        outcomes = {
            "written": state_counts.get("written", 0),
            "reinforced": state_counts.get("reinforced", 0),
            "merged": state_counts.get("merged", 0),
            "rejected": state_counts.get("rejected", 0),
            "deferred": state_counts.get("deferred", 0),
            "failed": state_counts.get("failed_poison", 0),
            "expired": state_counts.get("expired_unprocessed", 0),
        }
        coverage: dict[str, dict[str, int]] = {}
        if _table_exists(conn, "extraction_attempts"):
            for attempt in conn.execute(
                """SELECT source,COUNT(*) attempts,
                          SUM(CASE WHEN units_emitted=0 THEN 1 ELSE 0 END) zero_unit_attempts,
                          SUM(units_emitted) units_emitted,SUM(units_invalid) units_invalid
                   FROM extraction_attempts WHERE started_at>=? GROUP BY source""", (since,)
            ):
                coverage[str(attempt[0])] = {
                    "attempts": int(attempt[1]), "zero_unit_attempts": int(attempt[2] or 0),
                    "units_emitted": int(attempt[3] or 0), "units_invalid": int(attempt[4] or 0),
                }
        link_status: dict[str, int] = {}
        if _table_exists(conn, "link_status"):
            link_status = {str(row[0]): int(row[1]) for row in conn.execute(
                "SELECT status,COUNT(*) FROM link_status GROUP BY status")}
        same_origin = 0
        if _column_exists(conn, "ingestion_inbox", "dedup_key"):
            same_origin = int(conn.execute(
                """SELECT COUNT(*) FROM (
                     SELECT i.dedup_key FROM ingestion_inbox i JOIN ingestion_units u ON u.candidate_id=i.id
                     WHERE i.created_at>=? AND i.dedup_key IS NOT NULL
                     GROUP BY i.dedup_key HAVING COUNT(DISTINCT
                       CASE WHEN json_extract(u.destination,'$.store')='COMMON_DDD' THEN 'common' ELSE 'injected' END)>1
                   )""", (since,)).fetchone()[0])
    denominator = outcomes["written"] + outcomes["reinforced"] + outcomes["rejected"]
    stages = {key: int(stage_row[key] or 0) for key in (
        "inbox", "organized", "card_0", "card_1", "card_2", "destination_rules", "card_3", "write"
    )}
    return {
        "window": window,
        "since": since,
        "total_units": total,
        "denominator_units": denominator,
        "written_rate": outcomes["written"] / denominator if denominator else 0.0,
        "reinforced_rate": outcomes["reinforced"] / denominator if denominator else 0.0,
        "rejected_rate": outcomes["rejected"] / denominator if denominator else 0.0,
        "outcomes": outcomes,
        "extraction_coverage": coverage,
        "link_status": link_status,
        "same_origin_reroute_count": same_origin,
        "stages": stages,
        "lanes": _group(by_lane, "k"),
        "sources": _group(by_source, "k"),
        "states": state_counts,
        "reasons": _group(by_reason, "k"),
    }


def list_items(filters: dict[str, Any] | None = None, page: int = 1) -> dict[str, Any]:
    """List recent candidates with parameterized filters and a page-size cap of 50."""
    filters = dict(filters or {})
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(filters.pop("page_size", 25) or 25)))
    requested_days = int(filters.pop("time", filters.pop("days", 30)) or 30)
    days = min(30, max(1, requested_days))
    clauses = ["i.created_at >= ?"]
    params: list[Any] = [_iso(_utc_now() - timedelta(days=days))]
    exact = {
        "source": "i.source",
        "store": "json_extract(u.destination, '$.store')",
        "state": "u.state",
        "reason": "u.reason",
        "lane": "i.lane",
    }
    for name, column in exact.items():
        value = filters.get(name)
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    keyword = str(filters.get("keyword") or "").strip()
    if keyword:
        clauses.append("(i.payload LIKE ? ESCAPE '\\' OR u.claim_group LIKE ? ESCAPE '\\')")
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend((f"%{escaped}%", f"%{escaped}%"))
    where = " AND ".join(clauses)
    base = f"""FROM ingestion_inbox i LEFT JOIN ingestion_units u ON u.candidate_id=i.id
               WHERE {where}"""
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        total = int(conn.execute(f"SELECT COUNT(DISTINCT i.id) {base}", params).fetchone()[0])
        rows = conn.execute(
            f"""SELECT i.id, i.created_at, i.source, i.lane, i.state AS candidate_state,
                       i.payload, GROUP_CONCAT(DISTINCT u.state) AS unit_states,
                       GROUP_CONCAT(DISTINCT json_extract(u.destination, '$.store')) AS stores,
                       GROUP_CONCAT(DISTINCT u.reason) AS reasons
                {base} GROUP BY i.id ORDER BY i.created_at DESC, i.id
                LIMIT ? OFFSET ?""",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    items = []
    for row in rows:
        payload = _sanitize_tree(_json(row["payload"], {}))
        preview = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        items.append({
            "id": row["id"], "created_at": row["created_at"], "source": row["source"],
            "lane": row["lane"], "state": row["candidate_state"],
            "unit_states": (row["unit_states"] or "").split(",") if row["unit_states"] else [],
            "stores": (row["stores"] or "").split(",") if row["stores"] else [],
            "reasons": (row["reasons"] or "").split(",") if row["reasons"] else [],
            "preview": _clip_utf8(preview, 320),
        })
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "has_next": page * page_size < total, "window_days": days}


def _workspace_link(value: Any, workspace_root: Path) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    target = path if path.is_absolute() else workspace_root / path
    try:
        resolved = target.resolve(strict=False)
        root = workspace_root.resolve(strict=False)
        return str(resolved) if resolved.is_relative_to(root) else None
    except (OSError, RuntimeError):
        return None


def item_detail(item_id: str, workspace_root: Path | None = None,
                offset: int = 0) -> dict[str, Any] | None:
    """Return one secret-redacted audit page; oversized fields expose a next offset."""
    offset = max(0, int(offset))
    workspace_root = (workspace_root or get_swarmws()).resolve(strict=False)
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        candidate = conn.execute("SELECT * FROM ingestion_inbox WHERE id = ?", (item_id,)).fetchone()
        if candidate is None:
            return None
        units = conn.execute("SELECT * FROM ingestion_units WHERE candidate_id = ? ORDER BY unit_id", (item_id,)).fetchall()
        writes = conn.execute("SELECT * FROM ingestion_writes WHERE unit_id IN (SELECT unit_id FROM ingestion_units WHERE candidate_id = ?) ORDER BY created_at", (item_id,)).fetchall()
        relations = conn.execute("""SELECT * FROM ingestion_relations WHERE subject_anchor LIKE ? OR object_anchor LIKE ? ORDER BY created""", (f"%cand:{item_id}%", f"%cand:{item_id}%")).fetchall()
    pages: list[dict[str, Any]] = []
    cand = dict(candidate)
    for key in ("payload", "envelope", "project_snapshot", "project_candidates", "versions"):
        cand[key] = _sanitize_tree(
            _json(cand.get(key), {} if key != "project_candidates" else []),
            offset=offset, pages=pages, path=f"candidate.{key}",
        )
    out_units = []
    for index, row in enumerate(units):
        value = dict(row)
        for key in ("claim_group", "decision", "destination"):
            value[key] = _sanitize_tree(_json(value.get(key), {}), offset=offset,
                                        pages=pages, path=f"units[{index}].{key}")
        value["reason"] = _sanitize_text(value.get("reason") or "", offset=offset,
                                          pages=pages, path=f"units[{index}].reason")
        destination = _json(row["destination"], {})
        write = next((item for item in writes if item["unit_id"] == row["unit_id"]), None)
        target_path = write["target_path"] if write is not None else destination.get("relative_path")
        section = destination.get("section") or ""
        source_v2 = cand.get("source_v2") or cand.get("source")
        created = str(cand.get("created_at") or "")
        recent_s1 = source_v2 == "S1" and created >= _iso(_utc_now() - timedelta(hours=24))
        if target_path and row["state"] == "written" and recent_s1:
            value["redaction_anchor"] = f"{target_path}#{section}#cand:{row['unit_id']}"
            value["can_redact"] = True
        else:
            value["can_redact"] = False
        out_units.append(value)
    out_writes = []
    for index, row in enumerate(writes):
        value = dict(row)
        value.pop("intended_bytes", None)
        for key in ("anchors_present", "anchors_absent", "relations", "baseline_patch", "tree_manifest"):
            value[key] = _sanitize_tree(
                _json(value.get(key), [] if key in ("anchors_present", "anchors_absent", "relations") else {}),
                offset=offset, pages=pages, path=f"writes[{index}].{key}",
            )
        value["open_file"] = _workspace_link(value.get("target_path"), workspace_root)
        value["open_archive"] = _workspace_link(value.get("archive_path"), workspace_root)
        out_writes.append(value)
    has_more = any(page["next_offset"] is not None for page in pages)
    result = {"candidate": cand, "envelope": cand["envelope"], "units": out_units,
              "writes": out_writes, "relations": [dict(row) for row in relations],
              "detail_offset": offset, "has_more": has_more,
              "next_offset": offset + _MAX_FIELD_BYTES if has_more else None,
              "paged_fields": [page["path"] for page in pages]}
    with _ACCESS_LOCK:
        _ACCESS_LOG.append((_iso(_utc_now()), item_id, "dashboard"))
    return result


def drain_access_log(conn: sqlite3.Connection) -> int:
    """Persist queued detail reads on the worker's writable connection."""
    with _ACCESS_LOCK:
        rows = list(_ACCESS_LOG)
        _ACCESS_LOG.clear()
    if not rows:
        return 0
    try:
        conn.executemany("INSERT INTO ingestion_access_log(at,item_id,actor) VALUES(?,?,?)", rows)
    except Exception:
        with _ACCESS_LOCK:
            for row in rows[-_ACCESS_LOG.maxlen:]:
                _ACCESS_LOG.append(row)
        raise
    return len(rows)


def daily_stats(range: int = 30) -> dict[str, Any]:
    """Return aggregate-only historical statistics, bounded to 365 days."""
    days = min(365, max(1, int(range or 30)))
    since = (_utc_now().date() - timedelta(days=days - 1)).isoformat()
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""SELECT * FROM ingestion_daily_stats WHERE utc_day >= ?
                               ORDER BY utc_day, source, lane, store, state, reason""", (since,)).fetchall()
    return {"days": days, "rows": [dict(row) for row in rows]}


def bootstrap_progress(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    """Return terminal/total bootstrap candidate counts for one project."""
    project = str(project or "").strip()
    if not project:
        raise ValueError("project is required")
    rows = conn.execute(
        """SELECT i.source_v2,i.source_ref,i.state
           FROM ingestion_inbox i
           WHERE (i.project_hint=? OR EXISTS (
             SELECT 1 FROM ingestion_units u
             WHERE u.candidate_id=i.id AND u.project=?
           )) AND (i.source_v2='S7' OR (
             i.source_v2='S4' AND i.source_ref LIKE 'backfill:%'
           ))""",
        (project, project),
    ).fetchall()
    counts = {
        "s7": {"terminal": 0, "total": 0},
        "s4_backfill": {"terminal": 0, "total": 0},
    }
    for row in rows:
        key = "s7" if row["source_v2"] == "S7" else "s4_backfill"
        counts[key]["total"] += 1
        if row["state"] == "done":
            counts[key]["terminal"] += 1
    terminal = counts["s7"]["terminal"] + counts["s4_backfill"]["terminal"]
    total = counts["s7"]["total"] + counts["s4_backfill"]["total"]
    return {
        "project": project,
        "terminal": terminal,
        "total": total,
        "pending": max(0, total - terminal),
        **counts,
    }


def read_bootstrap_progress(project: str) -> dict[str, Any]:
    """Open the current instance read-only and return C10 progress."""
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        return bootstrap_progress(conn, project)


# the first review release that runs for every session with a user message and records
# every correction (design 2026-10-30-cdf-agent-decides D3/D12); earlier reviews ran above
# a threshold and corrections came from a keyword detector, so they cover nothing
EVERY_SESSION_REVIEW_RELEASE = 17


def _review_release(version: str) -> int | None:
    """``cdf-review-r17-v1`` → 17, ``cdf-review-r16.15-v7`` → 16; None if not a review."""
    import re
    m = re.match(r"cdf-review-r(\d+)", version or "")
    return int(m.group(1)) if m else None


def corrections(conn: sqlite3.Connection, since: datetime | None = None) -> list[dict[str, Any]]:
    """Every correction unit the after-session review (S7) recorded, oldest first:
    ``{at: datetime, session_id, skill, repeats}``. The review is the agent that had the
    whole session; nothing here judges text (design 2026-10-30-cdf-agent-decides D12).
    ``repeats`` is true only when the review's declaration named one earlier correction
    (pipeline_v2 records it only then), so the repeat rate rests on that alone (I9). A
    unit counts whatever was then done with it: a correction said again (``same``) is
    still a time the user corrected the agent."""
    if not _table_exists(conn, "ingestion_inbox"):
        return []
    # created_at is an ISO UTC time; a day earlier than *since* bounds the scan in SQL
    # whatever the stored offset notation, the exact cut is made below
    floor = (since - timedelta(days=1)).date().isoformat() if since else ""
    rows = conn.execute(
        """SELECT i.created_at, i.source_ref, json_extract(i.payload,'$.skill'),
                  json_extract(u.decision,'$.version_vector.repeats')
             FROM ingestion_inbox i LEFT JOIN ingestion_units u ON u.candidate_id=i.id
            WHERE i.source_v2='S7' AND i.unit_kind='cognition' AND i.created_at >= ?
              AND json_extract(i.payload,'$.type')='correction'
            ORDER BY i.created_at""", (floor,)).fetchall()
    out = []
    for created, session_id, skill, repeats in rows:
        try:
            at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if since is not None and at < since:
            continue
        out.append({"at": at, "session_id": str(session_id or ""),
                    "skill": str(skill) if skill else None, "repeats": bool(repeats)})
    return out


def read_corrections(since: datetime | None = None) -> tuple[list[dict[str, Any]], datetime | None]:
    """``(corrections, covered_from)``: ``corrections(conn, since)`` of this instance's
    store and when the first review of the every-session release
    (``EVERY_SESSION_REVIEW_RELEASE``) was recorded (an attempt is recorded when the review
    is done), from when the list covers (``None``: no store, or no such review yet —
    nothing is known, not zero). O3 is decided per window, so the first review's own day
    may count as not covered."""
    try:
        with closing(connect_ro()) as conn:
            found = corrections(conn, since)
            first = None
            if _table_exists(conn, "extraction_attempts"):
                for version, started in conn.execute(
                        "SELECT extractor_version, MIN(started_at) FROM extraction_attempts "
                        "WHERE source='S7' GROUP BY extractor_version"):
                    release = _review_release(str(version))
                    if release is not None and release >= EVERY_SESSION_REVIEW_RELEASE and started:
                        first = min(first, str(started)) if first else str(started)
    except (OSError, sqlite3.Error):
        return [], None
    if not first:
        return found, None
    start = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
    return found, start if start.tzinfo else start.replace(tzinfo=timezone.utc)


REVIEW_TYPES = ("code", "files", "conversation")


def review_health(conn: sqlite3.Connection, since: str) -> dict[str, Any]:
    """After-session review metrics (2026-10-29-cdf-session-review.md §12), per session type.

    - reviews / units / invalid: S7 attempts since *since*. What a review missed is
      measured by the monthly error review (brain-driven v5), not a second extraction.
    - agent_self: S7 units written to EVOLUTION or MEMORY, and how many of them the
      user withdrew (a finished user_redact of an anchor the unit's write produced).
    """
    by_type = {kind: {"reviews": 0, "units": 0, "invalid": 0} for kind in REVIEW_TYPES}
    if not (_column_exists(conn, "extraction_attempts", "source")
            and _column_exists(conn, "ingestion_inbox", "source_v2")):
        # a pre-v2 database (only old fixtures): nothing to report
        return {"by_type": by_type, "agent_self_written": 0, "agent_self_withdrawn": 0,
                "agent_self_withdrawn_rate": None}
    for row in conn.execute("""SELECT evidence_range, units_emitted, units_invalid
                               FROM extraction_attempts WHERE source='S7' AND started_at>=?""",
                            (since,)):
        rng = _json(row[0], {})
        kind = rng.get("session_type") if rng.get("session_type") in by_type else "conversation"
        entry = by_type[kind]
        entry["reviews"] += 1
        entry["units"] += int(row[1] or 0)
        entry["invalid"] += int(row[2] or 0)
    written = conn.execute(
        """SELECT u.unit_id, w.anchors_present FROM ingestion_units u
           JOIN ingestion_inbox i ON i.id=u.candidate_id
           LEFT JOIN ingestion_writes w ON w.unit_id=u.unit_id AND w.op_kind='ingestion_unit'
           WHERE i.source_v2='S7' AND u.state='written' AND i.created_at>=?
             AND (u.destination LIKE '%EVOLUTION%' OR u.destination LIKE '%MEMORY%')""",
        (since,)).fetchall()
    # a write records the unit's identity (``cand:<id>``); a redaction records the whole
    # anchor (``<path>#<section>#cand:<id>``): compare by the identity, the last part
    redacted: set[str] = set()
    for (relations,) in conn.execute(
            "SELECT relations FROM ingestion_writes WHERE op_kind='user_redact' AND step='done'"):
        for item in _json(relations, []):
            if isinstance(item, dict) and item.get("redact_anchor"):
                redacted.add(_identity(str(item["redact_anchor"])))
    units: dict[str, set[str]] = {}
    for unit_id, anchors in written:
        units.setdefault(str(unit_id), set()).update(str(a) for a in _json(anchors, []))
    withdrawn = sum(1 for anchors in units.values() if {_identity(a) for a in anchors} & redacted)
    return {"by_type": by_type, "agent_self_written": len(units),
            "agent_self_withdrawn": withdrawn,
            "agent_self_withdrawn_rate": round(withdrawn / len(units), 3) if units else None}


def _identity(anchor: str) -> str:
    """The unit identity an anchor ends with (``a#b#cand:x`` -> ``cand:x``; a bare identity
    is returned as it is)."""
    return anchor.rsplit("#", 1)[-1]


# what each CDF write action did to a main file (design 2026-10-30-brain-driven §2.4)
_CHANGE_KIND = {"add": "add", "add_exception": "add", "replace": "override", "override": "override",
                "refine": "override"}


def brain_changes(conn: sqlite3.Connection, project: str | None = None,
                  since: str = "") -> list[dict[str, Any]]:
    """Every change made to the main cognition files since *since* (ISO time), oldest first:
    ``add`` / ``override`` / ``withdraw`` by CDF or the user, ``user_edit`` (a governed edit
    by the user), and ``conflict`` (a unit the judge said contradicts an entry, written
    next to it, nothing overridden). Each row: kind, at, path, head, source, reason, and
    ``text`` -- the removed original for an override or a withdrawal. *project*: only that
    project's DDD (``Projects/<project>/``). Read-only; records live outside the main files."""
    prefix = f"Projects/{project}/" if project else ""
    def mine(path: str) -> bool:
        return not prefix or path.startswith(prefix)
    out: list[dict[str, Any]] = []
    rows = conn.execute(
        """SELECT w.op_kind, w.target_path, w.anchors_absent, w.relations, w.created_at,
                  u.action, u.reason, i.source, i.payload
           FROM ingestion_writes w
           LEFT JOIN ingestion_units u ON u.unit_id = w.unit_id
           LEFT JOIN ingestion_inbox i ON i.id = u.candidate_id
           WHERE w.step = 'done' AND w.created_at >= ? ORDER BY w.created_at""", (since,)).fetchall()
    for op_kind, path, absent, relations, at, action, reason, source, payload in rows:
        path = str(path or "")
        if not mine(path):
            continue
        removed = [str(x) for x in _json(absent, []) if isinstance(x, str)]
        if op_kind == "user_redact":
            item = next((r for r in _json(relations, []) if isinstance(r, dict)), {})
            text = removed[0] if removed else ""
            status = str(item.get("status") or "withdrawn")
            out.append({"kind": "override" if status == "overridden" else "withdraw",
                        "at": at, "path": path,
                        "head": text.strip().splitlines()[0] if text.strip() else "",
                        "source": str(item.get("actor") or "user"),
                        "status": status,
                        "reason": str(item.get("reason") or ""), "text": text})
            continue
        if op_kind == "user_edit":
            out.append({"kind": "user_edit", "at": at, "path": path, "head": "", "source": "user",
                        "reason": "", "text": ""})
            continue
        if op_kind != "ingestion_unit":
            continue
        unit = _json(payload, {})
        kind = _CHANGE_KIND.get(str(action or ""), "add")
        if kind == "add" and reason == "contradicts":
            kind = "conflict"
        out.append({"kind": kind, "at": at, "path": path, "head": str(unit.get("title") or ""),
                    "source": str(source or ""), "reason": str(reason or ""),
                    "text": "\n\n".join(removed) if kind == "override" else ""})
    return out


def brain_change_summary(changes: list[dict[str, Any]]) -> dict[str, int]:
    """How many of each kind (``brain_changes`` rows)."""
    counts = {"add": 0, "override": 0, "withdraw": 0, "conflict": 0, "user_edit": 0}
    for row in changes:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return counts


def health() -> dict[str, Any]:
    """Return lane backlog, anomaly events, cleanup time, and bounded file sizes."""
    now = _utc_now()
    with closing(connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        oldest_expr = (
            "MIN(CASE WHEN u.state='deferred' AND u.defer_reason IN "
            "('unknown_domain','missing_section') THEN NULL ELSE i.created_at END)"
            if _column_exists(conn, "ingestion_units", "defer_reason")
            else "MIN(i.created_at)"
        )
        backlog_rows = conn.execute(f"""SELECT i.lane, COUNT(*) count,
            {oldest_expr} oldest
            FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id
            WHERE u.state IN ('queued','judged','writing','deferred') GROUP BY i.lane""").fetchall()
        events = conn.execute("SELECT id,at,kind,detail FROM ingestion_events WHERE resolved=0 ORDER BY at DESC LIMIT 100").fetchall()

        cleanup = conn.execute("SELECT last_completed_at FROM ingestion_lanes WHERE lane='maintenance'").fetchone()
        inbox_count = int(conn.execute("SELECT COUNT(*) FROM ingestion_inbox").fetchone()[0])
        llm_rows = conn.execute(
            """SELECT utc_day,source,calls,input_tokens,output_tokens
               FROM ingestion_llm_usage WHERE utc_day>=?
               ORDER BY utc_day,source""",
            ((now.date() - timedelta(days=29)).isoformat(),),
        ).fetchall()
        review = review_health(conn, (now - timedelta(days=30)).isoformat())
    lanes: dict[str, Any] = {}
    for row in backlog_rows:
        try:
            oldest = datetime.fromisoformat(str(row["oldest"]).replace("Z", "+00:00"))
            wait = max(0, int((now - oldest).total_seconds()))
        except (TypeError, ValueError):
            wait = None
        lanes[row["lane"]] = {"backlog": int(row["count"]), "oldest_wait_seconds": wait}
    root = get_swarmws() / ".context"
    def size(name: str) -> int:
        try:
            return (root / name).stat().st_size
        except OSError:
            return 0
    event_values = []
    for row in events:
        event_values.append({"id": row["id"], "at": row["at"], "kind": row["kind"],
                             "detail": _sanitize_tree(_json(row["detail"], {}))})
    db_path = ingestion_db_path()
    try:
        db_bytes = db_path.stat().st_size
    except OSError:
        db_bytes = 0
    return {"lanes": lanes, "events": event_values,
            "breakers": [event for event in event_values if event["kind"] == "breaker"],
            "last_cleanup_at": cleanup[0] if cleanup else None,
            "inbox_count": inbox_count, "inbox_bytes": db_bytes,
            "memory_bytes": size("MEMORY.md"), "evolution_bytes": size("EVOLUTION.md"),
            "llm_by_source": [dict(row) for row in llm_rows],
            "review": review}
