"""The shared record of background-cognition credential failures outside CDF's own tables.

Every background model call goes through ``jobs.bedrock._call`` (``structured()`` /
``text()``): session summarization, the session scorer, inbox triage, jobs, CDF's helper
calls. Many of those callers catch the error and only log it. ``_call`` therefore notes
here a call that failed because no credentials resolve or Bedrock rejected them
(``core.ingestion.llm.failure_reason``), and clears the whole record when a later call
of any source gets a reply: every caller signs with the same credentials
(``jobs.bedrock.get_client`` / ``build_timeout_client``), so one reply means they work
again, and a rarely-running source (a weekly job) does not keep the item up after the
person fixed the sign-in. ``core.attention_authority`` reads it next to CDF's tables and shows the one
"Bedrock credentials are not working" item. CDF's tables stay CDF's: writing these
failures there would fake CDF records and skew CDF's own status.

The record is one small JSON file in the data dir: the latest failure per source
(``source`` is the call's usage feature, e.g. ``summarize.session``), at most
``MAX_SOURCES`` sources, each reason at most ``MAX_REASON_CHARS`` characters. The reason
is the error's class name and AWS error code only, never its message, so no key or token
text reaches the disk. It is written in the thread that made the blocking Bedrock call
(never on the event loop), under a process-local lock with an atomic replace; a lost
update between two processes at worst drops one entry until that source fails again.
Nothing here ever raises into the model call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILE_NAME = "background_credentials.json"
MAX_SOURCES = 32
MAX_REASON_CHARS = 200

_lock = threading.Lock()


def _path() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / FILE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, dict[str, str]] | None:
    """The recorded failures by source ({} when there is no file); None when the file
    cannot be read as a record. Reads only."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
        failures = data.get("failures") if isinstance(data, dict) else None
        if not isinstance(failures, dict):
            raise ValueError("no failures object")
    except ValueError:
        return None
    return {str(k): v for k, v in failures.items()
            if isinstance(v, dict) and isinstance(v.get("at"), str)}


def _read(path: Path) -> dict[str, dict[str, str]]:
    """``_load`` for a writer: a file that cannot be read as a record is moved aside
    (``.corrupt-<ts>``, never deleted) and the record starts empty."""
    recorded = _load(path)
    if recorded is not None:
        return recorded
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    try:
        path.rename(path.with_name(f"{path.name}.corrupt-{stamp}"))
    except OSError as exc:
        logger.debug("corrupt background credentials record not moved aside: %s", exc)
    return {}


def _write(path: Path, failures: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"failures": failures}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _reason(exc: BaseException) -> str:
    """``judge_unavailable:aws_credentials: <class>[:<AWS code>]`` of the credentials
    error in *exc*'s chain; no message text."""
    from core.ingestion.llm import AWS_CREDENTIALS_REASON, _is_credentials_error
    seen: set[int] = set()
    current: BaseException | None = exc
    found: BaseException = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _is_credentials_error(current):
            found = current
            break
        current = current.__cause__ or current.__context__
    name = type(found).__name__
    code = ""
    response = getattr(found, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
    detail = f"{name}:{code}" if code else name
    return f"{AWS_CREDENTIALS_REASON}: {detail}"[:MAX_REASON_CHARS]


def note_failure(source: str | None, exc: BaseException) -> bool:
    """Record *exc* for *source* when it is a credentials failure; True when recorded.
    Any other failure, or an error while recording, leaves the record as it was."""
    try:
        from core.ingestion.llm import failure_reason
        if failure_reason(exc) is None:
            return False
        key = (str(source or "").strip() or "unattributed")[:80]
        entry = {"at": _now(), "reason": _reason(exc)}
        path = _path()
        with _lock:
            failures = _read(path)
            failures.pop(key, None)
            failures[key] = entry
            if len(failures) > MAX_SOURCES:
                newest = sorted(failures.items(), key=lambda kv: kv[1]["at"])[-MAX_SOURCES:]
                failures = dict(newest)
            _write(path, failures)
        return True
    except Exception:  # noqa: BLE001 - the record never breaks a model call
        logger.debug("background credentials failure not recorded", exc_info=True)
        return False


def note_reply() -> None:
    """A background call got a reply: the credentials work, so the record is cleared.
    Costs one ``stat`` when nothing is recorded (the usual case)."""
    try:
        path = _path()
        if not path.exists():
            return
        with _lock:
            if _read(path):
                _write(path, {})
    except Exception:  # noqa: BLE001 - the record never breaks a model call
        logger.debug("background credentials record not cleared", exc_info=True)


def failures() -> list[dict[str, Any]]:
    """The recorded failures, newest first: ``{"source", "at", "reason"}``; none when
    the file is missing or cannot be read. Reads only: the next ``note_failure`` moves
    a corrupt file aside."""
    try:
        path = _path()
        if not path.exists():
            return []
        with _lock:
            recorded = _load(path) or {}
    except Exception:  # noqa: BLE001
        logger.debug("background credentials record not read", exc_info=True)
        return []
    rows = [{"source": k, "at": v["at"], "reason": str(v.get("reason") or "")}
            for k, v in recorded.items()]
    return sorted(rows, key=lambda r: r["at"], reverse=True)
