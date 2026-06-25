"""Tests for the deployed-daemon resources fallback in utils.bundle_paths.

Bug (observed in backend-daemon.log, 32 occurrences Jun 22-25): a frozen binary
launched from the build-output dir during ``prod.sh build`` (smoke-test) has no
``resources/`` sibling, so ``get_resources_dir`` exhausted its Tauri-bundle
candidates and fell back to a stale ``__file__``-derived dev path under
``desktop/src-tauri/binaries/.../desktop/resources`` → ``default-agent.json``
not-found → silent DB fallback (PIT69). The rsync ``--delete --exclude=/resources``
fix in daemon-lib.sh addresses the deploy-window race but NOT this build-output
binary case (its exe_dir is the bundle dir, not the deployed daemon dir).

Fix: add the canonical deployed-daemon resources dir
(``~/.swarm-ai/daemon/resources``) as a last-resort candidate so any frozen
binary still finds resources regardless of where it was launched from.

Acceptance criteria:
  AC1: the candidate list always includes ~/.swarm-ai/daemon/resources
  AC2: execution — when exe_dir has no resources/ but the deployed daemon dir
       does, get_resource_file resolves the file via the fallback
  AC3: negative — without the fallback the same lookup would fail (proves the
       test exercises the new path, per STEERING #11)

Methodology: contract test (candidate list membership) + execution test
(monkeypatch sys.frozen + sys.executable + Path.home, real tmp filesystem).
"""
from __future__ import annotations

from pathlib import Path

from utils import bundle_paths


def test_ac1_candidates_include_deployed_daemon_resources():
    """AC1: the deployed-daemon resources dir is always a candidate."""
    exe_dir = Path("/some/build-output/binaries/python-backend-x/")
    candidates = bundle_paths._get_tauri_bundle_resource_candidates(exe_dir)
    deployed = Path.home() / ".swarm-ai" / "daemon" / "resources"
    assert deployed in candidates, (
        f"deployed-daemon resources dir {deployed} must be a fallback candidate; "
        f"got {candidates}"
    )


def test_ac2_execution_finds_file_via_deployed_fallback(tmp_path, monkeypatch):
    """AC2: a frozen binary in a build-output dir (no resources/ sibling) still
    finds default-agent.json via the deployed-daemon fallback.

    Reproduces the real scenario: exe_dir = build-output bundle (no resources),
    deployed daemon dir HAS resources/default-agent.json. The dev_path passed in
    does not exist either (stale __file__ path).
    """
    fake_home = tmp_path / "home"
    deployed_res = fake_home / ".swarm-ai" / "daemon" / "resources"
    deployed_res.mkdir(parents=True)
    (deployed_res / "default-agent.json").write_text('{"behavior":"x"}')

    # Build-output binary: exe_dir has NO resources/ sibling.
    build_out = tmp_path / "binaries" / "python-backend-x"
    build_out.mkdir(parents=True)
    fake_exe = build_out / "python-backend"
    fake_exe.write_text("bin")

    # Stale dev path (the __file__-derived fallback) — must NOT exist.
    stale_dev = tmp_path / "src" / "desktop" / "resources"

    monkeypatch.setattr(bundle_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundle_paths.sys, "executable", str(fake_exe), raising=False)
    monkeypatch.setattr(bundle_paths.Path, "home", classmethod(lambda cls: fake_home))

    found = bundle_paths.get_resource_file("default-agent.json", stale_dev)
    assert found is not None, "fallback failed: resource not found via deployed daemon dir"
    assert found.read_text() == '{"behavior":"x"}'
    assert deployed_res in found.parents


def test_ac3_negative_without_deployed_resources_returns_none(tmp_path, monkeypatch):
    """AC3: control — if the deployed daemon dir has no resources either, the
    lookup returns None (proves AC2 succeeds specifically because of the
    fallback, not some other path)."""
    fake_home = tmp_path / "home"
    (fake_home / ".swarm-ai" / "daemon").mkdir(parents=True)  # no resources/

    build_out = tmp_path / "binaries" / "python-backend-x"
    build_out.mkdir(parents=True)
    fake_exe = build_out / "python-backend"
    fake_exe.write_text("bin")
    stale_dev = tmp_path / "src" / "desktop" / "resources"

    monkeypatch.setattr(bundle_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundle_paths.sys, "executable", str(fake_exe), raising=False)
    monkeypatch.setattr(bundle_paths.Path, "home", classmethod(lambda cls: fake_home))

    found = bundle_paths.get_resource_file("default-agent.json", stale_dev)
    assert found is None
