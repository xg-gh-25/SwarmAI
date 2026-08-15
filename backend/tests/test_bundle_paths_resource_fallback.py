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
    from config import get_app_data_dir

    exe_dir = Path("/some/build-output/binaries/python-backend-x/")
    candidates = bundle_paths._get_tauri_bundle_resource_candidates(exe_dir)
    # Derive the expectation from the SAME authority the code uses. Re-deriving
    # `Path.home() / ".swarm-ai"` here is exactly what _get_deployed_daemon_resources'
    # docstring says NOT to do — and it made this test fail the moment the app-data
    # root became overridable (SWARM_DATA_DIR), which is also how it would fail on any
    # box whose HOME differs from the run's app-data root.
    deployed = get_app_data_dir() / "daemon" / "resources"
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
    # Redirect the app-data root the SUPPORTED way. Patching Path.home alone stopped
    # reaching _get_deployed_daemon_resources once get_app_data_dir() gained the
    # SWARM_DATA_DIR override (env wins over home). The Path.home patch is kept so the
    # config-unavailable except-branch is still covered.
    monkeypatch.setenv("SWARM_DATA_DIR", str(fake_home / ".swarm-ai"))
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
    # Redirect the app-data root the SUPPORTED way. Patching Path.home alone stopped
    # reaching _get_deployed_daemon_resources once get_app_data_dir() gained the
    # SWARM_DATA_DIR override (env wins over home). The Path.home patch is kept so the
    # config-unavailable except-branch is still covered.
    monkeypatch.setenv("SWARM_DATA_DIR", str(fake_home / ".swarm-ai"))
    monkeypatch.setattr(bundle_paths.Path, "home", classmethod(lambda cls: fake_home))

    found = bundle_paths.get_resource_file("default-agent.json", stale_dev)
    assert found is None


def test_ac4_execution_get_resources_dir_uses_deployed_fallback(tmp_path, monkeypatch):
    """AC4: the PRODUCTION path — agent_defaults._get_resources_dir() calls
    get_resources_dir() (the *dir* function, not get_resource_file). Execution-
    test THAT function directly: a frozen build-output binary with no resources/
    sibling and a non-existent dev_path must resolve to the deployed daemon dir.

    (AC2 covered get_resource_file; this covers the actual entry point that
    produced the 32 default-agent.json errors — STEERING: test the path that runs.)
    """
    fake_home = tmp_path / "home"
    deployed_res = fake_home / ".swarm-ai" / "daemon" / "resources"
    deployed_res.mkdir(parents=True)
    (deployed_res / "default-agent.json").write_text("{}")

    build_out = tmp_path / "binaries" / "python-backend-x"
    build_out.mkdir(parents=True)
    fake_exe = build_out / "python-backend"
    fake_exe.write_text("bin")
    stale_dev = tmp_path / "src" / "desktop" / "resources"  # must NOT exist

    monkeypatch.setattr(bundle_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundle_paths.sys, "executable", str(fake_exe), raising=False)
    # Redirect the app-data root the SUPPORTED way. Patching Path.home alone stopped
    # reaching _get_deployed_daemon_resources once get_app_data_dir() gained the
    # SWARM_DATA_DIR override (env wins over home). The Path.home patch is kept so the
    # config-unavailable except-branch is still covered.
    monkeypatch.setenv("SWARM_DATA_DIR", str(fake_home / ".swarm-ai"))
    monkeypatch.setattr(bundle_paths.Path, "home", classmethod(lambda cls: fake_home))

    resolved = bundle_paths.get_resources_dir(stale_dev)
    assert resolved == deployed_res, (
        f"get_resources_dir must fall back to the deployed daemon dir "
        f"{deployed_res}, got {resolved}"
    )


def test_ac5_get_resources_dir_prefers_real_dev_path(tmp_path, monkeypatch):
    """AC5: the fallback is LAST-resort — a real dev_path still wins, so the
    fallback can't hijack a correctly-configured environment (guards C5: only
    fires when nothing else resolves)."""
    real_dev = tmp_path / "desktop" / "resources"
    real_dev.mkdir(parents=True)
    fake_home = tmp_path / "home"
    (fake_home / ".swarm-ai" / "daemon" / "resources").mkdir(parents=True)

    # Redirect the app-data root the SUPPORTED way. Patching Path.home alone stopped
    # reaching _get_deployed_daemon_resources once get_app_data_dir() gained the
    # SWARM_DATA_DIR override (env wins over home). The Path.home patch is kept so the
    # config-unavailable except-branch is still covered.
    monkeypatch.setenv("SWARM_DATA_DIR", str(fake_home / ".swarm-ai"))
    monkeypatch.setattr(bundle_paths.Path, "home", classmethod(lambda cls: fake_home))
    resolved = bundle_paths.get_resources_dir(real_dev)
    assert resolved == real_dev, "a real dev_path must take priority over the fallback"
