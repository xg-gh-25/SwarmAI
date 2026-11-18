"""Expert benchmark, phase 1 step 1: cases seeded from a repository's history.

Design: docs/design/2026-11-18-expert-review-benchmark.md §3 (S1) and §4 (the sealed case).

Questions this answers, on a small repository built here:
  - Does SZZ name the commit B that wrote the lines a fix F changed, and give the defect
    lines in B's own coordinates even when a commit between B and F moved them?
  - Are the filters applied and counted: a fix that only adds lines, blamed lines split
    between two commits, a B diff above the size limit?
  - Does the red/green check keep a fix whose tests fail on the old source and pass on
    its own, and drop one whose tests pass either way?
  - Is the sealed case one commit (B's parent) with B's change uncommitted on top, with no
    other ref and no trace of F?
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.expert_bench import cases as bc
from core.expert_bench import sealed

PY = sys.executable


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
                          check=True, capture_output=True, text=True, env=env).stdout.strip()


def _commit(repo, message, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


GOOD = "def price(qty, unit):\n    if qty < 0:\n        raise ValueError(qty)\n    return qty * unit\n"
BUGGY = "def price(qty, unit):\n    if qty <= 0:\n        raise ValueError(qty)\n    return qty * unit\n"
TEST = ("import pytest\nfrom shop import price\n\n\ndef test_zero_is_free():\n"
        "    assert price(0, 5) == 0\n")


@pytest.fixture
def history(tmp_path, monkeypatch):
    """init -> B (writes the bug) -> X (moves its lines down) -> F (fixes it, adds a test)."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    init = _commit(repo, "init", {"README.md": "shop\n", "shop.py": "VERSION = 1\n"})
    b = _commit(repo, "add pricing", {"shop.py": "VERSION = 1\n\n\n" + BUGGY})
    x = _commit(repo, "add a header", {"shop.py": '"""Shop."""\n# one\n# two\n\nVERSION = 1\n\n\n' + BUGGY})
    f = _commit(repo, "fix: a zero quantity is free, not an error",
                {"shop.py": '"""Shop."""\n# one\n# two\n\nVERSION = 1\n\n\n' + GOOD,
                 "tests/test_shop.py": TEST})
    return {"repo": repo, "init": init, "b": b, "x": x, "f": f}


TEST_CMD = [PY, "-m", "pytest", "{tests}", "-q", "-p", "no:cacheprovider"]


def test_szz_names_b_with_labels_in_b_coordinates(history):
    found = bc.szz(history["repo"], history["f"])
    assert found.reason is None and found.commit == history["b"]
    assert found.share == 1.0
    # in B, "if qty <= 0" is line 5 (in F's parent it is line 9, after X's four lines)
    assert found.labels == {"shop.py": [(5, 5)]}


def test_extract_keeps_the_confirmed_case(history):
    cases, counts = bc.extract(history["repo"], test_cmd=TEST_CMD, grep=r"\bfix")
    assert counts["candidates"] == 1 and counts["kept"] == 1, counts
    [case] = cases
    assert (case["base"], case["head"]) == (history["init"], history["b"])
    assert case["goal"] == "add pricing" and case["source"] == "seeded"
    assert case["confirmed"] is True and case["labels"] == "partial"
    assert case["defects"] == [{"path": "shop.py", "from_line": 5, "to_line": 5,
                                "category": None, "fix": history["f"],
                                "note": "fix: a zero quantity is free, not an error"}]
    json.dumps(case)


def test_a_fix_whose_tests_pass_on_the_old_source_is_dropped(history):
    repo = history["repo"]
    _commit(repo, "fix: tidy wording", {"shop.py": (repo / "shop.py").read_text().replace("Shop.", "The shop."),
                                        "tests/test_wording.py": "def test_ok():\n    assert True\n"})
    cases, counts = bc.extract(repo, test_cmd=TEST_CMD, grep=r"\bfix")
    assert counts["candidates"] == 2 and counts["not_red_on_old_source"] == 1
    assert [c["defects"][0]["fix"] for c in cases] == [history["f"]]


def test_add_only_split_owner_and_size_filters_are_counted(history, monkeypatch):
    repo = history["repo"]
    _commit(repo, "fix: add a guard", {"shop.py": (repo / "shop.py").read_text() + "\nLIMIT = 9\n",
                                       "tests/test_limit.py": "def test_limit():\n    pass\n"})
    found = bc.szz(repo, _git(repo, "rev-parse", "HEAD"))
    assert found.reason == "add_only"
    # a fix touching lines from B and from X: B owns 1 of 2 changed lines
    _commit(repo, "fix: two at once", {"shop.py": (repo / "shop.py").read_text()
                                       .replace("# one", "# uno").replace("unit\n", "unit + 0\n", 1),
                                       "tests/test_two.py": "def test_two():\n    pass\n"})
    found = bc.szz(repo, _git(repo, "rev-parse", "HEAD"), owner_share=0.8)
    assert found.reason == "no_single_owner" and found.share == 0.5
    found = bc.szz(repo, history["f"], max_diff_lines=2)
    assert found.reason == "diff_too_large"


def test_a_red_run_without_a_failed_test_is_dropped(history):
    repo = history["repo"]
    # F2 adds a function and a test that imports it: on the old source the test cannot load
    text = (repo / "shop.py").read_text()
    _commit(repo, "fix: add a discount", {"shop.py": text.replace("VERSION = 1", "VERSION = 1\nRATE = 2"),
                                          "tests/test_rate.py": "from shop import RATE\n\n\ndef test_rate():\n    assert RATE == 2\n"})
    f2 = _git(repo, "rev-parse", "HEAD")
    kept, reason, _ = bc.red_green(repo, f2, TEST_CMD)
    assert kept and not reason          # red, but only because the import fails
    kept, reason, _ = bc.red_green(repo, f2, TEST_CMD, red_pattern=r"^FAILED ")
    assert (kept, reason) == (False, "red_without_failed_test")
    kept, reason, out = bc.red_green(repo, history["f"], TEST_CMD, red_pattern=r"^FAILED ")
    assert kept and "test_zero_is_free" in out


def test_extract_without_a_test_command_keeps_unconfirmed_cases(history):
    cases, counts = bc.extract(history["repo"], test_cmd=None, grep=r"\bfix")
    assert counts["kept"] == 1 and cases[0]["confirmed"] is False


def test_sealed_case_is_one_commit_with_the_change_uncommitted(history, tmp_path):
    [case] = bc.extract(history["repo"], test_cmd=None, grep=r"\bfix")[0]
    dest = sealed.build(history["repo"], case, tmp_path / "sealed")
    log = _git(dest, "log", "--all", "--format=%H")
    assert len(log.splitlines()) == 1
    assert _git(dest, "for-each-ref") .count("\n") == 0 and not _git(dest, "remote")
    assert "if qty <= 0" in (dest / "shop.py").read_text()          # B's change, applied
    assert "if qty <= 0" not in _git(dest, "show", "HEAD:shop.py")   # and not committed
    assert not (dest / "tests" / "test_shop.py").exists()            # nothing of F
    assert _git(dest, "status", "--porcelain").strip()


def test_the_cli_keeps_each_outcome_and_resumes(history, tmp_path, capsys):
    from scripts import expert_bench
    out = tmp_path / "cases.jsonl"
    args = ["extract", "--repo", str(history["repo"]), "--out", str(out), "--grep", r"\bfix"]
    assert expert_bench.main(args) == 0
    assert json.loads(capsys.readouterr().out)["kept"] == 1
    assert len(out.read_text().splitlines()) == 1
    outcomes = (tmp_path / "cases.jsonl.outcomes.jsonl").read_text().splitlines()
    assert [json.loads(l)["outcome"] for l in outcomes] == ["kept"]
    assert expert_bench.main(args) == 0                  # a second run skips what is decided
    assert json.loads(capsys.readouterr().out) == {"candidates": 0, "kept": 0}
    assert len(out.read_text().splitlines()) == 1


# --- the correctness review's findings, one test each -------------------------------

def test_a_fix_that_deletes_a_source_folder_does_not_crash_or_blame_the_deleted_file(history):
    repo = history["repo"]
    _commit(repo, "extra module", {"pkg2/old.py": "A = 1\nB = 2\n"})
    _git(repo, "rm", "-q", "pkg2/old.py")
    f2 = _commit(repo, "fix: drop old module, round the price",
                 {"shop.py": (repo / "shop.py").read_text().replace("return qty * unit", "return qty * unit + 0"),
                  "tests/test_more.py": "def test_more():\n    pass\n"})
    found = bc.szz(repo, f2)
    assert found.reason is None and set(found.labels) == {"shop.py"}
    # the old source puts pkg2/old.py back into a folder F removed: no crash, a decision
    assert bc.red_green(repo, f2, TEST_CMD) == (False, "not_red_on_old_source", "")


def test_a_candidate_that_raises_is_an_error_outcome_and_the_run_goes_on(history, monkeypatch):
    seen = []
    real = bc.szz
    monkeypatch.setattr(bc, "szz", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cases, counts = bc.extract(history["repo"], test_cmd=None, grep=r"\bfix",
                               on_outcome=lambda f, o, c, d: seen.append((o, d)))
    assert counts["error"] == 1 and not cases and seen == [("error", "RuntimeError: boom")]
    monkeypatch.setattr(bc, "szz", real)


def test_a_resumed_run_keeps_one_case_per_b(history, tmp_path, capsys):
    from scripts import expert_bench
    repo = history["repo"]
    # a second, newer fix of a line B wrote ("return qty * unit")
    _commit(repo, "fix: round the price", {"shop.py": (repo / "shop.py").read_text()
                                           .replace("return qty * unit", "return round(qty * unit, 2)"),
                                           "tests/test_round.py": "def test_round():\n    pass\n"})
    out = tmp_path / "cases.jsonl"
    args = ["extract", "--repo", str(repo), "--out", str(out), "--grep", r"\bfix"]
    expert_bench.main(args + ["--limit", "1"])          # the newer fix: B kept
    expert_bench.main(args)                              # resumed: the older fix of the same B
    heads = [json.loads(l)["head"] for l in out.read_text().splitlines()]
    assert heads == [history["b"]]
    outcomes = [json.loads(l)["outcome"] for l in (tmp_path / "cases.jsonl.outcomes.jsonl")
                .read_text().splitlines()]
    assert outcomes == ["kept", "duplicate_b"]


def test_a_half_written_outcome_line_does_not_stop_a_resume(history, tmp_path, capsys):
    from scripts import expert_bench
    out = tmp_path / "cases.jsonl"
    (tmp_path / "cases.jsonl.outcomes.jsonl").write_text('{"fix": "abc", "outc')
    assert expert_bench.main(["extract", "--repo", str(history["repo"]), "--out", str(out),
                              "--grep", r"\bfix"]) == 0


def test_export_ignore_and_a_force_tracked_file_reach_the_sealed_case(history, tmp_path):
    repo = history["repo"]
    _commit(repo, "attributes", {".gitattributes": "tests/** export-ignore\n", ".gitignore": "gen.py\n"})
    (repo / "gen.py").write_text("G = 1\n")
    _git(repo, "add", "-f", "gen.py")
    _git(repo, "commit", "-qm", "tracked though ignored")
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "change both", {"gen.py": "G = 2\n", "tests/test_g.py": "def test_g():\n    pass\n"})
    dest = sealed.build(repo, {"base": base, "head": head}, tmp_path / "sealed")
    # tracked in the sealed repo, so the reviewer's `git diff` shows B's change to it
    assert _git(dest, "ls-files", "gen.py") == "gen.py"
    assert "gen.py" in _git(dest, "diff", "--name-only").split()
    assert (dest / "gen.py").read_text() == "G = 2\n"
    assert (dest / "tests" / "test_g.py").exists() and (dest / "tests" / "test_shop.py").exists()


def test_a_merge_commit_is_never_b(history):
    repo = history["repo"]
    _git(repo, "checkout", "-q", "-b", "side", history["b"])
    _commit(repo, "side edit", {"other.py": "O = 1\n"})
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    merge = _git(repo, "rev-parse", "HEAD")
    # a fix of lines only the merge wrote: resolve by rewriting shop.py in the merge
    (repo / "shop.py").write_text((repo / "shop.py").read_text() + "MERGED = 1\n")
    _git(repo, "commit", "-q", "--amend", "-am", "Merge side")
    merge = _git(repo, "rev-parse", "HEAD")
    f2 = _commit(repo, "fix: merged constant", {"shop.py": (repo / "shop.py").read_text()
                                                .replace("MERGED = 1", "MERGED = 2"),
                                                "tests/test_m.py": "def test_m():\n    pass\n"})
    found = bc.szz(repo, f2)
    assert (found.reason, found.commit) == ("merge_b", merge)


def test_a_timeout_kills_the_whole_test_run(tmp_path):
    marker = tmp_path / "child.pid"
    script = f"import os,subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nopen({str(marker)!r},'w').write(str(p.pid))\ntime.sleep(60)\n"
    rc, out = bc._run([PY, "-c", script], tmp_path, timeout=2)
    assert (rc, out) == (-9, "timed out")
    import os, time
    pid = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the grandchild outlived the timeout")


def test_tests_inside_an_argument_and_no_tests(history):
    repo = history["repo"]
    sh = ["sh", "-c", f"{PY} -m pytest {{tests}} -q -p no:cacheprovider"]
    kept, reason, out = bc.red_green(repo, history["f"], sh, red_pattern=r"^FAILED ")
    assert kept and "test_zero_is_free" in out
    nt = _commit(repo, "fix: no test kept", {"shop.py": (repo / "shop.py").read_text() + "\n"})
    assert bc.red_green(repo, nt, TEST_CMD) == (False, "no_tests", "")
