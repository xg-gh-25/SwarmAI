"""Every list of a change's files shown for review puts the heart of the change first.

Questions this answers:
  - Does ``core.review_order.role`` tell source, test and supporting files apart by path
    alone, across languages?
  - Does ``ordered`` put source files (largest change first) before tests before
    supporting files?
  - Do the three producers use it: the turn card (``turn_changes._block``, whose row cap
    then drops supporting files first), a landing's files (``session_landings``) and a
    Hive return's review (``hive_return._review``)?
"""
from __future__ import annotations

import subprocess

import pytest

from core import review_order as ro


@pytest.mark.parametrize("path,expected", [
    ("backend/core/engine.py", "source"),
    ("desktop/src/components/Card.tsx", "source"),
    ("cmd/server/main.go", "source"),
    ("backend/tests/test_engine.py", "test"),
    ("src/components/__tests__/Card.test.tsx", "test"),
    ("pkg/server_test.go", "test"),
    ("src/test/java/com/x/EngineTest.java", "test"),
    ("web/app.spec.ts", "test"),
    ("backend/tests/fixtures/sample.json", "test"),
    ("docs/design/plan.md", "supporting"),
    ("README.md", "supporting"),
    ("backend/generated/apps_index.json", "supporting"),
    ("desktop/src/i18n/locales/zh.json", "supporting"),
    ("package-lock.json", "supporting"),
    ("go.sum", "supporting"),
    (".github/workflows/ci.yml", "supporting"),
    ("backend/config.yaml", "supporting"),
])
def test_role_from_the_path(path, expected):
    assert ro.role(path) == expected


def test_source_largest_first_then_tests_then_supporting():
    files = [
        {"path": "README.md", "added": 500, "deleted": 0},
        {"path": "tests/test_a.py", "added": 80, "deleted": 0},
        {"path": "src/small.py", "added": 1, "deleted": 1},
        {"path": "src/big.py", "added": 40, "deleted": 30},
        {"path": "src/bin.dat", "added": None, "deleted": None},
        {"path": "tests/test_b.py", "added": 90, "deleted": 0},
    ]
    assert [f["path"] for f in ro.ordered(files)] == [
        "src/big.py", "src/small.py", "src/bin.dat",
        "tests/test_b.py", "tests/test_a.py", "README.md"]


def test_turn_card_orders_rows_and_its_cap_drops_supporting_files_first(monkeypatch):
    from core import turn_changes as tc
    monkeypatch.setattr(tc, "MAX_FILES", 2)
    rows = [{"path": p, "added": a, "deleted": 0, "gitRoot": "/r"}
            for p, a in (("docs/notes.md", 900), ("src/x.py", 3), ("tests/test_x.py", 50))]
    block = tc._block(rows)
    assert [f["path"] for f in block["files"]] == ["src/x.py", "tests/test_x.py"]
    assert block["omitted"] == 1


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def change(tmp_path, monkeypatch):
    """A repository whose second commit adds a doc, a test and two source files."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "seed.py").write_text("x = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("doc\n" * 300)
    (repo / "test_core.py").write_text("t\n" * 50)
    (repo / "big.py").write_text("b\n" * 20)
    (repo / "small.py").write_text("s\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return repo, base, _git(repo, "rev-parse", "HEAD")


EXPECTED = ["big.py", "small.py", "test_core.py", "README.md"]


def test_a_landing_lists_its_files_in_review_order(change):
    from core import session_landings as sl
    repo, base, tip = change
    land = sl._landing(str(repo), "main", sl.Deadline(10), "session/x", "turn-1", base, tip)
    assert [f["path"] for f in land["files"]] == EXPECTED


def test_a_hive_return_review_lists_its_files_in_review_order(change):
    from core import hive_return as hr
    repo, base, tip = change
    review = hr._review(repo, base, tip, [])
    assert [f["path"] for f in review["files"]] == EXPECTED
