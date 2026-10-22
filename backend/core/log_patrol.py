"""Daily log patrol: which kinds of WARNING / ERROR / failed request are new or
growing (design chat-transcript-single-source S5; RC5 "nobody reads the errors").

Pure logic, no IO (the job handler jobs/handlers/log_patrol does the reading,
the state file and the notification):

- ``signature(line)`` — the KIND of a log line, or None for a line that is not a
  problem. Daemon log ``<ts> [pid] - <logger> - WARNING|ERROR - <msg>`` ->
  ``"<LEVEL> <logger>: <msg with ids, numbers, quoted values and paths masked>"``;
  a hook that timed out (its gate let the call through) -> ``"hook timeout: <name>"``;
  a uvicorn access line with a 4xx/5xx -> ``"HTTP <status> <METHOD> <path, ids masked>"``.
- ``count(lines)`` — signature -> count.
- ``compare(today, history, ...)`` — findings: a kind not seen on any of the
  previous days (once there is a baseline), a kind at least ``GROWTH`` times its
  daily mean (and at least ``MIN_GROWTH_COUNT``), and every hook timeout kind at
  or over ``HOOK_TIMEOUT_DAILY_LIMIT`` (a gate failing open that often is a gate
  that does not hold). Sorted most first.

Public: ``signature``, ``count``, ``compare``, ``Finding``, the thresholds.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

GROWTH = 2.0
MIN_GROWTH_COUNT = 10
HOOK_TIMEOUT_DAILY_LIMIT = 20
MAX_SIG_LEN = 160

_DAEMON = re.compile(r"^\d{4}-\d\d-\d\d [\d:,]+ \[\d+\] - (?P<logger>[\w.]+) - (?P<level>WARNING|ERROR|CRITICAL) - (?P<msg>.*)$")
_HOOK_TIMEOUT = re.compile(r"Hook '(?P<name>[^']+)' timed out")
_ACCESS = re.compile(r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>[45]\d\d)\b')

_MASKS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<id>"),
    (re.compile(r"(?:/[\w.@~+-]+){2,}/?"), "<path>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<s>"),
    (re.compile(r"\b(?:run|local|prewarm|sess|s)[-_][\w-]+"), "<id>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
]


def _mask(text: str) -> str:
    for rx, sub in _MASKS:
        text = rx.sub(sub, text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_SIG_LEN]


def signature(line: str) -> str | None:
    line = line.rstrip("\n")
    m = _DAEMON.match(line)
    if m:
        h = _HOOK_TIMEOUT.search(m["msg"])
        if h:
            return f"hook timeout: {h['name']}"
        return f"{m['level']} {m['logger']}: {_mask(m['msg'])}"
    a = _ACCESS.search(line)
    if a:
        path = a["path"].split("?", 1)[0]
        return f"HTTP {a['status']} {a['method']} {_mask_path(path)}"
    return None


def _mask_path(path: str) -> str:
    parts = [p if not p or re.fullmatch(r"[A-Za-z_-]+", p) else "<id>" for p in path.split("/")]
    return "/".join(parts)[:MAX_SIG_LEN]


def count(lines: Iterable[str]) -> Counter:
    c: Counter = Counter()
    for line in lines:
        sig = signature(line)
        if sig:
            c[sig] += 1
    return c


@dataclass(frozen=True)
class Finding:
    kind: str          # new | growing | hook_timeouts
    signature: str
    count: float       # per day
    baseline: float    # daily mean over the previous days (0 for new)


def compare(today: Mapping[str, float], history: Sequence[Mapping[str, float]]) -> list[Finding]:
    """``history``: the previous days' counts, oldest first (at most 7 are used)."""
    days = list(history)[-7:]
    seen = set().union(*days) if days else set()
    out: list[Finding] = []
    for sig, n in today.items():
        mean = sum(d.get(sig, 0) for d in days) / len(days) if days else 0.0
        if sig.startswith("hook timeout: ") and n >= HOOK_TIMEOUT_DAILY_LIMIT:
            out.append(Finding("hook_timeouts", sig, n, mean))
        elif days and sig not in seen:
            out.append(Finding("new", sig, n, 0.0))
        elif days and n >= MIN_GROWTH_COUNT and n >= GROWTH * mean:
            out.append(Finding("growing", sig, n, mean))
    return sorted(out, key=lambda f: (-f.count, f.signature))
