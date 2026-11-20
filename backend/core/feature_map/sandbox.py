"""The product-built environment a replay runs in (design §6.2-§6.3).

The repo's commands do not decide it:

- a fresh temp directory with a **copy** of the checkout's tracked and untracked
  (not ignored) files, so a replay never builds in, or writes to, the person's working
  tree. Dependency folders are installed fresh by the target's ``prepare`` (the
  package managers' cache directories are passed through so it is fast); a link to the
  main checkout's ``node_modules`` is never reused, because an ``npm ci`` through it
  would wipe the person's own folder;
- the environment emptied to a per-OS allowlist, plus the target's ``env``: a ``HOME``
  inside the sandbox, AWS credential and config files pointed at an empty file,
  ``AWS_EC2_METADATA_DISABLED`` set;
- free ports chosen here, below the OS's ephemeral range (``PORT_RANGE``); a port
  that already answers is refused;
- every service in its own session / process group (a new process group on Windows),
  so cleanup kills the whole group and never the caller's (a capped helper that killed
  its own group once took the agent and the daemon with it); readiness counts only while
  the service's own process is alive;
- each start state from fresh services (``fresh_start``): every running service is
  stopped, the state gets an empty folder ``{state_dir}`` = ``<sandbox>/state/<state>``,
  and the target and the targets it needs are started again on the ports chosen when
  they were prepared (a prepare step may build a port into the app). Prepare runs once
  per target (``prepare``). A target that keeps its data under ``{state_dir}`` starts
  every state from a clean app; what it keeps elsewhere (``HOME``, the checkout) is
  shared by the states;
- removed after the run unless ``keep``;
- on a machine that needs it (a Hive, a Linux machine where the replay user exists, one
  whose instance metadata service answers), every process it starts runs as the replay
  user (``user``, ``core.feature_map.replay_user``),
  whose traffic to the metadata service the host's firewall rejects: the folder is given to
  that user (ACL), each start goes through sudo with a spawn tag, stopping a start stops
  every process with its tag as that user, closing stops every process of that user, and
  the package caches are that user's own. Once a process of that user has run, the
  person's process writes nothing in the folder by a name that process could have set up
  (a link there would turn the write into one anywhere the person can write): what it
  writes there is a new file (``write_new``: a random name, exclusive create, links not
  followed), a log opened once, exclusively, under the ``logs`` folder handle taken before
  the folder was given away; a start state's folder and its fixture are made by the replay
  user itself; ``HOME``, ``TMPDIR`` and the empty AWS file are made before any process runs.
  The proxy variables are not passed then (a proxy is a way around the firewall rule).

What it does not contain: the app's own server-side network calls and the repo's own
files (a repo-local ``.env`` is part of the checkout) — the trust of running the repo's
tests; on a machine with a metadata service, the instance's credentials are out of reach
(the replay user). The browser is kept off the network by the replay's proxy (``replay``).

Failures raise ``SandboxError`` whose ``reason`` is a not_run reason: ``dependencies
missing``, ``needs <command>``, ``launch failed``, ``not ready``, ``port taken``.
A readiness URL and a base URL must be http(s) to a loopback host (``loopback_url``):
a target file cannot make the person's machine poll another host.

Exports: Sandbox, SandboxError, allowed_env, cache_dirs, loopback_url, make_env_files.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Mapping

from core.feature_map.files import FeatureMap, Target
from core.feature_map.replay_user import ReplayUser

STEP_TIMEOUT_S = 1200          # a hang guard per prepare/setup command, never a budget
PORT_CLOSE_S = 10              # how long a stopped service's port may take to close
PREPARE_STATE = ".prepare"     # {state_dir} while preparing: no start state's name ('.')
_PROXY = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
_POSIX_ALLOW = ("PATH", "LANG", "LC_ALL", "TERM", "TZ", *_PROXY)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_LOG_TAIL_BYTES = 64 * 1024
_READ_NEW_MAX = 64 * 1024 * 1024     # a Playwright report or listing; more is refused


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A readiness probe never follows a redirect (a service could point it at another host,
    the metadata service included): a 3xx is an answer, so the service is ready."""

    def redirect_request(self, *_args, **_kwargs):
        return None


# loopback only: no proxy from the person's environment, no redirect
_PROBE = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_WIN_ALLOW = ("SystemRoot", "windir", "COMSPEC", "PATHEXT", "SystemDrive",
              "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")
# the caches a prepare step reuses, pointed at the person's real cache folders: each
# tool's own default for the platform (the sandbox HOME would otherwise make every
# replay start from a cold cache). Each entry: (macOS, Linux, Windows) relative to the
# real home; "XDG:" = under $XDG_CACHE_HOME when set, "LOCAL:" = under %LOCALAPPDATA%.
_CACHE_VARS = {
    "npm_config_cache": (".npm", ".npm", "LOCAL:npm-cache"),
    "YARN_CACHE_FOLDER": ("Library/Caches/Yarn", "XDG:yarn", "LOCAL:Yarn/Cache"),
    "PIP_CACHE_DIR": ("Library/Caches/pip", "XDG:pip", "LOCAL:pip/Cache"),
    "UV_CACHE_DIR": ("Library/Caches/uv", "XDG:uv", "LOCAL:uv/cache"),
    "GOMODCACHE": ("go/pkg/mod", "go/pkg/mod", "go/pkg/mod"),
}


def cache_dirs(real_home: Path, platform: str | None = None,
               environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """{variable: folder} of the package caches for *platform* (default: this one). A
    variable the person set themselves wins."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    column = 0 if platform == "darwin" else 2 if platform == "win32" else 1
    out = {}
    for var, defaults in _CACHE_VARS.items():
        if environ.get(var):
            out[var] = environ[var]
            continue
        rel = defaults[column]
        if rel.startswith("XDG:"):
            base = Path(environ.get("XDG_CACHE_HOME") or real_home / ".cache")
            out[var] = str(base / rel[4:])
        elif rel.startswith("LOCAL:"):
            base = Path(environ.get("LOCALAPPDATA") or real_home / "AppData" / "Local")
            out[var] = str(base / rel[6:])
        else:
            out[var] = str(real_home / rel)
    return out


class SandboxError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def make_env_files(sandbox: Path) -> None:
    """The folders and the file ``allowed_env`` points at: ``home``, ``tmp`` and ``empty``
    in *sandbox*. Made once, before any process runs in it (never written again: by then a
    process of another user may have replaced them with links)."""
    (sandbox / "home").mkdir(exist_ok=True)
    (sandbox / "tmp").mkdir(exist_ok=True)
    fd = os.open(sandbox / "empty", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NOFOLLOW, 0o644)
    os.close(fd)


def allowed_env(sandbox: Path, real_home: Path, extra: Mapping[str, str] | None = None,
                *, own_caches: bool = True) -> dict[str, str]:
    """The services' environment: the allowlist, a sandbox HOME, empty AWS files,
    the real package caches, then *extra* (the target's own ``env``). It writes nothing:
    the folders and the empty file are ``make_env_files``'s. *own_caches* False (another
    user runs the services): the caches under *real_home* only, never the person's own
    cache variables, and no proxy variables."""
    names = _POSIX_ALLOW + (_WIN_ALLOW if sys.platform == "win32" else ())
    if not own_caches:
        names = tuple(n for n in names if n not in _PROXY)
    env = {k: os.environ[k] for k in names if k in os.environ}
    home, tmp, empty = sandbox / "home", sandbox / "tmp", sandbox / "empty"
    env.update({"HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(tmp), "TEMP": str(tmp),
                "TMP": str(tmp), "AWS_CONFIG_FILE": str(empty),
                "AWS_SHARED_CREDENTIALS_FILE": str(empty), "AWS_EC2_METADATA_DISABLED": "true",
                "FEATURE_MAP_SANDBOX": str(sandbox), "CI": "1"})
    env.update(cache_dirs(real_home) if own_caches else cache_dirs(real_home, environ={}))
    env.setdefault("LANG", "C.UTF-8")
    env.update(dict(extra or {}))
    return env


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def loopback_url(url: str, what: str) -> str:
    """*url* when it is http(s) to 127.0.0.1, localhost or [::1]; else ``bad file``."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        host, parts = None, None
    if parts is None or parts.scheme not in ("http", "https") or host not in _LOOPBACK:
        raise SandboxError("bad file", f"{what} must be http(s) to 127.0.0.1, localhost or "
                           f"[::1], not {url[:120]}")
    return url


# Below every OS's ephemeral range (macOS/Windows 49152+, Linux 32768+). A port the OS
# hands out for bind(0) is free only until any process asks for one: a service that
# opens its own listener on port 0 (the SwarmAI backend does) can take the port chosen
# for the next service. A port from this range is never handed out that way.
PORT_RANGE = (20000, 32000)


def _free_port(taken: Iterable[int] = ()) -> int:
    """A loopback port no one listens on, from ``PORT_RANGE``, not in *taken*."""
    import random
    taken = set(taken)
    for _ in range(200):
        port = random.randrange(*PORT_RANGE)
        if port in taken or _answers(port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise SandboxError("port taken", f"no free port in {PORT_RANGE[0]}-{PORT_RANGE[1]}")


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _copy_checkout(root: Path, dest: Path) -> None:
    """Tracked + untracked (not ignored) files of *root*, symlinks kept as links."""
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
                            capture_output=True, check=True, timeout=300).stdout
    dest.mkdir(parents=True, exist_ok=True)
    for raw in listed.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        src, dst = root / rel, dest / rel
        if not src.exists() and not src.is_symlink():
            continue                       # deleted in the working tree
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            try:
                os.symlink(os.readlink(src), dst)
            except OSError:
                shutil.copy2(src, dst, follow_symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dst)


def _kill_group(proc: subprocess.Popen) -> None:
    kill_tag = getattr(proc, "fm_kill", None)
    if kill_tag is not None:
        kill_tag()                         # every process of that start, as the replay user
    if proc.poll() is not None and sys.platform == "win32":
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGTERM)   # the child's own group (new session), never ours
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


class Sandbox:
    """``with Sandbox(root) as sb: sb.fresh_start(fmap, target, "home");
    sb.setup(fmap, target, "home")``."""

    def __init__(self, root: Path, *, keep: bool = False, real_home: Path | None = None,
                 folder_copy: Path | None = None, user: ReplayUser | None = None):
        self.root = Path(root)
        self.keep = keep
        self.user = user                        # who its processes run as (None: the person)
        self.real_home = Path(user.home if user else (real_home or Path.home()))
        self._spawns = 0
        self.folder_copy = folder_copy          # a Brain-home feature folder to copy in
        self.dir: Path | None = None
        self.checkout: Path | None = None
        self.ports: dict[str, int] = {}
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.prepared: set[str] = set()         # targets whose ports and prepare are done
        self.running: set[str] = set()          # targets whose services are up
        self.state_dir: Path | None = None      # the current start state's folder
        self.logs: dict[str, str] = {}          # service id -> its log's name under logs/
        self.features: Path | None = None       # the sandbox copy of the feature folder
        self._logs_fd: int | None = None        # logs/, opened before the folder was given away
        self._log_fds: dict[str, int] = {}      # log name -> its file, opened once

    def __enter__(self) -> "Sandbox":
        # the real path: on macOS the temp folder is reached through /var -> /private/var,
        # and tools that resolve paths (Playwright) would otherwise disagree with ours
        self.dir = Path(tempfile.mkdtemp(prefix="swarm-feature-map-")).resolve()
        self.checkout = self.dir / "checkout"
        _copy_checkout(self.root, self.checkout)
        (self.dir / "logs").mkdir()
        if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
            self._logs_fd = os.open(self.dir / "logs", os.O_RDONLY | os.O_DIRECTORY)
        self.state_dir = self.dir / "state" / PREPARE_STATE
        self.state_dir.mkdir(parents=True)
        make_env_files(self.dir)
        if self.user is not None:
            self.user.grant(self.dir)
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        for _sid, proc in reversed(self.procs):
            _kill_group(proc)
        self.procs.clear()
        self.stop_user()
        self.close_logs()
        if self.dir and not self.keep:
            self._remove(self.dir)

    def close_logs(self) -> None:
        """Close the log files and the ``logs`` folder handle (the files stay)."""
        for fd in [*self._log_fds.values(), *([self._logs_fd] if self._logs_fd is not None else [])]:
            try:
                os.close(fd)
            except OSError:
                pass
        self._log_fds.clear()
        self._logs_fd = None

    def log_path(self, name: str) -> Path:
        return self.dir / "logs" / name

    def _log(self, name: str) -> int:
        """The file of log *name* under ``logs/``: created the first time, exclusively and
        without following a link (a name something in the sandbox made first is refused),
        then kept open for every later start that writes to it."""
        fd = self._log_fds.get(name)
        if fd is not None:
            return fd
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY
        try:
            if self._logs_fd is not None:
                fd = os.open(name, flags, 0o640, dir_fd=self._logs_fd)
            else:
                fd = os.open(self.log_path(name), flags, 0o640)
        except FileExistsError as exc:
            raise SandboxError("launch failed", f"logs/{name} exists before the sandbox made it "
                               "(something in the sandbox put it there)") from exc
        except OSError as exc:
            raise SandboxError("launch failed", f"logs/{name} could not be made: {exc}") from exc
        self._log_fds[name] = fd
        return fd

    def _tail(self, name: str, lines: int = 5) -> list[str]:
        """The last *lines* lines of log *name*, read from its own file (never by name)."""
        fd = self._log_fds.get(name)
        if fd is None:
            return []
        try:
            if hasattr(os, "pread"):
                size = os.fstat(fd).st_size
                start = max(0, size - _LOG_TAIL_BYTES)
                data = os.pread(fd, size - start, start)
            else:                                      # Windows: no replay user there
                data = self.log_path(name).read_bytes()[-_LOG_TAIL_BYTES:]
        except OSError:
            return []
        return data.decode("utf-8", errors="replace").strip().splitlines()[-lines:]

    def write_new(self, stem: str, suffix: str, text: str) -> Path:
        """A new file directly in the sandbox folder holding *text*: a random name, created
        exclusively, a link never followed; readable by the replay user (its mode keeps the
        folder's ACL entry for that user)."""
        path = self.dir / f"{stem}.{secrets.token_hex(8)}{suffix}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o640)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o640)                   # the umask may have dropped the group bit
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
        return path

    def read_new(self, path: Path) -> str:
        """The text of *path*, a file in the sandbox folder a process of the sandbox wrote,
        without following a link at its name (``OSError`` when it is one)."""
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"{path} is not a regular file")
            if st.st_size > _READ_NEW_MAX:
                raise OSError(f"{path} is larger than {_READ_NEW_MAX} bytes")
            return fh.read(_READ_NEW_MAX).decode("utf-8", errors="replace")

    def stop_user(self) -> bool:
        """Stop every process of the replay user (this process holds the machine's
        replay-user lock, ``replay_user.resolve``), also one that cleared its tag or left its
        group; at least every tagged one of this sandbox when that cannot run. True when none
        is left (or there is no replay user): only then is nothing changing the sandbox."""
        if self.user is None or not self.dir:
            return True
        left = self.user.kill_all()
        if left is None:
            self.user.kill(self.dir.name + "/", prefix=True)
        if left != 0:
            # the next replay's resolve refuses until they are gone
            logging.getLogger(__name__).warning(
                "feature map: %s process(es) of %s survived the replay",
                "unknown" if left is None else left, self.user.name)
        return left == 0

    def _remove(self, path: Path) -> None:
        """Remove *path*: what the replay user wrote first, as that user, then the rest."""
        if self.user is not None and path.exists():
            self.user.remove(path)
        shutil.rmtree(path, ignore_errors=True)

    def popen(self, argv: list[str], *, cwd: Path, env: Mapping[str, str],
              **kwargs) -> subprocess.Popen:
        """Start *argv* in its own process group (session), as the replay user when there is
        one: through sudo, its environment in a file only the two users read, a spawn tag in
        it that ``_kill_group`` stops it by. ``FileNotFoundError`` when the program is not
        on the environment's PATH."""
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
                 if sys.platform == "win32" else {"start_new_session": True})
        if self.user is None:
            return subprocess.Popen(argv, cwd=str(cwd), env=dict(env), **kwargs, **group)
        program = argv[0]
        if os.sep not in program:
            program = shutil.which(program, path=env.get("PATH", "")) or ""
            if not program:
                raise FileNotFoundError(f"{argv[0]} is not on PATH")
        self._spawns += 1
        tag = f"{self.dir.name}/{self._spawns}"
        envfile = self.write_new(".spawn", ".json", json.dumps({**env, "FEATURE_MAP_SPAWN": tag}))
        cmd = self.user.command("exec", str(envfile), str(cwd), program, *argv[1:], tag=tag)
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        proc = subprocess.Popen(cmd, cwd=str(cwd), env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                **kwargs, **group)
        proc.fm_kill = lambda: self.user.kill(tag)       # type: ignore[attr-defined]
        return proc

    # placeholders: {port.<service>}, {sandbox}, {checkout}, {state_dir}
    def expand(self, text: str) -> str:
        out = (text.replace("{state_dir}", str(self.state_dir)).replace("{sandbox}", str(self.dir))
               .replace("{checkout}", str(self.checkout)))
        for sid, port in self.ports.items():
            out = out.replace("{port." + sid + "}", str(port))
        return out

    def env(self, target: Target) -> dict[str, str]:
        return allowed_env(self.dir, self.real_home,
                           {k: self.expand(v) for k, v in target.env.items()},
                           own_caches=self.user is None)

    def _cwd(self, target: Target) -> Path:
        return self.checkout / target.root if target.root else self.checkout

    def _run(self, argv: Iterable[str], target: Target, reason: str, label: str) -> None:
        argv = [self.expand(a) for a in argv]
        log = f"{target.id}-{label}.log"
        try:
            proc = self.popen(argv, cwd=self._cwd(target), env=self.env(target),
                              stdout=self._log(log), stderr=subprocess.STDOUT)
        except FileNotFoundError as exc:
            raise SandboxError(f"needs {argv[0]}", str(exc)) from exc
        try:
            proc.wait(timeout=STEP_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc)
            raise SandboxError(reason, f"{' '.join(argv)[:200]} did not finish in "
                               f"{STEP_TIMEOUT_S}s") from exc
        except BaseException:
            _kill_group(proc)              # interrupted: never leave its group running
            raise
        _kill_group(proc)                  # anything it left behind in its group
        if proc.returncode != 0:
            tail = self._tail(log)
            raise SandboxError(reason, f"{' '.join(argv)[:200]} exited {proc.returncode}: "
                               + " | ".join(tail)[:600])

    def prepare(self, fmap: FeatureMap, target: Target) -> None:
        """Choose the ports of *target* and of every target it needs, and run their prepare
        commands, once per target (needs first). The ports are kept for every later start."""
        for need in target.needs:
            if need not in self.prepared:
                self.prepare(fmap, fmap.targets[need])
        if target.id in self.prepared:
            return
        for svc in target.services:
            self.ports.setdefault(svc.id, _free_port(self.ports.values()))
        # prepare runs before (or between) start states: {state_dir} is a folder none of
        # them sees, also when a target is prepared after another one's states ran
        current, self.state_dir = self.state_dir, self.dir / "state" / PREPARE_STATE
        try:
            for i, argv in enumerate(target.prepare):
                self._run(argv, target, "dependencies missing", f"prepare-{i}")
        finally:
            self.state_dir = current
        self.prepared.add(target.id)

    def start(self, fmap: FeatureMap, target: Target) -> None:
        """Start the services of *target* and of every target it needs (needs first),
        preparing each one not prepared yet. A target already running is left as it is."""
        for need in target.needs:
            if need not in self.running:
                self.start(fmap, fmap.targets[need])
        if target.id in self.running:
            return
        self.prepare(fmap, target)
        for svc in target.services:
            port = self.ports[svc.id]
            ready = loopback_url(self.expand(svc.ready), f"{svc.id} ready")   # before it starts
            if _answers(port):
                raise SandboxError("port taken", f"127.0.0.1:{port} answers before {svc.id} started")
            log = f"{target.id}-{svc.id}.log"
            self.logs[svc.id] = log
            argv = [self.expand(a) for a in svc.argv]
            try:
                proc = self.popen(argv, cwd=self._cwd(target), env=self.env(target),
                                  stdout=self._log(log), stderr=subprocess.STDOUT)
            except FileNotFoundError as exc:
                raise SandboxError(f"needs {argv[0]}", str(exc)) from exc
            self.procs.append((svc.id, proc))
            self._wait_ready(target, svc.id, ready, proc)
        self.running.add(target.id)

    def stop(self) -> None:
        """Stop every running service (each one's whole group), then wait until their
        ports are closed, so the next start finds them free."""
        ports = [self.ports[sid] for sid, _proc in self.procs if sid in self.ports]
        for _sid, proc in reversed(self.procs):
            _kill_group(proc)
        self.procs.clear()
        self.running.clear()
        deadline = time.monotonic() + PORT_CLOSE_S
        for port in ports:
            while _answers(port) and time.monotonic() < deadline:
                time.sleep(0.1)

    def fresh_start(self, fmap: FeatureMap, target: Target, state: str) -> None:
        """Begin start state *state* of *target* from fresh services: stop every running
        service, give the state an empty folder (``{state_dir}``), then start *target* and
        the targets it needs again. The state's own setup is ``setup``. A state name two
        targets share names the same folder, emptied again for each."""
        base = self.dir / "state"
        folder = base / state
        # the folder is emptied: it must be a direct child of state/ (a state name is an
        # identifier, files._ident; this holds even if a name arrives another way)
        if state == PREPARE_STATE or folder.name != state or folder.resolve().parent != base.resolve():
            raise SandboxError("bad file", f"start state {state[:80]!r} is not a folder name")
        self.stop()
        try:
            if self.user is not None:
                # by now a process of the replay user may have made state/ (or the folder) a
                # link: that user empties and makes it, so nothing outside its reach is touched
                self.user.remove(folder)
                if os.path.lexists(folder):
                    raise OSError(f"{folder} could not be removed")
                self.user.mkdir(folder)
            else:
                if folder.exists():
                    self._remove(folder)
                    if folder.exists():
                        raise OSError(f"{folder} could not be removed")
                folder.mkdir(parents=True)
        except OSError as exc:
            raise SandboxError("launch failed", f"the folder of start state {state} could not "
                               f"be made empty: {exc}") from exc
        self.state_dir = folder
        self.start(fmap, target)

    def _wait_ready(self, target: Target, sid: str, url: str, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + target.ready_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = self._tail(self.logs[sid])
                raise SandboxError("launch failed", f"{sid} exited {proc.returncode} before it was "
                                   "ready: " + " | ".join(tail)[:600])
            try:
                with _PROBE.open(url, timeout=2):   # noqa: S310 — loopback readiness URL
                    return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return                 # it answers: ready
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        raise SandboxError("not ready", f"{sid} did not answer {url} in {target.ready_timeout_s}s")

    def setup(self, fmap: FeatureMap, target: Target, state: str) -> None:
        """Apply start state *state* of *target* (after ``fresh_start``): its fixture
        copied (from the sandbox copy of the feature folder, ``features``, when there is one;
        by the replay user when there is one), its setup run."""
        st = target.start_states.get(state)
        if st is None:
            raise SandboxError("bad file", f"target {target.id} has no start state {state}")
        if st.fixture:
            src = (self.features or fmap.folder) / st.fixture
            dest = Path(self.expand(st.to)) if st.to else self.dir / "fixture" / Path(st.fixture).name
            if not dest.resolve().is_relative_to(self.dir.resolve()):
                raise SandboxError("bad file", f"start state {state} copies outside the sandbox")
            if not src.exists():
                raise SandboxError("bad file", f"fixture {st.fixture} does not exist")
            if self.user is not None:
                # the services already ran as the replay user: the copy is that user's too
                problem = self.user.copy(src, dest)
                if problem:
                    raise SandboxError("launch failed", f"fixture {st.fixture}: {problem}")
            elif src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            elif src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            else:
                raise SandboxError("bad file", f"fixture {st.fixture} does not exist")
        for i, argv in enumerate(st.setup):
            self._run(argv, target, "launch failed", f"setup-{state}-{i}")
