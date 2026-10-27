"""Job handler: the daily log patrol (core/log_patrol; design S5).

Reads what the daemon log and the uvicorn access log (backend-stdout.log) gained
since the last run — per-file byte offsets in the state file; a file that shrank
or was replaced (rotation) is read from its start — counts each problem kind,
compares with the previous 7 days, and sends ONE notification listing what is
new or growing (nothing when nothing is). The first run only records a baseline.

State: ``~/.swarm-ai/logs/log_patrol.json`` — ``{"offsets": {path: [inode, offset]},
"days": [{"date": ..., "counts": {sig: per-day rate}, "raw": {sig: count}}]}`` (7 runs
kept), plus the last report. The job runs on weekdays, so a run's counts are divided
by the days since the previous run (Monday's covers the weekend).

Public: ``run_log_patrol(dry_run=False, *, log_dir=None, notifier=None, today=None) -> dict``.
Zero-LLM, deterministic.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

FILES = ("backend-daemon.log", "backend-stdout.log")
MAX_READ = 64 * 1024 * 1024     # per file per run; beyond it the oldest part is skipped
MAX_LISTED = 12


def _log_dir() -> Path:
    from config import get_log_file_path
    return get_log_file_path().parent


def _new_lines(path: Path, prev: Optional[list]) -> tuple[list[str], list]:
    try:
        st = path.stat()
    except OSError:
        return [], prev or [0, 0]
    start = prev[1] if prev and prev[0] == st.st_ino and prev[1] <= st.st_size else 0
    start = max(start, st.st_size - MAX_READ)
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines(), [st.st_ino, st.st_size]


def _default_notifier(**kwargs) -> dict:
    from skills.s_notify.notify import send_notification
    return send_notification(**kwargs)


def run_log_patrol(dry_run: bool = False, *, log_dir: Optional[Path] = None,
                   notifier: Optional[Callable[..., dict]] = None,
                   today: Optional[str] = None) -> dict:
    from core.log_patrol import compare, count
    d = Path(log_dir) if log_dir else _log_dir()
    state_path = d / "log_patrol.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    offsets = state.get("offsets", {})
    days = state.get("days", [])
    first_run = not offsets
    counts: dict[str, int] = {}
    try:
        for name in FILES:
            lines, offsets[str(d / name)] = _new_lines(d / name, offsets.get(str(d / name)))
            for sig, n in count(lines).items():
                counts[sig] = counts.get(sig, 0) + n
    except Exception as exc:  # noqa: BLE001
        logger.error("log patrol: read failed: %s", exc)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    day = today or date.today().isoformat()
    # a second run the same day adds to that day (the offsets moved on)
    for x in days:
        if x.get("date") == day:
            for sig, n in x.get("raw", x["counts"]).items():
                counts[sig] = counts.get(sig, 0) + n
    before = [x for x in days if x.get("date") != day]
    # the job runs on weekdays: Monday's read covers the weekend too, so a run is
    # compared as a per-day rate over the days since the previous run
    span = 1
    if before:
        try:
            span = max(1, (date.fromisoformat(day) - date.fromisoformat(before[-1]["date"])).days)
        except ValueError:
            span = 1
    rates = {sig: n / span for sig, n in counts.items()}
    findings = [] if first_run else compare(rates, [x["counts"] for x in before])
    days = before + [{"date": day, "counts": rates, "raw": counts}]
    report = [{"kind": f.kind, "signature": f.signature, "count": round(f.count, 1),
               "baseline": round(f.baseline, 1)} for f in findings]
    notified = False
    if findings and not dry_run:
        lines = [f"- [{f.kind}] {f.signature} — {f.count:g}/day" + (f" (≈{f.baseline:.0f}/day before)" if f.baseline else "")
                 for f in findings[:MAX_LISTED]]
        if len(findings) > MAX_LISTED:
            lines.append(f"- … and {len(findings) - MAX_LISTED} more (see {state_path})")
        try:
            (notifier or _default_notifier)(message="\n".join(lines), title="SwarmAI log patrol",
                                            channels=["slack"])
            notified = True
        except Exception as exc:  # noqa: BLE001
            logger.error("log patrol: notify failed: %s", exc)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offsets": offsets, "days": days[-7:], "last_report": report},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, state_path)
    return {"status": "success", "kinds": len(counts), "events": sum(counts.values()),
            "findings": report, "baseline_only": first_run, "notified": notified}
