"""mutation_check — does each fix's test go red when the fix is taken out?

A fix's test counts only when reverting the fix turns it red (AGENT R28). This runs
that check for any language and any test runner: it only edits text and runs a
command.

    "$SWARM_BACKEND_BIN" py -m scripts.mutation_check <spec.json> [--timeout S] [--cwd DIR] -- <test command ...>
    "$SWARM_BACKEND_BIN" py -m scripts.mutation_check --restore <file ...>

``spec.json`` is a list of mutations, each ``{"file": path, "name": label, "old": text,
"new": text}``: ``old`` must occur exactly once in ``file`` (the fix, or the line that
carries it), ``new`` is what it becomes without the fix. Paths are relative to ``--cwd``
(default: the current directory), which is also where the command runs.

For each mutation, in order:
  1. ``old`` is looked up; a count other than 1 is reported (``anchor``), nothing changes.
  2. the file's bytes are copied to ``<file>.mutation-backup``, then the mutation is written.
  3. the command runs (its own process group, wall-clock cap ``--timeout``, default 300s).
     Exit code 0 = MISSED (the tests stay green without the fix); anything else = CAUGHT;
     a run killed at the cap is CAUGHT with ``timeout: true`` (a hang is a failure, but
     read its log before trusting it).
  Each write moves the file's mtime strictly forward, so a cache keyed on mtime + size
  (``.pyc``, build tools) never runs the previous version of the file.
  4. the original bytes are written back and the backup removed — also on an exception,
     Ctrl-C, SIGTERM, SIGALRM (a wall-clock cap such as ``perl -e 'alarm N; exec …'``)
     or SIGHUP. Only SIGKILL can leave a mutated file; its backup stays on disk,
     and a later run refuses that file until ``--restore`` puts the original back.

Before any mutation the command runs once unchanged; if that run is not green, nothing is
mutated (every "caught" would be meaningless). ``--no-baseline`` skips it.

Each run's output goes to its own log in a fresh temp directory (``log_dir`` in the
result), never a shared path. Output is one JSON object on stdout; exit 0 when every
mutation is caught, 1 when one is missed, has a bad anchor, the baseline is red or a
backup is left over, 2 on bad use.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKUP_SUFFIX = ".mutation-backup"


class _Terminated(Exception):
    """SIGTERM / SIGALRM / SIGHUP arrived: unwind so every ``finally`` restores its file."""


def _on_term(_signum, _frame):  # pragma: no cover - exercised through a subprocess test
    raise _Terminated()


def _write_newer(path: Path, data: bytes) -> None:
    """Write ``data`` and move the mtime strictly forward (at least 2s past the
    file's previous mtime). Caches keyed on mtime + size -- Python's ``.pyc``, many
    build tools -- otherwise keep the old compiled code when a mutation keeps the
    size (``a + b`` -> ``a - b``) and lands within the same second, and the run
    tests the unmutated code: a false "missed"."""
    before = path.stat().st_mtime
    path.write_bytes(data)
    stamp = max(time.time(), before + 2)
    os.utime(path, (stamp, stamp))


def _backup_of(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def _run(cmd: list[str], cwd: Path, timeout: float, log: Path) -> tuple[int, bool, float]:
    """Run ``cmd`` in its own process group; returns (exit code, timed out, seconds)."""
    start = time.monotonic()
    with log.open("wb") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
            return rc, False, time.monotonic() - start
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return -9, True, time.monotonic() - start
        except BaseException:
            _kill_group(proc)
            raise


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def restore(files: list[Path]) -> dict:
    restored, absent = [], []
    for f in files:
        b = _backup_of(f)
        if b.exists():
            _write_newer(f, b.read_bytes())
            b.unlink()
            restored.append(str(f))
        else:
            absent.append(str(f))
    return {"ok": True, "restored": restored, "no_backup": absent}


def _load_spec(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("spec must be a non-empty JSON list")
    for i, m in enumerate(data):
        if not isinstance(m, dict) or not all(isinstance(m.get(k), str) for k in ("file", "old", "new")):
            raise ValueError(f"mutation {i}: needs string fields file, old, new")
        if m["old"] == m["new"]:
            raise ValueError(f"mutation {i}: old and new are the same")
    return data


def check(spec: list[dict], cmd: list[str], cwd: Path, timeout: float, baseline: bool) -> dict:
    log_dir = Path(tempfile.mkdtemp(prefix="mutation-check-"))
    files = sorted({(cwd / m["file"]).resolve() for m in spec})
    leftover = [str(f) for f in files if _backup_of(f).exists()]
    if leftover:
        return {"ok": False, "error": "backup left by an earlier run: restore first (--restore)",
                "files": leftover, "log_dir": str(log_dir)}
    result: dict = {"ok": True, "log_dir": str(log_dir), "mutations": []}
    if baseline:
        rc, timed_out, secs = _run(cmd, cwd, timeout, log_dir / "baseline.log")
        result["baseline"] = {"rc": rc, "timeout": timed_out, "seconds": round(secs, 1)}
        if rc != 0:
            result["ok"] = False
            result["error"] = "baseline is not green: nothing was mutated"
            return result
    for i, m in enumerate(spec):
        name = m.get("name") or f"mutation {i}"
        path = (cwd / m["file"]).resolve()
        entry: dict = {"name": name, "file": m["file"]}
        try:
            original = path.read_bytes()
        except OSError as exc:
            entry.update(status="anchor", error=f"cannot read: {exc.__class__.__name__}")
            result["mutations"].append(entry)
            continue
        text = original.decode("utf-8")
        count = text.count(m["old"])
        if count != 1:
            entry.update(status="anchor", anchor_count=count)
            result["mutations"].append(entry)
            continue
        backup = _backup_of(path)
        backup.write_bytes(original)
        try:
            _write_newer(path, text.replace(m["old"], m["new"], 1).encode("utf-8"))
            rc, timed_out, secs = _run(cmd, cwd, timeout, log_dir / f"{i:02d}.log")
        finally:
            _write_newer(path, original)
            backup.unlink()
        entry.update(status="caught" if rc != 0 else "missed", rc=rc, timeout=timed_out,
                     seconds=round(secs, 1), log=str(log_dir / f"{i:02d}.log"))
        result["mutations"].append(entry)
    statuses = [e["status"] for e in result["mutations"]]
    result["caught"] = statuses.count("caught")
    result["missed"] = statuses.count("missed")
    result["anchor"] = statuses.count("anchor")
    result["ok"] = result["missed"] == 0 and result["anchor"] == 0
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, cmd = argv[:at], argv[at + 1:]
    p = argparse.ArgumentParser(prog="mutation_check", description=__doc__.splitlines()[0])
    p.add_argument("spec", nargs="?")
    p.add_argument("--restore", nargs="+", metavar="FILE")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--cwd", default=".")
    p.add_argument("--no-baseline", action="store_true")
    a = p.parse_args(argv)
    cwd = Path(a.cwd).resolve()
    if a.restore:
        print(json.dumps(restore([(cwd / f).resolve() for f in a.restore])))
        return 0
    if not a.spec or not cmd:
        print(json.dumps({"ok": False, "error": "usage: mutation_check <spec.json> -- <test command>"}))
        return 2
    try:
        spec = _load_spec(Path(a.spec))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"bad spec: {exc}"}))
        return 2
    # SIGALRM: the documented test cap (``perl -e 'alarm N; exec …'``) delivers it to
    # this process; its default action ends it before any ``finally`` runs
    for name in ("SIGTERM", "SIGALRM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _on_term)
    try:
        out = check(spec, cmd, cwd, a.timeout, baseline=not a.no_baseline)
    except _Terminated:
        print(json.dumps({"ok": False, "error": "terminated; every file was restored"}))
        return 1
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
