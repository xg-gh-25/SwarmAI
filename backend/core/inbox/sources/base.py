"""The Inbox source contract and registry.

Design: ``docs/design/2026-11-20-inbox.md`` §4. A source pulls the person's own messages
from one channel and, for conversation sources, replies into a thread. Each concrete source
lives in its own module under ``core/inbox/sources/`` and registers a factory here; the
runner (``core/inbox/runner.py``) and the send path (``core/inbox/send.py``) only ever talk
to this protocol.

Rules every source keeps:

- **Identity comes from the source** (``whoami``), never from configuration or a constant.
- **Pull reads only.** ``READ_TOOLS`` lists every tool or API call ``pull``/``thread``/
  ``find_sent`` may use; anything else is refused by the source itself and pinned by a test.
- **Reply goes to the given thread only.** ``reply`` takes no recipient argument: the
  recipients are whatever ``thread(ref).target`` resolves to.
- **Failures are typed.** Expired credentials raise ``SourceAuthError`` (the runner marks
  the source ``auth_failed`` and Needs You shows the fix); a send the source knows did not
  happen raises ``SendFailed``; any other exception during ``reply`` means the outcome is
  unknown, and the send path records it as ``unknown`` and never resends automatically.
  A ``SendFailed`` caused by expired credentials carries their ``fix``: the send path then
  marks the source ``auth_failed`` with that fix, as a pull would.

Optional members (a source without them works; callers look them up with ``getattr``):

- ``status_detail() -> str``: why the source cannot send (a GitHub token that cannot read
  notifications, mail writes turned off), valid after ``can_send``; shown in the channel row.
- ``own_messages_to(thread, limit) -> list[Message]``: the person's own recent messages to
  the one person a reply to ``thread`` reaches (a Slack 1:1 DM: its history; mail whose
  reply goes only to its sender: the mail sent to that address), oldest first; none when
  a reply would reach anyone else (a channel thread, a Reply-To that differs). Drafting
  uses them for the person's voice; they hold only text the person wrote, and the caller
  drops any message by someone else again.
- ``SECRET_NAMES``: the secrets the backend reads from ``sources/credentials.py`` under
  ``<source id>:<name>`` (``user_token`` for the Slack Web API, ``password`` for IMAP).

Key public symbols: ``Source``, ``SourceConfig``, ``SourceAuthError``, ``SendFailed``,
``SourceNotFound``, ``register_source``, ``create_source``, ``registered_kinds``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ClassVar, Optional, Protocol, runtime_checkable

from core.inbox.models import (
    Identity,
    PullResult,
    SendReceipt,
    SourceCategory,
    Thread,
    ThreadRef,
)


class SourceAuthError(Exception):
    """Credentials are missing or expired. ``fix`` is the action shown to the person
    (e.g. "run mwinit -f", "gh auth login")."""

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


class SendFailed(Exception):
    """The source is sure the reply was not sent. ``fix`` is set when the cause is expired
    credentials: the action shown to the person, as on ``SourceAuthError``."""

    def __init__(self, message: str = "", fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


class SourceNotFound(LookupError):
    """No source factory is registered for a kind."""


@dataclass(frozen=True)
class SourceConfig:
    """One configured source instance (a row of ``inbox_sources``).

    ``backend`` selects how the kind is reached, e.g. ``mcp:<server id>`` for an MCP server
    the person configured, ``slack-web`` / ``imap`` / ``gh`` / ``feed`` for the built-in
    clients. ``options`` holds backend-specific settings (watched channels, the extra mail
    addresses of the person, a GHES host); secrets are never stored here.
    """

    id: str
    kind: str
    backend: str
    options: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Source(Protocol):
    kind: ClassVar[str]
    category: ClassVar[SourceCategory]
    READ_TOOLS: ClassVar[frozenset[str]]

    async def whoami(self) -> Identity: ...

    async def pull(self, cursor: Optional[str]) -> PullResult: ...

    async def thread(self, ref: ThreadRef) -> Thread: ...

    async def can_send(self) -> bool: ...

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt: ...

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]: ...

    async def close(self) -> None: ...


SourceFactory = Callable[[SourceConfig], Source]
_REGISTRY: dict[str, SourceFactory] = {}


def register_source(kind: str, factory: SourceFactory) -> None:
    """Register the factory for a source kind. A second registration of a kind replaces the
    first (tests use this to install fakes)."""
    _REGISTRY[kind] = factory


def create_source(config: SourceConfig) -> Source:
    try:
        factory = _REGISTRY[config.kind]
    except KeyError as exc:
        raise SourceNotFound(config.kind) from exc
    return factory(config)


def registered_kinds() -> list[str]:
    return sorted(_REGISTRY)
