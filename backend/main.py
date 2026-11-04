"""FastAPI application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from config import settings, get_app_data_dir, get_log_file_path, get_swarmws
from core import session_registry
from core import hive_boot  # imported first: its STARTED_AT is this process's start
from utils.bundle_paths import get_resource_file
from routers import agents_router, skills_router, experts_router, mcp_router, chat_router, chat_threads_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router, system_router, todos_router, search_router, workspace_config_router, workspace_api_router, projects_router, tscc_router, artifacts_router, escalations_router, attention_router, voice_router, hive_router, apps_router
from routers.ingestion import router as ingestion_router
from routers.aws_account import router as aws_account_router
from routers.ingestion_redact import router as ingestion_redact_router
from routers.cognition_health import router as cognition_health_router, level_router as cognition_level_router
from routers.autonomous_jobs import router as autonomous_jobs_router
from routers.pipelines import router as pipelines_router
from routers.pollinate import router as pollinate_router
from routers.jobs import router as jobs_router
from channels.gateway import channel_gateway
from middleware.error_handler import setup_error_handlers
from middleware.hive_auth import HiveAuthMiddleware
from middleware.loopback_host import LoopbackHostMiddleware
from middleware.rate_limit import limiter
from database import initialize_database

# Runtime flag to track if lifespan startup has completed
# This is different from initialization_complete in DB which persists across restarts
_startup_complete = False


def _generate_permissions_json(workspace_path: Path, dangerous_patterns: list[str]) -> None:
    """Write read-only ``permissions.json`` for user visibility.

    Shows only the dangerous command patterns — all other tools are
    auto-approved via ``bypassPermissions``, so listing them adds no value.
    The file is regenerated at each startup; editing it has no effect.
    """
    import json as _json
    settings_dir = workspace_path / ".claude" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    content = {
        "description": (
            "Commands matching these glob patterns require user approval "
            "per session. All other commands are auto-approved. "
            "Add your own patterns in ~/.swarm-ai/dangerous_commands.json; "
            "the built-in patterns always apply."
        ),
        "dangerous_commands": dangerous_patterns,
    }
    (settings_dir / "permissions.json").write_text(
        _json.dumps(content, indent=2) + "\n", encoding="utf-8"
    )


# Startup timing instrumentation (populated by lifespan, read by system status endpoint).
# ``_startup_time_ms`` holds the total wall-clock time from lifespan entry to
# ``_startup_complete = True``.  ``_phase_timings`` holds per-phase durations
# keyed by phase name (e.g. ``"database_ms"``, ``"workspace_ms"``).
# Both are ``None`` until the lifespan completes its critical path.
_startup_time_ms: float | None = None
_phase_timings: dict[str, float] | None = None


# get_log_file_path() moved to config.py (leaf module, single source of truth) so
# job handlers can read the live log path without importing main.py's full app graph.


# Configure logging
log_level = logging.DEBUG if settings.debug else logging.INFO
# [pid] right after the timestamp: during a deploy the old daemon, the new one
# and (formerly) build probes all wrote here, and nothing told their lines apart.
# Keep asctime FIRST — log readers anchor on the leading timestamp.
log_format = "%(asctime)s [%(process)d] - %(name)s - %(levelname)s - %(message)s"

# In daemon mode the StreamHandler's output (stderr) is redirected by launchd
# to ``backend-stderr.log``, which has NO rotation and grows unbounded (observed
# at 132MB). The RotatingFileHandler below already keeps the full INFO stream
# capped at ~40MB, so the console only needs WARNING+ for crash diagnosis in
# daemon mode. Require the EXPLICIT ``SWARMAI_MODE=daemon`` that the launchd
# plist + wrapper set — never default to it here, or ``./dev.sh`` (which leaves
# SWARMAI_MODE unset) would also be silenced to WARNING on its live console.
console_level = (
    logging.WARNING if os.environ.get("SWARMAI_MODE") == "daemon" else log_level
)

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(console_level)
console_handler.setFormatter(logging.Formatter(log_format))

# File handler - write logs to file with rotation (10MB × 3 backups)
# Plain FileHandler grows unbounded; RotatingFileHandler caps at ~40MB total.
log_file = get_log_file_path()
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(log_level)
file_handler.setFormatter(logging.Formatter(log_format))

# Configure root logger. Records go through a queue: the writes (file + console,
# flush and the rotation check) run on one listener thread, never on the caller's.
# Measured 2026-10-21: logging flush / shouldRollover on the asyncio loop thread
# stalled it 4-5s at a time under disk load (the SSE heartbeat stops meanwhile).
# The format is applied ONLY by the real handlers on the listener thread; the
# record is prepared (message rendered, exc_info formatted) by QueueHandler at the
# call site. The QueueHandler itself must have NO formatter: QueueHandler.prepare()
# puts self.format(record) into record.msg, so a formatter there writes every
# prefix twice (logging.basicConfig gives one to any handler it is passed, even
# without format=; that was the 2026-10-21 regression).
import atexit as _atexit
import queue as _queue
from logging.handlers import QueueHandler as _QueueHandler, QueueListener as _QueueListener

_log_queue: "_queue.SimpleQueue" = _queue.SimpleQueue()
_queue_handler = _QueueHandler(_log_queue)
_queue_handler.setLevel(min(console_level, log_level))
_log_listener = _QueueListener(_log_queue, console_handler, file_handler, respect_handler_level=True)
_log_listener.start()
_atexit.register(_log_listener.stop)  # drains what is queued at exit


def _install_root_queue_handler(root: logging.Logger, handler: logging.Handler, level: int) -> None:
    """Route the root logger into the queue, as basicConfig would, but without
    giving the QueueHandler a formatter. Like basicConfig, a root that already has
    handlers (pytest's capture) is left alone."""
    if root.handlers:
        return
    root.setLevel(level)
    root.addHandler(handler)


_install_root_queue_handler(logging.getLogger(), _queue_handler, log_level)
logger = logging.getLogger(__name__)
logger.info(f"Log file: {log_file}")


class _NetworkErrorDemoteFilter(logging.Filter):
    """Slack's SDKs log every failed request during a network outage at ERROR,
    every ~11s all night (1,550 DNS errors since 10-14). An outage is expected:
    such records become WARNING, and the same message is let through at most
    once per ``window_s``."""

    _MARKERS = ("nodename nor servname", "Errno 8", "URLError", "Name or service not known",
                "Temporary failure in name resolution", "Network is unreachable")

    def __init__(self, window_s: float = 300.0) -> None:
        super().__init__()
        self._window_s = window_s
        self._last: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        msg = record.getMessage()
        if not any(m in msg for m in self._MARKERS):
            return True
        record.levelno, record.levelname = logging.WARNING, "WARNING"
        key = msg[:160]
        now = time.monotonic()
        if now - self._last.get(key, -1e18) < self._window_s:
            return False
        if len(self._last) > 256:
            self._last.clear()
        self._last[key] = now
        return True


# Third-party noise (counted 2026-10-21): httpx logs one INFO line per Bedrock
# request and watchfiles one per change batch — together with the gateway's
# per-request line about a third of the log.
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)
for _name in ("slack_bolt.App", "slack_sdk.web.base_client", "slack_sdk.socket_mode.builtin.client"):
    logging.getLogger(_name).addFilter(_NetworkErrorDemoteFilter())


# ---------------------------------------------------------------------------
# Backend-as-Daemon: mode detection + backend.json lifecycle
# ---------------------------------------------------------------------------

_BACKEND_JSON_DEFAULT = str(Path(get_app_data_dir()) / "backend.json")
_backend_start_monotonic: float = 0.0  # set during lifespan startup

# Unique boot identifier — changes on every process restart.
# Tauri daemon watchdog compares this to detect silent restarts
# (daemon restart too fast for poll interval to catch the gap).
_boot_id: str = __import__("uuid").uuid4().hex[:12]


def _resolve_sdk_version() -> str:
    """Resolve the real claude-agent-sdk version at runtime.

    Frozen-env-safe: PyInstaller strips dist-info, so importlib.metadata
    fails in production. The SDK ships a ``__version__`` attribute on the
    package itself, which survives freezing. Fallback chain:
    ``claude_agent_sdk.__version__`` → env ``CLAUDE_AGENT_SDK_VERSION`` →
    literal ``"unknown"``. Never raises.
    """
    try:
        import claude_agent_sdk
        v = getattr(claude_agent_sdk, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get("CLAUDE_AGENT_SDK_VERSION") or "unknown"


def _resolve_cli_version() -> str:
    """Resolve the bundled Claude Code CLI version by running ``--version``.

    Called ONCE at boot (cached in ``_cli_version``); never per request.
    The bundled CLI path is resolved the same way the SDK's transport does:
    ``<subprocess_cli pkg>/.._bundled/claude``. 2s timeout, returns
    ``"unknown"`` on any failure (missing binary, timeout, parse error).
    Never raises.
    """
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli as _sc
        cli_name = "claude.exe" if sys.platform == "win32" else "claude"
        cli_path = Path(_sc.__file__).resolve().parents[2] / "_bundled" / cli_name
        if not cli_path.is_file():
            return "unknown"
        out = subprocess.run(
            [str(cli_path), "--version"],
            capture_output=True, text=True, timeout=2.0,
        )
        # Non-zero exit (corrupt binary, auth/license error) may still print
        # diagnostic text to stdout — don't mistake its first token for a
        # version. Treat any failure as unknown.
        if out.returncode != 0:
            return "unknown"
        # Output form: "2.1.150 (Claude Code)" — take the first token.
        first = (out.stdout or "").strip().split()
        return first[0] if first else "unknown"
    except Exception:
        return "unknown"


# Real runtime versions for /health observability.
# _sdk_version is eager (attribute read, cheap). _cli_version is filled at
# boot in lifespan() (subprocess, must not run per health request).
_sdk_version: str = _resolve_sdk_version()
_cli_version: str = "unknown"


def _is_port_listening(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket as _socket

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((host, port))
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _detect_run_mode() -> str:
    """Detect backend run mode from SWARMAI_MODE env var.

    - ``"daemon"``     — macOS launchd 24/7 service (default)
    - ``"subprocess"`` — Windows/Linux desktop (Tauri child process, dies with app)
    - ``"hive"``       — EC2 cloud deployment (systemd, 24/7)
    - ``"dev"``        — Local development (manual start, no channels)

    Gateway runs only in {daemon, hive}. /shutdown blocked in {daemon, hive}.
    """
    return os.environ.get("SWARMAI_MODE", "daemon")


def _backend_json_lock(path: str) -> str:
    """Return the advisory lock file path for backend.json operations."""
    return path + ".lock"


def _other_live_backend(path: str = _BACKEND_JSON_DEFAULT) -> "int | None":
    """PID of ANOTHER backend that backend.json says is running and that is alive,
    else None (no file, unreadable, our own pid, or a dead pid: we are its
    successor). A pid we may not signal counts as alive."""
    import json as _json
    try:
        pid = int(_json.loads(Path(path).read_text()).get("pid"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if pid == os.getpid() or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        return pid
    except OSError:
        return None
    return pid


def write_backend_json(
    port: int,
    mode: str,
    path: str = _BACKEND_JSON_DEFAULT,
) -> bool:
    """Write ``backend.json`` so other processes can discover this backend.

    Returns True when the file was written, False when the conflict guard
    skipped it (another live backend owns it).

    Uses an exclusive file lock (``flock_exclusive`` from ``utils.file_lock``,
    which wraps ``fcntl.flock`` on Unix and ``msvcrt.locking`` on Windows) to
    eliminate the TOCTOU race between conflict-check and file-write.  Without
    the lock, two backends starting simultaneously can both pass the conflict
    check and the last writer wins — corrupting discovery for the loser.

    **Conflict check (inside lock):** If an existing ``backend.json``
    records a PID that is alive AND the recorded port is accepting
    connections, we skip the write to prevent a competing backend from
    stealing the discovery file.

    **PID ownership guard:** After writing, only *this* process's PID is
    in the file — ``remove_backend_json`` checks PID before deleting to
    prevent a late-exiting process from removing a newer owner's file.
    """
    import json as _json
    from datetime import datetime, timezone

    from utils.file_lock import flock_exclusive, flock_unlock

    p = Path(path)
    lock_path = Path(_backend_json_lock(path))
    p.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fd:
        flock_exclusive(lock_fd)  # blocking exclusive lock
        try:
            # Conflict check: don't overwrite if an active backend already owns this file
            if p.exists():
                try:
                    existing = _json.loads(p.read_text())
                    existing_pid = existing.get("pid")
                    existing_port = existing.get("port")
                    if (
                        existing_pid is not None
                        and existing_pid != os.getpid()
                        and existing_port is not None
                    ):
                        # Check PID alive
                        try:
                            os.kill(existing_pid, 0)
                        except PermissionError:
                            pid_alive = True  # alive but owned by another user
                        except (OSError, ProcessLookupError):
                            pid_alive = False  # dead PID — safe to overwrite
                        else:
                            pid_alive = True
                        if pid_alive:
                            # PID alive — check if port is also listening
                            if _is_port_listening("127.0.0.1", existing_port):
                                logger.warning(
                                    "backend.json conflict: PID %d alive and port %d listening "
                                    "— skipping write (our PID=%d, port=%d)",
                                    existing_pid, existing_port, os.getpid(), port,
                                )
                                return False
                except (ValueError, OSError):
                    pass  # corrupt file — safe to overwrite

            data = {
                "pid": os.getpid(),
                "port": port,
                "mode": mode,
                "boot_id": _boot_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            p.write_text(_json.dumps(data, indent=2))
            return True
        finally:
            flock_unlock(lock_fd)


def remove_backend_json(
    path: str = _BACKEND_JSON_DEFAULT,
    startup_mode: str | None = None,
) -> None:
    """Delete ``backend.json`` on clean shutdown.

    Uses an exclusive file lock to prevent races with concurrent writers.

    **Mode guard:** If ``startup_mode`` is provided, only delete when the
    mode recorded in the file matches.  This prevents a dev instance from
    deleting the daemon's discovery file on exit.

    **PID ownership guard:** Only delete if the file's PID matches our PID.
    This prevents a late-exiting old process from deleting a newer process's
    discovery file (e.g. during version sync restart).
    """
    from utils.file_lock import flock_exclusive, flock_unlock

    p = Path(path)
    if not p.exists():
        return

    lock_path = Path(_backend_json_lock(path))
    try:
        with open(lock_path, "w") as lock_fd:
            flock_exclusive(lock_fd)
            try:
                if not p.exists():
                    return  # deleted between our check and lock acquisition

                try:
                    import json as _json
                    data = _json.loads(p.read_text())
                except (ValueError, OSError):
                    # Corrupt file — safe to remove
                    p.unlink(missing_ok=True)
                    return

                # PID ownership: only delete if WE wrote this file
                file_pid = data.get("pid")
                if file_pid is not None and file_pid != os.getpid():
                    logger.info(
                        "Skipping backend.json removal: file PID=%d != our PID=%d",
                        file_pid, os.getpid(),
                    )
                    return

                # Mode guard
                if startup_mode is not None:
                    file_mode = data.get("mode")
                    if file_mode is not None and file_mode != startup_mode:
                        logger.info(
                            "Skipping backend.json removal: file mode=%s != startup mode=%s",
                            file_mode, startup_mode,
                        )
                        return

                p.unlink(missing_ok=True)
            finally:
                flock_unlock(lock_fd)
    except Exception:
        # Best-effort — don't crash on shutdown for a discovery file
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def read_backend_json(path: str = _BACKEND_JSON_DEFAULT) -> dict | None:
    """Read and validate ``backend.json``.

    Uses a shared file lock to prevent reading a half-written file.

    Returns the parsed dict if the file exists, is valid JSON, and the
    PID recorded in it is still alive.  Returns ``None`` otherwise
    (missing file, corrupt JSON, dead PID).
    """
    import json as _json

    from utils.file_lock import flock_shared, flock_unlock

    p = Path(path)
    if not p.exists():
        return None

    lock_path = Path(_backend_json_lock(path))
    try:
        with open(lock_path, "w") as lock_fd:
            flock_shared(lock_fd)  # shared lock — multiple readers OK on Unix
            try:
                if not p.exists():
                    return None
                try:
                    data = _json.loads(p.read_text())
                except (ValueError, OSError):
                    return None

                # Stale PID check: is the recorded process still alive?
                pid = data.get("pid")
                if pid is None:
                    return None
                try:
                    os.kill(pid, 0)  # signal 0 = existence check
                except (OSError, ProcessLookupError):
                    return None  # process is dead → stale file

                return data
            finally:
                flock_unlock(lock_fd)
    except (OSError, IOError):
        # Lock file inaccessible — fall back to lockless read
        try:
            data = _json.loads(p.read_text())
            pid = data.get("pid")
            if pid is None:
                return None
            os.kill(pid, 0)
            return data
        except Exception:
            return None


def _detect_backend_port() -> int:
    """Detect the port this backend is listening on.

    Resolution order:
    1. ``--port N`` in sys.argv (production — set by desktop_main.py via Tauri)
    2. ``PORT`` env var
    3. ``settings.port`` (dev default: 8000)
    """
    # 1. Check sys.argv for --port N
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass

    # 2. Check PORT env var
    port_env = os.environ.get("PORT")
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass

    # 3. Fall back to settings
    return settings.port


def _get_seed_database_path() -> Path | None:
    """Get the path to the bundled seed database.
    
    Returns:
        Path to seed.db or None if not found
        
    See utils.bundle_paths for Tauri bundle structure documentation.
    """
    backend_dir = Path(__file__).resolve().parent
    dev_seed_path = backend_dir.parent / "desktop" / "resources" / "seed.db"
    return get_resource_file("seed.db", dev_seed_path)


def _purge_corrupt_db(db_path: Path, reason: str) -> "Path | None":
    """ISOLATE a suspect data.db (rename to .corrupt-<ts>), NEVER delete it.

    ⚠️ RENAMED SEMANTICS (run_a456640f, STEERING #20 + COE run_2d3417d9): this
    used to ``unlink()`` the db + sidecars — an IRREVERSIBLE destroy of an
    irreplaceable user store, triggered by nothing stronger than a boot-time
    exception. That is the destroy-user-data anti-pattern behind STEERING #20 —
    though log evidence shows this purge path did NOT fire for the 2026-08-12 loss
    (that was a different, upstream path; see data_safety.py module docstring).
    It now PRESERVES: it renames
    the db AND its ``-wal``/``-shm`` sidecars together to ``<name>.corrupt-<ts>``
    via ``isolate_store``, so a leftover foreign WAL is never replayed into a
    fresh seed (the original re-corruption concern) AND the data survives,
    recoverable.

    Returns the isolated path (or ``None`` if the file was already gone). The
    caller re-seeds a FRESH store beside it and drops a recovery marker so the
    user is asked — at the next session — whether to recover from the isolated
    copy (option B: reachable daemon + non-silent + never-destroy).
    """
    from core.data_safety import isolate_store
    if not db_path.exists():
        return None
    # A 0-byte file holds NO data to preserve (an interrupted create). Isolating it
    # would only litter ~/.swarm-ai with junk `.corrupt-*` files that no marker
    # references and nothing reaps (adversarial LOW, run_a456640f). Deleting an empty
    # file is not destruction of user data — so unlink the empties, preserve the rest.
    try:
        if db_path.stat().st_size == 0:
            logger.warning("%s at %s — 0-byte (no data to preserve); removing + re-seeding", reason, db_path)
            db_path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            return None
    except OSError:
        pass  # stat failed — fall through to the preserve path (never risk data)
    logger.warning("%s at %s — ISOLATING (rename, not delete) + re-seeding fresh", reason, db_path)
    return isolate_store(db_path)


def _reseed_from_seed(user_db_path: Path) -> bool:
    """Atomically copy seed.db → user_db_path and set WAL pragmas.

    Returns True on success (db ready), False if no seed is available (dev mode
    → caller falls back to runtime init). Shared by first-launch and the
    corruption-recovery path so both re-seed identically.
    """
    seed_db_path = _get_seed_database_path()
    if not seed_db_path or not seed_db_path.exists():
        logger.warning("Seed database not found, falling back to runtime initialization")
        return False

    tmp_path = user_db_path.with_suffix(".db.tmp")
    try:
        shutil.copy2(seed_db_path, tmp_path)
        os.replace(tmp_path, user_db_path)  # atomic on POSIX
        logger.info(f"Copied seed database from {seed_db_path} to {user_db_path}")
    except Exception as e:
        logger.error(f"Failed to copy seed database: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
            user_db_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning("Will fall back to runtime initialization")
        return False

    try:
        with sqlite3.connect(str(user_db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        logger.info("Set WAL mode and busy_timeout on seed-copied database")
    except Exception as e:
        logger.warning(f"Failed to set database pragmas (non-fatal): {e}")
    return True


async def _init_db_bounded(skip_schema: bool, timeout: float = 45.0) -> None:
    """Run initialize_database() under a wall-clock timeout, for BOTH boot paths.

    Both the fast path (skip_schema=True) and full-init acquire an exclusive
    migration flock; a stale lock holder can hang the call forever. The full-init
    path was already bounded (asyncio.wait_for 45s) but the fast path was NOT —
    so a wedged lock hung boot indefinitely with no health signal. Route both
    through this helper so the fast path gets the same bound. On breach: raise
    RuntimeError (launchd KeepAlive then surfaces a bounded restart, not an
    infinite "initializing" hang).

    Integrity: the migration run inside initialize_database() IS the integrity
    gate. A malformed/torn data.db makes the very first migration query raise
    sqlite3.DatabaseError — that IS the crash-loop A2 defends against. We catch
    it HERE (fast path only, where a seed exists to recover from), purge the
    corrupt file + sidecars, re-seed, and retry the migration ONCE on the fresh
    db. If the retry ALSO raises → re-raise (bounded KeepAlive restart, never an
    infinite loop). This replaces the old O(db-size) PRAGMA quick_check pre-probe
    that scanned every b-tree page (~47s on a 1.29GB db) to detect exactly this
    class — the migration detects it for free.

    ⚠️ ORDER IS LOAD-BEARING: sqlite3.OperationalError IS A SUBCLASS of
    sqlite3.DatabaseError. A momentarily-locked-but-VALID db raises
    OperationalError ("database is locked"); it must NOT be treated as
    corruption (that would unlink()+re-seed a valid db → USER DATA DESTROYED,
    the run_2d3417d9 HIGH). So the OperationalError re-raise clause MUST precede
    the DatabaseError recovery clause.
    """
    try:
        await asyncio.wait_for(initialize_database(skip_schema=skip_schema), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "Database initialization timed out after %ss (skip_schema=%s) — "
            "likely a stale migration lock; check for a wedged prior process",
            timeout, skip_schema,
        )
        raise RuntimeError("Database initialization timed out")
    except sqlite3.OperationalError:
        # Locked/busy — NOT corruption. Never destroy a valid-but-busy db.
        # (Subclass of DatabaseError → MUST be caught before the clause below.)
        raise
    except sqlite3.DatabaseError as e:
        # A migration query raised a DatabaseError. Two guards, in order:
        #
        # (1) VERDICT (AC5, STEERING #20): only a GENUINE corruption signature
        #     ("database disk image is malformed" / "file is not a database" /
        #     encrypted / bad schema) justifies touching the store. A bare
        #     DatabaseError is NOT proof of corruption — a full disk, a novel
        #     sqlite message, etc. must NOT trigger the heavy path. The COE was
        #     precisely "weakest judge (any DatabaseError) → heaviest act (destroy
        #     the store)". is_corruption_error is fail-CLOSED for the destructive
        #     action: unrecognized → NOT corruption → propagate (bounded restart).
        from core.data_safety import is_corruption_error, write_recovery_marker
        if not is_corruption_error(e):
            logger.error(
                "Database migration raised non-corruption %s: %s — NOT touching the "
                "store; propagating as a bounded restart (never destroy a maybe-valid db)",
                type(e).__name__, e,
            )
            raise
        # (2) RECOVERY (option B): only the fast path can recover (a seed exists);
        #     full-init has no seed, so re-raise there.
        if not skip_schema:
            raise
        user_db_path = get_app_data_dir() / "data.db"
        logger.warning(
            "Database migration raised %s: %s — CORRUPT data.db; isolating (preserve) "
            "+ re-seeding fresh + marking for user recovery, retry once",
            type(e).__name__, e,
        )
        # PRESERVE the corrupt store (rename, never delete).
        isolated = _purge_corrupt_db(
            user_db_path, "Malformed database (migration raised, prevents crash-loop)"
        )
        # NON-SILENT — write the recovery marker IMMEDIATELY after isolate, BEFORE
        # the reseed (adversarial MED, run_a456640f): if there is NO seed to recover
        # from, the reseed fails and we re-raise into a bounded restart — but the
        # user's data has ALREADY been renamed away, so the marker MUST already be on
        # disk or the isolated location is silently lost (data preserved but the user
        # never told where). Writing it here guarantees the recover-vs-discard
        # decision is recorded on EVERY path, seed-or-no-seed. Best-effort — a marker
        # failure must never block boot; the corrupt store is preserved regardless.
        try:
            write_recovery_marker(
                get_app_data_dir(),
                isolated_path=(isolated or "<already-absent>"),
                reason=f"Corrupt data.db isolated at boot ({type(e).__name__}: {e}). "
                       f"Your prior data is preserved in the isolated file — decide "
                       f"recover vs discard. (A fresh store may have been seeded so the "
                       f"app could start; if boot is looping, no seed was available.)",
            )
        except Exception as _mk_exc:  # noqa: BLE001 — surfacing is best-effort, never blocks boot
            logger.error("Failed to write DB recovery marker (non-blocking): %s", _mk_exc)
        # Now re-seed a FRESH store beside the isolated copy so the daemon stays
        # reachable (option B, not a hard-down).
        if not _reseed_from_seed(user_db_path):
            # No seed to recover from — re-raise (bounded restart). The marker above
            # already recorded the isolated location, so recovery is surfaceable even
            # though this boot can't come up.
            raise
        # Reset the DB INSTANCE's init flag so the retry actually re-runs
        # migrations on the FRESH db. SQLiteDatabase.initialize() short-circuits
        # on `if self._initialized: return` (sqlite.py:1738), and the failed
        # first attempt may have set it — so clear it on the singleton instance
        # (database.get_database()), not on the module.
        try:
            from database import get_database
            get_database()._initialized = False  # type: ignore[attr-defined]
        except Exception:
            pass
        # Retry ONCE on the fresh db. If THIS raises, let it propagate — a bounded
        # KeepAlive restart, never an infinite re-seed loop.
        await asyncio.wait_for(initialize_database(skip_schema=skip_schema), timeout=timeout)


def _ensure_database_initialized() -> bool:
    """Ensure the user database exists, copying from seed if needed.

    Checks whether a user database already exists at ``~/.swarm-ai/data.db``.

    * **Returning user** (``data.db`` exists): returns ``True`` immediately so
      the caller can skip the expensive init pipeline — user data is preserved.
    * **First launch** (``data.db`` missing, ``seed.db`` available): performs an
      atomic copy (write to a temp file, then ``os.replace``) and sets WAL mode
      + busy_timeout pragmas on the fresh copy.  Returns ``True``.
    * **Dev mode** (no ``seed.db``): logs a warning and returns ``False`` so the
      caller falls back to runtime initialization.

    Returns:
        ``True``  — database is ready; skip the init pipeline.
        ``False`` — no seed available; caller must run runtime init.

    Integrity note: there is intentionally NO content-scanning integrity probe
    here. The old ``_db_is_intact`` ran ``PRAGMA quick_check`` on every boot,
    which reads every b-tree page — O(db-size), ~47s on a 1.29GB db — to detect
    a malformed file. That check is redundant: the migration run by
    ``_init_db_bounded`` IS the integrity gate. A malformed/torn db makes the
    first migration query raise ``sqlite3.DatabaseError``, which ``_init_db_bounded``
    catches → purge + re-seed + retry-once. A transient lock raises
    ``OperationalError`` and is deliberately NOT treated as corruption. This
    detects exactly the crash-loop class for free and keeps cold-start at <1s.
    (Coverage note: ``_run_migrations`` → ``_run_data_cleanups`` runs
    unconditionally every boot and SELECTs over schema-owned tables — ``tasks``,
    ``chat_threads``, ``swarm_workspaces``, ``app_settings`` — so a torn page in
    ANY of those is also caught here as a migration DatabaseError, not just
    header/schema damage. The only shape NOT detected is a torn page in a table
    that no migration reads; that cannot crash-loop the boot and would surface
    later as a specific query error — out of scope for a boot-time guard.)

    Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.7, 2.8
    """
    user_db_path = get_app_data_dir() / "data.db"

    # Ensure the app data directory exists
    user_db_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Returning user: preserve existing data.db, skip init pipeline ---
    if user_db_path.exists():
        if user_db_path.stat().st_size == 0:
            # 0-byte = an interrupted create. Purge + fall through to re-seed.
            # (Malformed-but-nonzero is handled by the migration catch in
            # _init_db_bounded, not by an expensive pre-scan here.)
            _purge_corrupt_db(user_db_path, "Empty database")
        else:
            logger.info(f"Using existing user database at {user_db_path}")
            return True

    # --- First launch (or post-purge): atomic seed copy ---
    return _reseed_from_seed(user_db_path)


async def _deferred_refresh_defaults(label: str) -> None:
    """Background task that refreshes built-in skills and context files.

    Shared by both the fast-path and full-init quick-validation paths to
    avoid duplicating the same closure.  Logs success/failure and records
    elapsed time into the module-level ``_phase_timings`` dict.

    Also runs MCP migration (idempotent) to ensure legacy user-mcp-servers.json
    entries are converted to the new .claude/mcps/mcp-dev.json format.
    This was previously only in run_full_initialization — existing users who
    already had initialization_complete=1 would never get their MCPs migrated.

    Args:
        label: Human-readable label for log messages (e.g. ``"fast path"``).
    """
    _t_start = time.monotonic()
    try:
        from core.initialization_manager import initialization_manager
        await initialization_manager.refresh_builtin_defaults()
        logger.info("Builtin defaults refreshed (deferred, %s)", label)

        # Ensure MCP migration runs on every startup path (idempotent).
        # Previously only ran in run_full_initialization, so returning users
        # with initialization_complete=1 never got their MCPs migrated.
        try:
            from pathlib import Path
            from core.mcp_migration import migrate_if_needed
            from core.mcp_config_loader import merge_catalog_template
            from utils.bundle_paths import get_resources_dir

            ws_path = Path(initialization_manager.get_cached_workspace_path())
            await migrate_if_needed(ws_path)

            # Also merge catalog template (adds new entries from product updates)
            _backend_dir = Path(__file__).resolve().parent
            _dev_resources = _backend_dir.parent / "desktop" / "resources"
            resources_dir = get_resources_dir(_dev_resources)
            template_path = resources_dir / "mcp-catalog.json"
            merge_catalog_template(ws_path, template_path)

            # Ensure directory exists
            (ws_path / ".claude" / "mcps").mkdir(parents=True, exist_ok=True)
            logger.info("MCP config ensured (deferred, %s)", label)
        except Exception:
            logger.exception("MCP config setup failed (non-fatal, %s)", label)
    except Exception:
        logger.exception("Deferred refresh_builtin_defaults failed (non-fatal, %s)", label)
    finally:
        elapsed = round((time.monotonic() - _t_start) * 1000)
        _phase_timings_ref = _phase_timings
        if _phase_timings_ref is not None:
            _phase_timings_ref["refresh_defaults_ms"] = elapsed
        logger.info("Phase: refresh_builtin_defaults (deferred) — %dms", elapsed)


_SCHEDULER_INTERVAL_SECONDS = 3600  # 1 hour — same as the old launchd StartCalendarInterval


_job_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job-executor")


# Module-level handle so the watcher isn't garbage-collected + is stoppable.
_workspace_surface_watcher = None


async def _set_aside_inherited_cognition() -> None:
    """A Hive that becomes a worker moves aside the cognition it wrote as a full SwarmAI
    (core/worker_reset: moved, never deleted; once, by its marker). Never raises."""
    try:
        from core.initialization_manager import initialization_manager
        from core.worker_reset import set_aside
        ws = initialization_manager.get_cached_workspace_path()
        if ws:
            await asyncio.to_thread(set_aside, Path(ws))
    except Exception:
        logger.exception("Worker Hive: setting aside inherited cognition failed")


async def _start_workspace_surface_watcher() -> None:
    """Start the Canvas Layer-2 workspace surface watcher (daemon/hive only).

    Watches SwarmWS for author-agnostic writes (sub-agent/CLI/hook) that the SDK
    sidechain filter hides from Layer-1, and routes review-worthy content/knowledge
    writes onto the sole streaming session's live SSE stream. Non-fatal on any
    failure — surfacing is best-effort, never load-bearing.
    """
    global _workspace_surface_watcher
    await asyncio.sleep(10)  # Let startup settle (mirror code-intel watcher)
    try:
        from core.project_registry import get_swarmws
        from core.workspace_surface_watcher import WorkspaceSurfaceWatcher

        root = Path(get_swarmws())
        if not root.is_dir():
            logger.debug("WorkspaceSurfaceWatcher: SwarmWS root missing — skipping")
            return
        _workspace_surface_watcher = WorkspaceSurfaceWatcher(root)
        await _workspace_surface_watcher.start()
    except Exception:
        logger.debug("WorkspaceSurfaceWatcher startup failed (non-fatal)", exc_info=True)


async def _run_inprocess_scheduler() -> None:
    """In-process job scheduler loop (daemon/hive only).

    Replaces the external com.swarmai.scheduler launchd job with an asyncio
    loop inside the daemon process. Eliminates 5 failure modes:
    1. Path fragility (venv rebuild breaks external plist)
    2. No catch-up after sleep (launchd StartCalendarInterval doesn't retry)
    3. Silent failure (KeepAlive=false means no restart)
    4. Dual management (dev/daemon both install plist)
    5. No monitoring (nobody detects "scheduler hasn't run in N hours")

    Runs run_scheduler() in a thread to avoid blocking the event loop
    (it does sync I/O: file reads, network fetches, Bedrock calls).
    """
    # Initial delay: let startup settle (30s) then run immediately to catch up
    await asyncio.sleep(30)

    while True:
        t0 = time.monotonic()
        try:
            logger.info("In-process scheduler: running cycle")
            # run_scheduler is synchronous and long-running (agent tasks up
            # to 480s). Uses dedicated _job_executor to avoid blocking default
            # thread pool (which health endpoint's aiosqlite needs).
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_job_executor, _run_scheduler_safe)
            logger.info("In-process scheduler: cycle complete")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("In-process scheduler: cycle failed (will retry next interval)")

        # Elapsed-aware sleep: maintain stable interval regardless of cycle duration
        elapsed = time.monotonic() - t0
        sleep_time = max(0, _SCHEDULER_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_time)


def _run_scheduler_safe() -> None:
    """Wrapper that imports and runs the scheduler with error isolation."""
    try:
        from jobs.scheduler import run_scheduler
        run_scheduler()
    except SystemExit:
        # run_scheduler calls sys.exit on config errors — don't kill daemon
        logger.warning("In-process scheduler: run_scheduler called sys.exit (suppressed)")
    except Exception:
        # Re-raise so the async wrapper can log it
        raise


def _prewarm_tree_sitter_body() -> None:
    """Load the tree-sitter grammar libraries once (process-wide dlopen), so the
    first parse anywhere does not pay the first load. Never raises."""
    try:
        import tree_sitter_language_pack  # noqa: F401
        from core.code_intel.grammars import get_language
    except Exception:  # noqa: BLE001 — not installed: code intel uses its regex path
        return
    t0 = time.time()
    loaded = []
    for lang in ("python", "typescript", "javascript"):
        try:
            get_language(lang)
            loaded.append(lang)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tree-sitter prewarm %s failed: %s", lang, exc)
    logger.info("tree-sitter pre-warmed %s in %.1fs", ",".join(loaded) or "-",
                time.time() - t0)


def _prewarm_recall_body() -> None:
    """Warm the two cold-start recall legs so the FIRST session's recall_leg
    doesn't pay the first-in-process penalty (run_16113a9b).

    Measured: recall runs ONCE per session; the session leg is 1665ms cold →
    ~300ms warm, library 819ms → ~350ms warm. The first-in-process cost is
    two things a fresh startup query legitimately warms and that PERSIST across
    the fresh per-call connections recall opens:
      (a) the OS page cache of the session/library DB files, and
      (b) the function-local deferred imports (sqlite_vec native lib,
          RecallEngine/KnowledgeStore/SessionRecall) → sys.modules, process-wide.
    NOTE (Gate-1, verified vec_db.py:155 + session_recall.py:227): recall opens
    and CLOSES its connection per call, so this does NOT keep a warm connection —
    page-cache + import-cache are what survive, which is enough. This is a
    BEST-EFFORT reduction of cold-start VARIANCE, not a guarantee: if the very
    first user session races ahead of this fire-and-forget task, that session
    still pays the cold cost.

    Warms ONLY session + library — the two legs with a measured cold penalty.
    Deliberately NOT ddd (per-project ~800ms CPU-BM25, no active project at
    startup, and not even on the recall_leg timer) nor context_files/codeintel
    (no cold-start penalty). Read-only + embed-free; never writes, never raises
    (a warmup failure must be non-fatal — mirrors _prewarm_boto3).
    """
    try:
        from core import recall_multi
        recall_multi.recall_all(
            "warmup", domains=("session", "library"), allow_embed=False,
        )
        logger.info("recall pre-warmed (session + library legs)")
    except Exception:
        logger.debug("recall pre-warm failed (non-critical)", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global _startup_complete, _startup_time_ms, _phase_timings
    from core.initialization_manager import initialization_manager

    t0 = time.monotonic()
    phase_timings: dict[str, float] = {}

    # SWARM_BACKEND_BIN for every child (agent sessions, jobs, MCP servers): the backend
    # program that runs `pipeline-v1` and `py` (utils/backend_bin.py, design §3.2)
    from utils.backend_bin import export_backend_bin
    export_backend_bin()
    # SWARM_BACKEND_PORT for the same children: a skill that calls this daemon's API
    # (s_project-manager's create_project) must reach dev (8000) as well as the
    # packaged daemon (18321), not a hardcoded port.
    os.environ["SWARM_BACKEND_PORT"] = str(_detect_backend_port())

    # One-time migration: ~/.swarm-ai/.context/ → ~/.swarm-ai/state/
    from jobs.paths import _migrate_legacy_state_dir
    _migrate_legacy_state_dir()

    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Database type: {settings.database_type}")
    logger.info(f"Rate limit: {settings.rate_limit_per_minute}/minute")

    # NOTE: SWARMAI_OWNER_PID is set in claude_environment._configure_claude_environment()
    # which runs before any child process is spawned.  Do NOT duplicate it here.

    # Ensure database exists (copy seed DB if needed)
    # Validates: Requirements 2.1, 2.2, 2.5, 2.6, 3.1
    skip_init_pipeline = _ensure_database_initialized()

    if skip_init_pipeline:
        # Fast startup path — seed-sourced or returning user.
        # Create the DB instance (connection pool) without running DDL or migrations.
        logger.info("Fast startup (seed-sourced) — skipping schema DDL, migrations, and full init")
        # Bounded (was unbounded): a stale migration flock must not hang boot forever.
        await _init_db_bounded(skip_schema=True)
        logger.info("Database instance created (schema skipped)")

        t_db = time.monotonic()
        phase_timings["database_ms"] = round((t_db - t0) * 1000)
        logger.info("Phase: database init — %dms", phase_timings["database_ms"])

        # Ensure workspace filesystem exists on disk.
        # The seed DB contains the workspace_config row but NOT the
        # actual directories/files.  For returning users this also
        # heals any missing system-managed items via verify_integrity().
        from database import db as _db
        from core.swarm_workspace_manager import swarm_workspace_manager
        try:
            workspace = await swarm_workspace_manager.ensure_default_workspace(_db)
            initialization_manager._cached_workspace_path = (
                swarm_workspace_manager.expand_path(workspace["file_path"])
            )
            logger.info("Workspace filesystem verified on fast startup path")
        except Exception as e:
            logger.error("Failed to ensure workspace on fast startup: %s", e)

        t_workspace = time.monotonic()
        phase_timings["workspace_ms"] = round((t_workspace - t_db) * 1000)
        logger.info("Phase: workspace verify — %dms", phase_timings["workspace_ms"])

        # Refresh built-in skills and context files — deferred to background
        # so it doesn't block _startup_complete.  The DB already has
        # skill/context data from the previous session (or the seed).
        phase_timings["refresh_defaults_ms"] = 0  # Updated by background task on completion
        asyncio.create_task(_deferred_refresh_defaults("fast path"))
        logger.info("refresh_builtin_defaults deferred to background")
    else:
        # Full initialization path — dev-mode fallback (no seed.db available).
        # Preserve the existing init pipeline exactly.
        logger.info("Full initialization (runtime) — running schema DDL + migrations + init")
        await _init_db_bounded(skip_schema=False)
        logger.info("Database initialized")

        t_db = time.monotonic()
        phase_timings["database_ms"] = round((t_db - t0) * 1000)
        logger.info("Phase: database init — %dms", phase_timings["database_ms"])

        # Check initialization state and run appropriate flow
        # Validates: Requirements 3.1
        if await initialization_manager.is_initialization_complete():
            # Quick validation path - fast startup for returning users
            logger.info("Initialization complete flag is set, running quick validation...")
            if not await initialization_manager.run_quick_validation():
                # Resources missing, fall back to full init
                logger.warning("Quick validation failed, falling back to full initialization...")
                await initialization_manager.run_full_initialization()
            else:
                logger.info("Quick validation passed - fast startup complete")
                
                # Refresh built-in skills and context files — deferred to
                # background since quick validation passed (data exists in DB).
                phase_timings["refresh_defaults_ms"] = 0  # Updated by background task
                asyncio.create_task(_deferred_refresh_defaults("quick-val path"))
                logger.info("refresh_builtin_defaults deferred to background (quick-val path)")
        else:
            # First-time initialization
            logger.info("First-time startup, running full initialization...")
            await initialization_manager.run_full_initialization()

        # On the full-init path, workspace is handled inside the init pipeline.
        # Record workspace_ms as the time from DB init to end of init pipeline.
        t_workspace = time.monotonic()
        phase_timings["workspace_ms"] = round((t_workspace - t_db) * 1000)
        logger.info("Phase: workspace/init pipeline — %dms", phase_timings["workspace_ms"])

        # refresh_defaults_ms: set to 0 if not already set (full init runs it synchronously)
        if "refresh_defaults_ms" not in phase_timings:
            phase_timings["refresh_defaults_ms"] = 0

    # Both startup paths (the fast one is how every installed Hive starts): a Hive
    # that becomes a worker sets aside what it wrote as a full SwarmAI, once, before
    # any session can read it.
    await _set_aside_inherited_cognition()

    # Start channel gateway (deferred to background if channels exist)
    # Validates: Requirements 1.1, 1.2, 1.3, 1.4
    #
    # MODE GUARD: Only daemon and hive run the channel gateway.
    # Subprocess/dev must NOT start it — Socket Mode allows only ONE
    # connection per app token.  Two processes competing causes
    # rapid reconnect churn (770+ "connection closed" events observed).
    phase_timings["gateway_ms"] = 0  # Updated by background task on completion
    _run_mode = _detect_run_mode()
    _gateway_allowed = _run_mode in ("daemon", "hive")

    if not _gateway_allowed:
        channel_gateway._startup_state = "not_started"
        logger.info(
            "Channel gateway skipped — mode=%s (only daemon/hive run channels)",
            _run_mode,
        )
    else:

        _channels_count: int | None = None  # None = query failed, fall back to sync
        try:
            from database import db as _startup_db
            _channels_list = await _startup_db.channels.list()
            _channels_count = len(_channels_list)
        except Exception:
            logger.warning(
                "Failed to query channels count — falling back to synchronous gateway startup"
            )

        if _channels_count == 0:
            # No channels configured — skip gateway startup entirely.
            channel_gateway._startup_state = "not_started"
            logger.info("No channels configured — skipping channel gateway startup")
        elif _channels_count is not None and _channels_count > 0:
            # Channels exist — defer startup to a background task so it
            # doesn't block _startup_complete.
            async def _deferred_gateway_startup() -> None:
                _t_start = time.monotonic()
                try:
                    channel_gateway._startup_state = "starting"
                    await channel_gateway.startup()
                    channel_gateway._startup_state = "started"
                    logger.info(
                        "Channel gateway started (deferred, %d channels)",
                        _channels_count,
                    )
                except Exception:
                    channel_gateway._startup_state = "failed"
                    logger.exception("Deferred channel gateway startup failed")
                finally:
                    elapsed = round((time.monotonic() - _t_start) * 1000)
                    _phase_timings_ref = _phase_timings
                    if _phase_timings_ref is not None:
                        _phase_timings_ref["gateway_ms"] = elapsed
                    logger.info("Phase: channel gateway (deferred) — %dms", elapsed)

            asyncio.create_task(_deferred_gateway_startup())
            logger.info(
                "Channel gateway startup deferred to background (%d channels)",
                _channels_count,
            )
        else:
            # Fallback: channels count query failed (None) — run synchronously
            # (preserves current behavior).
            await channel_gateway.startup()
            channel_gateway._startup_state = "started"
            logger.info("Channel gateway started (synchronous fallback)")

    # --- Initialize file-based config and permission components ---
    # Requirements: 1.2, 4.7, 4.8, 9.3
    from core.app_config_manager import AppConfigManager
    from core.credential_validator import CredentialValidator
    from routers.settings import set_config_manager

    app_config = AppConfigManager.instance()
    app_config.load()
    logger.info("AppConfigManager loaded (config.json)")

    # First start only: remember the default model the governed files were
    # written for, so a later default-model change can prompt a prompt audit
    # (core/governance_audit.py). Best-effort; never blocks startup.
    try:
        from core.governance_audit import record_baseline
        from core.project_registry import get_swarmws as _gov_ws
        await asyncio.to_thread(record_baseline, _gov_ws(), app_config.get("default_model"))
    except Exception:
        logger.debug("governance audit baseline skipped", exc_info=True)

    # Load dangerous command patterns (creates ~/.swarm-ai/dangerous_commands.json if missing)
    from core.security_hooks import load_dangerous_patterns
    dangerous_patterns = load_dangerous_patterns()
    logger.info("Dangerous command patterns loaded (%d patterns)", len(dangerous_patterns))

    cred_validator = CredentialValidator()
    logger.info("CredentialValidator initialized")

    t_config = time.monotonic()
    phase_timings["config_ms"] = round((t_config - t_workspace) * 1000)
    logger.info("Phase: config/permission load — %dms", phase_timings["config_ms"])

    # Pre-warm boto3 import so the first STS call doesn't pay the ~8s
    # PyInstaller import cost on the hot path.  This runs in a background
    # thread to avoid blocking startup.
    async def _prewarm_boto3():
        try:
            await asyncio.to_thread(lambda: __import__("boto3"))
            logger.info("boto3 pre-warmed for credential validation")
        except Exception:
            logger.debug("boto3 pre-warm failed (non-critical)", exc_info=True)
    asyncio.create_task(_prewarm_boto3())

    # Pre-warm the two cold-start recall legs (session + library) off the hot
    # path so the FIRST session's recall_leg doesn't pay the first-in-process
    # cost (run_16113a9b). Fire-and-forget in a thread — never blocks startup
    # readiness; best-effort (a first session racing ahead still pays cold).
    async def _prewarm_recall():
        await asyncio.to_thread(_prewarm_recall_body)
    asyncio.create_task(_prewarm_recall())

    # Pre-load the tree-sitter grammars code intel uses most, so the first parse
    # does not pay the dlopen. They come from the grammars the app ships
    # (core.code_intel.grammars): never a download, which would hold the GIL and
    # stop every thread (192 s on a fresh HOME, 2026-10-28). Off the loop, best-effort.
    async def _prewarm_tree_sitter():
        await asyncio.to_thread(_prewarm_tree_sitter_body)
    asyncio.create_task(_prewarm_tree_sitter())

    # Bring the usage recorded before the usage ledger into it (core/usage_import):
    # read-only on the old tables, idempotent, so it runs at every start. Off the loop.
    async def _import_usage_history():
        try:
            from config import get_app_data_dir
            from core import usage_import
            import database as _database
            counts = await asyncio.to_thread(
                usage_import.run, Path(_database.db.db_path), Path(get_app_data_dir()) / "ingestion.db")
            if any(counts.values()):
                logger.info("usage ledger: imported %s from the old usage records", counts)
        except Exception:
            logger.warning("usage ledger: history import failed (will retry next start)", exc_info=True)
    asyncio.create_task(_import_usage_history())

    # Generate permissions.json for user visibility
    try:
        ws_path = getattr(initialization_manager, '_cached_workspace_path', None)
        if ws_path:
            _generate_permissions_json(Path(ws_path), dangerous_patterns)
            logger.info("permissions.json generated at %s/.claude/settings/", ws_path)
        else:
            logger.warning("Workspace path not available — skipping permissions.json generation")
    except Exception as exc:
        logger.warning("Failed to generate permissions.json (non-critical): %s", exc)

    # ── Remove a crashed session's checkpoint JSON before session infra starts ─
    # (not on a worker Hive: it keeps no DailyActivity of its own, core/app_role)
    from core.app_role import is_worker as _is_worker
    try:
        if not _is_worker():
            from hooks.daily_activity_hook import recover_crash_checkpoint as _recover_checkpoint
            _recovered = _recover_checkpoint()
            if _recovered:
                logger.info("Crash checkpoint of an earlier session removed")
    except Exception:
        logger.debug("Crash checkpoint recovery skipped (non-fatal)", exc_info=True)

    # ── Session lifecycle hooks ──────────────────────────────────────
    from core.session_hooks import SessionLifecycleHookManager, BackgroundHookExecutor
    from core.summarization import SummarizationPipeline
    from core.compliance import ComplianceTracker
    from routers.memory import set_compliance_tracker

    summarization_pipeline = SummarizationPipeline()
    compliance_tracker = ComplianceTracker()
    # 180s accommodates evolution_maintenance_hook which mines 1000+
    # transcripts + calls Bedrock LLM (~90s).  All other hooks finish <5s.
    # The evolution hook now runs in a thread pool (run_in_executor) so
    # the timeout actually fires instead of being bypassed by blocking code.
    hook_manager = SessionLifecycleHookManager(timeout_seconds=180.0)

    # Create fire-and-forget executor — hooks never block the chat path
    hook_executor = BackgroundHookExecutor(hook_manager)

    # CDF kick remains last below
    from core.session_hooks import register_default_hooks
    _hook_names = register_default_hooks(
        hook_manager, git_lock=hook_executor.git_lock,
        summarization_pipeline=summarization_pipeline, compliance_tracker=compliance_tracker)

    # Wire hooks into session_registry (new architecture)
    set_compliance_tracker(compliance_tracker)
    logger.info("Session lifecycle hooks registered (%s, background executor)", ", ".join(_hook_names))

    # ── Initialize new session architecture ──────────────────────────
    session_registry.initialize(app_config)
    # Wire hooks AFTER initialize so lifecycle_manager exists

    # CDF kick is intentionally LAST: every source has finished enqueueing first.
    from hooks.ingestion_session_kick import IngestionSessionKickHook
    hook_manager.register(IngestionSessionKickHook())
    logger.info("Registered IngestionSessionKickHook last")
    session_registry.configure_hooks(executor=hook_executor, manager=hook_manager)
    logger.info("SessionRouter architecture initialized")
    await session_registry.start_lifecycle()
    logger.info("LifecycleManager started at startup")

    # Desktop prewarm pool (方案A, run_f107f442): warm a depth-2 pool of
    # baseline-prompt subprocesses off the hot path so a new desktop tab's first
    # message ADOPTS one (skipping the 8-14s cold __aenter__ handshake). Gated by
    # SWARM_DESKTOP_PREWARM (default OFF) — a no-op when unset. Fire-and-forget,
    # best-effort, never blocks startup (mirrors _prewarm_boto3/_prewarm_recall).
    async def _prewarm_desktop_pool():
        try:
            router = session_registry.session_router
            if router is not None:
                await router.warm_desktop_pool(depth=2)
        except Exception:
            logger.debug("desktop pool pre-warm failed (non-critical)", exc_info=True)
    asyncio.create_task(_prewarm_desktop_pool())

    # A Hive closes the turns its daemon's death left open (core/hive_boot), awaited
    # here: before the claimed messages are reopened and before the Swarm Out bridge
    # starts, which keeps the turns of this boot from starting first (a turn that starts
    # anyway is never touched). Non-fatal; startup waits for it, about one read of the
    # repositories per turn it closes (SessionRouter._task_repo_facts, bounded there).
    if _detect_run_mode() == "hive":
        try:
            closed = await hive_boot.close_open_turns(session_registry.session_router)
            if closed:
                logger.info("Closed %d turn(s) the daemon's stop left open", closed)
        except Exception as exc:  # pragma: no cover - non-fatal startup guard
            logger.warning("hive_boot.close_open_turns failed (non-fatal): %s", exc)

    # Root-1 SSOT: reopen any pending messages left in the 'claimed' phase by a
    # crash (claimed_at set, sent=0) so they re-drain on the next IDLE instead of
    # being stuck forever. Idempotent, DB-only, non-fatal — never blocks startup.
    try:
        from core.session_pending import reopen_dangling_claims
        reopened = await reopen_dangling_claims()
        if reopened:
            logger.info("Reopened %d dangling pending-message claim(s)", reopened)
    except Exception as exc:  # pragma: no cover - non-fatal startup guard
        logger.warning("reopen_dangling_claims failed (non-fatal): %s", exc)
    # ─────────────────────────────────────────────────────────────────

    t_agent = time.monotonic()
    phase_timings["session_infra_ms"] = round((t_agent - t_config) * 1000)
    logger.info("Phase: session infrastructure — %dms", phase_timings["session_infra_ms"])

    # Wire AppConfigManager into Settings router (DI).
    # Skip if already configured (e.g. test fixtures may pre-set).
    from routers import settings as _settings_mod
    if _settings_mod._config_manager is None:
        set_config_manager(app_config)
        logger.info("Settings router configured with AppConfigManager")
    else:
        logger.debug("Settings router already configured (skipping overwrite)")

    # Wire up TSCC state manager for the tscc router
    from core.tscc_state_manager import TSCCStateManager
    from routers.tscc import register_tscc_dependencies

    _tscc_state_manager = TSCCStateManager()
    register_tscc_dependencies(_tscc_state_manager)
    logger.info("TSCC state manager initialized")

    # Kill ALL leftover claude CLI processes from previous instance.
    # At startup, no claude processes should be running — any that exist are
    # zombies from a crash or unclean shutdown. These hold vnodes and can
    # cause kernel panics (COE 2026-03-15: 80 zombies -> vnode exhaustion -> panic).
    # NOT for a verify_build probe: the sweep is system-wide (pgrep), not scoped to
    # this data dir, so a probe started during `./prod.sh build` could kill the
    # LIVE daemon's CLIs (an untagged process, or a `ps` hiccup reading the tag).
    # SWARMAI_NO_CLI_SWEEP=1: same for any other throwaway instance (the new-user
    # smoke test) — it boots on a temp HOME but the sweep would still see the
    # live daemon's processes.
    if (os.environ.get("SWARMAI_VERIFY_BUILD") == "1"
            or os.environ.get("SWARMAI_NO_CLI_SWEEP") == "1"):
        logger.info("Isolated instance (verify probe / smoke) — skipping leftover claude CLI sweep")
    elif not os.environ.get("SWARMAI_MODE"):
        # F6, fail closed: every real launcher sets SWARMAI_MODE explicitly (the
        # launchd wrapper, hive's systemd unit, Tauri's subprocess spawn, dev.sh).
        # Unset means a script or test imported the app (conftest strips it): it
        # has no leftovers of its own to clean, even on a temp HOME where no
        # backend.json is found.
        logger.info("SWARMAI_MODE not set (not a registered launcher) — skipping leftover claude CLI sweep")
    elif _other_live_backend() is not None:
        # F6: only the backend that owns backend.json may sweep. Another live
        # backend is registered (this is a test, probe or second instance): its
        # CLIs are not leftovers. Two clusters of 3-5 CLIs killed within 230 ms
        # (10-20, 10-21) came after the probe / owner-tag fixes.
        logger.info("Backend pid %s owns backend.json — skipping leftover claude CLI sweep",
                    _other_live_backend())
    else:
        startup_killed = session_registry.kill_all_claude_processes()
        if startup_killed:
            logger.warning("Killed %d leftover claude process(es) at startup", startup_killed)

    # Write backend.json so Tauri (or other processes) can discover us
    global _backend_start_monotonic
    _backend_start_monotonic = time.monotonic()
    backend_port = _detect_backend_port()
    backend_mode = _detect_run_mode()
    _bjson_written = write_backend_json(port=backend_port, mode=backend_mode)
    # Desktop CLI sessions reach the Bedrock thinking gateway on this port.
    from core.bedrock_thinking_gateway import set_listen_port as _set_gw_port
    _set_gw_port(backend_port)
    if _bjson_written:
        logger.info("backend.json written (port=%d, mode=%s)", backend_port, backend_mode)
        # The SwarmAI AWS account (design 2026-10-31-swarmai-aws-account §5): record,
        # the `swarmai` credential_process profile and one verification, off the
        # critical path. Only the backend that owns backend.json writes these files.
        async def _aws_account_startup():
            from core import aws_account as _aws_account, executors as _executors
            try:
                await _executors.run_in("subprocess", _aws_account.startup, backend_port)
            except Exception:
                logger.warning("aws_account startup failed", exc_info=True)
            if _aws_account.is_hive_mode():
                return
            try:  # Hive rows in the SwarmAI account move to it (design §5.5)
                from database import db as _hdb
                from hive import accounts as _hive_accounts
                outcome = await _hive_accounts.migrate(_hdb.db_path)
                if outcome:
                    logger.info("hive accounts: %s", outcome)
            except Exception:
                logger.warning("hive account migration failed", exc_info=True)
            # the table follows EC2 and replacements finish (design §7.3); every 10 minutes
            from hive import fleet as _fleet
            from routers.hive import delete_row as _delete_row
            while True:
                try:
                    await _fleet.reconcile(_hdb.db_path, cleanup=_delete_row)
                    await _fleet.status(_hdb.db_path)   # what Need-You shows (cached)
                except Exception:
                    logger.warning("hive fleet reconcile failed", exc_info=True)
                await asyncio.sleep(600)
        # kept referenced: it runs the reconcile loop for the daemon's life (the loop only
        # holds weak references to tasks)
        app.state.aws_account_task = asyncio.create_task(_aws_account_startup())
    else:
        # The conflict guard already logged a WARNING with the owner's pid/port.
        logger.info("backend.json NOT written (port=%d, mode=%s) — owned by another live backend",
                    backend_port, backend_mode)

    # CDF runs in every full HTTP mode, but the file lock permits one owner per data dir.
    # The lock may still be held by the process this one replaces (a restart overlaps
    # its shutdown), so a backend that does not get it keeps trying every 30 s instead
    # of staying enqueue/read-only until the next restart.
    _ingestion: dict = {"handle": None}

    async def _try_ingestion_owner() -> bool:
        """Take the owner lock and start the worker; False when another backend holds it."""
        from core.ingestion.owner import acquire as _acquire_ingestion_owner
        from core.ingestion.worker import WorkerHandle as _IngestionWorkerHandle
        from core.project_registry import get_swarmws as _ingestion_workspace
        owner = await asyncio.to_thread(_acquire_ingestion_owner)
        if owner is None:
            return False
        try:
            handle = _IngestionWorkerHandle(owner, _ingestion_workspace())
            handle.start()
        except BaseException:
            owner.release()
            raise
        _ingestion["handle"] = handle   # only a started handle is stopped at shutdown
        logger.info("CDF ingestion owner worker started")
        return True

    async def _ingestion_owner_loop() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                if await _try_ingestion_owner():
                    return
            except Exception:
                logger.exception("CDF ingestion worker startup failed; retrying in 30 s")

    _ingestion_owner_task = None
    try:
        _ingestion_started = await _try_ingestion_owner()
    except Exception:
        if _is_worker():
            # a worker Hive whose extraction could not start would end every run with no
            # lessons and no record of being done: refuse to start instead (design §3.1).
            # The owner lock held by another live backend is not this: that one extracts.
            logger.exception("CDF ingestion worker startup failed")
            raise RuntimeError("a worker Hive needs its CDF extraction, which did not start "
                               "(see the log above)") from None
        logger.exception("CDF ingestion worker startup failed; retrying every 30 s")
        _ingestion_started = False
    if not _ingestion_started:
        logger.info("CDF ingestion owner not taken; this process is enqueue/read-only and "
                    "retries every 30 s")
        _ingestion_owner_task = asyncio.create_task(_ingestion_owner_loop())

    # Resolve the bundled CLI version once at boot; never run it in /health.
    global _cli_version
    _cli_version = _resolve_cli_version()
    logger.info("runtime versions: sdk=%s cli=%s", _sdk_version, _cli_version)

    # Mark startup as complete - health check will now return healthy
    _startup_complete = True
    total_ms = round((time.monotonic() - t0) * 1000)
    _startup_time_ms = total_ms
    _phase_timings = phase_timings
    logger.info(
        "Startup complete — total %dms (db=%dms, workspace=%dms, config=%dms, session=%dms)",
        total_ms,
        phase_timings.get("database_ms", 0),
        phase_timings.get("workspace_ms", 0),
        phase_timings.get("config_ms", 0),
        phase_timings.get("session_infra_ms", 0),
    )
    logger.info("Startup complete - ready to serve requests")

    # ── Start managed subsidiary services (Slack bot, etc.) ─────────────
    # Deferred to background so it never blocks startup.  Services
    # discover the backend via ~/.swarm-ai/backend.port written here.
    from core.service_manager import service_manager as _svc_mgr

    async def _deferred_services_startup() -> None:
        try:
            ws_path = initialization_manager.get_cached_workspace_path()
            backend_port = _detect_backend_port()
            await _svc_mgr.start_all(ws_path, backend_port)
        except Exception:
            logger.exception("Managed services startup failed (non-fatal)")

    asyncio.create_task(_deferred_services_startup())

    # ── In-process job scheduler (daemon/hive only) ───────────────────
    # Replaces the external com.swarmai.scheduler launchd job.
    # Benefits: no path fragility, catches up after sleep, self-monitoring,
    # and inherits the daemon's credentials/env.
    _scheduler_task: asyncio.Task | None = None
    if backend_mode in ("daemon", "hive"):
        _scheduler_task = asyncio.create_task(
            _run_inprocess_scheduler(),
            name="inprocess-scheduler",
        )
        logger.info("In-process scheduler started (60min interval)")

    # ── Swarm Out bridge (hive only) ──────────────────────────────────
    # Mirrors this Hive's open prompts to S3 and delivers the Host's answers
    # (core/swarm_out_bridge.py). Off when the instance has no Hive identity.
    _swarm_out_bridge_task: asyncio.Task | None = None
    if backend_mode == "hive":
        from core.swarm_out_bridge import run_bridge_loop
        _swarm_out_bridge_task = asyncio.create_task(run_bridge_loop(), name="swarm-out-bridge")

    # ── Swarm Out runs, refreshed whether or not anyone looks (host only) ──
    # Settling, releasing a Hive's workspace and expiry do not wait for the panel to
    # poll (docs/design/2026-10-31-swarm-out-run-review.md §3.1).
    _swarm_out_refresh_task: asyncio.Task | None = None
    if backend_mode != "hive":
        from core.swarm_out import run_refresh_loop as _swarm_out_refresh_loop
        _swarm_out_refresh_task = asyncio.create_task(_swarm_out_refresh_loop(), name="swarm-out-refresh")

    # ── Read-only content origin for "Open in browser" (desktop launchers only) ──
    # docs/design/2026-10-31-swarm-out-ops-registry.md §5.5; core/content_origin.py.
    _content_origin_on = os.environ.get("SWARMAI_MODE") in ("daemon", "subprocess")
    if _content_origin_on:
        from core import content_origin as _content_origin
        await _content_origin.start(backend_port)

    # ── Hive spend into the host's usage ledger (host only) ───────────
    # Pulls each running Hive's new usage rows (core/usage_hive_sync), every 15 min.
    if backend_mode != "hive":
        from core.usage_hive_sync import run_loop as _usage_hive_loop
        asyncio.create_task(_usage_hive_loop(), name="usage-hive-sync")

    # ── Canvas Layer-2 workspace surface watcher (daemon/hive only) ───
    # Surfaces author-agnostic writes (sub-agent/CLI/hook — SDK-sidechain-
    # filtered from Layer-1) onto the sole streaming session's live SSE stream.
    # Additive to Layer-1 per-tool emit + pipeline-finish sweep_run_changes.
    if backend_mode in ("daemon", "hive"):
        asyncio.create_task(_start_workspace_surface_watcher())

    # ── Pipeline-run worktree sweep at startup (daemon/hive only, §8.3) ──
    # After a restart nothing else would release worktrees of runs that ended while
    # the daemon was down. Deferred + off-loop; a failure is logged, never fatal.
    if backend_mode in ("daemon", "hive"):
        async def _startup_run_recovery() -> None:
            # run recovery first and on its own: the worktree sweep below has no
            # whole-job deadline and must not hold REFLECT back (B-e re-audit MED)
            await asyncio.sleep(20)
            try:
                # a daemon that died inside `create` left an id with no run_create
                from config import get_app_data_dir
                from core.project_registry import get_swarmws as _ws
                from core.run_protocol.engine import recover_incomplete_creates
                await asyncio.to_thread(recover_incomplete_creates, get_app_data_dir(), _ws())
            except Exception:  # noqa: BLE001
                logger.warning("startup create recovery failed", exc_info=True)
            try:
                # a crash between a signed event and its run.json leaves a stale display
                from config import get_app_data_dir
                from core.project_registry import get_swarmws as _ws
                from core.run_protocol.engine import repair_stale_projections
                await asyncio.to_thread(repair_stale_projections, get_app_data_dir(), _ws())
            except Exception:  # noqa: BLE001
                logger.warning("startup projection repair failed", exc_info=True)
            try:
                # REFLECT's queue is daemon memory: requeue every signed-reflecting run
                from core.project_registry import get_swarmws
                from core.run_protocol.reflect_job import reschedule_reflecting
                await asyncio.to_thread(reschedule_reflecting, get_swarmws())
            except Exception:  # noqa: BLE001
                logger.warning("startup REFLECT requeue failed", exc_info=True)

        async def _startup_worktree_sweep() -> None:
            await asyncio.sleep(25)
            try:
                from jobs.handlers.worktree_sweep import run_worktree_sweep
                await asyncio.to_thread(run_worktree_sweep)
            except Exception:  # noqa: BLE001
                logger.warning("startup worktree sweep failed", exc_info=True)
        asyncio.create_task(_startup_run_recovery())
        asyncio.create_task(_startup_worktree_sweep())

    # Readiness sampler (run_7e8a2030): samples DB + auth health OFF the /health
    # request path so liveness never blocks on a slow dependency. See
    # core/readiness_sampler.py + the health_check liveness/readiness split.
    from core.readiness_sampler import readiness_sampler_loop
    _readiness_task = asyncio.create_task(readiness_sampler_loop())

    # Recall-metrics batch flush (run_40091f5c): drain the in-memory latency-sample
    # rings to the recall_metrics table every ~5 min (never per-recall — the write
    # stays off the recall hot path). Fire-and-forget background task like the
    # readiness sampler above.
    from core.recall_metrics import recall_metrics_flush_loop
    _recall_metrics_task = asyncio.create_task(recall_metrics_flush_loop())

    # Independent-thread liveness heartbeat (run_5b0d6ec3): decouples "process
    # alive" from the asyncio loop/GIL so a *busy* backend (heavy CPU on the loop
    # thread) can never be misread as *dead* by the Tauri watchdog → false
    # offline. daemon/hive only — the subprocess (dev/Windows/Linux) path has no
    # launchd-managed liveness contract and the Tauri watchdog reads the file
    # only in daemon mode. See core/heartbeat.py.
    _loop_tick_task = None
    if backend_mode in ("daemon", "hive"):
        from core import heartbeat
        heartbeat.start_heartbeat()
        _loop_tick_task = asyncio.create_task(heartbeat.loop_tick_loop())

    yield
    # Shutdown
    _startup_complete = False
    logger.info("Shutting down...")
    if _ingestion_owner_task is not None and not _ingestion_owner_task.done():
        _ingestion_owner_task.cancel()
    if _ingestion["handle"] is not None:
        try:
            await asyncio.to_thread(_ingestion["handle"].stop_and_join)
            logger.info("CDF ingestion owner worker stopped")
        except Exception:
            logger.exception("CDF ingestion worker did not stop cleanly")
    if _readiness_task and not _readiness_task.done():
        _readiness_task.cancel()
        try:
            await _readiness_task
        except asyncio.CancelledError:
            pass
    # Recall-metrics: cancel the flush loop, then drain the current window ONCE so a
    # CLEAN stop persists it (only a hard crash should lose ≤5min of samples).
    if _recall_metrics_task and not _recall_metrics_task.done():
        _recall_metrics_task.cancel()
        try:
            await _recall_metrics_task
        except asyncio.CancelledError:
            pass
    try:
        from core.recall_metrics import flush_once as _recall_flush_once
        await _recall_flush_once()
    except Exception:  # noqa: BLE001 — best-effort shutdown flush, never block teardown
        logger.debug("recall metrics shutdown flush skipped", exc_info=True)
    # Stop the liveness heartbeat (run_5b0d6ec3): cancel the loop-tick task +
    # stop the independent writer thread (removes the heartbeat file so a
    # restart doesn't read a stale one from this process).
    if _loop_tick_task and not _loop_tick_task.done():
        _loop_tick_task.cancel()
        try:
            await _loop_tick_task
        except asyncio.CancelledError:
            pass
    try:
        from core import heartbeat
        heartbeat.stop_heartbeat()
    except Exception:
        logger.debug("heartbeat stop on shutdown skipped", exc_info=True)
    # Drain the SQLite connection pool (run_7e8a2030) — join aiosqlite worker
    # threads so shutdown is clean and no connection leaks.
    try:
        from database.sqlite import close_all_pools
        await close_all_pools()
        logger.info("SQLite connection pools closed")
    except Exception:
        logger.debug("pool close on shutdown skipped", exc_info=True)
    # Stop Canvas Layer-2 workspace surface watcher
    try:
        if _workspace_surface_watcher is not None:
            await _workspace_surface_watcher.stop()
            logger.info("Workspace surface watcher stopped")
    except Exception:
        logger.debug("Workspace surface watcher shutdown skipped", exc_info=True)
    if _swarm_out_bridge_task and not _swarm_out_bridge_task.done():
        _swarm_out_bridge_task.cancel()
    if _swarm_out_refresh_task and not _swarm_out_refresh_task.done():
        _swarm_out_refresh_task.cancel()
    if _content_origin_on:
        from core import content_origin as _content_origin
        await _content_origin.stop()
    if backend_mode != "hive":
        from core.swarm_out import cancel_refresh as _swarm_out_cancel_refresh
        _swarm_out_cancel_refresh()
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("In-process scheduler stopped")
    try:
        from core import swarm_out_relay
        await swarm_out_relay.manager().stop_all()
    except Exception as exc:  # noqa: BLE001 — shutdown goes on
        logger.debug("swarm_out_relay stop failed: %s", exc)
    await _svc_mgr.stop_all()
    logger.info("Managed services stopped")
    await channel_gateway.shutdown()
    logger.info("Channel gateway stopped")
    await session_registry.stop_lifecycle()
    logger.info("LifecycleManager stopped")
    await session_registry.disconnect_all()
    logger.info("All sessions disconnected")
    remove_backend_json(startup_mode=backend_mode)
    logger.info("backend.json removed (mode=%s)", backend_mode)


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI Agent Platform API - Manage agents, skills, and MCP servers",
    lifespan=lifespan,
)

# Configure rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
# In production, you should set CORS_ORIGINS environment variable
# to restrict origins to your domain(s)
cors_origins = settings.cors_origins
if settings.debug:
    # In debug mode, also allow common development origins
    cors_origins = list(set(cors_origins + [
        "http://localhost:5173",  # Vite default
        "http://localhost:3000",  # CRA default
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ]))

# Hive mode: Caddy serves frontend on the same origin, so CORS isn't strictly
# needed. But add the domain explicitly for direct API access from other tools.
# Only HTTPS — Caddy enforces TLS termination; HTTP should never reach the app.
_hive_domain = os.environ.get("HIVE_DOMAIN", "")
if _hive_domain:
    cors_origins = list(set(cors_origins + [
        f"https://{_hive_domain}",
    ]))

# Hive app-layer auth (defense-in-depth INNER layer). Pure-ASGI, mode-gated to
# SWARMAI_MODE=hive; a no-op pass-through off-hive. Added BEFORE CORSMiddleware so
# that CORS is the OUTER layer (Starlette applies middleware LIFO / last-added-outermost):
# CORS therefore short-circuits the OPTIONS preflight and can decorate HiveAuth's 401
# with CORS headers. HiveAuth also independently allow-lists OPTIONS + /health.
# MUST be pure-ASGI (not BaseHTTPMiddleware) to avoid buffering the SSE StreamingResponse
# routes (/api/chat/stream) — see middleware/hive_auth.py header.
app.add_middleware(HiveAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
        "X-Swarm-User-Action",
        # Conditional GETs (chat history, workspace tree) send If-None-Match: not a
        # CORS-safelisted header, so its preflight was rejected (400) and every
        # cached re-fetch from the webview failed (2026-10-22: 134 rejected).
        "If-None-Match",
    ],
    expose_headers=[
        "X-Request-ID",
        "ETag",          # chat history 304 caching (services/chat.ts)
        "X-Has-More",    # chat history: older turns remain (routers/chat.py)
        "X-Transcript-Rev",  # chat history: the snapshot's transcript rev (resync)
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
    max_age=600,  # Cache preflight requests for 10 minutes
)

# Outermost: a loopback connection whose Host is not a loopback name is a DNS-rebinding
# page, refused before CORS or any route sees it (middleware/loopback_host.py). Off on a Hive.
app.add_middleware(LoopbackHostMiddleware)

# Setup error handlers
setup_error_handlers(app)

# Include routers
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(experts_router, prefix="/api/experts", tags=["experts"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
# Loopback Bedrock thinking-display shim; every request is SigV4-verified
# against the user's own credentials (core/bedrock_thinking_gateway.py).
from core.bedrock_thinking_gateway import GATEWAY_PREFIX as _BEDROCK_GW_PREFIX
from core.bedrock_thinking_gateway import router as bedrock_gateway_router
app.include_router(bedrock_gateway_router, prefix=_BEDROCK_GW_PREFIX, include_in_schema=False)
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(chat_threads_router, prefix="/api", tags=["chat-threads"])
app.include_router(workspace_router, prefix="/api/workspace", tags=["workspace"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(plugins_router, prefix="/api/plugins", tags=["plugins"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])

from routers.ingestion_wake import router as ingestion_wake_router
app.include_router(ingestion_wake_router)
app.include_router(channels_router, prefix="/api/channels", tags=["channels"])
app.include_router(system_router, prefix="/api/system", tags=["system"])
app.include_router(aws_account_router, prefix="/api/aws-account", tags=["aws-account"])
app.include_router(todos_router, prefix="/api/todos", tags=["todos"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(workspace_config_router, prefix="/api/workspaces", tags=["workspace-config"])
app.include_router(workspace_api_router, prefix="/api", tags=["workspace-api"])
app.include_router(projects_router, prefix="/api", tags=["projects"])
app.include_router(tscc_router, prefix="/api", tags=["tscc"])
app.include_router(autonomous_jobs_router, prefix="/api/autonomous-jobs", tags=["autonomous-jobs"])
app.include_router(pipelines_router, prefix="/api/pipelines", tags=["pipelines"])
app.include_router(pollinate_router, prefix="/api/pollinate", tags=["pollinate"])
app.include_router(jobs_router, tags=["jobs"])  # prefix already set in router
app.include_router(artifacts_router, prefix="/api", tags=["artifacts"])
app.include_router(escalations_router, tags=["escalations"])
app.include_router(attention_router, tags=["attention"])  # prefix /api/attention set in router
app.include_router(ingestion_router, tags=["ingestion"])  # prefix /api/ingestion set in router
app.include_router(ingestion_redact_router, tags=["ingestion"])
app.include_router(cognition_health_router, tags=["ingestion"])
app.include_router(cognition_level_router, tags=["ingestion"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
app.include_router(hive_router, prefix="/api/hive", tags=["hive"])
# A Hive's own Brains (install endpoint + list; 404 off a Hive), design 2026-10-28 §6
from routers.hive_brains import router as hive_brains_router  # noqa: E402
app.include_router(hive_brains_router, prefix="/api/hive", tags=["hive"])
from routers.swarm_out import router as swarm_out_router  # noqa: E402
app.include_router(swarm_out_router, prefix="/api/swarm-out", tags=["swarm-out"])
from routers.swarm_out_remote import router as swarm_out_remote_router  # noqa: E402
# a dispatched Hive session opened in a tab (docs/design/2026-10-29-swarm-out-hive-session-tab.md)
app.include_router(swarm_out_remote_router, prefix="/api/swarm-out/remote", tags=["swarm-out"])
# the usage view (docs/design/2026-10-29-usage-accounting.md §5, §11c)
from routers.usage import router as usage_router  # noqa: E402
app.include_router(usage_router, tags=["usage"])  # prefix /api/usage set in router
app.include_router(apps_router, prefix="/api/apps", tags=["apps"])

# Memory compliance router (no prefix — router defines /api internally)
from routers.memory import router as memory_router
app.include_router(memory_router, tags=["memory"])

# Code Intelligence API
from routers.code_intel import router as code_intel_router
app.include_router(code_intel_router, prefix="/api/code-intel", tags=["code-intel"])

# OS Eval API
from routers.eval import router as eval_router
app.include_router(eval_router, prefix="/api/eval", tags=["eval"])

# DDD Brain Hub API (read-only projection over ddd_paths + parse_entries + git)
from routers.ddd_brain import router as ddd_brain_router
app.include_router(ddd_brain_router, prefix="/api/ddd", tags=["ddd-brain"])

from routers.library_api import router as library_router
app.include_router(library_router, tags=["library"])  # prefix set in router
from routers.canvas_changes import router as canvas_changes_router
app.include_router(canvas_changes_router, tags=["canvas"])  # prefix set in router

# Recall metrics visibility (Run 3): read-only count/p50/p95 by context+domain.
from routers.recall_metrics_api import router as recall_metrics_router
app.include_router(recall_metrics_router, tags=["recall-metrics"])  # prefix set in router

from routers.community_api import router as community_router
app.include_router(community_router, tags=["community"])  # prefix set in router

# Register development-only router when DEBUG=true
if settings.debug:
    from routers.dev import router as dev_router
    app.include_router(dev_router, prefix="/api/dev", tags=["dev"])


@app.get("/health")
async def health_check():
    """Health check endpoint.
    
    Returns healthy only after the lifespan startup has completed.
    This prevents race conditions where the frontend tries to load
    resources before they're ready.
    """
    # Check runtime flag - this is set after lifespan startup completes
    if not _startup_complete:
        return {
            "status": "initializing",
            "version": settings.app_version,
            "sdk": "claude-agent-sdk",
            "sdk_version": _sdk_version,
            "cli_version": _cli_version,
            # boto3 is not pre-warmed yet during startup — never call STS here.
            "auth": "unknown",
        }
    
    # PE Review Finding #5: Use property directly, not hasattr
    pending_hooks = (
        session_registry.hook_executor.pending_count
        if session_registry.hook_executor
        else 0
    )

    # LIVENESS / READINESS SEPARATION (run_7e8a2030): the DB + auth checks used to
    # run HERE on the request critical path (asyncio.gather with 2s/1s caps). Under
    # executor-thread-pool starvation (unpooled aiosqlite → 20+ threads) even a
    # 2s-capped wait_for could not get SCHEDULED, so the /health round-trip blew past
    # the Rust watchdog's 3s budget → false "Backend offline" + disabled inputs, while
    # the daemon was alive. Fix: liveness (this handler) does ZERO awaited I/O — it
    # reads a snapshot the background readiness sampler maintains off the request path
    # (core/readiness_sampler.py). The dependency signal still reaches the frontend
    # banner via db_healthy/auth below; it just no longer GATES liveness or adds
    # latency. A stale snapshot (sampler wedged) reports "unknown", never a frozen value.
    from core.readiness_sampler import readiness_cache
    _readiness = readiness_cache.snapshot()
    _db_ready = _readiness["db_healthy"]  # True | False | None(unknown)
    auth_result = _readiness["auth"]

    if _db_ready is None:
        # Not yet sampled OR sampler stale → unknown. Liveness stays healthy (the
        # process IS serving this request); we just can't assert DB state.
        db_healthy: Any = "unknown"
    elif _db_ready is False:
        db_healthy = False
    else:
        db_healthy = True

    if isinstance(auth_result, str) and auth_result in ("valid", "expired", "unknown"):
        auth_status = auth_result
    else:
        # TimeoutError / any exception / unexpected value → unknown (fail-open).
        auth_status = "unknown"

    # status="healthy" unless readiness SAMPLED the DB as genuinely down (False).
    # "unknown" (not-yet-sampled or stale snapshot) stays healthy — the process IS
    # serving this request, and a transient sampler gap must not flip the UI to
    # "backend not ready". Only a confirmed DB-down degrades. Note: the DB check no
    # longer runs on THIS request path (readiness is sampled off-path), so a slow
    # DB can never drag /health latency — that severing is the offline-flap fix.
    status = "healthy" if db_healthy is not False else "degraded"

    # P3: Expose channel gateway state so monitoring can detect
    # "healthy but Slack is down" (silent failure of deferred startup)
    gw_state = channel_gateway.startup_state

    return {
        "status": status,
        "version": settings.app_version,
        "sdk": "claude-agent-sdk",
        "sdk_version": _sdk_version,
        "cli_version": _cli_version,
        "pending_hook_tasks": pending_hooks,
        "boot_id": _boot_id,
        "db_healthy": db_healthy,
        "channel_gateway": gw_state,
        "auth": auth_status,
    }


@app.get("/api/system/verify-import")
async def verify_import(module: str):
    """Check if a module is importable in this binary. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1 to prevent arbitrary import in
    normal operation.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"available": False, "error": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}
    try:
        __import__(module)
        return {"available": True, "module": module}
    except ImportError as e:
        return {"available": False, "module": module, "error": str(e)}


@app.get("/api/system/verify-data")
async def verify_data(path: str):
    """Check if a bundled data file/dir exists. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1. Path traversal blocked.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"exists": False, "detail": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}
    if ".." in path or path.startswith("/"):
        return {"exists": False, "detail": "invalid path"}

    import sys as _sys
    # Check in _MEIPASS (PyInstaller) or relative to backend dir
    bases = []
    if getattr(_sys, "frozen", False):
        bases.append(Path(_sys._MEIPASS))
    bases.append(Path(__file__).resolve().parent)

    for base in bases:
        target = base / path
        if target.exists():
            kind = "directory" if target.is_dir() else "file"
            return {"exists": True, "path": str(target), "detail": kind}
    return {"exists": False, "path": path, "detail": f"not found in {[str(b) for b in bases]}"}


@app.get("/api/system/verify-native")
async def verify_native(path: str):
    """Check if a native extension is loadable. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1. The probe loads native code (seconds
    when cold), so it runs in a worker thread, never on the event loop.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"loadable": False, "detail": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}
    return await asyncio.to_thread(_verify_native_probe, path)


def _verify_native_probe(path: str) -> dict:
    import sqlite3
    # path format: "sqlite_vec/vec0" (without .dylib suffix)
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] == "sqlite_vec":
        try:
            import sqlite_vec
            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            version = conn.execute("select vec_version()").fetchone()[0]
            conn.close()
            return {"loadable": True, "detail": f"sqlite-vec {version}"}
        except Exception as e:
            return {"loadable": False, "detail": str(e)}
    # tree_sitter/parse: a FUNCTIONAL AST probe, not a bare import. verify-import
    # only does __import__('tree_sitter'), which SUCCEEDS even when the AST path is
    # broken (run_2e46f2af: the old get_parser returned an old-ABI object whose
    # .parse(bytes) raised — import fine, parsing dead). So the real gate must
    # construct a parser and confirm a node comes back — mirroring the sqlite_vec
    # load-and-call probe above.
    if len(parts) == 2 and parts[0] == "tree_sitter":
        try:
            import tree_sitter
            from core.code_intel.grammars import get_language
            parser = tree_sitter.Parser(get_language("python"))
            tree = parser.parse(b"def _probe():\n    return 1\n")
            root_type = tree.root_node.type
            if root_type != "module":
                return {"loadable": False,
                        "detail": f"tree-sitter parsed but root type is {root_type!r}, expected 'module'"}
            return {"loadable": True, "detail": f"tree-sitter AST functional (root={root_type})"}
        except Exception as e:
            return {"loadable": False, "detail": f"tree-sitter AST non-functional: {e}"}
    return {"loadable": False, "detail": f"unknown native extension: {path}"}


@app.get("/api/system/capabilities")
async def get_capabilities():
    """Report all capability flags for this binary. Shows dev/prod divergence at a glance.

    Intentionally NOT gated behind SWARMAI_VERIFY_BUILD — this endpoint
    is always available for runtime diagnostics (e.g., Titus reports a
    broken feature → curl capabilities to see what's degraded). The
    verify-import/verify-data/verify-native endpoints are gated because
    they accept arbitrary input; this one has no parameters.
    """
    caps = {}

    # sqlite_vec
    try:
        from core.vec_db import VEC_AVAILABLE
        caps["sqlite_vec"] = VEC_AVAILABLE
    except ImportError:
        caps["sqlite_vec"] = False

    # psutil
    try:
        import psutil  # noqa: F401
        caps["psutil"] = True
    except ImportError:
        caps["psutil"] = False

    # Slack
    try:
        import slack_bolt  # noqa: F401
        caps["slack_bolt"] = True
    except ImportError:
        caps["slack_bolt"] = False

    # Key local modules
    for mod in ["core.recall_engine", "core.manifest_loader", "core.ingestion.worker"]:
        try:
            __import__(mod)
            caps[mod.split(".")[-1]] = True
        except ImportError:
            caps[mod.split(".")[-1]] = False

    # Frozen mode
    import sys as _sys
    caps["frozen"] = getattr(_sys, "frozen", False)
    caps["mode"] = os.environ.get("SWARMAI_MODE", "unknown")

    return {"capabilities": caps}


@app.get("/api/system/mode")
async def get_system_mode():
    """Return the backend's running mode (daemon or hive)."""
    uptime = time.monotonic() - _backend_start_monotonic if _backend_start_monotonic else 0
    return {
        "mode": _detect_run_mode(),
        "pid": os.getpid(),
        "port": _detect_backend_port(),
        "uptime_seconds": round(uptime, 1),
    }


@app.post("/shutdown")
async def shutdown():
    """Graceful shutdown endpoint - disconnects all Claude SDK clients.

    Blocked in daemon and hive modes — these run 24/7 background services
    (Slack channels, scheduled jobs) that must survive app window close.
    Allowed in dev and subprocess modes (no persistent background services).
    """
    mode = _detect_run_mode()
    if mode in ("daemon", "hive"):
        logger.warning("Shutdown endpoint blocked in %s mode", mode)
        return JSONResponse(
            status_code=403,
            content={"status": "forbidden", "reason": f"shutdown disabled in {mode} mode"},
        )
    logger.info("Shutdown endpoint called - disconnecting all clients")
    t0 = time.monotonic()
    try:
        # Timeout 8s — 2s shorter than Tauri's 10s curl timeout.
        # Ensures normal path finishes before force-kill.
        await asyncio.wait_for(session_registry.disconnect_all(), timeout=8.0)
    except asyncio.TimeoutError:
        logger.warning("disconnect_all timed out after 8s — proceeding with shutdown")
    elapsed = time.monotonic() - t0
    logger.info("Shutdown endpoint completed in %.2fs", elapsed)
    return {"status": "shutting_down"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
