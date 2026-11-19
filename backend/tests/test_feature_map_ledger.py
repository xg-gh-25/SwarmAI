"""Feature Map ledger: facts per Brain and repo, status, quarantine (R4.5, R5.1-R5.3)."""
from __future__ import annotations

from core.feature_map import ledger
from tests.feature_map_fixtures import app_data  # noqa: F401 — fixture

KEY = ledger.walk_key("home-a1b2", "opens", "regular")


def _line(result, *, h="h1", checkout="/main", trigger="replay", commit="c1", run=None, tree=None):
    return {"feature": "home-a1b2", "walk": "opens", "project": "regular", "key": KEY,
            "hash": h, "commit": commit, "clean": True, "code_tree": tree, "checkout": checkout,
            "trigger": trigger, "run": run, "result": result, "reason": "", "duration_s": 1.0,
            "evidence": ""}


def test_history_status_and_quarantine(app_data):  # noqa: F811
    # R5.1: every field of a line is written, under <app data>/feature_map/<brain>/<repo>/
    written = ledger.append("B", "app", [_line("pass", checkout="/main", commit="c1")])
    path = app_data / "feature_map" / "B" / "app" / "ledger.jsonl"
    assert path.is_file() and set(written[0]) >= {
        "at", "feature", "walk", "project", "key", "hash", "commit", "clean", "code_tree",
        "checkout", "trigger", "run", "result", "reason", "duration_s", "evidence"}
    # R5.3: a session worktree and a run worktree share one history
    ledger.append("B", "app", [_line("fail", checkout="/wt/run_x", commit="c2", run="run_x")])
    rows = ledger.read("B", "app")
    st = ledger.status(rows, KEY, "h1")
    assert st["last"]["result"] == "fail" and st["last"]["commit"] == "c2"
    # R5.2: the last pass is the commit it ran on, never a later one
    assert st["last_pass"]["commit"] == "c1" and not st["never_passed"]
    # an edited walk (new hash) starts its history over
    assert ledger.status(rows, KEY, "h2")["never_passed"] is True
    assert ledger.status(rows, KEY, "h2")["last"] is None
    # base replays are not the walk's own history
    ledger.append("B", "app", [_line("pass", trigger="base")])
    assert ledger.status(ledger.read("B", "app"), KEY, "h1")["last"]["result"] == "fail"


def test_quarantine_after_three_flakes_in_ten_released_by_five_passes(app_data):  # noqa: F811
    seq = ["pass", "flaky", "pass", "flaky", "pass", "pass"]
    ledger.append("B", "q", [_line(r) for r in seq])
    assert not ledger.quarantined(ledger.read("B", "q"), KEY, "h1")
    ledger.append("B", "q", [_line("flaky")])
    assert ledger.quarantined(ledger.read("B", "q"), KEY, "h1")
    ledger.append("B", "q", [_line("pass") for _ in range(4)])
    assert ledger.quarantined(ledger.read("B", "q"), KEY, "h1"), "4 passes do not release"
    ledger.append("B", "q", [_line("pass")])
    assert not ledger.quarantined(ledger.read("B", "q"), KEY, "h1"), "5 in a row release"
    # an edited walk is not quarantined by the old one's flakes
    assert not ledger.quarantined(ledger.read("B", "q"), KEY, "h2")


def test_unknown_result_is_refused(app_data):  # noqa: F811
    import pytest
    with pytest.raises(ValueError):
        ledger.append("B", "x", [_line("verified")])
