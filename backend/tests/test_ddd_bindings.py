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
    classify_project,
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
            "delivery_contract": {
                "remote_kind": "github-pr",
                "branch": "main",
                "review_path": "s_internal-crux-review",
                "auto_send": "on-clean-review",
            },
        },
        {
            "repo": "GCRAIDLCPreset",
            "kind": "internal",
            "clone": "brazil ws create --name GCRAIDLCPreset",
            "worktree": None,
            "delivery_contract": {
                "remote_kind": "code-amazon-cr",
                "build_system": "brazil",
                "branch": "mainline",
                "version_set": "GCRAIDLCPreset/development",
                "review_path": "s_internal-crux-review",
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
            review_path="s_internal-crux-review", auto_send="on-clean-review",
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
            review_path="s_internal-crux-review", auto_send="on-clean-review",
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
            review_path="s_internal-crux-review", auto_send="on-clean-review",
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
            review_path="s_internal-crux-review", auto_send="on-clean-review",
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
            review_path="s_internal-crux-review", auto_send="on-clean-review",
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


# ---------------------------------------------------------------------------
# Run 2 — Delivery Contract expansion (deploy_pipeline + refresh_policy) and
# code_intel field removal (DDD-agent-brain spec §3.6 ⑤ + derived-projection rule)
# ---------------------------------------------------------------------------

def test_self_hosted_main_and_local_script_are_valid():
    """A self-hosted main-only repo built by a repo-local script (SwarmAI's shape)
    must be expressible with HONEST Literal values — no fudging to github-pr/none.
    remote_kind='self-hosted-main' + build_system='local-script' must construct."""
    dc = DeliveryContract(
        remote_kind="self-hosted-main", build_system="local-script", branch="main",
        review_path="s_autonomous-pipeline", auto_send="manual-push",
    )
    assert dc.remote_kind == "self-hosted-main"
    assert dc.build_system == "local-script"


def test_local_script_build_is_not_deferred(tmp_path: Path):
    """AC3: only brazil/internal defer. A self-hosted external repo built by a
    local script must NOT raise NotImplementedError — its worktree already exists,
    it is not an unbuildable Midway target. (Uses a real local git fixture so bind
    proceeds past the deferral gate.)"""
    repo = tmp_path / "selfhosted"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    binding = Binding(
        repo="selfhosted", kind="external", clone=str(repo),
        delivery_contract=DeliveryContract(
            remote_kind="self-hosted-main", build_system="local-script", branch="main",
            review_path="s_autonomous-pipeline", auto_send="manual-push",
        ),
    )
    # must NOT raise NotImplementedError (the brazil/internal deferral must not fire)
    result = bind_repo(binding, tmp_path / "bindings")
    assert result is not None


def test_swarmai_bindings_yaml_is_valid():
    """The de-fudged SwarmAI bindings.yaml parses with the honest values."""
    from jobs.paths import PROJECTS_DIR

    p = PROJECTS_DIR / "SwarmAI" / "bindings.yaml"
    if not p.exists():
        pytest.skip("SwarmAI/bindings.yaml not present")
    doc = load_bindings(p)
    assert len(doc.bindings) >= 1
    dc = doc.bindings[0].delivery_contract
    assert dc.remote_kind == "self-hosted-main"
    assert dc.build_system == "local-script"


def test_delivery_contract_new_fields_default_none():
    """AC1: DeliveryContract gains deploy_pipeline + refresh_policy — both Optional,
    default None. A minimal contract (no new fields) must still construct and leave
    both at None (backward-compat: a v1 bindings.yaml omits them)."""
    dc = DeliveryContract(
        remote_kind="github-pr", branch="main",
        review_path="s_internal-crux-review", auto_send="on-clean-review",
    )
    assert dc.deploy_pipeline is None, "deploy_pipeline must default None when omitted"
    assert dc.refresh_policy is None, "refresh_policy must default None when omitted"
    # version_set is KEPT (frozen §2c schema member + ⑤ field) — asserting its
    # continued existence guards against an accidental symmetric removal.
    assert dc.version_set is None


def test_delivery_contract_new_fields_roundtrip(tmp_path: Path):
    """AC1: the two new ⑤ pointer fields load from yaml and are readable as data."""
    import yaml

    doc = {
        "bindings": [{
            "repo": "x", "kind": "internal", "clone": "brazil ws create",
            "delivery_contract": {
                "remote_kind": "code-amazon-cr", "build_system": "brazil",
                "branch": "mainline", "version_set": "X/development",
                "deploy_pipeline": "pipelines.amazon.com/pipelines/GCRAIDLCPreset",
                "refresh_policy": "on-develop",
                "review_path": "s_internal-crux-review", "auto_send": "on-clean-review",
            },
        }]
    }
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    loaded = load_bindings(p)
    dc = loaded.bindings[0].delivery_contract
    assert dc.deploy_pipeline == "pipelines.amazon.com/pipelines/GCRAIDLCPreset"
    assert dc.refresh_policy == "on-develop"
    assert dc.version_set == "X/development"  # unchanged, still carried


def test_legacy_code_intel_field_is_ignored(tmp_path: Path):
    """AC2: code_intel is REMOVED from the Binding schema (derived-projection rule
    §3.6 — the projection is NOT a binding member). A legacy bindings.yaml that
    still lists `code_intel: null` must STILL load (pydantic extra='ignore' default),
    and the loaded Binding must NOT expose a code_intel attribute."""
    import yaml

    legacy = {
        "bindings": [{
            "repo": "adlc-workflows", "kind": "external",
            "clone": "https://github.com/example/x.git",
            "worktree": None,
            "code_intel": None,   # ← legacy field, must be ignored, not rejected
            "delivery_contract": {
                "remote_kind": "github-pr", "branch": "main",
                "review_path": "s_internal-crux-review", "auto_send": "on-clean-review",
            },
        }]
    }
    p = tmp_path / "legacy.yaml"
    p.write_text(yaml.safe_dump(legacy), encoding="utf-8")
    doc = load_bindings(p)  # must NOT raise
    b = doc.bindings[0]
    assert not hasattr(b, "code_intel"), (
        "code_intel must be gone from the Binding model (derived-projection rule) — "
        "a legacy yaml carrying it loads via extra-ignore, but the field is not a member"
    )


def test_bind_repo_db_path_derived_from_worktree_only(local_git_repo: Path, tmp_path: Path):
    """AC2: with code_intel removed, bind_repo derives db_path solely from the worktree
    (worktree.parent/<repo>.code_intel.db) — no per-binding override path exists."""
    binding = Binding(
        repo="srcrepo", kind="external", clone=str(local_git_repo),
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_internal-crux-review", auto_send="on-clean-review",
        ),
    )
    worktree_root = tmp_path / "bindings"
    result = bind_repo(binding, worktree_root)
    # db lands beside the worktree, named after the repo — the sole derivation.
    assert Path(result.code_intel_db).name == "srcrepo.code_intel.db"
    assert Path(result.code_intel_db).parent == Path(result.worktree).parent


def test_bind_repo_rejects_traversal_in_repo_even_with_worktree_set(tmp_path: Path):
    """Gate-2 LOW (run_f8ef133b): binding.repo is used to build db_path
    (worktree.parent/<repo>.code_intel.db), so a '..' in repo must be rejected
    UNCONDITIONALLY — not only when worktree is unset. Before the fix, an explicit
    worktree bypassed the bare-name check and db_path could escape the bindings root."""
    victim_parent = tmp_path / "outside"
    victim_parent.mkdir()
    binding = Binding(
        repo="../outside/pwn",          # '..' — must be rejected before any db write
        kind="external", clone="https://example.com/x.git",
        worktree="legit-worktree",      # worktree SET — the branch that used to skip the check
        delivery_contract=DeliveryContract(
            remote_kind="github-pr", branch="main",
            review_path="s_internal-crux-review", auto_send="on-clean-review",
        ),
    )
    with pytest.raises(ValueError) as ei:
        bind_repo(binding, tmp_path / "bindings")
    assert "bare name" in str(ei.value)
    # no escaped db file was created outside the root
    assert not (victim_parent / "pwn.code_intel.db").exists()


# ---------------------------------------------------------------------------
# classify_project — the single-source-of-truth for DDD class (derive-on-read)
# ---------------------------------------------------------------------------

def _write_bindings(project_dir: Path, doc: dict) -> None:
    import yaml
    (project_dir / "bindings.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_classify_no_repo_when_no_bindings(tmp_path: Path):
    """No bindings.yaml → 'none' (pure-DDD; docs ARE the deliverable)."""
    assert classify_project(tmp_path) == "none"


def test_classify_external_when_all_external(tmp_path: Path):
    _write_bindings(tmp_path, {"bindings": [{
        "repo": "gh", "kind": "external",
        "clone": "https://github.com/o/gh.git", "worktree": None,
        "delivery_contract": {"remote_kind": "github-pr", "branch": "main",
                              "review_path": "s_code-review", "auto_send": "on-clean-review"},
    }]})
    assert classify_project(tmp_path) == "external"


def test_classify_internal_wins_on_mixed(tmp_path: Path):
    """ANY internal binding → 'internal' (needs s_internal-* + no_git_push gate)."""
    _write_bindings(tmp_path, VALID_DOC)  # mixed: external adlc + internal GCRAIDLCPreset
    assert classify_project(tmp_path) == "internal"


def test_classify_malformed_bindings_is_none_failsafe(tmp_path: Path):
    """A syntactically broken bindings.yaml → 'none', never a crash, never 'internal'."""
    (tmp_path / "bindings.yaml").write_text("this: is: not: valid: yaml", encoding="utf-8")
    assert classify_project(tmp_path) == "none"


def test_classify_empty_bindings_list_is_none(tmp_path: Path):
    _write_bindings(tmp_path, {"bindings": []})
    assert classify_project(tmp_path) == "none"
