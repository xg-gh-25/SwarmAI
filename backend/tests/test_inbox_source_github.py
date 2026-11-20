"""The GitHub Inbox source over a faked ``gh`` (REST shapes of notifications, comments, issues)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.inbox.models import ThreadRef
from core.inbox.sources.base import SendFailed, SourceAuthError, SourceConfig
from core.inbox.sources.github import GitHubSource, create
from tests.inbox_source_fakes import FakeGh

API = "https://api.github.com"
LOGIN = "octo-person"


def _notification(nid, reason, subject_type, url, latest, title="Fix the build", updated="2026-11-20T02:00:00Z", private=False):
    return {
        "id": nid, "reason": reason, "updated_at": updated, "unread": True,
        "subject": {"title": title, "type": subject_type, "url": url, "latest_comment_url": latest},
        "repository": {"full_name": "acme/widgets", "private": private, "html_url": "https://github.com/acme/widgets"},
    }


NOTIFICATIONS = [
    _notification("1", "mention", "Issue", f"{API}/repos/acme/widgets/issues/7", f"{API}/repos/acme/widgets/issues/comments/701"),
    _notification("2", "review_requested", "PullRequest", f"{API}/repos/acme/widgets/pulls/8",
                  f"{API}/repos/acme/widgets/pulls/comments/801", updated="2026-11-20T02:30:00Z"),
    _notification("3", "subscribed", "Issue", f"{API}/repos/acme/widgets/issues/9", f"{API}/repos/acme/widgets/issues/comments/901"),
    # the same comment again (a second notification thread pointing at it): one item
    _notification("4", "comment", "Issue", f"{API}/repos/acme/widgets/issues/7", f"{API}/repos/acme/widgets/issues/comments/701"),
    _notification("5", "subscribed", "Release", f"{API}/repos/acme/widgets/releases/1", f"{API}/repos/acme/widgets/releases/1",
                  title="v2.0"),
]


def _comment(cid, login, body, created="2026-11-20T01:59:00Z", **extra):
    return {"id": cid, "user": {"login": login, "type": "User"}, "body": body, "created_at": created,
            "updated_at": created, "html_url": f"https://github.com/acme/widgets/issues/7#issuecomment-{cid}", **extra}


def _routes(overrides=None):
    routes = {
        ("GET", "user"): {"login": LOGIN, "id": 4242, "name": "Octo Person"},
        ("GET", "notifications?per_page=1"): [],
        ("GET", "notifications?all=true"): NOTIFICATIONS,
        ("GET", "repos/acme/widgets/issues/comments/701"): _comment(701, "ana", f"@{LOGIN} please check"),
        ("GET", "repos/acme/widgets/pulls/comments/801"): _comment(801, "bob", "nit: rename", in_reply_to_id=800),
        ("GET", "repos/acme/widgets/issues/comments/901"): _comment(901, "dependabot[bot]", "bump", ),
        ("GET", "repos/acme/widgets/issues/7/comments"): [
            _comment(701, "ana", f"@{LOGIN} please check"),
            _comment(702, LOGIN, "**Done**, see the [fix](https://x)", created="2026-11-20T03:00:00Z"),
        ],
        ("GET", "repos/acme/widgets/issues/7"): {"id": 7, "user": {"login": "ana"}, "body": "The build fails",
                                                 "created_at": "2026-11-19T00:00:00Z"},
        ("GET", "repos/acme/widgets"): {"private": False},
        ("POST", "repos/acme/widgets/issues/7/comments"): lambda body: {"id": 703, "html_url": "https://github.com/c/703",
                                                                        "created_at": "2026-11-20T04:00:00Z"},
        ("POST", "repos/acme/widgets/pulls/8/comments/800/replies"): lambda body: {"id": 802, "html_url": "u"},
        ("POST", "graphql"): lambda body: {"data": {"addDiscussionComment": {"comment": {"id": "DC_1", "url": "u"}}}},
    }
    routes.update(overrides or {})
    # longest prefix first, so "repos/acme/widgets/issues/7/comments" wins over ".../issues/7"
    return FakeGh(sorted(((m, p, v) for (m, p), v in routes.items()), key=lambda r: -len(r[1])))


def _source(gh, **options):
    return GitHubSource(SourceConfig(id="gh-1", kind="github", backend="gh", options=options), runner=gh)


async def test_whoami_from_gh_api_user():
    gh = _routes()
    me = await _source(gh).whoami()
    assert me.ids == frozenset({LOGIN, "4242"})
    assert gh.calls[0]["path"] == "user"


async def test_pull_maps_reasons_and_dedups_by_comment_id():
    gh = _routes()
    result = await _source(gh).pull("2026-11-20T00:00:00Z")
    by_id = {m.external_id: m for m in result.messages}
    assert set(by_id) == {"issue_comment:701", "review_comment:801", "issue_comment:901", "notification:5@2026-11-20T02:00:00Z"}
    mention = by_id["issue_comment:701"]
    assert mention.mentions_me and mention.is_direct and mention.author_id == "ana"
    assert mention.thread.data == {"host": "", "repo": "acme/widgets", "subject": "Issue", "number": 7, "kind": "issue"}
    review = by_id["review_comment:801"]
    assert review.mentions_me and review.thread.data["kind"] == "review" and review.thread.data["review_comment_id"] == 800
    assert by_id["issue_comment:901"].watched and by_id["issue_comment:901"].is_bot
    assert result.cursor == "2026-11-20T02:30:00+00:00"
    listing = [c["path"] for c in gh.calls if c["path"].startswith("notifications?all")]
    assert listing == ["notifications?all=true&since=2026-11-20T00%3A00%3A00Z&per_page=50&page=1"]
    assert all(c["method"] == "GET" for c in gh.calls)


async def test_token_that_cannot_read_notifications_is_feed_only():
    gh = _routes({("GET", "notifications?per_page=1"): (1, "gh: Resource not accessible by personal access token (HTTP 403)")})
    src = _source(gh)
    result = await src.pull(None)
    assert result.messages == () and src.feed_only and "PAT" in src.feed_only_reason
    assert not await src.can_send()
    assert not any(c["path"].startswith("notifications?all") for c in gh.calls)


async def test_expired_auth_raises_source_auth_error():
    gh = _routes({("GET", "user"): (1, "HTTP 401: Bad credentials (https://api.github.com/user)\nTry authenticating with:  gh auth login")})
    with pytest.raises(SourceAuthError) as info:
        await _source(gh).whoami()
    assert info.value.fix == "gh auth login"
    ghes = _routes({("GET", "user"): (1, "HTTP 401: Bad credentials")})
    with pytest.raises(SourceAuthError) as info:
        await _source(ghes, hostname="github.corp.example").whoami()
    assert info.value.fix == "gh auth login --hostname github.corp.example"
    assert ghes.calls[0]["host"] == "github.corp.example"


async def test_reply_per_subject_type_goes_to_the_given_thread():
    gh = _routes()
    src = _source(gh)
    issue = ThreadRef("github", {"host": "", "repo": "acme/widgets", "subject": "Issue", "number": 7, "kind": "issue"})
    receipt = await src.reply(issue, "on it")
    review = ThreadRef("github", {"host": "", "repo": "acme/widgets", "subject": "PullRequest", "number": 8,
                                  "kind": "review", "review_comment_id": 800})
    await src.reply(review, "renamed")
    disc = ThreadRef("github", {"host": "", "repo": "acme/widgets", "subject": "Discussion", "number": 3,
                                "kind": "discussion", "discussion_id": "D_1", "reply_to_id": "DC_0"})
    await src.reply(disc, "thanks")
    writes = [(c["method"], c["path"], c["body"]) for c in gh.calls if c["method"] == "POST"]
    assert writes[0] == ("POST", "repos/acme/widgets/issues/7/comments", {"body": "on it"})
    assert writes[1] == ("POST", "repos/acme/widgets/pulls/8/comments/800/replies", {"body": "renamed"})
    assert writes[2][1] == "graphql" and writes[2][2]["query"].startswith("mutation")
    assert writes[2][2]["variables"] == {"discussionId": "D_1", "body": "thanks", "replyToId": "DC_0"}
    assert receipt.external_msg_id == "issue_comment:703"
    assert all("--input" in c["argv"] and "-F" not in c["argv"] for c in gh.calls if c["method"] == "POST")


async def test_reply_rejected_by_github_is_send_failed():
    gh = _routes({("POST", "repos/acme/widgets/issues/7/comments"): (1, "gh: Unprocessable Entity (HTTP 422)")})
    with pytest.raises(SendFailed):
        await _source(gh).reply(ThreadRef("github", {"repo": "acme/widgets", "number": 7, "kind": "issue"}), "x")


async def test_thread_target_and_public_flag():
    thread = await _source(_routes()).thread(ThreadRef("github", {"repo": "acme/widgets", "number": 7, "kind": "issue", "host": ""}))
    assert thread.target.label == "acme/widgets#7" and thread.target.public and thread.target.flags == ("public",)
    assert [m.author_id for m in thread.messages] == ["ana", "ana", LOGIN]


async def test_find_sent_matches_the_persons_comment_after_the_time():
    src = _source(_routes())
    ref = ThreadRef("github", {"repo": "acme/widgets", "number": 7, "kind": "issue", "host": ""})
    after = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)
    found = await src.find_sent(ref, after, "Done, see the fix")
    assert found is not None and found.external_msg_id == "issue_comment:702"
    assert await src.find_sent(ref, after, "Not done") is None
    assert await src.find_sent(ref, datetime(2026, 11, 20, 3, 30, tzinfo=timezone.utc), "Done, see the fix") is None


def test_paths_from_messages_are_validated():
    assert GitHubSource._path_of("https://ghe.corp.example/api/v3/repos/a/b/issues/comments/1") == "repos/a/b/issues/comments/1"
    assert GitHubSource._path_of("https://api.github.com/user/../../etc") is None
    assert GitHubSource._path_of("https://api.github.com/repos/a/b/issues/1 --method POST") is None
    with pytest.raises(ValueError):
        create(SourceConfig(id="g", kind="github", backend="gh", options={"hostname": "--evil"}))



async def test_discussion_is_found_by_number_never_by_title():
    seen = []

    def graphql(body):
        seen.append(body)
        return {"data": {"repository": {"discussion": {
            "id": "D_42", "number": 42, "title": "Roadmap", "url": "https://github.com/acme/widgets/discussions/42",
            "comments": {"nodes": [{"id": "DC_1", "url": "u1", "body": f"@{LOGIN} thoughts?", "createdAt": "2026-11-20T01:00:00Z",
                                    "author": {"login": "ana"}, "replies": {"nodes": []}}]}}}}}

    notes = [
        _notification("d1", "mention", "Discussion", f"{API}/repos/acme/widgets/discussions/42", None, title="Roadmap"),
        _notification("d2", "subscribed", "Discussion", None, None, title="Roadmap"),
    ]
    gh = _routes({("GET", "notifications?all=true"): notes, ("POST", "graphql"): graphql})
    result = await _source(gh).pull("2026-11-20T00:00:00Z")
    by_id = {m.external_id: m for m in result.messages}
    disc = by_id["discussion_comment:DC_1"]
    assert disc.thread.data["discussion_id"] == "D_42" and disc.thread.data["reply_to_id"] == "DC_1"
    assert len(seen) == 1 and seen[0]["variables"] == {"owner": "acme", "name": "widgets", "number": 42}
    assert "title" not in seen[0]["query"].split("{", 2)[1]  # nothing is matched by title
    notice = by_id["notification:d2@2026-11-20T02:00:00Z"]
    assert notice.thread.data["kind"] == "none"  # no number: no reply thread
    with pytest.raises(SendFailed):
        await _source(gh).reply(notice.thread, "x")


async def test_a_call_cut_short_by_the_budget_keeps_what_was_collected():
    import asyncio as aio

    gh = _routes()
    calls = {"n": 0}

    async def runner(argv, stdin, timeout):
        calls["n"] += 1
        if "repos/acme/widgets/pulls/comments/801" in argv:
            raise aio.TimeoutError
        return await gh(argv, stdin, timeout)

    src = GitHubSource(SourceConfig(id="gh-1", kind="github", backend="gh", options={"pull_budget_s": 5}), runner=runner)
    result = await src.pull("2026-11-20T00:00:00Z")
    assert "issue_comment:701" in {m.external_id for m in result.messages}
    assert result.cursor is None  # keep the old position


async def test_gh_is_killed_on_timeout_and_on_cancel(monkeypatch):
    import asyncio as aio
    import sys

    from core.inbox.sources import github as gh_mod

    procs = []
    real = aio.create_subprocess_exec

    async def spy(*argv, **kw):
        proc = await real(*argv, **kw)
        procs.append(proc)
        return proc

    monkeypatch.setattr(gh_mod.asyncio, "create_subprocess_exec", spy)
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(aio.TimeoutError):
        await gh_mod._run_gh(slow, None, 0.3)
    task = aio.create_task(gh_mod._run_gh(slow, None, 30))
    await aio.sleep(0.5)
    task.cancel()
    with pytest.raises(aio.CancelledError):
        await task
    assert len(procs) == 2 and all(p.returncode is not None for p in procs)



async def test_notifications_are_read_past_the_first_page():
    first = [_notification(f"r{i}", "subscribed", "Release", None, None, title=f"v{i}") for i in range(50)]
    second = [_notification("last", "mention", "Issue", f"{API}/repos/acme/widgets/issues/7",
                            f"{API}/repos/acme/widgets/issues/comments/701", updated="2026-11-20T01:00:00Z")]
    base = "notifications?all=true&since=2026-11-20T00%3A00%3A00Z&per_page=50"
    gh = _routes({("GET", base + "&page=1"): first, ("GET", base + "&page=2"): second, ("GET", base + "&page=3"): []})
    result = await _source(gh).pull("2026-11-20T00:00:00Z")
    assert "issue_comment:701" in {m.external_id for m in result.messages}
    assert len(result.messages) == 51


async def test_status_detail_and_an_expired_login_on_reply():
    gh = _routes({("GET", "notifications?per_page=1"): (1, "gh: Resource not accessible by personal access token (HTTP 403)")})
    src = _source(gh)
    assert not await src.can_send()
    assert src.status_detail() == src.feed_only_reason and "PAT" in src.status_detail()
    assert src.login
    ok = _source(_routes())
    assert await ok.can_send() and ok.status_detail() == ""
    expired = _routes({("POST", "repos/acme/widgets/issues/7/comments"): (1, "HTTP 401: Bad credentials")})
    with pytest.raises(SendFailed) as info:
        await _source(expired).reply(ThreadRef("github", {"repo": "acme/widgets", "number": 7, "kind": "issue"}), "x")
    assert info.value.fix == "gh auth login"
