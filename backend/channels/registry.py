"""Channel adapter registry.

Maps channel type strings to adapter classes. Adapters register themselves
at import time. The registry only includes adapters whose dependencies
are available.
"""
from __future__ import annotations

import logging
from typing import Optional, Type

from channels.base import ChannelAdapter

logger = logging.getLogger(__name__)

_ADAPTER_REGISTRY: dict[str, Type[ChannelAdapter]] = {}


def register_adapter(channel_type: str, adapter_class: Type[ChannelAdapter]) -> None:
    """Register a channel adapter class for a given type."""
    _ADAPTER_REGISTRY[channel_type] = adapter_class
    logger.info(f"Registered channel adapter: {channel_type}")


def get_adapter_class(channel_type: str) -> Optional[Type[ChannelAdapter]]:
    """Get the adapter class for a channel type."""
    return _ADAPTER_REGISTRY.get(channel_type)


def list_supported_types() -> list[dict]:
    """List all supported channel types with metadata."""
    type_info = {
        "feishu": {
            "id": "feishu",
            "label": "Feishu (飞书)",
            "description": "Connect to Feishu/Lark via WebSocket long connection",
            "config_fields": [
                {"key": "app_id", "label": "App ID", "type": "text", "required": True},
                {"key": "app_secret", "label": "App Secret", "type": "password", "required": True},
            ],
        },
        "slack": {
            "id": "slack",
            "label": "Slack",
            "description": "Connect to Slack via Socket Mode",
            "config_fields": [
                {"key": "bot_token", "label": "Bot Token (xoxb-)", "type": "password", "required": True},
                {"key": "app_token", "label": "App Token (xapp-)", "type": "password", "required": True},
            ],
        },
        "discord": {
            "id": "discord",
            "label": "Discord",
            "description": "Connect to Discord via Gateway WebSocket",
            "config_fields": [
                {"key": "bot_token", "label": "Bot Token", "type": "password", "required": True},
                {"key": "guild_id", "label": "Guild ID (optional)", "type": "text", "required": False},
            ],
        },
        "web_widget": {
            "id": "web_widget",
            "label": "Web Widget",
            "description": "Embeddable chat widget for websites",
            "config_fields": [
                {"key": "allowed_origins", "label": "Allowed Origins", "type": "text_list", "required": False},
                {"key": "widget_theme", "label": "Theme", "type": "select", "options": ["light", "dark"], "required": False},
                {"key": "greeting_message", "label": "Greeting Message", "type": "text", "required": False},
            ],
        },
    }

    result = []
    for type_id, info in type_info.items():
        info["available"] = type_id in _ADAPTER_REGISTRY
        result.append(info)
    return result


def load_adapters() -> None:
    """Import all adapter modules to trigger registration.

    Each adapter module checks for its dependencies and registers
    itself if available.
    """
    # Feishu adapter
    try:
        from channels.adapters import feishu  # noqa: F401
    except ImportError:
        logger.info("Feishu adapter not available (lark-oapi not installed)")

    # Future: Slack adapter
    # try:
    #     from channels.adapters import slack  # noqa: F401
    # except ImportError:
    #     logger.info("Slack adapter not available (slack_bolt not installed)")

    # Future: Discord adapter
    # try:
    #     from channels.adapters import discord  # noqa: F401
    # except ImportError:
    #     logger.info("Discord adapter not available (discord.py not installed)")
