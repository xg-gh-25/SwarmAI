"""A stage attempt is judged on replay by the roster of the registry version that wrote it.

2.6.0 added FM-1 to TEST and compared every replayed attempt with today's roster, so a
run that reached TEST on 2.5.5 could no longer be read, continued or cancelled. Now
complete_stage stamps ``roster_version`` on each attempt, ``checks.roster_at`` gives
every version's roster, and an unstamped attempt (written before the stamp) is accepted
against the rosters of the versions that wrote none. A wrong check set is still refused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.run_protocol import checks, events as ev
from core.run_protocol import lifecycle as lc
from core.run_protocol.documents import complete_stage
from core.run_protocol.store import AuditStore
from tests.test_run_protocol_lifecycle import Stream

FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_v1_v2_5_5_run"

# The stage rosters of ROSTER_VERSION, frozen. When this test fails, a roster changed:
# raise checks.ROSTER_VERSION by one, record the OLD roster of each changed stage under
# the old version in checks._ROSTER_CHANGES, then update this table and the version.
FROZEN_VERSION = 2
FROZEN_ROSTERS = {
    ("bugfix", "evaluate"): {"RQ-1", "RQ-2", "RQ-4", "RQ-5", "RQ-6", "RQ-8"},
    ("bugfix", "think"): set(),
    ("bugfix", "plan"): {"DS-2a", "DS-4a", "DS-5", "DS-6", "DS-7", "DS-9a"},
    ("bugfix", "build"): {"BX-3"},
    ("bugfix", "review"): {"RV-4", "RV-5"},
    ("bugfix", "test"): {"FM-1", "TS-1", "TS-4"},
    ("bugfix", "reflect"): set(),
    ("full", "evaluate"): {"RQ-1", "RQ-2", "RQ-5", "RQ-6", "RQ-8"},
    ("full", "think"): set(),
    ("full", "plan"): {"DS-2a", "DS-5", "DS-6", "DS-7", "DS-9a"},
    ("full", "build"): {"BX-3"},
    ("full", "review"): {"RV-4", "RV-5"},
    ("full", "test"): {"FM-1", "TS-1", "TS-4"},
    ("full", "reflect"): set(),
    ("trivial", "evaluate"): {"RQ-1", "RQ-2", "RQ-5", "RQ-6", "RQ-8"},
    ("trivial", "plan"): {"DS-2a", "DS-5", "DS-7", "DS-9a"},
    ("trivial", "build"): {"BX-3"},
    ("trivial", "review"): {"RV-4", "RV-5"},
    ("trivial", "test"): {"FM-1", "TS-1", "TS-4"},
    ("trivial", "reflect"): set(),
    ("direct", "build"): {"BX-3"},
    ("direct", "review"): {"RV-4", "RV-5"},
    ("direct", "test"): {"FM-1", "TS-1"},
    ("direct", "reflect"): set(),
    ("research", "evaluate"): {"RQ-1", "RQ-2", "RQ-5", "RQ-6", "RQ-8"},
    ("research", "think"): set(),
    ("research", "reflect"): set(),
}


def _rostered_stages():
    """Every (profile, stage) whose attempt the fold judges by the registry (DELIVER and
    land attempts are judged by lifecycle's fixed sets)."""
    return sorted((profile, stage) for profile, stages in lc.PROFILE_STAGES.items()
                  for stage in stages if stage != "deliver")


def test_the_current_rosters_are_frozen_under_their_roster_version():
    current = {key: set(checks.roster_at(*key, checks.ROSTER_VERSION))
               for key in _rostered_stages()}
    changed = sorted(key for key in set(current) | set(FROZEN_ROSTERS)
                     if current.get(key) != FROZEN_ROSTERS.get(key))
    assert (checks.ROSTER_VERSION, changed) == (FROZEN_VERSION, []), (
        "a stage roster changed: raise checks.ROSTER_VERSION and record the old roster "
        f"of {changed} in checks._ROSTER_CHANGES, then refreeze this test")


def test_the_written_roster_is_the_validated_one_and_the_fixed_sets_are_frozen():
    for profile, stage in _rostered_stages():
        written = {spec.check_id for spec in checks.stage_checks(
            profile, stage, lc.document_variant(profile, stage))}
        assert written == set(checks.roster_at(profile, stage, checks.ROSTER_VERSION)), \
            (profile, stage)
    # land and DELIVER attempts are validated against these on replay: a change to one
    # strands recorded runs the same way, so it must be versioned before it lands
    assert (lc.LG_CHECKS, lc.NORMAL_LAND_CHECKS, lc.SKIP_LAND_CHECKS, lc.RF_CHECKS,
            lc.REPORT_ONLY) == (
        {"LG-1", "LG-3", "LG-4"}, {"LG-1", "LG-3", "LG-4", "DV-1"}, {"LG-3", "LG-4"},
        {"RF-1", "RF-2", "RF-3", "RF-4"}, {"DV-1"})


def test_version_1_is_the_roster_v2_5_5_recorded():
    """Every registry attempt of the run 2.5.5 itself wrote lists version 1's roster."""
    run_id = json.loads((FIXTURE / "fixture.json").read_text())["run_id"]
    log = FIXTURE / "audit" / "runs" / run_id / "events.jsonl"
    attempts = [json.loads(line) for line in log.read_text().splitlines()
                if '"kind":"stage_attempt"' in line.replace(" ", "")]
    assert [a["stage"] for a in attempts] == [
        "evaluate", "think", "plan", "build", "review", "test"]
    for attempt in attempts:
        assert "roster_version" not in attempt["payload"]
        assert set(attempt["payload"]["check_ids"]) == set(
            checks.roster_at("bugfix", attempt["stage"], 1)), attempt["stage"]
    assert checks.roster_at("bugfix", "test", 1) == {"TS-1", "TS-4"}
    assert checks.roster_at("direct", "test", 1) == {"TS-1"}


def test_an_unstamped_test_attempt_with_the_v2_5_5_roster_folds():
    stream = Stream("bugfix")
    stream.to_stage("test")
    opened = stream.attempt("test", ["TS-1", "TS-4"])
    stream.add("stage_complete", {"stage_attempt_event_id": opened["event_id"]},
               stage="test", attempt=opened["attempt"])
    state = stream.projection()
    assert (state["status"], state["stage"], state["stages"]["test"]) == (
        "active", "deliver", "completed")


def test_complete_stage_stamps_the_roster_version(tmp_path):
    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    context = checks.make_context(
        profile="bugfix", requirement_variant="full", design_variant="full",
        requirements={}, design={}, events=[], authority=authority)
    complete_stage(store, profile="bugfix", stage="think", variant=None,
                   context=context, at="2026-11-26T00:00:00+00:00", attempt=1)
    attempt = store.read_events()[0]
    assert attempt["kind"] == "stage_attempt"
    assert attempt["payload"] == {"check_ids": [], "roster_version": checks.ROSTER_VERSION}


def test_a_roster_change_after_the_run_was_created_does_not_break_it(monkeypatch):
    """The run records TEST under today's roster; a later version adds a TEST check (and
    records the old roster, as the registry comment says). The run still reads, and a
    new attempt must list the new roster."""
    stream = Stream("bugfix")
    stream.to_stage("test")
    today = sorted(checks.roster_at("bugfix", "test", checks.ROSTER_VERSION))
    opened = _stamped_attempt(stream, "test", today, checks.ROSTER_VERSION)
    stream.add("stage_complete", {"stage_attempt_event_id": opened["event_id"]},
               stage="test", attempt=opened["attempt"])
    before = checks.ROSTER_VERSION
    real = checks.stage_checks
    extra = checks.CheckSpec("ZZ-1", "validation_report", frozenset({"stage"}), ("test",),
                             frozenset({"test"}), frozenset({"bugfix"}), lambda _: (True, ""))

    def next_release(profile, stage, variant=None):
        specs = real(profile, stage, variant)
        return specs + (extra,) if (profile, stage) == ("bugfix", "test") else specs

    monkeypatch.setattr(checks, "stage_checks", next_release)
    monkeypatch.setattr(checks, "ROSTER_VERSION", before + 1)
    monkeypatch.setattr(checks, "_ROSTER_CHANGES", {
        **checks._ROSTER_CHANGES, before: {("bugfix", "test"): frozenset(today)}})
    assert stream.projection()["stage"] == "deliver"
    assert checks.roster_at("bugfix", "test", before + 1) == {*today, "ZZ-1"}
    # the old roster stamped with the new version is refused
    later = Stream("bugfix")
    later.to_stage("test")
    _stamped_attempt(later, "test", today, before + 1, fold=False)
    with pytest.raises(lc.LifecycleError, match="registry roster"):
        later.projection()


def _stamped_attempt(stream, stage, ids, version, *, fold=True):
    number = stream.projection()["stage_attempts"].get(stage, 0) + 1
    opened = stream.add("stage_attempt", {"check_ids": list(ids), "roster_version": version},
                        stage=stage, attempt=number, transition=False)
    if fold:
        for check_id in ids:
            stream.add("check", {"stage_attempt_event_id": opened["event_id"],
                                 "check_id": check_id, "passed": True,
                                 "detail": "deterministic result"})
    return opened


@pytest.mark.parametrize("ids,version", [
    (["TS-1", "TS-4"], 2),                  # version 2's TEST has FM-1: a check missing
    (["FM-1", "TS-1", "TS-4"], 1),          # version 1's TEST had no FM-1: one invented
    (["FM-1", "TS-1", "TS-4", "XX-9"], 2),  # an invented check
    (["XX-9"], None),                       # unstamped, no version ever had it
    (["TS-1"], None),                       # unstamped, a check missing
])
def test_a_test_attempt_with_a_wrong_check_set_is_refused(ids, version):
    stream = Stream("bugfix")
    stream.to_stage("test")
    payload = {"check_ids": ids}
    if version is not None:
        payload["roster_version"] = version
    stream.add("stage_attempt", payload, stage="test", attempt=1, transition=False)
    with pytest.raises(lc.LifecycleError, match="registry roster"):
        stream.projection()


@pytest.mark.parametrize("version", [0, 3, 99, True, "2"])
def test_an_unknown_roster_version_is_refused(version):
    stream = Stream("bugfix")
    stream.to_stage("test")
    with pytest.raises((lc.LifecycleError, ev.EventError)):
        stream.add("stage_attempt", {"check_ids": ["FM-1", "TS-1", "TS-4"],
                                     "roster_version": version},
                   stage="test", attempt=1, transition=False)
        stream.projection()


@pytest.mark.parametrize("stage", ["deliver", "land"])
def test_a_land_or_deliver_attempt_carrying_a_roster_version_is_refused(stage):
    """Their check sets are lifecycle's fixed sets: a roster_version there means nothing
    and is refused, like on_exhausted outside the stages it applies to."""
    stream = Stream("bugfix")
    stream.to_stage("deliver")
    ids = sorted(lc.RF_CHECKS) if stage == "deliver" else sorted(lc.NORMAL_LAND_CHECKS)
    stream.add("stage_attempt", {"check_ids": ids, "roster_version": checks.ROSTER_VERSION},
               stage=stage, attempt=1, transition=False)
    with pytest.raises(lc.LifecycleError, match="roster_version is only for"):
        stream.projection()
