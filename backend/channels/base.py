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
    """Normalized message from an external channel into SwarmAI."""
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
    """Message from SwarmAI to be sent to an external channel."""
    channel_id: str
    external_chat_id: str
    external_thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# Type for the on_message callback: async function that takes InboundMessage
OnMessageCallback = Callable[[InboundMessage], Awaitable[None]]

# Type for the on_error callback: async function that takes (channel_id, error_message)
OnErrorCallback = Callable[[str, str], Awaitable[None]]


class ChannelAdapter(ABC):
    """Base class for channel adapters.

    Each adapter handles the translation between an external platform's
    message format and SwarmAI's internal InboundMessage/OutboundMessage.

    Lifecycle:
    1. __init__(channel_id, config, on_message) - created by gateway
    2. set_on_error(callback) - gateway registers error callback
    3. start() - begin listening for messages (long-running)
    4. send_message(outbound) - send a response back to the platform
    5. stop() - gracefully shut down

    If start() spawns a background thread, runtime failures should be
    reported via ``self._on_error(channel_id, error_message)`` so the
    gateway can update the channel status and schedule a retry.
    """

    def __init__(self, channel_id: str, config: dict, on_message: OnMessageCallback):
        self.channel_id = channel_id
        self.config = config
        self._on_message = on_message
        self._on_error: Optional[OnErrorCallback] = None

    def set_on_error(self, callback: OnErrorCallback) -> None:
        """Register a callback for reporting runtime errors to the gateway."""
        self._on_error = callback

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

    # ------------------------------------------------------------------
    # Optional streaming support (override in subclasses)
    # ------------------------------------------------------------------

    @property
    def supports_streaming(self) -> bool:
        """Whether this adapter supports live message updates.

        When True, the gateway will call ``send_typing_indicator`` before
        the agent runs, then ``update_message`` with incremental text as
        it streams, and finally ``update_message`` with the complete
        response.  Adapters that return False get a single
        ``send_message`` after the agent finishes.
        """
        return False

    async def send_typing_indicator(
        self,
        external_chat_id: str,
        external_thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """Post a "thinking" placeholder and return its message ID.

        The returned ID is passed to ``update_message`` for live edits.
        Default implementation is a no-op for adapters that don't support it.
        """
        return None

    async def update_message(
        self,
        external_chat_id: str,
        message_id: str,
        text: str,
        *,
        is_final: bool = False,
    ) -> None:
        """Replace the content of a previously-sent message.

        Called repeatedly as the agent streams text.  ``is_final=True``
        on the last call so the adapter can apply final formatting
        (e.g. Block Kit conversion).
        """
