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

Before the daemon's first chat spawn (just after a restart) it starts the CLI from a
template built from the configuration the same way (``start``'s ``template_factory``,
``PromptBuilder.warm_up_template``); the first chat spawn's options replace it.

It runs while the person is active: a chat turn or a tab add/close/switch in the last
``ACTIVE_WINDOW_S``. A loop that activity starts waits ``AFTER_ACTIVITY_S`` first (the
turn's own cold spawn probes meanwhile); ``start`` at boot does not wait. The time of the last one and the learned key are kept in
``state_path()`` (nothing else: no environment, setting or credential), written by the
loop when they changed, so a restart while the person is active warms at once instead
of waiting for the first chat spawn to pay the probe. A warm-up is one SDK connect (the
``initialize`` handshake, no query, so no transcript) and close, capped at
``HANDSHAKE_CAP_S``; it never holds ``_spawn_lock``, and holds ``_env_lock`` only to
configure and copy the environment. It starts only when the spawn budget admits one
more CLI, and counts as a staged spawn (``running_count()``, read by
``SessionRouter.staged_spawn_count``) until its CLI is closed; a chat spawn the budget
refuses meanwhile ends it first (``yield_to_chat``). After a failure, or a
handshake that rewrote nothing while nothing is fresh (a provider without this cache),
the next one waits ``RETRY_S``: it never loops.

Public: ``start(config, template_factory)`` (daemon startup), ``note_activity()``,
``note_spawn(options, config)`` (both synchronous, no IO), ``running_count()``,
``yield_to_chat()``, ``stop()``; ``WARMER`` is the instance they drive.
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
#: a loop started by a chat turn or a tab change waits this long before its first
#: warm-up: that turn's own cold spawn re-probes meanwhile, and a warm-up beside it
#: would probe twice and take a second place in the spawn budget
AFTER_ACTIVITY_S = 30.0
#: one warm-up handshake, connect to ready
HANDSHAKE_CAP_S = 60.0
_CLOSE_CAP_S = 10.0


def cache_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "cache" / "model-access.json"


def state_path() -> Path:
    """The warmer's own state across restarts: last activity and learned key."""
    from config import get_app_data_dir
    return get_app_data_dir() / "model_access_warm.json"


def read_state(path: Path) -> tuple[Optional[float], Optional[str]]:
    """``(last activity, learned key)`` from *path*; ``None`` for what is missing or not
    in the expected shape (an unreadable file reads as no activity)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    at, key = data.get("last_activity"), data.get("key")
    at = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None
    return at, (key if isinstance(key, str) and key else None)


def write_state(path: Path, last_activity: float, key: Optional[str]) -> None:
    """Replace *path* with the last activity and the learned key (nothing else)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"last_activity": last_activity, "key": key}), encoding="utf-8")
    os.replace(tmp, path)


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

    @classmethod
    def from_fields(cls, fields: dict) -> "WarmTemplate":
        """From ``PromptBuilder.warm_up_template``'s dict."""
        sources = fields.get("setting_sources")
        env = fields.get("env")
        return cls(
            model=fields.get("model"),
            settings=fields.get("settings"),
            setting_sources=list(sources) if sources is not None else None,
            cwd=str(fields["cwd"]) if fields.get("cwd") else None,
            cli_path=str(fields["cli_path"]) if fields.get("cli_path") else None,
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
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 state: Callable[[], Path] = state_path) -> None:
        self._clock = clock
        self._path = path
        self._client_factory = client_factory
        self._budget = budget
        self._run_io = run_io
        self._sleep = sleep
        self._state = state
        self._template: Optional[WarmTemplate] = None
        #: builds a template from the configuration while no chat spawn gave one
        self._template_factory: Optional[Callable[[], dict]] = None
        #: (last activity, key) as last written to the state file
        self._saved: Optional[tuple] = None
        #: warm-up CLIs started and not yet closed (0 or 1)
        self._running = 0
        self._config: Any = None
        self._last_activity: float = float("-inf")
        self._key: Optional[str] = None
        self._not_before: float = float("-inf")
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    # ── daemon startup ───────────────────────────────────────────────────
    async def start(self, config: Any, template_factory: Callable[[], dict]) -> None:
        """Daemon startup, after the leftover-CLI sweep: take the configuration and a
        template factory for warm-ups before the first chat spawn, read the last
        activity and the learned key a previous process wrote, and start the loop when
        the person was active within ``ACTIVE_WINDOW_S``."""
        if self._stopped:
            return
        self._config = config if config is not None else self._config
        self._template_factory = template_factory
        try:
            at, key = await self._run_io(read_state, self._state())
        except Exception:  # noqa: BLE001 — no state: as if nobody was active
            logger.debug("model_access_warmer: state unreadable", exc_info=True)
            at, key = None, None
        # a time after now (a clock set back, a damaged file) would keep the person
        # "active" for good: ignored
        if at is not None and self._last_activity < at <= self._clock():
            self._last_activity = at
        if self._key is None and key is not None:
            self._key = key
        self._saved = (at, key)
        if self._active(self._clock()):
            self._ensure_task()

    def running_count(self) -> int:
        """Warm-up CLIs started and not yet closed: counted by the spawn budget."""
        return self._running

    async def yield_to_chat(self) -> bool:
        """A chat spawn the budget refused while a warm-up runs: end the warm-up (its
        CLI is closed and killed in the handshake's ``finally``) so chat goes first.
        True when a warm-up was ended; the next activity starts the loop again."""
        task = self._task
        if not self._running or task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task}, timeout=HANDSHAKE_CAP_S)
        logger.info("model_access_warm ended for a chat spawn the budget refused")
        return True

    # ── fed by the chat path (synchronous, no IO) ────────────────────────
    def note_activity(self) -> None:
        self._last_activity = self._clock()
        if self._task is None or self._task.done():
            self._not_before = max(self._not_before, self._last_activity + AFTER_ACTIVITY_S)
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

    def _has_template(self) -> bool:
        return self._template is not None or self._template_factory is not None

    def _ensure_task(self) -> None:
        if (self._stopped or not self._has_template()
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
            while self._has_template():
                now = self._clock()
                if not self._active(now):
                    logger.debug("model_access_warmer: inactive, stopping")
                    return
                await self._save_state()
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

    async def _save_state(self) -> None:
        """Write the last activity and the learned key when they changed (off the loop)."""
        current = (self._last_activity, self._key)
        if current == self._saved or self._last_activity == float("-inf"):
            return
        try:
            await self._run_io(write_state, self._state(), *current)
            self._saved = current
        except Exception:  # noqa: BLE001 — the next pass tries again; chat is unaffected
            logger.debug("model_access_warmer: state not written", exc_info=True)

    async def warm_once(self, before: dict[str, float]) -> None:
        """One warm-up handshake, then learn the chat key from what it rewrote."""
        now = self._clock()
        self._not_before = now + MIN_GAP_S
        if not self._budget():
            logger.info("model_access_warm skipped — spawn budget refuses a spawn")
            self._not_before = now + RETRY_S
            return
        # counted from the budget check (no await in between) until its CLI is closed
        self._running += 1
        t0 = time.perf_counter()
        try:
            options = await self._options()
            await self._handshake(options)
        except Exception as exc:  # noqa: BLE001 — logged; chat is unaffected
            logger.warning("model_access_warm failed after %.0f ms: %s: %s",
                           (time.perf_counter() - t0) * 1000, type(exc).__name__, exc)
            self._not_before = now + RETRY_S
            return
        finally:
            self._running -= 1
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
        """The warm-up's options: the chat template (or, before the first chat spawn,
        the one built from the configuration) with the environment configured and
        copied under ``_env_lock`` (released before the handshake)."""
        from claude_agent_sdk import ClaudeAgentOptions
        from .claude_environment import _configure_claude_environment, _env_lock
        tpl = self._template
        if tpl is None:
            tpl = WarmTemplate.from_fields(await self._run_io(self._template_factory))
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
            finally:
                # also when a cancel (yield_to_chat, stop) lands during the close
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


async def start(config: Any, template_factory: Callable[[], dict]) -> None:
    """Daemon startup (after the leftover-CLI sweep): see ``ModelAccessWarmer.start``."""
    try:
        await WARMER.start(config, template_factory)
    except Exception:  # noqa: BLE001 — never stops startup
        logger.warning("model_access_warmer: start failed", exc_info=True)


async def yield_to_chat() -> bool:
    """See ``ModelAccessWarmer.yield_to_chat`` (never raises)."""
    try:
        return await WARMER.yield_to_chat()
    except Exception:  # noqa: BLE001
        logger.debug("model_access_warmer: yield_to_chat failed", exc_info=True)
        return False


def running_count() -> int:
    """Warm-up CLIs started and not yet closed (counted by the spawn budget)."""
    try:
        return int(WARMER.running_count())
    except Exception:  # noqa: BLE001 — an unreadable count is no warm-up
        logger.debug("model_access_warmer: running_count failed", exc_info=True)
        return 0


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
