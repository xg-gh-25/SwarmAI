"""REFLECT source integration with Cognitive Distillation Flow.

REFLECT records run evidence, enqueues stable lesson candidates, and never writes
DDD or pipeline-lessons files directly.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skip(reason="r16.5 S3 no longer routes or writes pipeline lessons")


def test_reflect_process_enqueues_and_does_not_write_library(monkeypatch, tmp_path):
    from core import session_inputs
    from core.ingestion import api
    from core.run_protocol import reflect_job, reflect_library

    data = tmp_path / "data.db"
    with sqlite3.connect(data) as conn:
        conn.execute("""CREATE TABLE session_inputs (
            event_id TEXT PRIMARY KEY, session_id TEXT, kind TEXT, trust TEXT,
            content_hash TEXT, ref TEXT, created_at TEXT)""")
    monkeypatch.setattr(session_inputs, "_db_path", lambda: data)
    calls = []
    monkeypatch.setattr(
        api, "enqueue",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(status="queued", candidate_id="candidate-1"),
    )
    monkeypatch.setattr(
        reflect_library, "write",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy library writer called")),
    )

    run_dir = tmp_path / "run"
    artifact = run_dir / "artifacts" / "reflect.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({
        "schema": 1,
        "lessons": [{
            "id": "L1", "type": "pitfall", "statement": "Validate output before landing",
            "evidence": ["event-a"], "relation": {"kind": "new", "entry": None},
        }],
    }))

    class Engine:
        run_id = "run-7"
        workspace = tmp_path
        project = "Demo"
        contract = {"repo_roots": {"repo": str(tmp_path / "repo")}}

        def owner_session(self):
            return "session-7"

        def reflect_complete(self, **kwargs):
            self.completed = kwargs

    engine = Engine()
    engine.run_dir = run_dir
    result = reflect_job._process_run(engine)
    args, kwargs = calls[0]
    assert args[:2] == ("reflect", "run-7")
    assert args[2].startswith("lesson:")
    assert args[3]["kind"] == "reflect"
    assert kwargs["envelope"]["tainted"] is True
    assert result["lessons"][0]["outcome"]["status"] == "queued"
    assert not (tmp_path / "Knowledge" / "Library" / "pipeline-lessons").exists()
