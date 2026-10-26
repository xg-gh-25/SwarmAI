"""Speed / Token reference lines come from this install's runs; without one the cell is
N/A (design 2026-10-26-pipeline-review-closure-and-visibility §6.2, step 13).

Before: the lines came from a baselines.json computed on one machine and not even packed
into the daemon, so every run's Speed and Token said WARN "no baseline"; Token summed
cache reads into input, which grows with the host session's length, not the run's work.

What is tested:
- ``baselines.compute``: 4 landed runs of the profile → no line (baseline_runs 4); 5 →
  the p95 line, baseline_runs 5; unlanded or other-profile runs do not count; tokens
  only from runs that recorded cache_read_tokens;
- the audit: no line → Speed / Token N/A with "no baseline yet (n/5 runs)"; partly
  measured with no line → still WARN; nothing measured → BLOCK; a cap → BLOCK; cache
  reads never change the Token verdict and are listed apart;
- ``usage.measure`` and the expert usage record keep cache reads out of input.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.run_protocol import audit, baselines


def _write_run(root: Path, name: str, *, profile="direct", landed=True, seconds=100.0,
               tokens: tuple[int, int, int] | None = (1000, 100, 50000)) -> None:
    run = root / name
    run.mkdir(parents=True)
    (run / "contract.json").write_text(json.dumps({"profile": profile}))
    t0 = 1_700_000_000
    from datetime import datetime, timezone
    at = lambda s: datetime.fromtimestamp(t0 + s, timezone.utc).isoformat()
    rows = [{"kind": "run_create", "at": at(0), "payload": {}}]
    if tokens:
        for metric, value in zip(("input_tokens", "output_tokens", "cache_read_tokens"), tokens):
            rows.append({"kind": "metric", "producer": "cli", "at": at(seconds - 1),
                         "payload": {"name": metric, "value": value}})
    if landed:
        rows.append({"kind": "land", "at": at(seconds), "payload": {"landing": "landed"}})
    (run / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_four_runs_give_no_line_five_give_the_p95(tmp_path):
    for i in range(4):
        _write_run(tmp_path, f"run_{i}", seconds=100.0 + i)
    _write_run(tmp_path, "run_open", landed=False)
    _write_run(tmp_path, "run_other", profile="full")
    four = baselines.compute(tmp_path, "direct")
    assert four == {"budget_seconds": None, "budget_tokens": None, "baseline_runs": 4}
    _write_run(tmp_path, "run_4", seconds=500.0)
    five = baselines.compute(tmp_path, "direct")
    assert five["baseline_runs"] == 5 and five["budget_seconds"] == 500.0
    assert five["budget_tokens"] == 1100.0          # input + output, cache reads apart


def test_tokens_only_from_runs_that_recorded_cache_reads(tmp_path):
    for i in range(5):
        _write_run(tmp_path, f"run_old{i}", tokens=(90000, 100))   # an old run's sum
    value = baselines.compute(tmp_path, "direct")
    assert value["budget_seconds"] is not None and value["budget_tokens"] is None


def _cells(contract_extra: dict, metrics: list[tuple[str, float]], *, caps=()):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"],
                **contract_extra}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}}]
    events += [{"kind": "metric", "producer": "cli", "at": "2026-10-22T00:00:10+00:00",
                "payload": {"name": n, "value": v}} for n, v in metrics]
    events += [{"kind": "metric", "producer": "cli", "at": "2026-10-22T00:00:11+00:00",
                "payload": {"name": f"cap_exceeded.{c}", "value": 1, "cap": c}} for c in caps]
    events.append({"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
                   "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                               "n_pass": 1, "n_fail": 0, "n_not_run": 0}})
    value = audit.build(run_id="run_x", projection=projection, events=events,
                        repos_isolated=1, contract=contract, open_checks={}, findings=[],
                        audit_through="COMPLETED", commits={"repo": "a" * 40})
    return value, audit.render(value, fmt="report")


MEASURED = [("input_tokens", 1000), ("output_tokens", 100), ("cache_read_tokens", 9_000_000),
            ("accounted_calls", 1)]


def test_no_line_is_na_and_says_how_far(tmp_path):
    value, report = _cells({"baseline_runs": 3}, MEASURED)
    speed, token = value["summary"]["speed"], value["summary"]["token_cost"]
    assert speed["verdict"] == "N/A" and token["verdict"] == "N/A", (speed, token)
    assert "no baseline yet (3/5 runs)" in report
    assert "cache reads, not counted" in report


def test_cache_reads_never_change_the_token_verdict():
    low, _ = _cells({"budget_tokens": 2000, "baseline_runs": 5}, MEASURED)
    assert low["summary"]["token_cost"]["verdict"] == "PASS", low["summary"]["token_cost"]
    assert low["summary"]["token_cost"]["total_tokens"] == 1100


def test_unmeasured_is_block_and_a_cap_is_block_without_a_line():
    none, _ = _cells({}, [])
    assert none["summary"]["token_cost"]["verdict"] == "BLOCK"
    capped, _ = _cells({}, MEASURED, caps=("stage_attempt",))
    assert capped["summary"]["speed"]["verdict"] == "BLOCK"


def test_partly_measured_without_a_line_stays_warn():
    partial = [("input_tokens", 1000), ("output_tokens", 100)]   # no transcript measure
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              {"kind": "handout", "at": "2026-10-22T00:00:01+00:00", "payload": {}},
              {"kind": "handout", "at": "2026-10-22T00:00:02+00:00", "payload": {}}]
    events += [{"kind": "metric", "producer": "hook", "at": "2026-10-22T00:00:10+00:00",
                "payload": {"name": n, "value": v}} for n, v in partial]
    value = audit.build(run_id="run_x", projection=projection, events=events, repos_isolated=1,
                    contract=contract, open_checks={}, findings=[],
                    audit_through="COMPLETED", commits={"repo": "a" * 40})
    assert value["summary"]["token_cost"]["verdict"] == "WARN", value["summary"]["token_cost"]


def test_measure_and_the_expert_record_keep_cache_reads_out_of_input(tmp_path, monkeypatch):
    from core import transcript_usage
    from core.run_protocol import hook_bridge, usage
    recorded: list[dict] = []

    class Store:
        class authority:
            @staticmethod
            def writer(_who):
                return None

        def read_events(self):
            return []

        def append_new(self, **kw):
            recorded.append(kw["payload"])

    monkeypatch.setattr(transcript_usage, "resolve_transcripts", lambda ids: ["t"])
    monkeypatch.setattr(transcript_usage, "usage_in_window", lambda *a, **k: {
        "input_tokens": 10, "cache_read_tokens": 5000, "cache_creation_tokens": 7,
        "output_tokens": 3, "responses": 2})
    usage.measure(Store(), transcript_ids=["x"], created_at="2026-10-22T00:00:00+00:00",
                  finished_at="2026-10-22T00:10:00+00:00")
    hook_bridge.record_usage(Store(), {"input_tokens": 10, "cache_read_tokens": 5000,
                                       "cache_creation_tokens": 7, "output_tokens": 3,
                                       "responses": 2}, completed_at="2026-10-22T00:10:00+00:00")
    by = {}
    for row in recorded:
        by.setdefault(row["name"], []).append(row["value"])
    assert by["input_tokens"] == [17, 17] and by["cache_read_tokens"] == [5000, 5000], by


def test_an_incomplete_time_without_a_line_stays_warn():
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}}]
    value = audit.build(run_id="run_x", projection=projection, events=events,
                        repos_isolated=1, contract=contract, open_checks={}, findings=[],
                        audit_through="COMPLETED", commits={"repo": "a" * 40})
    assert value["summary"]["speed"]["verdict"] == "WARN", value["summary"]["speed"]
