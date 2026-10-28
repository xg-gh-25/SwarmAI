"""External signal bridges to CDF v2 S6 tests.

The bridge filters the local digest, submits complete external content, and creates no
DDD proposal. Active-session learned content is left to S2.
"""
from __future__ import annotations

import json

from hooks import signal_ddd_bridge as bridge


def _workspace(tmp_path):
    (tmp_path / "Services/signals").mkdir(parents=True)
    return tmp_path


def test_high_relevance_signal_goes_to_s6_without_proposal(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    item = {"title": "Competitor launch", "summary": "Complete market analysis",
            "score": 0.9, "url": "https://example.com/news"}
    (workspace / "Services/signals/signal_digest.json").write_text(
        json.dumps({"items": [item]}))
    calls = []
    monkeypatch.setattr(bridge, "extract_external", lambda *a, **k:
                        calls.append((a, k)) or [object()])
    assert bridge.bridge_signals_to_ddd(str(workspace)) == 1
    assert json.loads(calls[0][0][1]) == item
    assert calls[0][1]["source_url"] == item["url"]
    assert not (workspace / "Projects").exists()


def test_low_relevance_and_missing_digest_emit_nothing(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(bridge, "extract_external", lambda *a, **k: calls.append(1))
    (workspace / "Services/signals/signal_digest.json").write_text(
        json.dumps({"items": [{"title": "minor", "summary": "detail", "score": 0.2}]}))
    assert bridge.bridge_signals_to_ddd(str(workspace)) == 0 and calls == []
    (workspace / "Services/signals/signal_digest.json").unlink()
    assert bridge.bridge_signals_to_ddd(str(workspace)) == 0


def test_learned_content_passes_session_ownership_to_s6(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bridge, "extract_external", lambda *a, **k:
                        calls.append((a, k)) or [])
    assert bridge.bridge_learned_content_to_ddd(
        "Title", "Summary", "https://example.com/doc", str(tmp_path),
        session_id="session-1") is False
    assert calls[0][1]["session_id"] == "session-1"
