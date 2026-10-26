"""T6 (design 2026-10-25-pipeline-standalone §4.2, §4.3): a run created where no host
hook runs (no ``SWARM_APP_SESSION_ID``) still goes from create to landed.

What is tested (real CLI, ``tests/pipeline_v1_rig.py``, no run_owner_gate: outside
SwarmAI nothing runs it):
- the owner is the host's session id when the host has one (Claude Code's
  ``CLAUDE_CODE_SESSION_ID``), else a run-level id create makes; every ``next.command``
  of a run-level owner carries it;
- the contract fixes how answers arrive: ``host`` when a hook supplied the session id,
  ``relayed`` otherwise; ``review-answer`` records an expert's reply only on a relayed
  run, only from the owner, once per handout, ``source: relayed``;
- a full run lands with relayed answers, and its REPORT says they were relayed; a run
  whose answers came from the host's hook has no such line.
"""
from __future__ import annotations

import re
import shlex
import uuid

import pytest

from tests.pipeline_v1_rig import PROJECT, Rig

pytestmark = pytest.mark.timeout(300)


def _standalone(tmp_path, monkeypatch, profile="full", transcript: str | None = None) -> Rig:
    """A rig as a host without hooks runs it: no SWARM_APP_SESSION_ID, no gate."""
    r = Rig(tmp_path, monkeypatch, profile=profile)
    r.env.pop("SWARM_APP_SESSION_ID", None)
    r.env.pop("CLAUDE_CODE_SESSION_ID", None)
    if transcript:
        r.env["CLAUDE_CODE_SESSION_ID"] = transcript
    r.bash = lambda command, extra_env=None, session=None: r._exec(   # type: ignore[method-assign]
        ["bash", "-c", command], extra_env=extra_env)[0]
    return r


def _handout_id(r: Rig, prompt: str) -> str:
    from core.run_protocol import hook_bridge
    _, nonce = hook_bridge.parse_binding(prompt)
    (hid,) = [e["event_id"] for e in r.events()
              if e["kind"] == "handout" and e["payload"].get("nonce") == nonce]
    return hid


def _relay(r: Rig, handout_id: str, answer: str, owner: str | None = None) -> dict:
    env = {"SWARM_APP_SESSION_ID": owner} if owner else None
    return r.bash(f"printf %s {shlex.quote(answer)} | \"$SWARM_BACKEND_BIN\" pipeline-v1 "
                  f"review-answer --project {PROJECT} --run-id {r.run_id} "
                  f"--handout {handout_id} --answer -", extra_env=env)


def _owned(r: Rig, out: dict) -> str:
    """The agent runs the printed commands; the rig's own calls carry the owner."""
    owner = out["owner"]
    r.env["SWARM_APP_SESSION_ID"] = owner
    return owner


def test_no_host_session_gives_a_run_owner_and_relayed_answers(tmp_path, monkeypatch):
    r = _standalone(tmp_path, monkeypatch)
    out = r.create()
    owner = out["owner"]
    assert uuid.UUID(owner) and out["review_answers"] == "relayed", out
    assert out["next"]["command"].startswith(f"SWARM_APP_SESSION_ID={owner} "), out["next"]
    from core.run_protocol.contract import read_contract
    contract = read_contract(r.data, r.run_id)
    assert contract["session_id"] == owner and contract["review_answers"] == "relayed"
    assert contract["owner_source"] == "run"


def test_claude_code_session_id_is_the_owner_without_a_hook(tmp_path, monkeypatch):
    sid = str(uuid.uuid4())
    r = _standalone(tmp_path, monkeypatch, transcript=sid)
    out = r.create()
    assert out["owner"] == sid and out["review_answers"] == "relayed", out
    assert not out["next"]["command"].startswith("SWARM_APP_SESSION_ID="), out["next"]
    from core.run_protocol.contract import read_contract
    assert read_contract(r.data, r.run_id)["owner_source"] == "host_transcript"


def test_a_full_run_lands_with_relayed_answers_and_report_says_so(tmp_path, monkeypatch):
    r = _standalone(tmp_path, monkeypatch)
    relayed: list[dict] = []

    def spawn(prompt, answer, tool_use_id, via="session"):
        out = _relay(r, _handout_id(r, prompt), answer)
        assert out.get("ok") is True, out
        relayed.append(out)
        return prompt
    monkeypatch.setattr(r, "spawn_reviewer", spawn)
    _owned(r, r.create())
    state = r.drive()
    assert state["landing"] == "landed", state
    answers = [e for e in r.events() if e["kind"] == "answer"]
    assert answers and len(answers) == len(relayed)
    assert all(a["payload"]["source"] == "relayed" and a["producer"] == "cli" for a in answers)
    report = (r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md").read_text(encoding="utf-8")
    n = len(answers)
    assert f"Review answers: {n} of {n} relayed by the agent" in report, report


def test_host_answers_leave_no_relayed_line(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    out = r.create()
    assert out["review_answers"] == "host" and "owner" in out
    state = r.drive()
    assert state["landing"] == "landed", state
    report = (r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md").read_text(encoding="utf-8")
    assert "relayed" not in report


def test_review_answer_is_refused_where_a_hook_records_answers(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    r.create()
    r.drive(until="review")
    out = r.v1("review-start")
    (first, *_) = out["handouts"]
    refused = r.bash(f"printf %s 'NO FINDINGS' | \"$SWARM_BACKEND_BIN\" pipeline-v1 review-answer "
                     f"--project {PROJECT} --run-id {r.run_id} "
                     f"--handout {_handout_id(r, first['prompt'])} --answer -")
    assert refused.get("ok") is False and "hook" in refused.get("error", ""), refused
    assert not [e for e in r.events() if e["kind"] == "answer"]


def test_review_answer_is_the_owners_once_per_handout(tmp_path, monkeypatch):
    r = _standalone(tmp_path, monkeypatch)
    _owned(r, r.create())
    r.drive(until="review")
    out = r.v1("review-start")
    hid = _handout_id(r, out["handouts"][0]["prompt"])
    assert out["handouts"][0]["answer_command"].endswith(f"--handout {hid} --answer -")
    other = _relay(r, hid, "NO FINDINGS", owner=str(uuid.uuid4()))
    assert other.get("code") == "not_owner", other
    assert _relay(r, "ev_" + "0" * 16, "NO FINDINGS").get("ok") is False
    assert _relay(r, hid, "NO FINDINGS").get("ok") is True
    again = _relay(r, hid, "NO FINDINGS")
    assert again.get("ok") is False and re.search("already answered", again.get("error", "")), again
    assert len([e for e in r.events() if e["kind"] == "answer"]) == 1
