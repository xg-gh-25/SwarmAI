"""Tests for ddd_bindings — DDD↔repo binding schema + PULL/codeIntel (Run 1 BIND layer).

Acceptance criteria tested:
- AC1: load_bindings() accepts a §2c-conformant bindings.yaml and REJECTS malformed
  docs with a field-specific error. ≥3 invalid shapes go RED (non-vacuous — each
  passes only because the validator rejects it).
- AC2: bind_repo() clones a non-Midway target (local git fixture) into a worktree and
  builds a codeIntel graph via parse_repo + bulk_insert. node_count > 0.
- AC4: the shipped Projects/AIDLC/bindings.yaml parses via load_bindings() with 0 errors.

Design: Projects/AIDLC/.artifacts/runs/run_bb2c5bbe/GAP-REPORT-AND-SCHEMA.md §2c (frozen schema).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# These imports FAIL until the module exists (RED phase)
from core.ddd_bindings import (
    Binding,
    BindingsDoc,
    DeliveryContract,
    bind_repo,
    load_bindings,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_DOC = {
    "bindings": [
        {
            "repo": "adlc-workflows",
            "kind": "external",
            "clone": "https://github.com/example/adlc-workflows.git",
            "worktree": None,
            "code_intel": None,
            "delivery_contract": {
                "remote_kind": "github-pr",
                "branch": "main",
                "review_path": "s_swarm-code-reviewer",
                "auto_send": "on-clean-review",
            },
        },
        {
            "repo": "GCRAIDLCPreset",
            "kind": "internal",
            "clone": "brazil ws create --name GCRAIDLCPreset",
            "worktree": None,
            "code_intel": None,
            "delivery_contract": {
                "remote_kind": "code-amazon-cr",
                "build_system": "brazil",
                "branch": "mainline",
                "version_set": "GCRAIDLCPreset/development",
                "review_path": "s_swarm-code-reviewer",
                "auto_send": "on-clean-review",
            },
        },
    ]
}


@pytest.fixture
def valid_yaml(tmp_path: Path) -> Path:
    import yaml

    p = tmp_path / "bindings.yaml"
    p.write_text(yaml.safe_dump(VALID_DOC), encoding="utf-8")
    return p


@pytest.fixture
def local_git_repo(tmp_path: Path) -> Path:
    """A tiny real git repo with one python file — cloned offline in AC2."""
    repo = tmp_path / "srcrepo"
    repo.mkdir()
    (repo / "hello.py").write_text(
        "def greet(name):\n    return f'hello {name}'\n\n\nclass Greeter:\n"
        "    def run(self):\n        return greet('world')\n",
        encoding="utf-8",
    )
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    e = {**os.environ, **env}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=e)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=e)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=e)
    return repo


# ---------------------------------------------------------------------------
# AC1 — schema validation (pure, non-vacuous)
# ---------------------------------------------------------------------------

def test_valid_doc_parses(valid_yaml: Path):
    doc = load_bindings(valid_yaml)
    assert isinstance(doc, BindingsDoc)
    assert len(doc.bindings) == 2
    # build_system defaults to "none" for the external repo (not specified)
    assert doc.bindings[0].delivery_contract.build_system == "none"
    # explicit brazil for the internal repo
    assert doc.bindings[1].delivery_contract.build_system == "brazil"


def test_reject_missing_repo(tmp_path: Path):
    import yaml

    bad = {"bindings": [{"kind": "external", "clone": "x",
                         "delivery_contract": {"remote_kind": "github-pr",
                                               "branch": "main",
                                               "review_path": "r",
                                               "auto_send": "on-clean-review"}}]}
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_bindings(p)
    assert "repo" in str(ei.value)  # error must NAME the missing field


def test_reject_bad_remote_kind_enum(tmp_path: Path):
    import yaml

    bad = {"bindings": [{"repo": "x", "kind": "external", "clone": "x",
                         "delivery_contract": {"remote_kind": "gitlab-mr",  # invalid
                                               "branch": "main",
                                               "review_path": "r",
                                               "auto_send": "on-clean-review"}}]}
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_bindings(p)
    assert "remote_kind" in str(ei.value)


def test_reject_missing_review_path(tmp_path: Path):
    import yaml

    bad = {"bindings": [{"repo": "x", "kind": "external", "clone": "x",
                         "delivery_contract": {"remote_kind": "github-pr",
                                               "branch": "main",
                                               "auto_send": "on-clean-review"}}]}
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_bindings(p)
    assert "review_path" in str(ei.value)


def test_reject_bad_kind_enum(tmp_path: Path):
    import yaml

    bad = {"bindings": [{"repo": "x", "kind": "submodule",  # invalid
                         "clone": "x",
                         "delivery_contract": {"remote_kind": "github-pr",
                                               "branch": "main",
                                               "review_path": "r",
                                               "auto_send": "on-clean-review"}}]}
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_bindings(p)
    assert "kind" in str(ei.value)


def test_reject_non_list_bindings(tmp_path: Path):
    import yaml

    bad = {"bindings": {"repo": "x"}}  # object, not array — §2c requires array
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_bindings(p)


# ---------------------------------------------------------------------------
# AC2 — clone + codeIntel over a bound repo (real, offline)
# ---------------------------------------------------------------------------

def test_bind_repo_clones_and_indexes(local_git_repo: Path, tmp_path: Path):
    binding = Binding(
        repo="srcrepo",
        kind="external",
        clone=str(local_git_repo),  # local path — git clone handles it offline
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_swarm-code-reviewer", auto_send="on-clean-review",
        ),
    )
    worktree_root = tmp_path / "bindings"
    result = bind_repo(binding, worktree_root)
    assert result.node_count > 0, "codeIntel graph should have parsed hello.py symbols"
    assert Path(result.worktree).exists()
    assert Path(result.code_intel_db).exists()


def test_bind_repo_is_idempotent(local_git_repo: Path, tmp_path: Path):
    """Re-binding must not fail on an existing worktree (Gate-1 blocker 1)."""
    binding = Binding(
        repo="srcrepo", kind="external", clone=str(local_git_repo),
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_swarm-code-reviewer", auto_send="on-clean-review",
        ),
    )
    worktree_root = tmp_path / "bindings"
    r1 = bind_repo(binding, worktree_root)
    r2 = bind_repo(binding, worktree_root)  # must not raise
    assert r2.node_count == r1.node_count


def test_bind_repo_rejects_absolute_worktree_escape(tmp_path: Path):
    """A crafted absolute binding.worktree must NOT let rmtree escape the root (Gate-2 HIGH)."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete", encoding="utf-8")
    binding = Binding(
        repo="srcrepo", kind="external", clone="https://example.com/x.git",
        worktree=str(victim),  # absolute path OUTSIDE the bindings root
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_swarm-code-reviewer", auto_send="on-clean-review",
        ),
    )
    with pytest.raises(ValueError) as ei:
        bind_repo(binding, tmp_path / "bindings")
    assert "escapes" in str(ei.value)
    assert victim.exists() and (victim / "keep.txt").exists()  # NOT deleted


def test_bind_repo_rejects_traversal_in_repo(tmp_path: Path):
    """binding.repo with a path separator / '..' must be rejected before any rmtree."""
    binding = Binding(
        repo="../../etc", kind="external", clone="https://example.com/x.git",
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_swarm-code-reviewer", auto_send="on-clean-review",
        ),
    )
    with pytest.raises(ValueError):
        bind_repo(binding, tmp_path / "bindings")


def test_bind_repo_internal_brazil_is_deferred(tmp_path: Path):
    """internal/brazil clone is out of scope for Run 1 (pre-Run-2 Midway spike)."""
    binding = Binding(
        repo="GCRAIDLCPreset", kind="internal",
        clone="brazil ws create --name GCRAIDLCPreset",
        delivery_contract=DeliveryContract(
            remote_kind="code-amazon-cr", build_system="brazil", branch="mainline",
            review_path="s_swarm-code-reviewer", auto_send="on-clean-review",
        ),
    )
    with pytest.raises(NotImplementedError):
        bind_repo(binding, tmp_path / "bindings")


# ---------------------------------------------------------------------------
# AC4 — the shipped AIDLC bindings.yaml is schema-valid
# ---------------------------------------------------------------------------

def test_aidlc_bindings_yaml_is_valid():
    from jobs.paths import PROJECTS_DIR

    p = PROJECTS_DIR / "AIDLC" / "bindings.yaml"
    if not p.exists():
        pytest.skip("AIDLC/bindings.yaml not yet written")
    doc = load_bindings(p)
    assert len(doc.bindings) >= 1
