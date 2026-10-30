"""The repositories a dispatched task works on, and what the task did to each.

Design: docs/design/2026-10-28-swarm-out-run-results.md §4.4. A Swarm Out dispatch
carries a list of repositories (``[{url, ref}]``). The Host writes them into the task
text with the directory each one goes to; the Hive records them on the session
(``sessions.metadata.task_repos``). The Hive's agent clones or fetches each one there;
for a repository with a ``sha`` (the host's own commit) it also fetches the bundle the
host left in ``Hive/incoming/`` (core/hive_repo_inbox) and checks that commit out.
At every turn end the Hive reads, for each declared repository, what the task changed
against the version it pulled (``repo_facts``), for the ``turn_outcome`` block.

The directory comes from the URL alone (``repo_dir``), so the Host and the Hive name
the same one without asking each other. Only the git commands below run, with fixed
arguments, no shell, external diff, textconv and fsmonitor off, and only inside
``<workspace>/Hive/repos/``. The working tree is never read: the Hive's agent can
write a repository's config (and a submodule's), and git runs configured commands
(filter drivers) whenever it reads working-tree files. So only commits are counted
(``base..HEAD``, tree against tree) plus the number of untracked files, and no command
of the repository's own runs here; uncommitted changes are not measured.

What the task changed is measured from a fixed point: the declared ``sha`` when there is
one, else the upstream as it was when the task pulled it, read from the remote-tracking
ref's reflog at the session's start (``_pulled_upstream``), so a task that pushes to the
declared branch still shows its commits instead of moving its own base.

- ``parse(items)``        — validated ``[{url, ref, dir}]``; raises ``TaskRepoError``.
- ``repo_dir(url)``       — the workspace-relative directory of a repository.
- ``task_text(repos)``    — the paragraph the Host puts before the task.
- ``repo_facts(workspace, repos, since)`` — per repository: present, origin, base,
  commits, changed files, untracked count, pushed. Blocking (runs git): call it in a
  pool. ``since``: the session's start (unix seconds), the point the base is read at.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: a task names at most this many repositories
MAX_REPOS = 10
#: per repository in a turn_outcome: commits and files listed (the totals are exact)
MAX_COMMITS = 20
MAX_FILES = 50
#: hang guard for one git command on a local repository
GIT_TIMEOUT_S = 10

#: every ``error`` a repository fact can carry (``/runs`` passes only these through)
ERR_OUTSIDE = "the directory leads outside Hive/repos"
ERR_NO_HEAD = "the repository has no commit checked out"
ERR_NO_REF = "the declared ref is not among the repository's remote refs"
ERR_NO_HISTORY = "HEAD and the declared ref share no history"
ERR_NO_COMMIT = "the person's commit is not in the repository"
ERR_DIFF = "git diff failed"
ERR_TIMEOUT = "reading the repository timed out"
ERROR_UNREADABLE = "could not read the repository"
ERRORS = (ERR_OUTSIDE, ERR_NO_HEAD, ERR_NO_REF, ERR_NO_HISTORY, ERR_NO_COMMIT, ERR_DIFF,
          ERR_TIMEOUT, ERROR_UNREADABLE)

REPOS_ROOT = ("Hive", "repos")

_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+"
_HTTPS_RE = re.compile(rf"https://(?P<host>{_HOST})(?::\d{{1,5}})?/(?P<path>{_SEG}(?:/{_SEG}){{1,5}})/?")
_SSH_RE = re.compile(rf"git@(?P<host>{_HOST}):(?P<path>{_SEG}(?:/{_SEG}){{1,5}})")
_REF_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,99}")
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")


class TaskRepoError(ValueError):
    """A declared repository that is not accepted; the message is safe to show."""


def _split(url: str) -> tuple[str, list[str]]:
    """(lowercase host, path segments without a trailing ``.git``) of an accepted URL."""
    m = _HTTPS_RE.fullmatch(url) or _SSH_RE.fullmatch(url)
    if m is None:
        raise TaskRepoError(
            f"repository {url[:120]!r} is not an https:// or git@host:owner/name URL")
    segs = m.group("path").split("/")
    if segs[-1].endswith(".git"):
        segs[-1] = segs[-1][:-4]
    if any(s in ("", ".", "..") or s.startswith(".") for s in segs):
        raise TaskRepoError(f"repository {url[:120]!r} has an unexpected path")
    return m.group("host").lower(), segs


def repo_dir(url: str) -> str:
    """``Hive/repos/<host>/<owner>/<name>`` for an accepted URL (``/``-separated)."""
    host, segs = _split(url)
    return "/".join([*REPOS_ROOT, host, *segs])


def _same_repo(a: str, b: str) -> bool:
    """Two accepted URLs name the same repository (``https`` and ``git@`` forms of
    one host/path are the same; ``.git`` and case of the host do not matter)."""
    try:
        return _split(a) == _split(b)
    except TaskRepoError:
        return False


def parse(items: Any) -> list[dict[str, Optional[str]]]:
    """Validated ``[{url, ref, dir[, sha]}]`` (``ref`` None = the remote's default branch;
    ``sha``: the host's commit, its bundle kept on the Hive, core/hive_repo_inbox),
    one entry per directory. Raises TaskRepoError on anything not accepted."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise TaskRepoError("repos must be a list")
    if len(items) > MAX_REPOS:
        raise TaskRepoError(f"a task names at most {MAX_REPOS} repositories")
    out: list[dict[str, Optional[str]]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise TaskRepoError("each repository needs a url")
        url = item["url"].strip()
        if len(url) > 300:
            raise TaskRepoError("a repository URL is longer than 300 characters")
        ref = item.get("ref")
        if ref is not None:
            if not isinstance(ref, str) or not _REF_RE.fullmatch(ref) or ".." in ref \
                    or ref.endswith((".lock", "/", ".")) or "//" in ref:
                raise TaskRepoError(f"ref {str(ref)[:60]!r} is not a branch or tag name")
        sha = item.get("sha")
        if sha is not None and not (isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
            raise TaskRepoError("a repository's commit is not a sha")
        if sha is not None and ref is None:
            raise TaskRepoError("a repository with a commit needs its branch")
        d = repo_dir(url)
        if d in seen:
            continue
        seen.add(d)
        entry: dict[str, Optional[str]] = {"url": url, "ref": ref, "dir": d}
        if sha is not None:
            entry["sha"] = sha
        out.append(entry)
    return out


def task_text(repos: list[dict[str, Optional[str]]]) -> str:
    """The paragraph that tells the Hive's agent where each repository goes and, for one
    with the person's own commit, how to put it there (the commands run from the
    workspace; the bundle path inside ``git -C`` is relative to the repository)."""
    if not repos:
        return ""
    from core.hive_repo_inbox import bundle_rel
    lines = ["Repositories for this task (relative to your workspace; change code only "
             "inside these directories):"]
    for r in repos:
        d = r["dir"]
        if r.get("sha"):
            bundle = bundle_rel(r["sha"])
            from_repo = "/".join([".."] * len(d.split("/")) + [bundle])
            lines += [
                f"- {d}: the person's own commit {r['sha'][:12]} of {r['url']}, branch {r['ref']} "
                "(newer than the remote). Before working, run from your workspace:",
                f"  1. if {d} does not exist: git clone {r['url']} {d}; otherwise: git -C {d} fetch origin",
                f"     (if the clone is refused and {bundle} exists: git clone {bundle} {d} && "
                f"git -C {d} remote set-url origin {r['url']})",
                f"  2. if {bundle} exists: git -C {d} fetch {from_repo} HEAD",
                f"  3. git -C {d} checkout -B {r['ref']} {r['sha']}",
                "  Then do not pull or reset it; what you change is counted from this commit.",
            ]
        else:
            ref = r.get("ref") or "the default branch"
            lines.append(f"- {r['url']} @ {ref} -> {d} (clone it if the directory does not "
                         "exist, otherwise fetch and check out the ref)")
    return "\n".join(lines) + "\n\n"


# ── facts ─────────────────────────────────────────────────────────────────────

_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}


def _git(cwd: str, *args: str) -> Optional[str]:
    """stdout of one read-only git command, or None when it failed."""
    env = {**os.environ, **_GIT_ENV}
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", *args],
                           cwd=cwd, env=env, capture_output=True, timeout=GIT_TIMEOUT_S,
                           check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", "replace")


#: bytes of a ref's reflog read at most, from its end (the newest updates)
MAX_REFLOG_BYTES = 1 << 20


def _pulled_upstream(cwd: str, upstream: str, since: Optional[float]) -> Optional[str]:
    """The upstream commit the task started from, from the remote-tracking ref's own
    reflog (``old new who ts tz<TAB>message`` per update): the first update at or after
    ``since`` tells it — a clone/fetch/pull brought the task its start (its new value),
    anything else (the task's own push) moved away from it (its old value). None when
    no update came after ``since`` (the ref's current value is then the start), or the
    reflog does not say (none kept, a tag, no ``since``)."""
    if since is None:
        return None
    ref = upstream
    if ref == "refs/remotes/origin/HEAD":
        target = (_git(cwd, "symbolic-ref", "-q", ref) or "").strip()
        if not target.startswith("refs/remotes/origin/"):
            return None
        ref = target
    if not ref.startswith("refs/remotes/origin/"):
        return None                          # a tag: it does not move
    log = (_git(cwd, "rev-parse", "--git-path", f"logs/{ref}") or "").strip()
    if not log:
        return None
    raw = _read_tail(Path(cwd) / log)
    if raw is None:
        return None
    for line in raw.splitlines():            # oldest first
        head, _, message = line.partition("\t")
        parts = head.split()
        if len(parts) < 4 or not (_SHA_RE.fullmatch(parts[0]) and _SHA_RE.fullmatch(parts[1])):
            continue
        try:
            ts = int(parts[-2])
        except ValueError:
            continue
        if ts < since:
            continue
        value = parts[1] if message.startswith(("clone", "fetch", "pull")) else parts[0]
        if set(value) == {"0"}:
            # the task created this branch by pushing it: it started from the
            # remote's default branch
            default = (_git(cwd, "rev-parse", "--verify", "--quiet",
                            "refs/remotes/origin/HEAD^{commit}") or "").strip()
            return default if _SHA_RE.fullmatch(default) else None
        return value
    return None


def _read_tail(path: Path) -> Optional[str]:
    """The last MAX_REFLOG_BYTES of the file, from its first whole line; None when it
    cannot be read. The Hive's agent can put anything at that path: a FIFO would block
    a plain open() forever, so it is opened non-blocking and not through a symlink (a
    FIFO then reads as empty, a directory fails to read)."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return None
    try:
        start = max(0, os.fstat(fd).st_size - MAX_REFLOG_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        data = os.read(fd, MAX_REFLOG_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    text = data.decode("utf-8", "replace")
    if start > 0:
        text = text.partition("\n")[2]      # the first line is cut
    return text


def _origin_key(origin: str) -> str:
    """An origin URL in the form ``_split`` reads: userinfo removed from ``https://``
    (a token clone) and ``ssh://git@host[:port]/path`` as ``git@host:path``."""
    o = origin.strip()
    m = re.fullmatch(r"https://[^/@\s]+@(.+)", o)
    if m:
        return "https://" + m.group(1)
    m = re.fullmatch(r"ssh://git@([^/:\s]+)(?::\d{1,5})?/(.+)", o)
    if m:
        return f"git@{m.group(1)}:{m.group(2)}"
    return o


def _inside(root: Path, path: Path) -> bool:
    try:
        return os.path.commonpath([str(root), str(path)]) == str(root)
    except ValueError:
        return False


def _numstat(out: str) -> tuple[list[dict[str, Any]], int, int, int]:
    """(files listed, total files, added, deleted) of ``git diff --numstat -z``."""
    files: list[dict[str, Any]] = []
    total = added = deleted = 0
    for entry in out.split("\0"):
        if not entry:
            continue
        parts = entry.split("\t", 2)
        if len(parts) != 3:
            continue
        a, d, path = parts
        a_n = int(a) if a.isdigit() else None        # "-" for a binary file
        d_n = int(d) if d.isdigit() else None
        total += 1
        added += a_n or 0
        deleted += d_n or 0
        if len(files) < MAX_FILES:
            files.append({"path": path[:300], "added": a_n, "deleted": d_n})
    return files, total, added, deleted


def _one(workspace: Path, repo: dict[str, Optional[str]], since: Optional[float] = None) -> dict[str, Any]:
    fact: dict[str, Any] = {"url": repo["url"], "ref": repo.get("ref"), "dir": repo["dir"],
                            "present": False, "origin_matches": None, "base": None,
                            "head": None, "commits": [], "commits_total": 0, "files": [],
                            "files_total": 0, "added": 0, "deleted": 0, "untracked": 0,
                            "pushed": "unknown", "error": None}
    root = (workspace / Path(*REPOS_ROOT)).resolve()
    target = (workspace / repo["dir"])
    try:
        real = target.resolve()
    except OSError:
        return fact
    if not _inside(root, real):
        fact["error"] = ERR_OUTSIDE
        return fact
    if not real.is_dir():
        return fact
    top = _git(str(real), "rev-parse", "--show-toplevel")
    if top is None or Path(top.strip()).resolve() != real:
        return fact                                    # not a repository of its own
    fact["present"] = True
    origin = _git(str(real), "remote", "get-url", "origin")
    fact["origin_matches"] = bool(origin) and _same_repo(_origin_key(origin), repo["url"])
    if not fact["origin_matches"]:
        return fact                                    # not the declared repository
    head = (_git(str(real), "rev-parse", "--verify", "--quiet", "HEAD") or "").strip()
    fact["head"] = head if _SHA_RE.fullmatch(head) else None
    ref = repo.get("ref")
    candidates = [f"refs/remotes/origin/{ref}", f"refs/tags/{ref}"] if ref else ["refs/remotes/origin/HEAD"]
    upstream = None
    for c in candidates:
        if _git(str(real), "rev-parse", "--verify", "--quiet", c + "^{commit}") is not None:
            upstream = c
            break
    if fact["head"] is None:
        fact["error"] = ERR_NO_HEAD
        return fact
    declared = repo.get("sha")
    if declared:
        # the host's commit the task was told to start from; not fetched = said, never
        # replaced by a guess from the remote
        if _git(str(real), "cat-file", "-e", f"{declared}^{{commit}}") is None:
            fact["error"] = ERR_NO_COMMIT
            return fact
        start = declared
    elif upstream is None:
        fact["error"] = ERR_NO_REF
        return fact
    else:
        start = _pulled_upstream(str(real), upstream, since) or upstream
    base = (_git(str(real), "merge-base", "HEAD", start) or "").strip()
    if not _SHA_RE.fullmatch(base):
        fact["error"] = ERR_NO_HISTORY
        return fact
    fact["base"] = base
    count = (_git(str(real), "rev-list", "--count", f"{base}..HEAD") or "").strip()
    fact["commits_total"] = int(count) if count.isdigit() else 0
    listed = _git(str(real), "rev-list", f"--max-count={MAX_COMMITS}", f"{base}..HEAD") or ""
    fact["commits"] = [s for s in listed.split() if _SHA_RE.fullmatch(s)]
    # commits only, tree against tree: the working tree is never read (module doc)
    diff = _git(str(real), "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff",
                "--no-textconv", base, "HEAD")
    if diff is None:
        fact["error"] = ERR_DIFF
    else:
        fact["files"], fact["files_total"], fact["added"], fact["deleted"] = _numstat(diff)
    untracked = _git(str(real), "ls-files", "--others", "--exclude-standard", "-z")
    fact["untracked"] = sum(1 for e in (untracked or "").split("\0") if e)
    if fact["commits_total"] == 0:
        fact["pushed"] = "nothing_to_push"
    else:
        remote = _git(str(real), "branch", "-r", "--contains", "HEAD")
        fact["pushed"] = "unknown" if remote is None else ("yes" if remote.strip() else "no")
    return fact


def repo_facts(workspace: str, repos: Iterable[dict[str, Optional[str]]],
               since: Optional[float] = None) -> list[dict[str, Any]]:
    """What the task did to each declared repository (one entry each, in order). A
    repository whose read fails carries ``error``; the others are unaffected."""
    ws = Path(workspace)
    out = []
    for r in repos:
        try:
            out.append(_one(ws, r, since))
        except Exception as exc:  # noqa: BLE001 — one repository never hides the others
            out.append({"url": r.get("url"), "ref": r.get("ref"), "dir": r.get("dir"),
                        "present": None, "error": ERROR_UNREADABLE})
            logger.warning("task_repos: reading %s failed (%s)", r.get("dir"), type(exc).__name__)
    return out
