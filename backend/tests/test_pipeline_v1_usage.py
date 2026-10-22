"""Tests Pipeline v1 measured token attribution."""
from __future__ import annotations

import json

from core import transcript_usage


def test_usage_for_subagent_counts_sidechain_records(tmp_path):
    path = tmp_path / "agent.jsonl"
    path.write_text(json.dumps({"type":"assistant","isSidechain":True,"timestamp":"2026-10-22T00:00:00Z","message":{"id":"m1","usage":{"input_tokens":10,"output_tokens":3,"cache_read_input_tokens":4,"cache_creation_input_tokens":2}}})+"\n")
    usage = transcript_usage.usage_for_subagent(str(path))
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 3
    assert usage["cache_read_tokens"] == 4 and usage["responses"] == 1
