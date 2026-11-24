"""A Hive run's commits back on this machine (design
docs/design/2026-10-28-project-agent-flow.md §7 ⑨, host side).

``prepare(dispatch_id, url)``: take the bundle of one declared repository and check it
here, trusting nothing the Hive reported beyond "there is a bundle with this sha256":

- the repository is one this dispatch declared (its URL, exactly), its local checkout
  is the Brain's binding for that URL, and the bundle's key is recomputed from the
  directory this host sent (core/hive_bundle.key);
- the bundle's bytes have the sha256 the run's outcome named, within the size limit;
- ``git bundle verify`` passes, it has exactly one ref, and every prerequisite is an
  ancestor of this repository's main branch (the branch itself, not its upstream: a
  prerequisite only the upstream has is refused until main is brought up to date), or
  it has its equivalent there, as when main's history was rewritten since the
  dispatch: the prerequisite is still on this machine and exactly one commit of main
  makes the same change (``git patch-id --stable``, among the newest
  ``EQUIVALENT_WINDOW`` commits since their merge-base; author and subject decide
  between several);
- it is fetched with fsck, without tags or submodules, into
  ``refs/hive/<dispatch>/<key>`` only: no branch or tag here can move;
- the base is this machine's own ``merge-base`` of that tip and the main branch. When a
  prerequisite was mapped to its equivalent, the bundle is fetched into
  ``refs/hive/<dispatch>/<key>-hive`` only and the Hive's commits are replayed onto that
  equivalent from git objects only (no worktree, no hook: ``_replay``); the replayed
  commits are what is reviewed and landed, and only then does ``refs/hive/<dispatch>/<key>``
  name them, so a next round that starts from that ref never ships the history main
  rewrote (the ``-hive`` ref is never shipped). The review says ``base_mapped``: the old
  base, its equivalent and the paths where the two bases' trees differ (what the Hive
  built against that main no longer has). A replay that conflicts is refused naming the
  files; the Hive's own commits stay at the ``-hive`` ref for a takeover by hand.

``kept_here(dispatch_id, dir)``: whether a take-back that ``prepare`` refused after it
fetched the tip (a replay conflict, a merge) still left the commits on this host: the
bundle kept in the run's state dir is
the one the result names, verifies in the checkout, and its tip is at the run's ref or its
``-hive`` ref (core/swarm_out_actor.commits_here: a discard then loses nothing).

What the person reviews: the files with their line counts in review order
(``core.review_order``: source, then tests, then supporting files), the commits (their messages
are the Hive's text, shown as data and never put into an agent's context), and the
protected files, marked so the reviewer reads them with care. Protected are, in every
repository, the person-only paths (``PERSON_ONLY``: what changes how git behaves here,
``GIT_PROTECTED``, and what runs on push or configures a coding agent, CI pipelines,
agent settings, MCP servers, SwarmAI's own ``.swarmai/`` and AGENTS.md / CLAUDE.md at
any depth, ``CI_AGENT_PROTECTED``), plus the repository's own globs
(``.swarmai/pipeline.toml`` ``[hive_return] protected``, read from this machine's main
branch). Those globs only widen what is marked: no setting removes a person-only path
(a default one file in the repository could switch off would protect nothing where that
file says so). The repository's globs land with the rest; the landed commit names every
protected file.

``import_unrecorded(dispatch_id, row, declared)``: for a re-run on Elastic Workers
(core/carriers/agentcore ``_returned_starts``): when the newest bundle of a declared
repository in the run's kept record is named by no turn outcome (its turn was cut off
before its record was sealed), fetch it into ``refs/hive/<dispatch>/<key>`` with the same
checks ``prepare`` makes (the bytes against the record's sha256 and size, verify, one
ref, prerequisites in the main branch or with their one equivalent there, fsck; a mapped
one is replayed as ``prepare`` replays it), so the next round starts from it. Nothing
is shown or landed from it: the person reviews what the next round returns.

``land(dispatch_id, key, tip, allow_git_exec)``: only for the tip prepared and shown;
merges the reviewed change (``base..tip``; for a later turn of the same run, what came
after the tip that landed) onto the run's branch from objects only (no working tree, no
hook, no filter), as one commit with a message this host writes (the Hive's commits stay at the ``refs/hive/`` ref),
and fast-forwards the branch to it; the message ends with the repository's own landing
trailer and ``Hive-Dispatch: <dispatch id>``. Paths under an ignored or linked directory of the
checkout are refused at prepare (git would write a file into a virtualenv), and the
person-only paths (``PERSON_ONLY``) are protected in every repository. Not good enough is
not recorded here: the person re-runs the task with a correction or discards it. Qualifying the change (build, tests,
deploy) is the repository's own process and the person's, after this.

``verify_hand_landing(states, commits, dropped)``: when ``land`` refused (a conflict, a
change larger than the review shows), the dispatching agent lands the change itself and
names the commits; git here checks they are on the prepared branch, after the base, and
carry the run's change to the returned files (core/swarm_out_settle.set_landed_by_hand records it).
"""
from __future__ import annotations

import fnmatch
from collections import Counter
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
    from core import hive_bundle
    dest.parent.mkdir(parents=True, exist_ok=True)
    import asyncio
    if await asyncio.to_thread(_from_record, row["dispatch_id"], b, dest):
        return
    if isinstance(b.get("seq"), int):
        raise ReturnError("the run's record here does not hold this bundle yet; refresh the result, "
                          "then take the commits back")
    # the run's current round, reached through its carrier (a row written before rounds:
    # the one its Hive and session name)
    from core.carriers import registry
    handle = registry.handle_of(row)
    car = registry.carrier(handle.carrier)
    if getattr(car, "run_scoped", False):
        # a round that is a machine of its own (Elastic Workers) hands its bundles over
        # in its record only: its session is never asked (it may have ended)
        raise ReturnError("the run's record here does not hold this bundle yet; refresh the result, "
                          "then take the commits back")
    inst = await car.connect(handle)
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
        async with registry.client_for(inst, 300) as client:
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


#: how many of the branch's newest commits since its merge-base with a missing
#: prerequisite are looked through for the prerequisite's equivalent; one older than
#: that is not found (the bundle is refused as having none)
EQUIVALENT_WINDOW = 2000

#: one commit's own change as ``git patch-id`` reads it; the same command for the
#: prerequisite and for the branch's commits, so their ids compare whatever the diff
#: settings here are (merges have no patch: they match nothing)
_PATCH_LOG = ("log", "-p", "--diff-merges=off", "--no-renames", "--binary", "--no-ext-diff",
              "--no-textconv", "--no-color", "--format=commit %H")


def _patch_ids(repo: Path, *revs: str) -> dict[str, str]:
    """{commit: stable patch id} of the commits ``git log *revs`` lists (newest first);
    a merge, or a commit that changes nothing, has none. The log streams into
    ``git patch-id`` (no patch text is held here)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        with subprocess.Popen(["git", *_SAFE, *_PATCH_LOG, *revs, "--"], cwd=str(repo), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as log:
            try:
                p = subprocess.run(["git", "patch-id", "--stable"], cwd=str(repo), env=env, stdin=log.stdout,
                                   capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
            finally:
                log.stdout.close()
            rc = log.wait(timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReturnError(f"git could not run ({type(exc).__name__})") from None
    if rc != 0 or p.returncode != 0:
        raise ReturnError("git patch-id failed")
    out: dict[str, str] = {}
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and _SHA_RE.fullmatch(parts[1]):
            out[parts[1]] = parts[0]
    return out


def _equivalents(repo: Path, sha: str, branch: str) -> list[str]:
    """The commits of *branch* that make the same change as *sha* (a commit this machine
    has that *branch* does not contain): the same stable patch id, among the newest
    ``EQUIVALENT_WINDOW`` commits since their merge-base (of the whole branch when they
    have none: a rewrite from the root commit on). Several are narrowed to the ones
    with the same author name, email and subject (when none has them, all stay); [] when
    there is none (or *sha* is a merge, or changes nothing)."""
    own = _patch_ids(repo, "--max-count=1", sha).get(sha)
    if own is None:
        return []
    mb = _git(repo, "merge-base", sha, branch)
    since = mb.stdout.decode().strip()
    # no merge-base: the rewrite reached the root commit; the newest commits of the branch
    window = f"{since}..{branch}" if mb.returncode == 0 and _SHA_RE.fullmatch(since) else branch
    hits = [c for c, pid in _patch_ids(repo, "--no-merges", f"--max-count={EQUIVALENT_WINDOW}",
                                       window).items() if pid == own]
    if len(hits) > 1:
        def ident(c: str) -> str:
            return _out(repo, "show", "-s", "--no-show-signature", "--format=%an%x00%ae%x00%s", c)
        mine = ident(sha)
        same = [c for c in hits if ident(c) == mine]
        if same:
            return same
    return hits


def _verify_and_fetch(repo: Path, branch: str, bundle: Path, local_ref: str,
                      also: tuple[str, ...] = ()) -> tuple[str, str, dict[str, str]]:
    """(tip, base, mapped) after every check; the tip is at *local_ref* only (when a
    prerequisite was mapped: at ``<local_ref>-hive`` only, *local_ref* is not moved). Every
    prerequisite must be in *branch* itself (not only in its upstream): the commits
    are reviewed and landed against that branch. *also*: a Swarm Run child's commits
    may build on its lead run's snapshot refs instead (they are material for that run,
    never landed: design 2026-10-31-swarm-run §5.4). A prerequisite in neither, but on
    this machine, with exactly one equivalent on *branch* (``_equivalents``: the branch's
    history was rewritten since the dispatch) is accepted: *mapped* is {prerequisite:
    equivalent}, and the caller replays the commits onto it (``_take_replayed``) before
    anything is reviewed; *base* is then that equivalent (the caller takes the merge-base
    of the replayed tip)."""
    prereq, refs = _bundle_header(bundle)
    if len(refs) != 1:
        raise ReturnError(f"the bundle has {len(refs)} refs; one is expected")
    if not prereq:
        raise ReturnError("the bundle carries a whole history, not commits on top of this repository")
    short = branch.replace("refs/heads/", "")
    mapped: dict[str, str] = {}
    for sha in prereq:
        if _git(repo, "merge-base", "--is-ancestor", sha, branch).returncode == 0 or any(
                _git(repo, "merge-base", "--is-ancestor", sha, ref).returncode == 0 for ref in also):
            continue
        behind = f"(bring {short} up to date with its upstream, then take the commits back again)"
        if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            raise ReturnError(f"the bundle builds on {sha[:12]}, which is not in this machine's {short} "
                              f"and not on this machine at all, so no rewritten copy of it on {short} can "
                              f"be looked for either {behind}")
        eq = _equivalents(repo, sha, branch)
        if not eq:
            raise ReturnError(f"the bundle builds on {sha[:12]}, which is not in this machine's {short} {behind}")
        if len(eq) > 1:
            raise ReturnError(f"the bundle builds on {sha[:12]}, which is not in this machine's {short}; "
                              f"{len(eq)} commits of {short} make the same change ("
                              + ", ".join(c[:12] for c in eq[:5]) + ("…" if len(eq) > 5 else "")
                              + "), so which one it became is not clear: take the change over by hand")
        mapped[sha] = eq[0]
    if _git(repo, "bundle", "verify", "-q", str(bundle)).returncode != 0:
        raise ReturnError("git bundle verify failed")
    tip, ref = refs[0]
    into = _hive_ref(local_ref) if mapped else local_ref
    _out(repo, "-c", "fetch.fsckObjects=true", "-c", "transfer.fsckObjects=true", "fetch", "-q",
         "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head", str(bundle),
         f"+{ref}:{into}")
    if _out(repo, "rev-parse", "--verify", f"{into}^{{commit}}") != tip:
        raise ReturnError("the fetched tip is not the bundle's")
    if mapped:
        # the replay decides the base (a rewrite from the root shares no history with it)
        return tip, next(iter(mapped.values())), mapped
    base = _out(repo, "merge-base", tip, branch)
    if not _SHA_RE.fullmatch(base):
        raise ReturnError("the commits share no history with the main branch")
    return tip, base, mapped


def _hive_ref(local_ref: str) -> str:
    """Where the Hive's own commits are kept when they were replayed (never shipped)."""
    return f"{local_ref}-hive"


#: the most paths where the two bases differ that the review lists (the count is whole)
MAX_DIFFERS_SHOWN = 50

#: the most commits replayed onto a rewritten base (each is a few git calls; more is
#: taken over by hand)
MAX_REPLAY_COMMITS = 200


def _take_replayed(repo: Path, branch: str, local_ref: str, hive_tip: str,
                   mapped: dict[str, str]) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Replay the Hive's commits (at ``<local_ref>-hive``) onto the equivalent of their
    base (``_replay``) and point *local_ref* at the replayed tip. Returns (replayed tip,
    {Hive commit: replayed commit}, ``base_mapped``: ``{from, to, differs,
    differs_total}``, *differs* the paths where the old base's tree and its equivalent's
    differ). *local_ref* is not moved when the replay is refused."""
    tip, done = _replay(repo, branch, _hive_ref(local_ref), hive_tip, mapped)
    (old, new), = mapped.items()
    raw = _git(repo, *_SAFE, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff",
               "--no-textconv", old, new, check=True).stdout.decode("utf-8", "replace")
    differs = [p for p in raw.split("\0") if p]
    _out(repo, "update-ref", "-m", "hive return: replayed onto the rewritten branch", local_ref, tip)
    return tip, done, {"from": old, "to": new, "differs": differs[:MAX_DIFFERS_SHOWN],
                       "differs_total": len(differs)}


def _replay(repo: Path, branch: str, hive_ref: str, tip: str, mapped: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The Hive's commits ``<prerequisite>..tip`` put again onto the prerequisite's
    equivalent (*mapped*, from ``_verify_and_fetch``), one by one, from git objects only:
    each is a ``merge-tree`` of the chain so far and the commit over its own parent (a
    cherry-pick without a worktree, a hook or a filter), committed with the commit's own
    author, committer, dates and message, so the same bundle replays to the same commits
    every time (a later turn's ancestry check holds). Returns (replayed tip, {Hive commit:
    replayed commit}). Refused when the commits include a merge or build on more than one
    rewritten commit (more than one base to place), are more than
    ``MAX_REPLAY_COMMITS``, do not build on the named prerequisite one after another or
    include a commit already on *branch* (the prerequisite is the Hive's word: a named old
    commit must not pull the branch's own commits into the replay), or one of them
    conflicts (the message names the files); *hive_ref* (the Hive's own commits) is named
    for the takeover by hand."""
    short = branch.replace("refs/heads/", "")
    merges = _out(repo, "rev-list", "--count", "--min-parents=2", tip, *(f"^{p}" for p in mapped))
    if merges != "0":
        raise ReturnError(f"the commits build on a commit {short} rewrote and include a merge, which is not "
                          f"replayed here; take the change over by hand from {hive_ref}")
    if len(mapped) != 1:
        raise ReturnError(f"the commits build on {len(mapped)} commits {short} rewrote, which is not replayed "
                          f"here; take the change over by hand from {hive_ref}")
    (old, new), = mapped.items()
    chain = _out(repo, "rev-list", "--reverse", "--topo-order", tip, f"^{old}").split()
    if len(chain) > MAX_REPLAY_COMMITS:
        raise ReturnError(f"the commits build on a commit {short} rewrote and are {len(chain)} commits, more "
                          f"than the {MAX_REPLAY_COMMITS} replayed here; take the change over by hand "
                          f"from {hive_ref}")
    # The prerequisite comes from the bundle's header, which the Hive writes: the commits
    # must really build on it, one after another, and none of them may already be on the
    # branch. Otherwise a named old commit would pull the branch's own commits into the
    # replay (re-applying them onto the equivalent, under their own authors).
    own = _out(repo, "rev-list", "--count", tip, f"^{old}", f"^{branch}")
    prev = old
    for c in chain:
        if _out(repo, "rev-parse", "--verify", f"{c}^1") != prev:
            raise ReturnError(f"the commits do not build on {old[:12]}, the commit the bundle names as their "
                              f"base; take the change over by hand from {hive_ref}")
        prev = c
    if own != str(len(chain)):
        raise ReturnError(f"some of the commits on {old[:12]} are already in {short}; take the change over "
                          f"by hand from {hive_ref}")
    cur, done = new, {}
    for c in chain:
        parent = _out(repo, "rev-parse", "--verify", f"{c}^1")
        mt = _git(repo, *_SAFE, "merge-tree", "--write-tree", "--no-messages", "--name-only", "-z",
                  f"--merge-base={parent}", cur, c)
        fields = mt.stdout.decode("utf-8", "replace").split("\0")
        if mt.returncode == 1:
            files = sorted({f for f in fields[1:] if f})
            more = f" and {len(files) - 10} more" if len(files) > 10 else ""
            raise ReturnError(f"the commits do not replay onto {new[:12]}, the commit of {short} that makes the "
                              f"same change as {old[:12]} they build on ({short}'s history was rewritten): "
                              f"{c[:12]} conflicts in " + ", ".join(files[:10]) + more
                              + f"; take the change over by hand from {hive_ref}")
        if mt.returncode != 0 or not _SHA_RE.fullmatch(fields[0]):
            raise ReturnError("git merge-tree failed (git 2.40 or newer is needed)")
        cur = _recommit(repo, c, fields[0], cur)
        done[c] = cur
    return cur, done


def _recommit(repo: Path, commit: str, tree: str, parent: str) -> str:
    """A commit object with *commit*'s author, committer, encoding and message on *tree*
    over *parent* (its signature is dropped: it signed other content)."""
    raw = _git(repo, "cat-file", "commit", commit, check=True).stdout
    head, _, msg = raw.partition(b"\n\n")
    kept, keep = [], False
    for line in head.split(b"\n"):
        if not line.startswith(b" "):            # a header; " ..." continues the one before
            keep = line.split(b" ", 1)[0] in (b"author", b"committer", b"encoding")
        if keep:
            kept.append(line)
    obj = b"tree %s\nparent %s\n" % (tree.encode(), parent.encode()) + b"\n".join(kept) + b"\n\n" + msg
    try:
        p = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=str(repo), input=obj,
                           capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReturnError(f"git could not run ({type(exc).__name__})") from None
    new = p.stdout.decode().strip()
    if p.returncode != 0 or not _SHA_RE.fullmatch(new):
        raise ReturnError("the replayed commit could not be written")
    return new


#: paths that change how git itself behaves on this machine (filters, drivers, LFS,
#: submodules, hooks kept in the tree): protected in every repository
GIT_PROTECTED = ("**/.gitattributes", ".gitattributes", ".lfsconfig", "**/.lfsconfig", ".gitmodules",
                 ".husky/**", ".githooks/**", ".pre-commit-config.yaml")
#: paths that run code on push (CI pipelines and the local actions they call) or that
#: configure a coding agent run here (its settings, hooks, MCP servers, rules and the
#: instruction files it reads, at any depth; SwarmAI's own ``.swarmai/``, whose review
#: guidance goes into its reviewers' prompts), and editor / container tasks that run on
#: open: protected in every repository, like GIT_PROTECTED
CI_AGENT_PROTECTED = (
    ".github/workflows/**", ".github/actions/**", ".gitlab-ci.yml", ".circleci/**",
    "azure-pipelines.yml", "azure-pipelines.yaml", "Jenkinsfile", "**/Jenkinsfile",
    ".claude/**", "**/.claude/**", ".mcp.json", "**/.mcp.json", ".cursor/**", "**/.cursor/**",
    ".kiro/**", "**/.kiro/**", ".gemini/**", "**/.gemini/**", ".codex/**", ".amazonq/**",
    ".windsurf/**", ".windsurfrules", ".clinerules/**", ".github/copilot-instructions.md",
    ".github/instructions/**", ".cursorrules", ".aider.conf.yml", ".roo/**", ".roomodes",
    ".roorules", ".roorules-*", ".kilocode/**", ".continue/**", ".continuerc.json", ".junie/**",
    ".swarmai/**",
    "AGENTS.md", "**/AGENTS.md", "CLAUDE.md", "**/CLAUDE.md", "GEMINI.md", "**/GEMINI.md",
    ".vscode/tasks.json", ".devcontainer/**", ".devcontainer.json")
#: what an agent never lands from a Hive (the person may): refused to the agent's landing
#: here and to a Swarm Run's child merge (core/run_protocol/child_merge). Who is the person
#: is the route's actor (core/swarm_out_actor): like every person-only step in Swarm Out
#: this stops the agent's own mistakes, not an agent set on getting past it (the agent and
#: the daemon share an OS user)
PERSON_ONLY = GIT_PROTECTED + CI_AGENT_PROTECTED


def person_only(paths: list[str]) -> list[str]:
    """The *paths* an agent may not land (``PERSON_ONLY``)."""
    return [p for p in paths if _is_protected(p, list(PERSON_ONLY))]


def person_only_reason(paths: list[str]) -> str:
    """Why an agent's landing of *paths* (from ``person_only``) is refused."""
    more = f" and {len(paths) - 10} more" if len(paths) > 10 else ""
    return ("these commits change files that make code run on their own, here or on push, or that "
            "configure the coding agents (git hooks and filters, submodules, CI pipelines, agent "
            "settings and instructions): " + ", ".join(paths[:10]) + more)
_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _unsafe_paths(repo: Path, paths: list[str], base: Optional[str] = None) -> list[str]:
    """Paths the commits may not write on this machine: under a directory this checkout
    ignores (a dependency dir such as a virtualenv), under a link, or with control or
    bidi characters (a name that would not read as what it is). A path *base* already
    tracks is not ignored (git never ignores a tracked file, whatever a pattern says: a
    lock file under ``*.lock``)."""
    out = [p for p in paths if _BAD_CHARS.search(p)]
    ok = [p for p in paths if p not in out]
    tracked: set[str] = set()
    if ok and base is not None:
        # on stdin, one ``<base>:<path>`` a line (no control character in a path here), so
        # any number of paths fits; a failure leaves every path to the ignore check
        try:
            r = subprocess.run(["git", "cat-file", "--batch-check=%(objecttype)"], cwd=str(repo),
                               input="".join(f"{base}:{p}\n" for p in ok).encode(), capture_output=True,
                               timeout=GIT_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError):
            r = None
        lines = r.stdout.decode("utf-8", "replace").splitlines() if r is not None and r.returncode == 0 else []
        if len(lines) == len(ok):
            tracked = {p for p, kind in zip(ok, lines) if kind == "blob"}
    unknown = [p for p in ok if p not in tracked]
    if unknown:
        r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=str(repo),
                           input="\0".join(unknown).encode(), capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
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
    globs = [*PERSON_ONLY, *globs]
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
    bad = _unsafe_paths(repo, [f["path"] for f in files], base)
    if bad:
        raise ReturnError("the commits write where they may not on this machine (an ignored or linked "
                          "directory, or a name with control characters): " + ", ".join(repr(b) for b in bad[:5]))
    from core.review_order import ordered
    files = ordered(files)   # the heart of the change first; the cap drops supporting files first
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


def _is_bundle_named(path: Path, b: dict) -> bool:
    """*path* holds exactly the bytes the result names (``size`` and ``sha256``)."""
    if type(b.get("size")) is not int or not isinstance(b.get("sha256"), str):
        return False
    try:
        if path.stat().st_size != b["size"]:
            return False
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() == b["sha256"]


def kept_here(dispatch_id: str, rdir: str) -> bool:
    """Whether the commits the run's result names for the repository declared as *rdir*
    are on this host although taking them back was refused after ``_verify_and_fetch``
    fetched the tip (a replay that conflicts, a merge, history shared with nothing, the
    review refusing): the bundle ``prepare`` kept in the run's state dir is the one the
    result names now (its size and sha256: a file from an earlier turn is not), ``git
    bundle verify`` passes for it in the Brain's checkout, and its tip is the commit at
    ``refs/hive/<dispatch>/<key>`` or at its ``-hive`` ref. A refusal before the fetch
    (two equivalent bases, a prerequisite not here, the wrong branch checked out) leaves
    the tip at neither: False, as when any of the rest is not so or cannot be read
    (a malformed record too). Blocking."""
    from core import hive_bundle
    try:
        key = _key(hive_bundle.key(rdir))
        bundle = _state_dir(dispatch_id) / f"{key}.bundle"
        if not bundle.is_file():
            return False
        row = _row(dispatch_id)
        declared = next((r for r in row.get("repos") or [] if isinstance(r, dict) and r.get("dir") == rdir), None)
        brain = row["project_agent"].get("brain")
        if declared is None or not isinstance(declared.get("url"), str) or not isinstance(brain, str):
            return False
        if not _is_bundle_named(bundle, _bundle_fact(dispatch_id, declared)):
            return False
        repo = _checkout(brain, declared["url"])
        if _git(repo, "bundle", "verify", "-q", str(bundle)).returncode != 0:
            return False
        _prereq, refs = _bundle_header(bundle)
        if len(refs) != 1:
            return False
        tip = refs[0][0]
        local_ref = f"refs/hive/{dispatch_id}/{key}"
        for ref in (local_ref, _hive_ref(local_ref)):
            p = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            if p.returncode == 0 and p.stdout.decode("utf-8", "replace").strip() == tip:
                return True
        return False
    except (ReturnError, OSError, AttributeError, TypeError, KeyError, ValueError):
        return False                 # not knowing is not "the commits are here"


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
        lead = _lead_refs(repo, row)
        tip, base, mapped = _verify_and_fetch(repo, branch, bundle, local_ref, lead)
        hive_tip, replayed, moved = tip, {}, {}
        if mapped:
            # this machine's branch was rewritten since the dispatch: what is reviewed and
            # landed is the Hive's commits replayed onto the equivalent of their base
            # (the ref then names the replayed tip: every reader pairs it with ``tip``)
            tip, replayed, mapping = _take_replayed(repo, branch, local_ref, hive_tip, mapped)
            base = _out(repo, "merge-base", tip, branch)
            moved = {"base_mapped": mapping, "hive_tip": hive_tip, "hive_ref": _hive_ref(local_ref)}
        # a Swarm Run child starts from a snapshot of its lead run's worktree, which may hold
        # the lead's own edits: what the child changed is what came after that snapshot
        start = None if mapped else _child_base(repo, declared.get("sha"), lead, tip)
        if start is not None:
            base = start
        landed = before.get("landed_tip")
        if landed:
            # the Hive tip that landed is its replayed copy now (whatever it was replayed
            # onto when it landed: main may have been rewritten again since)
            landed = replayed.get(before.get("landed_hive_tip") or landed, landed)
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
        kept = ("landed_tip", "landed_hive_tip", "landed", "landed_at")
        state = {**{k: before[k] for k in kept if k in before},
                 "dispatch_id": dispatch_id, "url": url, "key": b["key"], "repo": str(repo),
                 "branch": branch, "ref": local_ref, "tip": tip, "base": base, **moved, **review,
                 # uncommitted edits in the checkout may make the landing refuse
                 "checkout_dirty": dirty,
                 "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "prepared"}
        _save(dispatch_id, b["key"], state)
        return state
    return await asyncio.to_thread(_work)


def import_unrecorded(dispatch_id: str, row: dict, declared: dict) -> Optional[dict[str, Any]]:
    """The newest kept bundle of *declared* that no turn outcome names, fetched into
    ``refs/hive/<dispatch>/<key>`` of the Brain's checkout: ``{repo, branch, ref, tip,
    seq}``; None when the repository has no bundle in the kept record or the newest one is
    named by the newest outcome with repos (``prepare`` takes that one). ReturnError when
    a check refuses it. Blocking."""
    from core import hive_bundle, hive_records
    url, rdir = declared.get("url"), declared.get("dir")
    if not isinstance(url, str) or not isinstance(rdir, str) or not rdir:
        return None
    k = hive_bundle.key(rdir)
    try:
        d = hive_records.local_dir(dispatch_id)
        es = hive_records.local_entries(dispatch_id)
    except ValueError:
        return None
    mine = [e for e in es if e.get("kind") == "bundle" and e.get("key") == k and type(e.get("seq")) is int]
    if not mine:
        return None
    newest = mine[-1]
    named = hive_records._referenced(d, es)
    if named is not None and newest["seq"] in named[1]:
        return None
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    if not isinstance(agent.get("brain"), str):
        raise ReturnError("the run names no Brain to take its commits back into")
    if not isinstance(newest.get("sha256"), str) or type(newest.get("size")) is not int:
        raise ReturnError("the run's record names its newest bundle without its size and sha256")
    repo = _checkout(agent["brain"], url)
    dest = _state_dir(dispatch_id) / f"{k}.unrecorded.bundle"
    dest.parent.mkdir(parents=True, exist_ok=True)
    b = {"seq": newest["seq"], "key": k, "sha256": newest["sha256"], "size": newest["size"]}
    local_ref = f"refs/hive/{dispatch_id}/{k}"
    try:
        if not _from_record(dispatch_id, b, dest):
            raise ReturnError("the run's record here does not hold its newest bundle whole")
        branch = _target_branch(repo, declared.get("ref"))
        tip, _base, mapped = _verify_and_fetch(repo, branch, dest, local_ref, _lead_refs(repo, row))
        if mapped:
            # the next round starts from the ref: never from the history the branch rewrote
            tip, _done, _mapping = _take_replayed(repo, branch, local_ref, tip, mapped)
    finally:
        dest.unlink(missing_ok=True)
    logger.info("hive_return: %s's newest bundle of %s (seq %d) had no turn record; imported at %s",
                dispatch_id, k, newest["seq"], local_ref)
    return {"repo": str(repo), "branch": branch, "ref": local_ref, "tip": tip, "seq": newest["seq"]}


#: a Swarm Run child's start snapshot (``refs/swarm/snapshots/<run>/b<n>-<repo>``,
#: core/run_protocol/child_cli), not the run's chain or the worktree it last took
_CHILD_BASE_REF = re.compile(r"refs/swarm/snapshots/run_[0-9a-f]{8,32}/b[0-9]+-[A-Za-z0-9_-]+")


def _child_base(repo: Path, sha: Any, lead: tuple[str, ...], tip: str) -> Optional[str]:
    """The commit a Swarm Run child started from when it is the tip of one of its lead
    run's child-start snapshots (in *lead*, ``_lead_refs``) and an ancestor of *tip*, else
    None. A ref that no longer resolves is passed over."""
    if not lead or not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        return None
    tips = set()
    for r in lead:
        if _CHILD_BASE_REF.fullmatch(r):
            p = _git(repo, "rev-parse", "--verify", "-q", f"{r}^{{commit}}")
            if p.returncode == 0:
                tips.add(p.stdout.decode("utf-8", "replace").strip())
    if sha not in tips or _git(repo, "merge-base", "--is-ancestor", sha, tip).returncode != 0:
        return None
    return sha


def _lead_refs(repo: Path, row: dict) -> tuple[str, ...]:
    """A Swarm Run child's lead run's snapshot refs in *repo* (core/run_worktree), else ()."""
    run = row.get("parent_run")
    if not isinstance(run, str) or not re.fullmatch(r"run_[0-9a-f]{8,32}", run):
        return ()
    p = _git(repo, "for-each-ref", "--format=%(refname)", f"refs/swarm/snapshots/{run}/")
    out = p.stdout.decode("utf-8", "replace") if isinstance(p.stdout, bytes) else (p.stdout or "")
    return tuple(r for r in out.split("\n") if r)


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
                    protected: list, summary: Optional[str], mapped: Optional[dict] = None) -> str:
    """The landed commit's message, written on this machine: the run's title, the summary
    the landing's reviewer wrote (what changed and why), and the files with their line
    counts as prepare computed them. The Hive's own commit messages are its text and are
    never copied here (an agent reads this log later); they stay at *ref*. *mapped*
    (``base_mapped``: ``{from, to, differs, differs_total}``): the commits were replayed
    onto the equivalent of the base they built on, which this machine's branch rewrote;
    the message says so and names the paths where the two bases differ."""
    msg = f"hive: {title}"
    if summary:
        msg += "\n\n" + summary
    rows = [f"  {f.get('path')} (+{f.get('added', 0)} -{f.get('deleted', 0)})"
            if isinstance(f.get("added"), int) else f"  {f.get('path')} (binary)" for f in files]
    if rows:
        msg += "\n\nFiles:\n" + "\n".join(rows)
    msg += (f"\n\nThe commits of a Hive run ({hive}, dispatch {dispatch_id}), reviewed and "
            f"squashed on this machine. The Hive's own commits are at {ref}.")
    if isinstance(mapped, dict) and mapped.get("from") and mapped.get("to"):
        msg += (f"\n\nThey built on {str(mapped['from'])[:12]}, which this machine's branch rewrote; they "
                f"were replayed onto {str(mapped['to'])[:12]}, the commit that makes the same change.")
        differs = [str(p) for p in mapped.get("differs") or []]
        if differs:
            total = mapped.get("differs_total") if isinstance(mapped.get("differs_total"), int) else len(differs)
            msg += (" The two bases differ in: " + ", ".join(differs[:20])
                    + (f" and {total - 20} more" if total > 20 else "") + ".")
    if protected:
        msg += ("\n\nChanges protected paths: " + ", ".join(protected[:20])
                + (" …" if len(protected) > 20 else ""))
    return msg


def dispatch_trailer(dispatch_id: str) -> str:
    """The trailer line naming the Hive dispatch whose commits a landed commit carries,
    so a reader of the history tells an agent run's commit from a person's."""
    return f"Hive-Dispatch: {dispatch_id}"


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


def land(dispatch_id: str, key: str, tip: str, allow_git_exec: bool = True,
         summary: Optional[str] = None) -> dict[str, Any]:
    """Land the prepared tip the person or the dispatching agent reviewed: landing is the
    judgment, so protected paths need no approval of their own (the landed commit's
    message names them). The agent's landing (the route passes ``allow_git_exec`` False
    for it: design 2026-11-02-hive-run-decision-record §2) refuses the person-only paths
    (``PERSON_ONLY``): git hooks and filters, which make git run code on this machine on
    its own, and CI pipelines and coding-agent configuration, which run code on push or
    steer an agent on this machine; the person may land them (``allow_git_exec`` decides
    the whole ``PERSON_ONLY`` set; the name is from when it held git hooks only). A
    repository's own protected globs the agent lands once reviewed. The commit is built from objects only
    (``merge-tree --write-tree`` of the branch and the tip over the reviewed base, then
    ``commit-tree``): no working tree holds the Hive's files and no hook runs until the
    main checkout fast-forwards to it, which runs no hook either."""
    from core import swarm_out
    summary = _summary(summary)
    state = _prepared(dispatch_id, key, tip)
    repo = Path(state["repo"])
    if _git(repo, "rev-parse", "--verify", "--quiet", f"{state['ref']}^{{commit}}").stdout.decode().strip() != tip:
        raise ReturnError("the ref of these commits moved since they were shown; review them again")
    # from every file, not only the marked ones: a review prepared before a path joined
    # PERSON_ONLY did not mark it
    held = person_only(sorted({*state["protected_files"], *(f["path"] for f in state["files"])}))
    if held and not allow_git_exec:
        raise ReturnError(person_only_reason(held)
                          + "; an agent does not land them: re-run asking the Hive to leave them out")
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
    msg = landing_message(title, row.get("hive"), dispatch_id, state.get("hive_ref") or state["ref"],
                          state["files"], sorted({*state["protected_files"], *held}), summary,
                          state.get("base_mapped"))
    try:   # the repository's own author and trailer ([landing] in .swarmai/pipeline.toml)
        settings = landing_settings(str(repo), branch)
    except LandingError as e:
        raise ReturnError(str(e)) from None
    c = subprocess.run(["git", *_SAFE, "commit-tree", tree, "-p", head, "-F", "-"], cwd=str(repo),
                       input=with_trailer(msg, settings["trailer"], (dispatch_trailer(dispatch_id),)).encode(),
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
                  "landed": new, "landed_tip": tip, "landed_hive_tip": state.get("hive_tip") or tip})
    _save(dispatch_id, key, state)
    from core import swarm_out_settle
    swarm_out_settle.mark_code_landed(dispatch_id)     # accepted: no turn adds commits now
    return {"landed": True, "head": new[:12], "into": branch.replace("refs/heads/", ""),
            "protected_files": state["protected_files"][:20]}



#: a commit id the agent names: hex only, so git never reads it as an option or a ref name
_COMMIT_ID_RE = re.compile(r"[0-9a-f]{7,64}")
MAX_HAND_COMMITS = 50
_SHOWN = 10
#: a dropped path scoped to one repository: ``<url>::<path>``
_SCOPE = "::"


def _changed(repo: Path, a: str, b: str) -> set[str]:
    """The paths that differ between two commits (renames as delete + add), exactly as git
    names them (split on NUL, nothing stripped)."""
    raw = _git(repo, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", a, b,
               check=True).stdout.decode("utf-8", "replace")
    return {p for p in raw.split("\0") if p}


def _blob(repo: Path, rev: str, path: str) -> str:
    p = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}:{path}")
    return p.stdout.decode().strip() if p.returncode == 0 else ""


def _content(repo: Path, obj: str) -> Optional[Counter]:
    """A blob's lines, counted (stripped, blank ones left out); None for an object that
    is not a blob (a submodule's commit)."""
    if not obj:
        return Counter()
    if _out(repo, "cat-file", "-t", obj) != "blob":
        return None
    raw = _git(repo, *_SAFE, "cat-file", "blob", obj, check=True).stdout.decode("utf-8", "replace")
    return Counter(x.strip() for x in raw.split("\n") if x.strip())


class _Run:
    """The run's own change to one returned file (``base..tip``), read once."""

    def __init__(self, repo: Path, base: str, tip: str, path: str):
        self.tip_obj = _blob(repo, tip, path)
        base_obj = _blob(repo, base, path)
        self.base = _content(repo, base_obj)
        tip_lines = _content(repo, self.tip_obj)
        if self.base is None or tip_lines is None:
            self.added = self.removed = Counter()     # a submodule: compared by its commit
        else:
            self.added = tip_lines - self.base        # what the run wrote there, net
            self.removed = self.base - tip_lines      # what it took out, net


def _holds(repo: Path, run: _Run, commit: str, path: str) -> bool:
    """The file as *commit* leaves it holds the run's whole change to it: every line the
    run wrote there, as many times more than the base had it as the run wrote it (a
    conflict resolution keeps them wherever main's edits put them; another edit of the
    same file lacks them, and a line the base already had does not count for the run);
    for a run that only took lines out, they are out; a file the run deleted is gone; a
    binary file, a mode change or a submodule is the tip's exact object. Main having
    removed a base copy of a line the run also wrote reads as not holding (fail closed:
    the agent names that file ``--dropped``)."""
    obj = _blob(repo, commit, path)
    if not run.tip_obj:
        return not obj
    if not run.added and not run.removed:
        return obj == run.tip_obj
    have = _content(repo, obj) if obj else None
    if not have:
        return False
    base = run.base or Counter()
    return (all(have[x] >= base[x] + n for x, n in run.added.items())
            and all(have[x] <= base[x] - n for x, n in run.removed.items()))


def _drops(states: list[dict], returned: list[set[str]], dropped: list[str]) -> list[set[str]]:
    """Each repository's dropped paths: ``<path>`` for the one repository that returned
    it, ``<url>::<path>`` for one of several that did. Raises ReturnError for a path no
    repository returned, or one several returned without naming which."""
    per: list[set[str]] = [set() for _ in states]
    unknown, ambiguous = [], []
    for raw in dropped or []:
        d = str(raw or "")
        if not d:
            continue
        # scoped by a repository's url as its prefix (a path or a url may hold ``::``)
        scoped = [(i, d[len(s["url"]) + len(_SCOPE):]) for i, s in enumerate(states)
                  if d.startswith(s["url"] + _SCOPE) and d[len(s["url"]) + len(_SCOPE):] in returned[i]]
        hits = [i for i, _p in scoped] or [i for i, files in enumerate(returned) if d in files]
        path = scoped[0][1] if scoped else d
        if not hits:
            unknown.append(d)
        elif len(hits) > 1:
            ambiguous.append(path)
        else:
            per[hits[0]].add(path)
    if unknown:
        raise ReturnError("these paths are not files the run returned: " + ", ".join(unknown[:_SHOWN]))
    if ambiguous:
        raise ReturnError("more than one repository of this run returned " + ", ".join(ambiguous[:_SHOWN])
                          + f": name the one you dropped it from as <url>{_SCOPE}<path>")
    return per


def _ancestor(repo: Path, a: str, b: str) -> bool:
    p = _git(repo, "merge-base", "--is-ancestor", a, b)
    if p.returncode not in (0, 1):
        raise ReturnError("git merge-base failed")
    return p.returncode == 0


def verify_hand_landing(states: list[dict], commits: list[str], dropped: list[str]) -> list[dict[str, Any]]:
    """The commits the dispatching agent says landed a run's change by hand, after
    ``land`` answered a conflict (or a change larger than the review shows). *states* are
    the return-prepare states of the repositories still waiting to land (``prepare``:
    repo, branch, base, tip). Checked from git here, no model:

    - each named commit is in exactly one of those repositories, reachable from its
      prepared branch and not from its prepared base (made after the run started);
    - each makes at least one file the run returned there hold the run's whole change
      to it, which a parent of it did not (``base..tip``, read whole: the review's file list
      is capped; see ``_holds``), so a commit on the branch that only edits the same
      file (the one the conflict was with, say), or a later one that only keeps the
      landed lines, is not the run's landing;
    - for every returned file but those the agent names in *dropped* (left out on
      purpose, kept in the record: ``_drops``), one named commit leaves the file holding
      the run's whole change, so a commit unrelated to the run, or one that lands only
      part of it unsaid, cannot settle it; every repository needs a commit.

    Raises ReturnError naming what does not hold; returns per repository its url, key,
    the full commit ids and the dropped paths."""
    wanted: list[str] = []
    for c in commits or []:
        c = str(c or "").strip().lower()
        if not _COMMIT_ID_RE.fullmatch(c):
            raise ReturnError(f"not a commit id: {c[:20]!r} (7 to 64 hex characters)")
        if c not in wanted:
            wanted.append(c)
    if not wanted:
        raise ReturnError("name the commit(s) that landed the run's change on the branch (--commit)")
    if len(wanted) > MAX_HAND_COMMITS:
        raise ReturnError(f"name at most {MAX_HAND_COMMITS} commits")
    if not states:
        raise ReturnError("no repository of this run waits to land")
    returned = [_changed(Path(s["repo"]), s["base"], s["tip"]) for s in states]
    drops = _drops(states, returned, dropped)
    per: list[list[str]] = [[] for _ in states]
    for c in wanted:
        hits, seen = [], set()
        for i, s in enumerate(states):
            repo, branch, base = Path(s["repo"]), s["branch"], s["base"]
            p = _git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")
            full = p.stdout.decode().strip() if p.returncode == 0 else ""
            if not _SHA_RE.fullmatch(full):
                continue
            if not _ancestor(repo, full, branch):
                seen.add(f"{c[:12]} is not on {branch.replace('refs/heads/', '')} in {s['url']}: land it "
                         "there first (`scripts.session_worktree land`)")
                continue
            if _ancestor(repo, full, base):
                seen.add(f"{c[:12]} is older than the run's base {base[:12]} in {s['url']}: it was on "
                         f"{branch.replace('refs/heads/', '')} before the run started")
                continue
            hits.append((i, full))
        if not hits:
            raise ReturnError("; ".join(sorted(seen)) if seen else
                              f"{c[:12]} is not a commit in any repository this run brought commits to")
        if len(hits) > 1:
            raise ReturnError(f"{c[:12]} names a commit in more than one repository of this run: give more of its id")
        i, full = hits[0]
        if full not in per[i]:
            per[i].append(full)
    left = [s["url"] for s, shas in zip(states, per) if not shas]
    if left:
        raise ReturnError("no commit you named landed " + ", ".join(left)
                          + ": name the commit(s) on its branch that landed its change")
    out = []
    for s, shas, files, drop in zip(states, per, returned, drops):
        if not files:
            raise ReturnError(f"the run's commits in {s['url']} change no file: there is nothing to land by hand")
        repo, carried, run = Path(s["repo"]), set(), {}
        for full in shas:
            parents = _out(repo, "rev-parse", f"{full}^@").split()
            # what the commit changes against any parent (a merge brings the run's files in
            # from one side)
            mine = set().union(*(_changed(repo, par, full) for par in parents)) & files
            if not mine:
                raise ReturnError(f"{full[:12]} changes none of the files the run returned in {s['url']}: "
                                  "it did not land this run")
            here, before = set(), set()
            for path in sorted(mine):
                if path not in run:
                    run[path] = _Run(repo, s["base"], s["tip"], path)
                if not _holds(repo, run[path], full, path):
                    continue
                # the commit that makes the file hold it (a parent did not: for a merge of
                # main into the run's side, main): a later commit that only keeps the
                # landed lines is not part of the landing
                if any(not _holds(repo, run[path], par, path) for par in parents):
                    here.add(path)
                else:
                    before.add(path)
            if not here and before:
                raise ReturnError(f"{', '.join(sorted(before)[:_SHOWN])} in {s['url']} already held the run's whole "
                                  f"change before {full[:12]}: name the commit that made it hold the change, or "
                                  "--dropped it when main already had those lines")
            if not here:
                raise ReturnError(f"{full[:12]} changes " + ", ".join(sorted(mine)[:_SHOWN]) + f" in {s['url']} "
                                  "but leaves none of them holding the run's whole change: it is not the run's "
                                  "landing (when one file landed over several commits, name the one that "
                                  "completed it)")
            carried |= here
        missing = sorted(files - carried - drop)
        if missing:
            more = f" and {len(missing) - _SHOWN} more" if len(missing) > _SHOWN else ""
            raise ReturnError(f"no commit you named leaves the run's whole change in files it returned in "
                              f"{s['url']}: " + ", ".join(missing[:_SHOWN]) + more + "; name the commits that "
                              "land them, or --dropped <path> for each you left out on purpose")
        out.append({"url": s["url"], "key": s["key"], "commits": shas, "dropped": sorted(drop)})
    return out


def _patch_id(repo: Path, a: str, b: str) -> Optional[str]:
    """The stable patch id of the change from ``a`` to ``b`` (None: no change)."""
    diff = _git(repo, *_SAFE, "diff", "--no-ext-diff", "--no-textconv", "--binary", a, b, check=True).stdout
    if not diff.strip():
        return None
    try:
        p = subprocess.run(["git", "patch-id", "--stable"], cwd=str(repo), input=diff,
                           capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReturnError(f"git could not run ({type(exc).__name__})") from None
    out = p.stdout.decode("utf-8", "replace").split()
    return out[0] if out else None


def verify_revert(dispatch_id: str, commits: list[str]) -> list[dict[str, str]]:
    """The commits the dispatching agent says undo this run's landing (design
    2026-11-02-hive-run-decision-record §1): every landed repository has exactly one of
    them, it is on the branch the landing went to, and its change is the exact reverse
    of the landed commit's (the same stable patch id as landed→landed^). Raises
    ReturnError naming what does not hold; returns the pairs it matched."""
    d = _state_dir(dispatch_id)
    landed = []
    for f in sorted(d.glob("*.json")):
        st = load(dispatch_id, f.stem) or {}
        if st.get("status") == "landed" and st.get("landed"):
            landed.append(st)
    if not landed:
        raise ReturnError("nothing of this run is landed here")
    wanted = [c.strip() for c in commits or [] if isinstance(c, str) and c.strip()]
    if not wanted:
        raise ReturnError("name the commit that undoes the landing (--commit)")
    matched, used = [], set()
    for st in landed:
        repo, branch, commit = Path(st["repo"]), st.get("branch") or "HEAD", st["landed"]
        target = _patch_id(repo, commit, f"{commit}^")
        hit = None
        for c in wanted:
            if c in used:
                continue
            p = _git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")
            full = p.stdout.decode().strip() if p.returncode == 0 else ""
            if not full:
                continue
            if _git(repo, "merge-base", "--is-ancestor", full, branch).returncode != 0:
                continue
            if _patch_id(repo, f"{full}^", full) == target:
                hit = (c, full)
                break
        if hit is None:
            raise ReturnError(f"no commit you named undoes the landing {commit[:12]} on "
                              f"{branch.rsplit('/', 1)[-1]} (it must be on that branch and reverse it exactly)")
        used.add(hit[0])
        matched.append({"landed": commit, "revert": hit[1]})
    extra = [c for c in wanted if c not in used]
    if extra:
        raise ReturnError("these commits undo nothing this run landed: " + ", ".join(x[:12] for x in extra))
    return matched
