"""A Hive run's commits back on this machine (design
docs/design/2026-10-28-project-agent-flow.md §7 ⑨, host side).

``prepare(dispatch_id, url)``: take the bundle of one declared repository and check it
here, trusting nothing the Hive reported beyond "there is a bundle with this sha256":

- the repository is one this dispatch declared (its URL, exactly), its local checkout
  is the Brain's binding for that URL, and the bundle's key is recomputed from the
  directory this host sent (core/hive_bundle.key);
- the bundle's bytes have the sha256 the run's outcome named, within the size limit;
- ``git bundle verify`` passes, it has exactly one ref, and every prerequisite is in
  this repository's history (an ancestor of its main branch or that branch's
  upstream);
- it is fetched with fsck, without tags or submodules, into
  ``refs/hive/<dispatch>/<key>`` only: no branch or tag here can move;
- the base is this machine's own ``merge-base`` of that tip and the main branch.

What the person reviews: the files with their line counts, the commits (their messages
are the Hive's text, shown as data and never put into an agent's context), and the
files under the repository's own protected paths (``.swarmai/pipeline.toml``
``[hive_return] protected``, read from this machine's main branch), which need their
own approval.

``land(dispatch_id, key, tip, approve_protected)``: only for the tip prepared and shown;
merges the reviewed change (``base..tip``; for a later turn of the same run, what came
after the tip that landed) onto the run's branch from objects only (no working tree, no
hook, no filter), as one commit with a message this host writes (the Hive's commits stay at the ``refs/hive/`` ref),
and fast-forwards the branch to it. Paths under an ignored or linked directory of the
checkout are refused at prepare (git would write a file into a virtualenv), and paths
that change git's own behaviour are protected in every repository (``GIT_PROTECTED``). Not good enough is
not recorded here: the person re-runs the task with a correction or discards it. Qualifying the change (build, tests,
deploy) is the repository's own process and the person's, after this.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

GIT_TIMEOUT_S = 120
MAX_FILES_SHOWN = 300
MAX_COMMITS_SHOWN = 50
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")


class ReturnError(ValueError):
    """The commits cannot be taken back or landed (the message says why)."""


def _state_dir(dispatch_id: str) -> Path:
    from core import swarm_out, swarm_out_results
    if not swarm_out_results.DISPATCH_ID_RE.fullmatch(dispatch_id or ""):
        raise ReturnError("not a dispatch id")
    return swarm_out._state_dir() / "returns" / dispatch_id


def _key(key: str) -> str:
    from core import hive_bundle
    if not hive_bundle.KEY_RE.fullmatch(key or ""):
        raise ReturnError("not a bundle key")
    return key


def _git(cwd: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
                           cwd=str(cwd), env=env, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReturnError(f"git could not run ({type(exc).__name__})") from None
    if check and p.returncode != 0:
        logger.warning("hive_return: git %s failed: %s", args[0], p.stderr.decode("utf-8", "replace").strip()[:300])
        raise ReturnError(f"git {args[0]} failed")
    return p


def _out(cwd: Path, *args: str) -> str:
    return _git(cwd, *args, check=True).stdout.decode("utf-8", "replace").strip()


# ── which repository, which checkout ─────────────────────────────────────────

def _row(dispatch_id: str) -> dict[str, Any]:
    from core import swarm_out
    try:
        row = swarm_out._log_row_of(dispatch_id)
    except swarm_out.SwarmOutError as e:
        raise ReturnError(str(e)) from None
    agent = row.get("project_agent")
    # a bundle is the one delivery there is; only a run from the retired branch-pr
    # delivery says otherwise (a row from before 2026-11-01 records "bundle")
    if not isinstance(agent, dict) or agent.get("delivery", "bundle") != "bundle":
        raise ReturnError("this run did not go out under a Project Agent that delivers a bundle")
    from core import hive_bundle
    if not hive_bundle.SESSION_RE.fullmatch(str(row.get("session_id") or "")):
        raise ReturnError("the Hive never named this run's session (or named it oddly)")
    return row


def _declared(row: dict, url: str) -> dict[str, Any]:
    for r in row.get("repos") or []:
        if isinstance(r, dict) and r.get("url") == url:
            return r
    raise ReturnError("this run did not declare that repository")


def _checkout(brain: str, url: str) -> Path:
    """The local checkout the Brain binds for *url*."""
    from core import task_repos
    from core.project_agent import _bindings, repo_roots
    from core.project_agent_session import PinError, brain_dir
    from core.project_registry import get_swarmws
    try:
        d = brain_dir(get_swarmws(), brain)
    except PinError as e:
        raise ReturnError(str(e)) from None
    roots = repo_roots(d)
    for name, b in _bindings(d).items():
        if task_repos._same_repo(task_repos._origin_key(b.clone), url) and name in roots:
            return roots[name]
    raise ReturnError(f"{brain} has no local checkout of this repository")


def _target_branch(repo: Path, declared_ref: Optional[str]) -> str:
    """The branch the commits land on: the one the checkout has checked out, which must
    be the run's declared ref when it declared one. The checkout must be a main
    checkout (session_worktree lands there)."""
    if _out(repo, "rev-parse", "--git-dir") != _out(repo, "rev-parse", "--git-common-dir"):
        raise ReturnError(f"{repo} is a linked worktree; the Brain's binding must point at the main checkout")
    p = _git(repo, "symbolic-ref", "--quiet", "HEAD")
    if p.returncode != 0:
        raise ReturnError(f"{repo} is on a detached HEAD; check out its branch first")
    branch = p.stdout.decode().strip()
    if declared_ref and branch != f"refs/heads/{declared_ref}":
        raise ReturnError(f"the run worked on {declared_ref}, but {repo} has "
                          f"{branch.replace('refs/heads/', '')} checked out; check out {declared_ref} first")
    return branch


def _protected(repo: Path, branch: str) -> list[str]:
    """The repository's ``[hive_return] protected`` globs, from this machine's branch."""
    from core.repo_pipeline_config import ConfigError, hive_return_protected
    try:
        return hive_return_protected(str(repo), branch)
    except ConfigError as e:
        raise ReturnError(f"{e}; fix it before taking commits back") from None


def _is_protected(path: str, globs: list[str]) -> bool:
    """Case-insensitive: on a case-insensitive disk ``.CLAUDE/x`` is written into
    ``.claude/`` (more paths protected, never fewer)."""
    import unicodedata
    p = unicodedata.normalize("NFC", path).casefold()
    for g in (unicodedata.normalize("NFC", x).casefold() for x in globs):
        if fnmatch.fnmatchcase(p, g) or (g.endswith("/**") and (p + "/").startswith(g[:-2])):
            return True
    return False


# ── prepare ──────────────────────────────────────────────────────────────────

def _bundle_fact(dispatch_id: str, declared: dict) -> dict[str, Any]:
    from core import hive_bundle, swarm_out_results
    rec = swarm_out_results.load(dispatch_id) or {}
    res = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    for f in ((res.get("work") or {}).get("repos") or []):
        if isinstance(f, dict) and f.get("dir") == declared.get("dir"):
            b = f.get("bundle")
            if not isinstance(b, dict):
                break
            if b.get("error"):
                raise ReturnError(f"the Hive could not bundle the commits: {b['error']}")
            if b.get("key") != hive_bundle.key(str(declared.get("dir"))):
                raise ReturnError("the bundle does not belong to the declared repository")
            return b
    raise ReturnError("the run's result names no bundle for this repository (refresh the result first)")


def _from_record(dispatch_id: str, b: dict, dest: Path) -> bool:
    """Copy the bundle from the run's record kept here (core/hive_records), checking its
    bytes against the fact again; False when the record does not hold it."""
    from core import hive_records
    if not isinstance(b.get("seq"), int):
        return False
    src = hive_records.bundle_path(dispatch_id, b["seq"], b["key"])
    if src is None:
        return False
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    h = hashlib.sha256()
    n = 0
    try:
        with open(src, "rb") as f, open(tmp, "wb") as out:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += len(chunk)
                if n > b["size"]:
                    break
                h.update(chunk)
                out.write(chunk)
        if n != b["size"] or h.hexdigest() != b["sha256"]:
            return False
        os.replace(tmp, dest)
        return True
    finally:
        tmp.unlink(missing_ok=True)


async def _download(row: dict, b: dict, dest: Path) -> None:
    from core import hive_bundle, swarm_out
    dest.parent.mkdir(parents=True, exist_ok=True)
    import asyncio
    if await asyncio.to_thread(_from_record, row["dispatch_id"], b, dest):
        return
    if isinstance(b.get("seq"), int):
        raise ReturnError("the run's record here does not hold this bundle yet; refresh the result, "
                          "then take the commits back")
    inst = await swarm_out._instance(row.get("hive_id") or "")
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    h = hashlib.sha256()
    n = 0
    stale = ("the Hive's bundle is not the one in the result shown (a later turn may have made a new one); "
             "refresh the result, then take the commits back")
    import httpx
    from core import hive_records

    def check(chunk: bytes) -> None:
        nonlocal n
        n += len(chunk)
        if n > hive_bundle.MAX_BUNDLE_BYTES or n > b["size"]:
            raise ReturnError(stale)
        h.update(chunk)
    try:
        async with swarm_out._client(inst, 300) as client:
            async with client.stream("GET", f"/api/hive/bundles/{row['session_id']}/{b['key']}") as r:
                if r.status_code != 200:
                    raise ReturnError(f"the Hive did not send the bundle (HTTP {r.status_code})")
                await hive_records.write_stream(r.aiter_bytes(), tmp, check)
        if n != b["size"] or h.hexdigest() != b["sha256"]:
            raise ReturnError(stale)
        await asyncio.to_thread(os.replace, tmp, dest)
    except httpx.HTTPError as e:
        raise ReturnError(f"the Hive did not answer ({type(e).__name__}); nothing was taken back") from None
    finally:
        # a cancel too: the removal runs to its end in its thread
        await asyncio.shield(asyncio.to_thread(tmp.unlink, missing_ok=True))


def _bundle_header(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """(prerequisite shas, [(sha, ref)]) from the bundle's header."""
    prereq: list[str] = []
    refs: list[tuple[str, str]] = []
    with open(path, "rb") as f:
        first = f.readline().decode("utf-8", "replace").strip()
        if first not in ("# v2 git bundle", "# v3 git bundle"):
            raise ReturnError("not a git bundle")
        for _ in range(10000):
            line = f.readline().decode("utf-8", "replace").rstrip("\n")
            if not line:
                break
            if line.startswith("@"):
                continue
            if line.startswith("-"):
                sha = line[1:].split(" ", 1)[0]
                if not _SHA_RE.fullmatch(sha):
                    raise ReturnError("the bundle's header is malformed")
                prereq.append(sha)
            else:
                sha, _, ref = line.partition(" ")
                if not _SHA_RE.fullmatch(sha) or not ref:
                    raise ReturnError("the bundle's header is malformed")
                refs.append((sha, ref))
    return prereq, refs


def _verify_and_fetch(repo: Path, branch: str, bundle: Path, local_ref: str) -> tuple[str, str]:
    """(tip, base) after every check; the tip is at *local_ref* only. Every
    prerequisite must be in *branch* itself (not only in its upstream): the commits
    are reviewed and landed against that branch."""
    prereq, refs = _bundle_header(bundle)
    if len(refs) != 1:
        raise ReturnError(f"the bundle has {len(refs)} refs; one is expected")
    if not prereq:
        raise ReturnError("the bundle carries a whole history, not commits on top of this repository")
    short = branch.replace("refs/heads/", "")
    for sha in prereq:
        if _git(repo, "merge-base", "--is-ancestor", sha, branch).returncode != 0:
            raise ReturnError(f"the bundle builds on {sha[:12]}, which is not in this machine's {short} "
                              f"(bring {short} up to date with its upstream, then take the commits back again)")
    if _git(repo, "bundle", "verify", "-q", str(bundle)).returncode != 0:
        raise ReturnError("git bundle verify failed")
    tip, ref = refs[0]
    _out(repo, "-c", "fetch.fsckObjects=true", "-c", "transfer.fsckObjects=true", "fetch", "-q",
         "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head", str(bundle),
         f"+{ref}:{local_ref}")
    if _out(repo, "rev-parse", "--verify", f"{local_ref}^{{commit}}") != tip:
        raise ReturnError("the fetched tip is not the bundle's")
    base = _out(repo, "merge-base", tip, branch)
    if not _SHA_RE.fullmatch(base):
        raise ReturnError("the commits share no history with the main branch")
    return tip, base


#: paths that change how git itself behaves on this machine (filters, drivers, LFS,
#: submodules, hooks kept in the tree): protected in every repository
GIT_PROTECTED = ("**/.gitattributes", ".gitattributes", ".lfsconfig", "**/.lfsconfig", ".gitmodules",
                 ".husky/**", ".githooks/**", ".pre-commit-config.yaml")
_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _unsafe_paths(repo: Path, paths: list[str]) -> list[str]:
    """Paths the commits may not write on this machine: under a directory this checkout
    ignores (a dependency dir such as a virtualenv), under a link, or with control or
    bidi characters (a name that would not read as what it is)."""
    out = [p for p in paths if _BAD_CHARS.search(p)]
    ok = [p for p in paths if p not in out]
    if ok:
        r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=str(repo),
                           input="\0".join(ok).encode(), capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
        out += [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]
    for p in ok:
        cur = repo
        for part in Path(p).parts[:-1]:
            cur = cur / part
            if cur.is_symlink():
                out.append(p)
                break
    return sorted(set(out))


def _review(repo: Path, base: str, tip: str, globs: list[str]) -> dict[str, Any]:
    raw = _out(repo, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", base, tip)
    globs = [*GIT_PROTECTED, *globs]
    files = []
    for entry in raw.split("\0"):
        parts = entry.split("\t", 2)
        if len(parts) == 3:
            add, dele, path = parts
            files.append({"path": path[:500], "added": int(add) if add.isdigit() else None,
                          "deleted": int(dele) if dele.isdigit() else None,
                          "protected": _is_protected(path, globs)})
    log = _out(repo, "log", f"--max-count={MAX_COMMITS_SHOWN}", "--format=%H%x1f%s%x1e", f"{base}..{tip}")
    commits = []
    for rec in log.split("\x1e"):
        sha, _, subject = rec.strip().partition("\x1f")
        if _SHA_RE.fullmatch(sha):
            commits.append({"sha": sha, "subject": subject[:300]})
    bad = _unsafe_paths(repo, [f["path"] for f in files])
    if bad:
        raise ReturnError("the commits write where they may not on this machine (an ignored or linked "
                          "directory, or a name with control characters): " + ", ".join(repr(b) for b in bad[:5]))
    return {"files": files[:MAX_FILES_SHOWN], "files_total": len(files),
            "protected_files": [f["path"] for f in files if f["protected"]], "commits": commits}


def _save(dispatch_id: str, key: str, state: dict) -> None:
    d = _state_dir(dispatch_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{key}.json.tmp"
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, d / f"{key}.json")


def load(dispatch_id: str, key: str) -> Optional[dict]:
    try:
        return json.loads((_state_dir(dispatch_id) / f"{_key(key)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def prepare(dispatch_id: str, url: str) -> dict[str, Any]:
    """Take back, check and describe one repository's commits (nothing lands)."""
    import asyncio
    _state_dir(dispatch_id)                       # a dispatch id, before any path is built
    row = await asyncio.to_thread(_row, dispatch_id)
    declared = _declared(row, url)
    b = await asyncio.to_thread(_bundle_fact, dispatch_id, declared)
    repo = await asyncio.to_thread(_checkout, row["project_agent"]["brain"], url)
    bundle = _state_dir(dispatch_id) / f"{b['key']}.bundle"
    await _download(row, b, bundle)
    local_ref = f"refs/hive/{dispatch_id}/{b['key']}"
    before = load(dispatch_id, b["key"]) or {}

    def _work() -> dict[str, Any]:
        branch = _target_branch(repo, declared.get("ref"))
        tip, base = _verify_and_fetch(repo, branch, bundle, local_ref)
        landed = before.get("landed_tip")
        if landed == tip:
            raise ReturnError("these commits already landed")
        if landed:
            # a later turn's commits: only what came after the landed tip is new
            if _git(repo, "merge-base", "--is-ancestor", landed, tip).returncode != 0:
                raise ReturnError("the Hive rewrote the commits that already landed; review them by hand "
                                  f"from {local_ref}")
            base = landed
        review = _review(repo, base, tip, _protected(repo, branch))
        dirty = bool(_out(repo, "status", "--porcelain", "--untracked-files=no"))
        state = {**{k: before[k] for k in ("landed_tip", "landed", "landed_at") if k in before},
                 "dispatch_id": dispatch_id, "url": url, "key": b["key"], "repo": str(repo),
                 "branch": branch, "ref": local_ref, "tip": tip, "base": base, **review,
                 # uncommitted edits in the checkout may make the landing refuse
                 "checkout_dirty": dirty,
                 "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "prepared"}
        _save(dispatch_id, b["key"], state)
        return state
    return await asyncio.to_thread(_work)


# ── land ────────────────────────────────────────────────────────────

def _prepared(dispatch_id: str, key: str, tip: str) -> dict[str, Any]:
    state = load(dispatch_id, key)
    if not state or state.get("status") != "prepared":
        raise ReturnError("these commits were not prepared for review (take them back first)")
    if not tip or state.get("tip") != tip:
        raise ReturnError("the commits changed since they were shown; review them again")
    return state


#: git settings for every command that could check files out or run something: no
#: hooks, no fsmonitor, git-lfs off (its filter would fetch from an .lfsconfig in the
#: Hive's tree)
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "filter.lfs.process=",
         "-c", "filter.lfs.smudge=", "-c", "filter.lfs.clean=", "-c", "filter.lfs.required=false")


# ── one changed file, both versions (read-only) ─────────────────────────────

#: the largest version of a file shown (above it: listed, never cut)
FILE_VERSION_MAX_BYTES = 1 << 20
#: how far a version is looked into for a NUL byte (git's own binary test)
_BINARY_SNIFF = 8000


class ReturnPathError(ReturnError):
    """The path asked for is not one of the prepared files (the route answers 400)."""


def _listed_path(path: str) -> str:
    """*path* as asked, when it could be a file the review lists: relative, no ``..``
    or ``.`` segment, no control or invisible character. Whether it IS listed is
    checked against the state."""
    if not isinstance(path, str) or not path or len(path) > 500:
        raise ReturnPathError("name one of the changed files")
    if _BAD_CHARS.search(path):
        raise ReturnPathError("that path has control or invisible characters")
    if path.startswith("/"):
        raise ReturnPathError("that path is absolute; name one of the changed files")
    if any(seg in ("", ".", "..") for seg in path.split("/")):
        raise ReturnPathError("that path is not one of the changed files")
    return path


def _version(repo: Path, rev: str, path: str) -> dict[str, Any]:
    """One side of a file from git's objects only (``cat-file``: no working tree, no
    filter, no textconv, no hook): {exists, text, binary, too_large}."""
    spec = f"{rev}:{path}"
    t = _git(repo, *_SAFE, "cat-file", "-t", spec)
    if t.returncode != 0:
        return {"exists": False, "text": None, "binary": False, "too_large": False}
    if t.stdout.decode().strip() != "blob":         # a submodule's commit, a tree
        return {"exists": True, "text": None, "binary": True, "too_large": False}
    size = _out(repo, *_SAFE, "cat-file", "-s", spec)
    if not size.isdigit():
        raise ReturnError("git cat-file failed")
    if int(size) > FILE_VERSION_MAX_BYTES:
        return {"exists": True, "text": None, "binary": False, "too_large": True}
    data = _git(repo, *_SAFE, "cat-file", "blob", spec, check=True).stdout
    if b"\0" in data[:_BINARY_SNIFF]:
        return {"exists": True, "text": None, "binary": True, "too_large": False}
    return {"exists": True, "text": data.decode("utf-8", "replace"), "binary": False, "too_large": False}


def file_versions(dispatch_id: str, url: str, path: str) -> dict[str, Any]:
    """Both versions of one file the prepared (or landed) review lists: at the reviewed
    base and at the Hive's tip, read from this machine's git objects only. Nothing is
    written; a path the review does not list is refused before git is asked."""
    _state_dir(dispatch_id)
    path = _listed_path(path)
    from core import hive_bundle
    declared = _declared(_row(dispatch_id), url)
    state = load(dispatch_id, hive_bundle.key(str(declared.get("dir"))))
    if not state or state.get("status") not in ("prepared", "landed") or state.get("url") != url:
        raise ReturnError("these commits were not taken back for review yet (take them back first)")
    if path not in {f.get("path") for f in state.get("files") or [] if isinstance(f, dict)}:
        raise ReturnPathError("that path is not one of the changed files")
    base, tip = str(state.get("base") or ""), str(state.get("tip") or "")
    if not _SHA_RE.fullmatch(base) or not _SHA_RE.fullmatch(tip):
        raise ReturnError("the review's state is unreadable; take the commits back again")
    repo = Path(state["repo"])
    before, after = _version(repo, base, path), _version(repo, tip, path)
    binary = before["binary"] or after["binary"]
    too_large = not binary and (before["too_large"] or after["too_large"])
    hide = binary or too_large
    return {"path": path, "base_sha": base, "tip_sha": tip,
            "base": None if hide else before["text"], "tip": None if hide else after["text"],
            "base_exists": before["exists"], "tip_exists": after["exists"],
            "binary": binary, "too_large": too_large}


#: the longest landing summary taken (refused past it, never cut)
SUMMARY_MAX = 4000


def landing_message(title: str, hive: Any, dispatch_id: str, ref: str, files: list,
                    protected: list, summary: Optional[str]) -> str:
    """The landed commit's message, written on this machine: the run's title, the summary
    the landing's reviewer wrote (what changed and why), and the files with their line
    counts as prepare computed them. The Hive's own commit messages are its text and are
    never copied here (an agent reads this log later); they stay at *ref*."""
    msg = f"hive: {title}"
    if summary:
        msg += "\n\n" + summary
    rows = [f"  {f.get('path')} (+{f.get('added', 0)} -{f.get('deleted', 0)})"
            if isinstance(f.get("added"), int) else f"  {f.get('path')} (binary)" for f in files]
    if rows:
        msg += "\n\nFiles:\n" + "\n".join(rows)
    msg += (f"\n\nThe commits of a Hive run ({hive}, dispatch {dispatch_id}), reviewed and "
            f"squashed on this machine. The Hive's own commits are at {ref}.")
    if protected:
        msg += ("\n\nChanges protected paths: " + ", ".join(protected[:20])
                + (" …" if len(protected) > 20 else ""))
    return msg


def _summary(summary: Optional[str]) -> Optional[str]:
    if summary is None:
        return None
    text = "\n".join(line.rstrip() for line in str(summary).strip().splitlines())
    if len(text) > SUMMARY_MAX:
        raise ReturnError(f"the landing summary is {len(text)} characters; at most {SUMMARY_MAX}")
    if _BAD_CHARS.search(text.replace("\n", "").replace("\t", "")):
        # the same set refused in a path: control, zero-width and direction characters
        raise ReturnError("the landing summary has control or invisible characters")
    return text or None


def land(dispatch_id: str, key: str, tip: str, approve_protected: bool = False,
         allow_git_exec: bool = True, summary: Optional[str] = None) -> dict[str, Any]:
    """Land the prepared tip the person or the dispatching agent approved; commits that
    change the repository's protected paths need ``approve_protected``, and the landed
    commit's message names those paths. The agent's landing (the route passes
    ``approve_protected`` False and ``allow_git_exec`` False for it) refuses every
    protected path; ``allow_git_exec`` names the git hooks and filters in the refusal,
    which run code on this machine. The commit is built from objects only
    (``merge-tree --write-tree`` of the branch and the tip over the reviewed base, then
    ``commit-tree``): no working tree holds the Hive's files and no hook runs until the
    main checkout fast-forwards to it, which runs no hook either."""
    from core import swarm_out
    summary = _summary(summary)
    state = _prepared(dispatch_id, key, tip)
    repo = Path(state["repo"])
    if _git(repo, "rev-parse", "--verify", "--quiet", f"{state['ref']}^{{commit}}").stdout.decode().strip() != tip:
        raise ReturnError("the ref of these commits moved since they were shown; review them again")
    git_exec = [p for p in state["protected_files"] if _is_protected(p, list(GIT_PROTECTED))]
    if git_exec and not allow_git_exec:
        raise ReturnError("these commits change files that make this machine run code (git hooks, "
                          "filters, submodules): " + ", ".join(git_exec[:10])
                          + "; an agent does not land them: re-run asking the Hive to leave them out")
    if state["protected_files"] and not approve_protected:
        raise ReturnError("the commits change protected paths (they guard the agent or run code "
                          "here): " + ", ".join(state["protected_files"][:10])
                          + "; an agent does not land them: re-run asking the Hive to leave them "
                          "out, or make the change yourself; the person may approve them")
    if state["files_total"] > len(state["files"]):
        raise ReturnError(f"the commits change {state['files_total']} files, more than the review shows; "
                          f"land them by hand from {state['ref']}")
    branch = _target_branch(repo, state["branch"].replace("refs/heads/", ""))
    head = _out(repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
    mt = _git(repo, *_SAFE, "merge-tree", "--write-tree", "--no-messages", f"--merge-base={state['base']}",
              head, tip)
    tree = mt.stdout.decode().strip().splitlines()[0] if mt.stdout else ""
    if mt.returncode == 1:
        raise ReturnError(f"the commits do not apply onto {branch.replace('refs/heads/', '')} (a conflict); "
                          f"land them by hand from {state['ref']}")
    if mt.returncode != 0 or not _SHA_RE.fullmatch(tree):
        raise ReturnError("git merge-tree failed (git 2.40 or newer is needed)")
    if tree == _out(repo, "rev-parse", f"{head}^{{tree}}"):
        raise ReturnError("the commits change nothing on the branch now")
    from core.run_protocol.landing import LandingError, _ident_env, landing_settings, with_trailer
    row = swarm_out._log_row_of(dispatch_id)
    title = " ".join(str(row.get("title") or "").split())[:72] or "a Hive run"
    msg = landing_message(title, row.get("hive"), dispatch_id, state["ref"], state["files"],
                          state["protected_files"], summary)
    try:   # the repository's own author and trailer ([landing] in .swarmai/pipeline.toml)
        settings = landing_settings(str(repo), branch)
    except LandingError as e:
        raise ReturnError(str(e)) from None
    c = subprocess.run(["git", *_SAFE, "commit-tree", tree, "-p", head, "-F", "-"], cwd=str(repo),
                       input=with_trailer(msg, settings["trailer"]).encode(),
                       env={**os.environ, **_ident_env(settings["author"]), "LC_ALL": "C"},
                       capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    new = c.stdout.decode().strip()
    if c.returncode != 0 or not _SHA_RE.fullmatch(new):
        logger.warning("hive_return: commit-tree failed: %s", c.stderr.decode("utf-8", "replace")[:300])
        raise ReturnError("the landing commit could not be made")
    # the landing's record, as session_worktree stamps it (design 2026-10-25-turn-result-card §3)
    from scripts import session_worktree as sw
    skey, turn = sw._attribution()
    stamp = f"swarm-land hive/{dispatch_id} session={skey} turn={turn} onto={head}"
    m = subprocess.run(["git", *_SAFE, "-c", "core.logAllRefUpdates=always", "merge", "--ff-only", "-q", new],
                       cwd=str(repo), env={**os.environ, "GIT_REFLOG_ACTION": stamp, "LC_ALL": "C"},
                       capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    if m.returncode != 0:
        logger.warning("hive_return: ff into %s refused: %s", branch, m.stderr.decode("utf-8", "replace")[:300])
        raise ReturnError(f"{branch.replace('refs/heads/', '')} could not fast-forward (it moved, or the checkout "
                          f"has uncommitted edits to these files); the commit {new[:12]} is not landed")
    state.update({"status": "landed", "landed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "landed": new, "landed_tip": tip})
    _save(dispatch_id, key, state)
    from core import swarm_out_settle
    swarm_out_settle.mark_code_landed(dispatch_id)     # accepted: no turn adds commits now
    return {"landed": True, "head": new[:12], "into": branch.replace("refs/heads/", ""),
            "protected_files": state["protected_files"][:20]}

