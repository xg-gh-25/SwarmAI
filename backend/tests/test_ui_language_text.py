"""Fixed backend text the person sees follows the app's language (``ui_language``), and
the Swarm Out wake markers are English while the old Chinese ones are still recognised.

- core/ui_language: ``zh`` gives Chinese, anything else (empty, ``en``, an unreadable
  setting) English.
- Inbox cards and channel settings (core/inbox/store, drafts, submit, sources), the
  feed refusals of core/inbox/connect, the mwinit hint only when the server's error names it.
- The chat's repetition-loop notice (core/output_degeneration).
- Swarm Out wakes (core/swarm_out_wake) and the run-intervention text rule.
- The OS Eval HTML report (scripts/eval_runner).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

import pytest

_CJK = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        assert key == "ui_language"
        return self.value


@pytest.fixture
def app_language(monkeypatch):
    """Set the app's language the way the desktop does (the ui_language setting)."""
    from core import app_config_manager

    def set_to(value):
        monkeypatch.setattr(app_config_manager.AppConfigManager, "instance",
                            classmethod(lambda cls: _Config(value)))
    set_to("")
    return set_to


# ── core/ui_language ─────────────────────────────────────────────────────────

def test_setting_values(app_language):
    from core.ui_language import text, ui_language
    for value, want in (("zh", "zh"), (" ZH ", "zh"), ("en", "en"), ("", "en"), (None, "en"),
                        ("fr", "en")):
        app_language(value)
        assert ui_language() == want, value
    app_language("zh")
    assert text({"k": "n={n}"}, {"k": "数={n}"}, "k", n=2) == "数=2"
    assert text({"k": "n={n}"}, {"k": "数={n}"}, "k", "en", n=2) == "n=2"


def test_unreadable_setting_is_english(monkeypatch):
    from core import app_config_manager
    from core.ui_language import ui_language

    def broken(cls):
        raise OSError("config unreadable")
    monkeypatch.setattr(app_config_manager.AppConfigManager, "instance", classmethod(broken))
    assert ui_language() == "en"


# ── Inbox ────────────────────────────────────────────────────────────────────

def _source_row(**kw):
    row = {"id": "slack-1", "kind": "slack", "backend": "token", "interval_s": 900, "health": "ok",
           "fix": "", "last_pull_at": None, "last_error": "", "can_send": 1, "enabled": 1,
           "me": None, "config": "{}"}
    row.update(kw)
    return row


def test_inbox_labels_follow_ui_language(app_language):
    from core.inbox import store
    watched = json.dumps({"watched_channels": ["a", "b"], "watched_repos": ["o/r"]})
    row = {"source_kind": "slack"}

    app_language("")
    assert [store.channel_label(k) for k in ("mail", "feed", "slack", "github", "mail:imap")] == \
        ["Mail", "Feeds", "Slack", "GitHub", "Mail"]
    assert store._where(row, {"is_direct": True}) == "Slack DM"
    assert store._where(row, {"mentions_me": True}) == "Slack · @you"
    assert store._where(row, {"where": "#general thread"}) == "#general thread"
    assert store.source_out(_source_row(config=watched))["detail"] == "watching 2 channels · watching 1 repo"
    assert store.source_out(_source_row(id="feed", kind="feed"), feeds=22)["detail"] == "22 feeds"

    app_language("zh")
    assert [store.channel_label(k) for k in ("mail", "feed", "slack")] == ["邮件", "信息流", "Slack"]
    assert store._where(row, {"is_direct": True}) == "Slack 私信"
    assert store._where(row, {"mentions_me": True}) == "Slack · @你"
    assert store.source_out(_source_row(config=watched))["detail"] == "关注 2 个频道 · 关注 1 个仓库"
    assert store.source_out(_source_row(id="feed", kind="feed"), feeds=22)["detail"] == "22 个 feed"


async def test_inbox_notes_and_fixes_follow_ui_language(app_language):
    from core.inbox import drafts, submit
    from core.inbox.sources.base import SourceConfig
    from core.inbox.sources.common import source_text
    from core.inbox.sources.mail import ImapSmtpSource
    from core.ui_language import text

    def no_smtp():
        cfg = SourceConfig(id="imap-1", kind="mail", backend="imap",
                           options={"host": "imap.example", "username": "me@example.com", "smtp_host": ""})
        return ImapSmtpSource(cfg, imap_factory=lambda h, p: None, smtp_factory=None,
                              secret_reader=lambda name: None)

    app_language("en")
    assert drafts._note("", 0) == "Drew only on this thread"
    assert drafts._note("", 3) == "Drew on 3 earlier exchanges of yours"
    assert drafts._note("Ann", 2) == "Drew on 2 exchanges between you and Ann"
    assert text(submit._NOTE_EN, submit._NOTE_ZH, "drafted_by", producer="job-x") == "Drafted by job-x"
    assert source_text("slack_dm", who="Ann") == "DM · Ann"
    assert source_text("gh_review_thread", repo="o/r", number=7) == "o/r#7 review thread"
    src = no_smtp()
    assert not await src.can_send()
    assert src.status_detail().startswith("No SMTP server is set") and not _CJK.search(src.status_detail())

    app_language("zh")
    assert drafts._note("", 0) == "只参考了这个 thread"
    assert drafts._note("Ann", 2) == "参考了你和 Ann 的 2 条往来"
    assert drafts._note("小李", 2) == "参考了你和小李的 2 条往来"
    assert text(submit._NOTE_EN, submit._NOTE_ZH, "drafted_by", producer="job-x") == "由 job-x 起草"
    assert source_text("slack_dm", who="Ann") == "私信 · Ann"
    src = no_smtp()
    assert not await src.can_send()
    assert src.status_detail().startswith("没填 SMTP 服务器")


@pytest.mark.parametrize("method", ["ada_account", "sso_app", "bedrock_api_key", None])
def test_mwinit_hint_follows_the_servers_error(app_language, monkeypatch, method):
    """mwinit is named when the MCP server's own error names mwinit / Midway, whatever
    SwarmAI's AWS account signs in with; any other expired login gets the generic hint."""
    from core import aws_account
    from core.inbox.sources.common import auth_fix_for
    monkeypatch.setattr(aws_account, "remediation_method", lambda config=None: method)
    assert auth_fix_for("error: run mwinit to refresh your Midway cookie", "outlook") == \
        "Run mwinit -f, then click Retry"
    assert auth_fix_for("Midway session expired", "outlook") == "Run mwinit -f, then click Retry"
    assert auth_fix_for("401 unauthorized", "outlook") == \
        'Sign in to MCP server "outlook" again, then click Retry'
    app_language("zh")
    assert auth_fix_for("401 unauthorized", "outlook") == "重新登录 MCP server「outlook」后点重试"


async def test_connect_feed_refusals_are_english(monkeypatch):
    from core.inbox import connect, store

    async def row(source_id):
        return {"id": source_id, "kind": "feed", "config": "{}"}
    monkeypatch.setattr(connect, "_row", row)
    for call in (lambda: connect.update(store.FEED_SOURCE_ID, enabled=False),
                 lambda: connect.remove(store.FEED_SOURCE_ID)):
        with pytest.raises(connect.ConnectRefused) as info:
            await call()
        assert info.value.code == "feed_source"
        assert "under Feeds" in info.value.message and not _CJK.search(info.value.message)


# ── chat: repetition-loop notice ─────────────────────────────────────────────

def test_degeneration_notice_follows_ui_language(app_language):
    from core import output_degeneration as od

    app_language("")
    assert od.degeneration_notice() == od.DEGENERATION_NOTICE
    assert not _CJK.search(od.DEGENERATION_NOTICE) and "repetition loop" in od.DEGENERATION_NOTICE
    tool = od.degeneration_notice(tool="Write")
    assert tool.startswith(od.DEGENERATION_NOTICE) and "Write" in tool and not _CJK.search(tool)
    assert not _CJK.search(od.DEGENERATION_TRANSCRIPT_MARKER)
    assert od.DEGENERATION_TRANSCRIPT_MARKER.startswith("[system: output stopped")

    app_language("zh")
    zh = od.degeneration_notice(tool="Write")
    assert "输出陷入重复已中止" in zh and "Write" in zh and "Output stopped" not in zh


# ── Swarm Out wakes ──────────────────────────────────────────────────────────

def test_wake_markers_english_and_old_ones_recognised():
    from core import swarm_out_wake as wake
    for marker in (wake.MARKER, wake.RUN_MARKER):
        assert not _CJK.search(marker)
    assert wake.WAKE_TEXT.startswith(wake.MARKER)
    assert wake.RUN_WAKE_TEXT.startswith(wake.RUN_MARKER)
    assert wake.MIXED_WAKE_TEXT.startswith(wake.RUN_MARKER)
    for text in (wake.WAKE_TEXT, wake.RUN_WAKE_TEXT, wake.MIXED_WAKE_TEXT,
                 "[Swarm Out 自动继续] Runs you dispatched need you",
                 "[Swarm Run 自动继续] Children of your Swarm Run changed",
                 "  [Swarm Run auto-continue] x"):
        assert wake.is_wake_text(text), text
    for text in ("use the other table", "继续", "[Swarm Out] This task was dispatched", "", None, 3):
        assert not wake.is_wake_text(text), text


def test_run_interventions_skip_wakes_without_ledger(tmp_path):
    """A wake row the provenance ledger does not cover (written before it existed) is read
    by its text: neither marker, old or new, counts as the person steering."""
    from core import run_interventions as RI
    from core import swarm_out_wake as wake

    t0 = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc)
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, content TEXT, created_at TEXT)")
    rows = ["[Swarm Run 自动继续] Children of your Swarm Run changed",
            "[Swarm Out 自动继续] Runs you dispatched need you",
            wake.WAKE_TEXT, wake.RUN_WAKE_TEXT, "use the other table"]
    for i, text in enumerate(rows):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                     (str(i), "s-a", "user", json.dumps([{"type": "text", "text": text}]),
                      t0.replace(minute=10 + i).astimezone().replace(tzinfo=None).isoformat()))
    conn.commit()
    conn.close()
    raw = {"created_at": t0.isoformat(), "finished_at": t2.isoformat(), "session_id": "s-a",
           "human_decisions": 0}
    assert [c["text"] for c in RI.corrections(raw, db_path=path)] == ["use the other table"]
    assert RI.messages(raw, db_path=path) == 1


# ── OS Eval HTML report ──────────────────────────────────────────────────────

def _report(tmp_path, monkeypatch):
    from scripts import eval_runner as er
    monkeypatch.setattr(er, "_report_follow_ups", lambda *a, **k: [
        {"kind": "needs", "severity": "high", "what": {"en": "A case broke", "zh": "一个用例坏了"},
         "why": "", "action": {"en": "Look at it", "zh": "看一下"}}])
    golden = {"cases": [{"id": "GS_1", "dimension": "cognition", "title": "recall works",
                         "eval_method": "programmatic", "tier": "stable"},
                        {"id": "GS_2", "dimension": "quality", "title": "answers right",
                         "eval_method": "llm", "tier": "active"}]}
    run = {"run_id": "r1", "cases": [{"id": "GS_1", "status": "passed"}, {"id": "GS_2", "status": "failed",
                                                                         "notes": "wrong"}],
           "cases_passed": 1, "cases_failed": 1, "cases_skipped": 0, "duration_seconds": 1.5,
           "triggered_at": "2026-11-23T10:00:00", "triggered_by": "manual",
           "release": {"verdict": "blocked", "reason": "a gate case failed",
                       "checks": [{"id": "redline", "passed": True}, {"id": "dimensions", "passed": False}]}}
    return er.generate_html_report(run, golden, tmp_path).read_text(encoding="utf-8")


def test_eval_report_follows_ui_language(app_language, tmp_path, monkeypatch):
    app_language("")
    html = _report(tmp_path, monkeypatch)
    assert '<html lang="en">' in html
    assert "Does it really understand your work?" in html and "Blocked" in html
    assert "Red line · any failure blocks" in html and "A case broke" in html
    assert _CJK.findall(html) == []

    app_language("zh")
    html = _report(tmp_path, monkeypatch)
    assert '<html lang="zh">' in html
    for words in ("它真的懂你的工作吗？", "不放行", "红线 · 一票否决", "一个用例坏了", "我还是我吗？还好吗？"):
        assert words in html, words
