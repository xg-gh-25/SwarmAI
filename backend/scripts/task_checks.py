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

A check that exits non-zero is run once more on the commit the run started from (the
order's ``base`` of each repository when it names one: a correction round starts at an
earlier round's tip, and a check that round broke is the run's own failure; else its
``sha``): ``_Base`` clones every repository of the order at that commit (core/hive_workspace ``base_clone``), runs the order's setup lines there,
then the same command in the same directory, and appends a third line, the check's end
line with ``base_exit`` (that run's exit) or ``base_error`` (why the base could not be
made or the check not run there). The Host reads a check that failed on both with the
same exit code as ``pre_existing`` (core/swarm_out_results ``_checks``). The base clones
are made at the first failed check of a call, in a directory of that call's own, and
removed when the call ends. A check that passes makes
no base run. The end line without the base result is written first, so a call stopped
during the base run leaves the check reading as failed, as before there was a base run.

A record is the Hive's word, not proof: the Hive's agent runs as the same OS user and
could write the record itself (core/hive_workspace module doc: no file on a Hive is out
of its reach). It removes the accidental misreport (a check run in a loop the Host
cannot read, a reply that says a failed check passed), not a deliberate forgery.

Only the commands of the order run, as the Host declared them (on the base too: its
setup lines and the check's command): nothing here builds a command from anything else. The record and the logs live in
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
import shutil
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


class _BadDir(SystemExit):
    """A directory a line cannot run in (``_dir``): exits the command with its message,
    and is told apart from the SystemExit a stopping signal raises (``_stop_child``)."""


def _dir(ws: Path, order: dict[str, Any], repo: str, cwd: str, base: Optional[str] = None) -> Path:
    """Where a line runs: the order's clone of *repo*, then *cwd* inside it. Both come
    from the order the daemon wrote (core/hive_workspace ``_parse_checks``); this
    checks again that the path stays plain and inside the clone. *base*: the directory
    (relative to the workspace) holding the base clones (``_Base``), whose clone of
    *repo* is used instead."""
    from core import hive_workspace
    match = [r for r in order.get("repos") or [] if isinstance(r, dict) and r.get("name") == repo]
    if not match or not isinstance(match[0].get("dir"), str):
        raise _BadDir(f"task_checks: repository {repo!r} is not in work order {order['work_id']}")
    rel = (f"{base}/{repo}" if base else match[0]["dir"]) + (f"/{cwd}" if cwd else "")
    if not hive_workspace._plain_rel(cwd) or not hive_workspace._plain(ws, rel):
        raise _BadDir(f"task_checks: {rel} is not a plain directory of the work order")
    d = ws / rel
    if not d.is_dir():
        raise _BadDir(f"task_checks: {rel} does not exist")
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


def _setup_lines(ws: Path, order: dict[str, Any], files: Path, prefix: str,
                 base: Optional[str] = None, recorded: bool = True) -> tuple[int, int, dict[str, Any]]:
    """Run the order's setup lines in order, each in its directory (of the base clones
    when *base* is given, ``_dir``), and stop at the first that fails: (its exit or 0,
    its line number or 0, the PATH and environment collected). Each line's
    ``$GITHUB_PATH`` / ``$GITHUB_ENV`` files and log are ``<prefix><n>.*`` in *files*.
    *recorded*: write each line to ``SETUP_FILE`` and the state to ``ENV_FILE`` (the
    task's own setup, which the checks run with; never the base's)."""
    state = {"path": [str(PACKAGE_BIN)] if PACKAGE_BIN.is_dir() else [], "env": {}}
    for n, item in enumerate(order["checks"].get("setup") or [], 1):
        cwd = _dir(ws, order, item["repo"], item.get("cwd") or "", base)
        gh_path, gh_env = files / f"{prefix}{n}.path", files / f"{prefix}{n}.env"
        gh_path.write_text("")
        gh_env.write_text("")
        log = files / f"{prefix}{n}.log"
        started = _now()
        code = _run_one(item["command"], cwd,
                        _env_for(state, {"GITHUB_PATH": str(gh_path), "GITHUB_ENV": str(gh_env)}), log)
        if recorded:
            _append(files / SETUP_FILE, {"line": n, "exit": code, "started_at": started, "ended_at": _now(),
                                         "log": str(log.relative_to(ws))})
        if code != 0:
            return code, n, state
        state["path"] = [*reversed(_read_github_path(gh_path, cwd)), *state["path"]]
        state["env"].update(_read_github_env(gh_env))
        if recorded:
            (files / ENV_FILE).write_text(json.dumps(state), encoding="utf-8")
        print(f"{'base ' if base else ''}setup line {n}: ok")
    return 0, 0, state


def setup(work_id: str) -> int:
    ws = _workspace()
    order = _order(ws, work_id)
    cdir = _checks_dir(ws, work_id)
    code, n, state = _setup_lines(ws, order, cdir, "setup-")
    if code != 0:
        log = cdir / f"setup-{n}.log"
        print(f"setup line {n} exited {code} (log {log.relative_to(ws)}):\n{_tail(log)}")
        return code
    if not order["checks"].get("setup"):
        print("no setup lines")
    (cdir / ENV_FILE).write_text(json.dumps(state), encoding="utf-8")
    return 0


#: the prefix of the directory a ``run`` call keeps its base clones in (``_Base``),
#: inside the checks directory: one of its own per call, so calls run at the same time
#: never remove or reuse each other's
BASE_DIR = "base-"


class _Base:
    """The base a failed check is run on once more: a clone of each repository of the
    order at the commit the run started from (core/hive_workspace ``base_clone``: the
    order's ``base``, else its ``sha``), with the order's own setup lines run there, as on the task's clones
    (the base's own lockfiles; nothing borrowed from the change's setup, whose output
    lives in the change's clones). Made at the first failed check of a ``run`` call,
    used for every failed check of that call, removed when the call ends (``remove``).
    When it cannot be made, ``error`` says why and no check runs on it."""

    def __init__(self, ws: Path, order: dict[str, Any], cdir: Path) -> None:
        self.ws, self.order, self.cdir = ws, order, cdir
        self.root: Optional[Path] = None
        self.rel = ""
        self.made = False
        self.error: Optional[str] = None
        self.state: dict[str, Any] = {"path": [], "env": {}}

    def _make(self) -> None:
        import tempfile
        from core import hive_workspace
        self.made = True
        # this call's own (a call stopped by SIGKILL leaves one: the release removes it)
        self.root = Path(tempfile.mkdtemp(prefix=BASE_DIR, dir=self.cdir))
        self.rel = str(self.root.relative_to(self.ws))
        if not hive_workspace._plain(self.ws, self.rel):
            raise _NoBase("the base directory is not a plain directory of the work order")
        for r in self.order.get("repos") or []:
            name = r.get("name") if isinstance(r, dict) else None
            try:
                hive_workspace.base_clone(self.ws, self.order, name, self.root / str(name))
            except hive_workspace.WorkError as exc:
                raise _NoBase(f"{name}: {exc}") from None
        # its logs beside the checks' own (``base-setup-<n>.log``), kept after the clones go
        code, n, self.state = _setup_lines(self.ws, self.order, self.cdir, "base-setup-", base=self.rel,
                                           recorded=False)
        if code != 0:
            raise _NoBase(f"setup line {n} exited {code} on the base (log "
                          f"{(self.cdir / f'base-setup-{n}.log').relative_to(self.ws)})")

    def run(self, item: dict[str, Any], cid: str) -> dict[str, Any]:
        """``{"base_exit", "base_log"}`` of *item* run on the base, or ``{"base_error"}``."""
        try:
            if not self.made:
                try:
                    self._make()
                except _BadDir as exc:                    # a setup line's directory the base lacks
                    raise _NoBase(str(exc)) from None
            if self.error:
                raise _NoBase(self.error)
            try:
                cwd = _dir(self.ws, self.order, item["repo"], item.get("cwd") or "", self.rel)
            except _BadDir as exc:
                return {"base_error": str(exc)}       # this check's directory only
            log = self.cdir / f"{cid}.base.log"
            code = _run_one(item["command"], cwd, _env_for(self.state), log)
            return {"base_exit": code, "base_log": str(log.relative_to(self.ws))}
        except OSError as exc:              # a directory or clone the file system refused
            self.error = f"the base could not be made ({type(exc).__name__})"
            return {"base_error": self.error}
        except _NoBase as exc:
            self.error = str(exc)
            return {"base_error": self.error}

    def remove(self) -> None:
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)


class _NoBase(Exception):
    """The base cannot be made (``_Base``)."""


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
    base = _Base(ws, order, cdir)
    worst = 0
    try:
        for cid in ids or list(by_id):
            item = by_id[cid]
            cwd = _dir(ws, order, item["repo"], item.get("cwd") or "")
            log = cdir / f"{cid}.log"
            started = _now()
            record = cdir / hive_workspace.CHECKS_FILE
            _append(record, {"id": cid, "started_at": started})
            code = _run_one(item["command"], cwd, _env_for(state), log)
            ended = {"id": cid, "exit": code, "started_at": started, "ended_at": _now(),
                     "log": str(log.relative_to(ws))}
            # written before the base run: a call stopped during it leaves this line last,
            # which reads as the check's own failure (errored), as before the base run existed
            _append(record, ended)
            print(f"{cid}: exit {code} (log {log.relative_to(ws)})")
            if code == 0:
                continue
            print(_tail(log))
            worst = 1
            print(f"{cid}: running it once more on the commit the task started from "
                  "(with the setup lines first), to tell a failure the base already has from "
                  "one this change caused")
            got = base.run(item, cid)
            _append(record, {**ended, **got})
            if "base_exit" in got:
                print(f"{cid}: on the base, exit {got['base_exit']} (log {got['base_log']}): "
                      + ("it fails there too, with the same exit (already failing on the base)"
                         if got["base_exit"] == code else
                         "it passes there, so this change makes it fail" if got["base_exit"] == 0 else
                         "it fails there another way, so it reads as this change's failure"))
            else:
                print(f"{cid}: not run on the base ({got['base_error']}); it reads as failed")
    finally:
        base.remove()
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
