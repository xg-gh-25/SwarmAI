"""Tests for UserObserver — extracts behavioral observations from sessions.

Key public symbols tested:
- ``UserObserver``  — Core observation logic
- ``Observation``   — Observation dataclass
- ``UserObserverHook`` — Post-session hook
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from dataclasses import asdict

from core.user_observer import UserObserver, Observation, CORRECTION_PATTERNS, EXPERTISE_KEYWORDS
from core.session_hooks import SessionLifecycleHook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def obs_path(tmp_path: Path) -> Path:
    return tmp_path / "user_observations.jsonl"


@pytest.fixture
def observer(obs_path: Path) -> UserObserver:
    return UserObserver(observations_path=obs_path)


# ---------------------------------------------------------------------------
# Correction detection
# ---------------------------------------------------------------------------

def test_detect_correction(observer: UserObserver):
    """'don't use bullet points' should produce a correction observation."""
    messages = [
        {"role": "user", "content": "don't use bullet points in your responses please"},
    ]
    obs = observer.observe_session(messages, session_id="sess-001")
    corrections = [o for o in obs if o.category == "corrections"]
    assert len(corrections) >= 1
    assert corrections[0].confidence == 0.9
    assert "bullet" in corrections[0].evidence.lower()


def test_detect_correction_prefer_pattern(observer: UserObserver):
    """'I prefer concise answers' should produce a correction observation."""
    messages = [
        {"role": "user", "content": "I prefer concise answers instead of long paragraphs"},
    ]
    obs = observer.observe_session(messages, session_id="sess-002")
    corrections = [o for o in obs if o.category == "corrections"]
    assert len(corrections) >= 1


# ---------------------------------------------------------------------------
# Expertise detection
# ---------------------------------------------------------------------------

def test_detect_expertise(observer: UserObserver):
    """Messages with 3+ expertise keywords should produce expertise observations."""
    messages = [
        {"role": "user", "content": "I work with kubernetes and terraform daily"},
        {"role": "user", "content": "Our distributed architecture uses python and docker"},
        {"role": "assistant", "content": "I can help with your kubernetes setup"},
    ]
    obs = observer.observe_session(messages, session_id="sess-003")
    expertise = [o for o in obs if o.category.startswith("expertise")]
    assert len(expertise) >= 1
    assert expertise[0].confidence == 0.7


# ---------------------------------------------------------------------------
# Language preference detection
# ---------------------------------------------------------------------------

def test_detect_language_preference(observer: UserObserver):
    """>30% CJK messages should produce a communication preference."""
    messages = [
        {"role": "user", "content": "你好，请帮我看一下这段代码"},
        {"role": "user", "content": "这个函数有什么问题吗"},
        {"role": "user", "content": "谢谢你的帮助"},
        {"role": "user", "content": "ok thanks"},
    ]
    obs = observer.observe_session(messages, session_id="sess-004")
    lang = [o for o in obs if o.category == "preferences.communication"]
    assert len(lang) >= 1
    assert lang[0].confidence == 0.8


def test_no_language_preference_for_english(observer: UserObserver):
    """All English messages should NOT produce a language preference."""
    messages = [
        {"role": "user", "content": "Hello, can you help me?"},
        {"role": "user", "content": "Thanks for the explanation"},
    ]
    obs = observer.observe_session(messages, session_id="sess-005")
    lang = [o for o in obs if o.category == "preferences.communication"]
    assert len(lang) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_session(observer: UserObserver):
    """0 messages should produce no observations."""
    obs = observer.observe_session([], session_id="sess-empty")
    assert obs == []


def test_assistant_only_messages(observer: UserObserver):
    """Assistant-only messages should not produce corrections or language prefs."""
    messages = [
        {"role": "assistant", "content": "don't use bullet points"},
        {"role": "assistant", "content": "你好世界"},
    ]
    obs = observer.observe_session(messages, session_id="sess-asst")
    corrections = [o for o in obs if o.category == "corrections"]
    lang = [o for o in obs if o.category == "preferences.communication"]
    assert len(corrections) == 0
    assert len(lang) == 0


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------

def test_consolidate_newer_wins(observer: UserObserver):
    """Same category+observation: newer with higher confidence wins."""
    existing = [
        Observation(
            date="2026-04-07",
            category="corrections",
            observation="Prefers no bullet points",
            confidence=0.7,
            source_session="sess-old",
            evidence="old evidence",
        ),
    ]
    new = [
        Observation(
            date="2026-04-08",
            category="corrections",
            observation="Prefers no bullet points",
            confidence=0.9,
            source_session="sess-new",
            evidence="new evidence",
        ),
    ]
    merged = observer.consolidate(new, existing)
    matches = [o for o in merged if o.observation == "Prefers no bullet points"]
    assert len(matches) == 1
    assert matches[0].source_session == "sess-new"
    assert matches[0].confidence == 0.9


def test_consolidate_keeps_both_different(observer: UserObserver):
    """Different observations should both be kept."""
    existing = [
        Observation(
            date="2026-04-07",
            category="corrections",
            observation="Prefers no bullet points",
            confidence=0.9,
            source_session="sess-1",
            evidence="evidence-1",
        ),
    ]
    new = [
        Observation(
            date="2026-04-08",
            category="expertise.tools",
            observation="Uses pytest extensively",
            confidence=0.7,
            source_session="sess-2",
            evidence="evidence-2",
        ),
    ]
    merged = observer.consolidate(new, existing)
    assert len(merged) == 2


# ---------------------------------------------------------------------------
# JSONL persistence
# ---------------------------------------------------------------------------

def test_save_and_load_jsonl(observer: UserObserver, obs_path: Path):
    """Round-trip through JSONL file."""
    observations = [
        Observation(
            date="2026-04-08",
            category="corrections",
            observation="No bullet points",
            confidence=0.9,
            source_session="sess-001",
            evidence="user said don't use bullets",
        ),
        Observation(
            date="2026-04-08",
            category="expertise.tools",
            observation="Uses pytest",
            confidence=0.7,
            source_session="sess-002",
            evidence="mentioned pytest 5 times",
        ),
    ]
    observer.save_observations(observations)
    loaded = observer.load_existing()
    assert len(loaded) == 2
    assert loaded[0].category == "corrections"
    assert loaded[1].category == "expertise.tools"


def test_save_appends(observer: UserObserver, obs_path: Path):
    """Multiple saves should append, not overwrite."""
    obs1 = [Observation("2026-04-07", "corrections", "obs1", 0.9, "s1", "e1")]
    obs2 = [Observation("2026-04-08", "corrections", "obs2", 0.8, "s2", "e2")]
    observer.save_observations(obs1)
    observer.save_observations(obs2)
    loaded = observer.load_existing()
    assert len(loaded) == 2


def test_load_empty_file(observer: UserObserver, obs_path: Path):
    """Loading from nonexistent file returns empty list."""
    loaded = observer.load_existing()
    assert loaded == []


# ---------------------------------------------------------------------------
# Suggestion logic
# ---------------------------------------------------------------------------

def test_suggest_updates_min_count(observer: UserObserver):
    """<3 observations in a category should produce no suggestions."""
    observations = [
        Observation("2026-04-08", "corrections", "obs1", 0.9, "s1", "e1"),
        Observation("2026-04-08", "corrections", "obs2", 0.8, "s2", "e2"),
    ]
    suggestions = observer.suggest_user_md_updates(observations)
    assert suggestions == []


def test_suggest_updates_with_enough(observer: UserObserver):
    """3+ observations in a category with 5+ total should produce suggestions."""
    observations = [
        Observation("2026-04-08", "corrections", "obs1", 0.9, "s1", "e1"),
        Observation("2026-04-08", "corrections", "obs2", 0.8, "s2", "e2"),
        Observation("2026-04-08", "corrections", "obs3", 0.7, "s3", "e3"),
        Observation("2026-04-08", "expertise.tools", "obs4", 0.7, "s4", "e4"),
        Observation("2026-04-08", "expertise.tools", "obs5", 0.7, "s5", "e5"),
    ]
    suggestions = observer.suggest_user_md_updates(observations)
    assert len(suggestions) >= 1


def test_suggest_updates_max_two(observer: UserObserver):
    """Never more than 2 suggestions regardless of data."""
    observations = [
        Observation("2026-04-08", f"cat{i}", f"obs{j}", 0.9, f"s{j}", f"e{j}")
        for i in range(5)
        for j in range(4)
    ]
    suggestions = observer.suggest_user_md_updates(observations)
    assert len(suggestions) <= 2


# ---------------------------------------------------------------------------
# Hook protocol
# ---------------------------------------------------------------------------

def test_hook_protocol():
    """UserObserverHook satisfies SessionLifecycleHook protocol."""
    from hooks.user_observer_hook import UserObserverHook
    hook = UserObserverHook()
    assert isinstance(hook, SessionLifecycleHook)
    assert hook.name == "user-observer"
