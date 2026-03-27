"""Service Manager — lifecycle management for sidecar services.

Manages external subprocess services (e.g. Slack bot) that run alongside
the SwarmAI backend.  Services start after backend startup completes and
stop on backend shutdown.  A background health monitor restarts crashed
services automatically.

Design:
- Services are discovered from ``SwarmWS/Services/*/service.json``
- Each service.json declares: name, command, enabled, restart_policy
- The backend writes ``~/.swarm-ai/backend.port`` so services can
  discover the backend without lsof scanning
- Services receive SIGTERM on shutdown (graceful) with a 5s fallback to SIGKILL
- Health monitor runs every 30s, only restarts if restart_policy == "always"
- All failures are non-fatal — a broken service never blocks the backend

Public API:
    service_manager = ServiceManager()
    await service_manager.start_all(workspace_path, backend_port)
    await service_manager.stop_all()
    service_manager.get_status() -> list[dict]
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Port file path — written by backend, read by services
PORT_FILE = Path.home() / ".swarm-ai" / "backend.port"

# Health check interval
_HEALTH_CHECK_INTERVAL = 30  # seconds

# Grace period before SIGKILL after SIGTERM
_SHUTDOWN_GRACE_SECONDS = 5

# Cooldown between restart attempts to prevent thrashing
_RESTART_COOLDOWN_SECONDS = 10

# Max consecutive crashes before giving up
_MAX_CRASH_COUNT = 5


class ManagedService:
    """A single managed subprocess service."""

    __slots__ = (
        "name", "command", "cwd", "env", "restart_policy",
        "process", "crash_count", "last_start_time", "enabled",
    )

    def __init__(
        self,
        name: str,
        command: list[str],
        cwd: str,
        env: Optional[dict] = None,
        restart_policy: str = "always",
        enabled: bool = True,
    ):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self.restart_policy = restart_policy  # "always" | "never"
        self.enabled = enabled
        self.process: Optional[subprocess.Popen] = None
        self.crash_count = 0
        self.last_start_time: float = 0

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

    def to_status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "running": self.is_running,
            "pid": self.pid,
            "crash_count": self.crash_count,
            "restart_policy": self.restart_policy,
            "uptime_seconds": (
                round(time.monotonic() - self.last_start_time)
                if self.is_running else 0
            ),
        }


class ServiceManager:
    """Manages sidecar service subprocesses.

    Discovers services from ``{workspace}/Services/*/service.json``,
    starts them after the backend is ready, monitors health, and
    stops them on shutdown.
    """

    def __init__(self) -> None:
        self._services: list[ManagedService] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
        self._backend_port: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_all(self, workspace_path: str, backend_port: int) -> None:
        """Discover and start all enabled services.

        Args:
            workspace_path: Path to SwarmWS (e.g. ``~/.swarm-ai/SwarmWS``)
            backend_port: The port the backend is listening on
        """
        self._backend_port = backend_port
        self._write_port_file(backend_port)

        # Discover services
        services_dir = Path(workspace_path) / "Services"
        if not services_dir.is_dir():
            logger.info("No Services/ directory — skipping service discovery")
            return

        for service_dir in sorted(services_dir.iterdir()):
            config_file = service_dir / "service.json"
            if not config_file.is_file():
                continue
            try:
                svc = self._load_service(config_file)
                if svc:
                    self._services.append(svc)
            except Exception as exc:
                logger.warning(
                    "Failed to load service from %s: %s", config_file, exc
                )

        if not self._services:
            logger.info("No services discovered")
            return

        # Start each enabled service
        for svc in self._services:
            if svc.enabled:
                self._start_service(svc)

        started = [s.name for s in self._services if s.is_running]
        skipped = [s.name for s in self._services if not s.enabled]
        logger.info(
            "Services started: %s%s",
            started or "(none)",
            f" | skipped (disabled): {skipped}" if skipped else "",
        )

        # Start health monitor
        self._running = True
        self._monitor_task = asyncio.create_task(self._health_monitor())

    async def stop_all(self) -> None:
        """Stop all running services and clean up."""
        self._running = False

        # Cancel health monitor
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop services in reverse order
        for svc in reversed(self._services):
            self._stop_service(svc)

        self._services.clear()
        self._remove_port_file()
        logger.info("All services stopped, port file removed")

    def get_status(self) -> list[dict]:
        """Return status of all managed services."""
        return [svc.to_status() for svc in self._services]

    # ------------------------------------------------------------------
    # Service discovery
    # ------------------------------------------------------------------

    def _load_service(self, config_path: Path) -> Optional[ManagedService]:
        """Load a service definition from service.json.

        Expected format::

            {
                "name": "my-service",
                "command": ["./venv/bin/python", "service.py"],
                "enabled": true,
                "restart_policy": "always"
            }
        """
        with open(config_path) as f:
            config = json.load(f)

        name = config.get("name")
        command = config.get("command")
        if not name or not command:
            logger.warning("service.json missing name/command: %s", config_path)
            return None

        enabled = config.get("enabled", True)
        restart_policy = config.get("restart_policy", "always")
        cwd = str(config_path.parent)

        # Build environment: inherit current env + add backend port
        env = dict(os.environ)
        env["SWARM_BACKEND_PORT"] = str(self._backend_port or "")
        env["SWARM_BACKEND_URL"] = (
            f"http://127.0.0.1:{self._backend_port}"
            if self._backend_port else ""
        )
        # Merge any custom env from service.json
        for k, v in config.get("env", {}).items():
            env[k] = str(v)

        return ManagedService(
            name=name,
            command=command,
            cwd=cwd,
            env=env,
            restart_policy=restart_policy,
            enabled=enabled,
        )

    # ------------------------------------------------------------------
    # Subprocess lifecycle
    # ------------------------------------------------------------------

    def _start_service(self, svc: ManagedService) -> bool:
        """Start a single service subprocess. Returns True on success."""
        if svc.is_running:
            return True

        # Cooldown check
        elapsed = time.monotonic() - svc.last_start_time
        if elapsed < _RESTART_COOLDOWN_SECONDS and svc.crash_count > 0:
            logger.debug(
                "Service %s in cooldown (%.0fs since last start)",
                svc.name, elapsed,
            )
            return False

        try:
            log_dir = Path(svc.cwd) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_log = open(log_dir / "managed-stdout.log", "a")
            stderr_log = open(log_dir / "managed-stderr.log", "a")

            svc.process = subprocess.Popen(
                svc.command,
                cwd=svc.cwd,
                env=svc.env,
                stdout=stdout_log,
                stderr=stderr_log,
                # Start in new process group so SIGTERM doesn't propagate
                # from parent to child unexpectedly
                preexec_fn=os.setpgrp if sys.platform != "win32" else None,
            )
            svc.last_start_time = time.monotonic()
            logger.info(
                "Started service %s (pid=%d, cmd=%s)",
                svc.name, svc.process.pid, " ".join(svc.command),
            )
            return True

        except Exception as exc:
            logger.error("Failed to start service %s: %s", svc.name, exc)
            svc.crash_count += 1
            return False

    def _stop_service(self, svc: ManagedService) -> None:
        """Stop a service with SIGTERM, fallback to SIGKILL."""
        if not svc.process or not svc.is_running:
            return

        pid = svc.process.pid
        try:
            svc.process.terminate()  # SIGTERM
            try:
                svc.process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
                logger.info("Service %s (pid=%d) stopped gracefully", svc.name, pid)
            except subprocess.TimeoutExpired:
                svc.process.kill()  # SIGKILL
                svc.process.wait(timeout=2)
                logger.warning(
                    "Service %s (pid=%d) killed after %ds grace period",
                    svc.name, pid, _SHUTDOWN_GRACE_SECONDS,
                )
        except ProcessLookupError:
            pass  # Already dead
        except Exception as exc:
            logger.error("Error stopping service %s: %s", svc.name, exc)
        finally:
            svc.process = None

    # ------------------------------------------------------------------
    # Health monitor
    # ------------------------------------------------------------------

    async def _health_monitor(self) -> None:
        """Background loop that restarts crashed services."""
        while self._running:
            try:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

            for svc in self._services:
                if not svc.enabled or svc.restart_policy != "always":
                    continue

                if not svc.is_running and svc.process is not None:
                    # Process was started but has exited
                    exit_code = svc.process.returncode
                    svc.process = None
                    svc.crash_count += 1

                    if svc.crash_count >= _MAX_CRASH_COUNT:
                        logger.error(
                            "Service %s crashed %d times (exit=%s), "
                            "giving up — manual restart required",
                            svc.name, svc.crash_count, exit_code,
                        )
                        svc.enabled = False
                        continue

                    logger.warning(
                        "Service %s exited (code=%s, crash #%d/%d), restarting...",
                        svc.name, exit_code, svc.crash_count, _MAX_CRASH_COUNT,
                    )
                    self._start_service(svc)

    # ------------------------------------------------------------------
    # Port file
    # ------------------------------------------------------------------

    @staticmethod
    def _write_port_file(port: int) -> None:
        """Write backend port to a well-known file for service discovery."""
        try:
            PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
            PORT_FILE.write_text(str(port))
            logger.info("Wrote backend port %d to %s", port, PORT_FILE)
        except Exception as exc:
            logger.warning("Failed to write port file: %s", exc)

    @staticmethod
    def _remove_port_file() -> None:
        """Remove port file on shutdown."""
        try:
            PORT_FILE.unlink(missing_ok=True)
        except Exception:
            pass


# Module-level singleton
service_manager = ServiceManager()
