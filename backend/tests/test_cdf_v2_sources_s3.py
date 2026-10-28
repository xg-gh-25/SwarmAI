"""CDF v2 S3 run-source and REFLECT contract tests.

Tests use a realistic run record and monkeypatch all api_v2 functions. Invariants:
the five-part report is enqueued deterministically (no LLM) in the landing command,
which completes REFLECT without waiting for the CDF worker; only person-authored
human_answer text reaches the LLM trusted pass, and that pass never blocks REFLECT.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.ingestion import api_v2
from core.ingestion.sources import s3_run
from core.run_protocol import reflect, reflect_job


def _event(anchor="run_event:e1"):
    return {"kind": "event", "when": "2026-10-27", "scope": "project:P",
            "title": "Source migration landed", "what": "Six evidence sources replaced overlapping writers.",
            "outcome": "CDF owns governed writes.", "evidence": [anchor]}


def _report():
    return {"delivered": [_event()], "decisions": [], "corrections": [],
            "practices": [], "gaps": []}


class Store:
    def __init__(self, events): self.events = events
    def read_events(self): return self.events


class Engine:
    run_id = "run_one"
    project = "P"
    contract = {"repo_roots": {"repo": "/repo"}}
    def __init__(self, events=None): self.store = Store(events or [{"event_id": "e1", "kind": "land"}])
    def landed_commits(self): return {}
    def read_reflect_report(self): return _report()


class FakeLLM:
    def __init__(self, replies): self.replies = list(replies)
    def extract(self, prompt): return SimpleNamespace(value=self.replies.pop(0), raw="raw")


def test_reflect_requires_exact_five_part_contract():
    out = reflect.prepare(_report(), allowed_anchors={"run_event:e1"})
    assert out["schema"] == 2 and list(out["sections"]) == list(reflect.SECTIONS)
    with pytest.raises(reflect.ReflectError):
        reflect.prepare({"lessons": []}, allowed_anchors=set())


def test_s3_option_only_human_answer_records_no_unit(monkeypatch):
    engine = Engine([{"event_id": "e1", "kind": "human_answer",
                      "payload": {"option_id": "B"}}])
    monkeypatch.setattr(s3_run, "_roots", lambda e: (("run_event:e1",), ("root:e1",)))
    calls, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    result = s3_run.extract_run(engine, llm=FakeLLM([]))   # no LLM call at all
    assert result["human_answer"] == [] and len(result["main"]) == 1
    assert attempts[0][1]["status"] == "no_unit"
    assert calls[0][1]["extraction_pass"] == "main"
    assert calls[0][1]["read_root_ids"] == ("root:e1",)


def test_s3_human_text_is_trusted_pass_first(monkeypatch):
    events = [{"event_id": "h1", "kind": "human_answer",
               "payload": {"option_id": "B", "human_text": "Prefer the smaller design."}},
              {"event_id": "e1", "kind": "land"}]
    engine = Engine(events)
    monkeypatch.setattr(s3_run, "_roots", lambda e:
                        (("run_event:h1", "run_event:e1"), ("root:h1", "root:e1")))
    calls, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    cognition = {"kind": "cognition", "type": "guideline", "scope": "agent",
                 "title": "Prefer smaller designs", "what": "Prefer the smallest design that meets the need.",
                 "applicability": "When architecture choices have equal value.",
                 "evidence": ["run_event:h1"]}
    s3_run.extract_run(engine, llm=FakeLLM([[cognition]]))   # one LLM call: human text only
    assert [call[1]["extraction_pass"] for call in calls] == ["human_answer", "main"]
    assert calls[0][1]["read_root_ids"] == ("run_event:h1",)
    assert len(attempts) == 2


def test_finish_in_cli_completes_reflect_without_waiting_for_worker(monkeypatch):
    status = {"value": "reflecting"}
    done = []
    engine = SimpleNamespace(
        run_id="run_one", projection=lambda: {"status": status["value"]},
        reflect_complete=lambda degraded, reason: (done.append(degraded),
                                                   status.update(value="completed")),
        release_landed_worktrees=lambda: done.append("released"))
    monkeypatch.setattr(s3_run, "enqueue_report", lambda e: [SimpleNamespace(status="queued")])
    monkeypatch.setattr(s3_run, "has_human_text", lambda e: False)
    import core.ingestion.worker as worker
    monkeypatch.setattr(worker.IngestionWorker, "run_once",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run the worker")))
    assert reflect_job.finish_in_cli(engine) == {"reflect": "ok", "main": 1}
    assert done == [False, "released"]


def test_enqueue_failure_degrades_reflect_and_never_blocks(monkeypatch):
    status = {"value": "reflecting"}
    done = []
    engine = SimpleNamespace(
        run_id="run_one", projection=lambda: {"status": status["value"]},
        reflect_complete=lambda degraded, reason: (done.append((degraded, reason)),
                                                   status.update(value="completed")),
        release_landed_worktrees=lambda: None)
    monkeypatch.setattr(s3_run, "enqueue_report",
                        lambda e: (_ for _ in ()).throw(OSError("db down")))
    assert reflect_job.finish_in_cli(engine)["reflect"] == "degraded"
    assert done[0][0] is True and "OSError" in done[0][1]


def test_previous_pending_run_marks_only_nonterminal_s3(tmp_path):
    import json
    import sqlite3
    runs = tmp_path / "ws/Projects/P/.artifacts/runs/run_old"
    runs.mkdir(parents=True)
    (runs / "run.json").write_text(json.dumps({"created_at": "2026-10-26T00:00:00Z"}))
    db = tmp_path / "data/ingestion.db"; db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE ingestion_inbox(id TEXT, source_ref TEXT, source_v2 TEXT, source TEXT);
        CREATE TABLE ingestion_units(candidate_id TEXT, state TEXT);
        INSERT INTO ingestion_inbox VALUES('c','run_old','S3','S3');
        INSERT INTO ingestion_units VALUES('c','queued');
        """)
    assert s3_run.previous_pending_run(tmp_path / "data", tmp_path / "ws", "P", "run_new") == "run_old"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE ingestion_units SET state='written'")
    assert s3_run.previous_pending_run(tmp_path / "data", tmp_path / "ws", "P", "run_new") is None
