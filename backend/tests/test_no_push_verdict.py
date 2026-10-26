"""The pipeline gives no push verdict; open checks are one line in the audit's Quality cell.

XG 2026-10-26: "NOT PUSH READY" alarms people (native coding says nothing of the sort,
and the pipeline already guards more), and reading the list changes nothing. So:

- the REPORT and the card never say PUSH READY / NOT PUSH READY and have no
  "before you push" section; an open check (e.g. RV-2, DC-2) is one short sentence in the
  Quality cell (``WARN · ... · agent chose after the decision cap``);
- an open check never blocks landing or the commit: a run whose agent chose after the
  decision cap (DC-2 open) lands, main moves, and the REPORT is in the commit;
- the open checks are kept for the audit (``engine.open_checks()``, ``run_audit.json``),
  the CLI has no ``push-status`` and ``status`` / ``land`` return no ``push``;
- MD-1 (always passes since shared mode went) and MD-3 (a person's landing is a fact
  the REPORT already states) are gone.
"""
from __future__ import annotations

import pytest

from core.run_protocol import audit, checks
from tests.pipeline_v1_rig import Rig, _git
from tests.test_pipeline_v1_conformance_k3 import _ask

pytestmark = pytest.mark.timeout(300)

PUSH_WORDS = ("PUSH READY", "push ready", "push-ready", "before you push", "ready to push")


def _built(open_checks):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    report = {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
              "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                          "n_pass": 2, "n_fail": 0, "n_not_run": 0}}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              report, {"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": {}}]
    return audit.build(run_id="run_x", projection=projection, events=events,
                       repos_isolated=1, findings=[], contract=contract,
                       open_checks=open_checks, audit_through="COMPLETED",
                       commits={"repo": "a" * 40})


def test_no_push_verdict_in_report_or_card_and_open_checks_are_one_quality_line():
    value = _built({"DC-2": "agent chose after decision cap", "TS-1": "stale"})
    report, card = audit.render(value, fmt="report"), value["card"]
    for text in (report, card):
        assert not any(word in text for word in PUSH_WORDS), text
    assert "push_ready" not in value["outcome"], value["outcome"]
    row = next(line for line in report.splitlines() if line.startswith("| Quality |"))
    assert row.startswith("| Quality | WARN |"), row
    assert "agent chose after the decision cap" in row and "+1 more" in row, row
    clean = _built({})
    row = next(line for line in audit.render(clean, fmt="report").splitlines()
               if line.startswith("| Quality |"))
    assert row.startswith("| Quality | PASS |") and "more" not in row, row


def test_md1_md3_and_the_push_effect_are_gone():
    assert "MD-1" not in checks.CHECKS and "MD-3" not in checks.CHECKS
    assert "push" not in checks.EFFECTS
    assert not hasattr(checks, "push_checks")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str) -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_an_open_check_never_blocks_landing_or_the_commit(rig):
    r = rig("full")
    r.create()
    r.drive(until="think")
    for i in range(r.engine().contract["decision_cap"]):
        assert _ask(r, f"q{i}").get("ok") is True
        assert r.answer_card("a — swap")
    evidence = r.events()[0]["event_id"]
    assert _ask(r, "qcap", after_cap={"option_id": "b", "evidence_refs": [evidence]}).get("ok")
    assert "DC-2" in r.engine().open_checks()["open"]
    base = _git(r.repo, "rev-parse", "main")
    r.drive(until="deliver")
    out = r.complete_stage()
    assert "push" not in (out or {}), out
    assert r.state()["landing"] == "landed", r.state()
    assert _git(r.repo, "rev-parse", "main^") == base
    report = _git(r.repo, "show", f"main:.aidlc/deliverables/{r.run_id}/REPORT.md")
    assert not any(word in report for word in PUSH_WORDS), report
    assert "agent chose after the decision cap" in report, report
    status = r.v1("status")
    assert status.get("ok") is True and "push" not in status, status
    assert r.v1("push-status").get("ok") is not True
