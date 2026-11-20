"""Import this module to register every source kind the product ships.

Each source module registers its factories with ``core.inbox.sources.base.register_source``
when it is imported; this module imports them all, so the runner and the send path need one
import: ``import core.inbox.sources.builtin``. Kinds: ``slack``, ``mail``, ``github`` and
``feed:<type>`` for every fetcher in ``jobs.handlers.signal_fetch.ADAPTER_MAP``.
"""
from __future__ import annotations

from core.inbox.sources import feeds, github, mail, slack  # noqa: F401 - imported to register
from core.inbox.sources.base import registered_kinds


def ensure_registered() -> list[str]:
    """Every registered kind (the imports above did the registering)."""
    return registered_kinds()
