"""Base classes and data models for channel adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Awaitable

# Attachment types
ATTACH_TYPE_IMAGE = "image"
ATTACH_TYPE_FILE = "file"

# Max single attachment size (20 MB)
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024


@dataclass
class InboundMessage:
    """Normalized message from an external channel into owork."""
    channel_id: str
    external_chat_id: str
    external_sender_id: str
    external_thread_id: Optional[str] = None
    external_message_id: Optional[str] = None
    text: str = ""
    sender_display_name: Optional[str] = None
    attachments: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """Message from owork to be sent to an external channel."""
    channel_id: str
    external_chat_id: str
    external_thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# Type for the on_message callback: async function that takes InboundMessage
OnMessageCallback = Callable[[InboundMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """Base class for channel adapters.

    Each adapter handles the translation between an external platform's
    message format and owork's internal InboundMessage/OutboundMessage.

    Lifecycle:
    1. __init__(channel_id, config, on_message) - created by gateway
    2. start() - begin listening for messages (long-running)
    3. send_message(outbound) - send a response back to the platform
    4. stop() - gracefully shut down
    """

    def __init__(self, channel_id: str, config: dict, on_message: OnMessageCallback):
        self.channel_id = channel_id
        self.config = config
        self._on_message = on_message

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages. Called by gateway."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up resources."""
        ...

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> Optional[str]:
        """Send a message to the external platform.

        Returns:
            external_message_id if available, None otherwise
        """
        ...

    @abstractmethod
    async def validate_config(self) -> tuple[bool, Optional[str]]:
        """Validate the channel configuration.

        Returns:
            (is_valid, error_message)
        """
        ...

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """The type identifier of this channel adapter."""
        ...
