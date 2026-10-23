"""The pipeline request the daemon is executing right now (B-class design B-a/B-b).

A pipeline command is executed in the daemon, never in the agent's process. The
daemon learns who sent it from the one-time hook token it redeemed for it, never
from the request: this module carries those facts to the engine for the duration
of one request.

- ``Request`` — session id (the token's), run id and scope (the token's), the CLI
  process's transcript id, and its stdin (for ``--artifact -``).
- ``active(req)`` — context manager that makes *req* current in this thread.
- ``current()`` — the current request or None (in-daemon callers: hooks, the
  dashboard, tests playing the daemon).
- ``take_scope(scope, run_id)`` — the engine's proof that a hook-issued token
  covers this exact action; usable once per request.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class Request:
    session_id: str
    run_id: Optional[str]
    scope: str
    transcript_ids: tuple[str, ...] = ()
    stdin: Optional[str] = None
    _taken: set = field(default_factory=set)


_CURRENT: "contextvars.ContextVar[Optional[Request]]" = contextvars.ContextVar(
    "pipeline_request", default=None)


@contextmanager
def active(req: Request) -> Iterator[Request]:
    token = _CURRENT.set(req)
    try:
        yield req
    finally:
        _CURRENT.reset(token)


def current() -> Optional[Request]:
    return _CURRENT.get()


def take_scope(scope: str, run_id: str) -> "tuple[bool, str]":
    """Whether the hook token the daemon redeemed for this request covers *scope* on
    *run_id*; each scope is taken at most once per request."""
    req = _CURRENT.get()
    if req is None:
        return False, "no hook token (the command did not pass through the session's hook)"
    if req.run_id != run_id or req.scope != scope:
        return False, "hook token was issued for a different command"
    if scope in req._taken:
        return False, "unknown or already used hook token"
    req._taken.add(scope)
    return True, ""


__all__ = ["Request", "active", "current", "take_scope"]
