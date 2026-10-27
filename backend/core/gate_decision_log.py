"""One durable record per gate decision that stops or escalates a tool call.

Every PreToolUse hook is wrapped by ``HookRegistry.register`` with
``with_decision_log``, so a gate does not log its own denials: the registry
writes one JSON line per DENY / HITL verdict to
``~/.swarm-ai/logs/gate_decisions.jsonl``. The line carries the gate name, the
tool, the FULL command (capped at 4000 chars, not the 80 the old
``[BLOCKED]`` log lines kept) and the reason, so a later audit can count each
gate's denials and judge false positives from the real input.

Key public symbols:

- ``with_decision_log(name, hook)`` -- wrap a hook; the result is unchanged.
- ``record_decision(...)``          -- append one line (never raises).
- ``mark_self_logged(input_data)`` -- a gate wrote its own row; the wrapper skips it.
- ``record_force_bypass(...)``      -- append one line for an operator FORCE use.
- ``log_path()``                    -- the jsonl path (``SWARM_GATE_LOG`` overrides).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .verdict import Disposition, from_sdk

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 4000
_MAX_REASON_CHARS = 600
_MAX_BYTES = 20 * 1024 * 1024  # rotate to .1 past 20 MB
_lock = threading.Lock()
_ids_lock = threading.Lock()   # never held across file I/O (the wrapper runs on the loop)


def log_path() -> Path:
    override = os.environ.get("SWARM_GATE_LOG")
    if override:
        return Path(override)
    from config import get_app_data_dir
    return get_app_data_dir() / "logs" / "gate_decisions.jsonl"


def _tool_input_text(tool_name: str, tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return str(tool_input)[:_MAX_INPUT_CHARS]
    if tool_name == "Bash":
        text = str(tool_input.get("command", ""))
    elif "file_path" in tool_input:
        text = str(tool_input.get("file_path", ""))
    else:
        text = json.dumps(tool_input, ensure_ascii=False, default=str)
    return text[:_MAX_INPUT_CHARS]


def _reason(result: dict) -> str:
    hso = result.get("hookSpecificOutput") or {}
    text = (
        hso.get("permissionDecisionReason")
        or result.get("reason")
        or result.get("systemMessage")
        or ""
    )
    return str(text)[:_MAX_REASON_CHARS]


def _append(record: dict) -> None:
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _lock:
            try:
                if path.stat().st_size > _MAX_BYTES:
                    path.replace(path.with_suffix(path.suffix + ".1"))
            except FileNotFoundError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:  # never let logging break a tool call
        logger.debug("gate decision log write failed", exc_info=True)


def record_decision(
    gate: str,
    disposition: str,
    input_data: Any,
    reason: str = "",
    extra: "dict | None" = None,
) -> None:
    """Append one decision line. ``input_data`` is the SDK hook input dict; *extra*
    adds fields (a prompting gate's ``rule`` / ``match`` / ``prompted``)."""
    data = input_data if isinstance(input_data, dict) else {}
    tool_name = str(data.get("tool_name", ""))
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gate": gate,
        "decision": disposition,
        "tool": tool_name,
        "session_id": data.get("session_id"),
        "app_session_id": os.environ.get("SWARM_APP_SESSION_ID"),
        "input": _tool_input_text(tool_name, data.get("tool_input")),
        "reason": str(reason)[:_MAX_REASON_CHARS],
    }
    record.update(extra or {})
    _append(record)


# Hook inputs whose gate wrote its own row (a gate that prompts records the person's
# answer, which the wrapper cannot see): the wrapper then writes none. Keyed by the
# input dict's id, taken out by the wrapper right after the hook returns.
_self_logged: set[int] = set()


def mark_self_logged(input_data: Any) -> None:
    """A gate recorded this call itself; ``with_decision_log`` skips it."""
    with _ids_lock:
        if len(_self_logged) > 1000:   # only unwrapped callers (tests) leave ids behind
            _self_logged.clear()
        _self_logged.add(id(input_data))


def record_force_bypass(gate: str, input_data: Any = None, detail: str = "") -> None:
    """Record that an operator FORCE switch let a call through ``gate``."""
    record_decision(gate, "force_bypass", input_data or {}, detail)


HookFn = Callable[..., Awaitable[Any]]


def with_decision_log(name: str, hook: HookFn) -> HookFn:
    """Wrap ``hook`` so a DENY / HITL verdict it returns is logged once.

    The wrapped hook returns exactly what ``hook`` returns and raises exactly
    what it raises; only the logging is added.
    """

    async def logged(input_data: Any, tool_use_id: Any, context: Any) -> Any:
        result = await hook(input_data, tool_use_id, context)
        with _ids_lock:
            self_logged = id(input_data) in _self_logged
            _self_logged.discard(id(input_data))
        if isinstance(result, dict) and result and not self_logged:
            try:
                disposition = from_sdk(result).disposition
                if disposition >= Disposition.HITL:
                    record_decision(name, disposition.name.lower(), input_data, _reason(result))
            except Exception:
                logger.debug("gate decision classification failed", exc_info=True)
        return result

    logged.__name__ = getattr(hook, "__name__", name)
    logged.__wrapped__ = hook  # type: ignore[attr-defined]
    return logged
