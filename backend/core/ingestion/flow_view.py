"""Read-only queries behind the Cognition flow view, written for a person to read.

The view answers, in this order: what did SwarmAI learn, where did it land, how was each
item decided, and is anything wrong (design docs/design/2026-10-30-cdf-flow-view.md).

- ``landed_overview(window)``: the outcome totals, where written and reinforced units
  landed (store / project / file), the counts each decision step let through, dropped or
  moved, and the few anomalies worth a person's look.
- ``list_units(filters, page)``: one row per unit with its whole title and text, where it
  landed, the relation the producing agent declared and the reason code with its group.
- ``reason_group(state, reason)``: the one mapping from a stored outcome to the step that
  produced it; the frontend phrases a group, it never re-derives one.

Same safety as ``queries``: ``mode=ro`` connections, parameterised SQL, every text
secret-redacted and capped, pages of at most 50.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from datetime import timedelta
import json
import re
import sqlite3
from typing import Any

from . import queries
from core.memory_guard import MemoryGuard

from .contracts_v2 import PARKED_DEFER_REASONS
from .queries import _MAX_FIELD_BYTES, _WINDOWS, _clip_utf8, _iso, _json, _table_exists, _utc_now

# the files read in every turn; a project's DDD and the General Brain / Library are recalled
EVERY_TURN_STORES = ("MEMORY", "EVOLUTION", "STEERING", "USER", "TOOLS", "TIMELINE")
PROJECT_STORES = ("DDD",)
GENERAL_STORES = ("COMMON_DDD", "KNOWLEDGE")
PLACES = {"every_turn": EVERY_TURN_STORES, "project": PROJECT_STORES, "general": GENERAL_STORES}

# which decision step produced an outcome (pipeline_v2.run_unit_v2, worker, store migrations)
_BASIC = frozenset({"noisy", "credentialed_url", "malformed_payload"})
# a Hive lesson about a project other than its run's Brain (pipeline_v2: project_outside_brain)
_PLACE = frozenset({"project_outside_brain"})
# a project that did not exist was dropped before the General Brain took these (D7)
_NO_PROJECT = frozenset({"unknown_project", "gap_without_project"})
_KNOWN = frozenset({"already_exists"})
_WITHDRAWN = frozenset({"withdrawn"})          # the same as what the user withdrew
_OLDER = frozenset({"older_than_existing"})    # an existing entry is newer
_BACKLOG = frozenset({"discarded_backlog"})
# reasons only the retired v1 quality reviewer and card 3 produced (deleted 2026-10-30)
_RETIRED = frozenset({"retired_v1", "speculative", "not_atomic", "volatile", "not_grounded",
                      "not_significant", "not_self_contained", "unrenderable", "not_durable",
                      "untyped", "no_judgment_value", "conflict_unresolved",
                      "self_contradictory", "conflicts_stronger", "destination_mismatch"})
_WAITING_STATES = ("queued", "judged", "writing", "deferred")
_NOT_KEPT_STATES = ("rejected", "merged", "expired_unprocessed")
OUTCOMES = ("all", "new", "adds", "reinforced", "not_kept", "waiting")

_OUTCOME_SQL = {
    "all": "1=1",
    "new": "u.state='written' AND COALESCE(u.reason,'')<>'adds'",
    "adds": "u.state='written' AND u.reason='adds'",
    "reinforced": "u.state='reinforced'",
    "not_kept": f"u.state IN ({','.join(repr(s) for s in _NOT_KEPT_STATES)})",
    "waiting": f"u.state IN ({','.join(repr(s) for s in (*_WAITING_STATES, 'failed_poison'))})",
}
_STORE = "json_extract(u.destination,'$.store')"
# where a unit really landed: a reinforcement strengthens the entry it is the same as,
# which may sit in another file than the unit's destination (pipeline_v2._same_entry)
_LANDED_PATH = ("COALESCE(CASE WHEN u.state='reinforced' THEN "
                "json_extract(u.decision,'$.version_vector.same_path') END, "
                "json_extract(u.destination,'$.relative_path'))")
_PROJECT_NAME = re.compile(r"^[\w .\-/]{1,80}$")
_KEYWORD_CANDIDATES = 5000


def reason_group(state: str | None, reason: str | None) -> str:
    """The step behind one stored outcome: ``new`` / ``adds`` / ``reinforced`` (kept),
    ``basic`` / ``place`` / ``known`` / ``withdrawn`` / ``older`` (not kept, live steps),
    ``no_project`` / ``backlog`` / ``retired`` (not kept, earlier versions), ``other``,
    ``waiting`` / ``failed`` / ``expired``. A written unit adds to an entry only when it
    says so (reason ``adds``); every other write, a v1 one too, is new."""
    reason = reason or ""
    if state == "written":
        return "adds" if reason == "adds" else "new"
    if state == "reinforced":
        return "reinforced"
    if state in _WAITING_STATES:
        return "waiting"
    if state == "failed_poison":
        return "failed"
    if state == "expired_unprocessed":
        return "expired"
    if state == "merged":
        return "known"
    for group, codes in (("basic", _BASIC), ("place", _PLACE), ("known", _KNOWN),
                         ("withdrawn", _WITHDRAWN), ("older", _OLDER), ("no_project", _NO_PROJECT),
                         ("backlog", _BACKLOG), ("retired", _RETIRED)):
        if reason in codes:
            return group
    return "other"


_FIRST_LINE_MARKS = re.compile(r"^\s*(?:[-*]\s*)?(?:\[[^\]]*\]\s*)?")


def declared_target(ref: Any) -> str | None:
    """The entry an agent's ``same`` / ``adds`` names, as a reader sees it: the
    ``<id> | <first line>`` declaration without its id, list marker, type tag or bold."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    text = ref.split("|", 1)[1] if "|" in ref else ref
    text = _FIRST_LINE_MARKS.sub("", text.strip().splitlines()[0] if text.strip() else "")
    text = text.replace("**", "").strip()
    return _clip_utf8(text, 240) or None


def _since(window: str) -> str:
    if window not in _WINDOWS:
        raise ValueError("window must be one of 24h, 7d, 30d")
    return _iso(_utc_now() - _WINDOWS[window])


def _clean(value: Any, maximum: int = _MAX_FIELD_BYTES) -> str:
    """*value* secret-redacted (scanned whole, then capped at the detail page size)."""
    if not isinstance(value, str):
        return ""
    return _clip_utf8(MemoryGuard().scan(value).sanitized_content, maximum)


def _scrub(value: Any) -> Any:
    """Every string leaf of a response secret-redacted (an allowlist by construction: a
    field added later is scanned too)."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return _clean(value) if isinstance(value, str) else value


def _landing(path: str | None, store: str | None, project: str | None) -> tuple[str | None, str | None]:
    """The store and project of the file a unit landed in (*path*), when it is not the
    unit's own destination (a reinforcement of an entry elsewhere)."""
    if not path:
        return store, project
    if path.startswith(".context/"):
        stem = Path(path).stem.upper()
        return (stem if stem in EVERY_TURN_STORES else store), None
    if path.startswith("Projects/"):
        parts = path.split("/")
        return "DDD", parts[1] if len(parts) > 1 else project
    return store, project


def _unit_fields(claim: Any) -> dict[str, Any]:
    """Title, text, kind, type and scope of one stored unit (v2 claim groups; a v1 one
    carries only ``text``). Title and text are whole (the list clamps them visually);
    only a field over the detail page size is capped, the detail view pages the rest."""
    claim = claim if isinstance(claim, dict) else {}
    kind = claim.get("kind") or "cognition"
    kind_type = (claim.get("type") or claim.get("gap_kind") or claim.get("event_kind")
                 or claim.get("reference_kind"))
    text = claim.get("what") or claim.get("summary") or claim.get("text")
    scope = claim.get("scope")
    return {
        "kind": str(kind), "type": str(kind_type) if isinstance(kind_type, str) else None,
        "title": _clean(claim.get("title")), "text": _clean(text),
        "scope": _clean(scope, 200) or None,
        "same": declared_target(_clean(claim.get("same"))),
        "adds": declared_target(_clean(claim.get("adds"))),
    }


def landed_overview(window: str = "24h") -> dict[str, Any]:
    """Outcome totals, where kept units landed, step counts and anomalies for *window*."""
    since = _since(window)
    base = "FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id WHERE i.created_at >= ?"
    parked = sorted(PARKED_DEFER_REASONS)
    with closing(queries.connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        outcome_rows = conn.execute(
            f"SELECT u.state AS state, u.reason AS reason, COUNT(*) AS n {base} GROUP BY 1,2",
            (since,)).fetchall()
        source_rows = conn.execute(
            f"SELECT COALESCE(i.source_v2, i.source) AS source, COUNT(*) AS n {base} GROUP BY 1",
            (since,)).fetchall()
        landed_rows = conn.execute(
            f"""SELECT {_STORE} AS store, json_extract(u.destination,'$.project') AS project,
                       json_extract(u.destination,'$.relative_path') AS dest_path,
                       {_LANDED_PATH} AS path, u.state AS state, COUNT(*) AS n
                {base} AND u.state IN ('written','reinforced') GROUP BY 1,2,3,4,5""",
            (since,)).fetchall()
        moved_rows = conn.execute(
            f"""SELECT json_extract(u.destination,'$.rerouted_from') AS src, {_STORE} AS dst,
                       u.state AS state, COUNT(*) AS n
                {base} AND json_extract(u.destination,'$.rerouted_from') IS NOT NULL
                GROUP BY 1,2,3""", (since,)).fetchall()
        # waiting on purpose (a parked deferral waits for its section, no retry) is not late
        waiting_long = int(conn.execute(
            f"""SELECT COUNT(*) FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id
               WHERE u.state IN ('queued','judged','writing','deferred') AND i.created_at < ?
               AND NOT (u.state='deferred' AND COALESCE(u.defer_reason, u.reason, '')
                        IN ({','.join('?' * len(parked))}))""",
            (_iso(_utc_now() - timedelta(hours=24)), *parked)).fetchone()[0])
        links_skipped = breakers = 0
        if _table_exists(conn, "ingestion_events"):
            for kind, count in conn.execute(
                    """SELECT kind, COUNT(*) FROM ingestion_events
                       WHERE resolved=0 AND at >= ? AND kind IN ('relation_skipped','breaker')
                       GROUP BY kind""", (since,)):
                if kind == "relation_skipped":
                    links_skipped = int(count)
                else:
                    breakers = int(count)
        cleanup = conn.execute(
            "SELECT last_completed_at FROM ingestion_lanes WHERE lane='maintenance'").fetchone()

    groups: dict[str, int] = {}
    reasons: dict[str, dict[str, int]] = {}
    for row in outcome_rows:
        group = reason_group(row["state"], row["reason"])
        groups[group] = groups.get(group, 0) + int(row["n"])
        code = str(row["reason"] or row["state"] or "-")
        reasons.setdefault(group, {})
        reasons[group][code] = reasons[group].get(code, 0) + int(row["n"])
    total = sum(groups.values())
    kept_new, kept_adds = groups.get("new", 0), groups.get("adds", 0)
    not_kept = sum(groups.get(g, 0) for g in ("basic", "place", "known", "withdrawn", "older",
                                                "no_project", "backlog", "retired", "other", "expired"))
    waiting = groups.get("waiting", 0) + groups.get("failed", 0)

    landed_map: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
    for row in landed_rows:
        store, project = str(row["store"] or "-"), row["project"]
        if row["path"] != row["dest_path"]:
            store, project = _landing(row["path"], store, project)
        key = (store, project, row["path"])
        entry = landed_map.setdefault(key, {"store": store, "project": project, "path": row["path"],
                                            "written": 0, "reinforced": 0})
        entry["written" if row["state"] == "written" else "reinforced"] += int(row["n"])
    landed = sorted(landed_map.values(), key=lambda r: (-(r["written"] + r["reinforced"]), str(r["path"])))
    moved_all = sum(int(r["n"]) for r in moved_rows)
    # what went into a brain instead of an every-turn file and was written there
    moved: dict[tuple[str, str], int] = {}
    for row in moved_rows:
        if row["state"] == "written":
            key = (str(row["src"]), str(row["dst"]))
            moved[key] = moved.get(key, 0) + int(row["n"])

    projects = {str(r["project"]) for r in landed if r["store"] == "DDD" and r["project"]}
    spellings = sorted({p for p in projects
                        if _PROJECT_NAME.match(p)
                        and any(q != p and q.casefold() == p.casefold() for q in projects)})
    db_bytes = 0
    try:
        db_bytes = queries.ingestion_db_path().stat().st_size
    except OSError:
        pass
    last_cleanup = cleanup[0] if cleanup else None
    attention: list[dict[str, Any]] = []
    if groups.get("failed"):
        attention.append({"code": "failed", "count": groups["failed"]})
    if waiting_long:
        attention.append({"code": "waiting_long", "count": waiting_long})
    if breakers:
        attention.append({"code": "breaker", "count": breakers})
    stale_after = _iso(_utc_now() - timedelta(hours=48))
    if last_cleanup is None or str(last_cleanup) < stale_after:
        attention.append({"code": "no_cleanup", "bytes": db_bytes, "since": last_cleanup})
    if links_skipped:
        attention.append({"code": "links_skipped", "count": links_skipped})
    if spellings:
        attention.append({"code": "spelling", "count": len(spellings), "example": ", ".join(spellings)})

    return _scrub({
        "window": window, "since": since, "total": total,
        "totals": {"new": kept_new, "adds": kept_adds, "reinforced": groups.get("reinforced", 0),
                   "not_kept": not_kept, "waiting": waiting},
        "groups": groups, "reasons": reasons,
        "sources": {str(r["source"]): int(r["n"]) for r in source_rows},
        "steps": {
            "captured": total,
            "basic_dropped": groups.get("basic", 0),
            "place_dropped": groups.get("place", 0),
            "moved": moved_all,
            "reinforced": groups.get("reinforced", 0),
            "known": groups.get("known", 0),
            "withdrawn": groups.get("withdrawn", 0),
            "older": groups.get("older", 0),
            "written": kept_new + kept_adds,
        },
        "landed": landed,
        "moved": [{"from": src, "to": dst, "count": n} for (src, dst), n in sorted(moved.items())],
        "attention": attention,
        "store_bytes": db_bytes, "last_cleanup_at": last_cleanup,
    })


def list_units(filters: dict[str, Any] | None = None, page: int = 1) -> dict[str, Any]:
    """One readable row per unit, newest first, filtered by outcome, source, place,
    store / project / path / path prefix (a landed row: where the unit really landed),
    keyword and window; pages of at most 50. A keyword matches the redacted text only, so
    a search cannot confirm a secret the view does not show."""
    filters = dict(filters or {})
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(filters.get("page_size") or 25)))
    outcome = filters.get("outcome") or "all"
    if outcome not in _OUTCOME_SQL:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    clauses = ["i.created_at >= ?", _OUTCOME_SQL[outcome]]
    params: list[Any] = [_since(filters.get("window") or "24h")]
    place = filters.get("place")
    if place:
        if place not in PLACES:
            raise ValueError("place must be one of every_turn, project, general")
        stores = PLACES[place]
        clauses.append(f"{_STORE} IN ({','.join('?' * len(stores))})")
        params.extend(stores)
    for name, column in (("source", "COALESCE(i.source_v2, i.source)"), ("store", _STORE),
                         ("project", "json_extract(u.destination,'$.project')"),
                         ("path", _LANDED_PATH)):
        value = filters.get(name)
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    prefix = filters.get("path_prefix")
    if prefix:
        # case-sensitive (LIKE is not): Projects/swarmai/ is not Projects/SwarmAI/
        clauses.append(f"substr({_LANDED_PATH}, 1, length(?)) = ?")
        params.extend((str(prefix), str(prefix)))
    keyword = str(filters.get("keyword") or "").strip()
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("u.claim_group LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    where = " AND ".join(clauses)
    sql_base = f"FROM ingestion_units u JOIN ingestion_inbox i ON i.id=u.candidate_id WHERE {where}"
    select = f"""SELECT u.unit_id, u.candidate_id, u.claim_group, u.destination, u.decision,
                        u.state, u.reason, i.created_at, COALESCE(i.source_v2, i.source) AS source,
                        i.source_ref, {_LANDED_PATH} AS landed_path
                 {sql_base} ORDER BY i.created_at DESC, u.unit_id"""
    search_capped = False
    with closing(queries.connect_ro()) as conn:
        conn.row_factory = sqlite3.Row
        if keyword:
            # the raw text matched in SQL; keep only rows whose REDACTED text still matches
            found = conn.execute(f"{select} LIMIT ?", (*params, _KEYWORD_CANDIDATES + 1)).fetchall()
            search_capped = len(found) > _KEYWORD_CANDIDATES
            needle = keyword.casefold()
            found = [row for row in found[:_KEYWORD_CANDIDATES]
                     if needle in _clean(str(row["claim_group"] or "")).casefold()]
            total = len(found)
            rows = found[(page - 1) * page_size: page * page_size]
        else:
            total = int(conn.execute(f"SELECT COUNT(*) {sql_base}", params).fetchone()[0])
            rows = conn.execute(f"{select} LIMIT ? OFFSET ?",
                                (*params, page_size, (page - 1) * page_size)).fetchall()
    items = []
    for row in rows:
        fields = _unit_fields(_json(row["claim_group"], {}))
        destination = _json(row["destination"], {})
        destination = destination if isinstance(destination, dict) else {}
        vector = (_json(row["decision"], {}) or {}).get("version_vector") or {}
        store, project = destination.get("store"), destination.get("project")
        path, section = destination.get("relative_path"), destination.get("section")
        if row["landed_path"] != path:              # reinforced an entry in another file
            store, project = _landing(row["landed_path"], store, project)
            path, section = row["landed_path"], None
        items.append({
            "unit_id": row["unit_id"], "candidate_id": row["candidate_id"],
            "created_at": row["created_at"], "source": row["source"],
            "source_ref": str(row["source_ref"] or ""),
            **{key: fields[key] for key in ("kind", "type", "title", "text", "scope")},
            "state": row["state"], "reason": row["reason"],
            "group": reason_group(row["state"], row["reason"]),
            "store": store, "project": project, "path": path, "section": section,
            "moved_from": destination.get("rerouted_from"),
            "relation": "same" if fields["same"] else ("adds" if fields["adds"] else None),
            "target": fields["same"] or fields["adds"],
            "basis": vector.get("basis") if isinstance(vector, dict) else None,
        })
    return _scrub({"items": items, "page": page, "page_size": page_size, "total": total,
                   "has_next": page * page_size < total, "search_capped": search_capped})
