"""The daemon executes pipeline commands; the agent's CLI only asks (B-class B-a).

Signing happens only in the daemon: ``scripts/pipeline_v1.py`` sends its argv over an
owner-only Unix socket and prints the answer. The request carries the one-time token
the session's PreToolUse hook issued for exactly this command (run_owner_gate); the
daemon redeems it (``hook_token.redeem``), checks its scope and run against the parsed
command (``cli_scope.scope``), and runs the command with the token's session as the
caller (``request_ctx``). Nothing the request says about its session is trusted.

There is no local fallback: with the daemon down, or on a platform without Unix
sockets, a pipeline command fails (XG 2026-10-24: a fallback would undo the control).

- ``start(port)`` / ``stop()`` — the daemon's server (main.py, next to the token oracle).
- ``socket_path(port)`` — ``<app data>/run/pipeline-exec-<port>.sock``.
- ``call(token, argv, *, stdin, transcript_id)`` — the CLI side: (result, exit code).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import stat
import threading
from pathlib import Path
from typing import Any, Optional

from core import hook_token

logger = logging.getLogger(__name__)

_MAX_REQUEST = 16 * 1024 * 1024        # an artifact on stdin
_MAX_REPLY = 64 * 1024 * 1024
_CLIENT_TIMEOUT_S = 1800.0             # a landing (git) can take minutes
_CONCURRENCY = threading.BoundedSemaphore(4)         # CLI commands run at once
_QUEUE_WAIT_S = 30.0                                  # longest a CLI command waits to start
_MAX_CONNECTIONS = 32                                 # requests handled at once
# CLI commands waiting for a slot (the rest of the connections are refused fast)
_CLI_WAITING = threading.BoundedSemaphore(12)
_ADMISSION = threading.BoundedSemaphore(_MAX_CONNECTIONS)
UNAVAILABLE = "pipeline service unavailable"

_server: "socketserver.UnixStreamServer | None" = None
_server_path: "Path | None" = None
_server_port: "int | None" = None


def socket_path(port: int) -> Path:
    oracle = hook_token.socket_path(port)
    return oracle.with_name(oracle.name.replace("hook-token-", "pipeline-exec-")
                            .replace("ht-", "pe-"))


def _execute(req: dict) -> "tuple[dict[str, Any], int]":
    """Take an execution slot BEFORE redeeming the token, and only for a bounded wait:
    a command that cannot start within _QUEUE_WAIT_S answers busy with its token
    unspent, so nothing queued can run after its client gave up (B-e re-audit HIGH)."""
    if not _CLI_WAITING.acquire(blocking=False):
        return _busy()
    try:
        if not _CONCURRENCY.acquire(timeout=_QUEUE_WAIT_S):
            return _busy()
    finally:
        _CLI_WAITING.release()
    try:
        return _execute_admitted(req)
    finally:
        _CONCURRENCY.release()


def _busy() -> "tuple[dict[str, Any], int]":
    return {"ok": False, "code": "busy", "error":
            "pipeline service busy (other commands are running); run the same command "
            "again"}, 2


def _execute_admitted(req: dict) -> "tuple[dict[str, Any], int]":
    from core.run_protocol import cli_commands, cli_scope, request_ctx
    rec = hook_token.redeem(req.get("token"))
    if rec is None:
        return {"ok": False, "code": "no_token", "error":
                "no valid hook token: run the command as ONE Bash call so the session's "
                "hook issues its token (it is single use and expires in "
                f"{int(hook_token.TOKEN_TTL_SECONDS)}s)"}, 2
    argv = req.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return {"ok": False, "error": "malformed pipeline request"}, 2
    try:
        args = cli_commands.parser().parse_args(argv)
    except SystemExit:
        return {"ok": False, "error": "invalid arguments: " + " ".join(argv)[:300]}, 2
    run_id = getattr(args, "run_id", None) if args.command != "create" else None
    want = cli_scope.scope(args.command, getattr(args, "request", None))
    if rec.get("subcommand") != want or rec.get("run_id") != run_id:
        return {"ok": False, "code": "token_scope",
                "error": "hook token was issued for a different command"}, 2
    session = rec.get("session_id")
    if not isinstance(session, str) or not session:
        return {"ok": False, "error": "hook token names no session"}, 2
    transcript = req.get("transcript_id")
    stdin = req.get("stdin")
    ctx = request_ctx.Request(
        session_id=session, run_id=run_id, scope=want,
        transcript_ids=(transcript,) if isinstance(transcript, str) and transcript else (),
        stdin=stdin if isinstance(stdin, str) else None)
    with request_ctx.active(ctx):
        return cli_commands.run(args)


class _Handler(socketserver.StreamRequestHandler):
    timeout = 30.0                     # reading the request; the work itself may be long

    def handle(self) -> None:
        try:
            line = self.rfile.readline(_MAX_REQUEST + 1)
            if len(line) > _MAX_REQUEST:
                raise ValueError("request too large")
            req = json.loads(line.decode("utf-8"))
            if not isinstance(req, dict) or req.get("op") != "cli":
                raise ValueError("bad request")
            out, rc = _execute(req)
            reply = {"rc": rc, "out": out}
        except (OSError, ValueError, UnicodeDecodeError):
            reply = {"ok": False, "rc": 2,
                     "out": {"ok": False, "error": "malformed pipeline request"},
                     "error": "malformed pipeline request"}
        except Exception as exc:  # noqa: BLE001 — never kill the server thread
            logger.exception("pipeline request failed")
            err = f"{type(exc).__name__}: {exc}"[:500]
            reply = {"ok": False, "rc": 2, "error": err,
                     "out": {"ok": False, "code": "daemon_error", "error": err}}
        try:
            self.wfile.write((json.dumps(reply, ensure_ascii=False,
                                         separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Admission control (B round 1 MED): at most _MAX_CONNECTIONS requests are
    handled at once; a connection past that is answered "busy" and closed at once,
    so slow or numerous clients cannot pile up threads in the daemon."""
    daemon_threads = True

    def process_request(self, request, client_address):
        if not _ADMISSION.acquire(blocking=False):
            try:
                request.sendall((json.dumps({"ok": False, "rc": 2, "error": "busy",
                                             "out": {"ok": False, "code": "busy",
                                                     "error": "pipeline service busy; retry"}})
                                 + "\n").encode("utf-8"))
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            _ADMISSION.release()      # the worker never started: its slot is free again
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            _ADMISSION.release()


def start(port: int) -> bool:
    """Serve pipeline commands for the backend on *port*. Idempotent; False when the
    platform has no Unix sockets or the socket cannot be made private (pipeline
    commands then fail)."""
    global _server, _server_path, _server_port
    if not hook_token.oracle_supported():
        return False
    if _server is not None:
        return True
    path = socket_path(port)
    if not hook_token._private_dir(path.parent, create=True):
        logger.warning("pipeline service: %s is not a private directory", path.parent)
        return False
    try:
        st = os.lstat(path)
        if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid():
            os.unlink(path)
        else:
            logger.warning("pipeline service: %s exists and is not our socket", path)
            return False
    except FileNotFoundError:
        pass
    except OSError:
        return False
    old = os.umask(0o177)
    try:
        srv = _Server(str(path), _Handler)
    except OSError as exc:
        logger.warning("pipeline service: bind %s failed: %s", path, exc)
        return False
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    threading.Thread(target=srv.serve_forever, name="pipeline-exec", daemon=True).start()
    _server, _server_path, _server_port = srv, path, int(port)
    return True


def stop() -> None:
    global _server, _server_path, _server_port
    srv, path = _server, _server_path
    _server = _server_path = _server_port = None
    if srv is None:
        return
    srv.shutdown()
    srv.server_close()
    try:
        if path is not None and stat.S_ISSOCK(os.lstat(path).st_mode):
            os.unlink(path)
    except OSError:
        pass


def request(obj: dict, *, token: Optional[str]) -> dict:
    """THE CLIENT SIDE: send one request to the daemon named by *token* (a hook token,
    ``<port>.<64 hex>``); returns the reply object."""
    import re
    m = re.fullmatch(r"(\d{1,5})\.[0-9a-f]{64}", token or "")
    if not m:
        return {"ok": False, "code": "no_token", "error":
                "no hook token: pipeline commands must be run as ONE Bash call from a "
                "SwarmAI session (the session's hook issues the token)"}
    path = socket_path(int(m.group(1)))
    try:
        st = os.lstat(path)
    except OSError:
        return {"ok": False, "code": "daemon_down", "error":
                UNAVAILABLE + ": the SwarmAI backend is not running; tell the user"}
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() \
            or not hook_token._private_dir(path.parent, create=False):
        return {"ok": False, "error": "pipeline socket is not private to this user"}
    req = json.dumps(obj, ensure_ascii=False) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(_CLIENT_TIMEOUT_S)
            s.connect(str(path))
            s.sendall(req.encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < _MAX_REPLY:
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
        rep = json.loads(buf.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "code": "daemon_down",
                "error": f"{UNAVAILABLE}: {type(exc).__name__}"}
    return rep if isinstance(rep, dict) else {"ok": False, "error": "malformed reply"}


def call(token: Optional[str], argv: list[str], *, stdin: Optional[str] = None,
         transcript_id: Optional[str] = None) -> "tuple[dict[str, Any], int]":
    """THE CLI SIDE: one pipeline_v1 command; (result, exit code)."""
    rep = request({"op": "cli", "token": token, "argv": argv, "stdin": stdin,
                   "transcript_id": transcript_id}, token=token)
    if not isinstance(rep.get("out"), dict):
        return ({k: v for k, v in rep.items() if k != "rc"} if rep.get("ok") is False
                else {"ok": False, "error": "malformed pipeline reply"}), 2
    return rep["out"], int(rep.get("rc") or 0)


__all__ = ["UNAVAILABLE", "call", "request", "socket_path", "start", "stop"]
