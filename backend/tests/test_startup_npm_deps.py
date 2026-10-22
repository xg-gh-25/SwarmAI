"""Startup must not wait on npm, and a failed npm check is not "missing".

Incident 2026-10-22 (build verification, fresh HOME, host load ~30 on 12
cores): `npm list -g` timed out (15 s), the failure was read as "every
dependency missing", and `npm install -g` ran synchronously for 120 s -- once
for docx, once for pptx -- inside the startup projection. Startup took 4.5 min,
past the verifier's 180 s health wait.

Properties: an unknown check installs nothing and is retried later; the global
package set is read once per process; projection hands provisioning to the
background worker and returns without running npm.
"""

import subprocess
from unittest.mock import patch

import pytest

from core import manifest_loader as ml
from core.manifest_loader import ManifestLoader, SkillManifest


@pytest.fixture(autouse=True)
def _clean():
    ml._npm_global_cache.clear()
    for k in [k for k in ml._manifest_cache if k.startswith("_deps_checked_")]:
        ml._manifest_cache.pop(k, None)
    yield
    ml._npm_global_cache.clear()


def _manifest(name="skillx", deps=("docx",)) -> SkillManifest:
    return SkillManifest(name=name, dependencies={"npm": list(deps)})


def test_failed_check_installs_nothing_and_is_retried():
    calls = []

    def _run(cmd, **kw):
        calls.append(cmd[1])
        raise subprocess.TimeoutExpired(cmd, 15)

    with patch("core.manifest_loader.shutil.which", return_value="/usr/bin/npm"), \
         patch("core.manifest_loader.subprocess.run", _run):
        assert ManifestLoader.ensure_dependencies(_manifest()) == []
        assert ManifestLoader.ensure_dependencies(_manifest()) == []
    assert calls == ["list", "list"]  # retried, never "install"


def test_global_set_is_read_once_for_all_skills():
    calls = []

    def _run(cmd, **kw):
        calls.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"dependencies": {"docx": {}}}')

    with patch("core.manifest_loader.shutil.which", return_value="/usr/bin/npm"), \
         patch("core.manifest_loader.subprocess.run", _run):
        ManifestLoader.ensure_dependencies(_manifest("a"))
        ManifestLoader.ensure_dependencies(_manifest("b"))
    assert calls == ["list"]


def test_projection_does_not_run_npm_inline():
    """ensure_dependencies is called only by the background worker."""
    import inspect
    from core import projection_layer as pl
    src = inspect.getsource(pl)
    assert src.count("ensure_dependencies(") == 1
    assert "ensure_dependencies(" in inspect.getsource(pl._dep_worker_loop)


def test_worker_runs_scheduled_jobs():
    from core import projection_layer as pl
    done = []
    with patch.object(ManifestLoader, "ensure_dependencies",
                      side_effect=lambda m: done.append(m.name) or []):
        pl._schedule_dep_provision(_manifest("w1"), "w1")
        pl._schedule_dep_provision(_manifest("w2"), "w2")
        pl._dep_jobs.join()
    assert done == ["w1", "w2"]
