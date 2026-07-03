"""Job handler for the runtime session-health probe (run_f646b175).

Wires the real runtime fetchers (localhost daemon /health + /sessions/streaming-state,
resource_monitor RSS, tree_cpu_seconds, daemon log tail) into the pure-logic
``core.session_health_probe.session_health_probe`` and, on a RED result, sends a
Slack notification via s_notify.

Scheduled every ~15 min (system_jobs.py). Zero-LLM, deterministic.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_DAEMON_BASE = os.environ.get("SWARMAI_HEALTH_BASE", "http://127.0.0.1:18321")


def _resolve_log_path() -> Path:
    """The REAL daemon log path, from the single source of truth (config).

    Was previously hardcoded to a nonexistent ``daemon.log`` → _read_log()
    always returned "" → no_unrecovered_events scanned nothing (silent
    fail-safe). Fail-safe fallback to backend-daemon.log if the import fails.
    """
    try:
        from config import get_log_file_path
        return get_log_file_path()
    except Exception:
        return Path.home() / ".swarm-ai" / "logs" / "backend-daemon.log"


_LOG_PATH = _resolve_log_path()
_LOG_TAIL_BYTES = 200_000  # ~last 200KB window


def _http_json(path: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(f"{_DAEMON_BASE}{path}", timeout=timeout) as r:
        import json
        return json.loads(r.read().decode("utf-8"))


def _fetch_health() -> dict:
    return _http_json("/health")


def _fetch_streaming() -> list[dict]:
    """Normalize the streaming-state endpoint into a list[dict], each item
    carrying its session_id.

    The live endpoint returns a session-KEYED dict:
    ``{"sessions": {session_id: {state, streaming, pid, ...}}}`` — the
    session_id is the KEY, not a field. The core probe iterates the returned
    value and calls ``.get()`` on each item, so we MUST hand it a list of
    dicts (with session_id merged in from the key), not the raw dict — else it
    iterates the dict's string keys and crashes into its fail-safe branch
    (the bug that silently disabled the wedged-session check). Also still
    handles the legacy {"sessions": [...]} and bare-list shapes.
    """
    data = _http_json("/api/chat/sessions/streaming-state")
    sessions = data.get("sessions", data) if isinstance(data, dict) else data
    if isinstance(sessions, dict):
        # dict-of-dicts → list, injecting session_id from the key. The KEY is the
        # authoritative session id, so it wins over any stray same-named field in
        # val (spread val FIRST, then session_id — later key wins).
        return [{**val, "session_id": sid} for sid, val in sessions.items()
                if isinstance(val, dict)]
    return sessions if isinstance(sessions, list) else []


def _fetch_rss() -> float:
    """Total RSS (MB) of the daemon process tree (backend + CLI + MCP children).

    The handler runs INSIDE the daemon, so os.getpid() is the daemon root.
    process_tree_rss returns bytes (0 on failure → fail-safe: 0 < threshold
    passes, matching the probe's no-alarm-on-unreadable philosophy).
    """
    import os
    from core.resource_monitor import resource_monitor
    rss_bytes = resource_monitor.process_tree_rss(os.getpid())
    return rss_bytes / (1024 * 1024)


def _cpu_sampler(pid: int) -> Optional[float]:
    from core.resource_monitor import resource_monitor
    return resource_monitor.tree_cpu_seconds(pid)


def _read_log() -> str:
    try:
        if not _LOG_PATH.exists():
            return ""
        size = _LOG_PATH.stat().st_size
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            if size > _LOG_TAIL_BYTES:
                f.seek(size - _LOG_TAIL_BYTES)
            return f.read()
    except Exception:
        return ""  # fail-safe: unreadable log → probe treats as no-progress-unknown


def _deployed_commit() -> Optional[str]:
    try:
        vp = Path.home() / ".swarm-ai" / "daemon" / ".version"
        if vp.exists():
            return vp.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def run_session_health_probe(
    dry_run: bool = False,
    *,
    expected_commit: Optional[str] = None,
    notifier: Optional[Callable[..., dict]] = None,
    probe_fn: Optional[Callable[..., object]] = None,
) -> dict:
    """Run the runtime probe; on RED, notify Slack. Returns a JobResult dict.

    Args:
        dry_run: if True, never send a notification (still returns the result).
        expected_commit: if set, the probe checks deployed==expected.
        notifier: injected send fn (test seam); defaults to s_notify.
        probe_fn: injected probe (test seam); defaults to the real probe wired
            to the live daemon fetchers.
    """
    from core.session_health_probe import session_health_probe

    probe = probe_fn or session_health_probe
    try:
        result = probe(
            health_fetcher=_fetch_health,
            streaming_fetcher=_fetch_streaming,
            rss_fetcher=_fetch_rss,
            cpu_sampler=_cpu_sampler,
            log_reader=_read_log,
            expected_commit=expected_commit,
            deployed_commit_fetcher=_deployed_commit if expected_commit else None,
        ) if probe_fn is None else probe()
    except Exception as e:
        logger.error("session-health probe crashed: %s", e)
        return {"status": "error", "reason": f"probe crashed: {type(e).__name__}: {e}"}

    checks = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in result.checks]
    failed = [c for c in checks if not c["ok"]]

    # M1: dedup — only notify when the set of failed checks CHANGES (or on a
    # red→green recovery). Prevents a 15-min alarm storm on a sustained issue
    # (notification fatigue → humans mute → probe effectively dead).
    notified = False
    if not dry_run:
        fingerprint = ",".join(sorted(c["name"] for c in failed))  # "" when green
        prev = _read_alert_state()
        if result.red and fingerprint != prev:
            send = notifier or _default_notifier
            title = "🔴 SwarmAI runtime health DEGRADED"
            msg = result.summary + "\n" + "\n".join(
                f"- {c['name']}: {c['detail']}" for c in failed)
            try:
                send(message=msg, title=title, channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("session-health notify failed: %s", e)
        elif not result.red and prev:
            # red → green: one recovery message, then clear state.
            send = notifier or _default_notifier
            try:
                send(message="✅ SwarmAI runtime health RECOVERED",
                     title="SwarmAI runtime health", channels=["slack"])
                notified = True
            except Exception as e:
                logger.error("session-health recovery notify failed: %s", e)
        _write_alert_state(fingerprint)

    return {
        "status": "degraded" if result.red else "healthy",
        "probe_status": result.status,
        "summary": result.summary,
        "checks": checks,
        "failed": [c["name"] for c in failed],
        "notified": notified,
    }


_ALERT_STATE = Path.home() / ".swarm-ai" / "logs" / ".session_health_alert"


def _read_alert_state() -> str:
    try:
        return _ALERT_STATE.read_text(encoding="utf-8").strip() if _ALERT_STATE.exists() else ""
    except Exception:
        return ""


def _write_alert_state(fingerprint: str) -> None:
    try:
        _ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        _ALERT_STATE.write_text(fingerprint, encoding="utf-8")
    except Exception:
        pass  # state is best-effort; failure just means no dedup this tick


def _default_notifier(**kwargs) -> dict:
    from skills.s_notify.notify import send_notification
    return send_notification(**kwargs)
