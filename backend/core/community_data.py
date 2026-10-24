"""community_data — read-only data layer behind the Community overlay.

The Community overlay is SwarmAI's two-way membrane with the outside world:
inbound signals (what we subscribe to + what's worth reading) and outbound
engagement (how our community participation is doing). READ-ONLY — pure
functions parse existing on-disk sources into overlay-ready shapes. No mutation,
no self_tune coupling, no config writes.

Data sources (all verified live on-disk during the pipeline's PLAN stage):
  - Watching tab ← Services/swarm-jobs/config.yaml `feeds:` list (parse_sources)
  - Inbound tab  ← Knowledge/{Signals,Reports}/*.{md,html} (recent, newest-first;
                   HTML reports are fail-closed — see _is_community_html_report;
                   build_feed — the overlay splits Reports vs Signals by category)
  (Hot Topics and the Outbound engagement tab read one user's GitHub_Community
   DDD artifacts and were removed: usable-by-anyone P7.)

Design decisions forced by the data (Gate-1, run_5165013e):
  - config.yaml feeds have NO `managed_by` key → default to "manual" (never
    KeyError). This is also the self_tune coexistence contract: a Phase-2 UI
    write sets managed_by="user"; self_tune.prune_unused_feeds only auto-disables
    managed_by=="self-tune", so user/manual feeds are structurally protected.
  - Signal digests are curated markdown narratives, not per-item scored JSON → the
    Feed tab surfaces recent digest/report FILES (open in Canvas), not invented
    scored rows.

Functions take explicit paths (dependency injection) so they are unit-testable
against tmp fixtures and never hard-depend on the live workspace.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Feed tab is community-scoped: only signal digests + reports, never Notes/etc.
_FEED_CATEGORIES = {"Signals", "Reports"}
_FEED_CAP = 100  # bound the returned payload (not a silent truncation of value)
_MEMBER_CAP = 50  # bound the per-feed members payload (member_count stays the TRUE total)

# Internal governance reports that must NOT surface in the community Feed. These are
# the recurring, machine-generated reports whose SSOT is the internal-report JobTypes
# (jobs.models.JobType: DDD_WEEKLY_REPORT / DDD_SELF_AUDIT / SWARMAI_MONTHLY_REPORT) plus
# the pipeline-weekly + validator-audit generators. This is a NAMED registry (not
# scattered magic strings) — when a new internal-report generator is added, add its
# stem here. A report can also self-classify via a frontmatter `audience:` token (below),
# which OVERRIDES this list in both directions.
# Matched against the filename with any leading `YYYY-MM-DD-`/`YYYY-MM-` date prefix
# stripped, so `2026-07-27-ddd-weekly.md` and `2026-06-swarmai-monthly.md` both match.
_INTERNAL_REPORT_STEMS = (
    "ddd-weekly",          # JobType.DDD_WEEKLY_REPORT (s_ddd-weekly-report)
    "ddd-self-audit",      # JobType.DDD_SELF_AUDIT
    "swarmai-monthly",     # the removed SwarmAI monthly report: old files on disk stay hidden
    "pipeline-weekly",     # pipeline health report generator
    "validator-check-usage",  # validator-check-usage-audit governance report (precise stem, not a broad `validator-` prefix)
)

# Only these EXACT frontmatter audience tokens are a classification signal. Any OTHER
# audience value (the field is free-text in the real corpus — e.g. "how it works
# internally") is IGNORED, never treated as internal/community (Gate-1 correction:
# a substring match on "internal" would wrongly hide a community brief).
_AUDIENCE_INTERNAL = "internal"
_AUDIENCE_COMMUNITY = "community"
_FRONTMATTER_HEAD_BYTES = 600  # read only the head for frontmatter, never the full file in the feed loop

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?-")

# Community-facing HTML report stems (fail-CLOSED allowlist). Unlike .md, an .html
# report carries NO `---` frontmatter, so _report_audience can never self-classify it
# → a bare *.html glob would DEFAULT-SHOW everything, leaking confidential reports
# (CMHK customer financials, internal eval scorecards, temp files) into the OUTWARD
# community Feed (Gate-1 CRITICAL, run_03b5d04f). So HTML is fail-CLOSED: excluded
# UNLESS its date-stripped stem starts with one of these, OR it opts in via an
# explicit <meta name="audience" content="community"> tag. The community weekly
# report writer (s_github_community/report.py) emits `<date>-weekly.html`.
# EXACT date-stripped stems (stem-minus-.html must EQUAL one of these). A prefix
# match here would leak: `startswith("weekly")` also matches a future confidential
# `<date>-weekly-cmhk-financials.html` (Gate-2 HIGH, run_03b5d04f) — the community
# weekly is exactly `<date>-weekly.html`, so require equality, not prefix.
_COMMUNITY_HTML_EXACT_STEMS = frozenset({
    "weekly",              # community weekly report (report.py: <date>-weekly.html)
    "community_report",    # community_report.html
})
# INTENTIONAL prefix stems — ONLY a specific, unambiguous community-report family.
# A bare "community-" prefix was dropped (Gate-2 HIGH): it would leak a future
# confidential `community-cmhk-secret.html`. Fail-closed — a new community-report
# family must be added here explicitly, never matched by a loose prefix.
_COMMUNITY_HTML_PREFIX_STEMS = (
    "github-community-weekly-",  # legacy community weekly naming (e.g. -w22)
)
# An HTML head large enough to catch a <meta name="audience"> tag in <head>.
_HTML_HEAD_BYTES = 2048
# Match <meta ... name="audience" ... content="X"> in EITHER attribute order
# (name-before-content OR content-before-name) — a reversed-order tag must still
# force-classify (Gate-2 LOW, run_03b5d04f).
_HTML_AUDIENCE_RES = (
    re.compile(r"""<meta\s+[^>]*\bname=["']audience["'][^>]*\bcontent=["']([^"']+)["']""", re.IGNORECASE),
    re.compile(r"""<meta\s+[^>]*\bcontent=["']([^"']+)["'][^>]*\bname=["']audience["']""", re.IGNORECASE),
)


def _html_report_audience(head_text: str) -> str | None:
    """Return the EXACT audience token from an HTML report's
    <meta name="audience" content="..."> tag, or None if absent/free-text.
    The HTML analogue of _report_audience (which reads `---` frontmatter that
    HTML files do not have)."""
    for pat in _HTML_AUDIENCE_RES:
        m = pat.search(head_text)
        if m:
            token = m.group(1).strip().lower()
            return token if token in (_AUDIENCE_INTERNAL, _AUDIENCE_COMMUNITY) else None
    return None


def _is_community_html_report(name: str, head_text: str) -> bool:
    """Classify a Reports/*.html file — FAIL-CLOSED (default EXCLUDE). Precedence:
    (1) explicit <meta audience> tag overrides both ways; (2) else surface ONLY if
    the date-stripped stem matches _COMMUNITY_HTML_STEMS; (3) else EXCLUDE. This is
    the inverse polarity of _is_community_report (.md), because HTML cannot carry
    the frontmatter that makes DEFAULT-SHOW safe for markdown."""
    audience = _html_report_audience(head_text)
    if audience == _AUDIENCE_INTERNAL:
        return False
    if audience == _AUDIENCE_COMMUNITY:
        return True
    stem = _DATE_PREFIX_RE.sub("", name.lower())
    stem_noext = stem[:-5] if stem.endswith(".html") else stem
    if stem_noext in _COMMUNITY_HTML_EXACT_STEMS:
        return True
    return any(stem.startswith(s) for s in _COMMUNITY_HTML_PREFIX_STEMS)


def _report_audience(head_text: str) -> str | None:
    """Return the EXACT audience token ('internal'|'community') from a report's
    frontmatter head, or None if absent/free-text/unparseable. Reuses the shared
    parse_frontmatter (never hand-rolls YAML). Free-text audience → None (ignored)."""
    try:
        from core.daily_activity_writer import parse_frontmatter
    except ImportError:
        return None
    fm, _ = parse_frontmatter(head_text)
    raw = fm.get("audience")
    if not isinstance(raw, str):
        return None
    token = raw.strip().lower()
    return token if token in (_AUDIENCE_INTERNAL, _AUDIENCE_COMMUNITY) else None


def _is_community_report(name: str, head_text: str) -> bool:
    """Classify a Reports/*.md file as community-facing (surface in Feed) or internal
    (exclude). Precedence: (1) exact frontmatter audience token overrides; (2) else the
    internal-generator name registry excludes; (3) else DEFAULT SHOW (most reports are
    community; fail-CLOSED would empty the feed since most carry no audience tag)."""
    audience = _report_audience(head_text)
    if audience == _AUDIENCE_INTERNAL:
        return False
    if audience == _AUDIENCE_COMMUNITY:
        return True
    stem = _DATE_PREFIX_RE.sub("", name.lower())
    return not any(stem.startswith(s) for s in _INTERNAL_REPORT_STEMS)


def feed_cap() -> int:
    """The feed payload cap — exposed so the router can flag honest truncation."""
    return _FEED_CAP


def _feed_member_key(feed_type: str) -> str | None:
    """The config key holding this feed type's editable string members, or None.
    Single source = jobs.models.MEMBER_KEY (keyed by FeedType). An unknown/invalid
    type → None (no editable members), never a crash."""
    try:
        from jobs.models import FeedType, MEMBER_KEY
    except ImportError:
        return None
    try:
        return MEMBER_KEY.get(FeedType(feed_type))
    except ValueError:
        return None  # a type not in the FeedType enum has no editable members


def feed_members(cfg: dict, feed_type: str) -> list[str]:
    """The full list of editable STRING members for a feed (the urls/keywords/queries/
    repos/… at the per-type MEMBER_KEY). Only string entries are members — a non-string
    (e.g. a dict) is skipped. Returns [] for a no-member type or absent/blank key."""
    key = _feed_member_key(feed_type)
    if not key or not isinstance(cfg, dict):
        return []
    raw = cfg.get(key)
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, str)]


def parse_sources(config_path: Path) -> list[dict]:
    """Parse the `feeds:` list from a jobs config.yaml into overlay source rows.

    Returns [] for a missing/empty/unparseable config (fresh install) — never
    raises. Each row: {id, name, type, tier, enabled, managed_by,
    members, member_count, member_kind, members_truncated, tags}.

    `managed_by` defaults to "manual" when the key is absent. `member_count` is the
    ACCURATE per-type member count (urls/keywords/repos/… via MEMBER_KEY) and `members`
    is that list capped at _MEMBER_CAP (`members_truncated` flags the cut). `member_kind`
    is the config key name (or None for a no-editable-member feed type).
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
        # managed_by: ABSENT → "manual" (never assume the key). Normalize case so a
        # config typo ("Self-Tune") still matches self_tune's lowercase gate in
        # Phase-2 (self_tune.prune_unused_feeds keys on managed_by=="self-tune").
        raw_managed = fd.get("managed_by", "manual")
        managed_by = raw_managed.lower() if isinstance(raw_managed, str) and raw_managed else "manual"
        # Editable members (the accurate per-type count + the capped list). member_count
        # is the TRUE total; members is capped at _MEMBER_CAP for payload safety.
        ftype = fd.get("type", "unknown")
        members_all = feed_members(cfg, ftype)
        rows.append(
            {
                "id": fid,
                "name": fd.get("name", fid),
                "type": ftype,
                "tier": fd.get("tier", "engineering"),
                "enabled": bool(fd.get("enabled", True)),
                "managed_by": managed_by,
                "member_kind": _feed_member_key(ftype),
                "member_count": len(members_all),
                "members": members_all[:_MEMBER_CAP],
                "members_truncated": len(members_all) > _MEMBER_CAP,
                "tags": fd.get("tags", []) if isinstance(fd.get("tags"), list) else [],
            }
        )
    return rows


# A community weekly REPORT filename: `<date>-weekly.md|html` (report.py emits exactly
# this stem). Matches ONLY the bare `weekly` stem — NOT `ddd-weekly` / `pipeline-weekly`
# / any other `*-weekly` internal report (those carry a qualifier before "weekly" and
# are internal, excluded upstream). Date prefix optional, extension .md or .html.
_WEEKLY_REPORT_NAME_RE = re.compile(r"^(?:\d{4}-\d{2}(?:-\d{2})?-)?weekly\.(?:md|html)$", re.IGNORECASE)


def _is_weekly_report_name(name: str) -> bool:
    """True for a community weekly report filename (<date>-weekly.md|html), which is a
    REPORT even when it lives in Knowledge/Signals/. Excludes qualified weeklies
    (ddd-weekly, pipeline-weekly, …) — those are internal and already filtered."""
    return bool(_WEEKLY_REPORT_NAME_RE.match(name))


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
        # Glob .md AND .html: the community weekly report is dual-written as
        # <date>-weekly.html (report.py). HTML is classified FAIL-CLOSED below —
        # a bare *.html glob would leak confidential reports (gap1, run_03b5d04f).
        feed_files = sorted(cdir.rglob("*.md")) + sorted(cdir.rglob("*.html"))
        for p in feed_files:
            if not p.is_file() or p.name.startswith("."):
                continue
            is_html = p.suffix.lower() == ".html"
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
            # Reports are classified community-vs-internal (Signals are pure community
            # digests — never filtered). For .md: internal governance reports are
            # excluded unless frontmatter opts in (DEFAULT-SHOW). For .html: FAIL-CLOSED
            # (DEFAULT-EXCLUDE) — HTML has no frontmatter to self-classify, so only a
            # community-stem or an explicit <meta audience=community> surfaces.
            if category == "Reports":
                head_bytes = _HTML_HEAD_BYTES if is_html else _FRONTMATTER_HEAD_BYTES
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as fh:
                        head = fh.read(head_bytes)
                except OSError:
                    continue
                classifier = _is_community_html_report if is_html else _is_community_report
                if not classifier(p.name, head):
                    continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            rel = p.relative_to(knowledge_dir)
            # A community WEEKLY report is written into Knowledge/Signals/ as
            # <date>-weekly.md (the engine dual-writes .html to Reports/ + .md to
            # Signals/). By file location it lands in the Signals category, but it is a
            # REPORT, not a daily digest — so the Inbound tab was still interleaving 11
            # weekly .md files into the daily-signal flow (the "reports mixed in" bug the
            # .html-only card fix missed). Reclassify by NAME so the frontend's
            # category split routes it to the report card, not the signal list. The
            # `*-ddd-weekly*` internal governance reports are already excluded upstream
            # by _is_community_report (they live in Reports/), so this only catches the
            # community weekly.
            effective_category = "Reports" if _is_weekly_report_name(p.name) else category
            items.append(
                {
                    "path": f"Knowledge/{rel.as_posix()}",
                    "category": effective_category,
                    "name": p.name,
                    "mtime": mtime,
                }
            )
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return items[:_FEED_CAP]
