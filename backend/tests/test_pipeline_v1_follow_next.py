"""Following `next` alone reaches the run's outcome (design
docs/design/2026-10-26-pipeline-two-ends.md §3.1, §8 step 1).

What is tested: a follower that reads only `next` (action + command) and supplies the
artifact the named command takes drives a run from create to its terminal state. It
knows no stage rules, the way an agent uses the pipeline. Cases: every profile; full
with two readings (Gate-0 required); BUILD failing three times (not-landed path); main
moving during DELIVER; a Gate-0 second round. A command that fails is not an error by itself (`next` must lead
back); the same steps repeating is.

KNOWN: cases red, marked xfail(strict=True); a fix that makes one pass turns the test
red until the case leaves KNOWN. Emptied in two-ends step 6; it may only shrink.
"""
import pytest

from core.run_protocol.lifecycle import PROFILE_STAGES
from tests.pipeline_v1_rig import SWARM, Rig

KNOWN: set[str] = set()   # emptied in two-ends step 6


def _sub(command: str) -> list[str]:
    tail = command.split("pipeline-v1 ", 1)[1]
    return tail.split(" --project", 1)[0].split()


def _ensure_change(r: Rig) -> None:
    if r.repo is not None and not r._edited:
        r.edit(Rig.CHANGE)
        r._edited = True


def _run(r: Rig, sub: list[str], stage: str, step: int) -> dict:
    name = sub[0]
    docs = r.documents()
    if name == "stage-complete" and "--artifact" in sub:
        if stage == "evaluate":
            return r.v1("stage-complete", "--artifact", r.artifact("requirements", docs["evaluate"]))
        _ensure_change(r)
        if stage == "plan":
            return r.v1("stage-complete", "--artifact", r.artifact("design", docs["plan"]))
        return r.v1("stage-complete", "--artifact", r.artifact("report", r.report()))
    if name == "gate0-start":
        return r.v1("gate0-start", "--artifact", r.artifact("requirements", docs["evaluate"]))
    if name == "reflect-prepare":
        return r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
    if name == "report-tests":
        return r.v1("report-tests", "--artifact", r.artifact("report", r.report()))
    if name == "land":
        return r.v1("land", "--message", "fix: swap the operands" + SWARM)
    if name == "review-start":
        out = r.v1("review-start")
        for i, h in enumerate(out.get("handouts") or []):
            r.spawn_reviewer(h["prompt"], "NO FINDINGS", f"review-{step}-{i}")
        return out
    return r.v1(*sub)


def follow(r: Rig, limit: int = 40, before=None, gate0_answers=None) -> tuple[dict, list[str]]:
    """*gate0_answers*: the Gate-0 verdicts to give, in order (default: SUPPORTED)."""
    trail: list[str] = []
    gate0_answers = list(gate0_answers or [])
    for step in range(limit):
        status = r.v1("status")
        state, nxt = status["state"], status["next"]
        if before is not None:
            before(r, state)
        if nxt["action"] == "none":
            return state, trail
        if nxt["action"] == "wait":
            for i, h in enumerate(nxt.get("handouts") or []):
                verdict = gate0_answers.pop(0) if gate0_answers else "SUPPORTED"
                answer = (f"VERDICT: {verdict}\nEvidence: backend/core/mod.py"
                          if state["stage"] == "evaluate" else "NO FINDINGS")
                r.spawn_reviewer(h.get("spawn_prompt") or h["prompt"], answer,
                                 f"{state['stage']}-{step}-{i}")
            trail.append(f"{state['stage']}:wait")
        else:
            assert nxt["command"], nxt
            sub = _sub(nxt["command"])
            trail.append(f"{state['stage']}:{' '.join(sub)}")
            out = _run(r, sub, state["stage"], step)
            if out.get("ok") is False:
                trail.append(f"  !{str(out.get('error') or out.get('gate0') or out.get('code'))[:80]}")
        if len(trail) >= 6 and len(set(trail[-6:])) <= 2:
            raise AssertionError(f"`next` loops: {trail}")
    raise AssertionError(f"no end after {limit} steps: {trail}")


def _case(name):
    marks = [pytest.mark.xfail(strict=True, reason="KNOWN (two-ends §3.1)")] if name in KNOWN else []
    return pytest.param(name, marks=marks, id=name)


@pytest.mark.parametrize("profile", [_case(p) for p in sorted(PROFILE_STAGES)])
def test_every_profile_reaches_its_outcome_by_next(tmp_path, monkeypatch, profile):
    r = Rig(tmp_path, monkeypatch, profile=profile, with_repo=profile != "research")
    try:
        r.create()
        state, trail = follow(r)
        assert state["status"] == "completed", (trail, state)
        assert state["landing"] == ("no_code" if profile == "research" else "landed"), (trail, state)
    finally:
        r.close()


class _TwoReadings(Rig):
    def documents(self):
        docs = super().documents()
        docs["evaluate"]["understanding"]["readings"] = ["one", "two"]
        return docs


@pytest.mark.parametrize("case", [_case("full-two-readings")])
def test_gate0_required_reaches_landed_by_next(tmp_path, monkeypatch, case):
    r = _TwoReadings(tmp_path, monkeypatch, profile="full")
    try:
        r.create()
        state, trail = follow(r)
        assert state["landing"] == "landed", (trail, state)
    finally:
        r.close()


@pytest.mark.parametrize("case", [_case("gate0-second-round")])
def test_gate0_second_round_reaches_landed_by_next(tmp_path, monkeypatch, case):
    """First Gate-0 answer negative: `next` starts the second (last) round."""
    r = Rig(tmp_path, monkeypatch, profile="bugfix")
    try:
        r.create()
        state, trail = follow(r, gate0_answers=["UNSUPPORTED", "SUPPORTED"])
        assert state["landing"] == "landed", (trail, state)
        assert sum(1 for s in trail if s.startswith("evaluate:gate0-start")) == 2, trail
    finally:
        r.close()


@pytest.mark.parametrize("case", [_case("build-exhausted")])
def test_build_exhausted_ends_not_landed_by_next(tmp_path, monkeypatch, case):
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        _ensure_change(r)
        for _ in range(3):
            r.v1("stage-complete")          # no report: BX-3 fails each attempt
        state, trail = follow(r)
        assert state["status"] == "completed" and state["landing"] == "not_landed", (trail, state)
    finally:
        r.close()


@pytest.mark.parametrize("case", [_case("main-moved")])
def test_main_moving_in_deliver_still_lands_by_next(tmp_path, monkeypatch, case):
    r = Rig(tmp_path, monkeypatch, profile="direct")
    moved: list[str] = []

    def move_once(rig, state):
        if not moved and state["stage"] == "deliver" and state["status"] == "active":
            moved.append(rig.commit_on_main({"docs/notes.md": "unrelated\n"}))
    try:
        r.create()
        state, trail = follow(r, before=move_once)
        assert moved and state["landing"] == "landed", (trail, state)
    finally:
        r.close()
