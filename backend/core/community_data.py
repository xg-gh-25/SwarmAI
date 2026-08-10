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
  - Hot Topics   ← Projects/GitHub_Community/2-understanding/TECH.md
                   `## GitHub Hot Topics` Rankings table (parse_hot_topics)
  - Outbound tab ← Projects/GitHub_Community/.artifacts/{engagement_log,
                   reply_archive,star_log}.jsonl — aggregate_engagement (scalar
                   KPIs) + engagement_items (the per-engagement list, joined
                   engagement×replies, needs-followup first)

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
    "swarmai-monthly",     # JobType.SWARMAI_MONTHLY_REPORT (s_swarmai-monthly-report)
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


def _is_true(v: object) -> bool:
    """Truthy for a real bool True OR a stringified 'true'/'1' (jsonl round-trips
    vary by writer — is_maintainer may be a bool or a string)."""
    return v is True or (isinstance(v, str) and v.lower() in ("true", "1"))


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

    # publish.py writes status="published" on a successful comment post (community
    # engine publish.py:136). The old predicate matched "posted" — a value the
    # writer never emits — so comments_posted was silently 0 on the live corpus
    # (216 published / 0 posted). Count what the writer actually writes.
    comments_posted = sum(1 for e in eng if e.get("status") == "published")
    replies_received = len(replies)
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


# Cap the Outbound engagement list payload (not a silent truncation of value — the
# KPI counts stay the TRUE totals; this only bounds the rendered rows).
_ENGAGEMENT_ITEMS_CAP = 200

# Our own GitHub identity — a reply BY us is not a reply we owe an answer to. The
# community engine posts as this login (monitor.py/track.py all query xg-gh-25).
_OUR_GH_LOGIN = "xg-gh-25"


def _is_bot_author(author: str) -> bool:
    """A CI/app bot reply (dependabot[bot], github-actions[bot], *-bot) is not a
    human waiting on us. Mirrors monitor.py's bot heuristic."""
    a = (author or "").lower()
    return a.endswith("[bot]") or a.endswith("-bot")


def _needs_followup(reply_rows: list[dict]) -> bool:
    """A thread needs OUR follow-up iff the LATEST reply is from someone else —
    not us (_OUR_GH_LOGIN) and not a bot. A thread where WE replied last (or only
    bots replied) is NOT waiting on us. `reply_rows` are in reply_archive order,
    which is chronological (track.py appends as it fetches), so the last element is
    the newest.

    This is the fix for the 'needs-followup is 72% noise' bug: the old rule
    (bool(reply_rows) — ANY reply) flagged every thread we'd already answered, so
    the highest-priority sort sorted nothing. The signal is the LAST voice in the
    thread, not the mere existence of replies."""
    if not reply_rows:
        return False
    last = reply_rows[-1]
    author = (last.get("author") or "").lower()  # case-insensitive (matches _is_bot_author)
    if author == _OUR_GH_LOGIN.lower() or _is_bot_author(author):
        return False
    return True


def engagement_items(artifacts_dir: Path) -> list[dict]:
    """Join engagement_log × reply_archive into per-engagement rows for the Outbound
    list — the clickable companion to aggregate_engagement's scalar KPIs.

    Each row = one PUBLISHED comment we posted, with any replies it received nested
    under it. The join is EXACT on (repo, issue_number) == (source_repo, source_issue)
    — both sides use the same value space ("owner/name" string + int). Rows that
    received a reply (esp. a maintainer reply) are surfaced FIRST (needs_followup),
    because a reply to us is the highest-priority action (TECH.md Scoring Formula
    reply_to_us_bonus). Newest-first within each group.

    Returns [] on a missing dir (never crashes). Only PUBLISHED engagements appear —
    drafts/errors/blocked are KPI-only, not actionable rows.
    """
    eng = _read_jsonl(artifacts_dir / "engagement_log.jsonl")
    replies = _read_jsonl(artifacts_dir / "reply_archive.jsonl")

    # Index replies by (source_repo, source_issue) → list, for an O(1) join.
    # SKIP replies missing either key half: a (None, None) key would false-match every
    # engagement that also happens to be missing repo/issue_number (e.g. a malformed
    # row), cross-attaching unrelated replies. A reply we can't attribute is dropped
    # from the join (it never inflates a wrong row).
    replies_by_key: dict[tuple[str, object], list[dict]] = {}
    for r in replies:
        key = (r.get("source_repo"), r.get("source_issue"))
        if key[0] is None or key[1] is None:
            continue
        replies_by_key.setdefault(key, []).append(r)

    items: list[dict] = []
    for e in eng:
        if e.get("status") != "published":
            continue  # only actionable, actually-posted comments
        repo = e.get("repo")
        issue = e.get("issue_number")
        # Only join when BOTH key halves are present — else no replies (never a
        # (None,None) match). A published comment with a missing repo/issue still
        # renders as a row, just with zero replies.
        matched = replies_by_key.get((repo, issue), []) if (repo is not None and issue is not None) else []
        reply_rows = [
            {
                "author": r.get("author", ""),
                "body": r.get("body", ""),
                "is_maintainer": _is_true(r.get("is_maintainer")),
                "created_at": r.get("created_at", ""),
            }
            for r in matched
        ]
        # Chronological order (oldest→newest) so reply_rows[-1] is reliably the LATEST
        # reply — _needs_followup keys off the last voice in the thread. Don't trust
        # append order; sort explicitly. A MISSING created_at sorts LAST (newest), not
        # first: an un-timestamped reply is most likely a just-fetched one, and burying
        # it under a timestamped human reply would falsely flag a thread WE answered as
        # needs-followup (adversarial HIGH). "￿" sorts after any real ISO date.
        reply_rows.sort(key=lambda rr: rr.get("created_at") or "￿")
        has_maintainer = any(rr["is_maintainer"] for rr in reply_rows)
        # needs_followup = the LATEST reply is from someone else (not us, not a bot) —
        # i.e. a human is waiting on OUR response. A thread we replied to last is done.
        needs = _needs_followup(reply_rows)
        items.append({
            "repo": repo,
            "issue_number": issue,
            "topic": e.get("topic", ""),
            "status": e.get("status", ""),
            "comment_url": e.get("comment_url", ""),
            "posted_at": e.get("posted_at", ""),
            "confidence": e.get("confidence"),
            "reply_count": len(reply_rows),
            "has_maintainer_reply": has_maintainer,
            "needs_followup": needs,
            "replies": reply_rows,
        })

    # Sort tiers (gate on needs_followup FIRST — a maintainer thread we already
    # answered is NOT urgent): 0 = a maintainer is waiting on us, 1 = someone is
    # waiting on us, 2 = no action needed (we replied last / no reply). Two stable
    # passes: (1) newest-first by posted_at, then (2) by tier — newest stays on top
    # within a tier. Missing posted_at ("") sorts oldest = least urgent.
    def _tier(it: dict) -> int:
        if it["needs_followup"]:
            return 0 if it["has_maintainer_reply"] else 1
        return 2
    items.sort(key=lambda it: it.get("posted_at") or "", reverse=True)
    items.sort(key=_tier)
    return items[:_ENGAGEMENT_ITEMS_CAP]


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


# ── Hot Topics (gap2) — parse TECH.md "## GitHub Hot Topics" → Rankings table ──

_HOT_TOPICS_HEADER = "## GitHub Hot Topics"
# Enter Rankings on ANY `### Rankings` header (do NOT couple to the freshness
# clause — a doc edit dropping "(Updated …)" must not silently empty Hot Topics;
# Gate-2 MED, run_03b5d04f). The date is captured SEPARATELY, optionally.
_RANKINGS_HEADER_RE = re.compile(r"^###\s+Rankings\b", re.IGNORECASE)
_RANKINGS_DATE_RE = re.compile(r"\(Updated\s+([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _strip_md(cell: str) -> str:
    """Strip markdown bold from a table cell (links/emoji are kept — they carry
    meaning in the evidence/trend columns). Bold → plain so the UI renders text,
    not literal asterisks."""
    return _MD_BOLD_RE.sub(r"\1", cell).strip()


def parse_hot_topics(tech_md_path: Path) -> dict:
    """Parse the ## GitHub Hot Topics → ### Rankings pipe table from a project's
    TECH.md into {updated: <YYYY-MM-DD|None>, topics: [{rank,topic,evidence,trend}]}.

    FAIL-SOFT: a missing file / missing section / missing table → {updated:None,
    topics:[]} (never raises — the endpoint must not 500 on a doc edit). Scoped to
    the Rankings table ONLY: parsing stops at the next `###`/`##` header so it never
    bleeds into the sibling `### Top Movers` table (different schema). No LLM — the
    table is fixed-column pipe markdown."""
    empty = {"updated": None, "topics": []}
    try:
        text = tech_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty

    # Locate the ## GitHub Hot Topics section (bound the search to it).
    sec_start = text.find(_HOT_TOPICS_HEADER)
    if sec_start == -1:
        return empty
    section = text[sec_start:]

    updated: str | None = None
    topics: list[dict] = []
    in_rankings = False
    for line in section.splitlines():
        stripped = line.strip()
        # A new ###/## header AFTER we entered Rankings ends the table scope
        # (stops before ### Top Movers). The section's own ## header is the first
        # line, so only treat a header as a terminator once we're in Rankings.
        if in_rankings and (stripped.startswith("### ") or stripped.startswith("## ")):
            break
        if _RANKINGS_HEADER_RE.match(stripped):
            in_rankings = True
            dm = _RANKINGS_DATE_RE.search(stripped)  # date is OPTIONAL
            if dm:
                updated = dm.group(1)
            continue
        if not in_rankings:
            continue
        # Inside Rankings: parse pipe rows. Skip the header row (| Rank | Topic |…)
        # and the separator row (|------|). A data row starts with an int rank.
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        rank_raw = cells[0].strip()
        if not rank_raw.isdigit():
            continue  # header row ("Rank") or separator row ("------")
        topics.append({
            "rank": int(rank_raw),
            "topic": _strip_md(cells[1]),
            "evidence": _strip_md(cells[2]),
            "trend": _strip_md(cells[3]),
        })
    return {"updated": updated, "topics": topics}
