"""Cognition flow view queries (core/ingestion/flow_view.py) against a seeded ingestion.db.

Each test answers one question the view puts in front of a person:
- does every stored outcome land in the step that produced it (reason_group);
- do the totals, the steps, where things landed and what moved add up;
- are the anomalies raised only when their condition holds;
- does the unit list filter by outcome / place / landed row / keyword, show whole text,
  the declared relation's entry and redact secrets;
- is the database left untouched, and do the routes validate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.ingestion import flow_view, queries
from routers import ingestion

_SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _seed(conn: sqlite3.Connection, n: int, source: str, state: str, reason: str | None,
          claim: dict, destination: dict | None, *, age: timedelta = timedelta(),
          vector: dict | None = None, source_ref: str = "session-1", defer: str | None = None) -> None:
    cid = f"c{n}"
    conn.execute("""INSERT INTO ingestion_inbox
        (id,idem_key,source,source_ref,lane,payload,payload_schema,content_hash,envelope,
         project_snapshot,project_candidates,state,versions,created_at,source_v2)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, f"idem{n}", source, source_ref, "session", json.dumps(claim), "cognition:v2",
         f"h{n}", "{}", "{}", "[]", "done", "{}", iso(-age), source))
    decision = {"version_vector": {"basis": "inferred", **(vector or {})}}
    conn.execute("""INSERT INTO ingestion_units
        (unit_id,candidate_id,claim_group,decision,destination,reason,state,updated_at,defer_reason)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (f"{cid}.unit", cid, json.dumps(claim), json.dumps(decision),
         json.dumps(destination) if destination else None, reason, state, iso(), defer))


IMPROVEMENT = {"store": "DDD", "project": "SwarmAI",
               "relative_path": "Projects/SwarmAI/2-understanding/IMPROVEMENT.md",
               "section": "What Worked"}


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ingestion.db"
    from core.ingestion.store import _create
    _create(path, 1000).close()                 # the current schema, in the temp dir
    conn = sqlite3.connect(path)
    lesson = {"kind": "cognition", "type": "process", "title": "Check where units landed",
              "what": "Search the stores for the title. " * 40, "scope": "project:SwarmAI"}
    _seed(conn, 1, "S7", "written", "new", lesson, IMPROVEMENT)
    _seed(conn, 2, "S7", "written", "adds",
          {**lesson, "title": "Adds one", "adds": "20dbe2ef | - [model] **R22 quota defers units** body"},
          {**IMPROVEMENT, "rerouted_from": "EVOLUTION"})
    _seed(conn, 3, "S2", "reinforced", "reinforced",
          {**lesson, "title": "Same one", "same": "aa11 | Compaction thresholds"}, IMPROVEMENT)
    _seed(conn, 4, "S2", "rejected", "already_exists", {**lesson, "title": "Known one"}, IMPROVEMENT)
    _seed(conn, 5, "S6", "rejected", "project_outside_brain",
          {"kind": "gap", "gap_kind": "debt", "title": "Gap", "what": "x",
           "scope": "project:CNTech IVT Tenets"}, None, source_ref=f"hive:run?token={_SECRET}")
    _seed(conn, 6, "S2", "rejected", "speculative", {**lesson, "title": "Old judge"}, IMPROVEMENT)
    _seed(conn, 7, "S2", "written", "new",
          {"kind": "event", "event_kind": "event", "title": "Landed", "what": f"key {_SECRET}"},
          {"store": "TIMELINE", "relative_path": ".context/TIMELINE.md"})
    _seed(conn, 8, "S2", "written", "new", {**lesson, "title": "Lowercase"},
          {**IMPROVEMENT, "project": "swarmai", "relative_path": "Projects/swarmai/2-understanding/TECH.md"})
    _seed(conn, 9, "S2", "written", "new", {**lesson, "title": "Too old"}, IMPROVEMENT,
          age=timedelta(days=3))
    # a project unit the same as a Memory entry: it strengthened that entry, not IMPROVEMENT
    _seed(conn, 10, "S7", "reinforced", "reinforced", {**lesson, "title": "Same as memory"}, IMPROVEMENT,
          vector={"same_path": ".context/MEMORY.md", "same_anchor": ".context/MEMORY.md#Pitfalls#x"})
    _seed(conn, 11, "S7", "rejected", "withdrawn", {**lesson, "title": "You withdrew it"}, IMPROVEMENT)
    _seed(conn, 12, "S7", "rejected", "older_than_existing", {**lesson, "title": "Older"}, IMPROVEMENT)
    # moved out of an every-turn file, then skipped as known: moved, but not written there
    _seed(conn, 13, "S7", "rejected", "already_exists", {**lesson, "title": "Moved then known"},
          {**IMPROVEMENT, "rerouted_from": "MEMORY"})
    _seed(conn, 14, "S2", "written", "contradicts", {**lesson, "title": "Legacy write"}, IMPROVEMENT)
    _seed(conn, 15, "S7", "rejected", "unknown_project", {**lesson, "title": "Legacy no project"}, None)
    # parked on purpose (waits for its section) for two days: not an anomaly
    _seed(conn, 16, "S7", "deferred", "missing_section", {**lesson, "title": "Parked"}, IMPROVEMENT,
          age=timedelta(days=2), defer="missing_section")
    _seed(conn, 17, "S7", "rejected", "already_exists", {**lesson, "title": "Has a key",
          "what": f"the key is {_SECRET}"}, IMPROVEMENT)
    conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)",
                 (iso(), "relation_skipped", "{}"))
    conn.commit()
    conn.close()

    def ro() -> sqlite3.Connection:
        value = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        value.execute("PRAGMA query_only=ON")
        return value

    monkeypatch.setattr(queries, "connect_ro", ro)
    monkeypatch.setattr(queries, "ingestion_db_path", lambda: path)
    return path


@pytest.mark.parametrize(("state", "reason", "group"), [
    ("written", "new", "new"), ("written", "adds", "adds"), ("written", "contradicts", "new"),
    ("written", None, "new"), ("reinforced", "reinforced", "reinforced"),
    ("rejected", "already_exists", "known"), ("merged", None, "known"),
    ("rejected", "withdrawn", "withdrawn"), ("rejected", "older_than_existing", "older"),
    ("rejected", "noisy", "basic"), ("rejected", "credentialed_url", "basic"),
    ("rejected", "project_outside_brain", "place"), ("rejected", "unknown_project", "no_project"),
    ("rejected", "gap_without_project", "no_project"), ("rejected", "discarded_backlog", "backlog"),
    ("rejected", "speculative", "retired"), ("rejected", "something_new", "other"),
    ("deferred", "missing_section", "waiting"), ("failed_poison", None, "failed"),
    ("expired_unprocessed", "expired_unprocessed", "expired"),
])
def test_every_outcome_has_one_step(state: str, reason: str | None, group: str):
    assert flow_view.reason_group(state, reason) == group


def test_declared_target_reads_like_the_entry():
    assert flow_view.declared_target("20dbe2ef | - [model] **R22 quota** rest") == "R22 quota rest"
    assert flow_view.declared_target("Plain title") == "Plain title"
    assert flow_view.declared_target(None) is None


def test_overview_adds_up_and_raises_only_real_anomalies(db: Path):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    view = flow_view.landed_overview("24h")
    assert view["total"] == 15                                  # c9 and c16 are older
    assert view["totals"] == {"new": 4, "adds": 1, "reinforced": 2, "not_kept": 8, "waiting": 0}
    assert view["steps"] == {"captured": 15, "basic_dropped": 0, "place_dropped": 1, "moved": 2,
                             "reinforced": 2, "known": 3, "withdrawn": 1, "older": 1, "written": 5}
    assert view["sources"] == {"S7": 8, "S2": 6, "S6": 1}
    # moved and then written; the one moved and then skipped as known is not "went there"
    assert view["moved"] == [{"from": "EVOLUTION", "to": "DDD", "count": 1}]
    by_path = {r["path"]: r for r in view["landed"]}
    improvement = by_path["Projects/SwarmAI/2-understanding/IMPROVEMENT.md"]
    assert (improvement["written"], improvement["reinforced"]) == (3, 1)
    memory = by_path[".context/MEMORY.md"]                      # where the reinforcement landed
    assert (memory["store"], memory["project"], memory["reinforced"]) == ("MEMORY", None, 1)
    codes = {a["code"]: a for a in view["attention"]}
    assert set(codes) == {"links_skipped", "spelling", "no_cleanup"}   # parked is not late
    assert codes["links_skipped"]["count"] == 1
    assert codes["spelling"]["example"] == "SwarmAI, swarmai"
    assert codes["no_cleanup"]["since"] is None                 # maintenance never completed
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_recent_cleanup_is_not_an_anomaly(db: Path):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO ingestion_lanes(lane,last_completed_at) VALUES('maintenance',?)", (iso(),))
    conn.commit()
    conn.close()
    assert "no_cleanup" not in {a["code"] for a in flow_view.landed_overview("24h")["attention"]}


def test_units_filter_and_read_whole(db: Path):
    every = flow_view.list_units({"window": "24h", "page_size": 50})
    assert every["total"] == 15
    by_title = {item["title"]: item for item in every["items"]}
    adds = by_title["Adds one"]
    assert (adds["group"], adds["relation"], adds["target"]) == ("adds", "adds", "R22 quota defers units body")
    assert adds["moved_from"] == "EVOLUTION"
    assert by_title["Same one"]["relation"] == "same"
    assert by_title["Check where units landed"]["text"] == "Search the stores for the title. " * 40
    assert by_title["Gap"]["type"] == "debt" and by_title["Gap"]["group"] == "place"
    memory = by_title["Same as memory"]
    assert (memory["store"], memory["path"], memory["project"]) == ("MEMORY", ".context/MEMORY.md", None)
    assert by_title["You withdrew it"]["group"] == "withdrawn"
    assert by_title["Legacy write"]["group"] == "new"

    def titles(**filters) -> set[str]:
        return {item["title"] for item in flow_view.list_units({"window": "24h", "page_size": 50, **filters})["items"]}
    assert titles(outcome="new") == {"Check where units landed", "Landed", "Lowercase", "Legacy write"}
    assert titles(outcome="adds") == {"Adds one"}
    assert titles(outcome="reinforced") == {"Same one", "Same as memory"}
    assert titles(outcome="not_kept") == {"Known one", "Gap", "Old judge", "You withdrew it", "Older",
                                          "Moved then known", "Legacy no project", "Has a key"}
    assert titles(place="every_turn") == {"Landed"}
    assert titles(path=".context/MEMORY.md") == {"Same as memory"}
    assert "Same as memory" not in titles(path=IMPROVEMENT["relative_path"])
    assert titles(path_prefix="Projects/swarmai/") == {"Lowercase"}    # case-sensitive
    assert "Lowercase" not in titles(path_prefix="Projects/SwarmAI/")
    assert titles(keyword="R22") == {"Adds one"}
    assert titles(keyword="%") == set()
    assert "Too old" in titles(window="7d")
    page = flow_view.list_units({"window": "24h", "page_size": 999}, 1)
    assert page["page_size"] == 50


def test_no_secret_reaches_the_view_or_a_search(db: Path):
    every = flow_view.list_units({"window": "24h", "page_size": 50})
    blob = json.dumps(every)
    assert _SECRET not in blob                                   # text, source_ref, every field
    assert _SECRET not in json.dumps(flow_view.landed_overview("24h"))
    # a search for the hidden value cannot confirm it exists
    assert flow_view.list_units({"window": "24h", "keyword": _SECRET})["total"] == 0
    assert flow_view.list_units({"window": "24h", "keyword": _SECRET[:8]})["total"] == 0
    assert flow_view.list_units({"window": "24h", "keyword": "the key is"})["total"] == 1


def test_units_rejects_unknown_filters(db: Path):
    with pytest.raises(ValueError):
        flow_view.list_units({"outcome": "everything"})
    with pytest.raises(ValueError):
        flow_view.list_units({"place": "nowhere"})


def test_routes_validate(db: Path):
    app = FastAPI()
    app.include_router(ingestion.router)
    client = TestClient(app)
    landed = client.get("/api/ingestion/landed?window=24h")
    assert landed.status_code == 200
    assert landed.json()["totals"]["new"] == 4
    units = client.get("/api/ingestion/units?outcome=adds")
    assert units.status_code == 200 and units.json()["items"][0]["title"] == "Adds one"
    assert client.get("/api/ingestion/units?outcome=bogus").status_code == 422
    assert client.get("/api/ingestion/units?page_size=51").status_code == 422
    assert client.get("/api/ingestion/units?page=100001").status_code == 422
