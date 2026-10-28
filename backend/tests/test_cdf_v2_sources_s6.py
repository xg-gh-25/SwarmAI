"""CDF v2 S6 external-source tests.

Tests assert Reference-only output, session/run ownership exclusion, complete content,
and no network operation in the source module.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from core.ingestion import api_v2
from core.ingestion.sources import s6_external


class FakeLLM:
    def extract(self, prompt):
        assert "complete external document" in prompt
        return SimpleNamespace(value=[{
            "kind": "reference", "reference_kind": "link", "scope": "world",
            "title": "External design reference",
            "summary": "This reference explains a complete external design.",
            "body": "Keep this reference for later comparison with system designs.",
            "source_url": "https://example.com/design",
            "evidence": ["web:https://example.com/design"],
        }], raw="raw")


def test_s6_enqueues_reference_without_fetching(monkeypatch):
    calls, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    out = s6_external.extract_external(
        "signal-1", "complete external document",
        source_url="https://example.com/design", root_ids=("root:web",), llm=FakeLLM())
    assert len(out) == 1 and len(attempts) == 1
    assert calls[0][0][2].kind == "reference"
    assert calls[0][1]["read_root_ids"] == ("root:web",)
    source = inspect.getsource(s6_external)
    assert "requests." not in source and "urlopen(" not in source and "httpx." not in source


def test_s6_rejects_evidence_owned_by_session_or_run(monkeypatch):
    attempts = []
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    assert s6_external.extract_external("x", "body", evidence_anchor="doc:x",
                                        session_id="s") == []
    assert s6_external.extract_external("x", "body", evidence_anchor="doc:x",
                                        run_id="r") == []
    reasons = [call[0][0].evidence_range["reason"] for call in attempts]
    assert reasons == ["owned_by_S2", "owned_by_S3"]
