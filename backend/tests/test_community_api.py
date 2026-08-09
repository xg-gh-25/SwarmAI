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
import yaml

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


def test_parse_sources_emits_members_and_accurate_count(tmp_path: Path) -> None:
    # B1: each source emits its editable string members + an ACCURATE member_count
    # (per the per-type MEMBER_KEY), not the urls-only source_count. source_count is
    # RETAINED for back-compat (existing frontend consumers).
    p = tmp_path / "c.yaml"
    p.write_text(
        "feeds:\n"
        "  - id: rss1\n    name: RSS1\n    type: rss\n    config:\n      urls: [https://a.com/f, https://b.com/f]\n"
        "  - id: hn1\n    name: HN1\n    type: hacker-news\n    config:\n      keywords: [llm, agent, rag]\n"
        "  - id: gh1\n    name: GH1\n    type: github-releases\n    config:\n      repos: [a/b, c/d, e/f, g/h]\n"
    )
    by_id = {s["id"]: s for s in parse_sources(p)}
    # rss: members are the urls
    assert by_id["rss1"]["members"] == ["https://a.com/f", "https://b.com/f"]
    assert by_id["rss1"]["member_count"] == 2
    assert by_id["rss1"]["member_kind"] == "urls"
    assert by_id["rss1"]["source_count"] == 2  # retained
    # hacker-news: members are the keywords (source_count would be 0 — old bug)
    assert by_id["hn1"]["members"] == ["llm", "agent", "rag"]
    assert by_id["hn1"]["member_count"] == 3
    assert by_id["hn1"]["member_kind"] == "keywords"
    # github-releases: members are the repos, member_count=4 (source_count was 0 before)
    assert by_id["gh1"]["member_count"] == 4
    assert by_id["gh1"]["member_kind"] == "repos"


def test_parse_sources_no_member_type_has_null_kind(tmp_path: Path) -> None:
    # A feed type with no editable string members (github-trending) → member_kind None,
    # members [], member_count 0 — the UI shows a "no editable members" state.
    p = tmp_path / "c.yaml"
    p.write_text(
        "feeds:\n  - id: gt\n    name: GT\n    type: github-trending\n    config:\n      spoken_language: en\n"
    )
    s = parse_sources(p)[0]
    assert s["member_kind"] is None
    assert s["members"] == []
    assert s["member_count"] == 0


def test_parse_sources_members_truncated_flag(tmp_path: Path) -> None:
    # A very long member list is capped in the payload with an honest truncated flag.
    from core.community_data import _MEMBER_CAP

    urls = [f"https://x{i}.com/f" for i in range(_MEMBER_CAP + 5)]
    p = tmp_path / "c.yaml"
    p.write_text(
        "feeds:\n  - id: big\n    name: Big\n    type: rss\n    config:\n      urls: ["
        + ", ".join(urls) + "]\n"
    )
    s = parse_sources(p)[0]
    assert len(s["members"]) == _MEMBER_CAP
    assert s["members_truncated"] is True
    assert s["member_count"] == _MEMBER_CAP + 5  # count is the TRUE total, not the capped len


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


def test_build_feed_excludes_internal_governance_reports(tmp_path: Path) -> None:
    # Gap A (A1): internal governance reports (ddd-weekly / pipeline-weekly /
    # swarmai-monthly / validator-audit) must NOT appear in the community feed,
    # while real community research reports ARE kept. Mirrors the real corpus.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    internal = [
        "2026-07-27-ddd-weekly.md", "2026-06-swarmai-monthly.md",
        "pipeline-weekly.md", "validator-check-usage-audit.md",
    ]
    community = [
        "2026-07-12-mattpocock-skills-deep-research.md",
        "2026-07-27-openworker-research.md",
        "2026-07-17-Understand-Anything-research.md",
    ]
    for n in internal + community:
        (k / "Reports" / n).write_text("# " + n)
    names = {it["name"] for it in build_feed(k)}
    for n in internal:
        assert n not in names, f"internal report leaked into feed: {n}"
    for n in community:
        assert n in names, f"community report wrongly excluded: {n}"


def test_build_feed_frontmatter_audience_exact_token_overrides(tmp_path: Path) -> None:
    # Gap A (A2): `audience: internal` force-EXCLUDES an otherwise-community-named
    # file; `audience: community` force-INCLUDES an otherwise-internal-named file.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-08-01-cool-research.md").write_text(
        "---\naudience: internal\n---\n# secret internal research"
    )
    (k / "Reports" / "2026-08-02-ddd-weekly.md").write_text(
        "---\naudience: community\n---\n# actually a community digest"
    )
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-01-cool-research.md" not in names  # audience:internal wins over community name
    assert "2026-08-02-ddd-weekly.md" in names          # audience:community wins over internal name


def test_build_feed_free_text_audience_is_ignored(tmp_path: Path) -> None:
    # Gap A (A2, the Gate-1 correction): a FREE-TEXT audience value must NOT be
    # treated as a classification signal — the gstack brief's audience literally
    # contains "internally" but is a community report and MUST stay shown.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-07-27-gstack-gbrain-research.md").write_text(
        '---\naudience: "how does it work internally + what do we steal"\n---\n# gstack'
    )
    names = {it["name"] for it in build_feed(k)}
    assert "2026-07-27-gstack-gbrain-research.md" in names  # free-text audience ignored → default SHOW


def test_build_feed_signals_never_filtered(tmp_path: Path) -> None:
    # Signals dir is pure community digests — never classified/excluded, even if a
    # signal file's name happened to collide with an internal pattern.
    k = tmp_path / "Knowledge"
    (k / "Signals").mkdir(parents=True)
    (k / "Signals" / "2026-08-07-ddd-weekly.md").write_text("# a signal digest")
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-07-ddd-weekly.md" in names  # Signals unfiltered


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


# ── Phase-2: Sources CRUD write endpoints ────────────────────────────────────


import asyncio

import jobs.config_io as config_io
from routers.community_api import (
    NewFeed, FeedPatch, add_feed, update_feed, delete_feed,
)
from fastapi import HTTPException


@pytest.fixture
def tmp_config_members(tmp_path: Path, monkeypatch) -> Path:
    """A tmp config.yaml with feeds carrying string members, for member-CRUD tests."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        config_io.CONFIG_HEADER
        + yaml.dump({"feeds": [
            {"id": "rssf", "name": "RSS", "type": "rss", "tier": "leaders", "enabled": True,
             "config": {"urls": ["https://a.com/feed", "https://b.com/feed"]}},
            {"id": "gtf", "name": "GT", "type": "github-trending", "enabled": True,
             "config": {"spoken_language": "en"}},
        ], "defaults": {}}, sort_keys=False)
    )
    monkeypatch.setattr(config_io, "CONFIG_FILE", cfg)
    return cfg


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch) -> Path:
    """A tmp config.yaml that the write endpoints target (monkeypatch the module
    default CONFIG_FILE that mutate_config uses when no path is passed)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        config_io.CONFIG_HEADER
        + yaml.dump({"feeds": [
            {"id": "existing", "name": "Existing", "type": "rss", "tier": "engineering", "enabled": True},
        ], "defaults": {}}, sort_keys=False)
    )
    monkeypatch.setattr(config_io, "CONFIG_FILE", cfg)
    return cfg


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        # Clear the stale closed loop so a later test's asyncio.get_event_loop()
        # doesn't inherit it (the Run-1 pollution class — Gate-2 red-team).
        asyncio.set_event_loop(None)


def test_add_feed_writes_managed_by_user(tmp_config: Path) -> None:
    _run(add_feed(NewFeed(id="new1", name="New", type="rss", tier="leaders")))
    data = yaml.safe_load(tmp_config.read_text())
    new = next(f for f in data["feeds"] if f["id"] == "new1")
    assert new["managed_by"] == "user"  # protected from self_tune auto-disable
    assert new["enabled"] is True
    assert new["tier"] == "leaders"


def test_add_feed_rejects_invalid_type(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(add_feed(NewFeed(id="x", name="X", type="not-a-real-type")))
    assert ei.value.status_code == 422


def test_add_feed_rejects_invalid_tier(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(add_feed(NewFeed(id="x", name="X", type="rss", tier="platinum")))
    assert ei.value.status_code == 422


def test_add_feed_rejects_duplicate_id(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(add_feed(NewFeed(id="existing", name="Dup", type="rss")))
    assert ei.value.status_code == 409  # feeds is a list — no silent dup


def test_update_feed_toggle_and_tier(tmp_config: Path) -> None:
    _run(update_feed("existing", FeedPatch(enabled=False, tier="frontier")))
    data = yaml.safe_load(tmp_config.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "existing")
    assert f["enabled"] is False
    assert f["tier"] == "frontier"
    assert f["managed_by"] == "user"  # edit takes ownership


def test_update_feed_404_missing(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(update_feed("ghost", FeedPatch(enabled=False)))
    assert ei.value.status_code == 404


def test_update_feed_422_invalid_tier(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(update_feed("existing", FeedPatch(tier="platinum")))
    assert ei.value.status_code == 422


def test_delete_feed_removes(tmp_config: Path) -> None:
    res = _run(delete_feed("existing"))
    assert res["removed"] is True
    data = yaml.safe_load(tmp_config.read_text())
    assert all(f["id"] != "existing" for f in data["feeds"])


def test_delete_feed_idempotent_missing(tmp_config: Path) -> None:
    # deleting a missing id is a 200 no-op, NOT a 500 — safe double-click/retry
    res = _run(delete_feed("ghost"))
    assert res["ok"] is True
    assert res["removed"] is False


def test_add_feed_yaml_special_chars_roundtrip(tmp_config: Path) -> None:
    # A feed id/name with YAML-special chars (colon, braces) must round-trip through
    # yaml.dump→safe_load without corrupting the file or the id (Gate-2 chaos).
    _run(add_feed(NewFeed(id="my:feed{1}", name="A: B [c]", type="rss")))
    data = yaml.safe_load(tmp_config.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "my:feed{1}")
    assert f["name"] == "A: B [c]"  # exact round-trip, not truncated at the colon


def test_config_io_write_if_skips_noop(tmp_path: Path) -> None:
    # write_if=False must SKIP the write (no mtime churn on a no-op tune).
    from jobs.config_io import mutate_config, CONFIG_HEADER

    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_HEADER + yaml.dump({"feeds": [{"id": "a"}]}, sort_keys=False))
    import os
    mtime_before = os.stat(cfg).st_mtime_ns
    # a no-op mutator gated to NOT write
    mutate_config(lambda c: None, config_path=cfg, write_if=lambda _c, _r: False)
    assert os.stat(cfg).st_mtime_ns == mtime_before  # file untouched


def test_user_feed_survives_self_tune_prune(tmp_config: Path) -> None:
    # A user-added feed round-trips a self_tune prune UNCHANGED (managed_by:user
    # is never auto-disabled — the coexistence contract).
    from jobs.self_tune import prune_unused_feeds

    _run(add_feed(NewFeed(id="mine", name="Mine", type="rss", tier="engineering")))
    config = yaml.safe_load(tmp_config.read_text())
    # prune with zero usage for every feed → only self-tune-managed feeds disable
    prune_unused_feeds(config, usage={}, min_days=0, dry_run=False)
    mine = next(f for f in config["feeds"] if f["id"] == "mine")
    assert mine["enabled"] is True  # user feed protected, not auto-disabled


# ── Phase-3: MEMBER_KEY map (B5 — every FeedType mapped, no drift) ───────────


def test_member_key_covers_every_feed_type() -> None:
    """B5: MEMBER_KEY must map EVERY FeedType member (to a config key or None).
    Adding a FeedType without a MEMBER_KEY entry FAILS here — prevents the
    silent parallel-enumeration drift Gate-1 warned about (run_b8306bd8)."""
    from jobs.models import FeedType, MEMBER_KEY

    assert set(MEMBER_KEY.keys()) == set(FeedType), (
        "MEMBER_KEY out of sync with FeedType: "
        f"missing={set(FeedType) - set(MEMBER_KEY)}, extra={set(MEMBER_KEY) - set(FeedType)}"
    )


def test_member_key_string_list_types_map_to_real_config_keys() -> None:
    """The string-member feed types map to the config key they actually use."""
    from jobs.models import FeedType, MEMBER_KEY

    assert MEMBER_KEY[FeedType.RSS] == "urls"
    assert MEMBER_KEY[FeedType.HACKER_NEWS] == "keywords"
    assert MEMBER_KEY[FeedType.WEB_SEARCH] == "queries"
    assert MEMBER_KEY[FeedType.GITHUB_RELEASES] == "repos"
    assert MEMBER_KEY[FeedType.WEIBO_TRENDING] == "keywords"
    assert MEMBER_KEY[FeedType.EASTMONEY_MARKET] == "concept_keywords"
    # No editable STRING members (trending.platforms are {id,name} dicts, not strings —
    # editing via the string-member path would corrupt the list + crash the adapter).
    assert MEMBER_KEY[FeedType.TRENDING] is None
    assert MEMBER_KEY[FeedType.GITHUB_TRENDING] is None
    assert MEMBER_KEY[FeedType.GITHUB_COMMUNITY] is None


# ── Phase-3: member-level CRUD endpoints (B2) ────────────────────────────────


def test_add_member_appends_and_stamps_user(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    _run(add_member("rssf", MemberBody(value="https://c.com/feed")))
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://c.com/feed" in f["config"]["urls"]
    assert f["managed_by"] == "user"  # editing a member takes ownership


def test_add_member_rejects_empty(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("rssf", MemberBody(value="   ")))
    assert ei.value.status_code == 422


def test_add_member_rejects_duplicate(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("rssf", MemberBody(value="https://a.com/feed")))
    assert ei.value.status_code == 409


def test_add_member_rejects_no_member_type(tmp_config_members: Path) -> None:
    # github-trending has no editable string members → 422, never a silent crash.
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("gtf", MemberBody(value="anything")))
    assert ei.value.status_code == 422


def test_add_member_missing_feed_404(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("ghost", MemberBody(value="x")))
    assert ei.value.status_code == 404


def test_delete_member_removes(tmp_config_members: Path) -> None:
    from routers.community_api import delete_member, MemberBody

    res = _run(delete_member("rssf", MemberBody(value="https://a.com/feed")))
    assert res["removed"] is True
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://a.com/feed" not in f["config"]["urls"]
    assert f["managed_by"] == "user"


def test_delete_member_idempotent_missing(tmp_config_members: Path) -> None:
    # deleting an absent member is a 200 no-op (not 500) — safe double-click.
    from routers.community_api import delete_member, MemberBody

    res = _run(delete_member("rssf", MemberBody(value="https://not-there.com")))
    assert res["ok"] is True
    assert res["removed"] is False


def test_add_member_slash_value_roundtrips(tmp_config_members: Path) -> None:
    # A member value with slashes (URL) round-trips (body param, not path param).
    from routers.community_api import add_member, MemberBody

    _run(add_member("rssf", MemberBody(value="https://x.com/a/b/c?d=e")))
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://x.com/a/b/c?d=e" in f["config"]["urls"]


def test_delete_member_body_arrives_over_http(tmp_config_members: Path) -> None:
    # MED (Gate-2): the value travels in the DELETE BODY (axios api.delete(url,{data})).
    # The direct-handler tests above bypass HTTP — this proves the body actually arrives
    # through the real ASGI stack (a stack/proxy that drops DELETE bodies would 422 here,
    # not silently no-op, since MemberBody is required). Guards the value-in-body contract.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.community_api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.request(
        "DELETE", "/api/community/feeds/rssf/members", json={"value": "https://a.com/feed"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["removed"] is True and body["value"] == "https://a.com/feed"
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://a.com/feed" not in f["config"]["urls"]


def test_member_write_serializes_with_self_tune_lock(tmp_config_members: Path) -> None:
    # B3: concurrent member writes all persist (shared sidecar flock). This is the
    # mutation-target — disabling the flock makes concurrent adds clobber (RED).
    import threading
    from routers.community_api import add_member, MemberBody

    def _add(i: int) -> None:
        _run(add_member("rssf", MemberBody(value=f"https://feed{i}.com/f")))

    threads = [threading.Thread(target=_add, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    for i in range(8):
        assert f"https://feed{i}.com/f" in f["config"]["urls"], f"member {i} clobbered — lock not serializing"


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
