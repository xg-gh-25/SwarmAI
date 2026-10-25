"""The DELIVER REPORT measures the run's tokens up to the landing, and says when no
expert looked at the agent's test reports.

What is tested (real CLI and hooks, ``tests/pipeline_v1_rig.py``):
- ``land`` measures the run's transcript (run start -> now) before it renders the
  REPORT: with a transcript, Token Cost shows the measured total; without one it
  records ``metric(usage_not_available)`` at DELIVER and Token Cost is WARN with the
  reason, never BLOCK "not measured" (the old REPORT, rendered before REFLECT measured);
- a run whose test reports no review expert saw (a small direct change dispatches
  none) says so in the conclusion and the test section: "仅由 agent 报告";
- a run whose review experts answered says the reports were reviewed instead.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from tests.pipeline_v1_rig import BASE_FILES, Rig, _git

pytestmark = pytest.mark.timeout(300)


class NoGuideRig(Rig):
    """No ``.swarmai/review.md``: a small direct change then dispatches no expert."""
    BASE_FILES = {k: v for k, v in BASE_FILES.items() if k != ".swarmai/review.md"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct", cls: type = Rig) -> Rig:
        r = cls(tmp_path / f"case{len(made)}", monkeypatch, profile=profile)
        claude = r.root / "claude"
        (claude / "projects").mkdir(parents=True)
        r.env["CLAUDE_CONFIG_DIR"] = str(claude)      # never the developer's transcripts
        r.env.pop("CLAUDE_CODE_SESSION_ID", None)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _land(r: Rig) -> str:
    """Drive to DELIVER, land, and return the committed REPORT."""
    r.drive(until="deliver")
    r.complete_stage()
    state = r.state()
    assert state["landing"] == "landed", state
    return _git(r.repo, "show", f"main:.aidlc/deliverables/{r.run_id}/REPORT.md")


def _row(report: str, name: str) -> str:
    return next(line for line in report.splitlines() if line.startswith(f"| {name} |"))


# No expert in these two: an expert the rig spawns is not metered, which is BLOCK on its
# own (§E5, test_pipeline_v1_handoff); here only the main transcript is in question.
def test_without_a_transcript_token_cost_is_warn_with_the_reason(rig):
    r = rig("direct", NoGuideRig)
    r.create()
    report = _land(r)
    missing = [e for e in r.events() if e["kind"] == "metric"
               and e["payload"]["name"] == "usage_not_available"]
    assert [e["stage"] for e in missing] == ["deliver"], missing
    row = _row(report, "Token Cost")
    assert row.startswith("| Token Cost | WARN | not measured（"), row


def test_with_a_transcript_the_report_shows_the_tokens_up_to_delivery(rig):
    r = rig("direct", NoGuideRig)
    sid = str(uuid.uuid4())
    r.env["CLAUDE_CODE_SESSION_ID"] = sid
    r.create()
    transcript = r.root / "claude" / "projects" / "-repo" / f"{sid}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({
        "type": "assistant", "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"id": "msg_1", "role": "assistant",
                    "usage": {"input_tokens": 1000, "output_tokens": 200}}}) + "\n",
        encoding="utf-8")
    report = _land(r)
    measured = [e for e in r.events() if e["kind"] == "metric"
                and e["payload"]["name"] == "input_tokens"]
    assert [e["stage"] for e in measured] == ["deliver"], measured
    row = _row(report, "Token Cost")
    assert "not measured" not in row and " total · " in row, row


def test_reports_no_expert_saw_are_called_agent_only(rig):
    r = rig("direct", NoGuideRig)      # a small change: no review expert is dispatched
    r.create()
    report = _land(r)
    assert not [e for e in r.events() if e["kind"] in ("handout", "answer")]
    conclusion = report.splitlines()[2]
    assert "测试结果仅由 agent 报告，没有审查专家核实" in conclusion, conclusion
    assert "## 测试（仅由 agent 报告：pipeline 没有重跑，也没有审查专家核实）" in report, report


def test_reports_the_experts_reviewed_say_so(rig):
    r = rig("full")
    r.create()
    report = _land(r)
    assert [e for e in r.events() if e["kind"] == "answer" and e["stage"] == "review"]
    assert "仅由 agent 报告" not in report, report
    assert "## 测试（agent 报告，pipeline 没有重跑；审查专家审阅过这些报告）" in report, report
