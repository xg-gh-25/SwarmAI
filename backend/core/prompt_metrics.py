"""How big the every-turn context files are, and how long the first token takes.

Design docs/design/2026-10-30-cdf-agent-decides.md §4: the five files injected into
every turn (MEMORY, EVOLUTION, STEERING, USER, TOOLS) are injected whole, with no
budget; what keeps them lean is the agent's same / adds / overrides declarations.
Whether that holds is measured, not capped: each assembled prompt records the
tokens of those sections, and each turn its time to the first token. When either
rises clearly against its baseline, the agent consolidates the file (declarations,
never truncation).

Writers (never block the caller: a bounded queue and one writer thread; a full
queue drops and counts):

- ``record_sections(sections)`` — from context assembly, ``(section name, tokens)``
  of every assembled section; only the every-turn files are kept.
- ``record_ttft(ttft_ms, pre_send_ms, spawn_ms, retries)`` — from the router's TTFT
  probe, once per turn.

Reader: ``daily(days)`` — per UTC day, each file's last and largest token count and
the turns' first-token median and p90 (overall, and the model part: TTFT minus the
router's pre-send work and a cold spawn, on turns without a retry).
``python -m core.prompt_metrics [days]`` prints it.

Storage: one JSON line per record in ``<app data dir>/metrics/prompt-metrics-YYYY-MM.jsonl``,
the app data dir resolved when the record is made (``put``), not when the writer thread
writes it: a test's ``SWARM_DATA_DIR`` is gone by the time the thread drains, and records
of test prompts landed in the live file (a 13-token MEMORY "last" on 2026-10-30).
"""
from __future__ import annotations

import json
import logging
import queue
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: the section names of the files injected into every turn (context_directory_loader specs)
EVERY_TURN_SECTIONS = ("Memory", "Evolution Registry", "Steering", "User", "Tools")

_MAX_QUEUED = 5_000


def _dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "metrics"


def _path(day: str) -> Path:
    return _dir() / f"prompt-metrics-{day[:7]}.jsonl"


class _Writer:
    """One daemon thread appends queued records; ``flush`` drains the rest in the
    caller under the same lock, so a flushed record is on disk."""

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[Path, dict]]" = queue.Queue(_MAX_QUEUED)
        self._lock = threading.Lock()          # held while a batch is taken and written
        self._start = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.dropped = 0

    def put(self, rec: dict) -> None:
        try:
            self._q.put_nowait((_path(rec["at"]), rec))
        except queue.Full:
            self.dropped += 1
            return
        with self._start:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="prompt-metrics", daemon=True)
                self._thread.start()

    def _drain(self) -> None:
        with self._lock:
            batch = []
            while len(batch) < 1000:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._write(batch)

    def _run(self) -> None:
        idle = 0
        while idle < 300:                       # exits after ~30s idle; put() restarts it
            if self._q.empty():
                idle += 1
                time.sleep(0.1)
                continue
            idle = 0
            self._drain()

    @staticmethod
    def _write(batch: list[tuple[Path, dict]]) -> None:
        try:
            by_path: dict[Path, list[str]] = {}
            for path, r in batch:
                by_path.setdefault(path, []).append(json.dumps(r, ensure_ascii=False))
            for p, lines in by_path.items():
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception:  # noqa: BLE001 — a measurement never breaks the work it measures
            logger.debug("prompt metrics not written", exc_info=True)

    def flush(self) -> None:
        while not self._q.empty():
            self._drain()
        with self._lock:                        # a batch the thread took is written
            pass


_writer = _Writer()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_sections(sections: Iterable[tuple[str, int]]) -> None:
    """Record the tokens of the every-turn sections of one assembled prompt."""
    files = {name: int(tok) for name, tok in sections if name in EVERY_TURN_SECTIONS}
    if files:
        _writer.put({"at": _now(), "kind": "prompt", "files": files})


def record_ttft(ttft_ms: float, pre_send_ms: Optional[float] = None, spawn_ms: Optional[float] = None,
                retries: int = 0) -> None:
    """Record one turn's time to the first token, with the parts the router measured."""
    rec: dict[str, Any] = {"at": _now(), "kind": "ttft", "ms": round(float(ttft_ms), 1), "retries": int(retries)}
    if pre_send_ms is not None:
        rec["pre_send_ms"] = round(float(pre_send_ms), 1)
    if spawn_ms:
        rec["spawn_ms"] = round(float(spawn_ms), 1)
    _writer.put(rec)


def flush() -> None:
    """Write every queued record now."""
    _writer.flush()


def _model_ms(r: dict) -> Optional[float]:
    """The model part of a turn's TTFT: minus the router's pre-send work and a cold
    spawn; None on a retried turn (its TTFT spans several spawns) or without pre-send."""
    if r.get("retries") or r.get("pre_send_ms") is None:
        return None
    return max(0.0, r["ms"] - r["pre_send_ms"] - (r.get("spawn_ms") or 0.0))


def _p90(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * (len(xs) - 1) + 0.5))]


def daily(days: int = 7, today: Optional[datetime] = None) -> list[dict]:
    """Per UTC day (oldest first, days with no record omitted): each every-turn file's
    last and largest tokens, and the TTFT count, median and p90 (overall and model part)."""
    today = today or datetime.now(timezone.utc)
    wanted = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    recs: dict[str, list[dict]] = {d: [] for d in wanted}
    for month in sorted({d[:7] for d in wanted}):
        p = _dir() / f"prompt-metrics-{month}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = str(r.get("at", ""))[:10]
            if d in recs:
                recs[d].append(r)
    out = []
    for d in wanted:
        rs = recs[d]
        if not rs:
            continue
        files: dict[str, dict] = {}
        for r in rs:
            if r.get("kind") == "prompt":
                for name, tok in (r.get("files") or {}).items():
                    f = files.setdefault(name, {"last": 0, "max": 0})
                    f["last"] = tok
                    f["max"] = max(f["max"], tok)
        ttft = [r["ms"] for r in rs if r.get("kind") == "ttft"]
        model = [m for m in (_model_ms(r) for r in rs if r.get("kind") == "ttft") if m is not None]
        day: dict[str, Any] = {"day": d, "files": files, "turns": len(ttft)}
        if ttft:
            day["ttft_median_ms"] = round(statistics.median(ttft))
            day["ttft_p90_ms"] = round(_p90(ttft))
        if model:
            day["model_median_ms"] = round(statistics.median(model))
            day["model_p90_ms"] = round(_p90(model))
        out.append(day)
    return out


if __name__ == "__main__":  # pragma: no cover — a reading aid
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    for row in daily(n):
        print(json.dumps(row, ensure_ascii=False))
