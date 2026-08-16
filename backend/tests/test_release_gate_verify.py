"""Tests for the release-gate `--verify` pre-publish gate (run_d613bb27).

This is the FLOW-STEP replacement for the deleted `release_publish_guard` PreToolUse
hook. The enforcement moved from a product-wide per-command hook into the release flow
(s_swarm-release Stage 7c calls `release-gate --verify` before `gh release edit
--draft=false`). These tests pin the fail-CLOSED contract of the moved logic:
`_release_marker_authorizes_head` authorizes a publish IFF a CI-green marker attests the
commit being released — and BLOCKS (fail-closed) on every absence/mismatch/staleness.

Methodology: drive the real function with real marker JSON + a real temp git repo, so
the tag/HEAD deref runs the actual `git rev-parse`. Each BLOCK case is a negative that
goes RED if the function ever flips to fail-OPEN (the exact regression the deleted hook's
absence must not reintroduce).
"""
import json
import subprocess
from pathlib import Path

import pytest

# The moved logic lives in artifact_cli (imported as a module).
import importlib.util

_CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"
_spec = importlib.util.spec_from_file_location("artifact_cli_under_test", _CLI_PATH)
artifact_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(artifact_cli)

_authorizes = artifact_cli._release_marker_authorizes_head


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real one-commit git repo — the deref target for HEAD/tag resolution."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.dev")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-q", "-m", "c1")
    return r


def _marker(tmp_path: Path, **fields) -> Path:
    m = tmp_path / ".release-ci-green.json"
    m.write_text(json.dumps(fields))
    return m


# ── BLOCK cases (fail-closed) — each RED if the function flips to fail-open ──

def test_no_marker_blocks(tmp_path, repo):
    ok, reason = _authorizes(tmp_path / "absent.json", str(repo))
    assert ok is False
    assert "no CI-green marker" in reason


def test_unreadable_marker_blocks(tmp_path, repo):
    m = tmp_path / ".release-ci-green.json"
    m.write_text("{ not valid json")
    ok, reason = _authorizes(m, str(repo))
    assert ok is False
    assert "unreadable" in reason


def test_marker_missing_head_blocks(tmp_path, repo):
    m = _marker(tmp_path, repo_root=str(repo), run_id=1)  # no head_sha
    ok, reason = _authorizes(m, str(repo))
    assert ok is False
    assert "missing head_sha" in reason


def test_marker_missing_repo_root_blocks_even_with_cwd(tmp_path, repo):
    # A marker missing repo_root is stale/malformed → BLOCK, even though a caller
    # supplies a valid repo_root. fail-CLOSED: never fall back to the caller's cwd
    # (regression guard for the `or repo_root` weakening caught in adversarial review).
    head = _git(repo, "rev-parse", "HEAD")
    m = _marker(tmp_path, head_sha=head, run_id=1)  # no repo_root
    ok, reason = _authorizes(m, str(repo))  # caller DOES supply repo_root
    assert ok is False
    assert "missing head_sha/repo_root" in reason


def test_head_anchored_stale_marker_blocks(tmp_path, repo):
    # marker head_sha != current HEAD → stale → BLOCK
    m = _marker(tmp_path, head_sha="0" * 40, repo_root=str(repo), run_id=1)
    ok, reason = _authorizes(m, str(repo))
    assert ok is False
    assert "!= current HEAD" in reason


def test_tag_anchored_without_published_tag_blocks(tmp_path, repo):
    # marker is tag-anchored but caller names no tag → fail-closed
    head = _git(repo, "rev-parse", "HEAD")
    m = _marker(tmp_path, head_sha=head, repo_root=str(repo), tag="v9.9.9", run_id=1)
    ok, reason = _authorizes(m, str(repo), published_tag=None)
    assert ok is False
    assert "names no tag" in reason


def test_tag_anchored_wrong_commit_blocks(tmp_path, repo):
    # published tag derefs to a commit != the CI-verified marker commit → BLOCK
    _git(repo, "tag", "v9.9.9")  # tag on HEAD
    m = _marker(tmp_path, head_sha="1" * 40, repo_root=str(repo), tag="v9.9.9", run_id=1)
    ok, reason = _authorizes(m, str(repo), published_tag="v9.9.9")
    assert ok is False
    assert "CI not green on the" in reason


# ── PASS cases (only these authorize) ──

def test_head_anchored_matching_head_passes(tmp_path, repo):
    head = _git(repo, "rev-parse", "HEAD")
    m = _marker(tmp_path, head_sha=head, repo_root=str(repo), run_id=42)
    ok, reason = _authorizes(m, str(repo))
    assert ok is True
    assert "CI green on HEAD" in reason


def test_tag_anchored_matching_commit_passes(tmp_path, repo):
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v9.9.9")
    m = _marker(tmp_path, head_sha=head, repo_root=str(repo), tag="v9.9.9", run_id=42)
    ok, reason = _authorizes(m, str(repo), published_tag="v9.9.9")
    assert ok is True
    assert "matches" in reason
