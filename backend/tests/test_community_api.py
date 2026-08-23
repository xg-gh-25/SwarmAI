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

import datetime
import json
from pathlib import Path

import pytest
import yaml

from core.community_data import (
    parse_sources,
    aggregate_engagement,
    engagement_items,
    build_feed,
)

# A fixed "now" near the fixed-date fixtures below, so staleness (14d demotion in
# _needs_followup) is deterministic and does NOT drift with wall-clock. The engagement
# fixtures use 2026-08-02..09 timestamps; pin now to 2026-08-10 so those threads read
# as FRESH (<14d) — the pre-staleness behaviour the original assertions encode.
_FIXED_NOW = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)


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
    # member_count is the accurate per-type count (rss → urls) — replaced the old
    # urls-only source_count field (deleted: 0 readers, misleading for non-rss feeds).
    assert s["member_count"] == 2
    assert s["member_kind"] == "urls"
    assert "source_count" not in s  # dead field removed


def test_parse_sources_disabled_feed(config_file: Path) -> None:
    sources = parse_sources(config_file)
    by_id = {s["id"]: s for s in sources}
    assert by_id["hn-ai"]["enabled"] is False


def test_parse_sources_emits_members_and_accurate_count(tmp_path: Path) -> None:
    # B1: each source emits its editable string members + an ACCURATE member_count
    # (per the per-type MEMBER_KEY), replacing the urls-only source_count (deleted).
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
    assert "source_count" not in by_id["rss1"]  # dead field removed
    # hacker-news: members are the keywords (the old source_count would have been 0)
    assert by_id["hn1"]["members"] == ["llm", "agent", "rag"]
    assert by_id["hn1"]["member_count"] == 3
    assert by_id["hn1"]["member_kind"] == "keywords"
    # github-releases: members are the repos, member_count=4 (old source_count was 0)
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


def test_aggregate_engagement_maintainer_whitespace_padded(tmp_path: Path) -> None:
    # P8 door-parity (Gate-2 LOW): a whitespace-padded is_maintainer string must count,
    # matching the skill's _identity.is_external_maintainer which strips. Otherwise the
    # backend door and the skill door disagree on the SAME datum.
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "author": "extmaint", "is_maintainer": " true "},  # padded
                {"id": "r2", "author": "extmaint2", "is_maintainer": "1 "},      # padded
            ]
        )
        + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["maintainer_replies"] == 2  # both padded strings coerced true


# ── Engagement (GitHub_Community jsonl aggregation) ──────────────────────────


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".artifacts"
    d.mkdir()
    # engagement_log.jsonl — PRODUCTION shape: publish.py writes status="published"
    # on a successful post (community engine publish.py:136), NOT "posted". The old
    # fixture used "posted" — a value the writer never emits — which made the green
    # test mask the live comments_posted=0 bug (O009: a fixture encoding the wrong
    # assumption). issue_number + comment_url are real fields used by the Outbound list.
    (d / "engagement_log.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"comment_id": "c1", "status": "published", "posted_at": "2026-08-01T10:00:00Z", "repo": "a/b", "issue_number": 1, "topic": "memory", "confidence": 9, "comment_url": "https://github.com/a/b/issues/1#c1"},
                {"comment_id": "c2", "status": "published", "posted_at": "2026-08-05T10:00:00Z", "repo": "c/d", "issue_number": 2, "topic": "pipeline", "confidence": 8, "comment_url": "https://github.com/c/d/issues/2#c2"},
                {"comment_id": "c3", "status": "draft", "posted_at": "2026-08-06T10:00:00Z", "repo": "e/f", "issue_number": 3, "topic": "skills"},
            ]
        )
        + "\n"
    )
    # reply_archive.jsonl — real shape: author/is_maintainer/created_at/source_repo/
    # source_issue/body. Joins to engagement on (source_repo,source_issue)==(repo,issue_number).
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "author": "maintainer1", "is_maintainer": True, "created_at": "2026-08-02T10:00:00Z", "source_repo": "a/b", "source_issue": 1, "body": "great point, merged"},
                {"id": "r2", "author": "user9", "is_maintainer": False, "created_at": "2026-08-03T10:00:00Z", "source_repo": "a/b", "source_issue": 1, "body": "+1"},
            ]
        )
        + "\n"
    )
    # star_log.jsonl
    (d / "star_log.jsonl").write_text(
        json.dumps({"count": 42, "logged_at": "2026-08-06T10:00:00Z"}) + "\n"
    )
    return d


def test_aggregate_engagement_counts_published_comments(artifacts_dir: Path) -> None:
    agg = aggregate_engagement(artifacts_dir)
    # Only status=published count (2 of 3 — the draft is excluded). This is the
    # value publish.py actually writes; the pre-fix code matched "posted" → 0.
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
        json.dumps({"comment_id": "c1", "status": "published"}) + "\nNOT JSON\n"
        + json.dumps({"comment_id": "c2", "status": "published"}) + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["comments_posted"] == 2  # bad line skipped, not fatal


# ── Engagement items (Outbound clickable list — join engagement × replies) ───


def test_engagement_items_shape_and_join(artifacts_dir: Path) -> None:
    items = engagement_items(artifacts_dir, now=_FIXED_NOW)
    # 2 published comments become rows; the draft (c3) is KPI-only, not a row.
    assert len(items) == 2
    # a/b #1 received 2 replies (1 maintainer) → surfaced first (needs_followup).
    first = items[0]
    assert first["repo"] == "a/b" and first["issue_number"] == 1
    assert first["comment_url"] == "https://github.com/a/b/issues/1#c1"
    assert first["reply_count"] == 2
    assert first["has_maintainer_reply"] is True
    assert first["needs_followup"] is True
    assert {r["author"] for r in first["replies"]} == {"maintainer1", "user9"}
    assert any(r["is_maintainer"] and r["body"] == "great point, merged" for r in first["replies"])


def test_engagement_items_needs_followup_sorts_first(artifacts_dir: Path) -> None:
    items = engagement_items(artifacts_dir, now=_FIXED_NOW)
    # c/d #2 has no reply → not needs_followup → sorts AFTER a/b #1.
    assert items[0]["needs_followup"] is True
    assert items[1]["repo"] == "c/d" and items[1]["needs_followup"] is False


def test_engagement_items_cap_covers_real_history(tmp_path: Path) -> None:
    """run_897a4427: the item cap must be large enough that the FULL published
    history (incl. reconcile-backfilled rows) survives into the Outbound list, so a
    user who expands the 'handled' group sees ALL of it — not just the newest 200.
    The real corpus is ~294 published; a 200 cap silently dropped the ~31 oldest
    backfilled rows. This test writes 300 published rows and asserts none are
    truncated. Mutation-proof: reverting the cap to 200 makes this RED (300 → 200)."""
    d = tmp_path / ".artifacts"
    d.mkdir()
    log = d / "engagement_log.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        for i in range(300):
            f.write(json.dumps({
                "repo": "o/r", "issue_number": i, "comment_id": 10000 + i,
                "comment_url": f"https://github.com/o/r/issues/{i}#c{i}",
                "posted_at": f"2026-{1 + i % 9:02d}-15T00:00:00+00:00",
                "status": "published", "published": True,
            }) + "\n")
    items = engagement_items(d)
    assert len(items) == 300, (
        f"cap truncated real history to {len(items)} — backfilled history must "
        f"stay visible when handled is expanded")
    assert items[1]["replies"] == []


def test_engagement_items_excludes_unpublished(artifacts_dir: Path) -> None:
    items = engagement_items(artifacts_dir)
    # the draft engagement (e/f #3) must NOT appear as an actionable row.
    assert all(it["status"] == "published" for it in items)
    assert not any(it["repo"] == "e/f" for it in items)


def test_engagement_items_missing_dir(tmp_path: Path) -> None:
    assert engagement_items(tmp_path / "nope") == []


def _eng_reply_fixture(tmp_path: Path, replies: list[dict]) -> Path:
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "engagement_log.jsonl").write_text(
        json.dumps({"comment_id": "c1", "status": "published", "repo": "a/b", "issue_number": 1, "comment_url": "u1", "posted_at": "2026-08-01T00:00:00Z"}) + "\n"
    )
    (d / "reply_archive.jsonl").write_text("\n".join(json.dumps(r) for r in replies) + "\n")
    return d


def test_needs_followup_false_when_we_replied_last(tmp_path: Path) -> None:
    # A thread where OUR login (xg-gh-25) posted the LAST reply is handled — NOT
    # awaiting our response. This is the fix for the 72%-noise bug.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-02T00:00:00Z", "body": "q"},
        {"author": "xg-gh-25", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-03T00:00:00Z", "body": "our answer"},
    ])
    items = engagement_items(d, now=_FIXED_NOW)
    assert items[0]["reply_count"] == 2
    assert items[0]["needs_followup"] is False  # we replied last → handled


def test_needs_followup_true_when_other_replied_last(tmp_path: Path) -> None:
    d = _eng_reply_fixture(tmp_path, [
        {"author": "xg-gh-25", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-02T00:00:00Z", "body": "our comment"},
        {"author": "maintainer1", "is_maintainer": True, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-03T00:00:00Z", "body": "reply to us"},
    ])
    items = engagement_items(d, now=_FIXED_NOW)
    assert items[0]["needs_followup"] is True  # they replied last → we owe a response
    assert items[0]["has_maintainer_reply"] is True


def test_needs_followup_false_when_bot_replied_last(tmp_path: Path) -> None:
    d = _eng_reply_fixture(tmp_path, [
        {"author": "github-actions[bot]", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-02T00:00:00Z", "body": "triage"},
    ])
    items = engagement_items(d)
    assert items[0]["needs_followup"] is False  # a bot is not a human waiting on us


def test_needs_followup_ignores_reply_archive_order(tmp_path: Path) -> None:
    # Even if reply_archive is out of chronological order, the LATEST by created_at
    # decides — here 'other' is chronologically last despite being written first.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "other", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-09T00:00:00Z", "body": "later"},
        {"author": "xg-gh-25", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-05T00:00:00Z", "body": "earlier"},
    ])
    items = engagement_items(d, now=_FIXED_NOW)
    assert items[0]["needs_followup"] is True  # 'other' at 08-09 is the true last


def test_needs_followup_our_login_case_insensitive(tmp_path: Path) -> None:
    # Adversarial HIGH: our login may appear with different casing in reply_archive —
    # "XG-GH-25" must still count as US (handled), not a stranger awaiting a reply.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-02T00:00:00Z", "body": "q"},
        {"author": "XG-GH-25", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-03T00:00:00Z", "body": "our answer, upper-cased login"},
    ])
    items = engagement_items(d)
    assert items[0]["needs_followup"] is False  # case-insensitive: we replied last


def test_needs_followup_missing_created_at_on_our_last_reply(tmp_path: Path) -> None:
    # Adversarial HIGH: our reply has NO created_at (just-fetched). It must still sort
    # LAST (newest) so the thread reads as handled — not buried under the timestamped
    # human reply, which would falsely flag needs_followup.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-02T00:00:00Z", "body": "human q"},
        {"author": "xg-gh-25", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "body": "our answer, no timestamp"},
    ])
    items = engagement_items(d)
    assert items[0]["needs_followup"] is False  # our untimestamped reply sorts newest


# ── Self-login exclusion from the EXTERNAL-maintainer signal (DEFECT1) ────────
# is_maintainer is stored true for xg-gh-25's own replies on its OWN repo (bot is
# OWNER there). But every consumer wants the EXTERNAL-maintainer signal — a reply BY
# us is not an external maintainer's acknowledgment. Exclude self-login at every read.


def test_maintainer_replies_excludes_self_login(tmp_path: Path) -> None:
    # AC2: 3 is_maintainer=true rows, 1 is our own login → external count = 2, not 3.
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "author": "danielmiessler", "is_maintainer": True},
                {"id": "r2", "author": "Panniantong", "is_maintainer": True},
                {"id": "r3", "author": "xg-gh-25", "is_maintainer": True},  # OUR own reply on our repo
            ]
        )
        + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["maintainer_replies"] == 2  # xg-gh-25 self-reply excluded (was 3)


def test_maintainer_replies_excludes_self_login_case_insensitive(tmp_path: Path) -> None:
    # AC2: our login casing varies in the archive — must still be excluded.
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "reply_archive.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"id": "r1", "author": "external_maint", "is_maintainer": True},
                {"id": "r2", "author": "XG-GH-25", "is_maintainer": True},  # upper-cased self
            ]
        )
        + "\n"
    )
    agg = aggregate_engagement(d)
    assert agg["maintainer_replies"] == 1  # only the external one


def test_has_maintainer_reply_excludes_self_login(tmp_path: Path) -> None:
    # AC3: a thread whose ONLY is_maintainer reply is our own login must NOT be
    # flagged has_maintainer_reply — it is not external acknowledgment.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "xg-gh-25", "is_maintainer": True, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-03T00:00:00Z", "body": "our own reply on our repo"},
    ])
    items = engagement_items(d, now=_FIXED_NOW)
    assert items[0]["has_maintainer_reply"] is False  # self-maintainer excluded


def test_has_maintainer_reply_true_for_external_maintainer(tmp_path: Path) -> None:
    # AC3 companion: an EXTERNAL maintainer reply still flags has_maintainer_reply.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "danielmiessler", "is_maintainer": True, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-03T00:00:00Z", "body": "merged, thanks"},
    ])
    items = engagement_items(d, now=_FIXED_NOW)
    assert items[0]["has_maintainer_reply"] is True


# ── tier-0 uses LAST reply is external maintainer, not ANY (DEFECT2, FLAW-A) ──


def test_tier0_requires_last_reply_is_maintainer(tmp_path: Path) -> None:
    # AC5: an early external maintainer reply, then a later non-maintainer reply.
    # has_maintainer_reply is True (ANY), but the LAST voice is a non-maintainer, so
    # this must rank tier-1 (someone waiting), NOT tier-0 (maintainer waiting).
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    d_maintainer_last = _eng_reply_fixture(tmp_path / "a", [
        {"author": "randomuser", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-04T00:00:00Z", "body": "q"},
        {"author": "extmaint", "is_maintainer": True, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-05T00:00:00Z", "body": "maintainer replied last"},
    ])
    d_other_last = _eng_reply_fixture(tmp_path / "b", [
        {"author": "extmaint", "is_maintainer": True, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-04T00:00:00Z", "body": "maintainer early"},
        {"author": "randomuser", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-05T00:00:00Z", "body": "other replied last"},
    ])
    # Both are needs_followup (external last, fresh). The maintainer-last one is tier-0,
    # the other-last one is tier-1 — verified by which sorts first when we build a
    # combined view is not possible across dirs, so assert the tier signal directly:
    # a thread whose last reply is a non-maintainer must still be has_maintainer_reply
    # True but NOT rank as a maintainer-waiting row.
    items_a = engagement_items(d_maintainer_last, now=_FIXED_NOW)
    items_b = engagement_items(d_other_last, now=_FIXED_NOW)
    # both have a maintainer somewhere → has_maintainer_reply True on both
    assert items_a[0]["has_maintainer_reply"] is True
    assert items_b[0]["has_maintainer_reply"] is True
    # the tier-0 discriminator: last reply is an external maintainer
    assert items_a[0]["maintainer_waiting"] is True   # maintainer replied last
    assert items_b[0]["maintainer_waiting"] is False  # non-maintainer replied last


# ── staleness demotion: a >14d-old external reply is no longer active (DEFECT2) ──


def test_needs_followup_false_when_last_reply_stale(tmp_path: Path) -> None:
    # AC6: last external reply is 20 days before now → demoted (not an active todo).
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-07-21T00:00:00Z", "body": "old question"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)  # 20d later
    items = engagement_items(d, now=now)
    assert items[0]["needs_followup"] is False  # stale > 14d → demoted


def test_needs_followup_true_when_last_reply_fresh(tmp_path: Path) -> None:
    # AC6: last external reply is 2 days before now → still active.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-08-08T00:00:00Z", "body": "recent question"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)  # 2d later
    items = engagement_items(d, now=now)
    assert items[0]["needs_followup"] is True  # fresh < 14d → active


def test_needs_followup_stale_boundary_exactly_14d(tmp_path: Path) -> None:
    # AC6 boundary: exactly 14 days is still active (only STRICTLY older demotes).
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-07-27T00:00:00Z", "body": "14 days"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)  # exactly 14d
    items = engagement_items(d, now=now)
    assert items[0]["needs_followup"] is True  # 14d is the edge — not yet stale


def test_needs_followup_stale_missing_created_at_is_fresh(tmp_path: Path) -> None:
    # AC7: a last external reply with NO created_at must be treated as FRESH (never
    # false-demoted) — a just-fetched reply commonly lacks a timestamp.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "body": "no timestamp"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
    items = engagement_items(d, now=now)
    assert items[0]["needs_followup"] is True  # missing created_at → fresh, still active


def test_needs_followup_stale_unparseable_created_at_is_fresh(tmp_path: Path) -> None:
    # AC7: a naive/garbage created_at must NOT crash and must be treated as fresh.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "not-a-date", "body": "garbage ts"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
    items = engagement_items(d, now=now)  # must not raise
    assert items[0]["needs_followup"] is True  # unparseable → fresh


def test_needs_followup_stale_naive_created_at_no_crash(tmp_path: Path) -> None:
    # AC7: a NAIVE (no tz) created_at must not crash the aware-datetime subtraction.
    d = _eng_reply_fixture(tmp_path, [
        {"author": "someone", "is_maintainer": False, "source_repo": "a/b", "source_issue": 1, "created_at": "2026-07-21T00:00:00", "body": "naive ts, 20d old"},
    ])
    now = datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
    items = engagement_items(d, now=now)  # must not raise TypeError
    # naive is normalized to UTC and IS parseable → 20d old → stale → demoted
    assert items[0]["needs_followup"] is False


def test_engagement_items_none_keys_do_not_false_join(tmp_path: Path) -> None:
    # REVIEW HIGH: a reply missing source_issue must NOT cross-attach to a published
    # comment that also happens to be missing issue_number via a (repo, None) match.
    d = tmp_path / ".artifacts"
    d.mkdir()
    (d / "engagement_log.jsonl").write_text(
        json.dumps({"comment_id": "c1", "status": "published", "repo": "a/b", "issue_number": None, "comment_url": "u1"}) + "\n"
    )
    (d / "reply_archive.jsonl").write_text(
        # a reply on a/b but with a MISSING source_issue — must be dropped from the join
        json.dumps({"id": "r1", "author": "x", "is_maintainer": False, "source_repo": "a/b", "source_issue": None, "body": "unrelated"}) + "\n"
    )
    items = engagement_items(d)
    assert len(items) == 1
    assert items[0]["reply_count"] == 0  # NOT false-joined via (a/b, None)
    assert items[0]["needs_followup"] is False


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


def test_build_feed_reclassifies_weekly_in_signals_as_report(knowledge_dir: Path) -> None:
    # The community weekly report is written into Knowledge/Signals/ as
    # <date>-weekly.md (engine dual-writes). By folder it's a Signal, but it's a
    # REPORT — the Inbound tab must route it to the report card, NOT the daily signal
    # list. The knowledge_dir fixture has Signals/2026-08-06-weekly.md.
    items = build_feed(knowledge_dir)
    weekly = [it for it in items if it["name"] == "2026-08-06-weekly.md"]
    assert len(weekly) == 1
    assert weekly[0]["category"] == "Reports"  # reclassified by name, not folder
    # the daily digest stays a Signal
    digest = [it for it in items if it["name"] == "2026-08-07-digest.md"]
    assert digest and digest[0]["category"] == "Signals"


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


def test_build_feed_surfaces_community_weekly_html(tmp_path: Path) -> None:
    # gap1: the weekly report is dual-written to Knowledge/Reports/<date>-weekly.html
    # (report.py). The Feed used to glob *.md ONLY, so the .html weekly never surfaced.
    # A community weekly (stem "weekly") + the legacy "github-community-weekly-*" +
    # "community_report.html" MUST now appear.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-08-09-weekly.html").write_text("<!DOCTYPE html><html><body>weekly</body></html>")
    (k / "Reports" / "2026-05-30-github-community-weekly-w22.html").write_text("<!DOCTYPE html><html></html>")
    (k / "Reports" / "community_report.html").write_text("<!DOCTYPE html><html></html>")
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-09-weekly.html" in names, "community weekly .html must surface in Feed"
    assert "2026-05-30-github-community-weekly-w22.html" in names
    assert "community_report.html" in names


def test_build_feed_html_fail_closed_excludes_non_community(tmp_path: Path) -> None:
    # gap1 CRITICAL (Gate-1): HTML has NO frontmatter, so _report_audience can never
    # classify it internal — a bare *.html glob would leak confidential CMHK customer
    # reports + internal eval docs into the OUTWARD-facing Feed. HTML is fail-CLOSED:
    # DEFAULT-EXCLUDE unless the stem matches the community-report allowlist.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    leak = [
        "2026-07-19-cmhk-smb-deepdive.html",          # confidential customer financials
        "2026-07-19-cmhk-strategic-ipmm-review.html",
        "2026-05-18-bms-risk-intelligence.html",
        "2026-07-28-swarmai-vs-rocky-eval-scorecard.html",  # internal eval
        "2026-05-14-meshclaw-vs-swarmai-architecture.html",
        "report_fixed.html", "_tmp-section-1.html",   # junk/temp
    ]
    for n in leak:
        (k / "Reports" / n).write_text("<!DOCTYPE html><html><body>confidential</body></html>")
    names = {it["name"] for it in build_feed(k)}
    for n in leak:
        assert n not in names, f"NON-community HTML leaked into outward Feed: {n}"


def test_build_feed_html_audience_community_opt_in(tmp_path: Path) -> None:
    # An HTML report with an explicit <meta name="audience" content="community"> in its
    # head opts IN despite a non-community stem (the HTML analogue of the md frontmatter
    # audience:community override).
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-08-01-special-brief.html").write_text(
        '<!DOCTYPE html><html><head><meta name="audience" content="community"></head><body>x</body></html>'
    )
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-01-special-brief.html" in names


def test_build_feed_md_default_show_unchanged(tmp_path: Path) -> None:
    # Regression guard: the .md path keeps its DEFAULT-SHOW behavior (only HTML is
    # fail-closed). A community-named .md report with no frontmatter still surfaces.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-08-01-openworker-research.md").write_text("# community research")
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-01-openworker-research.md" in names


# ── Hot Topics — parse the LIVE signals.json hot_topics feed (run_ced271e8) ──────
# Was: parse a hand-maintained TECH.md Rankings table (went 2 months stale). Now:
# read the weekly monitor's signals.json, the same artifact backing daily signals.

import json as _json


def _write_signals(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "signals.json"
    p.write_text(_json.dumps(payload))
    return p


# A realistic signals.json shaped exactly like the live producer (compute_hot_topics):
# hot_topics[] = {id, total_comments, thread_count, top_thread{repo,number,title,...}}.
# Deliberately NOT pre-sorted by total_comments, to prove the parser sorts (H1).
_SIGNALS_FIXTURE = {
    "scanned_at": "2026-08-11T17:50:56.037632",
    "hot_topics": [
        {"id": "HT-COORDINATION", "total_comments": 7, "thread_count": 8,
         "top_thread": {"repo": "anthropics/claude-code-action", "number": 1367,
                        "title": "Workflow keyword trigger collides", "category": "General"}},
        {"id": "HT-SKILL-ARCH", "total_comments": 15, "thread_count": 18,
         "top_thread": {"repo": "danielmiessler/Personal_AI_Infrastructure", "number": 1613,
                        "title": "Surviving upgrades with a local delta", "category": "Ideas"}},
        {"id": "HT-MEMORY", "total_comments": 13, "thread_count": 4,
         "top_thread": {"repo": "MemPalace/mempalace", "number": 759,
                        "title": "MemPalace needs to be able to forget", "category": "Ideas"}},
    ],
}


def test_parse_hot_topics_reads_signals_and_ranks_by_comments(tmp_path: Path) -> None:
    from core.community_data import parse_hot_topics
    result = parse_hot_topics(_write_signals(tmp_path, _SIGNALS_FIXTURE))
    assert result["scanned_at"] == "2026-08-11T17:50:56.037632"
    topics = result["topics"]
    assert len(topics) == 3
    # H1: sorted by comments DESC + rank assigned from that order (fixture was unsorted).
    assert [t["comments"] for t in topics] == [15, 13, 7]
    assert [t["rank"] for t in topics] == [1, 2, 3]
    assert topics[0]["id"] == "HT-SKILL-ARCH"
    # id → human label (closed-set map), NOT the raw id.
    assert topics[0]["topic"] == "Skill / capability architecture"
    assert topics[0]["threads"] == 18
    assert topics[0]["top_repo"] == "danielmiessler/Personal_AI_Infrastructure"
    assert topics[0]["top_number"] == 1613


def test_parse_hot_topics_builds_discussions_url_not_issues(tmp_path: Path) -> None:
    # H2: hot_topics top_thread is a DISCUSSION (fetch_hot_discussions feeds
    # compute_hot_topics), so the URL path is /discussions/<n>, NEVER /issues/<n>.
    from core.community_data import parse_hot_topics
    topics = parse_hot_topics(_write_signals(tmp_path, _SIGNALS_FIXTURE))["topics"]
    top = topics[0]
    assert top["url"] == "https://github.com/danielmiessler/Personal_AI_Infrastructure/discussions/1613"
    assert "/issues/" not in top["url"]


def test_parse_hot_topics_accepts_github_explicit_url_and_guards_missing(tmp_path: Path) -> None:
    from core.community_data import parse_hot_topics
    payload = {"scanned_at": "2026-08-11T00:00:00", "hot_topics": [
        {"id": "HT-MEMORY", "total_comments": 5, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": 9,
                        "url": "https://github.com/a/b/discussions/9"}},  # valid github explicit → kept
        {"id": "HT-STREAMING", "total_comments": 2, "thread_count": 1,
         "top_thread": {"title": "no repo/number here"}},  # → empty url, still renders
    ]}
    topics = parse_hot_topics(_write_signals(tmp_path, payload))["topics"]
    by_id = {t["id"]: t for t in topics}
    assert by_id["HT-MEMORY"]["url"] == "https://github.com/a/b/discussions/9"  # github explicit preferred
    assert by_id["HT-STREAMING"]["url"] == ""                                    # missing repo/number → empty
    assert by_id["HT-STREAMING"]["topic"]                                        # row still has a label


def test_parse_hot_topics_url_is_hardened_against_injection(tmp_path: Path) -> None:
    # SECURITY (Gate-2): the URL is handed to the system browser. signals.json is a
    # local file a future producer / local edit could taint. A non-github explicit
    # url, a javascript:/file: scheme, or a path-traversal repo must NEVER reach the
    # browser as-is: drop the tainted explicit + reject a malformed repo → empty url.
    from core.community_data import parse_hot_topics
    payload = {"scanned_at": "2026-08-11T00:00:00", "hot_topics": [
        {"id": "HT-A", "total_comments": 9, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": 1, "url": "https://evil.com/phish"}},   # non-github explicit → drop, reconstruct
        {"id": "HT-B", "total_comments": 8, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": 2, "url": "javascript:alert(1)"}},      # dangerous scheme → drop, reconstruct
        {"id": "HT-C", "total_comments": 7, "thread_count": 1,
         "top_thread": {"repo": "../evil.com", "number": 3}},                            # path-traversal repo → empty
        {"id": "HT-D", "total_comments": 6, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": "3; rm -rf"}},                          # non-int number → empty
        {"id": "HT-E", "total_comments": 5, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": 5,
                        "url": "https://github.com/@evil/discussions/1"}},               # @-userinfo-ish path → drop explicit, reconstruct
        {"id": "HT-F", "total_comments": 4, "thread_count": 1,
         "top_thread": {"repo": "a/b", "number": 6,
                        "url": "https://github.com.evil.com/x/discussions/1"}},          # look-alike host → drop explicit, reconstruct
    ]}
    by_id = {t["id"]: t for t in parse_hot_topics(_write_signals(tmp_path, payload))["topics"]}
    assert by_id["HT-A"]["url"] == "https://github.com/a/b/discussions/1"  # tainted explicit dropped, safe reconstruct
    assert by_id["HT-B"]["url"] == "https://github.com/a/b/discussions/2"  # javascript: dropped, safe reconstruct
    assert by_id["HT-C"]["url"] == ""                                       # path-traversal repo (..) rejected
    assert by_id["HT-D"]["url"] == ""                                       # non-int number rejected
    assert by_id["HT-E"]["url"] == "https://github.com/a/b/discussions/5"  # @-path explicit dropped, safe reconstruct
    assert by_id["HT-F"]["url"] == "https://github.com/a/b/discussions/6"  # look-alike host dropped, safe reconstruct


def test_build_feed_html_prefix_leak_guard(tmp_path: Path) -> None:
    # Gate-2 HIGH (run_03b5d04f): the stem allowlist must be EXACT for "weekly"/
    # "community_report", NOT a prefix — else a future confidential
    # <date>-weekly-cmhk-financials.html / community-cmhk-secret.html would leak.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    must_show = ["2026-08-09-weekly.html", "community_report.html",
                 "2026-05-30-github-community-weekly-w22.html"]
    must_hide = ["2026-08-09-weekly-cmhk-secret.html", "weekly-cmhk-financials.html",
                 "community-cmhk-secret.html", "weekly-report-internal.html"]
    for n in must_show + must_hide:
        (k / "Reports" / n).write_text("<!DOCTYPE html><html></html>")
    names = {it["name"] for it in build_feed(k)}
    for n in must_show:
        assert n in names, f"legit community weekly wrongly hidden: {n}"
    for n in must_hide:
        assert n not in names, f"prefix-leak: confidential-looking HTML surfaced: {n}"


def test_build_feed_html_meta_audience_reversed_order(tmp_path: Path) -> None:
    # Gate-2 LOW: <meta content="internal" name="audience"> (reversed attr order)
    # must still force-EXCLUDE, even on a community-stem file.
    k = tmp_path / "Knowledge"
    (k / "Reports").mkdir(parents=True)
    (k / "Reports" / "2026-08-09-weekly.html").write_text(
        '<!DOCTYPE html><html><head><meta content="internal" name="audience"></head></html>'
    )
    names = {it["name"] for it in build_feed(k)}
    assert "2026-08-09-weekly.html" not in names, "reversed-order audience:internal must exclude"


def test_parse_hot_topics_missing_hot_topics_key_fail_soft(tmp_path: Path) -> None:
    # A valid signals.json that has no hot_topics list (e.g. an old scan) → empty
    # topics but still surface scanned_at (never silently drop the freshness).
    from core.community_data import parse_hot_topics
    result = parse_hot_topics(_write_signals(tmp_path, {"scanned_at": "2026-08-11T00:00:00", "signals": []}))
    assert result == {"scanned_at": "2026-08-11T00:00:00", "topics": []}


def test_parse_hot_topics_missing_file_fail_soft(tmp_path: Path) -> None:
    from core.community_data import parse_hot_topics
    result = parse_hot_topics(tmp_path / "nope.json")
    assert result == {"scanned_at": None, "topics": []}


def test_parse_hot_topics_malformed_json_fail_soft(tmp_path: Path) -> None:
    # A corrupt/half-written signals.json must not 500 the endpoint.
    from core.community_data import parse_hot_topics
    p = tmp_path / "signals.json"
    p.write_text("{not valid json,,,")
    result = parse_hot_topics(p)
    assert result == {"scanned_at": None, "topics": []}


def test_parse_hot_topics_skips_malformed_entries(tmp_path: Path) -> None:
    # A malformed row (missing id, not a dict) is skipped, never fatal.
    from core.community_data import parse_hot_topics
    payload = {"scanned_at": "2026-08-11T00:00:00", "hot_topics": [
        "not a dict",
        {"total_comments": 9},  # no id → skip
        {"id": "HT-MEMORY", "total_comments": 4, "thread_count": 2,
         "top_thread": {"repo": "a/b", "number": 1}},
    ]}
    topics = parse_hot_topics(_write_signals(tmp_path, payload))["topics"]
    assert len(topics) == 1
    assert topics[0]["id"] == "HT-MEMORY"


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


# ── F4: add_feed must apply the SAME url validation as add_member ─────────────
# The member route SSRF-checks config.urls (_validate_member); add_feed wrote
# config verbatim → a private/metadata/non-http(s) URL could be persisted via the
# feed route, bypassing the guard (run_36d8ba1c). Both write paths must agree.

def test_add_feed_rejects_ssrf_metadata_url_in_config(tmp_config: Path) -> None:
    # The exact bypass payload: an AWS metadata IP as an rss url in the config blob.
    with pytest.raises(HTTPException) as ei:
        _run(add_feed(NewFeed(id="ssrf", name="S", type="rss",
                              config={"urls": ["http://169.254.169.254/latest/meta-data/"]})))
    assert ei.value.status_code == 422
    # and it must NOT have been written
    data = yaml.safe_load(tmp_config.read_text())
    assert all(f["id"] != "ssrf" for f in data["feeds"])


def test_add_feed_rejects_non_http_scheme_url(tmp_config: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(add_feed(NewFeed(id="fileurl", name="F", type="rss",
                              config={"urls": ["file:///etc/passwd"]})))
    assert ei.value.status_code == 422


def test_add_feed_accepts_valid_public_urls(tmp_config: Path) -> None:
    _run(add_feed(NewFeed(id="okurls", name="OK", type="rss",
                          config={"urls": ["https://example.com/feed.xml"]})))
    data = yaml.safe_load(tmp_config.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "okurls")
    assert f["config"]["urls"] == ["https://example.com/feed.xml"]


def test_add_feed_no_urls_key_unaffected(tmp_config: Path) -> None:
    # A non-url feed (keywords) or a feed with no urls key must still succeed.
    _run(add_feed(NewFeed(id="kw", name="KW", type="hacker-news",
                          config={"keywords": ["agent"]})))
    data = yaml.safe_load(tmp_config.read_text())
    assert any(f["id"] == "kw" for f in data["feeds"])


def test_add_feed_non_list_urls_does_not_crash(tmp_config: Path) -> None:
    # Defensive: a malformed config where urls is not a list must not crash the
    # validator (it should skip url validation, letting the value through as-is).
    _run(add_feed(NewFeed(id="weird", name="W", type="rss", config={"urls": "notalist"})))
    data = yaml.safe_load(tmp_config.read_text())
    assert any(f["id"] == "weird" for f in data["feeds"])


# ── F6: github_community must set RawSignal.published (not the dropped published_at) ──
# github_community.py:119 passed published_at=created_at → RawSignal has field
# `published` (Pydantic extra=ignore dropped the kwarg) → every signal had
# published=None, deprioritized in signal_digest's per-tier newest-first sort.

def test_github_community_parse_created_valid_iso() -> None:
    from datetime import datetime, timezone
    from jobs.adapters.github_community import _parse_created
    got = _parse_created("2024-01-15T10:30:00Z")
    assert isinstance(got, datetime)
    assert got == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def test_github_community_parse_created_failsafe_none() -> None:
    from jobs.adapters.github_community import _parse_created
    assert _parse_created("") is None
    assert _parse_created(None) is None
    assert _parse_created("not-a-date") is None  # must not raise → scan never crashes


def test_github_community_signal_carries_published(monkeypatch) -> None:
    # E2E of the fix: a fake gh issue must produce a RawSignal whose `published`
    # is the parsed datetime — proving the kwarg reaches the field (not dropped).
    from datetime import datetime, timezone
    import jobs.adapters.github_community as gc
    from jobs.models import Feed

    fake_issues = [{"title": "New agent framework released", "number": 7,
                    "html_url": "https://github.com/o/r/issues/7", "comments": 3,
                    "created_at": "2024-01-15T10:30:00Z"}]
    monkeypatch.setattr(gc, "_run_gh", lambda args, timeout=20: json.dumps(fake_issues))
    monkeypatch.setattr(gc, "_match_topics", lambda text: ["agent"])
    feed = Feed(id="ghc", name="GHC", type="github-community", config={"repos": ["o/r"]})
    signals = gc.fetch_github_community(feed)
    assert signals, "expected at least one signal"
    assert signals[0].published == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


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


def test_member_key_edits_the_same_list_the_adapter_reads() -> None:
    """The member editor must edit the SAME list the fetch adapter consumes — proven by
    a ROUND-TRIP, not a hardcoded key string (the prior version hardcoded is-None and
    LOCKED the github-community bug; C044 test-theater). For each feed type, we assert
    `feed_members(cfg, type)` returns exactly the string list an adapter reads from that
    type's documented config key. A wrong MEMBER_KEY (e.g. github-community=None) makes
    feed_members return [] for a feed that HAS members → RED.

    `EDITABLE` = the config key each adapter actually reads members from (verified live:
    github_community.py:83 & github_releases → config.repos; rss → config.urls;
    hacker_news/weibo → config.keywords; eastmoney → config.concept_keywords).
    `NON_EDITABLE` = types with no flat-string member list (github-trending: only
    scalars; trending: platforms are {id,name} DICTS — editing as strings would corrupt
    the list + crash the adapter's .get()). NOTE: we do NOT construct a RawSignal here —
    feed_members only needs the config dict. (The github_community published_at-vs-
    published mismatch this once noted as latent is now FIXED — run_36d8ba1c; see
    test_github_community_signal_carries_published.)
    """
    from jobs.models import FeedType, MEMBER_KEY
    from core.community_data import feed_members

    EDITABLE = {
        FeedType.RSS: "urls",
        FeedType.HACKER_NEWS: "keywords",
        FeedType.GITHUB_RELEASES: "repos",
        FeedType.GITHUB_COMMUNITY: "repos",   # #1: repos is a flat str list, same as github-releases
        FeedType.WEIBO_TRENDING: "keywords",
        FeedType.EASTMONEY_MARKET: "concept_keywords",
    }
    NON_EDITABLE = {FeedType.GITHUB_TRENDING, FeedType.TRENDING, FeedType.WEB_SEARCH}

    sample = ["alpha", "beta/gamma"]
    for ftype, key in EDITABLE.items():
        # MEMBER_KEY must name this adapter's real member key...
        assert MEMBER_KEY[ftype] == key, f"{ftype.value}: MEMBER_KEY={MEMBER_KEY[ftype]!r}, adapter reads config.{key!r}"
        # ...and feed_members must round-trip the list the adapter would read.
        assert feed_members({key: list(sample)}, ftype.value) == sample, (
            f"{ftype.value}: member editor does NOT edit the list the adapter reads"
        )
    for ftype in NON_EDITABLE:
        assert MEMBER_KEY[ftype] is None, f"{ftype.value} should have no editable string members"
        assert feed_members({"anything": ["x"]}, ftype.value) == []


# ── Phase-3: member-level CRUD endpoints (B2) ────────────────────────────────


def test_add_member_appends_and_stamps_user(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    _run(add_member("rssf", MemberBody(value="https://c.com/feed")))
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://c.com/feed" in f["config"]["urls"]
    assert f["managed_by"] == "user"  # editing a member takes ownership


def test_add_member_rejects_invalid_url_for_rss(tmp_config_members: Path) -> None:
    # #8 strict: an rss feed's members are urls — a non-URL value ("hello world") would
    # land in config.urls and silently fail at fetch time. Reject 422 up front.
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("rssf", MemberBody(value="hello world")))
    assert ei.value.status_code == 422


def test_add_member_accepts_valid_url_for_rss(tmp_config_members: Path) -> None:
    from routers.community_api import add_member, MemberBody

    _run(add_member("rssf", MemberBody(value="https://valid.com/feed.xml")))
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert "https://valid.com/feed.xml" in f["config"]["urls"]


def test_add_member_rejects_invalid_repo_shape(tmp_config_members: Path, monkeypatch) -> None:
    # #8: a github-releases/github-community member must be owner/repo shape.
    import jobs.config_io as cio
    cfg = tmp_config_members
    data = yaml.safe_load(cfg.read_text())
    data["feeds"].append({"id": "ghr", "name": "GHR", "type": "github-releases", "enabled": True, "config": {"repos": ["a/b"]}})
    cfg.write_text(cio.CONFIG_HEADER + yaml.dump(data, sort_keys=False))
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("ghr", MemberBody(value="noslash")))
    assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei2:
        _run(add_member("ghr", MemberBody(value="too/many/slashes")))
    assert ei2.value.status_code == 422


def test_add_member_accepts_valid_repo(tmp_config_members: Path) -> None:
    data = yaml.safe_load(tmp_config_members.read_text())
    import jobs.config_io as cio
    data["feeds"].append({"id": "ghr2", "name": "GHR2", "type": "github-releases", "enabled": True, "config": {"repos": []}})
    tmp_config_members.write_text(cio.CONFIG_HEADER + yaml.dump(data, sort_keys=False))
    from routers.community_api import add_member, MemberBody

    _run(add_member("ghr2", MemberBody(value="owner/repo")))
    got = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in got["feeds"] if x["id"] == "ghr2")
    assert "owner/repo" in f["config"]["repos"]


def test_add_member_keyword_is_free_text(tmp_config_members: Path) -> None:
    # #8: keywords are free-text — any non-empty value is valid (no URL/shape rule).
    data = yaml.safe_load(tmp_config_members.read_text())
    import jobs.config_io as cio
    data["feeds"].append({"id": "hnf", "name": "HN", "type": "hacker-news", "enabled": True, "config": {"keywords": []}})
    tmp_config_members.write_text(cio.CONFIG_HEADER + yaml.dump(data, sort_keys=False))
    from routers.community_api import add_member, MemberBody

    _run(add_member("hnf", MemberBody(value="context engineering")))  # spaces OK for keywords
    got = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in got["feeds"] if x["id"] == "hnf")
    assert "context engineering" in f["config"]["keywords"]


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
    assert "/api/community/hot-topics" in paths  # gap2


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
        community_hot_topics,
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
        # New contract: {kpis:{...counts...}, items:[...list...]}.
        assert "kpis" in eng and "items" in eng
        assert "comments_posted" in eng["kpis"]
        assert isinstance(eng["items"], list)
        assert "avg_quality" not in eng["kpis"]  # no fabricated metric, even live

        hot = loop.run_until_complete(community_hot_topics())
        assert "topics" in hot and isinstance(hot["topics"], list)
        assert "scanned_at" in hot  # freshness anchor (ISO scan time; None if no signals.json)
        assert "updated" not in hot  # old TECH.md-date field is gone (migrated to scanned_at)
        assert hot["count"] == len(hot["topics"])
    finally:
        loop.close()
        # restore whatever loop policy state was here before (never leave None)
        asyncio.set_event_loop(prev_loop)


# ── SSRF hygiene: _validate_member must reject private/link-local/metadata IPs (③) ──
# The urls-branch of _validate_member previously accepted ANY http(s) URL with a
# netloc — so http://169.254.169.254/ (AWS/GCP metadata) or http://10.0.0.1/ landed
# in config.yaml `urls:`. The downstream RSS fetch (jobs.adapters.http_client) already
# egress-guards, so this is write-time hygiene / defense-in-depth. The check must use
# urlparse().hostname (NOT .netloc) — .netloc keeps :port/[ipv6]/user@ and fails the
# IP parse, letting http://169.254.169.254:8080/ SLIP THROUGH (Gate-1 finding).

@pytest.mark.parametrize("bad_url", [
    "http://169.254.169.254/latest/meta-data/",   # link-local metadata (bare)
    "http://169.254.169.254:8080/latest/",        # link-local + PORT (the .netloc bypass)
    "https://10.0.0.1/feed.xml",                   # private class-A
    "http://192.168.1.1/rss",                      # private class-C
    "http://127.0.0.1:9000/feed",                  # loopback + port
    "http://user:pw@10.0.0.1/x",                   # userinfo-prefixed private
    "http://[fd00::1]/feed",                        # private IPv6
])
def test_add_member_rejects_ssrf_ip_url_for_rss(tmp_config_members: Path, bad_url: str) -> None:
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("rssf", MemberBody(value=bad_url)))
    assert ei.value.status_code == 422, f"{bad_url} should be rejected 422"


@pytest.mark.parametrize("ok_url", [
    "https://example.com/feed.xml",       # public hostname
    "https://blog.aws.amazon.com/rss",    # public hostname (https — http would dead-fetch)
    "https://8.8.8.8/feed",                # public IP literal (must still pass)
])
def test_add_member_accepts_public_url_for_rss(tmp_config_members: Path, ok_url: str) -> None:
    # The SSRF guard must NOT over-reach — public hosts + public IP literals still validate.
    from routers.community_api import add_member, MemberBody
    _run(add_member("rssf", MemberBody(value=ok_url)))
    data = yaml.safe_load(tmp_config_members.read_text())
    f = next(x for x in data["feeds"] if x["id"] == "rssf")
    assert ok_url in f["config"]["urls"]


def test_add_member_rejects_http_url_for_rss(tmp_config_members: Path) -> None:
    # http:// must be rejected at write time: the RSS egress guard is https-ONLY
    # (jobs.adapters.http_client._validate_egress), so an http URL passes no fetch — it
    # would be a dead subscription silently failing forever. Align write-gate ↔ fetch.
    from routers.community_api import add_member, MemberBody

    with pytest.raises(HTTPException) as ei:
        _run(add_member("rssf", MemberBody(value="http://blog.aws.amazon.com/rss")))
    assert ei.value.status_code == 422
    assert "https" in str(ei.value.detail).lower()
