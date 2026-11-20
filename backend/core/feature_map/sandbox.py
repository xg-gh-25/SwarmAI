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
- removed after the run unless ``keep``.

What it does not contain: the app's own server-side network calls and the repo's own
files (a repo-local ``.env`` is part of the checkout) — the trust of running the repo's
tests. The browser is kept off the network by the replay's proxy (``replay``).

Failures raise ``SandboxError`` whose ``reason`` is a not_run reason: ``dependencies
missing``, ``needs <command>``, ``launch failed``, ``not ready``, ``port taken``.
A readiness URL and a base URL must be http(s) to a loopback host (``loopback_url``):
a target file cannot make the person's machine poll another host.

Exports: Sandbox, SandboxError, allowed_env, cache_dirs, loopback_url.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Mapping

from core.feature_map.files import FeatureMap, Target

STEP_TIMEOUT_S = 1200          # a hang guard per prepare/setup command, never a budget
PORT_CLOSE_S = 10              # how long a stopped service's port may take to close
PREPARE_STATE = ".prepare"     # {state_dir} while preparing: no start state's name ('.')
_POSIX_ALLOW = ("PATH", "LANG", "LC_ALL", "TERM", "TZ", "HTTPS_PROXY", "HTTP_PROXY",
                "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
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


def allowed_env(sandbox: Path, real_home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The services' environment: the allowlist, a sandbox HOME, empty AWS files,
    the real package caches, then *extra* (the target's own ``env``)."""
    names = _POSIX_ALLOW + (_WIN_ALLOW if sys.platform == "win32" else ())
    env = {k: os.environ[k] for k in names if k in os.environ}
    home, tmp = sandbox / "home", sandbox / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    empty = sandbox / "empty"
    empty.write_text("", encoding="utf-8")
    env.update({"HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(tmp), "TEMP": str(tmp),
                "TMP": str(tmp), "AWS_CONFIG_FILE": str(empty),
                "AWS_SHARED_CREDENTIALS_FILE": str(empty), "AWS_EC2_METADATA_DISABLED": "true",
                "FEATURE_MAP_SANDBOX": str(sandbox), "CI": "1"})
    env.update(cache_dirs(real_home))
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
                 folder_copy: Path | None = None):
        self.root = Path(root)
        self.keep = keep
        self.real_home = Path(real_home or Path.home())
        self.folder_copy = folder_copy          # a Brain-home feature folder to copy in
        self.dir: Path | None = None
        self.checkout: Path | None = None
        self.ports: dict[str, int] = {}
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.prepared: set[str] = set()         # targets whose ports and prepare are done
        self.running: set[str] = set()          # targets whose services are up
        self.state_dir: Path | None = None      # the current start state's folder
        self.logs: dict[str, Path] = {}

    def __enter__(self) -> "Sandbox":
        # the real path: on macOS the temp folder is reached through /var -> /private/var,
        # and tools that resolve paths (Playwright) would otherwise disagree with ours
        self.dir = Path(tempfile.mkdtemp(prefix="swarm-feature-map-")).resolve()
        self.checkout = self.dir / "checkout"
        _copy_checkout(self.root, self.checkout)
        (self.dir / "logs").mkdir()
        self.state_dir = self.dir / "state" / PREPARE_STATE
        self.state_dir.mkdir(parents=True)
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        for _sid, proc in reversed(self.procs):
            _kill_group(proc)
        self.procs.clear()
        if self.dir and not self.keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    # placeholders: {port.<service>}, {sandbox}, {checkout}, {state_dir}
    def expand(self, text: str) -> str:
        out = (text.replace("{state_dir}", str(self.state_dir)).replace("{sandbox}", str(self.dir))
               .replace("{checkout}", str(self.checkout)))
        for sid, port in self.ports.items():
            out = out.replace("{port." + sid + "}", str(port))
        return out

    def env(self, target: Target) -> dict[str, str]:
        return allowed_env(self.dir, self.real_home,
                           {k: self.expand(v) for k, v in target.env.items()})

    def _cwd(self, target: Target) -> Path:
        return self.checkout / target.root if target.root else self.checkout

    def _run(self, argv: Iterable[str], target: Target, reason: str, label: str) -> None:
        argv = [self.expand(a) for a in argv]
        log = self.dir / "logs" / f"{target.id}-{label}.log"
        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
                  if sys.platform == "win32" else {"start_new_session": True})
        try:
            with open(log, "ab") as out:
                proc = subprocess.Popen(argv, cwd=str(self._cwd(target)), env=self.env(target),
                                        stdout=out, stderr=subprocess.STDOUT, **kwargs)
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
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
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
            log = self.dir / "logs" / f"{target.id}-{svc.id}.log"
            self.logs[svc.id] = log
            argv = [self.expand(a) for a in svc.argv]
            kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
                      if sys.platform == "win32" else {"start_new_session": True})
            try:
                out = open(log, "ab")
                proc = subprocess.Popen(argv, cwd=str(self._cwd(target)), env=self.env(target),
                                        stdout=out, stderr=subprocess.STDOUT, **kwargs)
            except FileNotFoundError as exc:
                raise SandboxError(f"needs {argv[0]}", str(exc)) from exc
            finally:
                out.close()
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
            if folder.exists():
                shutil.rmtree(folder)
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
                tail = self.logs[sid].read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
                raise SandboxError("launch failed", f"{sid} exited {proc.returncode} before it was "
                                   "ready: " + " | ".join(tail)[:600])
            try:
                with urllib.request.urlopen(url, timeout=2):   # noqa: S310 — loopback readiness URL
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
        copied, its setup run."""
        st = target.start_states.get(state)
        if st is None:
            raise SandboxError("bad file", f"target {target.id} has no start state {state}")
        if st.fixture:
            src = (fmap.folder / st.fixture)
            dest = Path(self.expand(st.to)) if st.to else self.dir / "fixture" / Path(st.fixture).name
            if not dest.resolve().is_relative_to(self.dir.resolve()):
                raise SandboxError("bad file", f"start state {state} copies outside the sandbox")
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            elif src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            else:
                raise SandboxError("bad file", f"fixture {st.fixture} does not exist")
        for i, argv in enumerate(st.setup):
            self._run(argv, target, "launch failed", f"setup-{state}-{i}")
