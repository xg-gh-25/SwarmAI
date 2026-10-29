"""core/usage_accounting + the v18 usage_ledger (design 2026-10-29-usage-accounting §2-§4).

Questions: are the derived figures the one definition (billed = the four kinds, the
cache hit rate over the input side only); is each CLI result's row the increase over
the previous result of the same process (``model_usage`` is a running total — the
fixture is two real results of one process, 2026-10-29), per model, with a new process
counted whole and an idle model skipped; is a ``claude --print`` run's usage summed per
model (not the max); does a Bedrock call carry its four kinds and the feature it was
attributed to; is a row written once however often it is recorded; is the ledger in
the backup.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import database as database_module
from core import usage_accounting as ua


def mu(model="m", i=0, o=0, cr=0, cw=0, cost=0.0):
    return {model: {"inputTokens": i, "outputTokens": o, "cacheReadInputTokens": cr,
                    "cacheCreationInputTokens": cw, "costUSD": cost, "webSearchRequests": 0}}


def result(model_usage=None, total=None, usage=None):
    return SimpleNamespace(model_usage=model_usage, total_cost_usd=total, usage=usage)


# the two results of one real CLI process, 2026-10-29 (claude-sonnet-4-6 on Bedrock)
R1 = result(mu("sonnet", 3, 5, 0, 18815, 0.112974), 0.112974,
            {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 18815})
R2 = result(mu("sonnet", 6, 10, 18815, 19482, 0.1227045), 0.1227045,
            {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 18815, "cache_creation_input_tokens": 667})


def test_the_definition():
    row = {"input": 10, "output": 500, "cache_read": 900, "cache_write": 90}
    assert ua.billed(row) == 1500
    assert ua.comparable(row) == 600
    assert ua.cache_hit_rate(row) == 0.9            # 900 / (10 + 900 + 90): output is not input
    assert ua.cache_hit_rate({"output": 5}) is None


def test_a_result_is_the_increase_over_the_previous_result_of_its_process():
    t = ua.CliResults()
    (a,) = t.rows(R1, session_id="s", pid=1, surface="chat")
    (b,) = t.rows(R2, session_id="s", pid=1, surface="chat")
    assert {k: a[k] for k in ua.KINDS} == {"input": 3, "output": 5, "cache_read": 0, "cache_write": 18815}
    assert {k: b[k] for k in ua.KINDS} == {"input": 3, "output": 5, "cache_read": 18815, "cache_write": 667}
    # the second result's own usage agrees with the increase
    assert b["cache_write"] == R2.usage["cache_creation_input_tokens"]
    assert abs(a["cost_usd"] + b["cost_usd"] - 0.1227045) < 1e-12   # the rows add up to the process's total
    assert b["cli_cost_total"] == 0.1227045 and b["cost_source"] == "cli" and b["kind"] == "spend"
    assert a["dedup_key"] != b["dedup_key"]


def test_each_model_is_its_own_row_and_an_idle_model_none():
    t = ua.CliResults()
    first = t.rows(result({**mu("opus", 1, 10, 100, 50, 1.0), **mu("haiku", 2, 3, 0, 0, 0.01)}, 1.01),
                   session_id="s", pid=7, surface="chat")
    assert sorted(r["model"] for r in first) == ["haiku", "opus"]
    again = t.rows(result({**mu("opus", 2, 20, 300, 60, 1.5), **mu("haiku", 2, 3, 0, 0, 0.01)}, 1.51),
                   session_id="s", pid=7, surface="chat")
    assert [(r["model"], r["output"], r["cache_read"]) for r in again] == [("opus", 10, 200)]
    assert abs(again[0]["cost_usd"] - 0.5) < 1e-12


def test_a_lower_total_is_a_new_process_counted_whole():
    t = ua.CliResults()
    t.rows(result(mu("m", 5, 50, 1000, 100, 2.0), 2.0), session_id="s", pid=9, surface="chat")
    (r,) = t.rows(result(mu("m", 1, 4, 0, 30, 0.2), 0.2), session_id="s", pid=9, surface="chat")
    assert (r["output"], r["cache_write"], r["cost_usd"]) == (4, 30, 0.2)


def test_an_older_cli_without_model_usage_falls_back_to_usage_and_the_total():
    t = ua.CliResults()
    (a,) = t.rows(result(None, 1.0, {"input_tokens": 1, "output_tokens": 2}), session_id="s", pid=3,
                  surface="channel", default_model="opus")
    (b,) = t.rows(result(None, 1.25, {"output_tokens": 4, "cache_read_input_tokens": 10}), session_id="s", pid=3,
                  surface="channel", default_model="opus")
    assert (a["cost_usd"], b["cost_usd"], b["output"], b["cache_read"], b["model"], b["surface"]) == \
        (1.0, 0.25, 4, 10, "opus", "channel")


def test_a_print_run_is_summed_per_model_not_its_max():
    out = {"total_cost_usd": 0.9, "modelUsage": {**mu("opus", 10, 100, 1000, 0, 0.8), **mu("haiku", 50, 5, 0, 0, 0.1)}}
    rows = ua.rows_from_cli_json(out, surface="job", run_key="job-1:run-1", job_id="job-1")
    assert sorted((r["model"], r["input"], r["cost_usd"]) for r in rows) == [("haiku", 50, 0.1), ("opus", 10, 0.8)]
    assert all(r["job_id"] == "job-1" for r in rows)
    assert {r["dedup_key"] for r in ua.rows_from_cli_json(out, surface="job", run_key="job-1:run-1")} == \
        {r["dedup_key"] for r in rows}                       # the same run, the same rows
    assert ua.rows_from_cli_json({"usage": {}}, surface="job") == []


def test_a_bedrock_call_carries_its_kinds_and_its_feature():
    usage = {"inputTokens": 100, "outputTokens": 20, "cacheReadInputTokens": 400, "cacheWriteInputTokens": 50}
    with ua.attribute("judge.session"):
        row = ua.bedrock_row(usage, model="sonnet")
    assert {k: row[k] for k in ua.KINDS} == {"input": 100, "output": 20, "cache_read": 400, "cache_write": 50}
    assert (row["feature"], row["surface"], row["cost_source"]) == ("judge.session", "background", "none")
    with ua.attribute("cdf.s2"):
        assert ua.bedrock_row(usage, model="sonnet")["surface"] == "cdf"
    assert ua.current_feature() is None


@pytest.mark.asyncio
async def test_a_row_is_written_once():
    t = ua.CliResults()
    rows = t.rows(R1, session_id="s1", pid=11, surface="chat")
    db = database_module.db
    assert await db.insert_usage_rows(rows) == 1
    assert await db.insert_usage_rows(rows) == 0             # a replay writes nothing
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        got = await (await conn.execute("SELECT surface, model, cache_write, cost_usd, kind FROM usage_ledger")).fetchall()
    assert got == [("chat", "sonnet", 18815, 0.112974, "spend")]


def test_the_ledger_is_in_the_backup():
    from core.backup_manager import L2_TABLES
    assert "usage_ledger" in L2_TABLES
