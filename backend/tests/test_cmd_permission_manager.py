"""Unit tests for CmdPermissionManager.

Tests the filesystem-based command permission manager with in-memory cache.
Covers the full lifecycle: load, is_dangerous, is_approved, approve, reload,
plus edge cases like missing files, invalid JSON, overly-broad pattern
rejection, and persistence across instances.

Key invariants verified:

- ``load()`` creates default files when directory is missing
- ``is_dangerous()`` matches commands via glob patterns
- ``is_approved()`` matches commands via glob patterns
- ``approve()`` persists to disk and updates in-memory cache
- ``approve()`` rejects overly broad patterns (bare ``*``)
- ``reload()`` re-reads from disk and refreshes the cache
- New instances see previously approved patterns (persistence)
- Invalid JSON falls back gracefully
"""

import json
import pytest
from pathlib import Path

from core.cmd_permission_manager import (
    CmdPermissionManager,
    DEFAULT_DANGEROUS_PATTERNS,
    _OVERLY_BROAD_PATTERNS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_perms(tmp_path: Path) -> Path:
    """Return a path to a temporary cmd_permissions dir (does not exist)."""
    return tmp_path / "cmd_permissions"


@pytest.fixture
def mgr(tmp_perms: Path) -> CmdPermissionManager:
    """Return a CmdPermissionManager pointing at the temp directory."""
    m = CmdPermissionManager(base_dir=tmp_perms)
    m.load()
    return m


# ---------------------------------------------------------------------------
# load() tests
# ---------------------------------------------------------------------------


class TestLoad:
    """Tests for CmdPermissionManager.load()."""

    def test_creates_directory_and_default_files(self, tmp_perms: Path):
        """First launch: directory and both JSON files are created."""
        assert not tmp_perms.exists()
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        mgr.load()
        assert tmp_perms.is_dir()
        assert (tmp_perms / "dangerous_patterns.json").exists()
        assert (tmp_perms / "approved_commands.json").exists()

    def test_seeds_default_dangerous_patterns(self, mgr: CmdPermissionManager, tmp_perms: Path):
        """Default dangerous patterns are written when file is missing."""
        data = json.loads(
            (tmp_perms / "dangerous_patterns.json").read_text()
        )
        assert data["patterns"] == DEFAULT_DANGEROUS_PATTERNS

    def test_seeds_empty_approved_commands(self, mgr: CmdPermissionManager, tmp_perms: Path):
        """Approved commands file starts empty."""
        data = json.loads(
            (tmp_perms / "approved_commands.json").read_text()
        )
        assert data["commands"] == []

    def test_loads_existing_dangerous_patterns(self, tmp_perms: Path):
        """Loads custom dangerous patterns from existing file."""
        tmp_perms.mkdir(parents=True)
        custom = {"patterns": ["custom_danger *"]}
        (tmp_perms / "dangerous_patterns.json").write_text(
            json.dumps(custom)
        )
        (tmp_perms / "approved_commands.json").write_text(
            json.dumps({"commands": []})
        )
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        mgr.load()
        assert mgr.is_dangerous("custom_danger foo")
        assert not mgr.is_dangerous("rm -rf /tmp")

    def test_invalid_dangerous_json_falls_back(self, tmp_perms: Path):
        """Invalid JSON in dangerous_patterns.json falls back to defaults."""
        tmp_perms.mkdir(parents=True)
        (tmp_perms / "dangerous_patterns.json").write_text("NOT JSON")
        (tmp_perms / "approved_commands.json").write_text(
            json.dumps({"commands": []})
        )
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        mgr.load()
        # Should have fallen back to defaults
        assert mgr.is_dangerous("sudo reboot")

    def test_invalid_approved_json_falls_back(self, tmp_perms: Path):
        """Invalid JSON in approved_commands.json starts empty."""
        tmp_perms.mkdir(parents=True)
        (tmp_perms / "dangerous_patterns.json").write_text(
            json.dumps({"patterns": ["sudo *"]})
        )
        (tmp_perms / "approved_commands.json").write_text("{bad")
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        mgr.load()
        assert not mgr.is_approved("sudo reboot")

    def test_empty_files_fall_back(self, tmp_perms: Path):
        """Empty files fall back to defaults / empty."""
        tmp_perms.mkdir(parents=True)
        (tmp_perms / "dangerous_patterns.json").write_text("")
        (tmp_perms / "approved_commands.json").write_text("")
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        mgr.load()
        assert mgr.is_dangerous("sudo reboot")
        assert not mgr.is_approved("sudo reboot")


# ---------------------------------------------------------------------------
# is_dangerous() tests
# ---------------------------------------------------------------------------


class TestIsDangerous:
    """Tests for CmdPermissionManager.is_dangerous()."""

    def test_matches_sudo(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("sudo reboot")

    def test_matches_rm_rf(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("rm -rf /tmp/old")

    def test_matches_kill_9(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("kill -9 1234")

    def test_matches_chmod_777(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("chmod 777 /var/www")

    def test_matches_dd(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("dd if=/dev/zero of=/dev/sda")

    def test_matches_mkfs(self, mgr: CmdPermissionManager):
        assert mgr.is_dangerous("mkfs.ext4 /dev/sda1")

    def test_safe_command_not_dangerous(self, mgr: CmdPermissionManager):
        assert not mgr.is_dangerous("ls -la")

    def test_safe_echo_not_dangerous(self, mgr: CmdPermissionManager):
        assert not mgr.is_dangerous("echo hello")

    def test_safe_git_not_dangerous(self, mgr: CmdPermissionManager):
        assert not mgr.is_dangerous("git status")


# ---------------------------------------------------------------------------
# is_approved() tests
# ---------------------------------------------------------------------------


class TestIsApproved:
    """Tests for CmdPermissionManager.is_approved()."""

    def test_nothing_approved_initially(self, mgr: CmdPermissionManager):
        assert not mgr.is_approved("sudo reboot")

    def test_approved_exact_match(self, mgr: CmdPermissionManager):
        mgr.approve("npm install")
        assert mgr.is_approved("npm install")

    def test_approved_glob_match(self, mgr: CmdPermissionManager):
        mgr.approve("docker build *")
        assert mgr.is_approved("docker build -t myapp .")

    def test_non_matching_not_approved(self, mgr: CmdPermissionManager):
        mgr.approve("docker build *")
        assert not mgr.is_approved("docker run myapp")


# ---------------------------------------------------------------------------
# approve() tests
# ---------------------------------------------------------------------------


class TestApprove:
    """Tests for CmdPermissionManager.approve()."""

    def test_approve_persists_to_file(
        self, mgr: CmdPermissionManager, tmp_perms: Path
    ):
        mgr.approve("npm install")
        data = json.loads(
            (tmp_perms / "approved_commands.json").read_text()
        )
        patterns = [c["pattern"] for c in data["commands"]]
        assert "npm install" in patterns

    def test_approve_entry_has_metadata(
        self, mgr: CmdPermissionManager, tmp_perms: Path
    ):
        mgr.approve("npm install")
        data = json.loads(
            (tmp_perms / "approved_commands.json").read_text()
        )
        entry = data["commands"][0]
        assert "approved_at" in entry
        assert entry["approved_by"] == "user"

    def test_reject_bare_star(self, mgr: CmdPermissionManager):
        with pytest.raises(ValueError, match="too broad"):
            mgr.approve("*")

    def test_reject_double_star(self, mgr: CmdPermissionManager):
        with pytest.raises(ValueError, match="too broad"):
            mgr.approve("**")

    def test_reject_star_space_star(self, mgr: CmdPermissionManager):
        with pytest.raises(ValueError, match="too broad"):
            mgr.approve("* *")

    def test_approve_strips_whitespace(self, mgr: CmdPermissionManager):
        mgr.approve("  npm install  ")
        assert mgr.is_approved("npm install")


# ---------------------------------------------------------------------------
# reload() tests
# ---------------------------------------------------------------------------


class TestReload:
    """Tests for CmdPermissionManager.reload()."""

    def test_reload_picks_up_manual_edits(
        self, mgr: CmdPermissionManager, tmp_perms: Path
    ):
        """Manually editing the file and calling reload() refreshes cache."""
        # Manually add a pattern to the file
        data = json.loads(
            (tmp_perms / "approved_commands.json").read_text()
        )
        data["commands"].append({
            "pattern": "pip install *",
            "approved_at": "2026-01-01T00:00:00Z",
            "approved_by": "manual",
        })
        (tmp_perms / "approved_commands.json").write_text(
            json.dumps(data)
        )
        # Before reload, cache doesn't know about it
        assert not mgr.is_approved("pip install requests")
        # After reload, it does
        mgr.reload()
        assert mgr.is_approved("pip install requests")

    def test_reload_picks_up_new_dangerous_patterns(
        self, mgr: CmdPermissionManager, tmp_perms: Path
    ):
        """Manually adding a dangerous pattern and reloading works."""
        data = json.loads(
            (tmp_perms / "dangerous_patterns.json").read_text()
        )
        data["patterns"].append("custom_bad *")
        (tmp_perms / "dangerous_patterns.json").write_text(
            json.dumps(data)
        )
        assert not mgr.is_dangerous("custom_bad foo")
        mgr.reload()
        assert mgr.is_dangerous("custom_bad foo")


# ---------------------------------------------------------------------------
# Persistence across instances
# ---------------------------------------------------------------------------


class TestPersistence:
    """Tests for cross-instance persistence (simulating restart)."""

    def test_approved_pattern_survives_new_instance(self, tmp_perms: Path):
        """Approve a pattern, create a new instance, verify it's approved."""
        mgr1 = CmdPermissionManager(base_dir=tmp_perms)
        mgr1.load()
        mgr1.approve("docker build *")

        mgr2 = CmdPermissionManager(base_dir=tmp_perms)
        mgr2.load()
        assert mgr2.is_approved("docker build -t myapp .")

    def test_auto_load_on_first_check(self, tmp_perms: Path):
        """Calling is_dangerous without load() auto-loads."""
        mgr = CmdPermissionManager(base_dir=tmp_perms)
        # No explicit load() call
        assert mgr.is_dangerous("sudo reboot")
