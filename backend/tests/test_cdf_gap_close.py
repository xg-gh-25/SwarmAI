"""Closing a CDF gap, declared by the run that fixed it (design 2026-10-30-cdf-gap-lifecycle §6).

Invariants:
1. ``pipeline-v1 events`` lists the run's project's open gaps: not done ones, not
   another project's; past GAP_LIST_TOP, the ones the run's requirement ranks highest.
2. ``reflect.prepare`` refuses a ``closes`` id the run was not shown.
3. A written event of a landed run closes its gaps: the entry leaves the main file, and its
   text, who closed it and status ``closed`` are in the records (brain-driven §2.2);
   ``knowledge find`` no longer returns it.
4. A run that did not land, or an event that was not written, closes nothing.
5. A concurrent user edit is never overwritten; the close is retried and lands later.
6. A unit the same as a closed gap is written again (derived from it), never dropped.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ingestion import api, gaps, governed_edit, pipeline_v2
from core.ingestion.contracts import RetrievalHit, RetrievalResult
from core.ingestion.contracts_v2 import GAP_LIST_TOP, SOURCE_UNIT
from core.run_protocol import reflect

A = "cand-" + "a" * 24
B = "cand-" + "b" * 24
C = "cand-" + "c" * 24


META = "  <!-- ref:0 | last:2026-10-29 | decay:active | source:S3 | taint:external -->\n"


def _gap_line(gap_id: str, title: str, body: str = "The tab does not refresh.", mark: str = "guideline") -> str:
    """One gap entry as the writer renders it: the entry line, then its metadata line."""
    return f"- [{mark}] **{title}** {body} gap kind: follow_up <!-- cand:{gap_id}.unit -->\n" + META


def _improvement(ws: Path, project: str, lines: list[str]) -> Path:
    path = ws / "Projects" / project / "2-understanding" / "IMPROVEMENT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# IMPROVEMENT\n\n## What Worked\n\n" + _gap_line(C, "Not a gap section")
                    + "\n## What to Watch For\n\n" + "".join(lines) + "\n## What Failed\n",
                    encoding="utf-8")
    return path


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    data, workspace = tmp_path / "data", tmp_path / "SwarmWS"
    data.mkdir(); workspace.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    api.connect_rw().close()                    # the real, migrated ingestion.db
    return {"ws": workspace, "db": data / "ingestion.db"}


def _written_event(conn, unit_id: str, closes: list[str], *, state: str = "written",
                   source: str = "S3", project: str = "P", title: str = "Tab refresh fixed") -> None:
    payload = {"kind": "event", "when": "2026-10-30", "title": title,
               "what": "The tab refreshes on focus.", "outcome": "Fixed.",
               "evidence": ["run_event:e1"], "closes": closes}
    conn.execute(
        """INSERT INTO ingestion_inbox (id,idem_key,source,source_ref,lane,payload,payload_schema,
        content_hash,envelope,project_snapshot,created_at,unit_kind,source_v2,extraction_pass)
        VALUES (?,?,?,?,'run',?,'v2','h','{}',?,'2026-10-30T00:00:00+00:00','event',?,'main')""",
        (unit_id + "-c", unit_id, source, "run_fix", json.dumps(payload),
         json.dumps({"project": project}), source))
    conn.execute("""INSERT INTO ingestion_units (unit_id,candidate_id,claim_group,state,owner_epoch,updated_at)
        VALUES (?,?,'{}',?,1,'2026-10-30T00:00:00+00:00')""", (unit_id, unit_id + "-c", state))
    conn.commit()


# 1 ── the list a run is shown

def test_open_gaps_lists_only_this_projects_open_gaps(ws):
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh"), _gap_line(B, "Fixed one", mark="x")])
    _improvement(ws["ws"], "Q", [_gap_line("cand-" + "d" * 24, "Other project")])
    assert gaps.open_gaps("P") == [{"id": A, "title": "Tab refresh", "gap_kind": "follow_up"}]
    assert gaps.open_gaps("Missing") == []


def test_past_the_cap_the_requirement_picks_which_gaps_are_shown(ws):
    ids = [f"cand-{i:024x}" for i in range(GAP_LIST_TOP + 2)]
    lines = [_gap_line(gid, f"Gap {i}", body="Ordinary text.") for i, gid in enumerate(ids)]
    lines[0] = _gap_line(ids[0], "Websocket reconnect", body="The websocket never reconnects.")
    _improvement(ws["ws"], "P", lines)
    shown = [g["id"] for g in gaps.open_gaps("P", "fix the websocket reconnect")]
    assert len(shown) == GAP_LIST_TOP and ids[0] in shown
    assert shown == sorted(shown, key=ids.index)                 # file order kept
    assert ids[0] not in [g["id"] for g in gaps.open_gaps("P")]  # no query: newest
    assert gaps.open_gap_ids("P") == set(ids)                    # what may close: all open


# 2 ── prepare refuses an id the run was not shown

def _report(closes):
    return {"delivered": [{"kind": "event", "when": "2026-10-30", "title": "Fixed",
                           "what": "Fixed the tab.", "outcome": "Fixed.",
                           "evidence": ["run_event:e1"], "closes": closes}],
            "decisions": [], "corrections": [], "practices": [], "gaps": []}


def test_prepare_refuses_a_gap_the_run_was_not_shown():
    with pytest.raises(reflect.ReflectError, match="not open"):
        reflect.prepare(_report([B]), allowed_anchors={"run_event:e1"}, open_gap_ids={A})
    out = reflect.prepare(_report([A]), allowed_anchors={"run_event:e1"}, open_gap_ids={A})
    assert out["sections"]["delivered"][0]["closes"] == [A]


def test_closes_takes_only_gap_ids_and_the_extractor_never_sees_it():
    from core.ingestion.sources.extract_llm import unit_schema
    with pytest.raises(Exception):
        SOURCE_UNIT.validate_python(_report(["../TECH.md"])["delivered"][0])
    items = unit_schema(("event",))["properties"]["event_units"]["items"]["properties"]
    assert "closes" not in items


# 3 ── a landed run's written event closes its gaps

def test_a_written_event_withdraws_its_gap_into_the_records(ws):
    from core import addressing
    from core.ingestion import withdrawn
    path = _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh"), _gap_line(B, "Other")])
    assert any(A in u.text for u in addressing.ddd_units("P").units)
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A])
    assert gaps.close_declared(conn, "u1") == 1
    text = path.read_text(encoding="utf-8")
    assert A not in text and "Tab refresh" not in text                    # gone, no tombstone
    assert _gap_line(B, "Other") in text                                   # untouched
    assert not any(A in u.text for u in addressing.ddd_units("P").units)
    assert {u.identity for u in units_of(path)} == {f"cand:{B}.unit", f"cand:{C}.unit"}
    closed = [r for r in withdrawn.records(conn) if A in r.text]
    assert [r.status for r in closed] == ["closed"]
    assert "Closed by run_fix: Tab refresh fixed" in closed[0].reason
    assert gaps.close_declared(conn) == 0                                  # recorded once
    assert _results(conn) == [("u1", A, "closed")]
    assert gaps.close_gap("P", A, closed_by="run_fix", note="again") == "already_closed"


def units_of(path):
    from core.ingestion.units import parse_units
    return parse_units(path, root=path.parents[3])


def _results(conn):
    rows = conn.execute("SELECT detail FROM ingestion_events WHERE kind='gaps_closed' ORDER BY id")
    return [(d["unit_id"], d["gap"], d["result"]) for d in (json.loads(r[0]) for r in rows)]


def test_a_note_cannot_forge_another_gaps_id(ws):
    path = _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh"), _gap_line(B, "Other")])
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A], title=f"Fixed CAND:{B}.unit and cand:{B}.unit")
    assert gaps.close_declared(conn) == 1
    assert sorted(u.identity for u in units_of(path) if u.section == gaps.SECTION) == \
        [f"cand:{B}.unit"]
    assert [g["id"] for g in gaps.open_gaps("P")] == [B]
    assert gaps.close_gap("P", B, closed_by="run_y", note="fixed") == "closed"


def test_one_failing_gap_is_recorded_and_never_stops_the_others(ws, monkeypatch):
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh"), _gap_line(B, "Other")])
    real = gaps.close_gap

    tried = []

    def flaky(project, gap, **kw):
        tried.append(gap)
        if gap == A:
            raise ValueError("blocked phrase")
        return real(project, gap, **kw)
    monkeypatch.setattr(gaps, "close_gap", flaky)
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A, B])
    assert gaps.close_declared(conn) == 1
    assert gaps.close_declared(conn) == 0
    assert _results(conn) == [("u1", A, "error:ValueError"), ("u1", B, "closed")]
    assert tried == [A, B]                                   # a recorded gap is not tried again


def test_an_unreadable_or_crlf_improvement_never_blocks_and_never_loops(ws):
    path = _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A])
    gaps.close_declared(conn)
    assert _results(conn) == [("u1", A, "unparseable")]
    path.write_bytes(b"# IMPROVEMENT\n\n## What to Watch For\n\xff\xfe\n")
    assert gaps.open_gaps("P") == [] and gaps.open_gap_ids("P") == set()


# 4 ── nothing closes without a written event of a landed run

@pytest.mark.parametrize("state,source", [("rejected", "S3"), ("judged", "S3"), ("written", "S7")])
def test_an_unwritten_or_non_run_event_closes_nothing(ws, state, source):
    path = _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    before = path.read_bytes()
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A], state=state, source=source)
    assert gaps.close_declared(conn) == 0 and path.read_bytes() == before


def test_enqueue_keeps_only_open_gaps_of_a_landed_run(ws, monkeypatch):
    """The report file is writable after prepare: enqueue checks closes again."""
    from core.ingestion.sources import s3_run
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh"), _gap_line(B, "Done", mark="x")])
    _improvement(ws["ws"], "Q", [_gap_line(C, "Other project")])
    report = _report([A, B, C])
    calls = []
    monkeypatch.setattr(s3_run, "_roots", lambda e: (("run_event:e1",), ("root:e1",)))
    from core.ingestion.api import EnqueueResult
    monkeypatch.setattr(api_v2_mod(), "enqueue_unit", lambda *a, **k: calls.append(a[2]) or EnqueueResult("queued", "q"))
    monkeypatch.setattr(api_v2_mod(), "record_extraction_attempt", lambda *a, **k: "a")

    def engine(landing):
        return SimpleNamespace(run_id="run_x", project="P", contract={}, read_reflect_report=lambda: report,
                               projection=lambda: {"landing": landing})
    s3_run.enqueue_report(engine("not_landed"))
    s3_run.enqueue_report(engine("landed"))
    assert [unit.closes for unit in calls] == [None, [A]]
    monkeypatch.setattr(gaps, "open_gap_ids", lambda project: set())
    s3_run.enqueue_report(engine("landed"))
    assert calls[-1].closes is None


def api_v2_mod():
    from core.ingestion import api_v2
    return api_v2


# 5 ── a concurrent user edit is kept; the close is retried

def test_a_concurrent_user_edit_is_kept_and_the_close_retried(ws, monkeypatch):
    path = _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    real = governed_edit._insert_redact_plan
    raced = []

    def user_saves_first(conn, **kw):
        plan = real(conn, **kw)
        if not raced:
            raced.append(1)
            path.write_text(path.read_text(encoding="utf-8") + "user note\n", encoding="utf-8")
        return plan
    monkeypatch.setattr(governed_edit, "_insert_redact_plan", user_saves_first)
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A])
    assert gaps.close_declared(conn) == 0
    assert "user note" in path.read_text(encoding="utf-8") and A in path.read_text(encoding="utf-8")
    steps = [r[0] for r in conn.execute("SELECT step FROM ingestion_writes WHERE op_kind='user_redact'")]
    assert steps == ["abandoned"]                               # never retried forever
    assert gaps.close_declared(conn) == 1                       # the retry, on the file as it is
    text = path.read_text(encoding="utf-8")
    assert "user note" in text and A not in text


# 6 ── a recurrence of a closed gap is written again

def test_a_unit_the_same_as_a_closed_gap_is_written_again(tmp_path, monkeypatch):
    """The same text as a closed gap ([x]) is a recurrence: written, derived from it; the
    same as an open gap or a done task item is already known."""
    und = tmp_path / "Projects" / "Widget" / "2-understanding"
    und.mkdir(parents=True)
    improvement = und / "IMPROVEMENT.md"
    unit = {"kind": "gap", "gap_kind": "debt", "scope": "project:Widget", "title": "Tab refresh",
            "what": "The tab does not refresh after the user switches back to it.", "evidence": ["event:e1"]}
    row = {"id": "c", "unit_id": "c.unit", "unit_kind": "gap", "source_v2": "S3", "source_ref": "r",
           "extraction_pass": "main", "payload": json.dumps(unit),
           "evidence_root_ids": json.dumps(["root-1"]),
           "envelope": json.dumps({"root_ids": ["root-1"], "lineage_root_ids": ["root-1"],
                                   "strength": "inferred", "read_external": True, "cited_external": False}),
           "project_candidates": "[]", "project_hint": None, "project_snapshot": "{}"}
    entry = "**Tab refresh** The tab does not refresh after the user switches back to it. gap kind: debt <!-- cand:cand-old.unit -->"
    improvement.write_text(f"# IMPROVEMENT\n\n## What to Watch For\n\n- [x] {entry}\n", encoding="utf-8")
    decision = pipeline_v2.run_unit_v2(row, tmp_path)
    assert decision.action == "add"
    old = "Projects/Widget/2-understanding/IMPROVEMENT.md#What to Watch For#cand:cand-old.unit"
    assert {"predicate": "derived_from", "object_anchor": old} in decision.version_vector["relation_hints"]
    improvement.write_text(f"# IMPROVEMENT\n\n## What to Watch For\n\n- [risk] {entry}\n", encoding="utf-8")
    assert pipeline_v2.run_unit_v2(row, tmp_path).reason == "already_exists"


# wiring ── the real CLI shows the list, prepare checks it, the landing keeps closes

def test_the_run_is_shown_its_projects_gaps_and_a_landed_report_keeps_closes(tmp_path, monkeypatch):
    from tests.pipeline_v1_rig import PROJECT, Rig
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        _improvement(r.ws, PROJECT, [_gap_line(A, "Tab refresh"), _gap_line(B, "Done", mark="x")])
        r.create()
        r.drive(until="deliver")
        out = r.v1("events")
        assert out["open_gaps"] == [{"id": A, "title": "Tab refresh", "gap_kind": "follow_up"}]
        report = _report([B])
        r.v1("reflect-prepare", "--artifact", r.artifact("reflect0", report))  # registers the roots
        from core.run_protocol.provenance import run_roots
        report["delivered"][0]["evidence"] = [sorted(run_roots(r.run_id))[0]]
        refused = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", report))
        assert refused.get("ok") is not True and "not open" in json.dumps(refused)
        report["delivered"][0]["closes"] = [A]
        assert r.v1("reflect-prepare", "--artifact", r.artifact("reflect2", report)).get("ok") is True
        r.v1("land", "--message", "fix: tab refresh\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
        assert r.state()["landing"] == "landed"
        conn = api.connect_ro()
        payloads = [json.loads(p[0]) for p in conn.execute(
            "SELECT payload FROM ingestion_inbox WHERE unit_kind='event' AND source_v2='S3'")]
        assert [p.get("closes") for p in payloads] == [[A]]
    finally:
        r.close()


def test_the_worker_closes_right_after_the_write_and_in_maintenance(monkeypatch):
    from core.ingestion import worker, writer
    calls = []
    monkeypatch.setattr(gaps, "close_declared", lambda conn, unit_id=None: calls.append(unit_id) or 0)

    class Conn:
        in_transaction = False
        def execute(self, *a): return self
        def fetchone(self): return {}
    monkeypatch.setattr(writer, "apply_unit", lambda *a: "done")
    worker._hand_to_writer(Conn(), None, "u1", None)
    monkeypatch.setattr(writer, "apply_unit", lambda *a: "deferred")
    worker._hand_to_writer(Conn(), None, "u2", None)
    assert calls == ["u1"]
    self = SimpleNamespace(workspace_root=Path("/nonexistent"))
    for name in ("recover",):
        monkeypatch.setattr(writer, name, lambda *a, **k: None)
    monkeypatch.setattr(worker, "reaggregate_candidates", lambda conn: None)
    monkeypatch.setattr(worker, "cleanup_expired", lambda conn, **k: None)
    monkeypatch.setattr(pipeline_v2, "requeue_missing_sections", lambda root: None)
    worker.IngestionWorker._maintain(self, Conn())
    assert calls == ["u1", None]


# the project's own spelling ── a name in another case never becomes a path

def test_a_project_named_in_another_case_gets_the_folders_spelling(ws):
    from core.project_registry import canonical_project_name
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    assert [canonical_project_name(n) for n in ("p", "P", "New")] == ["P", "P", "New"]
    assert gaps.close_gap("p", A, closed_by="run_x", note="fixed") == "closed"
    conn = _connect(ws["db"])
    paths = [r[0] for r in conn.execute("SELECT target_path FROM ingestion_writes")]
    assert paths == ["Projects/P/2-understanding/IMPROVEMENT.md"]


def test_a_run_created_with_a_lower_case_project_belongs_to_the_folder(ws, monkeypatch):
    from core import repo_changes
    from core.run_protocol import project_binding
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    monkeypatch.setattr(repo_changes, "bound_projects", lambda: {"/repo": "P"})
    monkeypatch.setattr(project_binding, "_main_checkout", lambda root: root)
    assert project_binding.resolve("p", {"repo": "/repo"}).project == "P"      # bound
    assert project_binding.resolve("p", {"other": "/other"}).project == "P"    # unbound
    assert project_binding.resolve("New", {"other": "/other"}).project == "New"


def test_the_read_side_names_a_project_by_its_folders_spelling(ws, monkeypatch):
    from core import addressing
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    assert addressing.project_root("p").name == "P"
    table = addressing.ddd_units("p")
    assert table.project == "P" and any(A in u.text for u in table.units)


def test_two_closers_run_one_at_a_time(ws, monkeypatch):
    _improvement(ws["ws"], "P", [_gap_line(A, "Tab refresh")])
    held = []
    real = gaps.close_gap
    monkeypatch.setattr(gaps, "close_gap", lambda *a, **k: held.append(gaps._LOCK.locked()) or real(*a, **k))
    conn = _connect(ws["db"])
    _written_event(conn, "u1", [A])
    gaps.close_declared(conn)
    assert held == [True]
