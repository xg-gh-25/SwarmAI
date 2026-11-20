"""Read the feed configuration (``config.yaml`` ``feeds:``) as rows for the Inbox feed settings.

Design: ``docs/design/2026-11-20-inbox.md`` §2 (the feed configuration stays in
``Services/swarm-jobs/config.yaml``; writes go through ``jobs.config_io.mutate_config``).
Read only and never raises: a missing, empty or unparseable file is ``[]``.

Each row: ``{id, name, type, tier, enabled, managed_by, member_kind, member_count,
members, members_truncated, tags}``. ``managed_by`` is ``"manual"`` when the key is absent
(lower-cased otherwise). A feed's members are the editable strings at its type's config key
(``jobs.models.MEMBER_KEY``: an RSS feed's ``urls``, a Hacker News feed's ``keywords``, ...);
``member_count`` is the true total and ``members`` is capped at ``MEMBER_CAP``
(``members_truncated`` says so).

Key public symbols: ``parse_sources``, ``feed_members``, ``member_key``, ``MEMBER_CAP``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from jobs.models import MEMBER_KEY, FeedType

logger = logging.getLogger(__name__)

MEMBER_CAP = 50  # bounds the per-feed members payload; member_count stays the true total


def member_key(feed_type: str) -> str | None:
    """The config key holding this feed type's editable string members, or None (a type
    with no editable members, or one not in ``FeedType``)."""
    try:
        return MEMBER_KEY.get(FeedType(feed_type))
    except ValueError:
        return None


def feed_members(cfg: dict, feed_type: str) -> list[str]:
    """Every editable string member of a feed (non-string entries are skipped); ``[]`` for
    a type without members or an absent key."""
    key = member_key(feed_type)
    if not key or not isinstance(cfg, dict):
        return []
    raw = cfg.get(key)
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, str)]


def parse_sources(config_path: Path) -> list[dict]:
    """The ``feeds:`` list of ``config_path`` as feed-settings rows (see the module doc).
    A feed without a non-empty string id is skipped (it could not be keyed)."""
    if not config_path.is_file():
        return []
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("feed config: failed to read %s: %s", config_path, e)
        return []
    if not isinstance(config, dict):
        return []

    rows: list[dict] = []
    for fd in config.get("feeds", []) or []:
        if not isinstance(fd, dict):
            continue
        fid = fd.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        cfg = fd.get("config") or {}
        raw_managed = fd.get("managed_by", "manual")
        managed_by = raw_managed.lower() if isinstance(raw_managed, str) and raw_managed else "manual"
        ftype = fd.get("type", "unknown")
        members_all = feed_members(cfg, ftype)
        rows.append({
            "id": fid,
            "name": fd.get("name", fid),
            "type": ftype,
            "tier": fd.get("tier", "engineering"),
            "enabled": bool(fd.get("enabled", True)),
            "managed_by": managed_by,
            "member_kind": member_key(ftype),
            "member_count": len(members_all),
            "members": members_all[:MEMBER_CAP],
            "members_truncated": len(members_all) > MEMBER_CAP,
            "tags": fd.get("tags", []) if isinstance(fd.get("tags"), list) else [],
        })
    return rows
