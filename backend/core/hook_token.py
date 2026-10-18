"""hook_token — one-time proof that the PreToolUse hook really ran (worktree §4.2).

An environment variable cannot prove a hooked session: a `claude --print --bare`
child started by the model inherits every variable. So the daemon's PreToolUse
hook issues a token for exactly ONE artifact_cli command that changes worktree
state, and the CLI consumes it under the run lock.

- ``issue(run_dir, session_id, run_id, subcommand)`` → nonce. Writes
  ``runs/<id>/.hook-tokens/<sha256(nonce)>.json`` holding only the HASH, the
  session, run, subcommand and issue time — reading the file never yields the nonce.
- ``consume(run_dir, nonce, session_id, run_id, subcommand)`` → (ok, reason). Single
  use (the file is deleted on any verdict about it), TTL ``TOKEN_TTL_SECONDS``.
- ``purge_expired(run_dir)``: drop expired token files (sweep).
- ``TOKEN_ENV``: the variable the hook prefixes onto the command.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

TOKEN_ENV = "SWARM_HOOK_TOKEN"
TOKEN_TTL_SECONDS = 120.0
TOKEN_DIR = ".hook-tokens"
_NONCE_RE = re.compile(r"[0-9a-f]{64}")


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


def issue(run_dir: "str | Path", session_id: str, run_id: str, subcommand: str,
          now: "float | None" = None) -> str:
    """Create a token for ONE command; returns the nonce (never stored)."""
    nonce = secrets.token_hex(32)
    d = Path(run_dir) / TOKEN_DIR
    d.mkdir(parents=True, exist_ok=True)
    rec = {"sha256": _digest(nonce), "session_id": session_id, "run_id": run_id,
           "subcommand": subcommand, "issued_at": time.time() if now is None else now}
    tmp = d / f".{rec['sha256']}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, d / f"{rec['sha256']}.json")
    return nonce


def consume(run_dir: "str | Path", nonce: "str | None", session_id: "str | None",
            run_id: str, subcommand: str, now: "float | None" = None) -> "tuple[bool, str]":
    """Verify and burn the token. The caller holds the run lock."""
    if not nonce:
        return False, "no hook token (the command did not pass through the session's hook)"
    if not _NONCE_RE.fullmatch(nonce):
        return False, "malformed hook token"
    f = Path(run_dir) / TOKEN_DIR / f"{_digest(nonce)}.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "unknown or already used hook token"
    try:
        f.unlink()  # single use, whatever the verdict below
    except OSError:
        return False, "hook token could not be burned"
    now = time.time() if now is None else now
    if not isinstance(rec, dict):
        return False, "corrupt hook token"
    if now - float(rec.get("issued_at") or 0) > TOKEN_TTL_SECONDS:
        return False, "hook token expired"
    if rec.get("run_id") != run_id or rec.get("subcommand") != subcommand:
        return False, "hook token was issued for a different command"
    if not session_id or rec.get("session_id") != session_id:
        return False, "hook token belongs to another session"
    return True, ""


def purge_expired(run_dir: "str | Path", now: "float | None" = None) -> int:
    """Delete expired token files; returns how many."""
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
