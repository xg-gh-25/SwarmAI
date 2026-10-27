"""Shared parts of the Pipeline v1 real end-to-end tests (test_pipeline_v1_e2e_*.py).

The e2e suite was one file of 43 CLI-driven cases (~7 minutes serially); it is split
by topic so each file runs within one call's time cap (design
2026-10-27-pipeline-hygiene §2). Every run is driven only through
``"$SWARM_BACKEND_BIN" pipeline-v1`` and the real hook functions
(``tests/pipeline_v1_rig.py``); no test appends an event.

Exports: the ``rig`` fixture (its teardown checks that every run's event log still
folds), ``CODE_PROFILES`` and the helpers several topics use (``_main``,
``_reason``, ``_landed_clean``, ``_decision``, ``_not_landed_tests_red``,
``_prepare``, ``_request``).
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import Rig

CODE_PROFILES = ("direct", "trivial", "full", "bugfix")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile, **kwargs)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.run_id:
            assert r.v1("status").get("ok") is True, "the event log no longer folds"
        r.close()



def _main(r: Rig) -> str:
    from tests.pipeline_v1_rig import _git
    return _git(r.repo, "rev-parse", "main")


def _reason(state: dict) -> str | None:
    return state.get("reason")


def _landed_clean(state: dict) -> None:
    assert state["status"] == "completed" and state["landing"] == "landed", state
    assert all(v == "completed" for v in state["stages"].values()), state["stages"]


def _decision(r: Rig, request_id: str = "q1") -> str:
    return r.artifact(f"decision-{request_id}", {
        "request_id": request_id, "kind": "design_tradeoff",
        "question": "Swap or rewrite?", "why_evidence_insufficient": "both fit",
        "refs": [r.events()[0]["event_id"]],
        "options": [
            {"id": "a", "choice": "swap", "consequence": "small", "reversible": True,
             "risk": "low"},
            {"id": "b", "choice": "rewrite", "consequence": "large", "reversible": False,
             "risk": "high"}]})


def _not_landed_tests_red(r: Rig) -> None:
    """The agent reports a failing test in TEST three times (S6-1): the stage's checks
    are exhausted and the run ends not_landed, nothing committed."""
    assert r.profile == "trivial"
    r.create()
    r.drive(until="test")
    for _ in range(3):
        r.report_and_complete("fail")
    state = r.drive()
    assert state["landing"] == "not_landed" and _reason(state) == "checks_exhausted", state


def _prepare(r: Rig) -> dict:
    out = r.v1("run-land", "--prepare", "--skip-tests", "--note", "accepted by XG")
    assert out.get("ok") is True, out
    return out


def _request(r: Rig, prepared: dict) -> dict:
    request_id = prepared["prepared"]["request"]["request_id"]
    return r.v1("run-land", "--request", request_id, "--message", "fix: swap" + "\n\n"
                "Co-Authored-By: Swarm <swarm@swarmai.dev>")
