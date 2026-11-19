"""The ledger: one append-only line per walk result, per Brain and repo.

``<app data>/feature_map/<brain>/<repo>/ledger.jsonl``. Keyed by Brain and repo, not by
checkout, so results from session worktrees, pipeline run worktrees and the main
checkout share one history (design §7). Each line records what the CLI observed:

    {"at", "feature", "walk", "project", "key", "hash", "commit", "clean", "code_tree",
     "checkout", "trigger", "run", "result", "reason", "duration_s", "evidence"}

- ``key`` = ``<feature>::<walk title>::<project>`` (project = the viewport project, or
  ``command`` for a command walk);
- ``hash`` = the walk's content hash (``files.content_hash``): editing a walk starts its
  history over, so an edited walk never inherits an old pass or an old quarantine;
- ``trigger`` = ``verify`` | ``replay`` | ``base`` (a replay at a pipeline run's base,
  only for walks that failed at head) | ``manual``;
- ``result`` = ``pass`` | ``fail`` | ``flaky`` | ``not_run``.

States are computed from the lines when asked; nothing stores a derived "verified"
flag (PROJECT.md decision: Feature Map reports facts). A walk is quarantined when it
was flaky in 3 of its last 10 results (for its hash), and released after 5 passes in a
row. Evidence older than the last ``KEEP_EVIDENCE`` replays of a walk is deleted; the
lines stay. Exports: UNBOUND, brain_key, ledger_dir, append, read, results_for,
quarantined, status, prune_evidence, walk_key.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

KEEP_EVIDENCE = 20
FLAKY_WINDOW, FLAKY_LIMIT, RELEASE_STREAK = 10, 3, 5
RESULTS = ("pass", "fail", "flaky", "not_run")


def _app_data() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir())


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in part)[:120] or "_"


UNBOUND = "_unbound"


def brain_key(project: str | None) -> str:
    """The ledger's Brain name for a project (the CLI and the engine both use this, so
    a run without a project writes and reads the same ledger)."""
    return str(project or "") or UNBOUND


def ledger_dir(brain: str, repo: str) -> Path:
    return _app_data() / "feature_map" / _safe(brain) / _safe(repo)


def walk_key(feature: str, walk: str, project: str) -> str:
    return f"{feature}::{walk}::{project}"


def append(brain: str, repo: str, lines: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Append result lines (``at`` added) and return them as written."""
    out = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d = ledger_dir(brain, repo)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "ledger.jsonl", "a", encoding="utf-8") as fh:
        for line in lines:
            row = {"at": now, **line}
            if row.get("result") not in RESULTS:
                raise ValueError(f"ledger: unknown result {row.get('result')!r}")
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            out.append(row)
        fh.flush()
        os.fsync(fh.fileno())
    return out


def read(brain: str, repo: str) -> list[dict]:
    path = ledger_dir(brain, repo) / "ledger.jsonl"
    if not path.is_file():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue                      # a torn last line from a killed writer
        if isinstance(row, dict):
            rows.append(row)
    return rows


def results_for(rows: Sequence[Mapping[str, Any]], key: str, content_hash: str) -> list[dict]:
    """The walk's own results (not base replays), oldest first, for this content hash."""
    return [dict(r) for r in rows if r.get("key") == key and r.get("hash") == content_hash
            and r.get("trigger") != "base" and r.get("result") in ("pass", "fail", "flaky")]


def quarantined(rows: Sequence[Mapping[str, Any]], key: str, content_hash: str) -> bool:
    """Flaky in ``FLAKY_LIMIT`` of its last ``FLAKY_WINDOW`` results → quarantined;
    released by ``RELEASE_STREAK`` passes in a row (the window restarts at release)."""
    q, streak, start = False, 0, 0
    results = [r["result"] for r in results_for(rows, key, content_hash)]
    for i, result in enumerate(results):
        if not q:
            window = results[max(start, i - FLAKY_WINDOW + 1):i + 1]
            if window.count("flaky") >= FLAKY_LIMIT:
                q, streak = True, 0
        else:
            streak = streak + 1 if result == "pass" else 0
            if streak >= RELEASE_STREAK:
                q, start = False, i + 1
    return q


def status(rows: Sequence[Mapping[str, Any]], key: str, content_hash: str) -> dict[str, Any]:
    """What a walk's history says, as facts: its last result and where it ran, its
    last pass (commit and tree), and whether it is quarantined. ``never_passed`` when
    no pass exists for this content hash."""
    mine = [dict(r) for r in rows if r.get("key") == key and r.get("hash") == content_hash
            and r.get("trigger") != "base"]
    last = mine[-1] if mine else None
    passes = [r for r in mine if r.get("result") == "pass"]
    lp = passes[-1] if passes else None
    return {
        "key": key, "hash": content_hash,
        "last": ({k: last.get(k) for k in ("at", "result", "reason", "commit", "clean",
                                            "trigger", "run")} if last else None),
        "last_pass": ({k: lp.get(k) for k in ("at", "commit", "clean", "code_tree")}
                      if lp else None),
        "never_passed": lp is None,
        "quarantined": quarantined(rows, key, content_hash),
    }


def evidence_dir(brain: str, repo: str, line_id: str) -> Path:
    return ledger_dir(brain, repo) / "evidence" / _safe(line_id)


def prune_evidence(brain: str, repo: str, keep: int = KEEP_EVIDENCE) -> int:
    """Delete evidence folders beyond the last *keep* of each walk, never one of a
    walk's current failure. Returns how many were removed. The lines stay."""
    rows = read(brain, repo)
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("evidence"):
            by_key.setdefault(str(r.get("key")), []).append(r)
    root = (ledger_dir(brain, repo) / "evidence").resolve()
    removed = 0
    for key, items in by_key.items():
        last = items[-1]
        for r in items[:-keep] if len(items) > keep else []:
            if r is last and r.get("result") == "fail":
                continue
            path = Path(str(r["evidence"])).resolve()
            if path.is_dir() and path.is_relative_to(root):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed
