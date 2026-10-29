"""Tests for core/transcript_usage.py — measured token usage from CLI transcripts.

Methodology: write synthetic transcripts shaped like the CLI's (assistant records
carrying ``message.usage`` and a UTC ``timestamp``; several records per response
share one message id) under a tmp CLAUDE_CONFIG_DIR and assert on the sums.

Key properties:
- one usage per message id, across the main file and repeated records
- only responses inside ``[lo, hi]`` count; sidechain records in the main file do not
- sub-agent files (``<stem>/subagents/agent-*.jsonl``) count when asked
- keys come out in the token_usage table's names (cache_creation_tokens, ...)
- ids that are not SDK session ids never reach a path; missing files resolve to nothing
"""
import json
import os
from datetime import datetime

import pytest

from core import transcript_usage as tu


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _resp(mid, t, inp=10, out=5, cread=100, ccreate=20, sidechain=False, uuid=None):
    return {"type": "assistant", "timestamp": t, "uuid": uuid or f"u-{mid}-{t}",
            "isSidechain": sidechain,
            "message": {"id": mid, "usage": {
                "input_tokens": inp, "output_tokens": out,
                "cache_read_input_tokens": cread, "cache_creation_input_tokens": ccreate}}}


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(path)


SID = "0d206ebe-9c4f-40ee-875a-491716e326b8"
LO, HI = _ts("2026-10-18T10:00:00Z"), _ts("2026-10-18T11:00:00Z")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude" / "projects" / "-ws"


class TestUsageInWindow:
    def test_sums_inside_window_one_usage_per_message(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [
            _resp("m0", "2026-10-18T09:59:59Z"),                     # before the window
            _resp("m1", "2026-10-18T10:00:05Z"),
            _resp("m1", "2026-10-18T10:00:06Z", uuid="dup-block"),   # same response, 2nd block
            _resp("m2", "2026-10-18T10:30:00Z", inp=1, out=7, cread=3, ccreate=4),
            _resp("m3", "2026-10-18T10:40:00Z", sidechain=True),     # sidechain: not main work
            _resp("m4", "2026-10-18T11:00:01Z"),                     # after the window
        ])
        u = tu.usage_in_window([main], LO, HI)
        assert u["responses"] == 2
        assert u["input_tokens"] == 11 and u["output_tokens"] == 12
        assert u["cache_read_tokens"] == 103 and u["cache_creation_tokens"] == 24
        assert u["tokens"] == 11 + 12 + 24  # input + output + cache creation; reads excluded
        assert u["source"] == "transcript"

    def test_repeated_message_keeps_the_largest_output(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [
            _resp("m1", "2026-10-18T10:00:05Z", out=2),
            _resp("m1", "2026-10-18T10:00:06Z", out=9, uuid="later"),
        ])
        assert tu.usage_in_window([main], LO, HI)["output_tokens"] == 9

    def test_same_message_in_two_files_counts_once(self, cfg):
        a = _write(cfg / f"{SID}.jsonl", [_resp("m1", "2026-10-18T10:00:05Z")])
        b = _write(cfg / "11111111-2222-3333-4444-555555555555.jsonl",
                   [_resp("m1", "2026-10-18T10:00:05Z")])
        assert tu.usage_in_window([a, b], LO, HI)["responses"] == 1

    def test_subagent_files_count_when_asked(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [_resp("m1", "2026-10-18T10:00:05Z")])
        _write(cfg / SID / "subagents" / "agent-abc.jsonl",
               [_resp("s1", "2026-10-18T10:10:00Z", out=50, sidechain=True)])
        _write(cfg / SID / "subagents" / "agent-old.jsonl",
               [_resp("s2", "2026-10-18T09:00:00Z", out=70, sidechain=True)])
        with_sub = tu.usage_in_window([main], LO, HI)
        assert with_sub["responses"] == 2 and with_sub["output_tokens"] == 55
        assert tu.usage_in_window([main], LO, HI, include_subagents=False)["responses"] == 1

    def test_nothing_in_window_is_none(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [_resp("m0", "2026-10-18T09:00:00Z")])
        assert tu.usage_in_window([main], LO, HI) is None
        assert tu.usage_in_window([], LO, HI) is None

    def test_malformed_lines_and_missing_files_do_not_raise(self, cfg):
        p = cfg / f"{SID}.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text('not json\n{"type": "assistant", "timestamp": "bad"}\n'
                     + json.dumps(_resp("m1", "2026-10-18T10:00:05Z")) + "\n"
                     + '{"type": "assistant", "timestamp": "2026-10-18T10:01:00Z", "message": {"id": "m2", "usage": "x"}}\n',
                     encoding="utf-8")
        u = tu.usage_in_window([str(p), str(cfg / "missing.jsonl")], LO, HI)
        assert u["responses"] == 1


class TestResolveTranscripts:
    def test_resolves_existing_ids_only(self, cfg):
        _write(cfg / f"{SID}.jsonl", [_resp("m1", "2026-10-18T10:00:05Z")])
        paths = tu.resolve_transcripts([SID, "aaaaaaaa-0000-0000-0000-000000000000", SID])
        assert [os.path.basename(p) for p in paths] == [f"{SID}.jsonl"]

    @pytest.mark.parametrize("bad", ["../x", "a/b", "", None, "*", "x" * 200, "abc"])
    def test_rejects_non_session_ids(self, cfg, bad):
        assert tu.resolve_transcripts([bad]) == []

    def test_subagent_locator(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [])
        sub = _write(cfg / SID / "subagents" / "agent-1.jsonl", [])
        assert tu.find_subagent_transcripts([main]) == [sub]


class TestReviewHardening:
    """Security / concurrency review findings on the first version (run_045b96b2)."""

    def test_response_straddling_a_window_edge_counts_once_by_its_first_record(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [
            _resp("m1", "2026-10-18T10:59:59Z", out=1),
            _resp("m1", "2026-10-18T11:00:02Z", out=9, uuid="tail"),   # same response, past HI
        ])
        first = tu.usage_in_window([main], LO, HI)
        nxt = tu.usage_in_window([main], HI + 1, HI + 3600)
        assert first["responses"] == 1 and first["output_tokens"] == 9   # fullest usage kept
        assert nxt is None                                                # not counted again

    def test_bad_records_are_skipped_not_fatal(self, cfg):
        p = cfg / f"{SID}.jsonl"
        p.parent.mkdir(parents=True)
        bad = [
            '{"type": "assistant", "usage": 1, "x": ' + "[" * 5000 + "]" * 5000 + "}",   # deep nesting
            json.dumps({"type": "assistant", "timestamp": "2026-10-18T10:01:00Z",
                        "message": {"id": ["unhashable"], "usage": {"output_tokens": 1}}}),
            json.dumps({"type": "assistant", "timestamp": "2026-10-18T10:02:00Z",
                        "message": {"id": "m9", "usage": {"output_tokens": "abc", "input_tokens": 1e400}}}),
            json.dumps(_resp("m1", "2026-10-18T10:00:05Z")),
        ]
        p.write_text("\n".join(bad) + "\n", encoding="utf-8")
        u = tu.usage_in_window([str(p)], LO, HI)
        assert u["responses"] == 2 and u["output_tokens"] == 5   # m9's bad numbers count as 0

    def test_trailing_newline_id_and_too_many_ids(self, cfg):
        _write(cfg / f"{SID}.jsonl", [])
        assert not tu.valid_session_id(SID + "\n")
        assert tu.resolve_transcripts([SID + "\n"]) == []
        fillers = [f"{i:08x}-0000-0000-0000-000000000000" for i in range(tu.MAX_TRANSCRIPT_IDS)]
        assert tu.resolve_transcripts(fillers + [SID]) == []      # past the cap: not looked up

    def test_subagent_entry_that_is_not_a_file_is_ignored(self, cfg):
        main = _write(cfg / f"{SID}.jsonl", [])
        (cfg / SID / "subagents" / "agent-dir.jsonl").mkdir(parents=True)
        assert tu.find_subagent_transcripts([main]) == []


class TestByModel:
    """Each response's model is kept, so a run's usage splits per model (usage ledger
    attribution: a sub-agent on another model is priced as that model)."""

    def test_the_window_splits_per_response_model(self, cfg):
        a = _resp("m1", "2026-10-18T10:00:05Z", inp=1, out=2, cread=3, ccreate=4)
        a["message"]["model"] = "opus"
        b = _resp("m2", "2026-10-18T10:10:00Z", inp=10, out=20, cread=30, ccreate=40)
        b["message"]["model"] = "haiku"
        c = _resp("m3", "2026-10-18T10:20:00Z", inp=100, out=0, cread=0, ccreate=0)   # names no model
        main = _write(cfg / f"{SID}.jsonl", [a, b, c])
        u = tu.usage_in_window([main], LO, HI)
        assert u["by_model"] == {
            "opus": {"input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 3, "cache_creation_tokens": 4},
            "haiku": {"input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 30, "cache_creation_tokens": 40},
            "": {"input_tokens": 100, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        }
        assert u["input_tokens"] == sum(m["input_tokens"] for m in u["by_model"].values())
