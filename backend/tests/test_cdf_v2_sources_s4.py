"""CDF v2 S4 code-source tests.

Run landing records are the ownership authority. The LLM and every api_v2 side
effect are replaced so only source selection and arguments are tested.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.ingestion import api_v2
from core.ingestion.sources import s4_code


class FakeLLM:
    def extract(self, prompt):
        assert "commit:manual" in prompt and "commit:run-owned" not in prompt
        units = [{"kind": "cognition", "type": "model", "scope": "project:P",
                  "title": "The ingestion source boundary", "what": "Sources only enqueue typed units.",
                  "why": "One writer prevents competing persistent state.",
                  "evidence": ["commit:manual"]},
                 {"kind": "event", "when": "2026-10-27", "scope": "project:P",
                  "title": "Source boundary merged", "what": "The source boundary changed across modules.",
                  "impact": "Governed writes now have one path.", "evidence": ["commit:manual"]}]
        return SimpleNamespace(value=units, raw="raw")


def test_s4_excludes_run_landed_commit_before_extraction(monkeypatch):
    calls, attempts, roots = [], [], []
    monkeypatch.setattr(api_v2, "register_root", lambda kind, locator, content, trust:
                        roots.append((kind, locator, content, trust)) or "root:registered")
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    commits = [{"sha": "run-owned", "repository": "demo", "record": "pipeline patch"},
               {"sha": "manual", "repository": "demo", "record": "manual architecture patch"}]
    out = s4_code.extract_commits(
        commits, run_landed_commits={"run-owned"}, project_hint="P",
        root_ids=("root:manual",), llm=FakeLLM())
    assert len(out) == 2 and len(calls) == 2 and len(attempts) == 1
    assert all(call[0][0] == "S4" for call in calls)
    assert roots == [("commit", "repo:demo:manual", "manual architecture patch", "external")]
    assert all(call[1]["read_root_ids"] == ("root:registered", "root:manual") for call in calls)
    assert all(call[1]["project_snapshot"]["project"] == "P" for call in calls)


def test_s4_all_run_owned_records_no_unit(monkeypatch):
    attempts = []
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    assert s4_code.extract_commits(
        [{"sha": "owned"}], run_landed_commits={"owned"}, llm=FakeLLM()) == []
    assert attempts[0][1]["status"] == "no_unit"


def test_landed_commit_shas_comes_from_run_audits(tmp_path):
    path = tmp_path / "Projects/P/.artifacts/runs/run_one"
    path.mkdir(parents=True)
    (path / "run_audit.json").write_text(json.dumps({
        "details": {"commits": {"repo": "landed-sha"}}}))
    assert s4_code.landed_commit_shas(tmp_path) == {"landed-sha"}
