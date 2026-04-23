"""
Tests for Multi-Channel Notification module (s_notify).

Tests:
- AC4: s_notify sends to all 9 channels given valid webhook config
- AC5: Config is file-driven (notify-channels.yaml)
- AC7: s_notify is importable/callable by other skills and jobs
- Edge cases: missing config, disabled channels, HTTP errors
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────

SAMPLE_CONFIG = {
    "channels": {
        "feishu": {
            "enabled": True,
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test123",
        },
        "dingtalk": {
            "enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test456",
        },
        "wework": {
            "enabled": False,
            "webhook_url": "",
        },
        "telegram": {
            "enabled": True,
            "bot_token": "123456:ABC",
            "chat_id": "999",
        },
        "email": {
            "enabled": False,
            "from": "",
            "password": "",
            "to": "",
        },
        "ntfy": {
            "enabled": True,
            "server_url": "https://ntfy.sh",
            "topic": "swarm-test",
        },
        "bark": {
            "enabled": True,
            "url": "https://api.day.app/test-device-key",
        },
        "slack": {
            "enabled": True,
            "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
        },
        "webhook": {
            "enabled": True,
            "url": "https://example.com/webhook",
            "payload_template": '{"text": "{content}"}',
        },
    },
}


# ── AC7: Importable ───────────────────────────────────────────────────

class TestNotifyImportable:
    """AC7: s_notify is importable by other skills and jobs."""

    def test_send_notification_importable(self):
        """send_notification function is importable from notify module."""
        from skills.s_notify.notify import send_notification
        assert callable(send_notification)

    def test_load_config_importable(self):
        """load_notify_config function is importable."""
        from skills.s_notify.notify import load_notify_config
        assert callable(load_notify_config)


# ── AC5: Config file driven ──────────────────────────────────────────

class TestNotifyConfig:
    """AC5: Notification config is file-driven (notify-channels.yaml)."""

    def test_load_config_from_yaml(self, tmp_path):
        """Config loads from YAML file."""
        from skills.s_notify.notify import load_notify_config

        config_file = tmp_path / "notify-channels.yaml"
        import yaml
        config_file.write_text(yaml.dump(SAMPLE_CONFIG))

        config = load_notify_config(str(config_file))
        assert "channels" in config
        assert "feishu" in config["channels"]
        assert config["channels"]["feishu"]["enabled"] is True

    def test_missing_config_returns_empty(self):
        """Missing config file → empty config, no crash."""
        from skills.s_notify.notify import load_notify_config

        config = load_notify_config("/nonexistent/path/notify-channels.yaml")
        assert config == {} or config.get("channels", {}) == {}

    def test_disabled_channels_skipped(self, tmp_path):
        """Disabled channels are not sent to."""
        from skills.s_notify.notify import send_notification, load_notify_config

        config_file = tmp_path / "notify-channels.yaml"
        import yaml
        config_file.write_text(yaml.dump(SAMPLE_CONFIG))

        with patch("skills.s_notify.notify._send_feishu") as mock_feishu, \
             patch("skills.s_notify.notify._send_dingtalk") as mock_dingtalk, \
             patch("skills.s_notify.notify._send_wework") as mock_wework:
            mock_feishu.return_value = {"success": True}
            mock_dingtalk.return_value = {"success": True}

            result = send_notification(
                message="test",
                title="Test",
                channels=["feishu", "dingtalk", "wework"],
                config_path=str(config_file),
            )

        # feishu and dingtalk enabled, wework disabled
        mock_feishu.assert_called_once()
        mock_dingtalk.assert_called_once()
        mock_wework.assert_not_called()


# ── AC4: Sends to all 9 channels ─────────────────────────────────────

class TestNotifySending:
    """AC4: s_notify sends to all 9 channels given valid webhook config."""

    def test_feishu_sends_interactive_card(self, tmp_path):
        """Feishu sends an interactive card with markdown content."""
        from skills.s_notify.notify import _send_feishu

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_feishu(
                webhook_url="https://open.feishu.cn/test",
                title="Test",
                message="**Bold** text",
            )

        assert result["success"] is True
        payload = mock_post.call_args[1].get("json") or mock_post.call_args[0][1]
        assert "msg_type" in json.dumps(payload)

    def test_dingtalk_sends_markdown(self, tmp_path):
        """DingTalk sends markdown message type."""
        from skills.s_notify.notify import _send_dingtalk

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_dingtalk(
                webhook_url="https://oapi.dingtalk.com/test",
                title="Test",
                message="**Bold** text",
            )

        assert result["success"] is True

    def test_telegram_sends_message(self):
        """Telegram sends via sendMessage API."""
        from skills.s_notify.notify import _send_telegram

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_telegram(
                bot_token="123:ABC",
                chat_id="999",
                title="Test",
                message="Hello",
            )

        assert result["success"] is True
        # Should call Telegram API URL
        call_url = mock_post.call_args[0][0]
        assert "api.telegram.org" in call_url
        assert "sendMessage" in call_url

    def test_ntfy_sends_to_topic(self):
        """ntfy sends POST to server/topic."""
        from skills.s_notify.notify import _send_ntfy

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_ntfy(
                server_url="https://ntfy.sh",
                topic="swarm-test",
                title="Test",
                message="Hello",
            )

        assert result["success"] is True
        call_url = mock_post.call_args[0][0]
        assert "ntfy.sh" in call_url

    def test_bark_sends_to_device(self):
        """Bark sends to device URL."""
        from skills.s_notify.notify import _send_bark

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_bark(
                url="https://api.day.app/device-key",
                title="Test",
                message="Hello",
            )

        assert result["success"] is True

    def test_slack_sends_webhook(self):
        """Slack sends via incoming webhook."""
        from skills.s_notify.notify import _send_slack

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_slack(
                webhook_url="https://hooks.slack.com/services/T00/B00/xxx",
                title="Test",
                message="**Bold** text",
            )

        assert result["success"] is True

    def test_generic_webhook_sends(self):
        """Generic webhook sends with template substitution."""
        from skills.s_notify.notify import _send_webhook

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = _send_webhook(
                url="https://example.com/hook",
                title="Test",
                message="Hello",
                payload_template='{"text": "{content}"}',
            )

        assert result["success"] is True

    def test_send_notification_returns_per_channel_results(self, tmp_path):
        """send_notification returns dict with per-channel success/error."""
        from skills.s_notify.notify import send_notification

        config_file = tmp_path / "notify-channels.yaml"
        import yaml
        config_file.write_text(yaml.dump({
            "channels": {
                "feishu": {"enabled": True, "webhook_url": "https://test.feishu.cn/hook"},
                "slack": {"enabled": True, "webhook_url": "https://hooks.slack.com/xxx"},
            }
        }))

        with patch("skills.s_notify.notify._send_feishu") as mock_f, \
             patch("skills.s_notify.notify._send_slack") as mock_s:
            mock_f.return_value = {"success": True, "error": None}
            mock_s.return_value = {"success": False, "error": "timeout"}

            result = send_notification(
                message="test",
                config_path=str(config_file),
            )

        assert "feishu" in result
        assert result["feishu"]["success"] is True
        assert "slack" in result
        assert result["slack"]["success"] is False


# ── Edge cases ────────────────────────────────────────────────────────

class TestNotifyEdgeCases:
    """Edge cases: HTTP errors, empty URLs, partial failures."""

    def test_http_error_returns_failure(self):
        """Webhook returning non-2xx → channel reports failure, no crash."""
        from skills.s_notify.notify import _send_feishu

        with patch("skills.s_notify.notify.safe_post") as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            result = _send_feishu(
                webhook_url="https://open.feishu.cn/test",
                title="Test",
                message="Hello",
            )

        assert result["success"] is False
        assert result["error"] is not None

    def test_empty_webhook_url_skipped(self):
        """Channel with empty webhook URL → skip, report as skipped."""
        from skills.s_notify.notify import _send_feishu

        result = _send_feishu(
            webhook_url="",
            title="Test",
            message="Hello",
        )

        assert result["success"] is False

    def test_channel_filter_respects_list(self, tmp_path):
        """When channels list is specified, only those channels are sent to."""
        from skills.s_notify.notify import send_notification

        config_file = tmp_path / "notify-channels.yaml"
        import yaml
        config_file.write_text(yaml.dump({
            "channels": {
                "feishu": {"enabled": True, "webhook_url": "https://test.feishu.cn/hook"},
                "slack": {"enabled": True, "webhook_url": "https://hooks.slack.com/xxx"},
                "dingtalk": {"enabled": True, "webhook_url": "https://oapi.dingtalk.com/xxx"},
            }
        }))

        with patch("skills.s_notify.notify._send_feishu") as mock_f, \
             patch("skills.s_notify.notify._send_slack") as mock_s, \
             patch("skills.s_notify.notify._send_dingtalk") as mock_d:
            mock_f.return_value = {"success": True, "error": None}

            result = send_notification(
                message="test",
                channels=["feishu"],  # only feishu
                config_path=str(config_file),
            )

        mock_f.assert_called_once()
        mock_s.assert_not_called()
        mock_d.assert_not_called()
