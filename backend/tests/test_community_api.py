"""Tests for community_data — the read-only data layer behind the Community overlay.

Phase-1 is READ-ONLY: three pure functions parse existing on-disk sources
(config.yaml feeds, Knowledge/Signals+Reports files, GitHub_Community engagement
jsonl) into overlay-ready shapes. No mutation, no self_tune coupling.

Methodology: tmp_path fixtures that mirror the REAL on-disk shapes verified live
during PLAN (config.yaml feeds LACK managed_by; engagement_log has confidence/
status; reply_archive has is_maintainer) — so the tests encode reality, not an
assumed schema. The functions take explicit paths (dependency injection) so no
test touches the real workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.community_data import (
    parse_sources,
    aggregate_engagement,
    build_feed,
)


# ── Sources (config.yaml feeds) ──────────────────────────────────────────────

_CONFIG_YAML = """\
feeds:
  - id: ai-engineering
    name: AI Engineering Blogs
    type: rss
    tier: engineering
    enabled: true
    config:
      urls: [https://a.com/feed, https://b.com/feed]
    tags: [ai, engineering]
  - id: github-trending
    name: GitHub Trending
    type: github_trending
    tier: engineering
    enabled: true
    managed_by: self-tune
    config:
      spoken_language: en
  - id: hn-ai
    name: Hacker News
    type: hacker_news
    tier: aggregate
    enabled: false
    managed_by: user
    config: {}
defaults:
  max_active_feeds: 15
user_context: {}
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(_CONFIG_YAML)
    return p


def test_parse_sources_returns_all_feeds(config_file: Path) -> None:
    sources = parse_sources(config_file)
    assert len(sources) == 3
    ids = [s["id"] for s in sources]
    assert ids == ["ai-engineering", "github-trending", "hn-ai"]


def test_parse_sources_defaults_managed_by_to_manual(config_file: Path) -> None:
    # The REAL config.yaml feeds have NO managed_by key (verified live in PLAN).
    # A feed without it must default to "manual", never KeyError.
    sources = parse_sources(config_file)
    by_id = {s["id"]: s for s in sources}
    assert by_id["ai-engineering"]["managed_by"] == "manual"  # absent -> manual
    assert by_id["github-trending"]["managed_by"] == "self-tune"
    assert by_id["hn-ai"]["managed_by"] == "user"


def test_parse_sources_carries_core_fields(config_file: Path) -> None:
    sources = parse_sources(config_file)
    s = sources[0]
    assert s["name"] == "AI Engineering Blogs"
    assert s["type"] == "rss"
    assert s["tier"] == "engineering"
    assert s["enabled"] is True
    # url count is a useful, real, derivable datum (not fabricated)
    assert s["source_count"] == 2


def test_parse_sources_disabled_feed(config_file: Path) -> None:
    sources = parse_sources(config_file)
    by_id = {s["id"]: s for s in sources}
    assert by_id["hn-ai"]["enabled"] is False


def test_parse_sources_missing_file_returns_empty(tmp_path: Path) -> None:
    # A missing config.yaml must return [] (fresh install), never crash.
    assert parse_sources(tmp_path / "nope.yaml") == []


def test_parse_sources_empty_feeds(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("feeds: []\ndefaults: {}\n")
    assert parse_sources(p) == []


def test_parse_sources_normalizes_managed_by_case(tmp_path: Path) -> None:
    # A config typo "Self-Tune" must normalize to lowercase so it still matches
    # self_tune's lowercase gate (managed_by=="self-tune") in Phase-2.
    p = tmp_path / "c.yaml"
    p.write_text(
        "feeds:\n  - id: f1\n    name: F1\n    type: rss\n    managed_by: Self-Tune\n"
    )
    assert parse_sources(p)[0]["managed_by"] == "self-tune"


def test_parse_sources_skips_blank_id(tmp_path: Path) -> None:
    # A feed with an empty/blank id would break frontend keying — skip it.
    p = tmp_path / "c.yaml"
    p.write_text(
        "feeds:\n  - id: ''\n    name: Blank\n  - id: good\n    name: Good\n"
    )
    ids = [s["id"] for s in parse_sources(p)]
    assert ids == ["good"]


def test_aggregate_engagement_maintainer_case_insensitive(tmp_path: Path) -> None:
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "is_maintainer": "TRUE"},   # uppercase string
                {"id": "r2", "is_maintainer": True},     # real bool
                {"id": "r3", "is_maintainer": "false"},  # not a maintainer
            ]
        )
        + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["maintainer_replies"] == 2  # TRUE + True, not false


# ── Engagement (GitHub_Community jsonl aggregation) ──────────────────────────


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".artifacts"
    d.mkdir()
    # engagement_log.jsonl — real shape: comment_id/status/posted_at/topic/repo
    (d / "engagement_log.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"comment_id": "c1", "status": "posted", "posted_at": "2026-08-01T10:00:00Z", "repo": "a/b", "topic": "memory"},
                {"comment_id": "c2", "status": "posted", "posted_at": "2026-08-05T10:00:00Z", "repo": "c/d", "topic": "pipeline"},
                {"comment_id": "c3", "status": "draft", "posted_at": "2026-08-06T10:00:00Z", "repo": "e/f", "topic": "skills"},
            ]
        )
        + "\n"
    )
    # reply_archive.jsonl — real shape: author/is_maintainer/created_at/source_repo
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "author": "maintainer1", "is_maintainer": True, "created_at": "2026-08-02T10:00:00Z", "source_repo": "a/b"},
                {"id": "r2", "author": "user9", "is_maintainer": False, "created_at": "2026-08-03T10:00:00Z", "source_repo": "a/b"},
            ]
        )
        + "\n"
    )
    # star_log.jsonl
    (d / "star_log.jsonl").write_text(
        json.dumps({"count": 42, "logged_at": "2026-08-06T10:00:00Z"}) + "\n"
    )
    return d


def test_aggregate_engagement_counts_posted_comments(artifacts_dir: Path) -> None:
    agg = aggregate_engagement(artifacts_dir)
    # Only status=posted count as published comments (2 of 3).
    assert agg["comments_posted"] == 2


def test_aggregate_engagement_reply_stats(artifacts_dir: Path) -> None:
    agg = aggregate_engagement(artifacts_dir)
    assert agg["replies_received"] == 2
    assert agg["maintainer_replies"] == 1  # real, data-backed


def test_aggregate_engagement_no_fabricated_quality(artifacts_dir: Path) -> None:
    # There is NO quality-score data on disk -> the aggregate MUST NOT invent one.
    agg = aggregate_engagement(artifacts_dir)
    assert "avg_quality" not in agg


def test_aggregate_engagement_missing_dir(tmp_path: Path) -> None:
    # No artifacts dir -> zeros, not a crash.
    agg = aggregate_engagement(tmp_path / "nope")
    assert agg["comments_posted"] == 0
    assert agg["replies_received"] == 0


def test_aggregate_engagement_skips_bad_jsonl_lines(tmp_path: Path) -> None:
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "engagement_log.jsonl").write_text(
        json.dumps({"comment_id": "c1", "status": "posted"}) + "\nNOT JSON\n"
        + json.dumps({"comment_id": "c2", "status": "posted"}) + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["comments_posted"] == 2  # bad line skipped, not fatal


# ── Feed (recent signal digests + reports files) ─────────────────────────────


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    k = tmp_path / "Knowledge"
    (k / "Signals").mkdir(parents=True)
    (k / "Reports").mkdir(parents=True)
    (k / "Notes").mkdir(parents=True)
    (k / "Signals" / "2026-08-07-digest.md").write_text("# digest\nsignals")
    (k / "Signals" / "2026-08-06-weekly.md").write_text("# weekly")
    (k / "Reports" / "2026-08-06-community-report.md").write_text("# report")
    (k / "Notes" / "random.md").write_text("# not a signal/report")
    return k


def test_build_feed_only_signal_and_report_categories(knowledge_dir: Path) -> None:
    # Feed tab is COMMUNITY-scoped: only Signals + Reports, never Notes/other.
    items = build_feed(knowledge_dir)
    cats = {it["category"] for it in items}
    assert cats <= {"Signals", "Reports"}
    assert "Notes" not in cats


def test_build_feed_newest_first(knowledge_dir: Path) -> None:
    items = build_feed(knowledge_dir)
    assert len(items) == 3
    mtimes = [it["mtime"] for it in items]
    assert mtimes == sorted(mtimes, reverse=True)


def test_build_feed_carries_path_and_category(knowledge_dir: Path) -> None:
    items = build_feed(knowledge_dir)
    for it in items:
        assert it["path"].startswith("Knowledge/")
        assert it["category"] in {"Signals", "Reports"}


def test_build_feed_missing_knowledge(tmp_path: Path) -> None:
    assert build_feed(tmp_path / "nope") == []


def test_build_feed_drops_symlink_escaping_knowledge(tmp_path: Path) -> None:
    # A symlink under Signals/ pointing OUTSIDE Knowledge/ must NOT be emitted
    # (defense-in-depth vs the /workspace/file/resolve infoleak class).
    import os

    k = tmp_path / "Knowledge"
    (k / "Signals").mkdir(parents=True)
    (k / "Signals" / "real-digest.md").write_text("# real")
    outside = tmp_path / "secret.md"
    outside.write_text("# not for the feed")
    try:
        os.symlink(outside, k / "Signals" / "escape.md")
    except OSError:
        import pytest

        pytest.skip("symlinks not supported on this platform")
    items = build_feed(k)
    names = {it["name"] for it in items}
    assert "real-digest.md" in names
    assert "escape.md" not in names  # escaping symlink dropped


def test_read_jsonl_skips_oversized_file(tmp_path: Path) -> None:
    from core.community_data import _read_jsonl, _MAX_JSONL_BYTES

    d = tmp_path / ".artifacts"
    d.mkdir()
    big = d / "engagement_log.jsonl"
    # write just over the cap, cheaply (one line padded)
    big.write_text('{"x":"' + ("a" * (_MAX_JSONL_BYTES + 10)) + '"}\n')
    assert _read_jsonl(big) == []  # oversized → skipped, not OOM


# ── Router wiring (AC5 — endpoints reachable through the real ASGI app) ──────


def test_router_endpoints_registered() -> None:
    """The 3 GET endpoints must be wired into the app (import smoke — catches an
    unregistered router / bad prefix without a full HTTP round-trip)."""
    from routers.community_api import router

    paths = {r.path for r in router.routes}
    assert "/api/community/feed" in paths
    assert "/api/community/sources" in paths
    assert "/api/community/engagement" in paths


def test_router_handlers_return_real_shapes() -> None:
    """Call the handlers directly (they resolve live workspace paths via jobs/paths
    SSOT). Read-only — must return the documented shape and never raise, even if the
    live data is empty on a given machine.

    Uses a dedicated event loop that is fully restored afterward — `asyncio.run()`
    tears the thread's loop down and leaves NO current loop, which poisons any
    later test that calls the (deprecated) `asyncio.get_event_loop()` in the same
    process (test_orchestrator_surface_wiring). Save→new→run→close→restore keeps
    this test hermetic.
    """
    import asyncio

    from routers.community_api import (
        community_feed,
        community_sources,
        community_engagement,
    )

    try:
        prev_loop = asyncio.get_event_loop()
    except RuntimeError:
        prev_loop = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        feed = loop.run_until_complete(community_feed())
        assert "items" in feed and isinstance(feed["items"], list)
        assert "truncated" in feed and isinstance(feed["truncated"], bool)  # honest-truncation flag

        sources = loop.run_until_complete(community_sources())
        assert "sources" in sources and isinstance(sources["sources"], list)
        # every source (if any) carries managed_by — the coexistence field
        for s in sources["sources"]:
            assert "managed_by" in s

        eng = loop.run_until_complete(community_engagement())
        assert "comments_posted" in eng
        assert "avg_quality" not in eng  # no fabricated metric, even live
    finally:
        loop.close()
        # restore whatever loop policy state was here before (never leave None)
        asyncio.set_event_loop(prev_loop)
