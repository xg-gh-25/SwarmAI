"""Tests for plugin_manager git subprocess hang-guards (run_e76b3ea5).

An unbounded `git clone`/`fetch` against a network black-hole can NEVER finish and
would pin a subprocess-pool worker forever (a latent hang, independent of pooling).
The 3 git subprocess.run calls in sync_git_marketplace MUST carry a timeout= — a
genuine hang-guard (STEERING #2: bounds something that will never finish), NOT a
truncation of real work. On timeout, subprocess.run raises TimeoutExpired which
propagates out of sync_git_marketplace to the caller (a marketplace-sync failure) —
the worker is freed, real work is not silently discarded.

Methodology: patch subprocess.run to capture the timeout kwarg on each git call and
drive the real sync_git_marketplace clone path against a tmp cache dir. Mutation:
remove the timeout= kwarg → the assertion goes RED.
"""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.plugin_manager import PluginManager


@pytest.fixture
def pm(tmp_path):
    return PluginManager(base_dir=tmp_path)


async def test_git_clone_has_timeout_hang_guard(pm):
    """The fresh-clone path (no existing .git) must call git clone with a timeout=."""
    captured = {}

    def _fake_run(cmd, *a, **kw):
        # Record the timeout for the git-clone invocation.
        if isinstance(cmd, list) and "clone" in cmd:
            captured["clone_timeout"] = kw.get("timeout")
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    with patch("core.plugin_manager.subprocess.run", side_effect=_fake_run):
        # No .git in the tmp cache dir → takes the clone branch. Downstream
        # marketplace.json scan finds nothing → returns a SyncResult gracefully.
        await pm.sync_git_marketplace("testmp", "https://example.invalid/repo.git")

    assert captured.get("clone_timeout") is not None, (
        "git clone must carry a timeout= hang-guard — an unbounded clone pins a "
        "subprocess-pool worker forever on a network black-hole"
    )
    assert captured["clone_timeout"] >= 30, "clone timeout should be a real bound (>=30s)"


async def test_git_fetch_and_reset_have_timeout(pm, tmp_path):
    """The pull path (existing .git) must call git fetch + reset with a timeout=."""
    # Create a fake .git so sync takes the fetch/reset branch.
    cache = pm.get_marketplace_cache_dir("testmp")
    (cache / ".git").mkdir(parents=True, exist_ok=True)

    timeouts = []

    def _fake_run(cmd, *a, **kw):
        if isinstance(cmd, list) and ("fetch" in cmd or "reset" in cmd):
            timeouts.append(kw.get("timeout"))
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    with patch("core.plugin_manager.subprocess.run", side_effect=_fake_run):
        await pm.sync_git_marketplace("testmp", "https://example.invalid/repo.git")

    assert timeouts, "fetch/reset git calls were never made on the pull path"
    assert all(t is not None and t >= 30 for t in timeouts), (
        f"every git fetch/reset must carry a timeout= hang-guard, got {timeouts}"
    )
