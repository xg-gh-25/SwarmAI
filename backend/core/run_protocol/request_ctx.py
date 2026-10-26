"""Who is running the pipeline command in this process, and its input (S6-3).

A pipeline command runs in the agent's own process (design usable-by-anyone §3.3.5:
no daemon execution, no token). ``cli_main`` builds one ``Request``
from its environment and stdin and runs the command inside ``active``; tests and
in-daemon callers do the same with the values they mean.

- ``Request`` — the caller's app session id (``SWARM_APP_SESSION_ID``: the session
  injects it, run_owner_gate hands it to an adopted prewarm subprocess, and
  session_link_gate refuses any other assignment), the CLI's SDK transcript ids, and
  its stdin for ``--artifact -``, and where the session id came from
  (``session_source``, standalone T6).
- ``active(req)`` — context manager that makes *req* current in this thread.
- ``current()`` — the current request or None.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class Request:
    session_id: str
    transcript_ids: tuple[str, ...] = ()
    stdin: Optional[str] = None
    # where session_id came from (standalone T6): ``host`` = SWARM_APP_SESSION_ID, which
    # only a host hook sets (a SwarmAI session, an installed adapter);
    # ``host_transcript`` = Claude Code's CLAUDE_CODE_SESSION_ID, no hook; ``""`` = none
    session_source: str = "host"


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


__all__ = ["Request", "active", "current"]
