"""core/usage_report and the usage view's API (design
docs/design/2026-10-29-usage-accounting.md §5, §11c).

Questions: (1) is a window's total its spend rows only — attribution never added — with
the window before split at local midnight; (2) is a CLI result written as one row per
model one call, and are a turn's percentiles and cost table over those calls; (3) do
tokens with no price count as unpriced, not as $0 spent; (4) are the largest sessions
this host's chat and channel ones with their titles, the largest runs the attribution
rows, a Hive's runs grouped by its dispatch; (5) does the API refuse an unknown window
and a run id outside a project's runs; (6) is there no monthly budget anywhere.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core import usage_report as ur
from database.sqlite import _USAGE_LEDGER_DDL

NOW = datetime(2026, 10, 30, 12, 0).astimezone()
TODAY = NOW.date()
SINCE = ur._midnight(TODAY - timedelta(days=6))


def _at(iso_utc: str, seconds: int) -> str:
    return (datetime.fromisoformat(iso_utc) + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="seconds")


def _conn(rows):
    c = sqlite3.connect(":memory:")
    for stmt in _USAGE_LEDGER_DDL:
        c.execute(stmt)
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
    for i, r in enumerate(rows):
        base = dict(machine="host", surface="chat", kind="spend", imported=0, input=0, output=0,
                    cache_read=0, cache_write=0, cost_usd=None, dedup_key=f"k{i}")
        base.update(r)
        cols = ", ".join(base)
        c.execute(f"INSERT INTO usage_ledger({cols}) VALUES ({', '.join('?' for _ in base)})", list(base.values()))
    c.row_factory = sqlite3.Row
    return c


IN = _at(SINCE, 60)
BEFORE = _at(SINCE, -60)


def test_a_window_is_its_spend_rows_split_at_local_midnight():
    c = _conn([
        dict(at_utc=IN, cost_usd=2.0, cache_read=90, input=10, session_id="s1"),
        dict(at_utc=BEFORE, cost_usd=5.0, session_id="s1"),
        dict(at_utc=IN, kind="attribution", surface="pipeline", run_id="run_1", cost_usd=1.5, cache_read=50),
    ])
    r = ur.report("7d", c, NOW)
    assert r["total"]["cost_usd"] == 2.0 and r["total"]["cache_read"] == 90       # attribution not added
    assert r["previous"]["cost_usd"] == 5.0
    assert r["total"]["cache_hit_rate"] == 0.9
    assert r["attribution"]["cost_usd"] == 1.5 and r["attribution"]["runs"] == 1
    assert [d["in_window"] for d in r["daily"]] == [False] * 7 + [True] * 7
    assert sum(d["cost_usd"] for d in r["daily"] if d["in_window"]) == 2.0


def test_a_cli_result_is_one_call_and_turns_are_over_calls():
    rows = [dict(at_utc=IN, cost_usd=10.0, dedup_key="cli:n:s1:9:1:opus", session_id="s1", model="opus"),
            dict(at_utc=IN, cost_usd=6.0, dedup_key="cli:n:s1:9:1:haiku", session_id="s1", model="haiku")]
    rows += [dict(at_utc=IN, cost_usd=float(v), dedup_key=f"cli:n:s2:9:{i}:opus", session_id="s2") for i, v in enumerate((0.5, 2, 3))]
    rows.append(dict(at_utc=IN, surface="job", job_id="inbox", cost_usd=40.0))   # a job is not a turn
    r = ur.report("7d", c := _conn(rows), NOW)
    assert r["breakdown"]["surface"][0]["key"] == "job"
    chat = next(g for g in r["breakdown"]["surface"] if g["key"] == "chat")
    assert chat["calls"] == 4 and chat["rows"] == 5
    t = r["turns"]
    assert (t["turns"], t["p50"], t["p90"], t["max"]) == (4, 2.0, 16.0, 16.0)     # nearest rank
    assert [(b["turns"], b["cost_usd"]) for b in t["buckets"]] == [(1, 0.5), (2, 5.0), (0, 0), (1, 16.0)]
    assert r["concentration"]["big_turns"] == 1 and r["concentration"]["big_turns_cost_share"] == round(16 / 21.5, 4)
    c.close()


def test_a_hive_result_is_one_turn_and_its_session_is_not_a_host_session():
    rows = [dict(at_utc=IN, machine="demo", session_id="hs", model=m, cost_usd=v, dedup_key=f"hive:h1:{i}")
            for i, (m, v) in enumerate((("opus", 4.0), ("haiku", 1.0)), 1)]
    rows.append(dict(at_utc=_at(IN, 30), machine="demo", session_id="hs", cost_usd=2.0, dedup_key="hive:h1:3"))
    rows.append(dict(at_utc=IN, cost_usd=1.0, session_id="s1"))
    r = ur.report("7d", _conn(rows), NOW)
    assert r["turns"]["turns"] == 3 and r["turns"]["max"] == 5.0
    assert [s["key"] for s in r["top"]["sessions"]] == ["s1"]
    assert r["top"]["hive"][0]["calls"] == 2


def test_tokens_with_no_price_are_unpriced_not_spent():
    r = ur.report("7d", _conn([dict(at_utc=IN, surface="cdf", feature="cdf.s2", input=1000, output=100)]), NOW)
    cdf = r["breakdown"]["surface"][0]
    assert (cdf["cost_usd"], cdf["unpriced_calls"], cdf["unpriced_tokens"]) == (0, 1, 1100)
    assert r["gaps"]["unpriced"] == [{"surface": "cdf", "calls": 1, "tokens": 1100}]


def test_the_largest_sessions_runs_and_hive_runs():
    c = _conn([
        dict(at_utc=IN, cost_usd=9.0, session_id="s1"),
        dict(at_utc=IN, cost_usd=1.0, session_id="s2", surface="channel"),
        dict(at_utc=IN, cost_usd=50.0, session_id="s3", surface="job", job_id="j"),          # not a turn session
        dict(at_utc=IN, kind="attribution", surface="pipeline", run_id="run_1", cost_usd=3.0),
        dict(at_utc=IN, machine="demo", cost_usd=4.0, session_id="hs", dispatch_id="so_1"),
        dict(at_utc=IN, machine="demo", cost_usd=1.0, session_id="hs", dispatch_id="so_1", dedup_key="hive:h:2"),
    ])
    c.execute("INSERT INTO sessions VALUES ('s1', 'tidy TECH')")
    r = ur.report("7d", c, NOW, session_titles=lambda ids: {k: v for k, v in c.execute(
        f"SELECT id, title FROM sessions WHERE id IN ({','.join('?' for _ in ids)})", ids)},
        run_info=lambda rid: {"title": "the req", "report_path": f"P/{rid}/REPORT.md"},
        dispatch_titles={"so_1": "sent task"})
    top = r["top"]
    assert [(s["key"], s["title"]) for s in top["sessions"]] == [("s1", "tidy TECH"), ("s2", None)]       # a Hive session is under "hive"
    assert (top["runs"][0]["key"], top["runs"][0]["title"], top["runs"][0]["report_path"]) == ("run_1", "the req", "P/run_1/REPORT.md")
    assert [(h["key"], h["machine"], h["cost_usd"], h["title"]) for h in top["hive"]] == [("so_1", "demo", 5.0, "sent task")]
    assert top["jobs"][0]["key"] == "j"
    assert r["gaps"]["hives_reporting"] == ["demo"]


def test_the_api_refuses_an_unknown_window_and_a_run_id_outside_runs(tmp_path):
    from fastapi import HTTPException
    from routers import usage
    with pytest.raises(HTTPException) as e:
        asyncio.run(usage.usage_report_view(window="1y"))
    assert e.value.status_code == 400
    leak = tmp_path / "Projects" / "P" / ".artifacts" / "x"            # outside runs/
    leak.mkdir(parents=True)
    (leak / "run.json").write_text('{"requirement": "not a run"}')
    (tmp_path / "Projects" / "P" / ".artifacts" / "runs").mkdir()
    assert usage._run_info(str(tmp_path), "../x") is None


def test_there_is_no_monthly_budget():
    """No budget setting anywhere: the scheduler holds no job to a monthly spend, the
    API has no budget route, the report no budget (the user's decision, 2026-10-30)."""
    from jobs import executor
    from jobs.models import SchedulerDefaults
    from routers import usage
    assert not hasattr(executor, "_check_monthly_budget")
    assert "max_monthly_spend_usd" not in SchedulerDefaults.model_fields
    assert not [r for r in usage.router.routes if "budget" in r.path]
    assert "budget" not in ur.report("7d", _conn([]), NOW)
