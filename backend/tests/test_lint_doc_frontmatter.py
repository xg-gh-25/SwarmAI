"""lint_doc_frontmatter --diff: CI checks exactly the docs a change adds or modifies.

Closing guards D3 (docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md):
the product no longer rewrites SwarmAI's docs; SwarmAI's CI checks them instead.
Each test builds a throwaway git repo, so nothing touches this checkout.
"""
import subprocess
import sys
from pathlib import Path

LINT = Path(__file__).resolve().parents[2] / "scripts" / "lint_doc_frontmatter.py"
GOOD = "---\ntitle: t\nstatus: draft\ncreated: 2026-10-01\nupdated: 2026-10-02\n---\n# x\n"
BAD = "# no frontmatter\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "r"
    (repo / "docs" / "sub").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "docs" / "old-bad.md").write_text(BAD)       # pre-existing, untouched
    (repo / "docs" / "sub" / "keep.md").write_text(GOOD)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def _lint(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LINT), *args], cwd=repo,
                          capture_output=True, text=True, timeout=30)


def test_added_doc_without_frontmatter_fails_even_in_a_subdir(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"docs/sub/new.md": BAD})
    r = _lint(repo, "--diff", base)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "docs/sub/new.md" in r.stderr
    assert "old-bad.md" not in r.stderr          # untouched docs are not checked


def test_modified_doc_that_loses_frontmatter_fails(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"docs/sub/keep.md": BAD})
    assert _lint(repo, "--diff", base).returncode == 1


def test_clean_change_and_untouched_bad_doc_pass(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"docs/new.md": GOOD, "README.md": BAD})   # README outside docs/
    r = _lint(repo, "--diff", base)
    assert r.returncode == 0, r.stdout + r.stderr


def test_renamed_and_edited_doc_is_still_checked(tmp_path):
    # A long body keeps git's similarity high, so without --no-renames the
    # move shows up as R and --diff-filter=ACM would drop it.
    repo, base = _repo(tmp_path)
    body = "".join(f"line {i} of a long design doc\n" for i in range(200))
    _commit(repo, {"docs/sub/long.md": GOOD + body})
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "docs/sub/long.md", "docs/sub/moved.md")
    _commit(repo, {"docs/sub/moved.md": "# frontmatter dropped\n" + body})
    assert "R" in _git(repo, "diff", "--name-status", "-M", base, "HEAD")[:1]
    assert _lint(repo, "--diff", base).returncode == 1


def test_checks_the_committed_text_not_the_working_tree(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"docs/new.md": BAD})
    (repo / "docs" / "new.md").write_text(GOOD)   # uncommitted fix does not count
    assert _lint(repo, "--diff", base).returncode == 1


def test_unknown_base_fails_closed(tmp_path):
    repo, _ = _repo(tmp_path)
    r = _lint(repo, "--diff", "0" * 40)
    assert r.returncode == 1
    assert "failed" in r.stderr


def test_diff_without_base_is_a_usage_error(tmp_path):
    repo, _ = _repo(tmp_path)
    assert _lint(repo, "--diff").returncode == 2
