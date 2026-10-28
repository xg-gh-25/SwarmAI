"""CDF v2 S5 cross-run metric source tests.

The tests distinguish single-run facts from aggregate evidence and assert every
numeric field's metric anchor plus the explicit inferred-causality instruction.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.ingestion import api_v2
from core.ingestion.sources import s5_metrics


class FakeLLM:
    def extract(self, prompt):
        assert "causal explanation" in prompt and "inferred, not measured" in prompt
        return SimpleNamespace(value=[{
            "kind": "cognition", "type": "correction", "scope": "agent",
            "title": "Review retries are too frequent",
            "what": "The agent repeatedly needs review retries across runs.",
            "how": "Strengthen the pre-review validation step.",
            "applicability": "When the aggregate retry rate remains elevated.",
            "evidence": ["metric:week:retry_rate"],
        }], raw="raw")


def test_s5_emits_metric_anchored_agent_cognition(monkeypatch):
    calls, attempts = [], []
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k:
                        calls.append((a, k)) or SimpleNamespace(status="queued"))
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    out = s5_metrics.extract_metrics(
        "week", {"retry_rate": 0.4, "successes": 6,
                 "interpretation": "retries may indicate weak validation"},
        run_count=10, root_ids=("root:retry", "root:success"), llm=FakeLLM())
    assert len(out) == 1 and len(attempts) == 1
    args, kwargs = calls[0]
    assert args[0] == "S5" and args[2].scope == "agent"
    assert kwargs["read_root_ids"] == ("root:retry", "root:success")
    assert kwargs["extraction_pass"] == "main"


def test_s5_rejects_single_run_and_non_numeric_windows(monkeypatch):
    attempts = []
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda *a, **k:
                        attempts.append((a, k)) or "a")
    assert s5_metrics.extract_metrics("one", {"failures": 1}, run_count=1) == []
    assert s5_metrics.extract_metrics("two", {"note": "text"}, run_count=2) == []
    assert [row[0][0].evidence_range["reason"] for row in attempts] == [
        "single_run", "no_numeric_fields"]
