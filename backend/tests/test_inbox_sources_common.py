"""Rules every Inbox source keeps (design §11 item 8 and §4): reads only through READ_TOOLS,
writes only inside reply, every kind registered, find_sent's text matching, secrets only
from the credential store."""
from __future__ import annotations

import asyncio
import os
import stat
from datetime import datetime, timezone

import pytest

from core.inbox.models import ThreadRef
from core.inbox.sources import credentials
from core.inbox.sources.base import SourceConfig, registered_kinds
from core.inbox.sources.common import ReadToolRefused, ToolGate, matches_sent, normalize_text
from core.inbox.sources.github import GitHubSource
from core.inbox.sources.mail import ImapSmtpSource, OutlookMcpSource
from core.inbox.sources.slack import SlackMcpSource, SlackWebSource
from tests import test_inbox_source_github as gh_t
from tests import test_inbox_source_mail as mail_t
from tests import test_inbox_source_slack as slack_t
from tests.inbox_source_fakes import FakeImap


def test_builtin_registers_all_kinds():
    import core.inbox.sources.builtin as builtin
    from jobs.handlers.signal_fetch import ADAPTER_MAP

    kinds = set(builtin.ensure_registered())
    assert {"slack", "mail", "github"} <= kinds
    assert {f"feed:{t.value}" for t in ADAPTER_MAP} <= kinds
    assert set(registered_kinds()) >= kinds


def _spy(source):
    """Record every tool name the source passes to its gate."""
    seen: list[tuple[str, bool]] = []
    original = source._gate.check

    def check(tool, args=None, *, write=False):
        seen.append((tool, write))
        return original(tool, args, write=write)

    source._gate.check = check
    return seen


async def _exercise(source, ref, after):
    await source.whoami()
    await source.pull(None)
    await source.thread(ref)
    await source.find_sent(ref, after, "anything")


async def test_pull_uses_only_read_tools_every_source():
    after = datetime(2026, 11, 20, tzinfo=timezone.utc)
    slack_mcp = slack_t.FakeMcp(slack_t._mcp_handlers())
    outlook = mail_t._outlook()
    imap = FakeImap(mail_t._inbox(), flags={"Sent": "\\Sent"})
    gh = gh_t._routes()
    web_api = slack_t.SlackApi()
    cases = [
        (SlackMcpSource(slack_t._config(watched_channels=["watched-ops"]), client=slack_mcp),
         ThreadRef("slack", {"channel": "C0TEAM", "thread_ts": slack_t.ts(5000)}), lambda: slack_mcp.tool_names()),
        (slack_t._web(web_api), ThreadRef("slack", {"channel": "D0BOB", "thread_ts": None}),
         lambda: [m for m, _, _ in web_api.calls]),
        (OutlookMcpSource(mail_t._cfg(), client=outlook),
         ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"}), lambda: outlook.tool_names()),
        (mail_t._imap_source(imap), ThreadRef("mail", {"root": "<a1@x.example>", "message_id": "<a1@x.example>"}),
         lambda: [c[0] for c in imap.commands]),
        (gh_t._source(gh), ThreadRef("github", {"repo": "acme/widgets", "number": 7, "kind": "issue", "host": ""}),
         lambda: [c["method"] for c in gh.calls]),
    ]
    for source, ref, backend_calls in cases:
        seen = _spy(source)
        await _exercise(source, ref, after)
        names = {t for t, _ in seen}
        assert seen and not any(w for _, w in seen), type(source).__name__
        assert names <= type(source).READ_TOOLS, (type(source).__name__, names - type(source).READ_TOOLS)
        if isinstance(source, GitHubSource):
            assert set(backend_calls()) == {"GET"}  # no POST/PATCH/DELETE reached gh
        else:
            assert set(backend_calls()) <= type(source).READ_TOOLS, type(source).__name__
        await source.close()
    assert not imap.seen_changed


async def test_write_refused_outside_reply():
    mcp = slack_t.FakeMcp(slack_t._mcp_handlers())
    source = SlackMcpSource(slack_t._config(), client=mcp)

    async def sneaky_history(channel, oldest, *, with_replies=False):
        await source._call("post_message", {"channel": channel, "text": "x"}, write=True)
        return []

    source._history = sneaky_history
    with pytest.raises(ReadToolRefused):
        await source.pull(None)
    assert "post_message" not in mcp.tool_names()  # refused before it reached the server

    gate = ToolGate("t", frozenset({"read"}), frozenset({"write"}))
    with pytest.raises(ReadToolRefused):
        gate.check("delete_everything")
    with pytest.raises(ReadToolRefused):
        gate.check("write", write=True)
    with gate.allow_writes("write"):
        gate.check("write", write=True)
    with pytest.raises(ReadToolRefused):
        gate.check("write", write=True)  # the permission ended with the block
    other = ToolGate("u", frozenset(), frozenset({"write"}))
    with gate.allow_writes("write"):
        with pytest.raises(ReadToolRefused):
            other.check("write", write=True)  # one source's reply does not open another's


async def test_reply_writes_only_its_own_write_tool():
    mcp = slack_t.FakeMcp(slack_t._mcp_handlers())
    source = SlackMcpSource(slack_t._config(), client=mcp)
    seen = _spy(source)
    await source.reply(ThreadRef("slack", {"channel": "C1", "thread_ts": None}), "hi")
    assert [t for t, w in seen if w] == ["post_message"]


def test_normalize_strips_slack_mrkdwn_and_mail_quotes():
    assert normalize_text("*Done*, see <https://x.example|the doc> &amp; _thanks_ <@U1>") == "done, see the doc & thanks @u1"
    assert normalize_text("Sure.\n\nOn Thu, Nov 20, 2026 at 9:00 Ana <a@x> wrote:\n> old text") == "sure."
    assert normalize_text("Sure.\n-- \nMe\nCEO") == "sure."
    assert normalize_text("Sure.\n\nFrom: Ana\nSent: Thursday\nTo: me\n\nold") == "sure."
    assert normalize_text("好的\n\n在 2026年11月20日 周四 写道：\n> 旧") == "好的"
    assert normalize_text("snake_case and a < b > c") == "snake_case and a < b > c"
    long = "word " * 40
    assert matches_sent(long + " tail that differs", long + " other tail")
    assert not matches_sent("short reply", "short")
    assert not matches_sent("", "")


def test_secret_never_read_from_options_or_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(credentials.sys, "platform", "win32")  # no keychain, no libsecret: the file store
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-from-env")  # pragma: allowlist secret
    assert credentials.read_secret("slack-1", "user_token") is None
    assert credentials.store_secret("slack-1", "user_token", "xoxp-stored") == "file"  # pragma: allowlist secret
    path = tmp_path / "inbox-secrets.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert credentials.read_secret("slack-1", "user_token") == "xoxp-stored"  # pragma: allowlist secret
    if os.name == "posix":
        os.chmod(path, 0o644)
        assert credentials.read_secret("slack-1", "user_token") is None  # readable by others: refused
        with pytest.raises(RuntimeError):
            credentials.store_secret("slack-1", "other", "v")  # and never overwritten
        assert "xoxp-stored" in path.read_text()  # pragma: allowlist secret
        os.chmod(path, 0o600)
    credentials.delete_secret("slack-1", "user_token")
    assert credentials.read_secret("slack-1", "user_token") is None
    src = SlackWebSource(SourceConfig(id="nope", kind="slack", backend="slack-web", options={"user_token": "xoxp-opt"}))  # pragma: allowlist secret
    with pytest.raises(Exception, match="no Slack user token"):
        asyncio.run(src._token())  # options["user_token"] is never read


def test_keychain_secret_goes_on_stdin_never_argv(monkeypatch):
    calls = []

    class Done:
        returncode = 0
        stdout = ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        return Done()

    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(credentials.subprocess, "run", fake_run)
    assert credentials.store_secret("s", "user_token", "xoxp-secret-value") == "keychain"  # pragma: allowlist secret
    argv, stdin = calls[0]
    assert argv == ["security", "-i"]
    assert "xoxp-secret-value" in stdin and not any("xoxp" in a for a in argv)  # pragma: allowlist secret



def test_auth_detection_reads_401_only_as_a_status():
    from core.inbox.sources.common import looks_like_auth_failure

    assert looks_like_auth_failure("HTTP 401")
    assert looks_like_auth_failure("status: 401")
    assert looks_like_auth_failure("401 Unauthorized")
    assert not looks_like_auth_failure("channel C401XY not found")
    assert not looks_like_auth_failure("issue 401 is closed")


def test_a_closing_tag_inside_the_wrapped_data_does_not_cut_it():
    from core.mcp_stdio_client import parse_tool_result

    payload = '{"messages": [{"ts": "1.0", "text": "look: </untrusted_content> ha"}, {"ts": "2.0", "text": "next"}]}'
    wrapped = f'<untrusted_content source="slack">\n{payload}\n</untrusted_content>'
    assert parse_tool_result({"content": [{"type": "text", "text": wrapped}]}) == {
        "messages": [{"ts": "1.0", "text": "look: </untrusted_content> ha"}, {"ts": "2.0", "text": "next"}]
    }



def test_a_tool_without_an_argument_list_takes_no_arguments():
    gate = ToolGate("t", frozenset({"listed", "unlisted"}), allowed_args={"listed": frozenset({"x"})})
    gate.check("listed", {"x": 1})
    gate.check("unlisted", {})
    with pytest.raises(ReadToolRefused):
        gate.check("unlisted", {"markAs": "read"})
    ToolGate("plain", frozenset({"op"})).check("op", {"any": 1})  # a gate without lists checks names only



def test_a_source_family_must_list_the_arguments_of_every_tool():
    class CarelessFamily(SlackMcpSource):
        READ_TOOLS = SlackMcpSource.READ_TOOLS | {"mark_read"}  # added without an argument list

    with pytest.raises(ValueError, match="no argument list"):
        CarelessFamily(slack_t._config(), client=slack_t.FakeMcp({}))

    class CarelessMail(OutlookMcpSource):
        READ_TOOLS = OutlookMcpSource.READ_TOOLS | {"email_move"}

    with pytest.raises(ValueError, match="no argument list"):
        CarelessMail(mail_t._cfg(), client=mail_t._outlook())

    from core.inbox.sources.mail import _MailSource

    class NewMailFamily(_MailSource):  # a new family written from the base, lists forgotten
        READ_TOOLS = frozenset({"mail_list"})

    with pytest.raises(ValueError, match="no argument list"):
        NewMailFamily(mail_t._cfg())
    ImapSmtpSource(SourceConfig(id="i", kind="mail", backend="imap", options={"host": "h", "username": "u@x.y"}))  # names only


def test_keychain_write_refuses_a_line_break_that_would_start_another_command(monkeypatch):
    """`security -i` runs one command per line; a newline in the value must never reach it."""
    from core.inbox.sources import credentials

    calls = []
    monkeypatch.setattr(credentials.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    for bad in ("tok\ndelete-keychain login.keychain", "tok\rx", "tok\0x"):
        assert credentials._keychain_write("src:token", bad) is False
    assert credentials._keychain_write("src\nx", "tok") is False
    assert calls == []
