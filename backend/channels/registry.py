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
        "slack": {
            "id": "slack",
            "label": "Slack",
            "description": "Connect to Slack via Socket Mode",
            "config_fields": [
                {"key": "bot_token", "label": "Bot Token (xoxb-)", "type": "password", "required": True},
                {"key": "app_token", "label": "App Token (xapp-)", "type": "password", "required": True},
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
    # Slack adapter
    try:
        from channels.adapters import slack  # noqa: F401
    except ImportError:
        logger.info("Slack adapter not available (slack-bolt not installed)")
