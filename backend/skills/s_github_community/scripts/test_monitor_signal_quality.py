"""Tests for the unified signal-quality gate in monitor.py.

The gate `is_valuable_signal()` is the single admission threshold every produced
signal must pass — closing the drift where the dashboard "discovery" branch let
unknown-repo bot/PR noise through (42% of a real run) by exempting it from the
topic check. Layered order: not-a-PR → topics non-empty → not-a-bot → has-activity.

Also covers the two support predicates (_is_bot_author, _is_release_bot_title),
the _is_new empty-guard (must never raise on an empty created_at), and a REAL
signals.json replay (noise → 0, valuable human-topic signals preserved).
"""

import json
from pathlib import Path

import pytest

from skills.s_github_community.scripts.monitor import (
    _is_bot_author,
    _is_new,
    _is_release_bot_title,
    is_valuable_signal,
)


# ---- _is_bot_author: [bot] suffix is NOT enough (eks-distro-pr-bot has none) ----

@pytest.mark.parametrize("author,expected", [
    ("dependabot[bot]", True),
    ("open-design-crew[bot]", True),
    ("eks-distro-pr-bot", True),      # the 19%-of-noise case — no [bot] suffix, -bot ending
    ("renovate-bot", True),
    ("santifer", False),             # real human
    ("joaomdmoura", False),
    ("kitepon-rgb", False),
    ("", False),
])
def test_is_bot_author(author, expected):
    assert _is_bot_author(author) is expected


# ---- _is_release_bot_title: auto-release PR titles ----

@pytest.mark.parametrize("title,expected", [
    ("Bump kube-vip/kube-vip to latest release", True),
    ("[release-0.25] Bump aws/etcdadm-controller to latest release", True),
    ("Update EKS Distro base image tag files to latest", True),
    ("[RFC] OpenViking Session persistence design", False),   # real human RFC
    ("SessionStart hooks fire for phantom sessions", False),   # real human issue
])
def test_is_release_bot_title(title, expected):
    assert _is_release_bot_title(title) is expected


# ---- _is_new: MUST NOT raise on empty created_at (the 283-line bug guard) ----

def test_is_new_empty_created_at_returns_false_no_raise():
    # discussions had created_at="" hardcoded — the guard must return False, not raise.
    assert _is_new("") is False


def test_is_new_recent_is_true():
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _is_new(recent) is True


def test_is_new_old_is_false():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    assert _is_new(old) is False


# ---- is_valuable_signal: the layered gate, each rejection reason ----

def _sig(**kw):
    base = {
        "repo": "x/y", "title": "Memory persistence design", "url": "https://github.com/x/y/issues/1",
        "author": "humanuser", "existing_comments": 3, "created_at": "",
        "matched_topics": ["T-MEM"],
    }
    base.update(kw)
    return base


def test_gate_rejects_pull_request():
    ok, reason = is_valuable_signal(_sig(url="https://github.com/aws/x/pull/5234"))
    assert ok is False and "pull request" in reason.lower()


def test_gate_rejects_empty_topics():
    ok, reason = is_valuable_signal(_sig(matched_topics=[]))
    assert ok is False and "topic" in reason.lower()


def test_gate_rejects_bot_author():
    ok, reason = is_valuable_signal(_sig(author="eks-distro-pr-bot"))
    assert ok is False and "bot" in reason.lower()


def test_gate_rejects_release_bot_title():
    ok, reason = is_valuable_signal(_sig(author="somehuman", title="Bump foo to latest release"))
    assert ok is False and "bot" in reason.lower()


def test_gate_rejects_inactive_old():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    ok, reason = is_valuable_signal(_sig(existing_comments=0, created_at=old))
    assert ok is False and "activ" in reason.lower()


def test_gate_accepts_new_zero_comment_human_topic():
    """The first-responder case: a fresh 0-comment human issue WITH a topic must pass."""
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ok, reason = is_valuable_signal(_sig(existing_comments=0, created_at=fresh))
    assert ok is True


def test_gate_accepts_active_human_topic():
    ok, reason = is_valuable_signal(_sig(existing_comments=12))
    assert ok is True


# ---- Bot check ordered BEFORE activity (a fresh bot PR must not slip via is_new) ----

def test_bot_rejected_before_activity_lets_new_through():
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # a brand-new bot issue (not a PR url) with a topic — bot step must catch it
    ok, reason = is_valuable_signal(_sig(
        author="eks-distro-pr-bot", existing_comments=0, created_at=fresh,
        url="https://github.com/x/y/issues/9",
    ))
    assert ok is False and "bot" in reason.lower()


# ---- REAL signals.json replay: noise -> 0, human-topic signals preserved ----

def test_real_signals_replay():
    real = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "GitHub_Community" / ".artifacts" / "signals.json"
    if not real.exists():
        pytest.skip("real signals.json not present")
    data = json.loads(real.read_text())
    sigs = data.get("signals", [])
    if not sigs:
        pytest.skip("no signals to replay")

    kept, rejected = [], []
    for s in sigs:
        ok, reason = is_valuable_signal(s)
        (kept if ok else rejected).append((s, reason))

    # No surviving signal may be a bot, a PR, or topic-less.
    for s, _ in kept:
        assert "/pull/" not in s.get("url", ""), f"PR leaked: {s.get('url')}"
        assert not _is_bot_author(s.get("author", "")), f"bot leaked: {s.get('author')}"
        assert s.get("matched_topics"), f"topicless leaked: {s.get('title')}"

    # The known noisiest author must be fully filtered.
    assert not any(s.get("author") == "eks-distro-pr-bot" for s, _ in kept)
    # Something valuable must survive (don't nuke everything).
    assert len(kept) >= 5
