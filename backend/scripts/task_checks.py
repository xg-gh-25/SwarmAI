"""task_checks — run a dispatched task's setup and checks by id, and record each exit.

On a Hive, the Host sends a task's setup lines and checks (its Project Agent's, from
the repository's own CI) as part of the work order (core/hive_workspace ``prepare``,
``order["checks"]``). The agent runs them through this command instead of typing them,
so each run is recorded by the program that ran it, with its exit code; the Host reads
that record (``GET /api/hive/work/<id>/checks``) and shows the statuses from it
(core/swarm_out_results ``_checks``). How the agent writes its shell commands, and what
its reply says, no longer decide what the card shows.

    "$SWARM_BACKEND_BIN" py -m scripts.task_checks setup <work_id>
    "$SWARM_BACKEND_BIN" py -m scripts.task_checks run <work_id> [<id> ...]
    "$SWARM_BACKEND_BIN" py -m scripts.task_checks list <work_id>

``setup`` runs every setup line in order, each with ``bash -e -c`` in the directory the
order names for it, and stops at the first that fails (its exit is this command's).
Like a GitHub Actions runner it gives each line two files, ``$GITHUB_PATH`` and
``$GITHUB_ENV``: a directory a line appends to the first is put on ``PATH`` for every
later line and check, a ``NAME=value`` (or ``NAME<<DELIM`` ... ``DELIM``) line appended to
the second sets that variable. So setup copied from a CI workflow runs unchanged, on
every repository that uses that workflow format; any other line is plain bash.

The Hive package's own ``hive/bin`` (``PACKAGE_BIN``) is on ``PATH`` for setup and the
checks. It holds ``task-tools``, which the Host puts first in setup when the mode lists
its tools (core/task_spec): it installs them with the package's mise and adds them
through the same two files (design 2026-11-02-hive-task-toolchain).

``run`` runs the named checks (all of them without an id), one after the other, each
in its own directory with what setup collected. An id the order does not have is
refused and nothing runs. Each check writes two lines to the record: one when it starts
and one with its exit code when it ends, so a check cut off (the agent's own time limit,
SIGTERM) reads as started and not ended. The exit is 0 when every check exited 0.

A record is the Hive's word, not proof: the Hive's agent runs as the same OS user and
could write the record itself (core/hive_workspace module doc: no file on a Hive is out
of its reach). It removes the accidental misreport (a check run in a loop the Host
cannot read, a reply that says a failed check passed), not a deliberate forgery.

Only the commands of the order run, as the Host declared them: nothing here builds a
command from anything else. The record and the logs live in
``Hive/work/<work_id>/checks/``, which the release removes with the task. There is no
time limit here: the agent caps each call. The check runs in a process group of its
own (never this process's: that group is the caller's, which on a Hive can hold the agent
and the daemon). The check is ended, with every process it started, whenever this
process stops it or stops itself: on SIGTERM, SIGALRM, SIGHUP or SIGQUIT (to this
process, or to the caller's group it belongs to), on any exception while it waits
(SIGINT raises one), and when the check ends (so a process the check left in the
background does not outlive it).

"Every process it started" is found by parent, not by group: a check's command may move
a process into a group of its own (GNU ``timeout`` does, to signal what it runs), and
ending only the check's group leaves that one running. So the end kills the check's
group and every descendant of this process, parents before their children, round after
round until none is left (a child forked before its parent was killed is found in the
next round). The signals this process handles are held while it does (they act once it
is done), so a second stop does not interrupt the first halfway; the hold is per thread,
so this holds when task_checks runs alone in its process (as a command), and a caller
running ``main`` with other threads may still see a nested end (which still kills all). Run as a command on Linux,
this process is a child subreaper (``PR_SET_CHILD_SUBREAPER``): a descendant whose parent
ends is adopted here instead of by init, so it is still found, including what a finished
check left in the background. Elsewhere (macOS, a developer machine, not a Hive) an
orphan in another group escapes once its parent has ended. A pid read from the process
table can in principle be reused by an unrelated process before it is signalled (a
grandchild reaped by its own parent); with Linux's pid space this is not guarded
against. SIGKILL cannot be handled: a SIGKILL of this process, or of the caller's group,
leaves the check running unrecorded until it ends.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

ENV_FILE = "env.json"
SETUP_FILE = "setup.jsonl"
#: the Hive package's own commands (hive/bin: task-tools and the mise it runs)
PACKAGE_BIN = Path(__file__).resolve().parents[2] / "hive" / "bin"
#: lines of a check's log printed when it fails (the whole log stays in the file)
TAIL_LINES = 40

_child: Optional[subprocess.Popen] = None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _workspace() -> Path:
    from core.project_registry import get_swarmws
    return Path(get_swarmws())


def _order(ws: Path, work_id: str) -> dict[str, Any]:
    from core import hive_workspace
    try:
        order = hive_workspace.load(ws, work_id)
    except hive_workspace.WorkError:
        order = None
    if order is None:
        raise SystemExit(f"task_checks: no work order {work_id!r} on this Hive")
    checks = order.get("checks")
    if not isinstance(checks, dict):
        raise SystemExit(f"task_checks: work order {work_id} was sent without checks; run its "
                         "commands as the task text lists them")
    return order


def _dir(ws: Path, order: dict[str, Any], repo: str, cwd: str) -> Path:
    """Where a line runs: the order's clone of *repo*, then *cwd* inside it. Both come
    from the order the daemon wrote (core/hive_workspace ``_parse_checks``); this
    checks again that the path stays plain and inside the clone."""
    from core import hive_workspace
    match = [r for r in order.get("repos") or [] if isinstance(r, dict) and r.get("name") == repo]
    if not match or not isinstance(match[0].get("dir"), str):
        raise SystemExit(f"task_checks: repository {repo!r} is not in work order {order['work_id']}")
    rel = match[0]["dir"] + (f"/{cwd}" if cwd else "")
    if not hive_workspace._plain_rel(cwd) or not hive_workspace._plain(ws, rel):
        raise SystemExit(f"task_checks: {rel} is not a plain directory of the work order")
    d = ws / rel
    if not d.is_dir():
        raise SystemExit(f"task_checks: {rel} does not exist")
    return d


def _checks_dir(ws: Path, work_id: str) -> Path:
    from core import hive_workspace
    d = hive_workspace.checks_dir(ws, work_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_env(cdir: Path) -> dict[str, Any]:
    try:
        got = json.loads((cdir / ENV_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"path": [], "env": {}}
    path = [p for p in got.get("path") or [] if isinstance(p, str)]
    env = {k: v for k, v in (got.get("env") or {}).items() if isinstance(k, str) and isinstance(v, str)}
    return {"path": path, "env": env}


def _env_for(state: dict[str, Any], extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = {**os.environ, **state["env"], **(extra or {})}
    if state["path"]:
        env["PATH"] = os.pathsep.join([*state["path"], env.get("PATH", "")])
    return env


def _read_github_path(f: Path, cwd: Path) -> list[str]:
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if line:
            p = Path(line)
            out.append(str(p if p.is_absolute() else (cwd / p)))
    return out


def _read_github_env(f: Path) -> dict[str, str]:
    """``NAME=value`` lines and ``NAME<<DELIM`` blocks, as a GitHub Actions runner reads
    ``$GITHUB_ENV``."""
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delim = line.split("<<", 1)
            body = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            if name.strip():
                out[name.strip()] = "\n".join(body)
        elif "=" in line:
            name, value = line.split("=", 1)
            if name.strip():
                out[name.strip()] = value
        i += 1
    return out


def _append(f: Path, row: dict[str, Any]) -> None:
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


#: the signals this process handles (``main``), held while a check is ended
HANDLED = (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP, signal.SIGQUIT, signal.SIGINT)
#: rounds of killing before the end gives up on a process that will not die (one in
#: uninterruptible sleep): a hang guard, each round is a few milliseconds
END_ROUNDS = 50
PR_SET_CHILD_SUBREAPER = 36
#: a hang guard on the process listing where it needs ps (it answers in milliseconds)
PS_WAIT_S = 2


def _adopt_orphans() -> None:
    """On Linux, make this process a child subreaper, so a check's process whose parent
    ends is reparented here and still found by ``_descendants``. Elsewhere a no-op."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER)")
    except (OSError, AttributeError) as exc:
        print(f"task_checks: cannot adopt a check's orphans ({exc}); a process a check "
              "leaves in another group may outlive it", file=sys.stderr)


def _table() -> list[tuple[int, int, str]]:
    """(pid, parent pid, state) of every process: from /proc where there is one, else ps."""
    rows = []
    proc = Path("/proc")
    if (proc / "self" / "stat").exists():
        for d in proc.iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue                              # it ended while being read
            # "pid (comm) state ppid ...": comm may hold spaces and parentheses
            rest = stat[stat.rfind(")") + 2:].split()
            if len(rest) >= 2 and rest[1].isdigit():
                rows.append((int(d.name), int(rest[1]), rest[0]))
        return rows
    # ps is a child of this process too: its own row is left out, or every walk finds it
    ps = subprocess.Popen(["ps", "-A", "-o", "pid=,ppid=,stat="], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
    try:
        out, _ = ps.communicate(timeout=PS_WAIT_S)
    except subprocess.TimeoutExpired:
        ps.kill()
        ps.wait()
        raise OSError("ps did not answer")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[0]) != ps.pid:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


#: this process's descendants from before its first check (``main``): never a check's,
#: so the end leaves them (and what they start) alone, as when a caller runs ``main``
#: in-process
_spare: set[int] = set()


class _held:
    """Hold the signals this process handles until the block ends; one that came
    meanwhile acts then. Off the main thread (no handlers there) a no-op."""

    def __enter__(self):
        self.mask = None
        if threading.current_thread() is threading.main_thread():
            old = signal.pthread_sigmask(signal.SIG_BLOCK, ())
            try:
                signal.pthread_sigmask(signal.SIG_BLOCK, HANDLED)
            except BaseException:
                # a handler that ran inside the call (CPython runs pending ones there,
                # after the new mask is set) raised: put the old mask back, or the
                # signals stay held for good
                signal.pthread_sigmask(signal.SIG_SETMASK, old)
                raise
            self.mask = old

    def __exit__(self, *_exc):
        if self.mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, self.mask)


def _tree() -> list[tuple[int, str]]:
    """(pid, state) of every descendant of this process that the check started:
    ``_spare`` and what is below them are left out (not the check's)."""
    me = os.getpid()
    children: dict[int, list[int]] = {}
    state: dict[int, str] = {}
    for pid, ppid, st in _table():
        children.setdefault(ppid, []).append(pid)
        state[pid] = st
    found: list[int] = []
    todo = list(children.get(me, []))
    while todo:
        pid = todo.pop()
        if pid == me or pid in found or pid in _spare:
            continue
        found.append(pid)
        todo.extend(children.get(pid, []))
    return [(p, state.get(p, "")) for p in found]


def _descendants() -> list[int]:
    """The live processes of ``_tree`` (a zombie holds nothing)."""
    return [p for p, st in _tree() if not st.startswith("Z")]


def _reap(pids) -> None:
    """Collect the ended ones of *pids* that are this process's children (killed ones,
    and orphans adopted here). Only those: a caller running ``main`` in-process keeps
    the exit status of its own children."""
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass                                          # not a child here, or reaped


def _end_group(child: Optional[subprocess.Popen]) -> None:
    """End the check with everything it started: its own process group, and every
    descendant of this process, whatever group it moved to (module doc). Never this
    process or its group, which is the caller's. A process already gone is fine."""
    with _held():
        group = child.pid if child is not None else None
        for _ in range(END_ROUNDS):
            try:
                tree = _tree()
            except OSError as exc:                     # no process table (no ps)
                print(f"task_checks: cannot list processes ({exc}); ending only the "
                      "check's group", file=sys.stderr)
                tree = []
            if group is not None:
                # in the round the tree was read in, while the group surely has members
                # (its pgid cannot have been handed out again yet)
                try:
                    os.killpg(group, signal.SIGKILL)
                except OSError:
                    pass
                group = None
            live = [p for p, st in tree if not st.startswith("Z")]
            for pid in live:                           # parents first: none forks after
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            _reap([p for p, _ in tree])
            if not live:
                break
            time.sleep(0.01)


def _stop_child(signum, _frame) -> None:
    """A cap that ends this process (SIGALRM from ``perl -e 'alarm N'``, SIGTERM, an
    interrupt) ends the check with everything it started, so nothing keeps running
    unrecorded."""
    _end_group(_child)
    raise SystemExit(128 + signum)


def _run_one(command: str, cwd: Path, env: dict[str, str], log: Path) -> int:
    global _child
    with open(log, "wb") as out:
        # a group of its own: ending it ends the check's whole tree and nothing else
        _child = subprocess.Popen(["bash", "-e", "-c", command], cwd=str(cwd), env=env,
                                  stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        child = _child
        try:
            code = child.wait()
        except BaseException:
            _end_group(child)               # an interrupt or error while waiting: no orphan
            raise
        finally:
            _child = None
        _end_group(child)                   # what the check left in the background ends with it
        return code


def _tail(log: Path) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:])
    except OSError:
        return ""


def setup(work_id: str) -> int:
    ws = _workspace()
    order = _order(ws, work_id)
    cdir = _checks_dir(ws, work_id)
    state = {"path": [str(PACKAGE_BIN)] if PACKAGE_BIN.is_dir() else [], "env": {}}
    lines = order["checks"].get("setup") or []
    for n, item in enumerate(lines, 1):
        cwd = _dir(ws, order, item["repo"], item.get("cwd") or "")
        gh_path, gh_env = cdir / f"setup-{n}.path", cdir / f"setup-{n}.env"
        gh_path.write_text("")
        gh_env.write_text("")
        log = cdir / f"setup-{n}.log"
        started = _now()
        code = _run_one(item["command"], cwd,
                        _env_for(state, {"GITHUB_PATH": str(gh_path), "GITHUB_ENV": str(gh_env)}), log)
        _append(cdir / SETUP_FILE, {"line": n, "exit": code, "started_at": started, "ended_at": _now(),
                                    "log": str(log.relative_to(ws))})
        if code != 0:
            print(f"setup line {n} exited {code} (log {log.relative_to(ws)}):\n{_tail(log)}")
            return code
        state["path"] = [*reversed(_read_github_path(gh_path, cwd)), *state["path"]]
        state["env"].update(_read_github_env(gh_env))
        (cdir / ENV_FILE).write_text(json.dumps(state), encoding="utf-8")
        print(f"setup line {n}: ok")
    if not lines:
        print("no setup lines")
    (cdir / ENV_FILE).write_text(json.dumps(state), encoding="utf-8")
    return 0


def run(work_id: str, ids: list[str]) -> int:
    from core import hive_workspace
    ws = _workspace()
    order = _order(ws, work_id)
    checks = order["checks"].get("validations") or []
    by_id = {c["id"]: c for c in checks}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        print(f"task_checks: not a check of work order {work_id}: {', '.join(unknown)}; "
              f"its checks are: {', '.join(by_id) or 'none'}", file=sys.stderr)
        return 2
    cdir = _checks_dir(ws, work_id)
    state = _load_env(cdir)
    worst = 0
    for cid in ids or list(by_id):
        item = by_id[cid]
        cwd = _dir(ws, order, item["repo"], item.get("cwd") or "")
        log = cdir / f"{cid}.log"
        started = _now()
        record = cdir / hive_workspace.CHECKS_FILE
        _append(record, {"id": cid, "started_at": started})
        code = _run_one(item["command"], cwd, _env_for(state), log)
        _append(record, {"id": cid, "exit": code, "started_at": started, "ended_at": _now(),
                         "log": str(log.relative_to(ws))})
        print(f"{cid}: exit {code} (log {log.relative_to(ws)})")
        if code != 0:
            print(_tail(log))
            worst = 1
    return worst


def list_checks(work_id: str) -> int:
    order = _order(_workspace(), work_id)
    for c in order["checks"].get("validations") or []:
        print(f"{c['id']}: {c['command']}" + (f" (in {c['repo']}/{c['cwd']})" if c.get("cwd") else f" (in {c['repo']})"))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="task_checks", allow_abbrev=False,
                                description="Run a dispatched task's setup and checks by id.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="run the order's setup lines")
    s.add_argument("work_id")
    r = sub.add_parser("run", help="run the order's checks (all, or the ids given)")
    r.add_argument("work_id")
    r.add_argument("ids", nargs="*")
    li = sub.add_parser("list", help="list the order's checks")
    li.add_argument("work_id")
    args = p.parse_args(argv)
    with _held():
        try:
            spare = {pid for pid, _ in _tree()}
        except OSError:
            spare = set()
        _spare.clear()
        _spare.update(spare)
    # SIGINT needs no handler: it raises KeyboardInterrupt in the wait, which ends the group
    for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, _stop_child)
    if args.cmd == "setup":
        return setup(args.work_id)
    if args.cmd == "run":
        return run(args.work_id, args.ids)
    return list_checks(args.work_id)


if __name__ == "__main__":
    _adopt_orphans()          # the command only: a caller running main() keeps its own role
    sys.exit(main())
