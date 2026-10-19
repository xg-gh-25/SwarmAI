"""Pipeline commit message, mode: exit-criterion judging, and BUILD's files_touched.

WHAT IS TESTED
- ``artifact_cli._commit_message``: the ONE builder behind run-commit and the
  worktree landing. Subject = the DELIVER title (folded, at most 72 chars, cut at a
  word boundary), else the requirement's first line; the legacy auto-delivery title
  (``requirement[:80]``) never ends mid-word, while a complete-word title followed by
  punctuation is kept. Body = result_now and the approach lines, then the run-id note
  and the project ``Swarm`` trailer last.
  A real run-commit and a real worktree landing carry that message in git.
- ``pipeline_validator.judge_exit_criteria`` on a ``mode:`` ref: a report whose
  failures are all classified pre-existing / flaky (the runner's own accept rule,
  ``pipeline_test._exit_code``) meets the criterion; a regression / new / unknown
  failure, a timeout, a runner error, an unclassified failure count, or zero passes
  does not. Every report the judge accepts, the runner also exits 0 on.
- BUILD stage-complete: the artifact's declared ``files_changed`` reaches run.json
  ``files_touched`` (union with what was recorded, dedup on the resolved path),
  declared paths that are neither a file on disk nor a tracked deleted file are
  dropped and listed under ``dropped_from_declared`` (a deleted tracked file is kept,
  also when its directory is gone and in the Gate-2 diff pack; a directory is dropped;
  a file the workspace sweep already committed is kept), declared entries are stored
  absolute and listed under ``added_from_declared``, recorded entries are never
  dropped, and worktree mode keeps its own derivation.
- ``_lookup_already_committed`` credits a commit that carries this run's note
  (``(auto local-commit, run <id>;`` / ``Run: <id>``), not one that only mentions
  the run id.

METHODOLOGY
- Pure-function calls with literal dicts; evidence fixtures in the runner's C2 shape;
  tmp git repos for the real commits; the stage-boundary harness
  (``tests.test_stage_boundary_commands``) for stage-complete.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
for _p in (str(_BACKEND / "scripts"), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pipeline_validator as pv  # noqa: E402
import scripts.artifact_cli as cli  # noqa: E402
from tests.test_pipeline_worktree import flow  # noqa: E402,F401 — a fixture
from tests.test_stage_boundary_commands import (  # noqa: E402,F401 — `ws` is a fixture
    DECISION, _build_artifact, _c2, _complete, _make_run, _plan_artifact, _run_state,
    _start, _write_evidence, ws,
)

TRAILER = "Co-Authored-By: Swarm <swarm@swarmai.dev>"


def _deliver(title="Redact credentials in every displayed tool string",
             result_now="Every displayed tool string goes through one redactor. It runs before the cut.",
             approach=("one shared pattern module", "redact before cutting")):
    return {"title": title, "outcome": {"problem_before": "x", "result_now": result_now,
                                        "approach": list(approach)}}


# ── _commit_message ───────────────────────────────────────────────────────

class TestCommitMessage:
    def test_title_is_the_subject_and_the_body_carries_the_outcome(self):
        msg = cli._commit_message("run_ab12", "fix the thing in a long requirement", _deliver())
        lines = msg.splitlines()
        assert lines[0] == "Redact credentials in every displayed tool string"
        assert lines[1] == ""
        assert "Every displayed tool string goes through one redactor." in msg
        assert "- one shared pattern module" in msg and "- redact before cutting" in msg
        assert "run_ab12" in msg and "not pushed" in msg
        assert lines[-1] == TRAILER

    def test_long_title_is_cut_at_a_word_boundary(self):
        title = "Make every pipeline commit carry a real subject built from the delivery title instead of the raw text"
        subject = cli._commit_message("r", "req", _deliver(title=title)).splitlines()[0]
        assert len(subject) <= 72
        assert title.startswith(subject)
        assert title[len(subject)] == " ", f"cut mid-word: {subject!r}"

    def test_multi_line_title_is_folded(self):
        subject = cli._commit_message("r", "req", _deliver(title="Fix  the\nsubject \t line")).splitlines()[0]
        assert subject == "Fix the subject line"

    def test_auto_delivery_title_never_ends_mid_word(self):
        # the auto-delivery writer derives its title through commit_message
        req = "bugfix (security): chat tool rows now show the full Bash command and every argument"
        title = cli._commit_title(req)
        assert len(title) <= 72 and req.startswith(title) and req[len(title)] == " "
        assert cli._commit_message("r", req, _deliver(title=title)).splitlines()[0] == title

    @pytest.mark.parametrize("title,req", [("Fix login bug", "Fix login bugs in auth"),
                                           ("Add retry to the API client", "Add retry to the API clients")])
    def test_title_is_used_as_written(self, title, req):
        assert cli._commit_message("r", req, _deliver(title=title)).splitlines()[0] == title

    @pytest.mark.parametrize("req", ["Fix the login bug in auth", "Fix the login bug, and more",
                                     "Fix the login bug: details", "Fix the login bug (auth)"])
    def test_short_title_that_prefixes_the_requirement_is_kept(self, req):
        d = _deliver(title="Fix the login bug")
        assert cli._commit_message("r", req, d).splitlines()[0] == "Fix the login bug"

    def test_trailing_dash_is_kept(self):
        assert cli._commit_message("r", "req", _deliver(title="Add C--")).splitlines()[0] == "Add C--"

    def test_uncut_title_is_kept_as_written(self):
        assert cli._commit_message("r", "req", _deliver(title="Bump to v1.2.")).splitlines()[0] == "Bump to v1.2."

    def test_string_approach_is_not_spelled_out(self):
        msg = cli._commit_message("r", "req", {"title": "T", "outcome": {"approach": "not a list"}})
        assert "- n" not in msg and msg.splitlines()[-1] == TRAILER

    def test_early_word_boundary_is_used_instead_of_a_mid_word_cut(self):
        title = "Refactor the thing: https://example.com/" + "a" * 60 + " rest"
        subject = cli._commit_message("r", "req", _deliver(title=title)).splitlines()[0]
        assert subject == "Refactor the thing"

    def test_no_delivery_falls_back_to_the_requirement_first_line_at_a_word(self):
        req = ("One run, items 2+4 of the post-run audit. Attribution: a session that owns the run "
               "and more words here\nsecond line")
        subject = cli._commit_message("r", req, None).splitlines()[0]
        assert len(subject) <= 72 and req.startswith(subject)
        assert req[len(subject)] in " \n", f"cut mid-word: {subject!r}"

    def test_nothing_at_all(self):
        msg = cli._commit_message("r", "", None)
        assert msg.splitlines()[0] == "pipeline changes" and msg.splitlines()[-1] == TRAILER

    def test_template_title_is_not_a_title(self):
        subject = cli._commit_message("r", "Fix the thing", {"title": "Feature Name"}).splitlines()[0]
        assert subject == "Fix the thing"


class TestRunCommitUsesTheBuilder:
    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=30)

    def test_real_commit_subject_is_the_delivery_title(self, tmp_path):
        from tests.test_artifact_cli_run_commit import _run_commit, _write_run

        ws = tmp_path / "ws"
        (ws / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "f.py").write_text("v2\n")
        _write_run(ws, "run_msg", stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "f.py")])
        with patch.object(cli, "_load_stage_artifacts", return_value={"deliver": _deliver()}):
            _run_commit(ws, "run_msg")
        body = self._git(repo, "log", "-1", "--format=%B").stdout.strip()
        assert body.splitlines()[0] == "Redact credentials in every displayed tool string"
        assert body.splitlines()[-1] == TRAILER


@pytest.fixture
def token_oracle():
    """The worktree tests' token service (their autouse fixture does not travel with
    an imported ``flow``): CLI subprocesses burn their tokens through its socket."""
    import os

    from core import hook_token
    hook_token.stop_oracle()
    assert hook_token.start_oracle(50000 + (os.getpid() + 7) % 10000) or not hook_token.oracle_supported()
    yield
    hook_token.stop_oracle()


def _land_with(flow, message):
    import os

    import pipeline_worktree as pw
    from core import hook_token
    from tests.test_pipeline_worktree import _evidence, _g, _prepared, _state

    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
    try:
        pw.land_deliver(run_id=rid, run_file=f, run_state=_state(f), push_ready=True,
                        workspace=flow["ws"], self_repo=flow["main"],
                        gen_b=lambda wt, files: [], requirement="do x", message=message)
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)
    return _g(flow["main"], "log", "-1", "--format=%B")


def test_worktree_landing_without_a_message_uses_the_builder(flow, token_oracle):
    body = _land_with(flow, None).strip()
    assert body.splitlines()[0] == "do x" and body.splitlines()[-1] == TRAILER


def test_worktree_landing_uses_the_given_message(flow, token_oracle):
    import os

    import pipeline_worktree as pw
    from core import hook_token
    from tests.test_pipeline_worktree import _evidence, _g, _prepared, _state

    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    msg = cli._commit_message(rid, "do x", _deliver())
    os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
    try:
        pw.land_deliver(run_id=rid, run_file=f, run_state=_state(f), push_ready=True,
                        workspace=flow["ws"], self_repo=flow["main"],
                        gen_b=lambda wt, files: [], requirement="do x", message=msg)
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)
    assert _g(flow["main"], "log", "-1", "--format=%s") == "Redact credentials in every displayed tool string"


# ── judge_exit_criteria on mode: refs ────────────────────────────────────

def _verify_report(failures, passed=10, runner_errors=None, extra_counts=None):
    counts = {"passed": passed, "failed": 0, "errors": 0, "timeouts": 0, "skipped": 0}
    for f in failures:
        counts[{"failed": "failed", "error": "errors", "timeout": "timeouts"}[f["outcome"]]] += 1
    counts.update(extra_counts or {})
    ev = {"schema": 1, "mode": "verify", "run_id": "r", "started_at": "2026-01-01T00:10:00Z",
          "finished_at": "2026-01-01T00:10:00Z", "counts": counts, "results": [],
          "failures": list(failures)}
    if runner_errors:
        ev["runner_errors"] = runner_errors
    return ev


def _fail(cls, outcome="failed", n="t.py::a"):
    return {"nodeid": n, "classification": cls, "outcome": outcome, "summary": "x"}


CRIT = [{"id": "X9", "kind": "test", "ref": "mode:verify", "expect": "pass"}]


def _judge(tmp_path, ev):
    d = tmp_path / "evidence" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest-verify.json").write_text(json.dumps(ev))
    return pv.judge_exit_criteria(CRIT, tmp_path)


ACCEPTED = [
    [_fail("pre-existing", n="a.py::x"), _fail("pre-existing", n="b.py::y"), _fail("pre-existing", n="c.py::z")],
    [_fail("flaky")],
    [_fail("pre-existing", outcome="error")],
    [],
]
REJECTED = [
    ("regression", lambda: _verify_report([_fail("regression")])),
    ("new", lambda: _verify_report([_fail("new")])),
    ("unknown", lambda: _verify_report([_fail("unknown")])),
    ("timeout even if pre-existing", lambda: _verify_report([_fail("pre-existing", outcome="timeout")])),
    ("unclassified count", lambda: _verify_report([_fail("pre-existing")], extra_counts={"failed": 3})),
    ("runner error", lambda: _verify_report([], runner_errors=["shard 2 collected 0"])),
    ("nothing passed", lambda: _verify_report([_fail("pre-existing")], passed=0)),
    ("non-dict failure entry", lambda: {**_verify_report([_fail("pre-existing")]),
                                        "failures": ["t.py::a", _fail("pre-existing")]}),
]


@pytest.mark.parametrize("failures", ACCEPTED)
def test_mode_ref_met_when_every_failure_is_pre_existing_or_flaky(tmp_path, failures):
    res = _judge(tmp_path, _verify_report(failures))
    assert res["met"] is True, res["detail"]


@pytest.mark.parametrize("why,make", REJECTED, ids=[r[0] for r in REJECTED])
def test_mode_ref_unmet_otherwise(tmp_path, why, make):
    res = _judge(tmp_path, make())
    assert res["met"] is False and res["unmet"] == ["X9"], why


@pytest.mark.parametrize("cls", ["pre-existing", "flaky", "regression", "new", "unknown",
                                 "order-dependent", "timeout", "", None])
def test_judge_and_runner_agree_on_every_classification(cls):
    # one failure of this class: the judge accepts exactly what the runner accepts
    import pipeline_test

    ev = _verify_report([_fail(cls)])
    assert pv._evidence_green(ev) is (pipeline_test._exit_code(ev) == 0), cls


def test_global_deadline_timeout_accepted_and_per_test_hang_blocks(tmp_path):
    # A GLOBAL-deadline timeout (load/capacity signal) is accepted by BOTH the runner and
    # the judge; a per-test hang (same timeout outcome, no deadline flag) blocks in both.
    import pipeline_test

    dl = _verify_report([_fail("timeout", outcome="timeout")])
    dl["failures"][0]["deadline"] = True
    assert pipeline_test._failure_nonblocking(dl["failures"][0]) is True
    assert pipeline_test._exit_code(dl) == 0
    assert pv._evidence_green(dl) is True
    assert _judge(tmp_path, dl)["met"] is True

    hang = _verify_report([_fail("unknown", outcome="timeout")])  # no deadline flag
    assert pipeline_test._exit_code(hang) == 1
    assert pv._evidence_green(hang) is False


@pytest.mark.parametrize("failures", ACCEPTED)
def test_judge_never_accepts_what_the_runner_rejects(failures):
    import pipeline_test

    ev = _verify_report(failures)
    assert pv._evidence_green(ev) is True
    assert pipeline_test._exit_code(ev) == 0


def test_run_c5eb0858_shape_is_met(tmp_path):
    # 624 passed, 3 failures all classified pre-existing (the run that needed --force)
    ev = _verify_report([_fail("pre-existing", n=f"t{i}.py::a") for i in range(3)], passed=624)
    assert _judge(tmp_path, ev)["met"] is True


# ── BUILD records declared files_changed into files_touched ──────────────

def _to_build(reg, capsys, root, rid, **run_extra):
    rd = _make_run(root, rid, "direct", **run_extra)
    _start(reg, capsys, rid, "plan")
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    _write_evidence(rd, "tests/latest-tdd.json", _c2("tdd", rid, [
        {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
    _start(reg, capsys, rid, "build")
    return rd


def _complete_build(reg, capsys, rid, files_changed):
    art = _build_artifact()
    art["files_changed"] = list(files_changed)
    return _complete(reg, capsys, rid, "build", art,
                     {"decisions": DECISION, "exit_evidence": {"X2": "checked the page"}})


def _git_repo(path):
    """A git repo at ``path`` with one committed file (base.py)."""
    path.mkdir(parents=True)
    for a in (["init", "-q"], ["config", "user.email", "t@t.co"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, timeout=30)
    (path / "base.py").write_text("b\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True, timeout=30)
    return path


def test_declared_files_reach_files_touched_when_none_were_recorded(ws, capsys, tmp_path):
    root, reg = ws
    real = _git_repo(tmp_path / "src") / "changed.py"
    real.write_text("x = 1\n")
    _to_build(reg, capsys, root, "run_ft1")
    code, out = _complete_build(reg, capsys, "run_ft1", [str(real), "path/to/file.py"])
    assert code == 0, out
    assert _run_state(root, "run_ft1")["files_touched"] == [os.path.realpath(real)]


def test_recorded_duplicates_collapse(ws, capsys, tmp_path):
    root, reg = ws
    real = tmp_path / "src" / "dup.py"
    real.parent.mkdir()
    real.write_text("x\n")
    _to_build(reg, capsys, root, "run_ft7", files_touched=[str(real), str(real)])
    code, out = _complete_build(reg, capsys, "run_ft7", ["path/to/file.py"])
    assert code == 0, out
    assert _run_state(root, "run_ft7")["files_touched"] == [str(real)]


def test_declared_files_are_added_to_recorded_ones(ws, capsys, tmp_path):
    root, reg = ws
    real = _git_repo(tmp_path / "src") / "extra.py"
    real.write_text("x = 1\n")
    _to_build(reg, capsys, root, "run_ft2", files_touched=["backend/a.py"])
    code, out = _complete_build(reg, capsys, "run_ft2", [str(real), str(real)])
    assert code == 0, out
    assert _run_state(root, "run_ft2")["files_touched"] == ["backend/a.py", os.path.realpath(real)]


def test_recorded_only_run_is_unchanged(ws, capsys):
    root, reg = ws
    _to_build(reg, capsys, root, "run_ft3", files_touched=["backend/a.py", "backend/tests/test_x.py"])
    code, out = _complete_build(reg, capsys, "run_ft3", ["path/to/file.py"])
    assert code == 0, out
    assert _run_state(root, "run_ft3")["files_touched"] == ["backend/a.py", "backend/tests/test_x.py"]
    assert out["derived"]["files_changed"]["source"] == "run.json files_touched"


def test_deleted_tracked_file_is_kept(ws, capsys, tmp_path):
    root, reg = ws
    repo = tmp_path / "repo"
    repo.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t.co"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, timeout=30)
    (repo / "gone.py").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True, timeout=30)
    (repo / "gone.py").unlink()
    _to_build(reg, capsys, root, "run_ft4")
    code, out = _complete_build(reg, capsys, "run_ft4", [str(repo / "gone.py")])
    assert code == 0, out
    assert _run_state(root, "run_ft4")["files_touched"] == [os.path.realpath(repo / "gone.py")]


def test_deleted_file_with_its_directory_reaches_the_delivery_scope(ws, capsys, tmp_path):
    # `git rm -r pkg/`: the file's directory is gone too. The DELIVER scope (and the
    # Gate-2 diff) must still see the deletion run-commit will commit.
    root, reg = ws
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    for a in (["init", "-q"], ["config", "user.email", "t@t.co"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, timeout=30)
    (repo / "keep.py").write_text("k\n")
    (repo / "pkg" / "gone.py").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "rm", "-rq", "pkg"], check=True, timeout=30)
    gone = str(repo / "pkg" / "gone.py")
    _to_build(reg, capsys, root, "run_ft11")
    code, out = _complete_build(reg, capsys, "run_ft11", [gone])
    assert code == 0, out
    assert _run_state(root, "run_ft11")["files_touched"] == [os.path.realpath(gone)]
    assert cli._repo_root_for_file(gone) == cli._git_repo_root(gone) is not None
    scope = cli._changed_files_scope([gone])
    assert [(c["rel"], c["status"]) for c in scope["changed_files"]] == [("pkg/gone.py", "D")]
    pack = cli._diff_pack([os.path.realpath(gone)])
    assert "NOT INCLUDED" not in pack and "-x" in pack, pack


def test_declared_list_that_filters_to_nothing_fails_loud(ws, capsys):
    root, reg = ws
    _to_build(reg, capsys, root, "run_ft6")
    code, out = _complete_build(reg, capsys, "run_ft6", ["path/to/file.py", "no/such.py"])
    assert code != 0, out
    assert "none of the declared paths is a file on disk or a tracked deleted file" in json.dumps(out)
    assert not _run_state(root, "run_ft6").get("files_touched")


def test_declared_file_already_swept_into_a_commit_is_kept(ws, capsys, tmp_path):
    # The workspace auto-commit sweeps a script-written file within minutes: by BUILD
    # stage-complete git may show it unchanged, and it is still this run's change.
    root, reg = ws
    repo = _git_repo(tmp_path / "wsrepo")
    swept = repo / "Projects" / "X" / "notes.md"
    swept.parent.mkdir(parents=True)
    swept.write_text("written by a script\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "chore: sweep"], check=True, timeout=30)
    _to_build(reg, capsys, root, "run_ft12")
    code, out = _complete_build(reg, capsys, "run_ft12", [str(swept), "path/to/file.py"])
    assert code == 0, out
    assert _run_state(root, "run_ft12")["files_touched"] == [os.path.realpath(swept)]
    assert out["derived"]["files_changed"]["dropped_from_declared"] == ["path/to/file.py"]


def _repo_with_changed_dir(path):
    """A repo whose pkg/ directory holds a modified and an untracked file: the
    directory exists and holds real changes, so a check on "exists" or on "git shows
    it changed" would let it in."""
    repo = _git_repo(path)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "pkg"], check=True, timeout=30)
    (repo / "pkg" / "a.py").write_text("a2\n")
    (repo / "pkg" / "n.py").write_text("n\n")
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "pkg"],
                          capture_output=True, text=True, timeout=30).stdout.strip()
    return repo


def test_declared_directory_is_dropped(ws, capsys, tmp_path):
    root, reg = ws
    repo = _repo_with_changed_dir(tmp_path / "src")
    _to_build(reg, capsys, root, "run_ft8", files_touched=["backend/a.py"])
    code, out = _complete_build(reg, capsys, "run_ft8",
                                [str(repo / "pkg"), str(repo), str(repo / "pkg" / "n.py")])
    assert code == 0, out
    assert _run_state(root, "run_ft8")["files_touched"] == [
        "backend/a.py", os.path.realpath(repo / "pkg" / "n.py")]


def test_only_a_directory_declared_fails_loud(ws, capsys, tmp_path):
    root, reg = ws
    repo = _repo_with_changed_dir(tmp_path / "src")
    _to_build(reg, capsys, root, "run_ft9")
    code, out = _complete_build(reg, capsys, "run_ft9", [str(repo / "pkg")])
    assert code != 0, out
    assert not _run_state(root, "run_ft9").get("files_touched")


def test_symlinked_spelling_is_stored_by_realpath_and_reaches_the_scope(tmp_path):
    repo = _git_repo(tmp_path / "real")
    (repo / "new.py").write_text("n\n")
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    got = cli._union_touched([], [str(link / "new.py")])
    assert got == [os.path.realpath(repo / "new.py")]
    rows = cli._changed_files_scope(got)["changed_files"]
    assert [(c["rel"], c["status"]) for c in rows] == [("new.py", "A")]


def test_declared_relative_path_is_stored_absolute(ws, capsys, tmp_path, monkeypatch):
    root, reg = ws
    base = _git_repo(tmp_path / "wsroot")
    (base / "Projects" / "X").mkdir(parents=True)
    (base / "Projects" / "X" / "a.md").write_text("x\n")
    monkeypatch.setattr(cli, "_touched_bases", lambda: [str(tmp_path / "nowhere"), str(base)])
    _to_build(reg, capsys, root, "run_ft10", files_touched=["backend/a.py"])
    code, out = _complete_build(reg, capsys, "run_ft10", ["Projects/X/a.md"])
    assert code == 0, out
    want = os.path.realpath(base / "Projects" / "X" / "a.md")
    assert _run_state(root, "run_ft10")["files_touched"] == ["backend/a.py", want]
    assert out["derived"]["files_changed"]["added_from_declared"] == [want]


def test_isolated_run_keeps_the_worktree_derivation(ws, capsys, tmp_path, monkeypatch):
    import pipeline_worktree as pw

    root, reg = ws
    real = tmp_path / "src" / "declared.py"
    real.parent.mkdir()
    real.write_text("x\n")
    _to_build(reg, capsys, root, "run_ft5")
    monkeypatch.setattr(pw, "derive_files_touched", lambda *a, **k: ["/wt/run_ft5/a.py"])
    monkeypatch.setattr(pw, "is_isolated", lambda *a, **k: True)
    code, out = _complete_build(reg, capsys, "run_ft5", [str(real)])
    assert code == 0, out
    assert _run_state(root, "run_ft5")["files_touched"] == ["/wt/run_ft5/a.py"]


# ── _lookup_already_committed: the run's note, not a mention ─────────────

def _repo_with(tmp_path, message):
    repo = tmp_path / "r"
    repo.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t.co"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, timeout=30)
    (repo / "f.py").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True, timeout=30)
    return repo


@pytest.mark.parametrize("message,credited", [
    (cli._commit_message("run_aaaa1111", "do x", _deliver()), True),
    ("hand fix\n\nRun: run_aaaa1111", True),
    ("Follow-up fix (follow-up to run_aaaa1111)\n\n(auto local-commit, run run_bbbb2222; not pushed)",
     False),
    ("Finish the work of run run_aaaa1111\n\n(auto local-commit, run run_bbbb2222; not pushed)", False),
    (cli._commit_message("run_bbbb2222", "do y",
                         _deliver(result_now="Finishes what run_aaaa1111 left open.")), False),
])
def test_lookup_credits_the_runs_note_not_a_mention(tmp_path, message, credited):
    repo = _repo_with(tmp_path, message)
    got = cli._lookup_already_committed(str(repo), [str(repo / "f.py")],
                                        "2020-01-01T00:00:00+00:00", "run_aaaa1111")
    assert bool(got) is credited
