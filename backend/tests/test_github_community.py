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
