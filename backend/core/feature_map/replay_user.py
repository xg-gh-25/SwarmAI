"""The replay user: who a replay's processes run as on a machine with an instance metadata
service (design §6.3).

On a cloud machine the metadata service (169.254.169.254, and fd00:ec2::254 on EC2) hands
the instance role's credentials to any process that asks; emptying the environment does not
stop that. So on such a machine every process the sandbox starts (prepare, setup, services,
command walks, Playwright and its browser) runs as a separate unprivileged user,
``swarm-replay``, whose traffic to those addresses the host's firewall rejects. The host
sets that up as root (``hive/replay-host.sh`` on a Hive: the user, an nftables rule matching
its uid, and a sudoers rule letting the Hive user run commands as it); this module only uses
it, and checks it first: a probe run as that user must fail to connect to the metadata
service, and must fail to open the person's data (``private_paths``: the app data folder's
``data.db``, and listing that folder), or replay is ``not_run: runtime unavailable`` and
nothing of the repo starts.

The replay user has no way into the Hive user's home (the replayed code is the repo under
test, and the home holds the person's conversations, memory and stored secrets). So nothing
a replay runs lives there: on such a machine the pinned runtime and the task tools are in
the shared location (``shared_dir``, ``SHARED_DIR``), which the host makes as root (the Hive
user writes it, the replay user's group reads it), and the PATH a replay process gets holds
no folder inside the home (``outside_home``). It holds tools only, never the person's data:
a replay can read every task's tools there, another task's venv included. What the Hive
user writes there with its 0077 umask is made readable for the replay user (``publish``)
before every probe.

Which machines need it (``needs_replay_user``) is decided from fixed facts first, never from
a probe that could fail open: a Hive (``SWARMAI_MODE=hive``) and a Linux machine where the
replay user exists always do; only elsewhere does a TCP connect to the metadata service
decide (a desktop: it does not answer, and the sandbox runs as the person, as before). That
"not needed" answer is never cached. Where the replay user is needed, the unit that loads its
network rule must be active (``network_rule_loaded``, a fixed fact: a container image such as
the Elastic Workers one has no systemd and no root at run time, so it never is) before the
probe runs at all.

One replay at a time uses the replay user on a machine: ``resolve`` takes a lock (held until
the process ends) and stops every process of the replay user before the replay starts, and
refuses when one survives; ``Sandbox.close`` stops them all again. So nothing a replay
started, also a process that cleared its environment or left its group, outlives it or
touches the next replay's files.

Every start goes ``sudo -n -u swarm-replay -- /usr/bin/env -i FEATURE_MAP_SPAWN=<tag>
<python> -I replay_user_helper.py exec <envfile> <cwd> <program> ...``: the environment
travels in a new file of the sandbox only the two users read (``Sandbox.write_new``; never on
a command line), the tag in every process of that start, so stopping one start (``kill``,
between start states) finds its tree by its tag, and acts as the replay user (the Hive user
may not signal it). Files the replay user wrote are removed as that user first
(``remove_tree``); a start state's folder and its fixture are made as that user (``mkdir``,
``copy``), because by then a process of that user may have put links in the sandbox.

Exports: REPLAY_USER, SHARED_DIR, ReplayUser, Unavailable, current, imds_answers,
needs_replay_user, outside_home, private_paths, publish, remove_tree, resolve, shared_dir.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import pwd  # Unix-only: absent on Windows, where no replay user is ever used
except ImportError:
    pwd = None  # type: ignore[assignment]

REPLAY_USER = "swarm-replay"
METADATA = (("169.254.169.254", 80), ("fd00:ec2::254", 80))
HELPER = Path(__file__).resolve().with_name("replay_user_helper.py")
LOCK_WAIT_S = 2 * 3600          # a hang guard for another replay holding the replay user
_HELPER_TIMEOUT_S = 60
_SUDO_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
#: the systemd unit hive/replay-host.sh installs to load the replay user's network rule at
#: boot (its NFT_UNIT): active only once that rule loaded
NETWORK_RULE_UNIT = "swarmai-replay-imds.service"
#: why a replay user without its network rule cannot be used
NO_NETWORK_RULE = (f"the network rule that keeps {REPLAY_USER} off the instance metadata service "
                   f"is not in force on this machine ({NETWORK_RULE_UNIT} is not active: "
                   "hive/replay-host.sh loads the rule as root with nftables at boot; a machine "
                   "without nftables, without CAP_NET_ADMIN or without root at run time, such as a "
                   "container image, has none)")
#: where the pinned runtime and the task tools live on a machine that uses the replay user,
#: outside the Hive user's home: hive/replay-host.sh makes it as root (its SHARED_DIR), owned
#: by the Hive user with the replay user's group. SWARM_REPLAY_SHARED_DIR names another
#: folder; set but empty, there is none (the test suite's setting).
SHARED_DIR = Path("/var/lib/swarmai/replay")
#: why a replay user that can read the person's data cannot be used
NO_PRIVATE_DATA = (f"{REPLAY_USER} can read the Hive user's data, and the replayed code must "
                   "not: hive/replay-host.sh (run as root by every Hive update) removes its way "
                   "into the Hive user's home and makes that home 0700")


class Unavailable(RuntimeError):
    """The machine needs the replay user and it cannot be used; ``reason`` says why."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def imds_answers(timeout_s: float = 0.5) -> bool:
    """Whether a TCP connection from this process to an instance metadata address succeeds."""
    for host, port in METADATA:
        try:
            socket.create_connection((host, port), timeout=timeout_s).close()
            return True
        except OSError:
            continue
    return False


def _user_exists() -> bool:
    if pwd is None:  # Windows
        return False
    try:
        pwd.getpwnam(REPLAY_USER)
        return True
    except KeyError:
        return False


def shared_dir() -> Path | None:
    """The shared location for the runtime and the task tools when this machine has it: a
    real folder (not a link) this user can write that keeps everyone else out (no access
    for others: what ``publish`` opens below it is reached only through its group or ACL);
    else None."""
    raw = os.environ.get("SWARM_REPLAY_SHARED_DIR")
    if raw == "":
        return None
    path = Path(raw) if raw else SHARED_DIR
    try:
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o007 or not os.access(path, os.W_OK):
            return None
    except OSError:
        return None
    return path


def publish(root: Path) -> None:
    """Make every folder and file this user owns below *root* readable by others (folders
    and files with the owner's execute bit runnable too), so the replay user, which reads
    *root* through its group, can use what this user wrote with a 0077 umask. *root* keeps
    its own mode; links are neither changed nor followed, nor is what another user owns."""
    me = os.getuid()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in (*dirnames, *filenames):
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if st.st_uid != me or stat.S_ISLNK(st.st_mode):
                continue
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                want = mode | 0o055
            elif stat.S_ISREG(st.st_mode):
                want = mode | 0o044 | (0o011 if mode & stat.S_IXUSR else 0)
            else:
                continue
            if want != mode:
                try:
                    os.chmod(path, want)
                except OSError:
                    pass


def private_paths() -> list[Path]:
    """The person's data the replay user must not reach: ``data.db`` and the app data
    folder itself, for the folder in effect and the default one (a SWARM_DATA_DIR override
    never hides the real store), and the Hive user's home (the boundary itself: a folder
    counts as reached when it can be listed or passed through)."""
    from config import default_app_data_dir, get_app_data_dir
    out: list[Path] = []
    for folder in (Path(get_app_data_dir()), Path(default_app_data_dir())):
        for path in (folder / "data.db", folder):
            if path not in out:
                out.append(path)
    if pwd is not None:
        try:
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError:
            home = None
        if home is not None and str(home) != os.sep and home not in out:
            out.append(home)
    return out


def outside_home(path_value: str) -> str:
    """*path_value* (a PATH) without the folders inside the Hive user's home (its passwd
    home and $HOME, links resolved): the replay user cannot run anything there, and a
    program found there by the Hive user would not start as the replay user."""
    homes = set()
    if pwd is not None:
        try:
            homes.add(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))
        except KeyError:
            pass
    homes.add(os.path.realpath(str(Path.home())))
    homes.discard(os.sep)                # a home of "/" would take every folder away
    keep = []
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        real = os.path.realpath(entry)
        if any(real == h or real.startswith(h.rstrip(os.sep) + os.sep) for h in homes):
            continue
        keep.append(entry)
    return os.pathsep.join(keep)


def needs_replay_user() -> bool:
    """Whether a replay here must run as the replay user: on a Hive, on a Linux machine
    where the replay user exists, and wherever the metadata service answers."""
    if os.environ.get("SWARMAI_MODE") == "hive":
        return True
    if sys.platform.startswith("linux") and _user_exists():
        return True
    return imds_answers()


@dataclass(frozen=True)
class ReplayUser:
    name: str
    uid: int
    home: Path                  # its own home: its package caches
    sudo: str
    python: str

    def command(self, mode: str, *args: str, tag: str | None = None) -> list[str]:
        """The argv that runs the helper's *mode* as this user."""
        env = [f"FEATURE_MAP_SPAWN={tag}"] if tag else []
        return [self.sudo, "-n", "-u", self.name, "--", "/usr/bin/env", "-i", *env,
                self.python, "-I", str(HELPER), mode, *args]

    def _helper(self, mode: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(mode, *args), capture_output=True, text=True,
                              timeout=_HELPER_TIMEOUT_S, env=_SUDO_ENV, stdin=subprocess.DEVNULL)

    def kill(self, tag: str, *, prefix: bool = False) -> None:
        """Stop every process of this user tagged *tag* (or tagged with a value starting
        with *tag*): TERM, up to 3 s, KILL."""
        try:
            self._helper("kill", tag, *(["--prefix"] if prefix else []))
        except (OSError, subprocess.SubprocessError):
            pass

    def kill_all(self) -> int | None:
        """Stop every process of this user (TERM, up to 3 s, KILL); how many are still
        there after it (None: the helper did not answer)."""
        if self.uid == os.getuid():
            return None                  # never the caller's own processes
        try:
            proc = self._helper("kill", "--all", str(os.getuid()))
            return int(json.loads(proc.stdout.strip().splitlines()[-1])["left"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
            return None

    def remove(self, path: Path) -> None:
        """Remove what this user may of *path* (the caller removes the rest)."""
        try:
            self._helper("rm", str(path))
        except (OSError, subprocess.SubprocessError):
            pass

    def _must(self, mode: str, *args: str) -> str:
        """Run helper *mode*; "" when it succeeded, else why not."""
        try:
            proc = self._helper(mode, *args)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"sudo -u {self.name} {mode} could not run: {exc}"
        if proc.returncode == 0:
            return ""
        return (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[-300:]

    def mkdir(self, path: Path) -> None:
        """Make folder *path* as this user (its parent must exist). Raises ``OSError``."""
        problem = self._must("mkdir", str(path))
        if problem:
            raise OSError(f"{self.name} could not make {path}: {problem}")

    def copy(self, src: Path, dest: Path) -> str:
        """Copy file or folder *src* to *dest* as this user (links copied as links, a
        folder merged into an existing one); "" when done, else why not."""
        return self._must("copy", str(src), str(dest))

    def probe(self, paths: Iterable[Path | str] = ()) -> list[str]:
        """Why this user cannot be used ([] = it can): sudo refused, the metadata service
        answers it, a path it must read or run is out of its reach, or it can read the
        person's data (``private_paths``)."""
        try:
            proc = self._helper("probe", *[str(p) for p in paths], "--private",
                                *[str(p) for p in private_paths()])
        except subprocess.TimeoutExpired:
            return [f"sudo -u {self.name} did not answer in {_HELPER_TIMEOUT_S}s"]
        except OSError as exc:
            return [f"sudo could not start: {exc}"]
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-2:]
            return [f"sudo -n -u {self.name} failed (exit {proc.returncode}): "
                    + " | ".join(tail)[:300]]
        if out.get("uid") != self.uid:
            return [f"the probe ran as uid {out.get('uid')}, not {self.name} ({self.uid})"]
        return list(out.get("problems") or ([] if proc.returncode == 0 else
                                             [f"probe exit {proc.returncode}"]))

    def grant(self, folder: Path) -> None:
        """Give this user rwX on *folder* and everything in it, and on what is made in it
        later (default ACL), keeping the person's own access to what this user makes."""
        me = os.getuid()
        spec = f"u:{self.uid}:rwX,d:u:{self.uid}:rwX,d:u:{me}:rwX"
        try:
            proc = subprocess.run(["setfacl", "-R", "-m", spec, str(folder)],
                                  capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unavailable(f"setfacl could not give {self.name} the sandbox: {exc}") from exc
        if proc.returncode != 0:
            raise Unavailable(f"setfacl could not give {self.name} the sandbox: "
                              f"{(proc.stderr or '').strip()[:300]}")


_current: ReplayUser | None = None
_lock_fd: int | None = None


def current() -> ReplayUser | None:
    """The replay user this process resolved (None when it runs as the person or has not
    resolved yet)."""
    return _current


def _find_sudo() -> str | None:
    return shutil.which("sudo", path=_SUDO_ENV["PATH"])


def network_rule_loaded() -> bool:
    """Whether the unit that loads the replay user's network rule is active (``systemctl
    is-active``, readable without root). A fixed fact checked before any probe, so a machine
    whose credentials come some other way than the two metadata addresses the probe tries
    is refused too: no unit, no systemd, no answer all count as not loaded."""
    systemctl = shutil.which("systemctl", path=_SUDO_ENV["PATH"])
    if systemctl is None:
        return False
    try:
        proc = subprocess.run([systemctl, "is-active", "--quiet", NETWORK_RULE_UNIT], capture_output=True,
                              timeout=_HELPER_TIMEOUT_S, env=_SUDO_ENV, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _lock_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "feature_map" / "replay-user.lock"


def _take_lock(wait_s: float) -> None:
    """Hold the machine's replay-user lock until this process ends (one replay at a time
    uses the replay user). Raises ``Unavailable`` when it is not free within *wait_s*."""
    global _lock_fd
    if _lock_fd is not None:
        return
    import fcntl
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fd = fd
            return
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise Unavailable(f"another replay has used {REPLAY_USER} for over "
                                  f"{int(wait_s)}s (lock {path})") from None
            time.sleep(1)


def resolve(paths: Iterable[Path | str] = (), *, lock_wait_s: float = LOCK_WAIT_S) -> ReplayUser | None:
    """Who the sandbox runs as: None when this machine does not need the replay user (the
    person, as before); else the replay user, once this process holds the machine's
    replay-user lock, no process of that user is left, and a probe as that user found the
    metadata service closed, *paths* within reach and the person's data out of it (the
    shared location published first). Raises ``Unavailable`` when the
    replay user is needed and cannot be used. Only a usable replay user is remembered."""
    global _current
    user = _current
    if user is None:
        if not needs_replay_user():
            return None
        if not sys.platform.startswith("linux"):
            raise Unavailable("this machine has an instance metadata service, and replay keeps "
                              "it from the replayed app only on Linux (a replay user the "
                              "firewall keeps off it)")
        try:
            pw = pwd.getpwnam(REPLAY_USER)
        except KeyError:
            raise Unavailable(
                f"this machine needs a replay user and has no {REPLAY_USER}: replay runs the "
                f"repo's code only as a user the firewall keeps off the instance metadata "
                f"service (on a Hive the next update sets it up, hive/replay-host.sh)") from None
        sudo = _find_sudo()
        if sudo is None:
            raise Unavailable(f"no sudo to start the replay as {REPLAY_USER}")
        if not network_rule_loaded():
            raise Unavailable(NO_NETWORK_RULE)
        user = ReplayUser(REPLAY_USER, pw.pw_uid, Path(pw.pw_dir), sudo, sys.executable)
        _take_lock(lock_wait_s)
    # every replay of this process, also the second (a base and a head replay): none of that
    # user's processes may be left from an earlier one
    left = user.kill_all()
    if left is None:
        raise Unavailable(f"sudo -n -u {REPLAY_USER} could not stop that user's processes")
    if left:
        raise Unavailable(f"{left} process(es) of {REPLAY_USER} outlived an earlier replay "
                          "and could not be stopped")
    shared = shared_dir()
    if shared is not None:
        publish(shared)                 # what the Hive user wrote there, readable for this user
    problems = user.probe(paths)
    if problems:
        if any("only the person may read" in p for p in problems):
            problems = [NO_PRIVATE_DATA, *problems]
        if any("metadata service answers" in p for p in problems):
            # the user is there but the boundary is not: say which rule, and who loads it
            problems = [NO_NETWORK_RULE, *problems]
        raise Unavailable(f"replay user {REPLAY_USER}: " + "; ".join(problems))
    _current = user
    return user


def remove_tree(path: Path | str) -> None:
    """Remove *path*: first as the replay user (the files it wrote), then as the person."""
    user = current()
    if user is not None and Path(path).exists():
        user.remove(Path(path))
    shutil.rmtree(path, ignore_errors=True)
