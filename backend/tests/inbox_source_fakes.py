"""Recorded-shape fakes for the Inbox source tests (not a test module).

The real Slack / Outlook MCP servers need corporate auth and are not reachable from CI, so
the tests drive each source against fakes that answer in the shapes those backends use:
MCP tool results as text content (optionally wrapped in ``<untrusted_content>``), Slack Web
API JSON, ``gh api`` stdout/stderr, and imaplib/smtplib call results. Every fake records
each call it receives, so a test can assert exactly what reached the backend.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from core.mcp_stdio_client import McpTimeout, McpToolError


class FakeMcp:
    """Stands in for ``McpStdioClient``: ``handlers[tool](args)`` gives the tool's data."""

    def __init__(self, handlers: dict[str, Any], *, tools: Optional[list[str]] = None, env: Optional[dict] = None,
                 wrap: bool = True) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, dict]] = []
        self.tools = tools if tools is not None else list(handlers)
        self.env = env or {}
        self.stderr_tail = ""
        self.wrap = wrap
        self.closed = False
        self.started = 0

    async def start(self) -> None:
        self.started += 1

    async def call_tool(self, tool: str, args: dict, *, timeout: Optional[float] = None) -> dict:
        self.calls.append((tool, dict(args)))
        handler = self.handlers.get(tool)
        if handler is None:
            raise McpToolError(f"unknown tool {tool}")
        value = handler(args) if callable(handler) else handler
        if isinstance(value, BaseException):
            raise value
        text = json.dumps(value)
        if self.wrap:
            text = f'Results from the server.\n<untrusted_content source="mcp">\n{text}\n</untrusted_content>'
        return {"content": [{"type": "text", "text": text}]}

    async def list_tools(self, *, timeout: Optional[float] = None) -> list[dict]:
        return [{"name": t} for t in self.tools]

    async def close(self) -> None:
        self.closed = True

    def tool_names(self) -> list[str]:
        return [t for t, _ in self.calls]


def mcp_error(text: str) -> McpToolError:
    return McpToolError(text)


def mcp_timeout() -> McpTimeout:
    return McpTimeout("no answer")


class FakeGh:
    """Stands in for running ``gh``: ``routes`` maps (method, path prefix) to a payload, an
    ``(rc, stderr)`` pair, or a callable taking the request body."""

    def __init__(self, routes: list[tuple[str, str, Any]]) -> None:
        self.routes = routes
        self.calls: list[dict] = []

    async def __call__(self, argv: list[str], stdin: Optional[bytes], timeout: float) -> tuple[int, bytes, bytes]:
        assert argv[:2] == ["gh", "api"]
        method = argv[argv.index("-X") + 1]
        path = argv[argv.index("-H") + 2]
        body = json.loads(stdin) if stdin else None
        host = argv[argv.index("--hostname") + 1] if "--hostname" in argv else ""
        self.calls.append({"method": method, "path": path, "body": body, "host": host, "argv": argv})
        for m, prefix, payload in self.routes:
            if m == method and path.startswith(prefix):
                if callable(payload):
                    payload = payload(body)
                if isinstance(payload, tuple):
                    rc, err = payload
                    return rc, b"", err.encode()
                return 0, json.dumps(payload).encode(), b""
        return 1, b"", f"gh: Not Found (HTTP 404) {method} {path}".encode()


class FakeImap:
    """Stands in for ``imaplib.IMAP4_SSL``: folders of ``{uid: raw bytes}``."""

    def __init__(self, folders: dict[str, dict[int, bytes]], *, validity: str = "7", accept: str = "pw",
                 flags: Optional[dict[str, str]] = None) -> None:
        self.folders = folders
        self.validity = validity
        self.accept = accept
        self.flags = flags or {}
        self.commands: list[tuple] = []
        self.selected: Optional[str] = None
        self.readonly: Optional[bool] = None
        self.seen_changed = False

    def login(self, user: str, password: str):
        import imaplib

        self.commands.append(("LOGIN", user))
        if password != self.accept:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = False):
        name = mailbox.strip('"')
        self.commands.append(("EXAMINE" if readonly else "SELECT", name))
        self.selected, self.readonly = name, readonly
        if not readonly:
            self.seen_changed = True
        return ("OK", [str(len(self.folders.get(name, {}))).encode()]) if name in self.folders else ("NO", [b"no such folder"])

    def response(self, code: str):
        if code == "UIDVALIDITY":
            return code, [self.validity.encode()]
        if code == "UIDNEXT":
            uids = self.folders.get(self.selected or "", {})
            return code, [str((max(uids) if uids else 0) + 1).encode()]
        return code, [None]

    def uid(self, command: str, *args):
        self.commands.append(("UID " + command, *args))
        box = self.folders.get(self.selected or "", {})
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in self._search(box, args)).encode()]
        if command == "FETCH":
            uids, spec = args
            if "PEEK" not in spec:
                self.seen_changed = True
            data: list = []
            for u in str(uids).split(","):
                if int(u) in box:
                    data.append((f"{u} (UID {u} BODY[] {{{len(box[int(u)])}}}".encode(), box[int(u)]))
                    data.append(b")")
            return "OK", data
        raise AssertionError(f"unexpected UID {command}")

    def _search(self, box: dict[int, bytes], args: tuple) -> list[int]:
        import email
        import email.policy

        if args[0] == "UID":
            low = int(args[1].split(":")[0])
            return [u for u in box if u >= low] or ([max(box)] if box else [])
        if args[0] == "SINCE" and len(args) == 2:
            return sorted(box)
        if "TO" in args:   # SINCE <date> TO "<address>": the To header holds the address
            want = args[args.index("TO") + 1].strip('"').lower()
            return sorted(u for u, raw in box.items()
                          if want in str(email.message_from_bytes(raw, policy=email.policy.default).get("To") or "").lower())
        wanted = [a.strip('"') for a in args if isinstance(a, str) and a.startswith('"')]
        out = []
        for uid, raw in box.items():
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            heads = " ".join(str(msg.get(h) or "") for h in ("Message-ID", "References", "In-Reply-To"))
            if any(w in heads for w in wanted):
                out.append(uid)
        return sorted(out)

    def list(self):
        self.commands.append(("LIST",))
        lines = []
        for name in self.folders:
            flags = self.flags.get(name, "\\HasNoChildren")
            lines.append(f'({flags}) "/" "{name}"'.encode())
        return "OK", lines

    def logout(self):
        self.commands.append(("LOGOUT",))
        return "BYE", [b""]


class FakeSmtp:
    def __init__(self, *, refuse: Optional[Exception] = None) -> None:
        self.sent: list[tuple] = []
        self.refuse = refuse
        self.logged_in = None

    def __call__(self, host: str, port: int, security: str) -> "FakeSmtp":
        self.endpoint = (host, port, security)
        return self

    def login(self, user: str, password: str):
        self.logged_in = user

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if self.refuse is not None:
            raise self.refuse
        self.sent.append((msg, from_addr, list(to_addrs or [])))
        return {}

    def quit(self):
        pass


def rfc822(*, message_id: str, from_: str, to: str, subject: str = "Hi", body: str = "Hello",
           date: str = "Thu, 20 Nov 2026 01:00:00 +0000", cc: str = "", reply_to: str = "",
           references: str = "", in_reply_to: str = "", extra_headers: str = "", calendar: bool = False) -> bytes:
    lines = [f"Message-ID: {message_id}", f"From: {from_}", f"To: {to}", f"Subject: {subject}", f"Date: {date}"]
    if cc:
        lines.append(f"Cc: {cc}")
    if reply_to:
        lines.append(f"Reply-To: {reply_to}")
    if references:
        lines.append(f"References: {references}")
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if extra_headers:
        lines.append(extra_headers)
    if calendar:
        lines += ["MIME-Version: 1.0", 'Content-Type: multipart/alternative; boundary="b"', "", "--b",
                  "Content-Type: text/plain", "", body, "--b", "Content-Type: text/calendar; method=REQUEST", "",
                  "BEGIN:VCALENDAR", "END:VCALENDAR", "--b--", ""]
    else:
        lines += ["Content-Type: text/plain; charset=utf-8", "", body, ""]
    return "\r\n".join(lines).encode()


Handler = Callable[[dict], Any]
