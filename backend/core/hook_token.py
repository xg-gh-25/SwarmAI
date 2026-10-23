"""hook_token — one-time proof that the PreToolUse hook really ran (worktree §4.2).
An environment variable cannot prove a hooked session: a `claude --print --bare`
child started by the model inherits every variable. So the daemon's PreToolUse
hook issues a token for exactly ONE artifact_cli command that changes worktree
state, and the CLI consumes it.

Where the issuance record lives decides whether a token can be forged:
- **Oracle mode** (macOS / Linux): the record is kept ONLY in the daemon's memory.
  The CLI (a separate process) burns the token through the daemon's Unix-domain
  socket ``<app data>/run/hook-token-<port>.sock`` (owner-only file in an
  owner-only directory, never a network listener). Writing a file can no longer
  mint a token; the CLI never falls back to files on these platforms, and an
  unreachable daemon means "no token" (fail closed).
- **File mode** (no AF_UNIX: Windows): the record is a file holding only the hash,
  ``runs/<id>/.hook-tokens/<sha256(nonce)>.json`` (the pre-oracle behavior; a
  deliberate process can forge it — §7.3 threat model).

Key public symbols:
- ``issue(run_dir, session_id, run_id, subcommand)`` → token or None (oracle mode
  without a running oracle). Oracle tokens are ``<port>.<64 hex>``.
- ``consume(run_dir, token, session_id, run_id, subcommand)`` → (ok, reason). Single
  use, TTL ``TOKEN_TTL_SECONDS``.
- ``start_oracle(port)`` / ``stop_oracle()`` — the daemon's socket server (main.py).
- ``purge_expired(run_dir)`` — drop expired token files (file mode; sweep).
- ``TOKEN_ENV`` — the variable the hook prefixes onto the command.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import stat
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
TOKEN_ENV = "SWARM_HOOK_TOKEN"
TOKEN_TTL_SECONDS = 120.0
TOKEN_DIR = ".hook-tokens"
_NONCE_RE = re.compile(r"[0-9a-f]{64}")
_ORACLE_TOKEN_RE = re.compile(r"(\d{1,5})\.([0-9a-f]{64})")
_CONNECT_TIMEOUT_S = 3.0
_MAX_REQUEST = 4096
_SUN_PATH_MAX = 100          # macOS sun_path is 104 bytes, Linux 108


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


def oracle_supported() -> bool:
    return hasattr(socket, "AF_UNIX") and os.name != "nt"


def _data_dir() -> Path:
    try:
        from config import get_app_data_dir
        return Path(os.path.realpath(str(get_app_data_dir())))
    except Exception as exc:  # noqa: BLE001 — standalone use without the backend config
        logger.debug("app data dir unavailable (%s); using ~/.swarm-ai", exc)
        return Path(os.path.realpath(os.path.expanduser("~"))) / ".swarm-ai"


def _private_dir(d: Path, create: bool) -> bool:
    """*d* is a real directory (no symlink) owned by this user with no group/other
    access. With *create*, make it (0700) or tighten one we own."""
    if create:
        try:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            st = os.lstat(d)
            if stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and st.st_mode & 0o077:
                os.chmod(d, 0o700)
        except OSError:
            return False
    try:
        st = os.lstat(d)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


def socket_path(port: int) -> Path:
    """The oracle socket for the backend listening on *port* (dev and daemon can run
    side by side on one data dir). Falls back to a per-user temp dir when the data dir
    path is too long for ``sun_path`` (deep test temp dirs)."""
    data = _data_dir()
    p = data / "run" / f"hook-token-{int(port)}.sock"
    if len(str(p).encode()) <= _SUN_PATH_MAX:
        return p
    short = hashlib.sha256(str(data).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"swarm-{os.getuid()}" / f"ht-{short}-{int(port)}.sock"


# ── the daemon side: issuance records in memory, served on a Unix socket ─────
_MEM: "dict[str, dict]" = {}
_MEM_LOCK = threading.Lock()
_server: "socketserver.UnixStreamServer | None" = None
_server_port: "int | None" = None
_server_path: "Path | None" = None


def _purge_mem(now: float) -> None:
    for k in [k for k, r in _MEM.items() if now - r["issued_at"] > TOKEN_TTL_SECONDS]:
        _MEM.pop(k, None)


def _verdict(rec: "dict | None", session_id, run_id, subcommand, now: float) -> "tuple[bool, str]":
    if not isinstance(rec, dict):
        return False, "unknown or already used hook token"
    if now - float(rec.get("issued_at") or 0) > TOKEN_TTL_SECONDS:
        return False, "hook token expired"
    if rec.get("run_id") != run_id or rec.get("subcommand") != subcommand:
        return False, "hook token was issued for a different command"
    if not session_id or rec.get("session_id") != session_id:
        return False, "hook token belongs to another session"
    return True, ""


def _consume_mem(digest: str, session_id, run_id, subcommand) -> "tuple[bool, str]":
    now = time.time()
    with _MEM_LOCK:
        rec = _MEM.pop(digest, None)   # single use, whatever the verdict
        _purge_mem(now)
    return _verdict(rec, session_id, run_id, subcommand, now)


class _Handler(socketserver.StreamRequestHandler):
    timeout = _CONNECT_TIMEOUT_S

    def handle(self) -> None:
        try:
            line = self.rfile.readline(_MAX_REQUEST)
            req = json.loads(line.decode("utf-8"))
            if not isinstance(req, dict) or not _NONCE_RE.fullmatch(str(req.get("digest"))):
                raise ValueError("bad request")
            ok, why = _consume_mem(req["digest"], req.get("session_id"),
                                   req.get("run_id"), req.get("subcommand"))
        except (OSError, ValueError, UnicodeDecodeError):
            ok, why = False, "malformed hook-token request"
        try:
            self.wfile.write((json.dumps({"ok": ok, "reason": why}) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def start_oracle(port: int) -> bool:
    """Serve token consumption for the backend on *port*. Idempotent; False when the
    platform has no AF_UNIX or the socket cannot be made private (tokens are then not
    issued — isolated runs refuse instead of running unproven)."""
    global _server, _server_port, _server_path
    if not oracle_supported():
        return False
    if _server is not None:
        return _server_port == int(port)
    path = socket_path(port)
    if not _private_dir(path.parent, create=True):
        logger.warning("hook-token oracle: %s is not a private directory", path.parent)
        return False
    try:
        st = os.lstat(path)
        if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid():
            os.unlink(path)          # a stale socket of a previous run on this port
        else:
            logger.warning("hook-token oracle: %s exists and is not our socket", path)
            return False
    except FileNotFoundError:
        pass
    except OSError:
        return False
    old = os.umask(0o177)
    try:
        srv = _Server(str(path), _Handler)
    except OSError as exc:
        logger.warning("hook-token oracle: bind %s failed: %s", path, exc)
        return False
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    threading.Thread(target=srv.serve_forever, name="hook-token-oracle", daemon=True).start()
    _server, _server_port, _server_path = srv, int(port), path
    return True


def stop_oracle() -> None:
    global _server, _server_port, _server_path
    srv, path = _server, _server_path
    _server = _server_port = _server_path = None
    if srv is None:
        return
    srv.shutdown()
    srv.server_close()
    try:
        if path is not None and stat.S_ISSOCK(os.lstat(path).st_mode):
            os.unlink(path)
    except OSError:
        pass
    with _MEM_LOCK:
        _MEM.clear()


def issue(run_dir: "str | Path", session_id: str, run_id: str, subcommand: str,
          now: "float | None" = None) -> "str | None":
    """Create a token for ONE command; the nonce itself is never stored."""
    nonce = secrets.token_hex(32)
    rec = {"sha256": _digest(nonce), "session_id": session_id, "run_id": run_id,
           "subcommand": subcommand, "issued_at": time.time() if now is None else now}
    if oracle_supported():
        if _server is None:
            return None
        with _MEM_LOCK:
            _purge_mem(time.time())
            _MEM[rec["sha256"]] = rec
        return f"{_server_port}.{nonce}"
    d = Path(run_dir) / TOKEN_DIR
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{rec['sha256']}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, d / f"{rec['sha256']}.json")
    return nonce


UNREACHABLE = "hook-token service unreachable"


def _ask_oracle(port: int, digest: str, session_id, run_id, subcommand) -> "tuple[bool, str]":
    path = socket_path(port)
    try:
        st = os.lstat(path)
    except OSError:
        return False, UNREACHABLE + " (the issuing SwarmAI backend is not running)"
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() \
            or not _private_dir(path.parent, create=False):
        return False, "hook-token socket is not private to this user — refused"
    req = json.dumps({"digest": digest, "session_id": session_id, "run_id": run_id,
                      "subcommand": subcommand}) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(_CONNECT_TIMEOUT_S)
            s.connect(str(path))
            s.sendall(req.encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < _MAX_REQUEST:
                chunk = s.recv(_MAX_REQUEST)
                if not chunk:
                    break
                buf += chunk
        rep = json.loads(buf.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False, UNREACHABLE + " (the issuing SwarmAI backend is not running)"
    if not isinstance(rep, dict):
        return False, "malformed hook-token reply"
    return bool(rep.get("ok") is True), str(rep.get("reason") or "")


def consume(run_dir: "str | Path", token: "str | None", session_id: "str | None",
            run_id: str, subcommand: str, now: "float | None" = None) -> "tuple[bool, str]":
    """Verify and burn the token (single use, whatever the verdict)."""
    if not token:
        return False, "no hook token (the command did not pass through the session's hook)"
    if oracle_supported():
        m = _ORACLE_TOKEN_RE.fullmatch(token)
        if not m:
            return False, "malformed hook token"
        return _ask_oracle(int(m.group(1)), _digest(m.group(2)), session_id, run_id, subcommand)
    if not _NONCE_RE.fullmatch(token):
        return False, "malformed hook token"
    f = Path(run_dir) / TOKEN_DIR / f"{_digest(token)}.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "unknown or already used hook token"
    try:
        f.unlink()
    except OSError:
        return False, "hook token could not be burned"
    return _verdict(rec, session_id, run_id, subcommand, time.time() if now is None else now)


def purge_expired(run_dir: "str | Path", now: "float | None" = None) -> int:
    """Delete expired token files (file mode); returns how many."""
    d = Path(run_dir) / TOKEN_DIR
    if not d.is_dir():
        return 0
    now = time.time() if now is None else now
    n = 0
    for f in d.iterdir():
        try:
            if now - f.stat().st_mtime > TOKEN_TTL_SECONDS:
                f.unlink()
                n += 1
        except OSError:
            continue
    return n
