"""A small async MCP client over stdio (JSON-RPC 2.0, one JSON object per line).

The one transport for every part of the backend that calls a configured MCP server
directly (the Inbox sources). The agent's own MCP use goes through the Claude CLI, not
through this module.

What it does:

- spawns the server (``command`` + ``args``, with the server's ``env`` over the daemon's),
  performs ``initialize`` and ``notifications/initialized`` on first use, then sends
  ``tools/call`` and ``tools/list`` requests;
- reads the server's stdout for the whole life of the process in one reader task and hands
  each response to the request with the same JSON-RPC id, so a notification, a log line on
  stdout or a late answer can never be taken for the answer of another call;
- drains stderr into a bounded tail (used to explain a failure, never logged), so a chatty
  server cannot block on a full pipe;
- bounds every call by a timeout; a call that times out terminates the server, so a hung
  server is never reused (the next call starts a fresh one);
- ``close()`` terminates the server (and its process group on POSIX), killing it after a
  short grace period.

``resolve_server(server_id)`` reads a server's configuration **by its exact configured id**
from the two MCP layer files through ``core/mcp_config_loader`` (merge, validation and
``$VAR`` expansion are that module's), and ``parse_tool_result`` turns a ``tools/call``
result into data: ``structuredContent`` when present, else the text content with any
``<untrusted_content ...>`` wrapper removed, parsed as JSON when it is JSON. A tool result
is external data; nothing here interprets it as instructions.

Key public symbols: ``McpStdioClient``, ``McpServerSpec``, ``resolve_server``,
``parse_tool_result``, ``result_text``, ``unwrap_untrusted``, and the errors ``McpError``,
``McpUnavailable``, ``McpTimeout``, ``McpProtocolError``, ``McpToolError``,
``McpToolRefused``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_CALL_TIMEOUT = 15.0
DEFAULT_INIT_TIMEOUT = 10.0
# asyncio's default StreamReader limit is 64 KiB per line; an enriched Slack channel or a
# whole mail conversation is easily larger, and readline() would fail on it.
LINE_LIMIT = 32 * 1024 * 1024
_STDERR_TAIL_BYTES = 8 * 1024
_CLOSE_GRACE_S = 3.0


class McpError(Exception):
    """Any failure talking to an MCP server."""


class McpUnavailable(McpError):
    """No usable configuration, or the server could not be started."""


class McpTimeout(McpError):
    """A request got no answer within its timeout. The server was terminated."""


class McpProtocolError(McpError):
    """The server broke the protocol: failed handshake, exited, or sent no usable answer."""


class McpToolError(McpError):
    """The server answered a tool call with an error (``isError`` or a JSON-RPC error)."""

    def __init__(self, message: str, result: Optional[dict] = None) -> None:
        super().__init__(message)
        self.result = result


class McpToolRefused(McpError):
    """The tool is in the server entry's ``rejected_tools``: the person turned it off."""


@dataclass(frozen=True)
class McpServerSpec:
    """How to start one stdio MCP server, as resolved from the MCP configuration."""

    id: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    rejected_tools: frozenset[str] = frozenset()


def resolve_server(server_id: str, workspace: Optional[Path] = None) -> McpServerSpec:
    """Return the stdio server configured under exactly ``server_id``.

    Reads ``mcp-catalog.json`` and ``mcp-dev.json`` of the workspace (dev overrides catalog
    by id, disabled entries are dropped), validates the entry and expands ``$VAR`` in its
    args and env the same way the agent's MCP configuration does. Raises ``McpUnavailable``
    when no enabled entry has that id, the entry is invalid, or it is not a stdio server.
    """
    from core import mcp_config_loader as loader
    from utils.mcp_validation import validate_config_entry

    if not server_id:
        raise McpUnavailable("no MCP server id given")
    if workspace is None:
        from config import get_swarmws

        workspace = get_swarmws()
    catalog_path, dev_path = loader.get_mcp_file_paths(Path(workspace))
    entries = loader.merge_layers(
        loader.read_layer(catalog_path, default_enabled=False),
        loader.read_layer(dev_path, default_enabled=True),
    )
    entry = next((e for e in entries if isinstance(e, dict) and e.get("id") == server_id), None)
    if entry is None:
        raise McpUnavailable(f"no enabled MCP server with id {server_id!r}")
    try:
        errors = validate_config_entry(entry)
    except Exception as exc:  # noqa: BLE001 - it raises for a refused env, returns for the rest
        errors = [str(exc) or type(exc).__name__]
    if errors:
        raise McpUnavailable(f"MCP server {server_id!r} is misconfigured: {'; '.join(errors)}")
    if entry.get("connection_type", "stdio") != "stdio":
        raise McpUnavailable(f"MCP server {server_id!r} is not a stdio server")
    servers: dict = {}
    loader.add_mcp_server_to_dict(entry, servers, [], set())
    if not servers:
        raise McpUnavailable(f"MCP server {server_id!r} has no command")
    (cfg,) = servers.values()
    env = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
    return McpServerSpec(
        id=server_id,
        command=cfg["command"],
        args=tuple(str(a) for a in cfg.get("args") or ()),
        env=env,
        rejected_tools=frozenset(str(t) for t in entry.get("rejected_tools") or ()),
    )


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

# The tag may carry a boundary suffix (``<untrusted_content_ba28ac51cf93064b>``): then only
# the closing tag with the same suffix ends the block, which the wrapped data cannot forge.
_UNTRUSTED_OPEN_RE = re.compile(r"<untrusted_content(_[A-Za-z0-9]+)?(?=[\s>/])[^>]*>", re.I)


def _closing_re(suffix: str) -> re.Pattern[str]:
    return re.compile(r"</untrusted_content" + re.escape(suffix) + r"\s*>", re.I)


def unwrap_untrusted(text: str) -> str:
    """Return the text inside ``<untrusted_content ...>`` wrappers, or the text itself.

    Some servers wrap what they fetched in that tag (with attributes, sometimes with a
    preamble before it). The span from the first opening tag to the **last** closing tag
    is taken first, so a closing tag that appears inside the wrapped data (a message
    whose text contains ``</untrusted_content>``) cannot cut the payload short; only when
    that span is not JSON and there are several separate wrapped blocks are the blocks
    joined by a newline. An opening tag without a closing tag yields everything after it.
    """
    if not text:
        return ""
    opening = _UNTRUSTED_OPEN_RE.search(text)
    if not opening:
        return text.strip()
    suffix = opening.group(1) or ""
    closing = _closing_re(suffix)
    closes = [m for m in closing.finditer(text) if m.start() >= opening.end()]
    if not closes:
        return text[opening.end():].strip()
    outer = text[opening.end():closes[-1].start()].strip()
    try:
        json.loads(outer)
        return outer
    except ValueError:
        pass
    blocks = []
    pos = 0
    while True:
        o = _UNTRUSTED_OPEN_RE.search(text, pos)
        if not o:
            break
        c = _closing_re(o.group(1) or "").search(text, o.end())
        if not c:
            break
        blocks.append(text[o.end():c.start()].strip())
        pos = c.end()
    if len(blocks) > 1:
        return "\n".join(blocks)
    return outer


def _text_items(result: dict) -> list[str]:
    items = [
        unwrap_untrusted(i["text"])
        for i in result.get("content") or ()
        if isinstance(i, dict) and i.get("type") == "text" and isinstance(i.get("text"), str)
    ]
    return [i for i in items if i]


def result_text(result: Optional[dict]) -> str:
    """The text items of a ``tools/call`` result, unwrapped and joined by newlines."""
    if not isinstance(result, dict):
        return ""
    return "\n".join(_text_items(result))


def parse_tool_result(result: Optional[dict]) -> Any:
    """Turn a ``tools/call`` result into data.

    ``structuredContent`` wins when present. Otherwise the unwrapped text: parsed as JSON
    when the whole text is JSON, or when every text item is JSON (a list of them is
    returned), or when the text is a plain-text notice followed by one JSON document that
    runs to its end (some servers put a content-safety notice before the data); else the
    text itself. ``None`` for an empty result.
    """
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    items = _text_items(result)
    if not items:
        return None
    joined = "\n".join(items)
    try:
        return json.loads(joined)
    except ValueError:
        pass
    if len(items) > 1:
        parsed = []
        for item in items:
            try:
                parsed.append(json.loads(item))
            except ValueError:
                break
        else:
            return parsed
    trailing = _trailing_json(joined)
    return joined if trailing is _NOT_FOUND else trailing


_NOT_FOUND = object()


def _trailing_json(text: str) -> Any:
    """The JSON document that ends the text after a plain-text notice, or ``_NOT_FOUND``.
    Only a document that runs to the end of the text counts (trailing whitespace aside), so
    a brace inside the notice never passes for the data."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            value, end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if not text[end:].strip():
            return value
    return _NOT_FOUND


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class _Connection:
    """One running server process and everything that belongs to it: the requests sent
    to it, its reader tasks and its stderr tail. A process that is stopped takes only its
    own requests and readers with it, never those of a newer process."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.pending: dict[int, asyncio.Future] = {}
        self.reader: Optional[asyncio.Task] = None
        self.stderr_reader: Optional[asyncio.Task] = None
        self.stderr_tail: deque[bytes] = deque()
        self.stderr_size = 0

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    def stderr_text(self) -> str:
        return b"".join(self.stderr_tail).decode("utf-8", "replace")


class McpStdioClient:
    """One stdio MCP server, started on first use and reused until ``close()``.

    Calls may run concurrently: each request has its own id and waits for its own answer.
    """

    def __init__(
        self,
        command: str = "",
        args: Optional[list[str] | tuple[str, ...]] = None,
        env: Optional[dict[str, str]] = None,
        *,
        name: str = "mcp",
        client_name: str = "swarmai",
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        init_timeout: float = DEFAULT_INIT_TIMEOUT,
        rejected_tools: frozenset[str] = frozenset(),
    ) -> None:
        self._command: str = command or ""
        self._args: list = list(args or [])
        self._env: dict = dict(env or {})
        self._name = name
        self._client_name = client_name
        self._call_timeout = call_timeout
        self._init_timeout = init_timeout
        self._rejected_tools = frozenset(rejected_tools)
        self._conn: Optional[_Connection] = None
        self._initialized = False
        self._request_id = 0
        self._lock = asyncio.Lock()  # serialises start-up and close()
        self._write_lock = asyncio.Lock()
        self._last_stderr = ""

    @classmethod
    def from_server_id(cls, server_id: str, workspace: Optional[Path] = None, **kwargs: Any) -> "McpStdioClient":
        """A client for the server configured under exactly ``server_id`` (``resolve_server``)."""
        return cls.from_spec(resolve_server(server_id, workspace), **kwargs)

    @classmethod
    def from_spec(cls, spec: McpServerSpec, **kwargs: Any) -> "McpStdioClient":
        kwargs.setdefault("name", spec.id)
        return cls(spec.command, list(spec.args), dict(spec.env), rejected_tools=spec.rejected_tools, **kwargs)

    # -- introspection -----------------------------------------------------

    @property
    def available(self) -> bool:
        """True when a command is configured (it may still fail to start)."""
        return bool(self._command)

    @property
    def env(self) -> dict[str, str]:
        """The server's configured environment (laid over the daemon's at spawn)."""
        return dict(self._env)

    @property
    def _process(self) -> Optional[asyncio.subprocess.Process]:
        conn = getattr(self, "_conn", None)
        return conn.proc if conn is not None else None

    @_process.setter
    def _process(self, value: None) -> None:
        if value is not None:
            raise AttributeError("the process is set by start()")
        self._conn = None

    @property
    def running(self) -> bool:
        conn = self._conn
        return conn is not None and conn.alive

    @property
    def stderr_tail(self) -> str:
        """The last few KiB the server wrote to stderr (the current server, else the last
        one that stopped), for explaining a failure."""
        conn = self._conn
        return conn.stderr_text() if conn is not None else self._last_stderr

    # -- requests ----------------------------------------------------------

    def _build_request(self, tool_name: str, arguments: dict) -> dict:
        """Build a JSON-RPC 2.0 ``tools/call`` request with the next id."""
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

    def _next_request(self, method: str, params: dict) -> dict:
        self._request_id += 1
        return {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}

    async def call_tool_raw(self, tool_name: str, arguments: dict, *, timeout: Optional[float] = None) -> dict:
        """Call a tool and return its ``result`` as sent, ``isError`` results included."""
        if tool_name in self._rejected_tools:
            raise McpToolRefused(f"tool {tool_name!r} is turned off for MCP server {self._name!r}")
        conn = await self.start()
        request = self._build_request(tool_name, dict(arguments or {}))
        response = await self._roundtrip(conn, request, timeout if timeout is not None else self._call_timeout)
        if "error" in response:
            raise McpToolError(_error_text(response["error"]), None)
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(f"{self._name}: tools/call answered without a result object")
        return result

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: Optional[float] = None) -> dict:
        """Call a tool; raise ``McpToolError`` when the server reports an error."""
        result = await self.call_tool_raw(tool_name, arguments, timeout=timeout)
        if result.get("isError"):
            raise McpToolError(result_text(result) or f"{tool_name} failed", result)
        return result

    async def list_tools(self, *, timeout: Optional[float] = None) -> list[dict]:
        """``tools/list``: every tool the server offers (all pages)."""
        conn = await self.start()
        tools: list[dict] = []
        cursor: Optional[str] = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            response = await self._roundtrip(
                conn, self._next_request("tools/list", params), timeout if timeout is not None else self._call_timeout
            )
            if "error" in response:
                raise McpToolError(_error_text(response["error"]))
            result = response.get("result") or {}
            tools.extend(t for t in result.get("tools") or () if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> "_Connection":
        """Start the server and perform the handshake, unless it is already running.
        Returns the connection the caller's requests go to."""
        async with self._lock:
            conn = self._conn
            if conn is not None and conn.alive and self._initialized:
                return conn
            if conn is not None:
                self._detach(conn)
                await self._stop(conn)
            conn = await self._spawn()
            try:
                response = await self._roundtrip(
                    conn,
                    self._next_request(
                        "initialize",
                        {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": self._client_name, "version": "1.0"},
                        },
                    ),
                    self._init_timeout,
                )
                if "result" not in response:
                    raise McpProtocolError(
                        f"{self._name}: initialize failed: {_error_text(response.get('error'))}"
                    )
                await self._send(conn, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            except McpError:
                self._detach(conn)
                await self._stop(conn)
                raise
            self._initialized = True
            logger.info("MCP server %s initialized (pid=%s)", self._name, conn.proc.pid)
            return conn

    async def close(self) -> None:
        """Terminate the server. The client can be used again afterwards."""
        async with self._lock:
            conn = self._conn
            if conn is not None:
                self._detach(conn)
                await self._stop(conn)

    async def __aenter__(self) -> "McpStdioClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- internals ---------------------------------------------------------

    def _resolve_command(self) -> str:
        command = self._command
        if not command:
            raise McpUnavailable(f"{self._name}: no command configured")
        if os.path.isabs(command) or os.sep in command or (os.altsep and os.altsep in command):
            if not Path(command).is_file():
                raise McpUnavailable(f"{self._name}: command not found: {command}")
            return command
        path = self._env.get("PATH") or os.environ.get("PATH")
        found = shutil.which(command, path=path)
        if not found:
            raise McpUnavailable(f"{self._name}: command not found on PATH: {command}")
        return found

    async def _spawn(self) -> _Connection:
        command = self._resolve_command()
        env = {**os.environ, **self._env}
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            # Its own process group, so stopping it also stops what it started (npx -> node).
            kwargs["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=LINE_LIMIT,
                **kwargs,
            )
        except OSError as exc:
            raise McpUnavailable(f"{self._name}: could not start the server: {exc}") from exc
        conn = _Connection(proc)
        conn.reader = asyncio.create_task(self._read_stdout(conn))
        conn.stderr_reader = asyncio.create_task(self._read_stderr(conn))
        self._conn = conn
        self._initialized = False
        return conn

    def _detach(self, conn: _Connection) -> None:
        """Forget ``conn`` if it is still the current connection (no await: atomic)."""
        if self._conn is conn:
            self._conn = None
            self._initialized = False

    async def _send(self, conn: _Connection, message: dict) -> None:
        proc = conn.proc
        if not conn.alive or proc.stdin is None:
            raise McpProtocolError(f"{self._name}: server is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                raise McpProtocolError(f"{self._name}: server closed its input: {exc}") from exc

    async def _roundtrip(self, conn: _Connection, request: dict, timeout: float) -> dict:
        request_id = request["id"]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending[request_id] = future
        try:
            await self._send(conn, request)
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=max(timeout, 0.001))
            except asyncio.TimeoutError:
                logger.warning("MCP server %s: %s timed out after %.1fs", self._name, request.get("method"), timeout)
                conn.pending.pop(request_id, None)
                future.cancel()
                # A hung server is never reused: stop this process (only this one); the
                # next call starts a fresh server.
                self._detach(conn)
                await self._stop(conn)
                raise McpTimeout(f"{self._name}: no answer to {request.get('method')} within {timeout:.1f}s") from None
        finally:
            conn.pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read_stdout(self, conn: _Connection) -> None:
        reason = "server exited"
        try:
            assert conn.proc.stdout is not None
            while True:
                try:
                    line = await conn.proc.stdout.readline()
                except ValueError:
                    reason = "server sent a line over the size limit"
                    break
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # a log line on stdout: not ours to interpret
                if not isinstance(message, dict) or "id" not in message or "method" in message:
                    continue  # a notification, or a request from the server
                future = conn.pending.get(message.get("id"))
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the reader must end by failing the waiters
            reason = f"reading the server failed: {type(exc).__name__}"
            logger.debug("MCP server %s: %s", self._name, reason)
        self._fail_pending(conn, reason)

    def _fail_pending(self, conn: _Connection, reason: str) -> None:
        for future in list(conn.pending.values()):
            if not future.done():
                future.set_exception(McpProtocolError(f"{self._name}: {reason}"))

    async def _read_stderr(self, conn: _Connection) -> None:
        try:
            assert conn.proc.stderr is not None
            while True:
                chunk = await conn.proc.stderr.read(4096)
                if not chunk:
                    return
                conn.stderr_tail.append(chunk)
                conn.stderr_size += len(chunk)
                while conn.stderr_size > _STDERR_TAIL_BYTES and len(conn.stderr_tail) > 1:
                    conn.stderr_size -= len(conn.stderr_tail.popleft())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - stderr is diagnostics only
            logger.debug("MCP server %s: stopped reading stderr: %s", self._name, type(exc).__name__)
            return

    async def _stop(self, conn: _Connection) -> None:
        """Stop one connection's process and its readers; touches nothing else."""
        self._fail_pending(conn, "client closed")
        proc = conn.proc
        if proc.returncode is None:
            _signal(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_CLOSE_GRACE_S)
            except Exception as exc:  # noqa: BLE001 - escalate to a kill on any failure to stop
                logger.debug("MCP server %s did not stop on SIGTERM (%s); killing it", self._name, type(exc).__name__)
                _signal(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_CLOSE_GRACE_S)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("MCP server %s (pid %s) did not exit after a kill: %s", self._name, proc.pid, type(exc2).__name__)
        for task in (conn.reader, conn.stderr_reader):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - a reader that failed while stopping
                    logger.debug("MCP server %s: reader ended with %s", self._name, type(exc).__name__)
        self._last_stderr = conn.stderr_text()
        # Close the pipes now, on the loop that owns them, instead of at garbage
        # collection (possibly after that loop is gone).
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()


def _signal(proc: asyncio.subprocess.Process, sig: int) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        if sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        data = error.get("data")
        if isinstance(data, str) and data:
            message = f"{message}: {data}" if message else data
        return message or json.dumps(error, ensure_ascii=False)[:500]
    return str(error) if error is not None else "no result"
