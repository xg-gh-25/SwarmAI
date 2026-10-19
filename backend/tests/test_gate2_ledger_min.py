"""Gate-2 findings ledger (the tool writes the record, the model settles it) and the
Red Team joining the round-2 batch instead of a wave of its own.

What is tested (artifact_cli on real marker files, as in test_gate2_convergence):
- ``_gate2_ledger_entries``: one entry per reported finding across every reviewer and
  round, verbatim, stable ids, ``path`` from ``path`` or ``file``, a re-report keeps one entry
- ``_expand_gate2_ledger``: dispositions merge in; an entry without one is recorded
  ``unaddressed`` and unresolved (never dropped); an unknown id or disposition is an
  error; findings the model wrote itself are kept; a non-ledger artifact is untouched
- the expanded findings satisfy the validator's own binding check
  (``_binding_findings`` + ``_match_one_to_one``) — the whole point of the ledger
- a finding reported again in a later round keeps the STRONGEST severity /
  confidence (the check holds the record to the first; never weaker)
- ``refuted`` is recorded "Not a bug: <evidence>" (the shape design_spec's
  fix-by-class rule exempts), the expert's words kept in ``reported_finding``
- ``_red_team_with_recheck``: a round-1 specialist HIGH adds the Red Team to the
  round-2 batch (a whole-change prompt); not when it already ran, nor without a HIGH;
  round 1's shared change.diff is left as it was; a stale red-team.md is rewritten
"""
import json
import time
from datetime import datetime, timezone

import pytest

from scripts import artifact_cli as cli

RID = "run_ledger1"


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"taken_at": None, "late": False}, None))
    run_dir = tmp_path / "runs" / RID
    (run_dir / "evidence" / "prompts").mkdir(parents=True)
    t0 = time.time() - 3600
    state = {"id": RID, "created_at": _iso(t0 - 600), "profile": "full", "requirement": "R",
             "stage_timing": {"deliver": {"started_at": _iso(t0)}}}
    return audit, run_dir, state, t0


def _marker(audit, n, expert, ts, reported, state="findings"):
    body = {"ts": ts, "event": "SubagentStop", "run_id": RID, "expert_id": expert,
            "reported": reported, "reported_state": state}
    (audit / f"session_{n}.marker").write_text(json.dumps(body))


HIGH = {"severity": "HIGH", "confidence": 8, "path": "a.py", "line": 3, "summary": "leaks the token"}
MED = {"severity": "MEDIUM", "confidence": 7, "file": "b.ts", "line": 9, "summary": "stale cache"}
LOW = {"severity": "LOW", "confidence": 5, "path": "c.py", "summary": "naming"}


def test_entries_from_every_reviewer_verbatim_with_stable_ids(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH, LOW])
    _marker(audit, 2, "api-contract", t0 + 11, [MED])
    _marker(audit, 3, "correctness", t0 + 20, [HIGH])   # round 2 reports it again
    entries, unparsed = cli._gate2_ledger_entries(RID, run_dir, state)
    assert unparsed == []
    assert [(e["specialist"], e["severity"]) for e in entries] == [
        ("correctness", "HIGH"), ("api-contract", "MEDIUM"), ("correctness", "LOW")]
    med = entries[1]
    assert med["path"] == "b.ts" and med["blocking"] is True
    again, _ = cli._gate2_ledger_entries(RID, run_dir, state)
    assert [e["id"] for e in again] == [e["id"] for e in entries]


def test_expand_merges_dispositions_and_never_drops(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH, LOW])
    entries, _ = cli._gate2_ledger_entries(RID, run_dir, state)
    hid = entries[0]["id"]
    art = {"adversarial_review": {"findings_from": "ledger", "spawned": True,
                                  "dispositions": {hid: {"disposition": "fixed", "evidence": "a.py:3 now masks",
                                                         "test": "tests/test_a.py::t", "root_cause": "rc",
                                                         "class_fix": "cf", "severity": "LOW"}},
                                  "findings": [{"severity": "MEDIUM", "specialist": "meta-review", "finding": "risk"}]}}
    out, errors, warnings = cli._expand_gate2_ledger(RID, run_dir, state, art)
    assert errors == []
    ar = out["adversarial_review"]
    assert "findings_from" not in ar and "dispositions" not in ar and ar["findings_source"] == "ledger"
    by = {f.get("id"): f for f in ar["findings"]}
    assert by[hid]["resolved"] is True and by[hid]["disposition"] == "fixed" and by[hid]["test"]
    assert by[hid]["root_cause"] == "rc" and by[hid]["class_fix"] == "cf"   # fix-by-class fields pass
    assert by[hid]["severity"] == "HIGH"   # a disposition cannot rewrite what the expert said
    low = next(f for f in ar["findings"] if f.get("severity") == "LOW")
    assert low["disposition"] == "unaddressed" and low["resolved"] is False
    assert ar["findings"][-1]["specialist"] == "meta-review"   # the model's own kept
    assert any("unaddressed" in w for w in warnings)


def test_expand_rejects_unknown_ids_and_dispositions(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH])
    hid = cli._gate2_ledger_entries(RID, run_dir, state)[0][0]["id"]
    art = {"adversarial_review": {"findings_from": "ledger",
                                  "dispositions": {"nope-123456": {"disposition": "fixed"},
                                                   hid: {"disposition": "ignored"}}}}
    _out, errors, _w = cli._expand_gate2_ledger(RID, run_dir, state, art)
    assert any("not in the ledger" in e for e in errors)
    assert any("'ignored'" in e for e in errors)


def test_non_ledger_artifact_untouched(env):
    _audit, run_dir, state, _t0 = env
    art = {"adversarial_review": {"findings": [{"severity": "LOW"}]}}
    assert cli._expand_gate2_ledger(RID, run_dir, state, art) == (art, [], [])


def test_expanded_findings_satisfy_the_validators_binding_check(env):
    """The reason for the ledger: what it records always covers what the experts said."""
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH, LOW])
    _marker(audit, 2, "api-contract", t0 + 11, [MED])
    out, _e, _w = cli._expand_gate2_ledger(RID, run_dir, state, {"adversarial_review": {"findings_from": "ledger"}})
    recorded = out["adversarial_review"]["findings"]
    for eid, rep in (("correctness", [HIGH, LOW]), ("api-contract", [MED])):
        assert cli._match_one_to_one(cli._binding_findings(rep), recorded, eid) == []


def test_red_team_joins_the_round2_batch_on_a_specialist_high(env, monkeypatch):
    _audit, run_dir, state, _t0 = env
    monkeypatch.setattr(cli, "_red_team_required_now", lambda rs, art: True)
    monkeypatch.setattr(cli, "_d7_completed_expert_ids", lambda rid: {"correctness"})
    monkeypatch.setattr(cli, "_changed_files_scope", lambda ft: {"changed_files": [{"path": "a.py"}]})
    monkeypatch.setattr(cli, "_ledger_add", lambda rid, paths: [])
    written = {}

    def fake_write(**kw):
        written.update(kw)
        (kw["run_dir"] / "evidence" / "prompts" / "red-team.md").write_text("EXPERT-ID: red-team\n")
        return []
    monkeypatch.setattr(cli, "_write_expert_prompts", fake_write)
    got = cli._red_team_with_recheck(RID, run_dir, state, "SwarmAI", [dict(HIGH, specialist="correctness")])
    assert got["entry"]["id"] == "red-team" and got["entry"]["round"] == 1
    assert got["entry"]["spawn"].startswith("EXPERT-ID: red-team")
    assert written["experts"][0]["id"] == "red-team" and written["files"] == ["a.py"]


def test_red_team_not_added_when_it_ran_or_without_a_high(env, monkeypatch):
    _audit, run_dir, state, _t0 = env
    monkeypatch.setattr(cli, "_d7_completed_expert_ids", lambda rid: {"red-team"})
    monkeypatch.setattr(cli, "_red_team_required_now", lambda rs, art: True)
    assert cli._red_team_with_recheck(RID, run_dir, state, "SwarmAI", [HIGH]) == {}
    monkeypatch.setattr(cli, "_d7_completed_expert_ids", lambda rid: set())
    monkeypatch.setattr(cli, "_red_team_required_now", lambda rs, art: False)
    assert cli._red_team_with_recheck(RID, run_dir, state, "SwarmAI", [LOW]) == {}


def test_a_reported_again_finding_keeps_the_strongest_severity(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [dict(MED, confidence=8)])
    _marker(audit, 2, "correctness", t0 + 20, [dict(MED, confidence=5, severity="LOW")])
    entries, _ = cli._gate2_ledger_entries(RID, run_dir, state)
    assert len(entries) == 1
    assert entries[0]["severity"] == "MEDIUM" and entries[0]["confidence"] == 8 and entries[0]["blocking"]
    out, _e, _w = cli._expand_gate2_ledger(RID, run_dir, state, {"adversarial_review": {"findings_from": "ledger"}})
    rep = [dict(MED, confidence=8), dict(MED, confidence=5, severity="LOW")]
    assert cli._match_one_to_one(cli._binding_findings(rep), out["adversarial_review"]["findings"], "correctness") == []


def test_refuted_is_recorded_as_not_a_bug_and_passes_the_fix_by_class_rule(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH])
    hid = cli._gate2_ledger_entries(RID, run_dir, state)[0][0]["id"]
    out, errors, _w = cli._expand_gate2_ledger(RID, run_dir, state, {"adversarial_review": {
        "findings_from": "ledger", "dispositions": {hid: {"disposition": "refuted", "evidence": "a.py:3 never sees a token"}}}})
    assert errors == []
    f = out["adversarial_review"]["findings"][0]
    assert f["finding"].startswith("Not a bug: a.py:3") and f["reported_finding"] and f["resolved"] is True
    from scripts import design_spec
    rx = getattr(design_spec, "_NOT_A_BUG", None)
    if rx is not None:
        assert rx.search(f["finding"])
    assert cli._match_one_to_one(cli._binding_findings([HIGH]), out["adversarial_review"]["findings"], "correctness") == []


def test_red_team_prompt_keeps_round1_change_diff_and_rewrites_a_stale_prompt(env, monkeypatch):
    _audit, run_dir, state, _t0 = env
    prompts = run_dir / "evidence" / "prompts"
    (prompts / "change.diff").write_text("ROUND-1 DIFF")
    (prompts / "red-team.md").write_text("stale prompt from an earlier cycle")
    monkeypatch.setattr(cli, "_red_team_required_now", lambda rs, art: True)
    monkeypatch.setattr(cli, "_d7_completed_expert_ids", lambda rid: set())
    monkeypatch.setattr(cli, "_changed_files_scope", lambda ft: {"changed_files": [{"path": "a.py"}]})
    monkeypatch.setattr(cli, "_ledger_add", lambda rid, paths: [])
    monkeypatch.setattr(cli, "_ledger_shas", lambda rid, name, since=None: set())   # not handed out

    def fake_write(**kw):
        (prompts / "change.diff").write_text("WHOLE CHANGE NOW")
        (prompts / "red-team.md").write_text(f"EXPERT-ID: red-team\nRead `{prompts / 'change.diff'}`\n")
        return []
    monkeypatch.setattr(cli, "_write_expert_prompts", fake_write)
    got = cli._red_team_with_recheck(RID, run_dir, state, "SwarmAI", [dict(HIGH, specialist="correctness")])
    assert got["entry"]["id"] == "red-team"
    assert (prompts / "change.diff").read_text() == "ROUND-1 DIFF"          # round 1 untouched
    assert (prompts / "red-team-change.diff").read_text() == "WHOLE CHANGE NOW"
    text = (prompts / "red-team.md").read_text()
    assert "red-team-change.diff" in text and "stale prompt" not in text
