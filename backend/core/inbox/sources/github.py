"""The GitHub source (``kind="github"``, backend ``gh``): the person's notifications.

Design: ``docs/design/2026-11-20-inbox.md`` §4. Everything goes through the person's own
``gh`` login (``gh api``, run without a shell; GitHub Enterprise Server with
``options["hostname"]``, passed as ``--hostname``). Request bodies go on stdin
(``--input -``), never through ``-F``, which would read ``@file`` values.

- ``whoami``: ``gh api user`` (login and numeric id), then one probe of the notifications
  endpoint. A token that cannot read notifications (a fine-grained PAT, a GitHub App
  token) makes the source **feed-only**: ``pull`` returns nothing, ``can_send`` is false,
  and ``feed_only_reason`` (``status_detail()``) says why.
- ``pull``: ``notifications?all=true&since=<cursor>`` (``all`` because the default
  returns only unread ones, and anything opened on the web would be lost), then the latest
  comment of each thread for its text. ``reason`` ``mention``/``team_mention``/
  ``review_requested`` set ``mentions_me``; those and ``author``/``comment``/``assign`` set
  ``is_direct``; ``subscribed`` sets ``watched``. The cursor is the newest ``updated_at``.
- ``reply`` by subject: an issue or PR conversation comment (REST), a reply in a PR review
  thread (``pulls/<n>/comments/<id>/replies``), a Discussion comment (GraphQL
  ``addDiscussionComment``). The target names the thread; comments on a public
  repository carry the ``public`` flag. A reply refused because the login expired raises
  ``SendFailed`` with the ``gh auth login`` fix.
- More than ``max_pages`` × 50 notifications since the cursor (a first pull, a long time
  offline) are not all read: the newest are, the overflow is logged, and the cursor moves
  on. The older ones stay unread on GitHub; the Inbox does not page back for them.
- A Discussion is found by its number (from the notification's subject URL), never by
  its title: anyone can open a discussion with the same title in a public repository. A
  Discussion notification without a number becomes a notice with no reply thread.

``READ_TOOLS`` names each endpoint family a read may use; the paths are built from
validated parts (``owner/repo``, numbers, ids), never from raw text of a message.

Key public symbols: ``GitHubSource``, ``create``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, ClassVar, Optional
from urllib.parse import quote, urlparse

from core.inbox.models import (
    Identity,
    Message,
    PullResult,
    ReplyTarget,
    SendReceipt,
    SourceCategory,
    Thread,
    ThreadRef,
)
from core.inbox.sources.base import SendFailed, SourceAuthError, SourceConfig, register_source
from core.inbox.sources.common import (
    BudgetExhausted,
    Deadline,
    SourceUnavailable,
    ToolGate,
    call_timeout,
    matches_sent,
    parse_time,
)

logger = logging.getLogger(__name__)

KIND = "github"
_CALL_CAP_S = 30.0
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+(\?[A-Za-z0-9_.=&:%+-]*)?$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*(:\d+)?$")

DIRECT_REASONS = frozenset({"mention", "team_mention", "review_requested", "author", "comment", "assign"})
MENTION_REASONS = frozenset({"mention", "team_mention", "review_requested"})
WATCH_REASONS = frozenset({"subscribed"})

# (rc, stdout, stderr) of one gh invocation
Runner = Callable[[list[str], Optional[bytes], float], Awaitable[tuple[int, bytes, bytes]]]


async def _run_gh(argv: list[str], stdin: Optional[bytes], timeout: float) -> tuple[int, bytes, bytes]:
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise SourceAuthError("the GitHub CLI (gh) is not installed", fix="安装 GitHub CLI（gh）并运行 gh auth login") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    finally:
        # a timeout or a cancelled pull must not leave gh running
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
    return proc.returncode or 0, out, err


class _NotPermitted(RuntimeError):
    pass


class _ApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class GitHubSource:
    kind: ClassVar[str] = KIND
    category: ClassVar[SourceCategory] = SourceCategory.CONVERSATION
    # Each name is one endpoint family; see _api's callers for the exact paths.
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset({
        "user",                   # GET user
        "notifications",          # GET notifications
        "comment",                # GET repos/<r>/issues/comments/<id> | pulls/comments/<id>
        "issue",                  # GET repos/<r>/issues/<n> | pulls/<n>
        "issue_comments",         # GET repos/<r>/issues/<n>/comments
        "pull_review_comments",   # GET repos/<r>/pulls/<n>/comments
        "repo",                   # GET repos/<r>
        "discussion_lookup",      # GraphQL query: one discussion of a repository, by number
        "discussion_comments",    # GraphQL query: one discussion's comments
    })
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset({
        "issue_comment_create",   # POST repos/<r>/issues/<n>/comments
        "review_comment_reply",   # POST repos/<r>/pulls/<n>/comments/<id>/replies
        "discussion_comment_add", # GraphQL mutation addDiscussionComment
    })

    def __init__(self, config: SourceConfig, *, runner: Optional[Runner] = None) -> None:
        self.config = config
        opts = config.options or {}
        host = str(opts.get("hostname") or "").strip()
        if host and not _HOST.match(host):
            raise ValueError(f"invalid GitHub hostname {host!r}")
        self.hostname = host
        self._runner = runner or _run_gh
        self._gate = ToolGate(f"github:{config.id}", self.READ_TOOLS, self.WRITE_TOOLS)
        self._first_pull_s = float(opts.get("first_pull_hours", 24)) * 3600
        self._budget_s = float(opts.get("pull_budget_s", 120))
        self._max_pages = max(1, int(opts.get("max_pages", 10)))
        self._deadline: Optional[Deadline] = None
        self._me: Optional[Identity] = None
        self._login = ""
        self.feed_only = False
        self.feed_only_reason = ""
        self._private: dict[str, bool] = {}

    # -- gh ---------------------------------------------------------------------

    @property
    def _login_fix(self) -> str:
        return f"gh auth login --hostname {self.hostname}" if self.hostname else "gh auth login"

    def _argv(self, path: str, method: str, has_body: bool) -> list[str]:
        argv = ["gh", "api"]
        if self.hostname:
            argv += ["--hostname", self.hostname]
        argv += ["-X", method, "-H", "Accept: application/vnd.github+json", path]
        if has_body:
            argv += ["--input", "-"]
        return argv

    async def _api(self, tool: str, path: str, *, method: str = "GET", body: Optional[dict] = None, write: bool = False) -> Any:
        self._gate.check(tool, write=write)
        if path != "graphql" and (path.startswith("-") or not _SAFE_PATH.match(path)):
            raise ValueError(f"refusing an unsafe API path {path!r}")
        if not write and method != "GET" and path != "graphql":
            raise ValueError("a read must be a GET")
        if path == "graphql" and body is not None:
            query = str(body.get("query") or "").lstrip()
            if write != query.startswith("mutation"):
                raise ValueError("a GraphQL read must be a query, a write a mutation")
        stdin = json.dumps(body).encode("utf-8") if body is not None else None
        timeout, by_budget = (_CALL_CAP_S, False) if write else call_timeout(self._deadline, _CALL_CAP_S)
        try:
            rc, out, err = await self._runner(self._argv(path, method, stdin is not None), stdin, timeout)
        except asyncio.TimeoutError as exc:
            if by_budget:
                raise BudgetExhausted(f"gh api {tool} cut short by the pull budget") from exc
            raise
        text = err.decode("utf-8", "replace")
        if rc != 0:
            self._raise_for(rc, text, out)
        if not out.strip():
            return None
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise SourceUnavailable(f"gh api {tool}: the answer is not JSON") from exc
        if isinstance(data, dict) and data.get("errors") and path == "graphql":
            raise _ApiError(422, "; ".join(str(e.get("message", e)) for e in data["errors"] if isinstance(e, dict))[:300])
        return data

    def _raise_for(self, rc: int, err: str, out: bytes) -> None:
        low = err.lower()
        match = re.search(r"\(HTTP (\d{3})\)|HTTP (\d{3})", err)
        status = int(match.group(1) or match.group(2)) if match else 0
        if status == 401 or "bad credentials" in low or "gh auth login" in low or "not logged in" in low or "authentication required" in low:
            raise SourceAuthError("the gh login has expired or is missing", fix=self._login_fix)
        if status == 403 and ("resource not accessible" in low or "personal access token" in low or "integration" in low):
            raise _NotPermitted(err.strip()[:300])
        raise _ApiError(status, (err.strip() or out.decode("utf-8", "replace").strip())[:300] or f"gh exited {rc}")

    @staticmethod
    def _path_of(url: str) -> Optional[str]:
        """``repos/...`` from an API URL (github.com or GHES ``/api/v3``), validated."""
        if not url:
            return None
        path = urlparse(url).path.lstrip("/")
        if path.startswith("api/v3/"):
            path = path[len("api/v3/"):]
        return path if path.startswith("repos/") and _SAFE_PATH.match(path) else None

    # -- identity ----------------------------------------------------------------

    async def whoami(self) -> Identity:
        if self._me is None:
            user = await self._api("user", "user")
            if not isinstance(user, dict) or not user.get("login"):
                raise SourceUnavailable("gh api user did not return a login")
            self._login = str(user["login"])
            ids = {self._login}
            if user.get("id") is not None:
                ids.add(str(user["id"]))
            self._me = Identity(ids=frozenset(ids), display=str(user.get("name") or self._login))
            try:
                await self._api("notifications", "notifications?per_page=1")
            except _NotPermitted:
                self.feed_only = True
                self.feed_only_reason = (
                    "这个 GitHub token 读不了通知（fine-grained PAT 或 GitHub App token），只能看趋势；"
                    "用 gh auth login 换成经典登录后才能收到 @ 和 review 请求"
                )
                logger.info("github source %s: the token cannot read notifications; feed-only", self.config.id)
        return self._me

    async def can_send(self) -> bool:
        await self.whoami()
        return not self.feed_only

    @property
    def login(self) -> str:
        """The person's GitHub login, known after ``whoami``."""
        return self._login

    def status_detail(self) -> str:
        return self.feed_only_reason if self.feed_only else ""

    # -- pull --------------------------------------------------------------------

    async def pull(self, cursor: Optional[str]) -> PullResult:
        await self.whoami()
        if self.feed_only:
            return PullResult((), cursor)
        since = parse_time(cursor) if cursor else None
        since = since or datetime.now(timezone.utc) - timedelta(seconds=self._first_pull_s)
        since_q = quote(since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), safe="")
        collected: dict[str, Message] = {}
        newest = since
        complete = True
        self._deadline = Deadline(self._budget_s)
        try:
            for page in range(1, self._max_pages + 1):
                batch = await self._api("notifications", f"notifications?all=true&since={since_q}&per_page=50&page={page}")
                if not isinstance(batch, list) or not batch:
                    break
                for n in batch:
                    if not isinstance(n, dict):
                        continue
                    updated = parse_time(n.get("updated_at"))
                    if updated and updated > newest:
                        newest = updated
                    m = await self._message_for(n)
                    if m is not None:
                        old = collected.get(m.external_id)
                        collected[m.external_id] = m if old is None else replace(
                            old, mentions_me=old.mentions_me or m.mentions_me, is_direct=old.is_direct or m.is_direct
                        )
                if len(batch) < 50:
                    break
            else:
                logger.warning("github source %s: there may be more than %d notifications since the cursor; older ones are not read",
                               self.config.id, self._max_pages * 50)
        except BudgetExhausted:
            complete = False
        finally:
            self._deadline = None
        messages = tuple(sorted(collected.values(), key=lambda m: m.received_at))
        return PullResult(messages, newest.isoformat() if complete else None)

    async def _message_for(self, n: dict) -> Optional[Message]:
        repo_info = n.get("repository") or {}
        repo = str(repo_info.get("full_name") or "")
        if not _REPO.match(repo):
            return None
        self._private[repo] = bool(repo_info.get("private"))
        subject = n.get("subject") or {}
        subject_type = str(subject.get("type") or "")
        title = str(subject.get("title") or "")
        reason = str(n.get("reason") or "")
        number = _number_of(subject.get("url"))
        flags = {
            "mentions_me": reason in MENTION_REASONS,
            "is_direct": reason in DIRECT_REASONS,
            "watched": reason in WATCH_REASONS,
        }
        received = parse_time(n.get("updated_at")) or datetime.now(timezone.utc)
        base = {"host": self.hostname, "repo": repo, "subject": subject_type, "number": number}

        if subject_type == "Discussion":
            return await self._discussion_message(n, base, title, flags, received)

        latest_path = self._path_of(str(subject.get("latest_comment_url") or ""))
        obj: Any = None
        kind = "issue"
        if latest_path:
            if "/issues/comments/" in latest_path or "/pulls/comments/" in latest_path:
                obj = await self._api("comment", latest_path)
                kind = "review" if "/pulls/comments/" in latest_path else "issue"
            elif re.search(r"/(issues|pulls)/\d+$", latest_path):
                obj = await self._api("issue", latest_path)
        if number is None or subject_type not in ("Issue", "PullRequest"):
            # releases, commits, check runs: a notice without a reply thread
            return Message(
                external_id=f"notification:{n.get('id')}@{n.get('updated_at')}",
                thread=ThreadRef(KIND, {**base, "kind": "none"}),
                author_id="", author_name=repo, received_at=received, text=title, title=title,
                url=str(repo_info.get("html_url") or ""), watched=True, extra={"reason": reason, "repo": repo},
            )
        data = {**base, "kind": kind}
        if isinstance(obj, dict) and kind == "review":
            data["review_comment_id"] = int(obj.get("in_reply_to_id") or obj.get("id") or 0)
        author = (obj or {}).get("user") or {} if isinstance(obj, dict) else {}
        login = str(author.get("login") or "")
        body = str(obj.get("body") or "") if isinstance(obj, dict) else ""
        if isinstance(obj, dict) and obj.get("id") is not None:
            external_id = f"{'review_comment' if kind == 'review' else 'issue_comment'}:{obj['id']}" if "comments/" in (latest_path or "") else f"issue:{repo}#{number}@{obj.get('updated_at') or n.get('updated_at')}"
            received = parse_time(obj.get("updated_at") or obj.get("created_at")) or received
            url = str(obj.get("html_url") or "")
        else:
            external_id = f"notification:{n.get('id')}@{n.get('updated_at')}"
            url = ""
        mentions = flags["mentions_me"] or bool(self._login and re.search(rf"@{re.escape(self._login)}\b", body, re.I))
        return Message(
            external_id=external_id,
            thread=ThreadRef(KIND, data),
            author_id=login,
            author_name=login or repo,
            received_at=received,
            text=body or title,
            url=url,
            title=title,
            is_direct=flags["is_direct"],
            mentions_me=mentions,
            is_bot=author.get("type") == "Bot" or login.endswith("[bot]"),
            watched=flags["watched"],
            extra={"reason": reason, "repo": repo, "number": number},
        )

    async def _discussion_message(self, n: dict, base: dict, title: str, flags: dict, received: datetime) -> Optional[Message]:
        number = base.get("number")
        if number is None:
            # No number, no safe way to name the thread: a notice the person opens on GitHub.
            return Message(
                external_id=f"notification:{n.get('id')}@{n.get('updated_at')}",
                thread=ThreadRef(KIND, {**base, "kind": "none"}),
                author_id="", author_name=base["repo"], received_at=received, text=title, title=title,
                url=str((n.get("repository") or {}).get("html_url") or ""),
                is_direct=flags["is_direct"], mentions_me=flags["mentions_me"], watched=flags["watched"],
                extra={"reason": str(n.get("reason") or ""), "repo": base["repo"]},
            )
        owner, name = base["repo"].split("/", 1)
        query = (
            "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
            "discussion(number:$number){id number title url "
            "comments(last:1){nodes{id url body createdAt author{login} "
            "replies(last:1){nodes{id url body createdAt author{login}}}}}}}}"
        )
        data = await self._api("discussion_lookup", "graphql", method="POST",
                               body={"query": query, "variables": {"owner": owner, "name": name, "number": int(number)}})
        disc = (((data or {}).get("data") or {}).get("repository") or {}).get("discussion")
        if not isinstance(disc, dict) or not disc.get("id"):
            return None
        comment = next(iter((disc.get("comments") or {}).get("nodes") or []), None)
        reply_to = comment.get("id") if isinstance(comment, dict) else None
        latest = comment
        if isinstance(comment, dict):
            inner = next(iter((comment.get("replies") or {}).get("nodes") or []), None)
            if isinstance(inner, dict) and (parse_time(inner.get("createdAt")) or received) >= (parse_time(comment.get("createdAt")) or received):
                latest = inner
        data_ref = {**base, "number": disc.get("number"), "kind": "discussion", "discussion_id": disc.get("id")}
        if reply_to:
            data_ref["reply_to_id"] = reply_to
        latest = latest if isinstance(latest, dict) else {}
        login = str((latest.get("author") or {}).get("login") or "")
        body = str(latest.get("body") or "")
        return Message(
            external_id=f"discussion_comment:{latest['id']}" if latest.get("id") else f"discussion:{disc.get('id')}@{n.get('updated_at')}",
            thread=ThreadRef(KIND, data_ref),
            author_id=login,
            author_name=login or base["repo"],
            received_at=parse_time(latest.get("createdAt")) or received,
            text=body or title,
            url=str(latest.get("url") or disc.get("url") or ""),
            title=title,
            is_direct=flags["is_direct"],
            mentions_me=flags["mentions_me"] or bool(self._login and re.search(rf"@{re.escape(self._login)}\b", body, re.I)),
            watched=flags["watched"],
            extra={"reason": str(n.get("reason") or ""), "repo": base["repo"]},
        )

    # -- thread ---------------------------------------------------------------------

    def _parts(self, ref: ThreadRef) -> tuple[str, Optional[int], str]:
        repo = str(ref.data.get("repo") or "")
        if not _REPO.match(repo):
            raise SendFailed("the thread reference names no repository")
        number = ref.data.get("number")
        return repo, (int(number) if number is not None else None), str(ref.data.get("kind") or "issue")

    async def _is_private(self, repo: str) -> bool:
        if repo not in self._private:
            info = await self._api("repo", f"repos/{repo}")
            self._private[repo] = bool((info or {}).get("private"))
        return self._private[repo]

    async def _thread_messages(self, ref: ThreadRef) -> tuple[Message, ...]:
        repo, number, kind = self._parts(ref)
        if number is None or kind == "none":
            return ()
        raws: list[tuple[str, dict]] = []
        if kind == "discussion":
            query = (
                "query($id:ID!){node(id:$id){... on Discussion{id url title body createdAt author{login} "
                "comments(last:50){nodes{id url body createdAt author{login} "
                "replies(last:50){nodes{id url body createdAt author{login}}}}}}}}"
            )
            data = await self._api("discussion_comments", "graphql", method="POST",
                                   body={"query": query, "variables": {"id": str(ref.data.get("discussion_id") or "")}})
            node = ((data or {}).get("data") or {}).get("node") or {}
            if node:
                raws.append(("discussion", {"id": node.get("id"), "body": node.get("body"), "created_at": node.get("createdAt"),
                                            "html_url": node.get("url"), "user": {"login": (node.get("author") or {}).get("login")}}))
            for c in (node.get("comments") or {}).get("nodes") or []:
                for item in [c, *((c.get("replies") or {}).get("nodes") or [])]:
                    raws.append(("discussion_comment", {"id": item.get("id"), "body": item.get("body"), "created_at": item.get("createdAt"),
                                                        "html_url": item.get("url"), "user": {"login": (item.get("author") or {}).get("login")}}))
        elif kind == "review":
            root = int(ref.data.get("review_comment_id") or 0)
            for page in range(1, self._max_pages + 1):
                batch = await self._api("pull_review_comments", f"repos/{repo}/pulls/{number}/comments?per_page=100&page={page}")
                for c in batch or []:
                    if isinstance(c, dict) and (c.get("id") == root or c.get("in_reply_to_id") == root):
                        raws.append(("review_comment", c))
                if not isinstance(batch, list) or len(batch) < 100:
                    break
        else:
            issue = await self._api("issue", f"repos/{repo}/issues/{number}")
            if isinstance(issue, dict):
                raws.append(("issue", issue))
            for page in range(1, self._max_pages + 1):
                batch = await self._api("issue_comments", f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}")
                for c in batch or []:
                    if isinstance(c, dict):
                        raws.append(("issue_comment", c))
                if not isinstance(batch, list) or len(batch) < 100:
                    break
        out = []
        for prefix, c in raws:
            user = c.get("user") or {}
            login = str(user.get("login") or "")
            created = parse_time(c.get("created_at")) or datetime.now(timezone.utc)
            out.append(Message(
                external_id=f"{prefix}:{c.get('id')}" if prefix != "issue" else f"issue:{repo}#{number}",
                thread=ref, author_id=login, author_name=login, received_at=created,
                text=str(c.get("body") or ""), url=str(c.get("html_url") or ""),
                is_bot=user.get("type") == "Bot" or login.endswith("[bot]"),
            ))
        return tuple(sorted(out, key=lambda m: m.received_at))

    async def thread(self, ref: ThreadRef) -> Thread:
        await self.whoami()
        repo, number, kind = self._parts(ref)
        messages = await self._thread_messages(ref)
        if number is None or kind == "none":
            return Thread(ref=ref, messages=messages, target=None)
        public = not await self._is_private(repo)
        where = {"review": f"{repo}#{number} 的 review thread", "discussion": f"{repo} discussion #{number}"}.get(kind, f"{repo}#{number}")
        target = ReplyTarget(
            label=where,
            recipients=(f"{self.hostname or 'github.com'}/{repo}#{number}",),
            public=public,
            flags=("public",) if public else (),
        )
        return Thread(ref=ref, messages=messages, target=target)

    # -- reply -----------------------------------------------------------------------

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        if self.feed_only:
            raise SendFailed(self.feed_only_reason or "this GitHub source is feed-only")
        repo, number, kind = self._parts(ref)
        if number is None or kind == "none":
            raise SendFailed("this notification has no thread to reply to")
        try:
            if kind == "discussion":
                with self._gate.allow_writes("discussion_comment_add"):
                    variables = {"discussionId": str(ref.data.get("discussion_id") or ""), "body": text}
                    if ref.data.get("reply_to_id"):
                        variables["replyToId"] = str(ref.data["reply_to_id"])
                    mutation = (
                        "mutation($discussionId:ID!,$body:String!,$replyToId:ID){addDiscussionComment(input:"
                        "{discussionId:$discussionId,body:$body,replyToId:$replyToId}){comment{id url createdAt}}}"
                    )
                    data = await self._api("discussion_comment_add", "graphql", method="POST",
                                           body={"query": mutation, "variables": variables}, write=True)
                comment = (((data or {}).get("data") or {}).get("addDiscussionComment") or {}).get("comment") or {}
                return SendReceipt(external_msg_id=f"discussion_comment:{comment.get('id')}", url=str(comment.get("url") or ""),
                                   sent_at=parse_time(comment.get("createdAt")) or datetime.now(timezone.utc))
            if kind == "review":
                root = int(ref.data.get("review_comment_id") or 0)
                if not root:
                    raise SendFailed("the review thread has no comment to reply to")
                with self._gate.allow_writes("review_comment_reply"):
                    data = await self._api("review_comment_reply", f"repos/{repo}/pulls/{number}/comments/{root}/replies",
                                           method="POST", body={"body": text}, write=True)
                prefix = "review_comment"
            else:
                with self._gate.allow_writes("issue_comment_create"):
                    data = await self._api("issue_comment_create", f"repos/{repo}/issues/{number}/comments",
                                           method="POST", body={"body": text}, write=True)
                prefix = "issue_comment"
        except SourceAuthError as exc:
            # the login expired: nothing was posted, and the source needs gh auth login
            raise SendFailed(str(exc), fix=exc.fix) from exc
        except (_ApiError, _NotPermitted) as exc:
            # GitHub answered (4xx/5xx, a refused token): nothing was posted
            raise SendFailed(str(exc)) from exc
        data = data or {}
        return SendReceipt(external_msg_id=f"{prefix}:{data.get('id')}", url=str(data.get("html_url") or ""),
                           sent_at=parse_time(data.get("created_at")) or datetime.now(timezone.utc))

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]:
        me = await self.whoami()
        after = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
        for m in await self._thread_messages(ref):
            if me.is_me(m.author_id) and m.received_at > after and matches_sent(m.text, text):
                return SendReceipt(external_msg_id=m.external_id, url=m.url, sent_at=m.received_at)
        return None

    async def close(self) -> None:
        return None


def _number_of(url: Any) -> Optional[int]:
    if not isinstance(url, str):
        return None
    match = re.search(r"/(?:issues|pulls|discussions)/(\d+)$", url)
    return int(match.group(1)) if match else None


def create(config: SourceConfig) -> GitHubSource:
    if (config.backend or "gh") != "gh":
        raise ValueError(f"unknown GitHub backend {config.backend!r} (gh)")
    return GitHubSource(config)


register_source(KIND, create)
