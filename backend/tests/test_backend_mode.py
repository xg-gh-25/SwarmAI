"""Tests for Backend-as-Daemon mode detection and lifecycle.

Acceptance criteria tested:
  AC2: Tauri discovers existing backend via backend.json + /health probe
  AC3: GET /api/system/mode returns mode, pid, port
  AC5: Gateway sets Slack presence on start/stop
  AC6: backend.json written on startup, deleted on shutdown, stale PID detection

Methodology: TDD RED — all tests written before implementation.
"""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AC6: backend.json lifecycle
# ---------------------------------------------------------------------------

BACKEND_JSON_FIELDS = {"pid", "port", "mode", "started_at"}


class TestBackendJsonLifecycle:
    """backend.json is written on startup and deleted on clean shutdown."""

    def test_write_backend_json_creates_file(self, tmp_path):
        """write_backend_json creates the file with required fields."""
        from main import write_backend_json

        json_path = tmp_path / "backend.json"
        write_backend_json(port=18321, mode="daemon", path=str(json_path))

        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert BACKEND_JSON_FIELDS <= set(data.keys())
        assert data["port"] == 18321
        assert data["mode"] == "daemon"
        assert data["pid"] == os.getpid()

    def test_write_backend_json_sidecar_mode(self, tmp_path):
        """write_backend_json with mode='sidecar' records sidecar."""
        from main import write_backend_json

        json_path = tmp_path / "backend.json"
        write_backend_json(port=54321, mode="sidecar", path=str(json_path))

        data = json.loads(json_path.read_text())
        assert data["mode"] == "sidecar"
        assert data["port"] == 54321

    def test_remove_backend_json_deletes_file(self, tmp_path):
        """remove_backend_json deletes the file if it exists."""
        from main import remove_backend_json

        json_path = tmp_path / "backend.json"
        json_path.write_text('{"pid": 1}')
        remove_backend_json(path=str(json_path))

        assert not json_path.exists()

    def test_remove_backend_json_noop_if_missing(self, tmp_path):
        """remove_backend_json is a no-op if file doesn't exist."""
        from main import remove_backend_json

        json_path = tmp_path / "backend.json"
        # Should not raise
        remove_backend_json(path=str(json_path))

    def test_read_backend_json_valid(self, tmp_path):
        """read_backend_json returns parsed data for a valid file."""
        from main import read_backend_json

        json_path = tmp_path / "backend.json"
        json_path.write_text(json.dumps({
            "pid": os.getpid(),  # current process — alive
            "port": 18321,
            "mode": "daemon",
            "started_at": "2026-03-27T14:00:00+08:00",
        }))

        data = read_backend_json(path=str(json_path))
        assert data is not None
        assert data["port"] == 18321

    def test_read_backend_json_stale_pid(self, tmp_path):
        """read_backend_json returns None for a stale (dead) PID."""
        from main import read_backend_json

        json_path = tmp_path / "backend.json"
        # PID 999999999 is almost certainly not running
        json_path.write_text(json.dumps({
            "pid": 999999999,
            "port": 18321,
            "mode": "daemon",
            "started_at": "2026-03-27T14:00:00+08:00",
        }))

        data = read_backend_json(path=str(json_path))
        assert data is None, "Should return None for dead PID"

    def test_read_backend_json_missing_file(self, tmp_path):
        """read_backend_json returns None if file doesn't exist."""
        from main import read_backend_json

        json_path = tmp_path / "nonexistent.json"
        data = read_backend_json(path=str(json_path))
        assert data is None

    def test_read_backend_json_corrupt_file(self, tmp_path):
        """read_backend_json returns None for corrupt JSON."""
        from main import read_backend_json

        json_path = tmp_path / "backend.json"
        json_path.write_text("not valid json {{{")

        data = read_backend_json(path=str(json_path))
        assert data is None


# ---------------------------------------------------------------------------
# AC3: /api/system/mode endpoint
# ---------------------------------------------------------------------------


class TestSystemModeEndpoint:
    """GET /api/system/mode returns running mode info."""

    @pytest.mark.asyncio
    async def test_mode_endpoint_returns_mode(self):
        """The /api/system/mode endpoint returns mode, pid, port."""
        from main import get_system_mode

        result = await get_system_mode()
        assert "mode" in result
        assert result["mode"] in ("daemon", "sidecar")
        assert "pid" in result
        assert "port" in result
        assert isinstance(result["pid"], int)
        assert isinstance(result["port"], int)

    @pytest.mark.asyncio
    async def test_mode_endpoint_includes_uptime(self):
        """The mode endpoint includes uptime_seconds."""
        from main import get_system_mode

        result = await get_system_mode()
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# AC6: _detect_run_mode
# ---------------------------------------------------------------------------


class TestDetectRunMode:
    """_detect_run_mode correctly distinguishes daemon from sidecar."""

    def test_detect_daemon_from_env(self):
        """SWARMAI_MODE=daemon → daemon mode."""
        from main import _detect_run_mode

        with patch.dict(os.environ, {"SWARMAI_MODE": "daemon"}):
            assert _detect_run_mode() == "daemon"

    def test_detect_sidecar_default(self):
        """No SWARMAI_MODE → sidecar (default)."""
        from main import _detect_run_mode

        with patch.dict(os.environ, {}, clear=True):
            # Remove SWARMAI_MODE if present
            env = os.environ.copy()
            env.pop("SWARMAI_MODE", None)
            with patch.dict(os.environ, env, clear=True):
                assert _detect_run_mode() == "sidecar"


# ---------------------------------------------------------------------------
# AC5: Slack presence on gateway start/stop
# ---------------------------------------------------------------------------


class TestSlackPresenceLifecycle:
    """Gateway sets Slack bot presence on startup and shutdown."""

    @pytest.mark.asyncio
    async def test_slack_adapter_set_presence_auto(self):
        """set_presence('auto') calls users_setPresence."""
        try:
            from channels.adapters.slack import SlackChannelAdapter, SLACK_BOLT_AVAILABLE
        except ImportError:
            pytest.skip("slack-bolt not installed")

        if not SLACK_BOLT_AVAILABLE:
            pytest.skip("slack-bolt not available")

        adapter = SlackChannelAdapter("ch1", {
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
        }, on_message=AsyncMock())

        mock_client = MagicMock()
        mock_client.users_setPresence.return_value = {"ok": True}
        adapter._slack_client = mock_client

        await adapter.set_presence("auto")
        mock_client.users_setPresence.assert_called_once_with(presence="auto")

    @pytest.mark.asyncio
    async def test_slack_adapter_set_presence_away(self):
        """set_presence('away') calls users_setPresence."""
        try:
            from channels.adapters.slack import SlackChannelAdapter, SLACK_BOLT_AVAILABLE
        except ImportError:
            pytest.skip("slack-bolt not installed")

        if not SLACK_BOLT_AVAILABLE:
            pytest.skip("slack-bolt not available")

        adapter = SlackChannelAdapter("ch1", {
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
        }, on_message=AsyncMock())

        mock_client = MagicMock()
        mock_client.users_setPresence.return_value = {"ok": True}
        adapter._slack_client = mock_client

        await adapter.set_presence("away")
        mock_client.users_setPresence.assert_called_once_with(presence="away")

    @pytest.mark.asyncio
    async def test_slack_adapter_set_presence_no_client(self):
        """set_presence is a no-op when client is not initialized."""
        try:
            from channels.adapters.slack import SlackChannelAdapter, SLACK_BOLT_AVAILABLE
        except ImportError:
            pytest.skip("slack-bolt not installed")

        if not SLACK_BOLT_AVAILABLE:
            pytest.skip("slack-bolt not available")

        adapter = SlackChannelAdapter("ch1", {
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
        }, on_message=AsyncMock())
        adapter._slack_client = None

        # Should not raise
        await adapter.set_presence("auto")
