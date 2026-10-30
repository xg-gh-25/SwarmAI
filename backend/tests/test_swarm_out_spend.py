"""A Swarm Out run's tokens, cost and model, and the model a dispatched task runs on.

The run card used to add up the CLI result's ``usage`` (the main conversation only)
next to the CLI's process cost (sub-agents included): a run whose sub-agents did most
of the work showed a fifth of its tokens beside its whole cost. The card now reads the
usage ledger's rows for the dispatch (core/swarm_out_results.spend_of); and a dispatch
first makes the Hive's default model this host's (core/swarm_out_model).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import pytest

from core import swarm_out_model, swarm_out_results as res
from core.swarm_out_results import summarize

DID = "so_0123456789ab"


AT = "2026-10-30T05:40:00+00:00"


def _ledger(rows):
    """Rows (kind, dispatch_id, model, input, output, cache_read, cache_write, cost[, at_utc])."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage_ledger (kind TEXT, dispatch_id TEXT, model TEXT, input INT, output INT, "
                 "cache_read INT, cache_write INT, cost_usd REAL, at_utc TEXT)")
    conn.executemany("INSERT INTO usage_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [r if len(r) == 9 else (*r, AT) for r in rows])
    return conn


def _record(cli_cost_total, usage, ended="completed"):
    """A kept record of one turn whose outcome carries the CLI's ``usage`` and total."""
    turn = [{"role": "user", "content": [{"type": "text", "text": "go"}], "created_at": "2026-10-30T05:25:00"},
            {"role": "assistant", "created_at": "2026-10-30T05:40:00", "content": [
                {"type": "text", "text": "done"},
                {"type": "turn_outcome", "ended": ended, "at": AT,
                 "cli_cost_total": cli_cost_total, "cli_pid": 7, "usage": usage}]}]
    return {"result": summarize(turn)}


# the run measured on 2026-10-30: the main conversation's usage vs the ledger's rows
MAIN = {"input_tokens": 53, "output_tokens": 38_593, "cache_read_input_tokens": 5_415_316,
        "cache_creation_input_tokens": 183_200}
LEDGER = [("spend", DID, "us.anthropic.claude-opus-4-6-v1[1m]", 2751, 200_000, 20_000_000, 1_500_000, 30.0),
          ("spend", DID, "us.anthropic.claude-opus-4-6-v1[1m]", 0, 58_432, 6_305_869, 549_112, 10.118609),
          ("attribution", DID, "us.anthropic.claude-opus-4-6-v1[1m]", 9, 9, 9, 9, 99.0),
          ("spend", "so_ffffffffffff", "us.anthropic.claude-opus-5-5[1m]", 1, 1, 1, 1, 1.0)]


def test_the_card_shows_the_ledgers_tokens_beside_the_same_cost():
    spend = res.spend_of([DID], _ledger(LEDGER))[DID]
    pub = res.public(_record(40.118609, MAIN), spend=spend)
    assert pub["tokens"] == {"input": 2751, "output": 258_432, "cache_read": 26_305_869, "cache_write": 2_049_112}
    assert pub["tokens_partial"] is False
    assert pub["cost_usd"] == pytest.approx(40.118609) and pub["cost_partial"] is False
    assert pub["models"] == ["claude-opus-4-6"]


def test_before_the_ledger_rows_arrive_the_tokens_are_a_lower_bound():
    pub = res.public(_record(40.118609, MAIN), spend=None)
    assert pub["tokens"]["cache_read"] == 5_415_316 and pub["tokens_partial"] is True
    assert pub["cost_usd"] == pytest.approx(40.118609) and pub["models"] is None


def test_a_ledger_that_has_not_caught_up_never_lowers_a_count():
    """Only the first of the run's rows has arrived: its cost is short of the CLI's
    total, so the record's cost stands, and each kind is the larger of the two."""
    spend = res.spend_of([DID], _ledger(LEDGER[2:3] + [LEDGER[0][:3] + (0, 10, 1_000, 0, 1.0)]))[DID]
    pub = res.public(_record(40.118609, MAIN), spend=spend)
    assert pub["tokens_partial"] is True and pub["cost_usd"] == pytest.approx(40.118609)
    assert pub["tokens"]["cache_read"] == 5_415_316 and pub["tokens"]["output"] == 38_593


def test_a_run_not_ended_yet_keeps_its_tokens_a_lower_bound():
    """The ledger may hold every row so far, but a run still going adds more."""
    spend = res.spend_of([DID], _ledger(LEDGER))[DID]
    pub = res.public(_record(40.118609, MAIN, ended="paused"), spend=spend)
    assert pub["tokens_partial"] is True and pub["cost_partial"] is True
    assert pub["tokens"]["cache_read"] == 26_305_869            # the larger count, never lowered


def test_rows_after_the_runs_recorded_end_are_a_later_turn_and_left_out():
    later = ("spend", DID, "us.anthropic.claude-opus-4-6-v1[1m]", 0, 999, 999, 999, 99.0, "2026-10-30T07:00:00+00:00")
    got = res.spend_of([DID], _ledger(LEDGER + [later]), until={DID: AT})[DID]
    assert got["rows"] == 2 and got["cost_usd"] == pytest.approx(40.118609)
    assert res.spend_of([DID], _ledger(LEDGER + [later]))[DID]["rows"] == 3   # no end known: all


def test_a_kind_the_record_never_counted_stays_unknown_unless_the_ledger_has_it():
    rec = _record(40.0, MAIN)
    rec["result"]["tokens"] = {"input": 53, "output": 38_593}        # kept before the cache was counted
    spend = res.spend_of([DID], _ledger([("spend", DID, "us.anthropic.claude-opus-4-6-v1[1m]", 1, 10, 0, 0, 1.0)]))[DID]
    pub = res.public(rec, spend=spend)
    assert pub["tokens"]["cache_read"] is None and pub["tokens"]["cache_write"] is None
    assert pub["tokens"]["output"] == 38_593 and pub["tokens_partial"] is True


def test_the_ledger_counts_only_spend_rows_of_that_dispatch():
    got = res.spend_of([DID, "not-an-id"], _ledger(LEDGER))
    assert set(got) == {DID} and got[DID]["rows"] == 2
    assert got[DID]["cost_usd"] == pytest.approx(40.118609)


def test_a_model_the_registry_does_not_know_is_other():
    got = res.spend_of([DID], _ledger([("spend", DID, "<b>made-up</b>", 1, 1, 1, 1, 0.1)]))
    assert got[DID]["models"] == ["other"]
    assert "made-up" not in json.dumps(res.public(_record(0.1, MAIN), spend=got[DID]))


def test_an_unreadable_ledger_leaves_the_records_figures():
    conn = sqlite3.connect(":memory:")                 # no usage_ledger table
    assert res.spend_of([DID], conn) == {}


# ── the Hive runs this host's model ───────────────────────────────────────────

def _hive(settings, put_status=200, put_takes=True, can_call=True):
    seen: list[tuple[str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, body))
        if request.method == "GET":
            return httpx.Response(200, json=settings)
        if request.url.path == "/api/system/verify-auth":
            return httpx.Response(200, json={"success": can_call, "model": body["default_model"] if can_call else None,
                                             "error": "<injected> Bedrock said no"})
        out = {**settings, **(body or {})} if put_takes else settings
        return httpx.Response(put_status, json=out)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://h")
    return client, seen


def test_a_hive_on_another_model_is_moved_to_this_hosts():
    client, seen = _hive({"default_model": "claude-opus-4-6",
                          "available_models": ["claude-opus-4-8", "claude-opus-4-6", "claude-sonnet-4-6"]})
    got = asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5"))
    assert got == "claude-opus-5-5"
    assert seen[1] == ("POST", {"default_model": "claude-opus-5-5"})          # checked first, nothing saved
    assert seen[2] == ("PUT", {"default_model": "claude-opus-5-5",
                               "available_models": ["claude-opus-5-5", "claude-opus-4-8", "claude-opus-4-6",
                                                    "claude-sonnet-4-6"]})


def test_a_hive_already_on_this_hosts_model_is_not_written():
    client, seen = _hive({"default_model": "claude-opus-5-5", "available_models": ["claude-opus-5-5"]})
    assert asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5")) == "claude-opus-5-5"
    assert [m for m, _ in seen] == ["GET"]


@pytest.mark.parametrize("status, takes", [(400, False), (200, False)])
def test_a_hive_that_does_not_take_the_model_stops_the_dispatch(status, takes):
    client, _ = _hive({"default_model": "claude-opus-4-6", "available_models": []}, status, takes)
    with pytest.raises(swarm_out_model.ModelAlignError, match="did not take claude-opus-5-5"):
        asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5"))


def test_a_hive_that_cannot_call_the_model_is_left_as_it_was():
    client, seen = _hive({"default_model": "claude-opus-4-6", "available_models": ["claude-opus-4-6"]}, can_call=False)
    with pytest.raises(swarm_out_model.ModelAlignError, match="cannot call claude-opus-5-5") as e:
        asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5"))
    assert "PUT" not in [m for m, _ in seen] and "injected" not in str(e.value)


def test_the_hives_own_list_is_kept_whole_and_its_text_stays_out_of_errors():
    many = [f"claude-opus-4-{i}" for i in range(40)]
    client, seen = _hive({"default_model": "<b>x</b>", "available_models": many}, put_status=400, put_takes=False)
    with pytest.raises(swarm_out_model.ModelAlignError) as e:
        asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5"))
    assert seen[2][1]["available_models"] == ["claude-opus-5-5", *many]
    assert "<b>" not in str(e.value)


def test_unreadable_settings_stop_the_dispatch():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)), base_url="https://h")
    with pytest.raises(swarm_out_model.ModelAlignError, match="HTTP 503"):
        asyncio.run(swarm_out_model.align(client, "explorer", "claude-opus-5-5"))


def test_a_host_without_a_registry_model_changes_nothing(monkeypatch):
    monkeypatch.setattr(swarm_out_model, "host_model", lambda: None)
    client, seen = _hive({"default_model": "claude-opus-4-6"})
    assert asyncio.run(swarm_out_model.align(client, "explorer")) is None and seen == []


# ── the dispatch itself ───────────────────────────────────────────────────────

INST = {"id": "h1", "name": "worker-1", "status": "running", "cloudfront_domain": "abc123.cloudfront.net",
        "s3_bucket": "swarmai-hive-2361-us-east-1", "region": "us-east-1",
        "auth_user": "admin", "auth_password": "s3cret-pass", "account_ref": "a1"}


def _dispatching_hive(monkeypatch, tmp_path, *, takes=True, busy=False):
    from core import swarm_out
    seen: list[tuple[str, str]] = []
    settings = {"default_model": "claude-opus-4-6", "available_models": ["claude-opus-4-6"]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/settings":
            if request.method == "PUT" and takes:
                settings.update(json.loads(request.content))
            return httpx.Response(200, json=settings)
        if request.url.path == "/api/system/verify-auth":
            return httpx.Response(200, json={"success": True, "model": json.loads(request.content)["default_model"]})
        if request.url.path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json={"saturated": busy})
        if request.url.path == "/api/chat/stream":
            return httpx.Response(200, text='data: {"type": "session_start", "session_id": "sess-42"}\n\n',
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url=swarm_out._base(inst), transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(swarm_out_model, "host_model", lambda: "claude-opus-5-5")
    return swarm_out, seen, settings


def test_a_dispatch_sets_the_hives_model_before_it_sends_the_task(monkeypatch, tmp_path):
    swarm_out, seen, settings = _dispatching_hive(monkeypatch, tmp_path)
    row = asyncio.run(swarm_out.dispatch("worker-1", "Dive deep", "do X"))
    assert row["session_id"] == "sess-42" and settings["default_model"] == "claude-opus-5-5"
    assert seen.index(("PUT", "/api/settings")) < seen.index(("POST", "/api/chat/stream"))


def test_a_hive_that_refuses_the_model_gets_no_task(monkeypatch, tmp_path):
    swarm_out, seen, _ = _dispatching_hive(monkeypatch, tmp_path, takes=False)
    with pytest.raises(swarm_out.SwarmOutError, match="the task was not sent"):
        asyncio.run(swarm_out.dispatch("worker-1", "Dive deep", "do X"))
    assert ("POST", "/api/chat/stream") not in seen
    assert not (tmp_path / "dispatches.jsonl").exists()


def test_a_busy_hive_keeps_its_model(monkeypatch, tmp_path):
    swarm_out, seen, settings = _dispatching_hive(monkeypatch, tmp_path, busy=True)
    with pytest.raises(swarm_out.SwarmOutError, match="busy"):
        asyncio.run(swarm_out.dispatch("worker-1", "Dive deep", "do X"))
    assert settings["default_model"] == "claude-opus-4-6" and ("PUT", "/api/settings") not in seen
