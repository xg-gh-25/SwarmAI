"""Keep the CLI's model-access cache fresh off the chat spawn path.

The Claude CLI remembers which models the account may use in
``<CLAUDE_CONFIG_DIR or ~/.claude>/cache/model-access.json``. An entry is trusted for
15 minutes after it was written; a start that finds it older probes the provider
before its ``initialize`` handshake answers, inside ``session_unit._spawn``'s
``_spawn_lock``. Measured with CLI 2.1.301 on Bedrock: a chat-shaped handshake took
1765 ms on an expired entry and 344 ms right after (a 16-minute-old entry cost 1.6-2.8 s
on a sandbox daemon's cold starts, against 0.38 s fresh).

Two facts of the cache decide the design (both measured):

- A start that finds the entry fresh does not rewrite it: freshness runs out 15 minutes
  after the last probe, whatever starts came between. So a warm-up runs just after the
  entry expires (``AFTER_EXPIRY_S``), read from the file, not on a fixed cadence.
- The entry's key hashes the CLI version, the provider's environment (region, base URL,
  AWS profile / key variables) and the settings' ``awsCredentialExport``. The job
  executor's CLIs (boto3 keys in their environment) write other keys than chat. So the
  warm-up starts the CLI with the last chat spawn's settings, setting sources, cwd,
  model, CLI path and environment, and learns the chat key as the one entry its own
  handshake rewrote.

It runs while the person is active: a chat turn or a tab add/close/switch in the last
``ACTIVE_WINDOW_S``. A warm-up is one SDK connect (the ``initialize`` handshake, no
query, so no transcript) and close, capped at ``HANDSHAKE_CAP_S``; it never holds
``_spawn_lock``, and holds ``_env_lock`` only to configure and copy the environment.
After a failure, or a handshake that rewrote nothing while nothing is fresh (a provider
without this cache), the next one waits ``RETRY_S``: it never loops.

Public: ``note_activity()``, ``note_spawn(options, config)`` (both synchronous, no IO),
``stop()``; ``WARMER`` is the instance they drive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

#: how long the CLI trusts an "ok" entry (CLI 2.1.x)
FRESH_S = 900.0
#: a warm-up runs this long after the entry expired
AFTER_EXPIRY_S = 2.0
#: active while there was a chat turn or a tab change this recently
ACTIVE_WINDOW_S = 1800.0
#: the file is re-read at least this often (a chat spawn may have re-probed meanwhile)
POLL_S = 60.0
#: after a failed warm-up, or one that changed nothing while nothing is fresh
RETRY_S = 900.0
#: two warm-ups are at least this far apart
MIN_GAP_S = 60.0
#: one warm-up handshake, connect to ready
HANDSHAKE_CAP_S = 60.0
_CLOSE_CAP_S = 10.0


def cache_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "cache" / "model-access.json"


def read_entries(path: Path) -> dict[str, float]:
    """``{key: newest write time in seconds}`` of the model-access file; {} when the
    file is missing or not in the expected shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, float] = {}
        for key, models in (data.get("entries") or {}).items():
            stamps = [m.get("at") for m in (models or {}).values() if isinstance(m, dict)]
            stamps = [a for a in stamps if isinstance(a, (int, float))]
            if stamps:
                out[str(key)] = max(stamps) / 1000.0
        return out
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


@dataclass
class WarmTemplate:
    """What of a chat spawn's options a warm-up needs to compute the same cache key."""
    model: Optional[str]
    settings: Optional[str]
    setting_sources: Optional[list]
    cwd: Optional[str]
    cli_path: Optional[str]
    env: dict = field(default_factory=dict)

    @classmethod
    def from_options(cls, options: Any) -> "WarmTemplate":
        env = getattr(options, "env", None)
        sources = getattr(options, "setting_sources", None)
        cwd = getattr(options, "cwd", None)
        cli = getattr(options, "cli_path", None)
        return cls(
            model=getattr(options, "model", None),
            settings=getattr(options, "settings", None),
            setting_sources=list(sources) if sources is not None else None,
            cwd=str(cwd) if cwd else None,
            cli_path=str(cli) if cli else None,
            env=dict(env) if isinstance(env, dict) else {},
        )


def _default_client_factory(options: Any) -> Any:
    from claude_agent_sdk import ClaudeSDKClient
    return ClaudeSDKClient(options=options)


def _budget_allows() -> bool:
    try:
        from . import session_registry
        from .resource_monitor import resource_monitor
        router = session_registry.session_router
        alive = (router.alive_count + router.staged_spawn_count()) if router else 0
        return bool(resource_monitor.spawn_budget(alive_count=alive).can_spawn)
    except Exception:  # noqa: BLE001 — unknown budget: no warm-up (it is never needed)
        logger.debug("model_access_warmer: spawn budget unreadable", exc_info=True)
        return False


async def _run_io(fn: Callable[..., Any], *args: Any) -> Any:
    from . import executors
    return await executors.run_in("io", fn, *args)


class ModelAccessWarmer:
    def __init__(self, *, clock: Callable[[], float] = time.time,
                 path: Callable[[], Path] = cache_path,
                 client_factory: Callable[[Any], Any] = _default_client_factory,
                 budget: Callable[[], bool] = _budget_allows,
                 run_io: Callable[..., Awaitable[Any]] = _run_io,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._clock = clock
        self._path = path
        self._client_factory = client_factory
        self._budget = budget
        self._run_io = run_io
        self._sleep = sleep
        self._template: Optional[WarmTemplate] = None
        self._config: Any = None
        self._last_activity: float = float("-inf")
        self._key: Optional[str] = None
        self._not_before: float = float("-inf")
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    # ── fed by the chat path (synchronous, no IO) ────────────────────────
    def note_activity(self) -> None:
        self._last_activity = self._clock()
        self._ensure_task()

    def note_spawn(self, options: Any, config: Any = None) -> None:
        try:
            self._template = WarmTemplate.from_options(options)
        except Exception:  # noqa: BLE001 — a template we cannot read: no warm-up
            logger.debug("model_access_warmer: template unreadable", exc_info=True)
            return
        if config is not None:
            self._config = config
        self.note_activity()

    def stop(self) -> None:
        """Shutdown: cancel the loop; no later activity starts it again."""
        self._stopped = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    def _ensure_task(self) -> None:
        if (self._stopped or self._template is None
                or (self._task is not None and not self._task.done())):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._run(), name="model-access-warmer")

    # ── the loop ─────────────────────────────────────────────────────────
    def _active(self, now: float) -> bool:
        return now - self._last_activity <= ACTIVE_WINDOW_S

    def next_due(self, entries: dict[str, float], now: float) -> float:
        """When the next warm-up should run, from the file's entries."""
        if self._key is not None and self._key in entries:
            due = entries[self._key] + FRESH_S + AFTER_EXPIRY_S
        else:
            fresh = [at for at in entries.values() if now - at < FRESH_S]
            due = (min(fresh) + FRESH_S + AFTER_EXPIRY_S) if fresh else now
        return max(due, self._not_before)

    async def _run(self) -> None:
        try:
            while self._template is not None:
                now = self._clock()
                if not self._active(now):
                    logger.debug("model_access_warmer: inactive, stopping")
                    return
                entries = await self._run_io(read_entries, self._path())
                due = self.next_due(entries, now)
                if due > now:
                    await self._sleep(min(due - now, POLL_S))
                    continue
                await self.warm_once(entries)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a warmer bug must never reach chat
            logger.warning("model_access_warmer: loop failed", exc_info=True)

    async def warm_once(self, before: dict[str, float]) -> None:
        """One warm-up handshake, then learn the chat key from what it rewrote."""
        now = self._clock()
        self._not_before = now + MIN_GAP_S
        if not self._budget():
            logger.info("model_access_warm skipped — spawn budget refuses a spawn")
            self._not_before = now + RETRY_S
            return
        t0 = time.perf_counter()
        try:
            options = await self._options()
            await self._handshake(options)
        except Exception as exc:  # noqa: BLE001 — logged; chat is unaffected
            logger.warning("model_access_warm failed after %.0f ms: %s: %s",
                           (time.perf_counter() - t0) * 1000, type(exc).__name__, exc)
            self._not_before = now + RETRY_S
            return
        ms = (time.perf_counter() - t0) * 1000
        after = await self._run_io(read_entries, self._path())
        changed = [k for k, at in after.items() if before.get(k) != at]
        if len(changed) == 1:
            self._key = changed[0]
        elif not changed and not any(self._clock() - at < FRESH_S for at in after.values()):
            self._not_before = now + RETRY_S
        logger.info("model_access_warm ms=%.0f reprobed=%d key=%s", ms, len(changed),
                    (self._key or "unknown")[:8])

    async def _options(self) -> Any:
        """The warm-up's options: the chat template with the environment configured and
        copied under ``_env_lock`` (released before the handshake)."""
        from claude_agent_sdk import ClaudeAgentOptions
        from .claude_environment import _configure_claude_environment, _env_lock
        tpl = self._template
        async with _env_lock:
            if self._config is not None:
                _configure_claude_environment(self._config)
            env = dict(os.environ)
        env.update(tpl.env)
        kwargs: dict[str, Any] = dict(model=tpl.model, settings=tpl.settings,
                                      setting_sources=tpl.setting_sources, cwd=tpl.cwd,
                                      env=env, system_prompt="", max_turns=1,
                                      strict_mcp_config=True)
        if tpl.cli_path:
            kwargs["cli_path"] = tpl.cli_path
        return ClaudeAgentOptions(**kwargs)

    async def _handshake(self, options: Any) -> None:
        client = self._client_factory(options)
        try:
            await asyncio.wait_for(client.connect(), timeout=HANDSHAKE_CAP_S)
        finally:
            # the process object, not its pid: after the close reaps it a pid may be reused
            proc = _client_process(client)
            try:
                await asyncio.wait_for(client.disconnect(), timeout=_CLOSE_CAP_S)
            except Exception:  # noqa: BLE001 — the kill below covers a close that failed
                logger.debug("model_access_warmer: close failed", exc_info=True)
            _kill_if_running(proc)


def _client_process(client: Any) -> Any:
    return getattr(getattr(client, "_transport", None), "_process", None)


def _kill_if_running(proc: Any) -> None:
    """SIGKILL the warm-up CLI if its close left it running (returncode still None)."""
    if proc is None or getattr(proc, "returncode", 0) is not None:
        return
    try:
        from . import kill_audit
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            kill_audit.record([pid], "model-access warm-up")
        proc.kill()
    except ProcessLookupError:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("model_access_warmer: kill failed", exc_info=True)


WARMER = ModelAccessWarmer()


def note_activity() -> None:
    """A chat turn or a tab change: the person is active (sync, no IO)."""
    try:
        WARMER.note_activity()
    except Exception:  # noqa: BLE001 — never on the chat path
        logger.debug("model_access_warmer: note_activity failed", exc_info=True)


def note_spawn(options: Any, config: Any = None) -> None:
    """A chat CLI was spawned with *options* (sync, no IO)."""
    try:
        WARMER.note_spawn(options, config)
    except Exception:  # noqa: BLE001
        logger.debug("model_access_warmer: note_spawn failed", exc_info=True)


def stop() -> None:
    WARMER.stop()
