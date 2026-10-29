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


@pytest.mark.asyncio
async def test_the_accounting_call_writes_both_once_per_result():
    """The orchestrator's accounting call: the ledger gets the result's rows, per model
    and as the process's increase, the legacy token_usage row is still written for older
    readers, and a result seen by a second path is not counted again. (That the real
    result paths reach it: test_context_warning_bridge.TestUsageLedgerOnResultPaths.)"""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from core.streaming_orchestrator import StreamingOrchestrator
    parent = SimpleNamespace(session_id="sess-o", pid=4242, is_channel_session=True, _model_name="opus")
    orch = StreamingOrchestrator(parent)
    ua.cli_results._last.clear()
    import database
    r1, r2 = (result(m.model_usage, m.total_cost_usd, m.usage) for m in (R1, R2))
    rows: list = []
    with patch("core.usage_accounting.record_sync", lambda r: rows.extend(r)), \
            patch.object(database.db, "record_token_usage", AsyncMock(return_value=None)) as legacy:
        orch._record_token_usage_bg(r1, r1.usage)
        orch._record_token_usage_bg(r2, r2.usage)
        orch._record_token_usage_bg(r2, r2.usage)          # another path seeing the same result
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert [(r["surface"], r["session_id"], r["cli_pid"], r["cache_write"]) for r in rows] == \
        [("channel", "sess-o", 4242, 18815), ("channel", "sess-o", 4242, 667)]
    assert legacy.await_count == 2


def test_every_direct_model_call_names_its_feature():
    """Every call of jobs.bedrock.structured / text in the product passes feature=, so no
    model call reaches the ledger as 'unattributed' (design §10 invariant 6)."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    missing = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", ".venv/")) or "/tests/" in rel or rel.endswith("_test.py") or rel == "jobs/bedrock.py":
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "jobs.bedrock" not in src and "jobs import bedrock" not in src:
            continue
        tree = ast.parse(src)
        names, modules = set(), {"bedrock", "_bedrock"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "jobs.bedrock":
                names |= {a.asname or a.name for a in node.names if a.name in ("structured", "text", "converse_with_retry")}
            elif isinstance(node, ast.ImportFrom) and node.module == "jobs":
                modules |= {a.asname or a.name for a in node.names if a.name == "bedrock"}
            elif isinstance(node, ast.Import):
                modules |= {a.asname for a in node.names if a.name == "jobs.bedrock" and a.asname}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            receiver = f.value if isinstance(f, ast.Attribute) else None
            via_module = isinstance(receiver, ast.Name) and receiver.id in modules or (
                isinstance(receiver, ast.Attribute) and receiver.attr == "bedrock"
                and isinstance(receiver.value, ast.Name) and receiver.value.id == "jobs")
            called = f.id if isinstance(f, ast.Name) and f.id in names else (
                f.attr if isinstance(f, ast.Attribute) and f.attr in ("structured", "text", "converse_with_retry")
                and via_module else None)
            # a **kwargs spread may carry it; only an explicit call without it is reported
            if called and not any(k.arg in ("feature", None) for k in node.keywords):
                missing.append(f"{rel}:{node.lineno} {called}()")
    assert missing == [], missing


def test_a_bedrock_answer_is_written_to_the_ledger_with_its_feature(monkeypatch):
    from jobs import bedrock
    seen = []
    monkeypatch.setattr("core.usage_accounting.record_sync", lambda rows: seen.extend(rows))
    reply = {"output": {"message": {"content": [{"text": "hi"}]}}, "stopReason": "end_turn",
             "usage": {"inputTokens": 7, "outputTokens": 3, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}}
    client = SimpleNamespace(converse=lambda **kw: reply)
    monkeypatch.setattr(bedrock, "get_client", lambda **kw: client)
    assert bedrock.text("q", model_id="m", max_tokens=10, feature="eval.judge") == "hi"
    assert [(r["feature"], r["surface"], r["input"], r["output"], r["model"]) for r in seen] == [("eval.judge", "eval", 7, 3, "m")]


def _ledger_db(path):
    import sqlite3
    from database.sqlite import _USAGE_LEDGER_DDL
    with sqlite3.connect(path) as conn:
        for stmt in _USAGE_LEDGER_DDL:
            conn.execute(stmt)


def test_the_writer_thread_writes_queued_rows_once_and_never_waits_the_caller(tmp_path, monkeypatch):
    """Rows from any thread, with or without a loop, go to one writer thread: the caller
    returns at once, no event loop is made, a replayed row is not written twice."""
    import sqlite3
    import threading
    db_file = tmp_path / "u.db"
    _ledger_db(db_file)
    w = ua._Writer()
    w._db_path = str(db_file)
    monkeypatch.setattr(ua, "_writer", w)
    monkeypatch.setattr("asyncio.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asyncio.run used")))
    rows = [ua.bedrock_row({"inputTokens": 1, "outputTokens": 2}, model="m", feature="job.x")]
    t = threading.Thread(target=ua.record_sync, args=(rows,))
    t.start(); t.join()
    ua.record_sync(rows)
    assert ua.flush(5)
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT feature, surface, input, output FROM usage_ledger").fetchall() == [("job.x", "job", 1, 2)]
    assert ua.stats()["written"] == 2 and ua.stats()["lost"] == 0


def test_the_writer_never_creates_a_database_and_counts_what_it_lost(tmp_path, monkeypatch):
    missing = tmp_path / "none.db"
    w = ua._Writer()
    w._db_path = str(missing)
    monkeypatch.setattr(ua, "_writer", w)
    ua.record_sync([ua.bedrock_row({"inputTokens": 1}, model="m", feature="job.x")])
    assert ua.flush(10)
    assert not missing.exists()
    assert ua.stats()["lost"] == 1


def test_a_full_queue_drops_and_counts_instead_of_blocking(monkeypatch):
    w = ua._Writer()
    monkeypatch.setattr(w, "_start", lambda: None)          # no writer: the queue only fills
    monkeypatch.setattr(w, "MAX_QUEUED", 2)
    import queue as _q
    w._q = _q.Queue(2)
    w.put([{"dedup_key": str(i)} for i in range(5)])
    assert (w._q.qsize(), w.dropped) == (2, 3)


def test_keys_never_repeat_across_trackers_and_a_new_pid_is_a_new_process():
    a, b = ua.CliResults(), ua.CliResults()                # a daemon restart is a new tracker
    k1 = a.rows(R1, session_id="s", pid=None, surface="chat")[0]["dedup_key"]
    k2 = b.rows(R1, session_id="s", pid=None, surface="chat")[0]["dedup_key"]
    assert k1 != k2
    t = ua.CliResults()
    t.rows(result(mu("m", 5, 50, 1000, 100, 2.0), 2.0), session_id="s", pid=1, surface="chat")
    # a new process whose first total is above the old one's: counted whole, not as a difference
    (r,) = t.rows(result(mu("m", 9, 60, 2000, 200, 3.0), 3.0), session_id="s", pid=2, surface="chat")
    assert (r["output"], r["cost_usd"], r["cli_pid"]) == (60, 3.0, 2)
    # a result with no pid (the process already gone) continues the session's last process
    (r,) = t.rows(result(mu("m", 10, 70, 2000, 200, 3.5), 3.5), session_id="s", pid=None, surface="chat")
    assert (r["output"], r["cost_usd"], r["cli_pid"]) == (10, 0.5, 2)


def test_a_failed_agent_job_still_records_what_it_spent(monkeypatch):
    """An agent job that exits non-zero spent tokens too: its CLI JSON output reaches the
    ledger before the failure returns, summed per model, under the job's id."""
    import json as _json
    from jobs import executor
    from jobs.models import Job, JobType, SchedulerState
    seen = []
    monkeypatch.setattr("core.usage_accounting.record_sync", lambda rows: seen.extend(rows))
    for name, value in {"_resolve_claude_cli": lambda: "claude", "_check_claude_auth": lambda p: None,
                        "_get_aws_credentials": lambda: {}, "_cli_version": lambda p: "2.1.300",
                        "_cli_supports_bare": lambda p, v: False, "cli_too_old_message": lambda v, m: None,
                        "_load_mcp_config": lambda: {}, "_build_cli_env": lambda c=None: {}}.items():
        monkeypatch.setattr(executor, name, value)
    out = {"is_error": True, "total_cost_usd": 0.9,
           "modelUsage": {**mu("opus", 10, 100, 1000, 0, 0.8), **mu("haiku", 50, 5, 0, 0, 0.1)}}
    monkeypatch.setattr(executor.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=_json.dumps(out), stderr="boom"))
    job = Job(id="j1", name="j", type=list(JobType)[0], schedule="0 0 * * *", config={"prompt": "do it"})
    res = executor._handle_agent_task(job, SchedulerState())
    assert res.status == "failed"
    assert sorted((r["model"], r["surface"], r["job_id"], r["cost_usd"]) for r in seen) == \
        [("haiku", "job", "j1", 0.1), ("opus", "job", "j1", 0.8)]


def test_a_pipeline_run_is_an_attribution_per_model_replaced_by_its_later_measurement(tmp_path, monkeypatch):
    """A run's share of its host session (attribution rows) is one row per run and model,
    the REFLECT measurement replacing the DELIVER one, priced at the learned prices; the
    top bar's sum (spend rows only) never adds it."""
    import sqlite3
    from core.run_protocol import usage as run_usage
    from core import usage_prices
    db_file = tmp_path / "u.db"
    _ledger_db(db_file)
    w = ua._Writer()
    w._db_path = str(db_file)
    monkeypatch.setattr(ua, "_writer", w)
    monkeypatch.setattr(usage_prices, "current", lambda db_path=None: {"opus": {"input": 5e-6, "output": 25e-6,
                                                                               "cache_read": 0.5e-6, "cache_write": 6.25e-6}})
    store = SimpleNamespace(paths=SimpleNamespace(run_id="run_abc12345"))
    first = {"opus": {"input_tokens": 10, "output_tokens": 100, "cache_read_tokens": 1000, "cache_creation_tokens": 0}}
    later = {"opus": {"input_tokens": 20, "output_tokens": 200, "cache_read_tokens": 4000, "cache_creation_tokens": 0},
             "haiku": {"input_tokens": 5, "output_tokens": 5, "cache_read_tokens": 0, "cache_creation_tokens": 0}}
    run_usage._ledger_attribution(store, first, "deliver")
    cost = run_usage._ledger_attribution(store, later, "reflect")
    assert cost is None                                   # haiku has no price: the run's cost is not known
    assert ua.flush(5)
    with sqlite3.connect(db_file) as conn:
        got = conn.execute("SELECT model, stage, output, cache_read, kind, run_id, round(cost_usd, 6) FROM usage_ledger "
                           "ORDER BY model").fetchall()
        spend = conn.execute("SELECT COUNT(*) FROM usage_ledger WHERE kind='spend'").fetchone()[0]
    assert got == [("haiku", "reflect", 5, 0, "attribution", "run_abc12345", None),
                   ("opus", "reflect", 200, 4000, "attribution", "run_abc12345", 0.0071)]
    assert spend == 0
