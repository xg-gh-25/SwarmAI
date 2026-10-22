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
        _write_run(ws, "run_msg", stages=[{"stage": "deliver", "status": "completed", "push_ready": True},
                                          {"stage": "reflect", "status": "recorded", "lessons": ["[pitfall] a durable lesson long enough to count"]}],
                   files_touched=[str(repo / "f.py")])
        with patch.object(cli, "_load_stage_artifacts", return_value={"deliver": _deliver()}):
            _run_commit(ws, "run_msg")
        body = self._git(repo, "log", "-1", "--format=%B").stdout.strip()
        assert body.splitlines()[0] == "Redact credentials in every displayed tool string"
        assert body.splitlines()[-1] == TRAILER


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


def test_mode_verify_is_answered_by_a_regression_report(tmp_path):
    """A profile with a TEST stage skips BUILD's verify: regression (verify's scope plus
    L1 and L3) answers mode:verify, and the newest of the two decides."""
    d = tmp_path / "evidence" / "tests"
    d.mkdir(parents=True)
    reg = {**_verify_report([]), "mode": "regression", "finished_at": "2026-01-01T00:20:00Z"}
    (d / "latest-regression.json").write_text(json.dumps(reg))
    assert pv.judge_exit_criteria(CRIT, tmp_path)["met"] is True
    bad = {**_verify_report([_fail("regression")]), "finished_at": "2026-01-01T00:30:00Z"}
    (d / "latest-verify.json").write_text(json.dumps(bad))
    assert pv.judge_exit_criteria(CRIT, tmp_path)["met"] is False
    # mode:regression is NOT answered by a verify report
    crit = [{"id": "X8", "kind": "test", "ref": "mode:regression", "expect": "pass"}]
    (d / "latest-regression.json").unlink()
    (d / "latest-verify.json").write_text(json.dumps(_verify_report([])))
    assert pv.judge_exit_criteria(crit, tmp_path)["met"] is False


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


def test_global_deadline_timeout_is_unverified_in_runner_judge_and_push_ready(tmp_path):
    # A GLOBAL-deadline timeout never finished: runner, judge and the push-ready bound all
    # refuse it (unverified), and the push-ready reason says re-run, not edit code. It is
    # not counted as a regression in METRICS.
    import pipeline_test

    dl = _verify_report([_fail("timeout", outcome="timeout")])
    dl["failures"][0]["deadline"] = True
    assert pipeline_test._failure_nonblocking(dl["failures"][0]) is False
    assert pipeline_test._exit_code(dl) == 1
    assert pv._evidence_green(dl) is False
    assert _judge(tmp_path, dl)["met"] is False
    ok, reasons = pv.push_ready_upper_bound({"quality": {"tests_pass": True}}, "full",
                                            test_evidence=dl)
    assert ok is False and any("never finished" in r and "do not edit code" in r
                               for r in reasons), reasons
    assert cli._test_counts_from_evidence(dl)["failed"] == 0  # not a regression

    hang = _verify_report([_fail("unknown", outcome="timeout")])  # per-test hang
    assert pipeline_test._exit_code(hang) == 1
    assert pv._evidence_green(hang) is False
    assert cli._test_counts_from_evidence(hang)["failed"] == 1  # a hang IS a failure


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

def _git_repo(path):
    """A git repo at ``path`` with one committed file (base.py)."""
    path.mkdir(parents=True)
    for a in (["init", "-q"], ["config", "user.email", "t@t.co"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, timeout=30)
    (path / "base.py").write_text("b\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True, timeout=30)
    return path


def test_symlinked_spelling_is_stored_by_realpath_and_reaches_the_scope(tmp_path):
    repo = _git_repo(tmp_path / "real")
    (repo / "new.py").write_text("n\n")
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    got = cli._union_touched([], [str(link / "new.py")])
    assert got == [os.path.realpath(repo / "new.py")]
    rows = cli._changed_files_scope(got)["changed_files"]
    assert [(c["rel"], c["status"]) for c in rows] == [("new.py", "A")]


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
