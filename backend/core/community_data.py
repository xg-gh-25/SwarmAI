"""community_data — read-only data layer behind the Community overlay (Phase-1).

The Community overlay is SwarmAI's two-way membrane with the outside world:
inbound signals (what we subscribe to + what's worth reading) and outbound
engagement (how our community participation is doing). Phase-1 is strictly
READ-ONLY — three pure functions parse existing on-disk sources into
overlay-ready shapes. No mutation, no self_tune coupling, no config writes.

Data sources (all verified live on-disk during the pipeline's PLAN stage):
  - Sources tab  ← Services/swarm-jobs/config.yaml `feeds:` list
  - Feed tab     ← Knowledge/{Signals,Reports}/*.md (recent, newest-first)
  - Engagement   ← Projects/GitHub_Community/.artifacts/{engagement_log,
                   reply_archive,star_log}.jsonl

Design decisions forced by the data (Gate-1, run_5165013e):
  - config.yaml feeds have NO `managed_by` key → default to "manual" (never
    KeyError). This is also the self_tune coexistence contract: a Phase-2 UI
    write sets managed_by="user"; self_tune.prune_unused_feeds only auto-disables
    managed_by=="self-tune", so user/manual feeds are structurally protected.
  - There is NO quality-score data on disk (no quality_scores.jsonl; engagement_log
    carries confidence/status, not a 0-10 quality) → aggregate_engagement DOES NOT
    fabricate an avg_quality. Only data-backed metrics are returned.
  - Signal digests are curated markdown narratives, not per-item scored JSON → the
    Feed tab surfaces recent digest/report FILES (open in Canvas), not invented
    scored rows.

Functions take explicit paths (dependency injection) so they are unit-testable
against tmp fixtures and never hard-depend on the live workspace.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Feed tab is community-scoped: only signal digests + reports, never Notes/etc.
_FEED_CATEGORIES = {"Signals", "Reports"}
_FEED_CAP = 100  # bound the returned payload (not a silent truncation of value)


def feed_cap() -> int:
    """The feed payload cap — exposed so the router can flag honest truncation."""
    return _FEED_CAP


def parse_sources(config_path: Path) -> list[dict]:
    """Parse the `feeds:` list from a jobs config.yaml into overlay source rows.

    Returns [] for a missing/empty/unparseable config (fresh install) — never
    raises. Each row: {id, name, type, tier, enabled, managed_by, source_count,
    tags}. `managed_by` defaults to "manual" when the key is absent (the real
    config.yaml feeds have no managed_by — verified live).
    """
    if not config_path.is_file():
        return []
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("community_data: failed to read %s: %s", config_path, e)
        return []

    rows: list[dict] = []
    for fd in config.get("feeds", []) or []:
        if not isinstance(fd, dict):
            continue
        # id must be a non-empty string — a feed with a blank/missing id would
        # break frontend keying (React keys, lookups); skip it rather than emit "".
        fid = fd.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        cfg = fd.get("config") or {}
        # source_count = number of concrete sources this feed pulls (urls), a
        # real derivable datum, 0 when the feed has none.
        urls = cfg.get("urls") if isinstance(cfg, dict) else None
        source_count = len(urls) if isinstance(urls, list) else 0
        # managed_by: ABSENT → "manual" (never assume the key). Normalize case so a
        # config typo ("Self-Tune") still matches self_tune's lowercase gate in
        # Phase-2 (self_tune.prune_unused_feeds keys on managed_by=="self-tune").
        raw_managed = fd.get("managed_by", "manual")
        managed_by = raw_managed.lower() if isinstance(raw_managed, str) and raw_managed else "manual"
        rows.append(
            {
                "id": fid,
                "name": fd.get("name", fid),
                "type": fd.get("type", "unknown"),
                "tier": fd.get("tier", "engineering"),
                "enabled": bool(fd.get("enabled", True)),
                "managed_by": managed_by,
                "source_count": source_count,
                "tags": fd.get("tags", []) if isinstance(fd.get("tags"), list) else [],
            }
        )
    return rows


_MAX_JSONL_BYTES = 50 * 1024 * 1024  # 50MB — bound the read (defense vs a runaway/huge log → OOM)


def _read_jsonl(path: Path) -> list[dict]:
    """Read a jsonl file → list of dicts, skipping unparseable lines. [] if absent.

    Bounded by _MAX_JSONL_BYTES: an oversized file returns [] (logged) rather than
    reading it all into memory — these are append-only local logs that should never
    reach 50MB; if one does, it's a bug upstream, not data to render.
    """
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > _MAX_JSONL_BYTES:
            logger.warning("community_data: %s exceeds %d bytes — skipping", path, _MAX_JSONL_BYTES)
            return []
    except OSError:
        return []
    out: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # a corrupt line is skipped, never fatal
            if isinstance(obj, dict):
                out.append(obj)
    except OSError as e:
        logger.warning("community_data: failed to read %s: %s", path, e)
    return out


def aggregate_engagement(artifacts_dir: Path) -> dict:
    """Aggregate GitHub_Community engagement jsonl into data-backed metrics.

    Returns {comments_posted, replies_received, maintainer_replies, stars}.
    ONLY metrics with real backing data — deliberately NO avg_quality (there is
    no quality-score source on disk; inventing one would be data-dump theater).
    A missing artifacts dir yields zeros, never a crash.
    """
    eng = _read_jsonl(artifacts_dir / "engagement_log.jsonl")
    replies = _read_jsonl(artifacts_dir / "reply_archive.jsonl")
    stars = _read_jsonl(artifacts_dir / "star_log.jsonl")

    comments_posted = sum(1 for e in eng if e.get("status") == "posted")
    replies_received = len(replies)
    # is_maintainer may be a real bool OR a string ("True"/"true"/"TRUE") — jsonl
    # round-trips vary by writer. Normalize via str().lower() so every truthy form
    # matches, not just the three literals we happened to think of.
    def _is_true(v: object) -> bool:
        return v is True or (isinstance(v, str) and v.lower() in ("true", "1"))

    maintainer_replies = sum(1 for r in replies if _is_true(r.get("is_maintainer")))
    latest_stars = None
    if stars:
        last = stars[-1]
        latest_stars = last.get("count") if isinstance(last.get("count"), int) else None

    return {
        "comments_posted": comments_posted,
        "replies_received": replies_received,
        "maintainer_replies": maintainer_replies,
        "stars": latest_stars,
    }


def build_feed(knowledge_dir: Path) -> list[dict]:
    """Build the community Feed from recent Signals + Reports files.

    Returns overlay rows [{path, category, mtime, name}] sorted newest-first,
    capped. Community-scoped: ONLY Signals + Reports categories (a signal digest
    or a report), never general Notes. [] when Knowledge/ is absent.
    """
    if not knowledge_dir.is_dir():
        return []
    kroot = knowledge_dir.resolve()
    items: list[dict] = []
    for category in _FEED_CATEGORIES:
        cdir = knowledge_dir / category
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*.md"):
            if not p.is_file() or p.name.startswith("."):
                continue
            # Defense-in-depth: rglob follows dir symlinks, so a symlink under
            # Signals/Reports could point OUTSIDE Knowledge/. The downstream
            # /workspace/file/resolve already rejects an escaping path (400), but
            # never EMIT such a row — it would be a dead click + a would-be infoleak
            # if the resolver ever regressed. Drop any file whose real path escapes.
            try:
                if not p.resolve().is_relative_to(kroot):
                    continue
            except OSError:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            rel = p.relative_to(knowledge_dir)
            items.append(
                {
                    "path": f"Knowledge/{rel.as_posix()}",
                    "category": category,
                    "name": p.name,
                    "mtime": mtime,
                }
            )
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return items[:_FEED_CAP]
