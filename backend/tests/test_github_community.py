"""Tests for GitHub Community Engine — match scoring + admission gates."""

import pytest
from skills.s_github_community.scripts.match import (
    score_opportunity,
    check_repo_admission,
    check_topic_admission,
    Signal,
    RepoEntry,
    TopicEntry,
)


# --- Admission Gate: Source Matrix (4 conditions) ---


class TestRepoAdmission:
    """Source Matrix admission requires ALL 4 conditions."""

    def test_valid_repo_passes(self):
        repo = RepoEntry(
            name="NousResearch/hermes-agent",
            stars=154000,
            has_production_experience=True,
            active_last_30d=True,
            teaches_us=True,
        )
        assert check_repo_admission(repo) == (True, [])

    def test_no_experience_fails(self):
        repo = RepoEntry(
            name="crypto/trading-bot",
            stars=50000,
            has_production_experience=False,
            active_last_30d=True,
            teaches_us=True,
        )
        passed, reasons = check_repo_admission(repo)
        assert not passed
        assert "production_experience" in reasons[0]

    def test_low_stars_fails(self):
        repo = RepoEntry(
            name="nobody/tiny-repo",
            stars=500,
            has_production_experience=True,
            active_last_30d=True,
            teaches_us=True,
        )
        passed, reasons = check_repo_admission(repo)
        assert not passed
        assert "stars" in reasons[0]

    def test_inactive_fails(self):
        repo = RepoEntry(
            name="archived/old-framework",
            stars=10000,
            has_production_experience=True,
            active_last_30d=False,
            teaches_us=True,
        )
        passed, reasons = check_repo_admission(repo)
        assert not passed
        assert "active" in reasons[0]

    def test_nothing_to_learn_fails(self):
        repo = RepoEntry(
            name="yet-another/langchain-wrapper",
            stars=5000,
            has_production_experience=True,
            active_last_30d=True,
            teaches_us=False,
        )
        passed, reasons = check_repo_admission(repo)
        assert not passed
        assert "teaches" in reasons[0]


# --- Admission Gate: Topic Matrix (3 conditions) ---


class TestTopicAdmission:
    """Topic Matrix admission requires ALL 3 conditions."""

    def test_valid_topic_passes(self):
        topic = TopicEntry(
            id="T-MEM",
            name="Memory is the Moat",
            has_evidence=True,
            thesis_link="T1",
            active_repos_count=3,
        )
        assert check_topic_admission(topic) == (True, [])

    def test_no_evidence_fails(self):
        topic = TopicEntry(
            id="T-NEW",
            name="Some opinion",
            has_evidence=False,
            thesis_link="T1",
            active_repos_count=2,
        )
        passed, reasons = check_topic_admission(topic)
        assert not passed
        assert "evidence" in reasons[0]

    def test_no_thesis_link_fails(self):
        topic = TopicEntry(
            id="T-RANDOM",
            name="Best VS Code extensions",
            has_evidence=True,
            thesis_link=None,
            active_repos_count=3,
        )
        passed, reasons = check_topic_admission(topic)
        assert not passed
        assert "thesis" in reasons[0]

    def test_insufficient_repos_fails(self):
        topic = TopicEntry(
            id="T-NICHE",
            name="Very niche topic",
            has_evidence=True,
            thesis_link="T3",
            active_repos_count=1,
        )
        passed, reasons = check_topic_admission(topic)
        assert not passed
        assert "repos" in reasons[0]


# --- Scoring ---


class TestScoring:
    """Match scoring produces expected rankings."""

    def test_high_relevance_high_score(self):
        signal = Signal(
            repo="NousResearch/hermes-agent",
            issue_number=27339,
            title="Prompt Cache Invalidation",
            topic_relevance=5,
            expertise_depth=5,
            audience_reach=5,
            existing_comments=0,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=0,
            repo_comments_this_week=0,
        )
        score = score_opportunity(signal)
        assert score >= 30  # Passes threshold

    def test_first_responder_bonus(self):
        base = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=3,
            expertise_depth=3,
            audience_reach=3,
            existing_comments=5,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=0,
            repo_comments_this_week=0,
        )
        first_responder = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=3,
            expertise_depth=3,
            audience_reach=3,
            existing_comments=0,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=0,
            repo_comments_this_week=0,
        )
        assert score_opportunity(first_responder) > score_opportunity(base)

    def test_reply_to_us_always_high(self):
        signal = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=1,
            expertise_depth=1,
            audience_reach=1,
            existing_comments=20,
            is_reply_to_us=True,
            is_maintainer_issue=False,
            days_old=5,
            repo_comments_this_week=0,
        )
        score = score_opportunity(signal)
        assert score >= 30  # Reply bonus overrides low scores

    def test_anti_spam_kills_score(self):
        signal = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=5,
            expertise_depth=5,
            audience_reach=5,
            existing_comments=0,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=0,
            repo_comments_this_week=3,  # Over limit
        )
        score = score_opportunity(signal)
        assert score < 0  # Killed by anti-spam

    def test_stale_issue_penalized(self):
        fresh = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=4,
            expertise_depth=4,
            audience_reach=4,
            existing_comments=2,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=0,
            repo_comments_this_week=0,
        )
        stale = Signal(
            repo="test/repo",
            issue_number=1,
            title="test",
            topic_relevance=4,
            expertise_depth=4,
            audience_reach=4,
            existing_comments=2,
            is_reply_to_us=False,
            is_maintainer_issue=False,
            days_old=7,
            repo_comments_this_week=0,
        )
        assert score_opportunity(fresh) > score_opportunity(stale)


# --- Quality Gate ---


class TestQualityGate:
    """Quality gate blocks low-substance drafts."""

    def test_confidence_below_8_not_auto_publish(self):
        # This is tested at the workflow level in INSTRUCTIONS.md
        # Here we just verify the threshold constant
        from skills.s_github_community.scripts.match import AUTO_PUBLISH_THRESHOLD
        assert AUTO_PUBLISH_THRESHOLD == 8


# --- Publish Gate Tests ---


class TestPublishGate:
    """Quality gate enforces 4 conditions + anti-spam."""

    def test_low_confidence_blocked(self):
        from skills.s_github_community.scripts.publish import quality_gate
        passed, reason = quality_gate(confidence=5, repo="test/repo", body="x" * 200 + "```code```\n---\n*[SwarmAI](link)*")
        assert not passed
        assert "confidence" in reason

    def test_high_confidence_passes(self):
        from skills.s_github_community.scripts.publish import quality_gate
        body = "Here's our approach with 5000:1 cache ratio:\n```python\ntools = sorted(tools)\n```\nThis gives 42% improvement.\n\n---\n*[SwarmAI](https://github.com/xg-gh-25/SwarmAI)*"
        passed, reason = quality_gate(confidence=9, repo="test/repo", body=body)
        assert passed
        assert reason == "passed"

    def test_no_substance_blocked(self):
        from skills.s_github_community.scripts.publish import quality_gate
        body = "I agree with this approach, it makes a lot of sense for the use case described above and I think many people would benefit from it.\n\n---\n*[SwarmAI](link)*"
        passed, reason = quality_gate(confidence=9, repo="test/repo", body=body)
        assert not passed
        assert "no_substance" in reason

    def test_no_footer_blocked(self):
        from skills.s_github_community.scripts.publish import quality_gate
        body = "Here's our production approach with code:\n```python\ntools = sorted(tools, key=lambda t: t['name'])\n```\nThis gives us a 5000:1 cache hit ratio. The key insight is deterministic ordering."
        passed, reason = quality_gate(confidence=9, repo="test/repo", body=body)
        assert not passed
        assert "no_footer" in reason

    def test_too_short_blocked(self):
        from skills.s_github_community.scripts.publish import quality_gate
        passed, reason = quality_gate(confidence=9, repo="test/repo", body="short")
        assert not passed
        assert "too_short" in reason


# --- FOLD: fold_patterns.fold_section (curation) ---

_FOLD_DOC = """\
# GitHub_Community — Improvement Log

## What Worked
- seed insight A (do not touch)

## What Failed
- failed thing B (do not touch)

## Patterns Discovered

- **Curated pattern one** — hand-written, keep.
- **Curated pattern two** — also keep.
- [auto 2026-05-01] New engagement pattern from a/b — old1
- [auto 2026-05-02] New engagement pattern from a/c — old2
- [auto 2026-06-20] New engagement pattern from d/e — recent1
- [auto 2026-06-21] New engagement pattern from f/g — recent2

## Publishing Rule
- mirror to repo (do not touch)
"""


class TestFoldSection:
    def _fold(self, keep_recent=2):
        from datetime import date
        from skills.s_github_community.scripts.fold_patterns import fold_section
        return fold_section(_FOLD_DOC, "Patterns Discovered",
                            keep_recent=keep_recent, today=date(2026, 6, 25))

    def test_keeps_all_curated(self):
        r = self._fold()
        assert "Curated pattern one" in r.new_content
        assert "Curated pattern two" in r.new_content
        assert r.curated_kept == 2

    def test_keeps_recent_auto_archives_old(self):
        r = self._fold(keep_recent=2)
        # 2 most-recent auto kept in doc
        assert "recent1" in r.new_content and "recent2" in r.new_content
        # 2 oldest auto moved out of doc
        assert "old1" not in r.new_content and "old2" not in r.new_content
        assert r.archived_count == 2
        assert any("old1" in b for b in r.archived_bullets)

    def test_sibling_sections_byte_identical(self):
        # NEGATIVE: only Patterns Discovered may change.
        r = self._fold()
        for marker in ["## What Worked\n- seed insight A (do not touch)",
                       "## What Failed\n- failed thing B (do not touch)",
                       "## Publishing Rule\n- mirror to repo (do not touch)"]:
            assert marker in r.new_content, f"section drifted: {marker!r}"

    def test_pointer_line_added(self):
        r = self._fold()
        assert "folded to IMPROVEMENT-archive.md" in r.new_content

    def test_absent_section_is_noop(self):
        from datetime import date
        from skills.s_github_community.scripts.fold_patterns import fold_section
        r = fold_section(_FOLD_DOC, "Nonexistent Section", today=date(2026, 6, 25))
        assert r.new_content == _FOLD_DOC
        assert r.archived_count == 0


# --- FIX: cultivate._apply_ddd_updates dedup + cap (治本) ---

class TestApplyDddUpdatesDedupCap:
    def _setup(self, tmp_path, monkeypatch, initial_md):
        import skills.s_github_community.scripts.cultivate as cult
        monkeypatch.setattr(cult, "DDD_DIR", tmp_path)
        (tmp_path / "IMPROVEMENT.md").write_text(initial_md)
        return cult

    def _count_auto(self, tmp_path):
        import re
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        return len(re.findall(r"^- \[auto ", t, re.M))

    def test_dedup_same_section_action_once(self, tmp_path, monkeypatch):
        cult = self._setup(tmp_path, monkeypatch,
                           "## Patterns Discovered\n\n- seed\n")
        upd = [{"target": "IMPROVEMENT.md", "section": "Patterns Discovered",
                "action": "New engagement pattern from a/b", "content_preview": "x"}]
        cult._apply_ddd_updates(upd)
        cult._apply_ddd_updates(upd)  # same (section, action) again
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        assert t.count("New engagement pattern from a/b") == 1

    def test_cap_keeps_max_auto_entries(self, tmp_path, monkeypatch):
        from skills.s_github_community.scripts.cultivate import MAX_AUTO_ENTRIES
        cult = self._setup(tmp_path, monkeypatch,
                           "## Patterns Discovered\n\n- seed\n")
        for i in range(50):
            cult._apply_ddd_updates([{"target": "IMPROVEMENT.md",
                "section": "Patterns Discovered",
                "action": f"New engagement pattern from repo/{i}",
                "content_preview": f"p{i}"}])
        assert self._count_auto(tmp_path) == MAX_AUTO_ENTRIES

    def test_multiline_preview_collapsed_to_single_line(self, tmp_path, monkeypatch):
        # Adversarial B1: GitHub comment bodies contain newlines; a multi-line
        # [auto] entry breaks the line-index CAP (orphans continuation lines).
        # Preview must be collapsed to one line at write time.
        cult = self._setup(tmp_path, monkeypatch,
                           "## Patterns Discovered\n\n- seed\n")
        cult._apply_ddd_updates([{"target": "IMPROVEMENT.md",
            "section": "Patterns Discovered",
            "action": "New engagement pattern from a/b",
            "content_preview": "line one\n> quoted\n\nline two"}])
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        auto_lines = [ln for ln in t.split("\n") if ln.lstrip().startswith("- [auto ")]
        assert len(auto_lines) == 1
        # The whole entry is on that one line — no orphaned continuation.
        assert "line one" in auto_lines[0] and "line two" in auto_lines[0]
        assert "\n> quoted" not in t  # no raw newline leaked into the doc

    def test_dedup_applies_to_non_engagement_actions(self, tmp_path, monkeypatch):
        # Adversarial B2: dedup was gated to "New engagement pattern" only;
        # other auto actions (e.g. maintainer validation) must dedup too.
        cult = self._setup(tmp_path, monkeypatch,
                           "## Source Matrix\n\n- seed\n")
        upd = [{"target": "IMPROVEMENT.md", "section": "Source Matrix",
                "action": "Update x/y — maintainer confirmed approach",
                "content_preview": "p"}]
        cult._apply_ddd_updates(upd)
        cult._apply_ddd_updates(upd)
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        assert t.count("maintainer confirmed approach") == 1

    def test_section_prefix_collision_targets_exact(self, tmp_path, monkeypatch):
        # Adversarial B3: targeting "Patterns" must NOT write into "Patterns Discovered".
        cult = self._setup(tmp_path, monkeypatch,
            "## Patterns\n\n- short-sec seed\n\n## Patterns Discovered\n\n- long-sec seed\n")
        cult._apply_ddd_updates([{"target": "IMPROVEMENT.md",
            "section": "Patterns",
            "action": "New engagement pattern from z/z", "content_preview": "p"}])
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        # Entry must land in "## Patterns" body, before "## Patterns Discovered".
        pat = t.index("## Patterns\n")
        disc = t.index("## Patterns Discovered")
        entry = t.index("z/z")
        assert pat < entry < disc

    def test_cap_never_drops_hand_written(self, tmp_path, monkeypatch):
        # NEGATIVE: only [auto] entries are subject to the cap.
        from skills.s_github_community.scripts.cultivate import MAX_AUTO_ENTRIES
        hand = "\n".join(f"- **Curated {i}** — keep me." for i in range(5))
        cult = self._setup(tmp_path, monkeypatch,
                           f"## Patterns Discovered\n\n{hand}\n")
        for i in range(50):
            cult._apply_ddd_updates([{"target": "IMPROVEMENT.md",
                "section": "Patterns Discovered",
                "action": f"New engagement pattern from repo/{i}",
                "content_preview": f"p{i}"}])
        t = (tmp_path / "IMPROVEMENT.md").read_text()
        for i in range(5):
            assert f"Curated {i}" in t  # hand-written survive
        assert self._count_auto(tmp_path) == MAX_AUTO_ENTRIES  # autos still capped
