"""Channel system for connecting agents to external messaging platforms."""
from channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from channels.registry import get_adapter_class, list_supported_types, register_adapter
from channels.gateway import channel_gateway

__all__ = [
    "ChannelAdapter",
    "InboundMessage",
    "OutboundMessage",
    "get_adapter_class",
    "list_supported_types",
    "register_adapter",
    "channel_gateway",
]
