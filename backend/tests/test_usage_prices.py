"""core/usage_prices (design 2026-10-29-usage-accounting §6, §10 invariant 9).

Questions: does a model's fit recover the prices that made its rows (the measured
Sonnet call: 3 / 15 / 0.30 / 6.00 per million, a 1-hour cache write); does a fit that
does not explain its rows give none, not a wrong price; is a row that used a kind with
no price left unpriced; does the writer price a direct call with no cost.
"""
from __future__ import annotations

import random

from core import usage_prices as up
from core import usage_accounting as ua

P = {"input": 3e-6, "output": 15e-6, "cache_read": 0.3e-6, "cache_write": 6e-6}


def _rows(n=40, seed=1, noise=0.0, kinds=up.KINDS):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        t = {k: (rnd.randint(0, 50_000) if k in kinds else 0) for k in up.KINDS}
        cost = sum(t[k] * P[k] for k in up.KINDS) * (1 + rnd.uniform(-noise, noise))
        out.append((t["input"], t["output"], t["cache_read"], t["cache_write"], cost))
    return out


def test_a_fit_recovers_the_prices_that_made_the_rows():
    f = up.fit(_rows())
    assert {k: round(v * 1e6, 4) for k, v in f.items()} == {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 6.0}


def test_the_measured_call_is_priced_to_the_cent():
    # the two results of the real process (R1 / R2 in test_usage_accounting)
    p = {"m": {k: v for k, v in P.items()}}
    assert round(up.price(p, "m", {"input": 3, "output": 5, "cache_write": 18815}), 6) == 0.112974
    assert round(up.price(p, "m", {"input": 3, "output": 5, "cache_read": 18815, "cache_write": 667}), 7) == 0.0097305


def test_a_fit_that_does_not_explain_its_rows_is_none():
    assert up.fit(_rows(noise=0.5)) is None                 # costs the kinds cannot explain
    assert up.fit(_rows(n=3)) is None                       # too few rows


def test_a_kind_never_used_has_no_price_and_a_row_using_it_none():
    f = up.fit(_rows(kinds=("input", "output")))
    assert set(f) == {"input", "output"}
    assert up.price({"m": f}, "m", {"input": 10, "cache_read": 5}) is None
    assert up.price({}, "unknown-model", {"input": 10}) is None
    assert round(up.price({}, "us.anthropic.claude-sonnet-4-6", {"input": 1_000_000}), 6) == 3.0


def test_the_writer_prices_a_direct_call_with_no_cost(monkeypatch):
    monkeypatch.setattr(up, "current", lambda db_path=None: {"m": P})
    w = ua._Writer()
    batch = [ua.bedrock_row({"inputTokens": 1000, "outputTokens": 100}, model="m", feature="job.x"),
             ua.bedrock_row({"inputTokens": 1000}, model="nobody-knows", feature="job.x")]
    w._price(batch)
    assert (round(batch[0]["cost_usd"], 6), batch[0]["cost_source"]) == (0.0045, "priced")
    assert (batch[1]["cost_usd"], batch[1]["cost_source"]) == (None, "none")


def _ledger(rows_by_model):
    """A usage ledger with the CLI's priced rows of each model id."""
    import sqlite3
    import time
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage_ledger (id INTEGER PRIMARY KEY, model TEXT, input INT, output INT, "
                 "cache_read INT, cache_write INT, cost_usd REAL, cost_source TEXT, imported INT, kind TEXT, "
                 "at_utc TEXT)")
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    for model, rows in rows_by_model.items():
        for i, o, cr, cw, c in rows:
            conn.execute("INSERT INTO usage_ledger (model, input, output, cache_read, cache_write, cost_usd, "
                         "cost_source, imported, kind, at_utc) VALUES (?,?,?,?,?,?,'cli',0,'spend',?)",
                         (model, i, o, cr, cw, c, now))
    return conn


def test_a_short_model_id_is_priced_at_its_short_name_fit():
    # the CLI prices the full id; a pipeline run's transcript records the short one
    prices = up.learn(_ledger({"us.anthropic.claude-opus-5-5[1m]": _rows()}))
    row = {"input": 1_000_000}
    assert up.price(prices, "claude-opus-5-5", row) == __import__("pytest").approx(3.0)
    assert up.price(prices, "eu.anthropic.claude-opus-5-5[1m]", row) == __import__("pytest").approx(3.0)
    # a full id of the standard tier is not priced at a fit only the [1m] tier taught
    assert up.price(prices, "us.anthropic.claude-opus-5-5", row) is None
    # a model id with its own fit uses it, never the short name's
    own = {**prices, "claude-opus-5-5": {"input": 9e-6}}
    assert up.price(own, "claude-opus-5-5", row) == __import__("pytest").approx(9.0)
    # neither its own fit, a short-name fit nor a fallback price: unpriced
    assert up.price(prices, "claude-unknown-9", row) is None
    assert up.price(prices, None, row) is None


def test_a_short_name_fit_obeys_the_fit_rule():
    # two ids of one short name, each with its own fit, priced the same: one short fit
    same = up.learn(_ledger({"us.anthropic.claude-opus-5-5[1m]": _rows(seed=2),
                             "us.anthropic.claude-opus-5-5": _rows(seed=3)}))
    assert all(up.short_key("claude-opus-5-5", t) in same for t in ("", "[1m]", "std"))
    # an id of the short name without enough rows for a fit of its own: no short fit
    thin = up.learn(_ledger({"us.anthropic.claude-opus-5-5[1m]": _rows(seed=2),
                             "us.anthropic.claude-opus-5-5": _rows(n=4, seed=3)}))
    assert up.short_key("claude-opus-5-5") not in thin
    # priced differently (a context-window tier at twice the price): no short fit
    double = [(i, o, cr, cw, c * 2) for i, o, cr, cw, c in _rows(seed=5)]
    split = up.learn(_ledger({"us.anthropic.claude-opus-5-5[1m]": double,
                              "us.anthropic.claude-opus-5-5": _rows(seed=6)}))
    assert up.short_key("claude-opus-5-5") not in split
    assert up.price(split, "claude-opus-5-5", {"input": 1000}) is None
    # each id keeps its own fit
    assert "us.anthropic.claude-opus-5-5[1m]" in split and "us.anthropic.claude-opus-5-5" in split
    # only the standard tier priced: a [1m] id with no fit of its own is not priced at it,
    # a tierless id is
    std = up.learn(_ledger({"us.anthropic.claude-opus-5-5": _rows(seed=7)}))
    assert up.price(std, "eu.anthropic.claude-opus-5-5[1m]", {"input": 1000}) is None
    assert up.price(std, "claude-opus-5-5", {"input": 1_000_000}) == __import__("pytest").approx(3.0)
    assert up.price(std, "eu.anthropic.claude-opus-5-5", {"input": 1_000_000}) == __import__("pytest").approx(3.0)
