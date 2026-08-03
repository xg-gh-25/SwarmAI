"""Daemon lifecycle guard — C034 defense-in-depth.

This module holds the *testable core* of the daemon-lifecycle robustness
system. Shell scripts (``scripts/daemon-lib.sh``) and the guardian launchd
agent (``swarmai_guardian.sh``) orchestrate launchctl; the judgment logic
that decides *whether* an action is safe lives here so it can be unit-tested.

Three responsibilities (see design run_b5592983):

1. **Prevention** — ``is_daemon_descendant()`` detects when a lifecycle
   operation is being invoked from inside a process tree rooted at the
   daemon (the C034 root cause: sending ``launchctl kill SIGTERM`` from a
   child session leaves the daemon in HTTP-dead-but-not-exited limbo).
   ``reexec_detached()`` relaunches the operation in a new session group
   (``start_new_session=True``) so it survives the daemon's death. It is
   fire-and-forget: a daemon-killing op reaps its own caller, so no result can
   be relayed back — the outcome is observed out-of-band (health check,
   guardian, ``scan_deployed_no_restart`` at startup). The ``/api/system/upgrade``
   endpoint keeps its own result file because that op does NOT kill its caller.

2. **Recovery** — ``guardian_decision()`` is the pure function the guardian
   agent calls every poll. It re-bootstraps the daemon ONLY when it is
   deregistered AND no intent-sentinel is present AND the port has been dead
   for N consecutive probes. The sentinel (written by ``stop`` and the
   upgrade endpoint before bootout) is what prevents the guardian from
   racing an intentional shutdown or corrupting an in-flight rsync
   (COE 2026-05-01 PYZ/zlib corruption).

3. **Observability** — ``scan_deployed_no_restart()`` reads the upgrade
   result files the upgrader writes; a ``deployed_no_restart`` status means a
   binary was deployed but the daemon never came back up. Surfaced at startup.

Public CLI (``python -m core.daemon_guard <cmd>``) lets the shell layer reuse
this logic without reimplementing it in bash:

    ancestry-check        exit 0 if current process is a daemon descendant
    write-sentinel        write the intent sentinel before a bootout
    clear-sentinel        remove the intent sentinel after a healthy start
    scan-deploy           print JSON of any deployed_no_restart result files
    guardian-decision     print SHOULD_BOOTSTRAP / SKIP for the guardian loop

Mechanism declarations (Step 1.7):
- ppid ancestry: ``os.getppid`` chain reaches the daemon pid even though SDK
  subprocesses use ``start_new_session=True`` (new pgid, preserved ppid).
  Verified live: chain 54489→54487→50035→49520(daemon)→1.
- detached re-exec: ``Popen(start_new_session=True)`` survives daemon death;
  same pattern as the production upgrade endpoint.
- sentinel staleness: JSON ``written_at`` vs ``time.time()`` — plain stdlib.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────

DAEMON_LABEL = "com.swarmai.backend"
DAEMON_PORT = 18321

#: Intent sentinel — its presence tells the guardian "this down-state is
#: intentional (stop/upgrade), do not resurrect the daemon".
SENTINEL_PATH = Path.home() / ".swarm-ai" / ".daemon-intentional-down"

#: backend.json records the running daemon's pid (written by the daemon itself).
BACKEND_JSON_PATH = Path.home() / ".swarm-ai" / "backend.json"

#: Sentinel older than this (and port still dead) is treated as stale — a
#: crash *during* an upgrade must not suppress recovery forever. Bounds the
#: worst-case outage when an upgrader crashes mid-rsync. A legitimate upgrade
#: completes in well under a minute on local disk, so 5 minutes is a generous
#: ceiling — far better than a longer window that would let a crashed-upgrade
#: sentinel suppress recovery and regress past the C034 7-minute outage.
#: Note: ``permanent`` sentinels (from ``stop``) are never stale (see below).
SENTINEL_STALE_SECONDS = 5 * 60  # 5 minutes

#: Cap the ppid walk so a (pathological) pid cycle can't loop forever.
MAX_ANCESTRY_HOPS = 12

#: Upgrade result files written by the /api/system/upgrade detached upgrader.
UPGRADE_RESULT_GLOB = "swarm-upgrade-*.json"

#: A deployed_no_restart result older than this is no longer actionable noise.
DEPLOY_RESULT_FRESH_SECONDS = 24 * 60 * 60  # 24 hours

# ── Daemon pid discovery ─────────────────────────────────────────────────────


def _read_backend_json_pid(backend_json: Path = BACKEND_JSON_PATH) -> Optional[int]:
    """Return the daemon pid recorded in backend.json, or None."""
    try:
        data = json.loads(backend_json.read_text())
        pid = data.get("pid")
        return int(pid) if pid is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _launchctl_pid(label: str = DAEMON_LABEL) -> Optional[int]:
    """Return the pid launchd reports for the daemon service, or None.

    Parses ``launchctl print gui/<uid>/<label>`` for the ``pid = N`` line.
    This is the authoritative source — if backend.json disagrees (stale after
    a recycle), trust launchd.
    """
    try:
        uid = os.getuid()
        out = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("pid ="):
                return int(stripped.split("=", 1)[1].strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def read_daemon_pid() -> Optional[int]:
    """Resolve the daemon pid, trusting launchd over backend.json on mismatch.

    backend.json can go stale (pid recycled to an unrelated process after a
    crash). launchctl is the source of truth for "what pid is the service
    actually running as". We prefer launchctl; fall back to backend.json only
    when launchd has no answer (e.g. service deregistered but we still want a
    best-effort pid for ancestry).
    """
    lc = _launchctl_pid()
    if lc is not None:
        return lc
    return _read_backend_json_pid()


# ── Prevention: ancestry walk ────────────────────────────────────────────────


def _parent_pid(pid: int) -> Optional[int]:
    """Return the parent pid of ``pid`` via ``ps -o ppid=``, or None."""
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        text = out.stdout.strip()
        if not text:
            return None
        return int(text)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def build_ancestry(start_pid: Optional[int] = None,
                   max_hops: int = MAX_ANCESTRY_HOPS) -> list[int]:
    """Return the ppid chain from ``start_pid`` up toward init (pid 1).

    Includes ``start_pid`` itself as the first element. Stops at pid <= 1, at
    ``max_hops``, or when the parent can't be resolved.
    """
    if start_pid is None:
        start_pid = os.getpid()
    chain: list[int] = []
    pid = start_pid
    for _ in range(max_hops):
        chain.append(pid)
        if pid <= 1:
            break
        parent = _parent_pid(pid)
        if parent is None or parent in chain:  # unresolved or cycle
            break
        pid = parent
    return chain


def is_daemon_descendant(daemon_pid: Optional[int] = None,
                         start_pid: Optional[int] = None) -> bool:
    """True if the current (or ``start_pid``) process should be treated as a
    daemon descendant for the C034 prevention guard.

    FAIL-CLOSED: when the daemon pid cannot be resolved we return ``True`` —
    i.e. "treat as descendant, re-exec detached". Rationale: the pid is
    unresolvable exactly when launchctl is flaky / backend.json is missing —
    the messy partial-failure states where C034 is MOST likely (an agent
    subprocess running ``daemon stop`` while launchctl momentarily errors,
    then bootout reaps its own host). Fail-open would silently disable the
    guard in the highest-risk moment. The detached re-exec is harmless even
    on a false positive (the op just runs in a clean session), so there is no
    downside to defaulting to the safe path.

    The daemon pid is resolved via :func:`read_daemon_pid` unless explicitly
    supplied (tests inject it).
    """
    if daemon_pid is None:
        daemon_pid = read_daemon_pid()
    if daemon_pid is None:
        return True  # fail-closed: re-exec detached when we can't prove safety
    return daemon_pid in build_ancestry(start_pid=start_pid)


# ── Prevention: detached re-exec (fire-and-forget) ───────────────────────────


def reexec_detached(argv: list[str], op: str) -> None:
    """Re-launch ``argv`` in a NEW session group, fire-and-forget.

    This is the C034 prevention primitive. The whole reason we detach is that
    the operation (``stop``/``restart``) is about to KILL the daemon — and the
    original caller is a child of that daemon, so it will be reaped before the
    operation completes. ``start_new_session=True`` puts the re-execed command
    in its own session group so it survives the daemon's death.

    **No result relay.** A relay back to the original session is physically
    impossible for daemon-killing ops: the caller is dead before the op
    finishes — there is nobody left to read a /tmp file. The outcome of these
    ops is observed out-of-band instead:
      - ``stop``    → next health check / guardian sees the daemon down
      - ``restart`` → the next request lands on the fresh daemon (new version)
      - partial deploy → ``scan_deployed_no_restart`` surfaces it at startup

    The detached child gets ``_SWARM_LIFECYCLE_DETACHED=1`` so its own guard
    short-circuits (no recursion). The ``/api/system/upgrade`` endpoint keeps
    its own result file — but that op does NOT kill its caller, so the relay
    there has a live reader. The two relay stories differ because the lifetimes
    differ.
    """
    env = {
        **os.environ,
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        # Mark the detached child so its own guard short-circuits (no recursion).
        "_SWARM_LIFECYCLE_DETACHED": "1",
    }
    subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


# ── Sentinel: intent signal for the guardian ─────────────────────────────────


def write_sentinel(reason: str, written_by: str,
                   sentinel_path: Path = SENTINEL_PATH,
                   permanent: bool = False) -> None:
    """Write the intent sentinel BEFORE a bootout (stop/upgrade).

    Its presence suppresses the guardian.

    ``permanent=True`` (used by ``stop``): the daemon should stay down until an
    explicit ``start`` — the stale-guard must NOT resurrect it. Without this, a
    user who runs ``stop`` would see the daemon mysteriously restart 5 minutes
    later (the guardian treating the stop-sentinel as stale).

    ``permanent=False`` (used by ``upgrade``): carries a timestamp so the
    guardian's stale-guard can recover if the upgrader crashed mid-flight and
    left the sentinel behind (bounded outage).
    """
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": reason,
        "written_by": written_by,
        "written_at": time.time(),
        "written_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "permanent": permanent,
    }
    sentinel_path.write_text(json.dumps(payload, indent=2))


def read_sentinel(sentinel_path: Path = SENTINEL_PATH) -> Optional[dict]:
    """Return the sentinel payload, or None if absent/unreadable."""
    try:
        return json.loads(sentinel_path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def clear_sentinel(sentinel_path: Path = SENTINEL_PATH) -> None:
    """Remove the intent sentinel (after a confirmed-healthy start)."""
    try:
        sentinel_path.unlink()
    except FileNotFoundError:
        pass


def sentinel_is_stale(sentinel: dict, now: Optional[float] = None,
                      stale_seconds: int = SENTINEL_STALE_SECONDS) -> bool:
    """True if the sentinel is old enough that the guardian should recover.

    - ``permanent`` sentinels (written by ``stop``) are NEVER stale — the
      daemon stays down until an explicit ``start``. Returning stale here would
      make the guardian resurrect a deliberately-stopped daemon.
    - A sentinel without a parseable ``written_at`` is treated as stale (we
      cannot trust an intent signal we can't age).
    - A FUTURE-dated ``written_at`` (clock moved backward, or a bogus value)
      is treated as stale: we won't let an unageable signal suppress recovery
      of a down daemon indefinitely.
    """
    if sentinel.get("permanent"):
        return False
    if now is None:
        now = time.time()
    written_at = sentinel.get("written_at")
    try:
        age = now - float(written_at)
    except (TypeError, ValueError):
        return True
    if age < -60:  # dated >60s in the future → untrustworthy → stale
        return True
    return age > stale_seconds


# ── Recovery: guardian decision (pure function) ──────────────────────────────

SHOULD_BOOTSTRAP = "SHOULD_BOOTSTRAP"
SKIP = "SKIP"


def guardian_decision(*, registered: bool, port_alive: bool,
                      dead_probe_count: int, required_dead_probes: int,
                      sentinel: Optional[dict], now: Optional[float] = None) -> dict:
    """Decide whether the guardian should re-bootstrap the daemon.

    Returns a dict: ``{"action": SHOULD_BOOTSTRAP|SKIP, "reason": str,
    "clear_sentinel": bool}``.

    Decision table (the daemon is healthy if registered AND port_alive):
    - registered OR port_alive            → SKIP (daemon is fine / restarting)
    - not enough consecutive dead probes  → SKIP (avoid racing a normal restart)
    - fresh sentinel present              → SKIP (intentional stop/upgrade)
    - stale sentinel present              → SHOULD_BOOTSTRAP + clear_sentinel
    - no sentinel                         → SHOULD_BOOTSTRAP
    """
    if registered or port_alive:
        return {"action": SKIP, "reason": "daemon registered or port alive",
                "clear_sentinel": False}

    if dead_probe_count < required_dead_probes:
        return {"action": SKIP,
                "reason": f"only {dead_probe_count}/{required_dead_probes} dead probes "
                          "(may be a normal restart in flight)",
                "clear_sentinel": False}

    if sentinel is not None:
        if sentinel_is_stale(sentinel, now=now):
            return {"action": SHOULD_BOOTSTRAP,
                    "reason": "sentinel present but stale — recovering",
                    "clear_sentinel": True}
        return {"action": SKIP,
                "reason": f"intentional down (sentinel by {sentinel.get('written_by')})",
                "clear_sentinel": False}

    return {"action": SHOULD_BOOTSTRAP,
            "reason": "deregistered, no sentinel, port dead — accidental down",
            "clear_sentinel": False}


# ── Observability: deployed_no_restart scan ──────────────────────────────────


def scan_deployed_no_restart(result_dir: Path = Path("/tmp"),
                             now: Optional[float] = None,
                             fresh_seconds: int = DEPLOY_RESULT_FRESH_SECONDS) -> list[dict]:
    """Return recent upgrade result files whose status is deployed_no_restart.

    A ``deployed_no_restart`` means the upgrader rsynced a new binary but the
    daemon never came back (bootstrap failed). The guardian usually self-heals
    this, so this is observability-only — surfaced at startup as a WARNING.
    Stale results (older than ``fresh_seconds``) are ignored.
    """
    if now is None:
        now = time.time()
    findings: list[dict] = []
    try:
        candidates = sorted(result_dir.glob(UPGRADE_RESULT_GLOB))
    except OSError:
        return findings
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if data.get("status") != "deployed_no_restart":
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if (now - mtime) > fresh_seconds:
            # Stale litter — the upgrader never deletes its result files, so
            # without this they accumulate forever in /tmp. Best-effort unlink
            # of anything past the freshness window (it's no longer actionable).
            try:
                path.unlink()
            except OSError:
                pass
            continue
        findings.append({"file": str(path), **data})
    return findings


# ── CLI dispatch ─────────────────────────────────────────────────────────────


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m core.daemon_guard "
              "<ancestry-check|write-sentinel|clear-sentinel|scan-deploy|guardian-decision>")
        return 2

    cmd = argv[0]

    if cmd == "ancestry-check":
        # exit 0 = current process IS a daemon descendant (caller should re-exec)
        return 0 if is_daemon_descendant() else 1

    if cmd == "reexec-if-descendant":
        # The production prevention path. If the current process is a daemon
        # descendant, re-exec the given argv detached (new session group) so it
        # survives the daemon's death, and return 0 (caller should STOP — the
        # detached copy owns the operation now). If not a descendant, return 1
        # (caller proceeds inline). argv[1:] is the command to re-exec. The
        # detached child gets _SWARM_LIFECYCLE_DETACHED=1 so it won't recurse.
        if os.environ.get("_SWARM_LIFECYCLE_DETACHED"):
            return 1  # already detached → proceed inline
        if not is_daemon_descendant():
            return 1  # not a descendant → proceed inline
        child_argv = argv[1:]
        if not child_argv:
            return 1
        reexec_detached(child_argv, op="lifecycle")  # fire-and-forget
        return 0

    if cmd == "write-sentinel":
        reason = argv[1] if len(argv) > 1 else "lifecycle"
        written_by = argv[2] if len(argv) > 2 else "shell"
        permanent = len(argv) > 3 and argv[3] == "permanent"
        write_sentinel(reason, written_by, permanent=permanent)
        return 0

    if cmd == "clear-sentinel":
        clear_sentinel()
        return 0

    if cmd == "scan-deploy":
        print(json.dumps(scan_deployed_no_restart(), indent=2))
        return 0

    if cmd == "guardian-decision":
        # Gather live signals for the guardian loop.
        registered = _launchctl_pid() is not None
        port_alive = _port_alive(DAEMON_PORT)
        # dead_probe_count / required come from the shell loop via argv.
        dead = int(argv[1]) if len(argv) > 1 else (0 if port_alive else 1)
        required = int(argv[2]) if len(argv) > 2 else 3
        decision = guardian_decision(
            registered=registered, port_alive=port_alive,
            dead_probe_count=dead, required_dead_probes=required,
            sentinel=read_sentinel(),
        )
        print(json.dumps(decision))
        # exit 0 = should bootstrap (shell-friendly)
        return 0 if decision["action"] == SHOULD_BOOTSTRAP else 1

    print(f"unknown command: {cmd}")
    return 2


def _port_alive(port: int) -> bool:
    """True if something is listening on the local port."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except (ConnectionRefusedError, OSError):
        return False
    finally:
        s.close()


if __name__ == "__main__":
    import sys
    # This module is pure-stdlib by design, so it can be copied to
    # ~/.swarm-ai/guardian/daemon_guard.py and run as a standalone script by the
    # installed guardian — no repo checkout, no PYTHONPATH, no frozen-bundle
    # dependency. Both `python -m core.daemon_guard <cmd>` (dev) and
    # `python /path/to/daemon_guard.py <cmd>` (production guardian) work.
    raise SystemExit(_main(sys.argv[1:]))
