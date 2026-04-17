"""Slack channel adapter with Socket Mode primary + HTTP polling fallback.

**Primary path (Socket Mode):** Connects via the official slack-bolt SDK's
persistent WebSocket — no public URL or webhook endpoint needed.

**Fallback path (HTTP polling):** When Socket Mode is blocked by corporate
VPN (SSLEOFError, DNS failure, proxy 403), the adapter automatically
switches to polling ``conversations.history()`` via the Slack Web API.
After 3 consecutive WebSocket thread deaths the adapter activates polling
mode and periodically re-attempts Socket Mode reconnection.

**Outgoing messages:** Always via Web API; when that is blocked by corp
proxy, falls back to the ``slack-mcp`` binary via stdio JSON-RPC 2.0
(routes through Slack desktop's local IPC).

Threading model: Socket Mode runs in a background thread; polling runs
as an asyncio task on the main FastAPI event loop.  Both paths converge
on ``_normalize_event()`` → ``_on_message()`` for gateway delivery.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
    SLACK_BOLT_AVAILABLE = True
except ImportError:
    SLACK_BOLT_AVAILABLE = False

from channels.base import (
    ATTACH_TYPE_FILE,
    ATTACH_TYPE_IMAGE,
    MAX_ATTACHMENT_SIZE,
    ChannelAdapter,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

# Slack API limits
_TEXT_FALLBACK_LIMIT = 39_000   # text field (notification fallback) — hard limit ~40K
_BLOCK_SECTION_LIMIT = 3_000   # single section block text limit
_MAX_BLOCKS_PER_MSG = 50       # max blocks array length per message

# Errors that indicate corp proxy / network blocking (trigger MCP fallback)
_PROXY_ERRORS = (ConnectionError, OSError, TimeoutError)

# ---------------------------------------------------------------------------
# Polling fallback constants
# ---------------------------------------------------------------------------
_WS_FAIL_THRESHOLD = 3       # consecutive WS thread deaths before switching to polling
_POLL_INTERVAL = 5.0          # seconds between polling cycles
_POLL_DM_REFRESH = 300.0      # seconds between DM channel list refresh
_WS_RETRY_INTERVAL = 300.0    # seconds between Socket Mode reconnect attempts during polling
_POLL_MSG_LIMIT = 10          # max messages per conversations.history call

# Slack API error strings that indicate auth failure (permanent, not transient)
_AUTH_ERROR_CODES = frozenset({
    "invalid_auth", "token_revoked", "not_authed", "account_inactive",
    "token_expired", "org_login_required", "ekm_access_denied",
    "missing_scope",  # permanent — scope can't self-fix
})


class SlackAuthError(Exception):
    """Raised when a Slack API call fails due to authentication.

    Distinguishes auth failures (permanent until re-auth) from transient
    network errors.  The gateway uses this to skip retries and circuit-break.
    """

    def __init__(self, message: str, error_code: str = ""):
        super().__init__(message)
        self.error_code = error_code


def _is_auth_error(exc: Exception) -> bool:
    """Return True if *exc* is a Slack auth failure (not transient).

    Checks both slack_sdk.errors.SlackApiError.response["error"] and
    generic HTTP status codes (401, 403).
    """
    # SlackApiError from the SDK
    if hasattr(exc, "response"):
        resp = exc.response
        # Check error code string
        error_code = resp.get("error", "") if isinstance(resp, dict) else getattr(resp, "data", {}).get("error", "")
        if error_code in _AUTH_ERROR_CODES:
            return True
        # Check HTTP status code
        status = getattr(resp, "status_code", None)
        if status in (401, 403):
            return True
    return False


# ---------------------------------------------------------------------------
# MCP stdio bridge — fallback path for corp proxy environments
# ---------------------------------------------------------------------------

def _find_slack_mcp_config() -> Optional[dict]:
    """Find the slack-mcp server config from mcp-dev.json.

    Returns ``{"command": str, "args": list, "env": dict}`` or None.
    Searches the SwarmWS mcp-dev.json for the slack-mcp entry.
    """
    # SwarmWS location (standard path)
    mcp_dev = Path.home() / ".swarm-ai" / "SwarmWS" / ".claude" / "mcps" / "mcp-dev.json"
    if not mcp_dev.is_file():
        return None

    try:
        entries = json.loads(mcp_dev.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return None
        for entry in entries:
            eid = entry.get("id", "") or entry.get("name", "")
            if "slack" in eid.lower() and entry.get("enabled", True):
                config = entry.get("config", {})
                cmd = config.get("command", "")
                if cmd:
                    return {
                        "command": cmd,
                        "args": config.get("args", []),
                        "env": config.get("env", {}),
                    }
    except Exception:
        logger.debug("Failed to read slack-mcp config from %s", mcp_dev, exc_info=True)
    return None


class SlackMcpBridge:
    """Thin stdio JSON-RPC 2.0 bridge to the slack-mcp binary.

    Spawns the slack-mcp process on first use, performs the MCP
    initialization handshake, then reuses the connection for subsequent
    calls.  Thread-safe: all access serialized by ``_lock``.
    """

    def __init__(self) -> None:
        config = _find_slack_mcp_config()
        if config:
            self._command: str = config["command"]
            self._args: list = config["args"]
            self._env: dict = config["env"]
        else:
            self._command = ""
            self._args = []
            self._env = {}
        self._process: Optional[subprocess.Popen] = None
        self._initialized = False
        self._request_id = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True if a slack-mcp binary was found in config."""
        return bool(self._command)

    def _build_request(self, tool_name: str, arguments: dict) -> dict:
        """Build a JSON-RPC 2.0 tools/call request."""
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

    def _spawn(self) -> bool:
        """Spawn the slack-mcp subprocess if not already running."""
        if self._process and self._process.poll() is None:
            return True  # already alive

        if not self._command:
            return False

        cmd_path = Path(self._command)
        if not cmd_path.is_file():
            logger.warning("slack-mcp binary not found: %s", self._command)
            return False

        env = {**os.environ, **self._env}
        try:
            self._process = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )
            self._initialized = False
            return True
        except Exception:
            logger.exception("Failed to spawn slack-mcp process")
            self._process = None
            return False

    def _send_receive(self, request: dict, timeout: float = 15.0) -> Optional[dict]:
        """Send a JSON-RPC request and read the response line."""
        proc = self._process
        if not proc or proc.poll() is not None:
            return None

        line = json.dumps(request) + "\n"
        try:
            proc.stdin.write(line.encode())
            proc.stdin.flush()

            # Read one line with timeout via threading
            result = [None]
            exc_holder = [None]

            def _reader():
                try:
                    result[0] = proc.stdout.readline()
                except Exception as e:
                    exc_holder[0] = e

            t = threading.Thread(target=_reader, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if t.is_alive():
                logger.warning("slack-mcp response timed out after %.1fs", timeout)
                return None
            if exc_holder[0]:
                raise exc_holder[0]

            raw = result[0]
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            logger.debug("slack-mcp send/receive failed", exc_info=True)
            return None

    def _ensure_initialized(self) -> bool:
        """Perform the MCP initialize + initialized notification handshake."""
        if self._initialized:
            return True

        if not self._spawn():
            return False

        # Step 1: send initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "swarm-slack-adapter", "version": "1.0"},
            },
        }
        self._request_id = 1
        resp = self._send_receive(init_req, timeout=10.0)
        if not resp or "result" not in resp:
            logger.warning("slack-mcp initialize handshake failed: %s", resp)
            self.close()
            return False

        # Step 2: send initialized notification (no id, no response expected)
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.stdin.write((json.dumps(notif) + "\n").encode())
                proc.stdin.flush()
            except Exception:
                pass

        self._initialized = True
        logger.info("slack-mcp bridge initialized (pid=%s)", proc.pid if proc else "?")
        return True

    def call_tool(self, tool_name: str, arguments: dict) -> Optional[dict]:
        """Call an MCP tool and return the result (blocking, thread-safe).

        Returns the ``result`` dict from the JSON-RPC response, or None
        on any error.
        """
        with self._lock:
            if not self._ensure_initialized():
                return None
            request = self._build_request(tool_name, arguments)
            resp = self._send_receive(request)
            if resp and "result" in resp:
                return resp["result"]
            if resp and "error" in resp:
                logger.warning("slack-mcp tool error: %s", resp["error"])
            return None

    def close(self) -> None:
        """Terminate the MCP subprocess."""
        proc = self._process
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self._process = None
            self._initialized = False


class SlackChannelAdapter(ChannelAdapter):
    """Adapter for Slack using Socket Mode WebSocket.

    Config keys:
        bot_token:  Slack Bot Token (xoxb-...)
        app_token:  Slack App-Level Token (xapp-...)
    """

    def __init__(self, channel_id: str, config: dict, on_message) -> None:
        super().__init__(channel_id, config, on_message)
        self._bot_token: str = config.get("bot_token", "")
        self._app_token: str = config.get("app_token", "")
        self._bolt_app = None
        self._handler = None
        self._slack_client = None
        self._ws_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stopped = False
        # User name cache: user_id -> display_name
        self._user_cache: dict[str, str] = {}
        # MCP fallback bridge (lazy — spawned on first proxy error)
        self._mcp_bridge: Optional[SlackMcpBridge] = None
        # Auth health tracking
        self._last_auth_check: float = 0.0
        self._consecutive_auth_failures: int = 0
        # Polling fallback state
        self._connection_mode: str = "socket"  # "socket" or "polling"
        self._ws_fail_count: int = 0
        self._poll_channels: dict[str, str] = {}  # channel_id -> last_ts
        self._poll_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._bot_user_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, Optional[str]]:
        """Verify bot_token and app_token are present and valid."""
        if not self._bot_token or not self._bot_token.startswith("xoxb-"):
            return False, "Missing or invalid bot_token (must start with xoxb-)"
        if not self._app_token or not self._app_token.startswith("xapp-"):
            return False, "Missing or invalid app_token (must start with xapp-)"

        try:
            client = WebClient(token=self._bot_token)
            # Run sync auth_test in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, client.auth_test)
            if not result.get("ok"):
                error = result.get("error", "unknown")
                if error in _AUTH_ERROR_CODES:
                    return False, f"AUTH_ERROR: {error}"
                return False, f"Bot token auth failed: {error}"
            self._last_auth_check = time.time()
            return True, None
        except Exception as exc:
            if _is_auth_error(exc):
                return False, f"AUTH_ERROR: {exc}"
            return False, f"Slack credential check error: {exc}"

    async def revalidate_auth(self) -> bool:
        """Re-check token validity (called on suspected auth failures).

        Returns True if auth is still good, False if expired/revoked.
        Lightweight — single API call, no restart.
        """
        if not self._slack_client:
            return False
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, self._slack_client.auth_test,
            )
            ok = result.get("ok", False)
            if ok:
                self._last_auth_check = time.time()
            return ok
        except Exception as exc:
            logger.warning("Auth revalidation failed: %s", exc)
            return False

    async def start(self) -> None:
        """Start the adapter — Socket Mode primary, polling fallback.

        Launches Socket Mode in a background thread and starts a health
        monitor that switches to HTTP polling if the WS thread dies
        repeatedly (see ``_ws_health_monitor``).
        """
        if self._stopped:
            self._stopped = False

        self._loop = asyncio.get_running_loop()
        self._slack_client = WebClient(token=self._bot_token)

        # Start Socket Mode in background thread
        self._start_socket_mode_thread()

        # Start health monitor (switches to polling on persistent failure)
        self._monitor_task = asyncio.create_task(self._ws_health_monitor())

        logger.info(
            "Slack adapter started for channel %s (bot_token=xoxb-...%s)",
            self.channel_id,
            self._bot_token[-4:] if len(self._bot_token) > 4 else "****",
        )

    async def stop(self) -> None:
        """Stop the adapter and release resources."""
        self._stopped = True

        # Cancel polling task
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

        # Cancel health monitor
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None

        if self._handler is not None:
            try:
                self._handler.close()
            except Exception:
                pass

        ws_thread = self._ws_thread
        if ws_thread is not None and ws_thread.is_alive():
            ws_thread.join(timeout=3.0)
            if ws_thread.is_alive():
                logger.warning(
                    "Slack WS thread for channel %s did not stop within 3s",
                    self.channel_id,
                )

        self._bolt_app = None
        self._handler = None
        self._slack_client = None
        self._ws_thread = None
        self._loop = None
        self._poll_channels = {}

        # Clean up MCP bridge subprocess
        if self._mcp_bridge:
            self._mcp_bridge.close()
            self._mcp_bridge = None

        logger.info("Slack adapter stopped for channel %s", self.channel_id)

    # ------------------------------------------------------------------
    # Incoming messages — shared normalization + mode-specific delivery
    # ------------------------------------------------------------------

    def _normalize_event(self, event: dict) -> Optional[InboundMessage]:
        """Normalize a Slack event dict to an InboundMessage.

        Shared by both Socket Mode (background thread) and HTTP polling
        (asyncio task).  Returns None for events that should be skipped
        (bot messages, subtypes, empty text).
        """
        # Skip message subtypes (edited, deleted, etc.) except file_share
        subtype = event.get("subtype")
        if subtype and subtype not in ("file_share",):
            return None

        # Skip messages from bots (including ourselves)
        if event.get("bot_id"):
            return None

        user_id = event.get("user", "")

        # In polling mode, also filter by bot_user_id (no bot_id in history)
        if self._bot_user_id and user_id == self._bot_user_id:
            return None

        text = event.get("text", "").strip()
        channel_id = event.get("channel", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")
        channel_type = event.get("channel_type", "im")

        # Download any attached files
        attachments = []
        for file_info in event.get("files", []):
            attachment = self._download_file_sync(file_info)
            if attachment:
                attachments.append(attachment)

        if not text and not attachments:
            return None

        return InboundMessage(
            channel_id=self.channel_id,
            external_chat_id=channel_id,
            external_sender_id=user_id,
            external_thread_id=thread_ts,
            external_message_id=ts,
            text=text,
            sender_display_name=self._get_user_name(user_id),
            attachments=attachments,
            metadata={
                "chat_type": self._normalize_chat_type(channel_type),
                "message_type": "text",
                "ts": ts,
            },
        )

    def _handle_message_event(self, event: dict, say=None) -> None:
        """Handle an incoming message event from Slack Socket Mode.

        Called from the Socket Mode background thread.  Normalizes the
        event and bridges the result to the main asyncio loop.
        """
        if self._stopped:
            return

        msg = self._normalize_event(event)
        if msg is None:
            return

        # Bridge to main asyncio loop (called from background thread)
        main_loop = self._loop
        if main_loop is not None and not main_loop.is_closed() and not self._stopped:
            main_loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._on_message(msg),
            )

    def _handle_app_mention(self, event: dict, say=None) -> None:
        """Handle @bot mentions in channels.

        Delegates to _handle_message_event — mentions are just messages
        with channel_type context.
        """
        if self._stopped:
            return
        # Treat as a regular message — the channel_type will indicate
        # it's a group context, and the gateway handles group exclusion.
        event.setdefault("channel_type", "channel")
        self._handle_message_event(event, say)

    # ------------------------------------------------------------------
    # Chat type normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_chat_type(channel_type: str) -> str:
        """Normalize Slack channel_type to gateway chat_type.

        Slack types: im, mpim, channel, group
        Gateway types: im, mpim, channel (triggers is_group=True)
        """
        if channel_type == "im":
            return "im"
        if channel_type == "mpim":
            return "mpim"
        # Both "channel" and "group" map to "channel" (group context)
        return "channel"

    # ------------------------------------------------------------------
    # User name resolution
    # ------------------------------------------------------------------

    def _get_user_name(self, user_id: str) -> str:
        """Resolve a Slack user ID to a display name (cached).

        Tries users.info first; falls back to users.profile.get if the bot
        lacks the ``users:read`` scope (only needs ``users.profile:read``).
        """
        if user_id in self._user_cache:
            return self._user_cache[user_id]

        if not self._slack_client:
            return user_id

        # Attempt 1: users.info (requires users:read scope)
        try:
            result = self._slack_client.users_info(user=user_id)
            if result.get("ok"):
                user = result.get("user", {})
                profile = user.get("profile", {})
                name = (
                    profile.get("real_name_normalized")
                    or user.get("real_name")
                    or profile.get("display_name_normalized")
                    or profile.get("display_name")
                    or user.get("name")
                    or user_id
                )
                self._user_cache[user_id] = name
                return name
            logger.warning(
                "users.info returned ok=false for %s: %s",
                user_id, result.get("error"),
            )
        except Exception as exc:
            logger.warning(
                "users.info failed for %s: %s — trying users.profile.get",
                user_id, exc,
            )

        # Attempt 2: users.profile.get (only needs users.profile:read)
        try:
            result = self._slack_client.users_profile_get(user=user_id)
            if result.get("ok"):
                profile = result.get("profile", {})
                name = (
                    profile.get("real_name_normalized")
                    or profile.get("real_name")
                    or profile.get("display_name_normalized")
                    or profile.get("display_name")
                    or user_id
                )
                self._user_cache[user_id] = name
                return name
        except Exception as exc:
            logger.warning("users.profile.get also failed for %s: %s", user_id, exc)

        return user_id

    # ------------------------------------------------------------------
    # File download
    # ------------------------------------------------------------------

    def _download_file_sync(self, file_info: dict) -> Optional[dict]:
        """Download a Slack file using bot token auth."""
        import requests

        url = file_info.get("url_private_download") or file_info.get("url_private")
        if not url:
            return None

        size = file_info.get("size", 0)
        if size > MAX_ATTACHMENT_SIZE:
            logger.warning(
                "Slack file %s exceeds size limit (%d bytes), skipping",
                file_info.get("name"), size,
            )
            return None

        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._bot_token}"},
                timeout=30,
            )
            resp.raise_for_status()

            mimetype = file_info.get("mimetype", "application/octet-stream")
            return {
                "type": ATTACH_TYPE_IMAGE if mimetype.startswith("image/") else ATTACH_TYPE_FILE,
                "file_bytes": resp.content,
                "file_name": file_info.get("name", "attachment"),
                "file_size": len(resp.content),
                "mime_type": mimetype,
            }
        except Exception:
            logger.exception(
                "Failed to download Slack file %s", file_info.get("name"),
            )
            return None

    # ------------------------------------------------------------------
    # Streaming support — Native Slack Agents & AI Apps streaming API
    # ------------------------------------------------------------------
    #
    # Uses chat.startStream / chat.appendStream / chat.stopStream — the
    # purpose-built streaming API with NO rate limit (unlike chat.update
    # which is capped at ~50/min).  This is what makes streaming feel
    # instant instead of 1.2s-batched.
    #
    # Fallback: update_message (chat.update) remains for non-streaming
    # adapters or when native streaming fails.

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_native_streaming(self) -> bool:
        # Disabled: native streaming (startStream/appendStream/stopStream) renders
        # with an "AI inline" style that looks tool-like, not person-like.
        # Legacy path (postMessage → update) produces normal bot messages
        # with "🐝 Thinking..." → progressive updates → Block Kit final.
        return False

    async def _ensure_identity(self) -> None:
        """Resolve and cache team_id / bot_user_id (one-time, lazy)."""
        if hasattr(self, "_team_id"):
            return
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(
                None, self._slack_client.auth_test,
            )
            self._team_id: str = info.get("team_id", "")
            self._bot_user_id: str = info.get("user_id", "")
        except Exception:
            self._team_id = ""
            self._bot_user_id = ""

    async def start_stream(
        self,
        external_chat_id: str,
        external_thread_id: Optional[str] = None,
        text: Optional[str] = None,
        recipient_user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Start a native Slack stream. Returns stream message ts.

        Args:
            external_chat_id: Channel ID.
            external_thread_id: Thread ts (required — even for DMs, pass the
                inbound message ts).
            text: Optional initial markdown text.
            recipient_user_id: User ID for DM streaming (stopStream needs it).
        """
        if not self._slack_client:
            return None
        if not external_thread_id:
            logger.warning("start_stream called without thread_ts — native streaming requires it")
            return None
        try:
            loop = asyncio.get_running_loop()
            await self._ensure_identity()

            kwargs: dict = {
                "channel": external_chat_id,
                "thread_ts": external_thread_id,
            }
            if text:
                kwargs["markdown_text"] = text
            if self._team_id:
                kwargs["recipient_team_id"] = self._team_id
            if recipient_user_id:
                kwargs["recipient_user_id"] = recipient_user_id

            result = await loop.run_in_executor(
                None, lambda: self._slack_client.chat_startStream(**kwargs),
            )
            ts = result.get("ts")
            logger.info("Slack stream started: channel=%s thread=%s ts=%s", external_chat_id, external_thread_id, ts)
            return ts
        except Exception:
            logger.exception("Failed to start Slack native stream")
            return None

    async def append_stream(
        self,
        external_chat_id: str,
        stream_ts: str,
        text: str,
    ) -> None:
        """Append markdown text to an active Slack stream (no rate limit)."""
        if not self._slack_client or not text:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._slack_client.chat_appendStream(
                    channel=external_chat_id,
                    ts=stream_ts,
                    markdown_text=text,
                ),
            )
        except Exception:
            logger.debug("Failed to append to Slack stream ts=%s", stream_ts)

    async def stop_stream(
        self,
        external_chat_id: str,
        stream_ts: str,
        text: Optional[str] = None,
        final_blocks: Optional[list[dict]] = None,
        recipient_user_id: Optional[str] = None,
    ) -> None:
        """Stop a native Slack stream — message becomes a normal message."""
        if not self._slack_client:
            return
        try:
            loop = asyncio.get_running_loop()
            await self._ensure_identity()

            kwargs: dict = {
                "channel": external_chat_id,
                "ts": stream_ts,
            }
            if text:
                kwargs["markdown_text"] = text
            if final_blocks:
                kwargs["blocks"] = final_blocks
            if self._team_id:
                kwargs["recipient_team_id"] = self._team_id
            if recipient_user_id:
                kwargs["recipient_user_id"] = recipient_user_id
            await loop.run_in_executor(
                None, lambda: self._slack_client.chat_stopStream(**kwargs),
            )
            logger.info("Slack stream stopped: channel=%s ts=%s", external_chat_id, stream_ts)
        except Exception:
            logger.exception("Failed to stop Slack stream")

    # Legacy fallback — kept for non-native-streaming code paths
    async def send_typing_indicator(
        self,
        external_chat_id: str,
        external_thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """Post a placeholder message (fallback when native streaming unavailable).

        Falls back to MCP when WebClient is blocked by corp proxy.
        """
        if not self._slack_client:
            return None
        try:
            kwargs = {
                "channel": external_chat_id,
                "text": "Thinking...",
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": ":bee: _Thinking..._"},
                    }
                ],
            }
            if external_thread_id:
                kwargs["thread_ts"] = external_thread_id
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: self._slack_client.chat_postMessage(**kwargs)
            )
            return result.get("ts")
        except _PROXY_ERRORS:
            logger.warning(
                "Slack WebClient blocked (proxy?) — falling back to MCP for typing indicator"
            )
            return await self._mcp_post_message(
                external_chat_id,
                ":bee: _Thinking..._",
                external_thread_id,
            )
        except Exception as exc:
            if _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                logger.error(
                    "Slack AUTH_ERROR in typing indicator (consecutive=%d): %s",
                    self._consecutive_auth_failures, exc,
                )
                # Try MCP fallback — different auth path
                return await self._mcp_post_message(
                    external_chat_id,
                    ":bee: _Thinking..._",
                    external_thread_id,
                )
            logger.exception("Error sending Slack typing indicator")
            return None

    async def update_message(
        self,
        external_chat_id: str,
        message_id: str,
        text: str,
        *,
        is_final: bool = False,
    ) -> None:
        """Update message via chat.update (fallback path).

        All sync Slack SDK calls dispatched via ``run_in_executor``.
        Falls back to MCP when WebClient is blocked by corp proxy.
        """
        if not self._slack_client:
            return

        loop = asyncio.get_running_loop()
        client = self._slack_client

        try:
            if is_final:
                blocks = self._text_to_blocks(text)
                fallback = text[:_TEXT_FALLBACK_LIMIT]

                if len(blocks) <= _MAX_BLOCKS_PER_MSG:
                    await loop.run_in_executor(None, lambda: client.chat_update(
                        channel=external_chat_id,
                        ts=message_id,
                        text=fallback,
                        blocks=blocks,
                    ))
                else:
                    first_chunk = blocks[:_MAX_BLOCKS_PER_MSG]
                    await loop.run_in_executor(None, lambda: client.chat_update(
                        channel=external_chat_id,
                        ts=message_id,
                        text=fallback,
                        blocks=first_chunk,
                    ))
                    remaining = blocks[_MAX_BLOCKS_PER_MSG:]
                    while remaining:
                        chunk = remaining[:_MAX_BLOCKS_PER_MSG]
                        remaining = remaining[_MAX_BLOCKS_PER_MSG:]
                        try:
                            await loop.run_in_executor(None, lambda c=chunk: client.chat_postMessage(
                                channel=external_chat_id,
                                text="(continued)",
                                blocks=c,
                            ))
                        except Exception:
                            logger.warning("Failed to post overflow chunk")
                            break
            else:
                display = self._md_to_mrkdwn(text) + " :writing_hand:"
                if len(display) > _BLOCK_SECTION_LIMIT:
                    display = "..." + display[-(_BLOCK_SECTION_LIMIT - 20):] + " :writing_hand:"
                fallback = text[-_TEXT_FALLBACK_LIMIT:] if len(text) > _TEXT_FALLBACK_LIMIT else text
                await loop.run_in_executor(None, lambda: client.chat_update(
                    channel=external_chat_id,
                    ts=message_id,
                    text=fallback,
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": display},
                        }
                    ],
                ))
        except _PROXY_ERRORS:
            logger.warning(
                "Slack WebClient blocked (proxy?) — falling back to MCP for update_message"
            )
            await self._mcp_update_message(external_chat_id, message_id, text)
        except Exception as exc:
            if _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                logger.error(
                    "Slack AUTH_ERROR in update_message (consecutive=%d): %s",
                    self._consecutive_auth_failures, exc,
                )
                await self._mcp_update_message(external_chat_id, message_id, text)
                return
            logger.exception("Error updating Slack message")

    # ------------------------------------------------------------------
    # Status reactions (emoji feedback on inbound messages)
    # ------------------------------------------------------------------

    async def add_reaction(
        self,
        external_chat_id: str,
        message_ts: str,
        emoji: str,
    ) -> None:
        """Add an emoji reaction to a message."""
        if not self._slack_client:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._slack_client.reactions_add(
                    channel=external_chat_id, timestamp=message_ts, name=emoji,
                ),
            )
        except Exception:
            # Silently ignore — reaction failures shouldn't block anything
            logger.debug("Failed to add reaction %s to %s", emoji, message_ts)

    async def remove_reaction(
        self,
        external_chat_id: str,
        message_ts: str,
        emoji: str,
    ) -> None:
        """Remove an emoji reaction from a message."""
        if not self._slack_client:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._slack_client.reactions_remove(
                    channel=external_chat_id, timestamp=message_ts, name=emoji,
                ),
            )
        except Exception:
            logger.debug("Failed to remove reaction %s from %s", emoji, message_ts)

    # ------------------------------------------------------------------
    # MCP fallback helpers (corp proxy bypass)
    # ------------------------------------------------------------------

    def _get_mcp_bridge(self) -> Optional[SlackMcpBridge]:
        """Lazily create and return the MCP bridge singleton."""
        if self._mcp_bridge is None:
            bridge = SlackMcpBridge()
            if bridge.available:
                self._mcp_bridge = bridge
                logger.info("Slack MCP fallback bridge available")
            else:
                logger.debug("Slack MCP fallback bridge not available (no slack-mcp config)")
                return None
        return self._mcp_bridge

    async def _mcp_post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        """Send a message via slack-mcp MCP fallback (async wrapper).

        Returns the message ``ts`` on success, None on failure.
        """
        bridge = self._get_mcp_bridge()
        if not bridge:
            return None

        args: dict = {"channel_id": channel, "text": text}
        if thread_ts:
            args["thread_ts"] = thread_ts

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: bridge.call_tool("post_message", args),
        )
        if result:
            # MCP response: {"content": [{"type": "text", "text": "..."}]}
            # Try to extract ts from the text content
            content = result.get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    response_text = item.get("text", "")
                    # slack-mcp may return JSON with ts, or just confirmation text
                    try:
                        parsed = json.loads(response_text)
                        if isinstance(parsed, dict) and "ts" in parsed:
                            return parsed["ts"]
                    except (json.JSONDecodeError, TypeError):
                        pass
                    # If we got any content, the message was sent — return a synthetic ts
                    if response_text:
                        logger.info("MCP fallback sent message to %s (no ts in response)", channel)
                        return "mcp-sent"
        return None

    async def _mcp_update_message(
        self,
        channel: str,
        message_ts: str,
        text: str,
    ) -> bool:
        """Update a message via slack-mcp MCP fallback (async wrapper)."""
        bridge = self._get_mcp_bridge()
        if not bridge:
            return False

        args: dict = {"channel_id": channel, "message_ts": message_ts, "text": text}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: bridge.call_tool("edit_message", args),
        )
        return result is not None

    # ------------------------------------------------------------------
    # HTTP polling fallback — activates when Socket Mode is blocked
    # ------------------------------------------------------------------

    async def _switch_to_polling(self) -> None:
        """Switch from Socket Mode to HTTP polling fallback.

        Called when the WS thread has died ``_WS_FAIL_THRESHOLD`` times.
        Discovers DM channels and starts a polling asyncio task.
        """
        self._connection_mode = "polling"
        logger.warning(
            "Channel %s: switching to HTTP polling mode after %d "
            "consecutive Socket Mode failures",
            self.channel_id,
            self._ws_fail_count,
        )
        # Resolve bot identity (needed for filtering own messages)
        await self._ensure_identity()
        # Discover which channels to poll
        await self._discover_poll_channels()
        # Start the polling loop
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._run_polling())

    async def _discover_poll_channels(self) -> None:
        """Discover DM channels to poll via ``conversations.list``."""
        if not self._slack_client:
            return
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._slack_client.conversations_list(
                    types="im", limit=100, exclude_archived=True,
                ),
            )
            for ch in result.get("channels", []):
                ch_id = ch.get("id", "")
                if ch_id and ch_id not in self._poll_channels:
                    # New channel — start polling from now
                    self._poll_channels[ch_id] = str(time.time())
            logger.info(
                "Channel %s: discovered %d DM channels for polling",
                self.channel_id,
                len(self._poll_channels),
            )
        except _PROXY_ERRORS:
            logger.warning(
                "WebClient blocked during channel discovery — "
                "polling will use previously known channels"
            )
        except Exception:
            logger.exception("Failed to discover DM channels for polling")

    async def _poll_channel_messages(self, channel_id: str) -> None:
        """Poll a single channel for new messages via ``conversations.history``.

        Fetches messages newer than the last-seen timestamp, processes
        them oldest-first, and updates the timestamp watermark.
        """
        oldest = self._poll_channels.get(channel_id, str(time.time()))
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._slack_client.conversations_history(
                    channel=channel_id,
                    oldest=oldest,
                    limit=_POLL_MSG_LIMIT,
                    inclusive=False,
                ),
            )
            messages = result.get("messages", [])
            if not messages:
                return

            # API returns newest-first; process oldest-first.
            # conversations.history messages don't include 'channel' or
            # 'channel_type' — inject them so _normalize_event works.
            for msg_data in reversed(messages):
                msg_data.setdefault("channel", channel_id)
                msg_data.setdefault("channel_type", "im")
                msg = self._normalize_event(msg_data)
                if msg is not None:
                    await self._on_message(msg)
                # Always advance watermark (even for skipped bot messages)
                self._poll_channels[channel_id] = msg_data["ts"]
        except _PROXY_ERRORS:
            logger.debug(
                "WebClient blocked polling channel %s — skipping cycle",
                channel_id,
            )
        except Exception:
            logger.debug(
                "Failed to poll channel %s",
                channel_id,
                exc_info=True,
            )

    async def _run_polling(self) -> None:
        """Main polling loop — runs as an asyncio task.

        Polls all known DM channels, periodically refreshes the channel
        list, and attempts Socket Mode reconnection.
        """
        ws_retry_time = time.time() + _WS_RETRY_INTERVAL
        dm_refresh_time = time.time() + _POLL_DM_REFRESH

        logger.info(
            "Channel %s: HTTP polling started (%d channels, %.0fs interval)",
            self.channel_id,
            len(self._poll_channels),
            _POLL_INTERVAL,
        )

        while not self._stopped and self._connection_mode == "polling":
            # Poll each known channel
            for ch_id in list(self._poll_channels.keys()):
                if self._stopped or self._connection_mode != "polling":
                    return
                await self._poll_channel_messages(ch_id)

            # Periodically refresh DM channel list
            now = time.time()
            if now > dm_refresh_time:
                await self._discover_poll_channels()
                dm_refresh_time = now + _POLL_DM_REFRESH

            # Periodically attempt Socket Mode reconnection
            if now > ws_retry_time:
                if await self._try_socket_mode_reconnect():
                    logger.info(
                        "Channel %s: Socket Mode recovered — "
                        "switching back from polling",
                        self.channel_id,
                    )
                    return  # polling loop ends; Socket Mode takes over
                ws_retry_time = now + _WS_RETRY_INTERVAL

            await asyncio.sleep(_POLL_INTERVAL)

        logger.info("Channel %s: HTTP polling stopped", self.channel_id)

    async def _try_socket_mode_reconnect(self) -> bool:
        """Attempt to reconnect Socket Mode during polling.

        Returns True if reconnection succeeds (switches back to socket
        mode), False otherwise.
        """
        if self._stopped or not self._app_token:
            return False

        logger.info(
            "Channel %s: attempting Socket Mode reconnect...",
            self.channel_id,
        )
        try:
            bolt_app = App(token=self._bot_token)
            bolt_app.event("message")(self._handle_message_event)
            bolt_app.event("app_mention")(self._handle_app_mention)
            handler = SocketModeHandler(bolt_app, self._app_token)

            # Try connecting in a thread with a timeout
            connected = [False]

            def _try_connect():
                try:
                    handler.connect()  # non-blocking connect
                    connected[0] = True
                except Exception:
                    pass

            t = threading.Thread(target=_try_connect, daemon=True)
            t.start()
            t.join(timeout=10.0)

            if connected[0]:
                # Socket Mode works — switch back
                handler.close()  # close the test connection

                # Close old handler to release resources (sockets, threads)
                if self._handler is not None:
                    try:
                        self._handler.close()
                    except Exception:
                        pass

                self._bolt_app = bolt_app
                self._handler = SocketModeHandler(bolt_app, self._app_token)
                self._connection_mode = "socket"
                self._ws_fail_count = 0

                # Cancel polling task
                if self._poll_task and not self._poll_task.done():
                    self._poll_task.cancel()
                    self._poll_task = None

                # Start Socket Mode in background thread
                self._start_socket_mode_thread()
                # Restart health monitor
                if self._monitor_task and not self._monitor_task.done():
                    self._monitor_task.cancel()
                self._monitor_task = asyncio.create_task(
                    self._ws_health_monitor()
                )
                return True
            else:
                try:
                    handler.close()
                except Exception:
                    pass
                return False
        except Exception:
            logger.debug(
                "Socket Mode reconnect failed for channel %s",
                self.channel_id,
                exc_info=True,
            )
            return False

    async def _ws_health_monitor(self) -> None:
        """Monitor WebSocket thread health; switch to polling on failure.

        Runs as an asyncio task alongside Socket Mode.  Checks the WS
        thread every 10 seconds.  When the thread dies repeatedly,
        activates the polling fallback.
        """
        # Grace period for initial Socket Mode connection
        await asyncio.sleep(15)

        while not self._stopped and self._connection_mode == "socket":
            ws = self._ws_thread
            if ws is not None and not ws.is_alive() and not self._stopped:
                self._ws_fail_count += 1
                if self._ws_fail_count >= _WS_FAIL_THRESHOLD:
                    await self._switch_to_polling()
                    return  # monitor done — polling takes over
                else:
                    logger.info(
                        "Channel %s: Socket Mode thread died (%d/%d), "
                        "restarting...",
                        self.channel_id,
                        self._ws_fail_count,
                        _WS_FAIL_THRESHOLD,
                    )
                    # Reset handler so _start_socket_mode_thread creates
                    # a fresh one — reusing a crashed handler's start()
                    # may fail due to corrupted internal state.
                    if self._handler is not None:
                        try:
                            self._handler.close()
                        except Exception:
                            pass
                        self._handler = None
                    self._start_socket_mode_thread()
            await asyncio.sleep(10)

    def _start_socket_mode_thread(self) -> None:
        """Start (or restart) the Socket Mode background thread.

        Extracted from ``start()`` so both initial startup and internal
        reconnection share the same logic.
        """
        if self._bolt_app is None:
            self._bolt_app = App(token=self._bot_token)
            self._bolt_app.event("message")(self._handle_message_event)
            self._bolt_app.event("app_mention")(self._handle_app_mention)

        if self._handler is None:
            self._handler = SocketModeHandler(self._bolt_app, self._app_token)

        def _run_socket_mode():
            try:
                self._handler.start()  # Blocking
            except Exception as exc:
                if not self._stopped:
                    is_auth = _is_auth_error(exc)
                    if is_auth:
                        # Auth errors → escalate to gateway (circuit breaker)
                        self._consecutive_auth_failures += 1
                        logger.error(
                            "Slack Socket Mode AUTH_ERROR for channel %s "
                            "(consecutive=%d): %s",
                            self.channel_id,
                            self._consecutive_auth_failures,
                            exc,
                        )
                        error_msg = (
                            f"AUTH_ERROR: Socket Mode connection failed: {exc}"
                        )
                        main_loop = self._loop
                        if (
                            self._on_error is not None
                            and main_loop is not None
                            and not main_loop.is_closed()
                        ):
                            try:
                                main_loop.call_soon_threadsafe(
                                    asyncio.ensure_future,
                                    self._on_error(
                                        self.channel_id, error_msg
                                    ),
                                )
                            except RuntimeError:
                                pass  # loop already closed
                    else:
                        # Transport errors → DON'T escalate to gateway.
                        # The _ws_health_monitor detects the dead thread,
                        # counts failures, and switches to polling after
                        # _WS_FAIL_THRESHOLD deaths.  If we escalated here,
                        # the gateway would destroy this adapter and create
                        # a fresh one with _ws_fail_count=0 — polling would
                        # never activate.
                        logger.warning(
                            "Slack Socket Mode transport error for channel "
                            "%s (health monitor will handle): %s",
                            self.channel_id,
                            exc,
                        )

        self._ws_thread = threading.Thread(
            target=_run_socket_mode,
            daemon=True,
            name=f"slack-ws-{self.channel_id}",
        )
        self._ws_thread.start()

    # ------------------------------------------------------------------
    # Outgoing messages
    # ------------------------------------------------------------------

    async def send_message(self, message: OutboundMessage) -> Optional[str]:
        """Send a message back to Slack with Block Kit formatting.

        Converts markdown-style text to Slack's ``mrkdwn`` format and
        wraps it in section blocks.  Long messages are automatically
        split across multiple Slack messages to stay within API limits.

        Falls back to ``slack-mcp`` via stdio JSON-RPC when the Web API
        is unreachable (corp proxy blocking direct HTTPS to slack.com).
        """
        if not self._slack_client:
            return None

        loop = asyncio.get_running_loop()
        client = self._slack_client

        try:
            blocks = self._text_to_blocks(message.text)
            fallback = message.text[:_TEXT_FALLBACK_LIMIT]

            thread_ts = message.external_thread_id or None

            # Split blocks into chunks of _MAX_BLOCKS_PER_MSG
            first_ts: Optional[str] = None
            block_chunks = [
                blocks[i:i + _MAX_BLOCKS_PER_MSG]
                for i in range(0, len(blocks), _MAX_BLOCKS_PER_MSG)
            ] or [[{"type": "section", "text": {"type": "mrkdwn", "text": " "}}]]

            for idx, chunk in enumerate(block_chunks):
                kwargs = {
                    "channel": message.external_chat_id,
                    "text": fallback if idx == 0 else "(continued)",
                    "blocks": chunk,
                }
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts

                result = await loop.run_in_executor(
                    None, lambda kw=kwargs: client.chat_postMessage(**kw)
                )
                if idx == 0:
                    first_ts = result.get("ts")

            return first_ts
        except _PROXY_ERRORS:
            logger.warning(
                "Slack WebClient blocked (proxy?) — falling back to MCP for send_message"
            )
            return await self._mcp_post_message(
                message.external_chat_id,
                message.text,
                message.external_thread_id,
            )
        except Exception as exc:
            if _is_auth_error(exc):
                self._consecutive_auth_failures += 1
                logger.error(
                    "Slack AUTH_ERROR in send_message (consecutive=%d): %s",
                    self._consecutive_auth_failures, exc,
                )
                # Try MCP fallback — different auth path
                return await self._mcp_post_message(
                    message.external_chat_id,
                    message.text,
                    message.external_thread_id,
                )
            logger.exception("Error sending Slack message")
            return None

    # ------------------------------------------------------------------
    # Block Kit helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _md_to_mrkdwn(text: str) -> str:
        """Convert markdown to Slack mrkdwn (inline conversion, no blocks).

        Used for streaming updates where Block Kit overhead is unnecessary.
        Lightweight — skips table conversion (too expensive mid-stream).
        """
        import re

        if not text:
            return " "

        mrkdwn = text

        # Images FIRST: ![alt](url) -> just URL
        mrkdwn = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'\2', mrkdwn)
        # Links [text](url) -> <url|text>
        mrkdwn = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', mrkdwn)
        # Headers # Title -> *Title*
        mrkdwn = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', mrkdwn, flags=re.MULTILINE)

        # Process outside code fences only
        parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', mrkdwn)
        for i, part in enumerate(parts):
            if not part.startswith('`'):
                part = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', part)
                part = re.sub(r'\*\*(.+?)\*\*', r'*\1*', part)
                part = re.sub(r'~~(.+?)~~', r'~\1~', part)
                parts[i] = part

        return ''.join(parts)

    @staticmethod
    def _text_to_blocks(text: str) -> list[dict]:
        """Convert markdown text to Slack Block Kit blocks.

        Slack's ``mrkdwn`` is close to markdown but not identical:
        - Italic: *text* -> _text_  (processed first to avoid bold collision)
        - Bold: **text** -> *text*
        - Strikethrough: ~~text~~ -> ~text~
        - Code blocks and inline code work as-is.
        - Links: [text](url) -> <url|text>

        Limitation: ``***bold+italic***`` doesn't convert cleanly (rare in practice).

        Each section block has a 3000-char limit, so long messages
        are split across multiple blocks.
        """
        import re

        if not text:
            return [{"type": "section", "text": {"type": "mrkdwn", "text": " "}}]

        mrkdwn = text

        # ── Pre-processing (before code-fence splitting) ──────────

        # Images FIRST: ![alt](url) -> just the URL (Slack auto-unfurls)
        # Must run before link conversion or the ![...] gets partially matched.
        mrkdwn = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'\2', mrkdwn)

        # Convert markdown links [text](url) -> <url|text>
        mrkdwn = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', mrkdwn)

        # Headers: # Title -> *Title* (bold, since Slack has no header syntax)
        mrkdwn = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', mrkdwn, flags=re.MULTILINE)

        # Horizontal rules: --- or *** or ___ -> visual separator
        mrkdwn = re.sub(r'^[\-\*_]{3,}\s*$', '─' * 30, mrkdwn, flags=re.MULTILINE)

        # Tables: convert to code block for readability (Slack has no table support)
        lines = mrkdwn.split('\n')
        in_table = False
        table_lines: list[str] = []
        result_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            is_table_row = bool(re.match(r'^\|.*\|$', stripped))
            is_separator = bool(re.match(r'^\|[\s\-:|]+\|$', stripped))

            if is_table_row and not in_table:
                in_table = True
                table_lines = [stripped]
            elif in_table and (is_table_row or is_separator):
                if not is_separator:  # skip the |---|---| line
                    table_lines.append(stripped)
            elif in_table:
                # End of table — emit as code block
                result_lines.append('```')
                result_lines.extend(table_lines)
                result_lines.append('```')
                table_lines = []
                in_table = False
                result_lines.append(line)
            else:
                result_lines.append(line)

        if in_table and table_lines:
            result_lines.append('```')
            result_lines.extend(table_lines)
            result_lines.append('```')

        mrkdwn = '\n'.join(result_lines)

        # ── Code-fence-aware formatting ───────────────────────────

        # Process outside of code fences only
        parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', mrkdwn)
        for i, part in enumerate(parts):
            if not part.startswith('`'):
                # Italic first: *text* -> _text_ (single asterisks only, not **)
                part = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', part)
                # Bold: **text** -> *text*
                part = re.sub(r'\*\*(.+?)\*\*', r'*\1*', part)
                # Strikethrough: ~~text~~ -> ~text~
                part = re.sub(r'~~(.+?)~~', r'~\1~', part)
                parts[i] = part
        mrkdwn = ''.join(parts)

        # Split into 3000-char blocks (Slack section limit)
        _BLOCK_LIMIT = 3000
        blocks: list[dict] = []

        if len(mrkdwn) <= _BLOCK_LIMIT:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": mrkdwn},
            })
        else:
            # Split on paragraph boundaries to avoid mid-sentence breaks
            paragraphs = mrkdwn.split('\n\n')
            current = ""
            for para in paragraphs:
                candidate = f"{current}\n\n{para}" if current else para
                if len(candidate) > _BLOCK_LIMIT and current:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": current},
                    })
                    current = para
                else:
                    current = candidate
            if current:
                # Final chunk may still exceed limit — hard-split as last resort
                while len(current) > _BLOCK_LIMIT:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": current[:_BLOCK_LIMIT]},
                    })
                    current = current[_BLOCK_LIMIT:]
                if current.strip():
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": current},
                    })

        return blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": " "}}]

    # ------------------------------------------------------------------
    # Presence management (AC5: daemon lifecycle)
    # ------------------------------------------------------------------

    async def set_presence(self, presence: str) -> None:
        """Set the Slack bot's presence status.

        Args:
            presence: ``"auto"`` (online when active) or ``"away"``.
        """
        if not self._slack_client:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self._slack_client.users_setPresence(presence=presence)
            )
        except Exception:
            logger.debug("Failed to set Slack presence to %s", presence)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def channel_type(self) -> str:
        return "slack"


# ------------------------------------------------------------------
# Self-registration
# ------------------------------------------------------------------
if SLACK_BOLT_AVAILABLE:
    from channels.registry import register_adapter
    register_adapter("slack", SlackChannelAdapter)
else:
    logger.debug(
        "Slack adapter not registered: slack-bolt package is not installed"
    )
