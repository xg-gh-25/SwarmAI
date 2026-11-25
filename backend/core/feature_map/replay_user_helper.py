"""Run as the replay user, through sudo, by core.feature_map.replay_user: the few things the
Hive user cannot do for processes and files that user owns. Standard library only, run with
``python -I`` (no import from the backend: the replay user may not be able to load it).

    replay_user_helper.py exec <envfile> <cwd> <program> [arg ...]
        Read the environment from <envfile> (JSON, deleted once read), change to <cwd> and
        exec <program> with exactly that environment. Exit 127 when <program> is missing,
        126 when it cannot be run.
    replay_user_helper.py kill <tag> [--prefix]
        Stop every process of this user whose environment holds FEATURE_MAP_SPAWN=<tag>
        (or a value starting with <tag> with --prefix): SIGTERM, up to 3 s, then SIGKILL
        (found again, so a child forked in between goes too).
    replay_user_helper.py kill --all <caller uid>
        Stop every process of this user: SIGTERM, up to 3 s, then kill(-1, SIGKILL) until
        none is left (a fork loop cannot outrun it); print {"left": <how many are still
        there>}. Refused (exit 2) as root or when this user is the caller: it never stops
        the caller's own processes.
    replay_user_helper.py rm <path>
        Remove <path> as far as this user may: its own folders made writable first.
    replay_user_helper.py mkdir <path>
        Make the folder <path> (its parent must exist; exit 1 when it cannot be made).
    replay_user_helper.py copy <src> <dest>
        Copy the file or folder <src> to <dest>, links as links, a folder merged into an
        existing one (exit 1 with the reason on stderr when it fails).
    replay_user_helper.py probe [<path> ...] [--private <path> ...]
        Exit 0 when a TCP connection to the instance metadata service (169.254.169.254:80,
        [fd00:ec2::254]:80) fails, every <path> is readable (and runnable when it has an
        execute bit) and no private <path> (the person's data: a file opened, a folder
        listed, or passed through) can be reached; else exit 3, the reasons on stdout as
        JSON.
"""
import json
import os
import shutil
import signal
import socket
import stat
import sys
import time

TAG = "FEATURE_MAP_SPAWN"
METADATA = (("169.254.169.254", 80), ("fd00:ec2::254", 80))


def _exec(envfile, cwd, argv):
    with open(envfile, encoding="utf-8") as fh:
        env = json.load(fh)
    try:
        os.unlink(envfile)
    except OSError:
        pass
    try:
        os.chdir(cwd)
        os.execve(argv[0], argv, env)
    except FileNotFoundError as exc:
        sys.stderr.write("feature-map: cannot start %s: %s\n" % (argv[0], exc))
        sys.exit(127)
    except OSError as exc:
        sys.stderr.write("feature-map: cannot start %s as %s: %s\n"
                         % (argv[0], os.getuid(), exc))
        sys.exit(126)


def _real_uid(pid):
    """The real uid of *pid* from /proc/<pid>/status (None when it is gone). Not the owner of
    /proc/<pid>: a process that made itself non-dumpable has that entry owned by root."""
    try:
        with open("/proc/%d/status" % pid, "rb") as fh:
            for line in fh:
                if line.startswith(b"Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _mine():
    """The pids of this user's processes (by real uid), but this one."""
    me, mine, out = os.getpid(), os.getuid(), []
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) != me and _real_uid(int(name)) == mine:
            out.append(int(name))
    return out


def _tagged(value, prefix):
    key = (TAG + "=").encode()
    out = []
    for pid in _mine():
        try:
            with open("/proc/%d/environ" % pid, "rb") as fh:
                items = fh.read().split(b"\0")
        except OSError:
            continue
        for item in items:
            if item.startswith(key):
                got = item[len(key):].decode("utf-8", "replace")
                if got == value or (prefix and got.startswith(value)):
                    out.append(pid)
                break
    return out


def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            return fh.read().rsplit(b")", 1)[-1].split()[0] not in (b"Z", b"X")
    except (OSError, IndexError):
        return False


def _stop(find):
    """SIGTERM what *find* returns, up to 3 s, then SIGKILL what it returns then."""
    pids = find()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    for pid in find():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _kill(value, prefix):
    if value:
        _stop(lambda: _tagged(value, prefix))


def _kill_all(caller_uid):
    if caller_uid == os.getuid() or os.getuid() == 0 or os.geteuid() == 0:
        sys.stderr.write("feature-map: kill --all as root or as the caller's own user is refused\n")
        sys.exit(2)
    _stop(_mine)
    # kill(-1) from an unprivileged user signals every process of that user but this one in
    # one pass, so a fork loop cannot outrun it (listing pids, then signalling them, can be)
    # (sent once whatever the listing says, then while one is still seen)
    deadline = time.monotonic() + 3
    while True:
        try:
            os.kill(-1, signal.SIGKILL)
        except OSError:
            pass
        if time.monotonic() >= deadline or not any(_alive(p) for p in _mine()):
            break
        time.sleep(0.1)
    print(json.dumps({"left": sum(1 for p in _mine() if _alive(p))}))


def _rm(path):
    mine = os.getuid()
    for root, dirs, _files in os.walk(path):
        for d in [root] + [os.path.join(root, d) for d in dirs]:
            try:
                st = os.lstat(d)
                if st.st_uid == mine and stat.S_ISDIR(st.st_mode):
                    os.chmod(d, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError as exc:
        sys.stderr.write("feature-map: mkdir %s: %s\n" % (path, exc))
        sys.exit(1)


def _copy(src, dest):
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
        else:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.copy2(src, dest, follow_symlinks=False)
    except (OSError, shutil.Error) as exc:
        sys.stderr.write("feature-map: copy %s to %s: %s\n" % (src, dest, exc))
        sys.exit(1)


def _private_open(path):
    """Whether this user can reach *path*: open it for reading (a folder: list it), or pass
    through it when it is a folder. Never blocks on a FIFO; anything refused or missing
    counts as private."""
    try:
        if os.path.isdir(path) and os.access(path, os.X_OK):
            return True
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    os.close(fd)
    return True


def _probe(paths, private=()):
    problems = []
    for host, port in METADATA:
        try:
            socket.create_connection((host, port), timeout=2).close()
            problems.append("the instance metadata service answers this user at %s:%d" % (host, port))
        except OSError:
            pass
    for path in paths:
        try:
            st = os.stat(path)
        except OSError as exc:
            problems.append("cannot reach %s: %s" % (path, exc.strerror))
            continue
        if not os.access(path, os.R_OK):
            problems.append("cannot read %s" % path)
        elif stat.S_ISREG(st.st_mode) and st.st_mode & stat.S_IXUSR and not os.access(path, os.X_OK):
            problems.append("cannot run %s" % path)
    for path in private:
        if _private_open(path):
            problems.append("this user can read %s, which only the person may read" % path)
    print(json.dumps({"uid": os.getuid(), "problems": problems}))
    sys.exit(3 if problems else 0)


def main(argv):
    if len(argv) >= 4 and argv[0] == "exec":
        _exec(argv[1], argv[2], argv[3:])
    elif len(argv) == 3 and argv[:2] == ["kill", "--all"] and argv[2].isdigit():
        _kill_all(int(argv[2]))
    elif len(argv) in (2, 3) and argv[0] == "kill" and argv[1] != "--all":
        _kill(argv[1], argv[2:] == ["--prefix"])
    elif len(argv) == 2 and argv[0] == "rm":
        _rm(argv[1])
    elif len(argv) == 2 and argv[0] == "mkdir":
        _mkdir(argv[1])
    elif len(argv) == 3 and argv[0] == "copy":
        _copy(argv[1], argv[2])
    elif argv and argv[0] == "probe":
        rest = argv[1:]
        cut = rest.index("--private") if "--private" in rest else len(rest)
        _probe(rest[:cut], rest[cut + 1:])
    else:
        sys.stderr.write(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
