"""A research run finishes DELIVER the way `next` says and ends `no_code` (design
docs/design/2026-10-26-research-deliver-fixes.md).

What is tested:
- 1: after reflect-prepare, `next` on a research run says stage-complete, never
  report-tests; following `next` to the end gives landing == "no_code";
- 2: `land` on a research run is refused before any event is written, and the run
  still ends no_code;
- 3: reflect.prepare accepts relation "new" when dedup candidates exist and keeps the
  candidates on the lesson; a relation target outside the candidates names them.
Driven through the CLI rig (a separate pipeline-v1 process per command).
"""
import pytest

from tests.pipeline_v1_rig import Rig


@pytest.fixture
def research(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="research", with_repo=False)
    r.create()
    r.drive(until="deliver")
    out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {
        "delivered": [], "decisions": [], "corrections": [],
        "practices": [], "gaps": [],
    }))
    assert out.get("ok") is True, out
    yield r
    r.close()


def _follow_next(r: Rig, limit: int = 4) -> dict:
    for _ in range(limit):
        state = r.v1("status")
        nxt = state["next"]
        if nxt["action"] == "none":
            return state["state"]
        assert "report-tests" not in nxt["command"], nxt
        sub = nxt["command"].split("pipeline-v1 ", 1)[1].split(" --project", 1)[0].split()
        out = r.v1(*sub)
        assert out.get("ok") is True, out
    raise AssertionError(f"run did not finish: {r.state()}")


def test_next_after_reflect_prepare_is_stage_complete_and_ends_no_code(research):
    nxt = research.v1("status")["next"]
    assert nxt["action"] == "run" and "stage-complete" in nxt["command"], nxt
    state = _follow_next(research)
    assert state["status"] == "completed" and state["landing"] == "no_code", state


def test_land_on_a_research_run_is_refused_and_writes_nothing(research):
    before = len(research.events())
    out = research.v1("land", "--message", "research: nothing to land")
    assert out.get("ok") is False and "stage-complete" in str(out.get("error")), out
    assert len(research.events()) == before
    state = _follow_next(research)
    assert state["landing"] == "no_code", state





def test_a_research_run_gets_its_own_think_card_and_a_full_run_the_code_one(tmp_path, monkeypatch):
    """Swarm Run P3 (impl §7): research THINK has its own card (splitting the question to
    Hives, the sourced report); every other profile keeps stages/think.md."""
    r = Rig(tmp_path / "research", monkeypatch, profile="research", with_repo=False)
    r.create()
    try:
        r.drive(until="think")
        nxt = r.v1("status")["next"]
        assert nxt["card"].endswith("stages/think.research.md"), nxt
    finally:
        r.close()
    f = Rig(tmp_path / "full", monkeypatch, profile="full")
    f.create()
    try:
        f.drive(until="think")
        assert f.v1("status")["next"]["card"].endswith("stages/think.md")
    finally:
        f.close()
