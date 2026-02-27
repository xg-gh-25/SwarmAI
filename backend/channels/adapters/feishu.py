"""Feishu (飞书) channel adapter using lark-oapi WebSocket long connection.

Connects to Feishu via the official lark-oapi SDK's WebSocket client,
so no public URL or webhook endpoint is needed. Messages are received
through the persistent WS connection and sent back via the REST API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import threading
from typing import Optional

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        GetImageRequest,
        GetMessageResourceRequest,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False

from channels.base import (
    ATTACH_TYPE_FILE,
    ATTACH_TYPE_IMAGE,
    MAX_ATTACHMENT_SIZE,
    ChannelAdapter,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

# Regex to strip @bot mentions that Feishu injects in group messages.
# Feishu formats mentions as @_user_1 or similar placeholders in the text field.
_AT_BOT_PATTERN = re.compile(r"@_user_\d+\s*")


class FeishuChannelAdapter(ChannelAdapter):
    """Adapter for Feishu/Lark using WebSocket long connection.

    Config keys:
        app_id:     Feishu app ID
        app_secret: Feishu app secret
    """

    def __init__(self, channel_id: str, config: dict, on_message) -> None:
        super().__init__(channel_id, config, on_message)
        self._app_id: str = config.get("app_id", "")
        self._app_secret: str = config.get("app_secret", "")
        self._ws_client = None
        self._api_client = None
        self._ws_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Event loop running inside the WS thread (for shutdown signalling)
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, Optional[str]]:
        """Verify that app_id / app_secret are present and credentials work."""
        if not self._app_id:
            return False, "Missing required config key: app_id"
        if not self._app_secret:
            return False, "Missing required config key: app_secret"

        try:
            client = (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .log_level(lark.LogLevel.WARNING)
                .build()
            )
            # Attempt to obtain a tenant access token to prove the credentials
            # are valid.  The SDK caches the token internally, so this also
            # warms the cache for the first real request.
            request = (
                lark.api.auth.v3.InternalTenantAccessTokenRequest.builder()
                .request_body(
                    lark.api.auth.v3.InternalTenantAccessTokenRequestBody.builder()
                    .app_id(self._app_id)
                    .app_secret(self._app_secret)
                    .build()
                )
                .build()
            )
            response = client.auth.v3.tenant_access_token.internal(request)
            if not response.success():
                return False, f"Feishu credential check failed: {response.msg}"
        except Exception as exc:
            return False, f"Feishu credential check error: {exc}"

        return True, None

    async def start(self) -> None:
        """Start the WebSocket long connection in a background thread."""
        if self._stopped:
            self._stopped = False

        # Capture the running event loop so we can bridge callbacks
        # from the WS thread back into asyncio.
        self._loop = asyncio.get_running_loop()

        # API client used for *sending* messages via REST.
        self._api_client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        # Build the event handler here (it has no event-loop dependency).
        self._event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_event)
            .build()
        )

        def _run_ws_with_own_loop():
            """Run the lark WS client in a thread with its own event loop.

            **Why everything is created here rather than in ``start()``:**

            1. The lark SDK uses a *module-level* ``loop`` variable (captured
               at import time) for all ``run_until_complete`` calls.  Under
               uvicorn + uvloop that loop is already running → RuntimeError.
               We patch it with a fresh loop created in this thread.

            2. ``lark.ws.Client.__init__`` creates an ``asyncio.Lock()``
               which binds to whatever loop is current at construction time.
               If the Client is created on the main thread but ``start()``
               runs on the patched loop, the Lock belongs to the wrong loop
               and async operations (connect, ping, receive) silently fail
               or timeout.  Creating the Client *inside* this thread solves
               both problems.
            """
            import lark_oapi.ws.client as ws_mod

            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            ws_mod.loop = new_loop          # patch the module-level loop
            # Store reference so stop() can terminate this loop from main thread
            self._ws_loop = new_loop

            try:
                # Create the WS client HERE so its asyncio.Lock() and any
                # other async primitives bind to new_loop.
                self._ws_client = lark.ws.Client(
                    self._app_id,
                    self._app_secret,
                    event_handler=self._event_handler,
                    log_level=lark.LogLevel.INFO,
                )
                self._ws_client.start()
            except Exception:
                if not self._stopped:
                    logger.exception(
                        "Feishu WS client crashed for channel %s",
                        self.channel_id,
                    )
            finally:
                self._ws_loop = None
                new_loop.close()

        self._ws_thread = threading.Thread(
            target=_run_ws_with_own_loop,
            daemon=True,
            name=f"feishu-ws-{self.channel_id}",
        )
        self._ws_thread.start()
        logger.info(
            "Feishu adapter started for channel %s (app_id=%s)",
            self.channel_id,
            self._app_id,
        )

    async def stop(self) -> None:
        """Stop the adapter and release resources.

        Terminates the WebSocket daemon thread by stopping its event loop,
        which causes the blocking ``lark.ws.Client.start()`` call to return.
        """
        self._stopped = True

        # Stop the WS thread's event loop so the blocking start() returns and
        # the daemon thread exits.  call_soon_threadsafe is the only safe way
        # to signal an event loop from another thread.
        ws_loop = self._ws_loop
        if ws_loop is not None and not ws_loop.is_closed():
            try:
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except RuntimeError:
                pass  # loop already closed

        # Wait briefly for the thread to finish
        ws_thread = self._ws_thread
        if ws_thread is not None and ws_thread.is_alive():
            ws_thread.join(timeout=3.0)
            if ws_thread.is_alive():
                logger.warning(
                    "Feishu WS thread for channel %s did not stop within 3s",
                    self.channel_id,
                )

        self._ws_client = None
        self._api_client = None
        self._ws_thread = None
        self._ws_loop = None
        self._loop = None
        logger.info("Feishu adapter stopped for channel %s", self.channel_id)

    # ------------------------------------------------------------------
    # Incoming messages (called from WS thread)
    # ------------------------------------------------------------------

    def _handle_message_event(self, data: "lark.im.v1.P2ImMessageReceiveV1") -> None:
        """Handle an incoming im.message.receive_v1 event.

        This callback runs on the WebSocket thread, NOT on the asyncio event
        loop, so we bridge into asyncio via ``call_soon_threadsafe``.
        """
        # Fast path: if the adapter has been stopped, discard immediately.
        # This prevents log noise from daemon threads that outlive stop().
        if self._stopped:
            return

        logger.info("Feishu message event received for channel %s", self.channel_id)
        try:
            event = data.event
            message = event.message
            message_type: str = message.message_type

            # Supported message types
            if message_type not in ("text", "image", "file"):
                logger.debug(
                    "Ignoring unsupported message type (type=%s, id=%s)",
                    message_type,
                    message.message_id,
                )
                return

            chat_id: str = message.chat_id
            open_id: str = event.sender.sender_id.open_id
            message_id: str = message.message_id
            chat_type: str = message.chat_type  # "p2p" or "group"

            # The content field is a JSON string, e.g. '{"text":"hello"}'.
            try:
                content_obj = json.loads(message.content)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Failed to parse message content for message_id=%s",
                    message_id,
                )
                return

            text: str = ""
            attachments: list[dict] = []

            if message_type == "text":
                text = content_obj.get("text", "")
                # In group chats, strip @bot mention placeholders so the agent
                # only sees the actual user text.
                if chat_type == "group":
                    text = _AT_BOT_PATTERN.sub("", text).strip()

            elif message_type == "image":
                image_key = content_obj.get("image_key", "")
                if image_key:
                    attachment = self._download_image(image_key)
                    if attachment:
                        attachments.append(attachment)

            elif message_type == "file":
                file_key = content_obj.get("file_key", "")
                file_name = content_obj.get("file_name", "attachment")
                if file_key:
                    attachment = self._download_file(message_id, file_key, file_name)
                    if attachment:
                        attachments.append(attachment)

            if not text and not attachments:
                logger.debug(
                    "No text or attachments after processing, skipping message_id=%s",
                    message_id,
                )
                return

            msg = InboundMessage(
                channel_id=self.channel_id,
                external_chat_id=chat_id,
                external_sender_id=open_id,
                external_message_id=message_id,
                text=text,
                attachments=attachments,
                metadata={
                    "chat_type": chat_type,
                    "message_type": message_type,
                },
            )

            # Bridge from sync WS thread into the asyncio event loop.
            main_loop = self._loop
            if main_loop is not None and not main_loop.is_closed() and not self._stopped:
                main_loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._on_message(msg),
                )
            else:
                logger.warning(
                    "Event loop unavailable; dropping inbound message %s "
                    "(loop=%s, stopped=%s)",
                    message_id,
                    main_loop,
                    self._stopped,
                )

        except Exception:
            logger.exception("Error handling Feishu message event")

    # ------------------------------------------------------------------
    # File / image download helpers (run on WS thread, sync SDK calls)
    # ------------------------------------------------------------------

    def _download_image(self, image_key: str) -> Optional[dict]:
        """Download an image from Feishu by its image_key.

        Returns an attachment dict or None on failure.
        """
        if not self._api_client:
            logger.warning("Cannot download image: API client not initialised")
            return None

        try:
            request = (
                GetImageRequest.builder()
                .image_key(image_key)
                .build()
            )
            response = self._api_client.im.v1.image.get(request)
            if not response.success():
                logger.warning(
                    "Failed to download image %s: code=%s msg=%s",
                    image_key, response.code, response.msg,
                )
                return None

            file_bytes: bytes = response.file.read()
            if len(file_bytes) > MAX_ATTACHMENT_SIZE:
                logger.warning(
                    "Image %s exceeds size limit (%d bytes), skipping",
                    image_key, len(file_bytes),
                )
                return None

            file_name = f"{image_key}.png"
            mime_type = _guess_image_mime(file_name, file_bytes)

            return {
                "type": ATTACH_TYPE_IMAGE,
                "file_bytes": file_bytes,
                "file_name": file_name,
                "file_size": len(file_bytes),
                "mime_type": mime_type,
                "file_key": image_key,
            }
        except Exception:
            logger.exception("Error downloading image %s", image_key)
            return None

    def _download_file(self, message_id: str, file_key: str, file_name: str) -> Optional[dict]:
        """Download a file attachment from Feishu by its file_key.

        Returns an attachment dict or None on failure.
        """
        if not self._api_client:
            logger.warning("Cannot download file: API client not initialised")
            return None

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type("file")
                .build()
            )
            response = self._api_client.im.v1.message_resource.get(request)
            if not response.success():
                logger.warning(
                    "Failed to download file %s (key=%s): code=%s msg=%s",
                    file_name, file_key, response.code, response.msg,
                )
                return None

            file_bytes: bytes = response.file.read()
            if len(file_bytes) > MAX_ATTACHMENT_SIZE:
                logger.warning(
                    "File %s exceeds size limit (%d bytes), skipping",
                    file_name, len(file_bytes),
                )
                return None

            mime_type = _guess_mime_type(file_name)

            return {
                "type": ATTACH_TYPE_FILE,
                "file_bytes": file_bytes,
                "file_name": file_name,
                "file_size": len(file_bytes),
                "mime_type": mime_type,
                "file_key": file_key,
            }
        except Exception:
            logger.exception("Error downloading file %s (key=%s)", file_name, file_key)
            return None

    # ------------------------------------------------------------------
    # Outgoing messages
    # ------------------------------------------------------------------

    async def send_message(self, message: OutboundMessage) -> Optional[str]:
        """Send a text message (or reply) back to Feishu."""
        if self._api_client is None:
            logger.error("Cannot send message: Feishu API client not initialised")
            return None

        content = json.dumps({"text": message.text})

        try:
            if message.reply_to_message_id:
                # Reply to a specific message in the same thread.
                request = (
                    ReplyMessageRequest.builder()
                    .message_id(message.reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("text")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = self._api_client.im.v1.message.reply(request)
            else:
                # Send a new message to the chat.
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(message.external_chat_id)
                        .msg_type("text")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = self._api_client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    "Feishu send_message failed: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
                return None

            sent_id = response.data.message_id if response.data else None
            logger.debug("Feishu message sent: message_id=%s", sent_id)
            return sent_id

        except Exception:
            logger.exception("Error sending Feishu message")
            return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def channel_type(self) -> str:
        return "feishu"


# ------------------------------------------------------------------
# MIME type helpers
# ------------------------------------------------------------------

# Magic bytes for common image formats
_IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # WebP starts with RIFF....WEBP
    (b"BM", "image/bmp"),
]


def _guess_image_mime(file_name: str, file_bytes: bytes) -> str:
    """Guess the MIME type of an image, falling back to magic bytes."""
    mime, _ = mimetypes.guess_type(file_name)
    if mime and mime.startswith("image/"):
        return mime
    for magic, mime_type in _IMAGE_MAGIC:
        if file_bytes[:len(magic)] == magic:
            return mime_type
    return "image/png"


def _guess_mime_type(file_name: str) -> str:
    """Guess the MIME type of a file by name."""
    mime, _ = mimetypes.guess_type(file_name)
    return mime or "application/octet-stream"


# ------------------------------------------------------------------
# Self-registration
# ------------------------------------------------------------------
if LARK_AVAILABLE:
    from channels.registry import register_adapter

    register_adapter("feishu", FeishuChannelAdapter)
else:
    logger.debug(
        "Feishu adapter not registered: lark-oapi package is not installed"
    )
