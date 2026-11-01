"""A landing whose change is not the reviewed one goes back to REVIEW (design
docs/design/2026-10-31-pipeline-review-close-and-landing-route.md §3 change 3).

What is tested, through real runs (``tests/pipeline_v1_rig.py``: CLI + hooks):

- a rebase conflict resolved in DELIVER changes the run's patch: ``land`` sends the run
  back to REVIEW without first asking for a test run, and that failed attempt is not
  one of the run's three landing failures (T7);
- the incremental review then shows the run's own patch of the resolved file, not the
  file main changed without a conflict; after it, TEST and the landing go through (T9,
  T10);
- only once: a second unreviewed change at landing lands, and the REPORT says no review
  saw it (design 2026-11-01-pipeline-delivery-friction N1);
- a second merge conflict after that reopen: the resolution is tested again and lands
  (N1), and a fresh conflict is not one of the run's three landing failures;
- only doc-class files differ: the engine completes REVIEW itself and the run is back
  in TEST (T8);
- an edit in DELIVER takes the same route (T7).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pipeline_v1_rig import SWARM, Rig, _git, write

pytestmark = pytest.mark.timeout(300)

MESSAGE = "fix: swap the operands" + SWARM
REFLECT = {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}


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


def _at_deliver(r: Rig) -> None:
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", REFLECT))


def _conflict_resolved(r: Rig) -> None:
    """main changes mod.py (a conflict) and other.py (none); the first land stops on the
    conflict, which is then resolved in the worktree."""
    r.commit_on_main({"backend/core/mod.py": "def add(a, b):\n    return a + b + 0\n",
                      "backend/core/other.py": "def other():\n    return 1  # main\n"})
    first = r.v1("land", "--message", MESSAGE)
    assert first.get("ok") is False and "merge_conflict" in str(first.get("failed")), first
    write(r.worktree, {"backend/core/mod.py": "def add(a, b):\n    return b + a + 0\n"})
    _git(r.worktree, "add", "backend/core/mod.py")


def _land_checks(r: Rig) -> list[dict]:
    return [e["payload"] for e in r.events() if e["kind"] == "check" and e.get("stage") == "land"]


def test_a_resolved_conflict_goes_back_to_review_and_then_lands(rig):
    r = rig()
    _at_deliver(r)
    _conflict_resolved(r)
    step = r.v1("status")["next"]
    out = r.v1("land", "--message", MESSAGE)
    assert out.get("reopened") == "review" and r.state()["stage"] == "review", out
    lg1 = [c for c in _land_checks(r) if c["check_id"] == "LG-1"][-1]
    assert lg1["cause"] == "review_reopened" and not lg1["passed"], lg1
    assert r.state().get("review_reopened_failures") == 1, r.state()
    started = r.v1("review-start")
    assert started.get("incremental") is True, started
    text = Path(started["handouts"][0]["path"]).read_text(encoding="utf-8")
    review_part = text.split("main moved under the run", 1)[1]
    assert "return b + a + 0" in review_part and "core/other.py" not in review_part, text
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], "NO FINDINGS", f"inc-{i}")
    assert r.v1("stage-complete").get("ok") is True
    assert r.state()["stage"] == "test"
    from tests.pipeline_v1_e2e_common import _landed_clean
    _landed_clean(r.drive())
    assert step is not None


def test_an_edit_in_deliver_goes_back_to_review_without_a_test_run_first(rig):
    r = rig()
    _at_deliver(r)
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return b + a  # edited\n"})
    step = r.v1("status")["next"]
    assert "land" in step["command"] and "back to REVIEW" in step["why"], step
    out = r.v1("land", "--message", MESSAGE)
    assert out.get("reopened") == "review", out


def test_deliver_sends_the_run_back_once(rig):
    r = rig()
    _at_deliver(r)
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return b + a  # one\n"})
    assert r.v1("land", "--message", MESSAGE).get("reopened") == "review"
    r.drive(until="deliver")
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return b + a  # two\n"})
    r.v1("report-tests", "--artifact", r.artifact("report", r.report()))
    # past the one reopen, the difference lands and the REPORT says no review saw it
    # (design 2026-11-01-pipeline-delivery-friction N1; SOUL P5)
    out = r.v1("land", "--message", MESSAGE)
    assert "reopened" not in out and r.state().get("landing") == "landed", out
    report = (r.engine().run_dir / "REPORT.md").read_text()
    assert "Landed with a difference no review saw" in report
    quality = next(line for line in report.splitlines() if line.startswith("| Quality |"))
    assert quality.startswith("| Quality | WARN |"), quality


def test_only_doc_files_differ_review_completes_itself(rig):
    r = rig()
    _at_deliver(r)
    r.edit({"docs/notes.md": "a note\n"})
    out = r.v1("land", "--message", MESSAGE)
    assert out.get("reopened") == "test" and r.state()["stage"] == "test", out
    assert not [e for e in r.events() if e["kind"] == "handout"
                and e["payload"].get("incremental_from")], "no expert for a doc change"


def test_main_s_code_commits_never_count_as_the_run_s_unreviewed_change(rig):
    """main moves a code file while the run's only new edit is a doc: what differs from
    the reviewed change is the run's own patch, so REVIEW completes itself."""
    r = rig()
    _at_deliver(r)
    r.commit_on_main({"backend/core/other.py": "def other():\n    return 1  # main\n"})
    r.edit({"docs/notes.md": "a note\n"})
    out = r.v1("land", "--message", MESSAGE)
    assert out.get("reopened") == "test" and r.state()["stage"] == "test", out


def test_the_patch_comparison_counts_only_the_run_s_own_edits(tmp_path):
    """_patch_paths_changed: main's commits never count; a whitespace-only change to the
    run's own hunk does; a file in one patch only does."""
    from core.run_protocol.engine import _patch_paths_changed
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    lines = "".join(f"line {i}\n" for i in range(40))
    write(repo, {"a.py": lines, "b.py": "def f():\n    return 1\n", "c.py": "x = 1\n"})
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "base")
    base1 = _git(repo, "rev-parse", "HEAD")

    def tree(files: dict[str, str]) -> str:
        write(repo, files)
        _git(repo, "add", "-A")
        return _git(repo, "write-tree")
    reviewed = tree({"a.py": lines.replace("line 3\n", "line 3 run\n"),
                     "b.py": "def f():\n    return 2\n"})
    _git(repo, "reset", "-q", "--hard", base1)
    # main moves: c.py and a far part of a.py, no conflict with the run
    write(repo, {"c.py": "x = 2\n", "a.py": lines.replace("line 35\n", "line 35 main\n")})
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "main")
    base2 = _git(repo, "rev-parse", "HEAD")
    moved = lines.replace("line 35\n", "line 35 main\n")
    same = tree({"a.py": moved.replace("line 3\n", "line 3 run\n"),
                 "b.py": "def f():\n    return 2\n"})
    assert _patch_paths_changed(str(repo), base1, reviewed, base2, same) == []
    spaced = tree({"b.py": "def f():\n     return 2\n"})            # indentation only
    assert _patch_paths_changed(str(repo), base1, reviewed, base2, spaced) == ["b.py"]
    extra = tree({"b.py": "def f():\n    return 2\n", "d.py": "new\n"})
    assert _patch_paths_changed(str(repo), base1, reviewed, base2, extra) == ["d.py"]
    # names git would quote or a header split would misread
    odd = tree({"my b/f.py": "x\n", "é.py": "y\n"})
    assert _patch_paths_changed(str(repo), base1, reviewed, base2, odd) == sorted(
        ["d.py", "my b/f.py", "é.py"])


def test_a_second_conflict_after_the_reopen_is_tested_and_lands(rig):
    """N1: main conflicts with the run twice. The first resolution goes back to REVIEW;
    the second is tested again (a base-moved failure, not one of the run's three) and
    lands, the REPORT saying no review saw it."""
    r = rig()
    _at_deliver(r)
    _conflict_resolved(r)
    assert r.v1("land", "--message", MESSAGE).get("reopened") == "review"
    r.drive(until="deliver")
    r.commit_on_main({"backend/core/mod.py": "def add(a, b):\n    return a + b + 0 + 0\n"})
    second = r.v1("land", "--message", MESSAGE)
    assert "merge_conflict" in str(second.get("failed")), second
    write(r.worktree, {"backend/core/mod.py": "def add(a, b):\n    return b + a + 0 + 0\n"})
    _git(r.worktree, "add", "backend/core/mod.py")
    retest = r.v1("land", "--message", MESSAGE)
    lg1 = [c for c in _land_checks(r) if c["check_id"] == "LG-1"][-1]
    assert lg1.get("cause") == "base_moved" and "rebased onto it" in str(lg1["detail"]), (retest, lg1, _land_checks(r)[-8:])
    r.v1("report-tests", "--artifact", r.artifact("report2", r.report()))
    out = r.v1("land", "--message", MESSAGE)
    state = r.state()
    assert state.get("landing") == "landed", (out, state)
    own = state.get("stage_attempts", {}).get("land", 0) - state.get("base_moved_failures", 0) \
        - state.get("review_reopened_failures", 0)
    assert own <= 2, state         # only the landing that went through is the run's own
    assert "Landed with a difference no review saw" in (r.engine().run_dir / "REPORT.md").read_text()


def test_a_hand_commit_in_the_worktree_is_folded_in_at_landing(rig):
    """N3: a commit made by hand on the run branch is folded back into the change when
    the landing syncs (its content unchanged), instead of failing LG-4."""
    r = rig()
    _at_deliver(r)
    _git(r.worktree, "add", "backend/core/mod.py")
    _git(r.worktree, "commit", "-qm", "by hand")
    r.commit_on_main({"docs/notes.md": "unrelated\n"})
    out = r.v1("land", "--message", MESSAGE)
    assert r.state().get("landing") == "landed", out
    landed = _git(r.repo, "show", "main:backend/core/mod.py")
    assert "return b + a" in landed
