"""
Multi-Channel Notification Module

Sends messages to 9 notification channels via HTTP webhooks.
Each channel is a simple HTTP POST with channel-specific payload format.

Channels: feishu, dingtalk, wework, telegram, email, ntfy, bark, slack, webhook

Config: ~/.swarm-ai/notify-channels.yaml (user-owned, contains secrets)

Usage:
    from skills.s_notify.notify import send_notification
    result = send_notification("Hello world", title="Alert", channels=["feishu", "slack"])
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

logger = logging.getLogger(__name__)

# Default config location
DEFAULT_CONFIG_PATH = str(Path.home() / ".swarm-ai" / "notify-channels.yaml")


# ── HTTP helper ───────────────────────────────────────────────────────

def safe_post(url: str, **kwargs) -> Any:
    """POST with proxy bypass and retry (reuses signal pipeline http client)."""
    try:
        from jobs.adapters.http_client import safe_client
        with safe_client(timeout=15) as client:
            return client.post(url, **kwargs)
    except ImportError:
        # Fallback for standalone use outside backend
        import httpx
        with httpx.Client(timeout=15, trust_env=False) as client:
            return client.post(url, **kwargs)


# ── Config ────────────────────────────────────────────────────────────

def load_notify_config(config_path: str | None = None) -> dict:
    """
    Load notification channel config from YAML file.

    Args:
        config_path: Path to notify-channels.yaml. None = default location.

    Returns:
        Dict with 'channels' key, or empty dict if file missing.
    """
    path = Path(config_path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        logger.warning(f"Notify config not found: {path}")
        return {}

    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        logger.error(f"Failed to load notify config: {e}")
        return {}


# ── Channel senders ───────────────────────────────────────────────────

def _send_feishu(webhook_url: str, title: str, message: str) -> dict:
    """Send to Feishu/Lark via interactive card."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL"}

    try:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "markdown", "content": message},
                ],
            },
        }
        resp = safe_post(webhook_url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_dingtalk(webhook_url: str, title: str, message: str) -> dict:
    """Send to DingTalk via markdown message."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL"}

    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"## {title}\n\n{message}"},
        }
        resp = safe_post(webhook_url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_wework(webhook_url: str, title: str, message: str) -> dict:
    """Send to WeCom/WeWork via markdown message."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL"}

    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": f"**{title}**\n\n{message}"},
        }
        resp = safe_post(webhook_url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_telegram(bot_token: str, chat_id: str, title: str, message: str) -> dict:
    """Send to Telegram via Bot API sendMessage."""
    if not bot_token or not chat_id:
        return {"success": False, "error": "Missing bot_token or chat_id"}

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        text = f"*{title}*\n\n{message}"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        resp = safe_post(url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_email(
    smtp_from: str, password: str, to: str, title: str, message: str,
    smtp_server: str = "", smtp_port: int = 0,
) -> dict:
    """Send via SMTP email."""
    if not smtp_from or not password or not to:
        return {"success": False, "error": "Missing email config"}

    try:
        # Auto-detect SMTP server from email domain
        if not smtp_server:
            domain = smtp_from.split("@")[-1]
            smtp_server = f"smtp.{domain}"
        if not smtp_port:
            smtp_port = 465

        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr(("Swarm", smtp_from))
        msg["To"] = to
        msg["Subject"] = title
        msg.attach(MIMEText(message, "plain", "utf-8"))
        msg.attach(MIMEText(f"<pre>{message}</pre>", "html", "utf-8"))

        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as server:
            server.login(smtp_from, password)
            server.sendmail(smtp_from, to.split(","), msg.as_string())

        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_ntfy(server_url: str, topic: str, title: str, message: str,
               token: str = "") -> dict:
    """Send to ntfy topic."""
    if not topic:
        return {"success": False, "error": "No topic configured"}

    try:
        url = f"{server_url.rstrip('/')}/{topic}"
        headers = {"Title": title, "Markdown": "yes"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = safe_post(url, content=message.encode("utf-8"), headers=headers)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_bark(url: str, title: str, message: str) -> dict:
    """Send to Bark (iOS push)."""
    if not url:
        return {"success": False, "error": "No Bark URL"}

    try:
        bark_url = f"{url.rstrip('/')}/{quote(title)}/{quote(message)}"
        resp = safe_post(bark_url)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_slack(webhook_url: str, title: str, message: str) -> dict:
    """Send to Slack via incoming webhook."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL"}

    try:
        # Convert markdown bold to Slack mrkdwn
        slack_text = message.replace("**", "*")
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": slack_text},
                },
            ],
        }
        resp = safe_post(webhook_url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_webhook(url: str, title: str, message: str,
                  payload_template: str = "") -> dict:
    """Send to generic webhook with template substitution."""
    if not url:
        return {"success": False, "error": "No webhook URL"}

    try:
        if payload_template:
            body = payload_template.replace("{title}", title).replace("{content}", message)
            payload = json.loads(body)
        else:
            payload = {"title": title, "content": message}

        resp = safe_post(url, json=payload)
        return {"success": resp.status_code == 200, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Channel dispatch ──────────────────────────────────────────────────

CHANNEL_SENDERS = {
    "feishu": lambda cfg, t, m: _send_feishu(cfg.get("webhook_url", ""), t, m),
    "dingtalk": lambda cfg, t, m: _send_dingtalk(cfg.get("webhook_url", ""), t, m),
    "wework": lambda cfg, t, m: _send_wework(cfg.get("webhook_url", ""), t, m),
    "telegram": lambda cfg, t, m: _send_telegram(
        cfg.get("bot_token", ""), cfg.get("chat_id", ""), t, m
    ),
    "email": lambda cfg, t, m: _send_email(
        cfg.get("from", ""), cfg.get("password", ""), cfg.get("to", ""),
        t, m, cfg.get("smtp_server", ""), cfg.get("smtp_port", 0),
    ),
    "ntfy": lambda cfg, t, m: _send_ntfy(
        cfg.get("server_url", "https://ntfy.sh"), cfg.get("topic", ""),
        t, m, cfg.get("token", ""),
    ),
    "bark": lambda cfg, t, m: _send_bark(cfg.get("url", ""), t, m),
    "slack": lambda cfg, t, m: _send_slack(cfg.get("webhook_url", ""), t, m),
    "webhook": lambda cfg, t, m: _send_webhook(
        cfg.get("url", ""), t, m, cfg.get("payload_template", ""),
    ),
}


# ── Public API ────────────────────────────────────────────────────────

def send_notification(
    message: str,
    title: str = "Swarm Notification",
    channels: list[str] | None = None,
    config_path: str | None = None,
) -> dict[str, dict]:
    """
    Send a notification to configured channels.

    Args:
        message: Message content (markdown supported by most channels)
        title: Message title/subject
        channels: Channel names to send to. None = all enabled channels.
        config_path: Path to notify-channels.yaml. None = default.

    Returns:
        Dict mapping channel name → {"success": bool, "error": str|None}
    """
    config = load_notify_config(config_path)
    channel_configs = config.get("channels", {})

    if not channel_configs:
        logger.warning("No notification channels configured")
        return {}

    # Filter to requested channels (if specified)
    target_channels = channels or list(channel_configs.keys())

    results: dict[str, dict] = {}
    for ch_name in target_channels:
        ch_cfg = channel_configs.get(ch_name, {})

        # Skip disabled channels
        if not ch_cfg.get("enabled", False):
            continue

        sender = CHANNEL_SENDERS.get(ch_name)
        if not sender:
            results[ch_name] = {"success": False, "error": f"Unknown channel: {ch_name}"}
            continue

        result = sender(ch_cfg, title, message)
        results[ch_name] = result
        logger.info(f"Notify '{ch_name}': {'ok' if result['success'] else result.get('error', 'failed')}")

    return results
