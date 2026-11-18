"""The REFLECT report every reader sees is the one reflect-prepare recorded.

reflect-prepare records the validated five-part report in a ``reflect_report`` event (its
body an audit blob) before writing artifacts/reflect.json as a projection, and
RunEngine.read_reflect_report reads the newest such event. So the RF checks, the CDF
submission (s3_run.enqueue_report, through report_units) and the Eval's knowledge count
read what was validated, whatever happens to the workspace file later.

What is tested (each on a research run at DELIVER, driven through the CLI rig):
- the event holds exactly the document the file got;
- an edited file changes neither the report, the submitted units nor the count;
- a degraded REFLECT records its empty report as a newer event;
- a run created before the event existed still reads the file; a new run that never
  ran reflect-prepare reads nothing from it;
- an unreadable recorded report reads as empty, never as the file;
- a deleted file still gives the knowledge count from the event;
- an invalid row is still dropped and named;
- no other backend code reads the report's sections from the file;
- display_models takes the report and its event from the log it is given, without
  reading the log again.
"""
import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig

EMPTY = {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}
#: a cognition unit that reflect-prepare never validated (no run anchor as evidence)
FORGED = {"kind": "cognition", "type": "guideline", "scope": "project:P",
          "title": "Forged after reflect-prepare", "what": "A unit written into the file later.",
          "applicability": "always", "evidence": ["doc:forged"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="research", with_repo=False)
    r.create()
    r.drive(until="deliver")
    yield r
    r.close()


def _prepare(r: Rig, report: dict) -> dict:
    out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", report))
    assert out.get("ok") is True, out
    return out


def _file(r: Rig) -> Path:
    return r.engine().run_dir / "artifacts" / "reflect.json"


def _forge(r: Rig) -> None:
    doc = json.loads(_file(r).read_text())
    doc["sections"]["practices"] = [FORGED]
    _file(r).write_text(json.dumps(doc))


def _recorded(r: Rig) -> list[dict]:
    return [e for e in r.events() if e["kind"] == "reflect_report"]


def _blob(r: Rig, event: dict) -> bytes:
    digest = event["payload"]["report_json"]["blob"]
    return (r.data / "state" / "pipeline_agent_audit" / "runs" / str(r.run_id)
            / "blobs" / digest).read_bytes()


def test_prepare_records_the_report_in_an_event(run):
    _prepare(run, EMPTY)
    events = _recorded(run)
    assert len(events) == 1
    assert json.loads(_blob(run, events[0])) == json.loads(_file(run).read_text())
    assert events[0]["payload"]["n_rows"] == 0


def test_an_edited_file_reads_the_recorded_report(run):
    from core.ingestion.sources.s3_run import report_units
    _prepare(run, EMPTY)
    _forge(run)
    engine = run.engine()
    report = engine.read_reflect_report()
    assert report == {name: [] for name in EMPTY}
    units, invalid = report_units(report)
    assert units == [] and invalid == []
    assert engine.display_models(engine.store.read_events())["knowledge"]["new"] == 0


def test_the_degraded_rewrite_is_recorded_too(run, monkeypatch):
    from core.run_protocol import checks
    real = checks.evaluate

    def failing(check_id, context):
        return (False, "forced") if check_id.startswith("RF-") else real(check_id, context)

    anchors = run.v1("events")["anchors"]
    assert anchors
    # a row reflect-prepare keeps, so the degraded rewrite is what empties the report
    report = {**EMPTY, "practices": [dict(FORGED, title="A kept practice", evidence=[anchors[0]])]}
    monkeypatch.setattr(checks, "evaluate", failing)
    for _ in range(3):
        if run.engine().projection().get("reflect") == "degraded":
            break
        run.engine().prepare_reflect(report)
    engine = run.engine()
    assert engine.projection().get("reflect") == "degraded"
    events = _recorded(run)
    assert events[0]["payload"]["n_rows"] == 1
    assert json.loads(_blob(run, events[-1]))["sections"] == {name: [] for name in EMPTY}
    # the projection the Pipelines views read counts the empty report, not the kept one
    assert json.loads((engine.run_dir / "run.json").read_text())["knowledge"]["new"] == 0
    _forge(run)
    assert run.engine().read_reflect_report() == {name: [] for name in EMPTY}


def _as_old_run(r: Rig) -> None:
    """Make the run's contract one written before the reflect_report event existed."""
    path = r.data / "state" / "pipeline_agent_audit" / "runs" / str(r.run_id) / "contract.json"
    contract = json.loads(path.read_text())
    assert contract.pop("reflect_recorded") is True
    path.write_text(json.dumps(contract))


def test_a_run_without_the_event_reads_the_file(run):
    _as_old_run(run)
    path = _file(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 2, "sections": {**EMPTY, "practices": [FORGED]}}))
    assert _recorded(run) == []
    report = run.engine().read_reflect_report()
    assert report["practices"] == [FORGED]
    assert set(report) == set(EMPTY)


@pytest.mark.parametrize("created_before", [False, True])
def test_an_unreadable_blob_reads_empty_not_the_file(run, caplog, created_before):
    """Also for a run created before the event existed and prepared after it (in
    flight at the update): its recorded report is still the only one it reads."""
    _prepare(run, EMPTY)
    if created_before:
        _as_old_run(run)
    event = _recorded(run)[-1]
    digest = event["payload"]["report_json"]["blob"]
    (run.data / "state" / "pipeline_agent_audit" / "runs" / str(run.run_id)
     / "blobs" / digest).unlink()
    _forge(run)
    with caplog.at_level("WARNING"):
        report = run.engine().read_reflect_report()
    assert report == {name: [] for name in EMPTY}
    assert "recorded REFLECT report unreadable" in caplog.text


def test_a_deleted_file_still_counts_from_the_event(run):
    _prepare(run, EMPTY)
    _file(run).unlink()
    engine = run.engine()
    assert engine.reflect_written() is True
    assert engine.display_models(engine.store.read_events())["knowledge"]["new"] == 0


def test_an_invalid_row_is_still_dropped(run):
    out = _prepare(run, {**EMPTY, "practices": [{"kind": "cognition", "title": "no type"}]})
    assert out["reflect"].get("dropped"), out
    recorded = json.loads(_blob(run, _recorded(run)[-1]))
    assert recorded["sections"]["practices"] == []


def test_no_other_code_reads_the_report_sections():
    """Only the engine reads artifacts/reflect.json; the router reads the old lessons
    list of runs from before the five-part report, never its sections."""
    backend = Path(__file__).resolve().parents[1]
    hits = {}
    for path in [*backend.joinpath("core").rglob("*.py"), *backend.joinpath("routers").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if '"reflect.json"' in text:   # a path built to the file, not a mention
            hits[str(path.relative_to(backend))] = text
    assert set(hits) <= {"core/run_protocol/engine.py", "routers/pipelines.py",
                         "core/run_protocol/knowledge_counts.py"}, sorted(hits)
    router = hits.get("routers/pipelines.py", "")
    assert '"sections"' not in router and "read_reflect_report" not in router


def test_a_new_run_that_skips_reflect_prepare_submits_nothing_from_the_file(run):
    """A run created now that never ran reflect-prepare cannot hand CDF a report written
    straight into the workspace file: there is no recorded report, so it is empty."""
    from core.ingestion.sources.s3_run import report_units
    path = _file(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 2, "sections": {**EMPTY, "practices": [FORGED]}}))
    engine = run.engine()
    assert engine.read_reflect_report() == {name: [] for name in EMPTY}
    assert report_units(engine.read_reflect_report()) == ([], [])
    assert engine.reflect_written() is False
    assert engine.display_models(engine.store.read_events())["knowledge"]["new"] is None


def test_display_models_reads_no_second_copy_of_the_log(run, monkeypatch):
    """display_models is given the log write_projection read and verified once; the
    report and its event are taken from it, not read again."""
    _prepare(run, EMPTY)
    engine = run.engine()
    events = engine.store.read_events()
    calls = []
    real = engine.store.read_events
    monkeypatch.setattr(engine.store, "read_events", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert engine.display_models(events)["knowledge"]["new"] == 0
    assert calls == []

