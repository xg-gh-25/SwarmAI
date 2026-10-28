#!/usr/bin/env python3
"""CDF rehearsal replay: run the real S2/S4/S5/S6 sources over a COPY of a workspace.

Design 2026-10-26 §14 stage B, step 2 of the full rehearsal. It points the backend at
a data dir and workspace given on the command line (a copy, never the live
``~/.swarm-ai``), replays the last N days through the production entry points, drains
the worker and writer, and prints one JSON report.

- S2: ``s2_session.extract_session_sync`` (what the S2 job executor runs) for every session updated in the window.
- S4: ``ddd_refresh._recent_commits`` + ``s4_code.extract_commits`` per governed repo.
- S5: ``governance_miner.enqueue_governance_proposals`` (the evolution optimizer cycle
  is NOT replayed: it rewrites SKILL.md files).
- S6: ``signal_ddd_bridge.bridge_signals_to_ddd`` on the copy's current digest.
- Between sources the worker drains under the real owner lock; Bedrock usage is read
  from ``core.ingestion.llm_usage.totals`` (the ingestion_llm_usage record) and the run
  stops at ``--max-usd``.

Public: ``guard_paths``, ``replay``, ``main``. ``--dry-run`` counts what would be
replayed and calls no model.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
SOURCES = ("s2", "s4", "s5", "s6")


def guard_paths(data_dir: Path, workspace: Path) -> None:
    """Refuse the live app data dir, anything inside it, or a missing copy."""
    live = (Path.home() / ".swarm-ai").resolve()
    for label, path in (("data dir", data_dir), ("workspace", workspace)):
        resolved = path.expanduser().resolve()
        if resolved == live or live in resolved.parents:
            raise SystemExit(f"refused: the {label} {resolved} is the live ~/.swarm-ai; "
                             "replay only on a copy")
        if not resolved.is_dir():
            raise SystemExit(f"refused: the {label} {resolved} does not exist")
    if not (data_dir / "data.db").is_file():
        raise SystemExit(f"refused: {data_dir}/data.db not found (copy the data dir first)")


def _sessions_in_window(data_dir: Path, since: datetime) -> list[str]:
    """Session ids updated since *since*, oldest first (read-only open of the copy)."""
    uri = f"file:{data_dir / 'data.db'}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE updated_at >= ? ORDER BY updated_at",
            (since.isoformat(),)).fetchall()
    return [row[0] for row in rows]


def sample_by_size(data_dir: Path, sessions: list[str], n: int) -> list[str]:
    """*n* sessions spread evenly over the size ranking (characters of sent messages),
    returned smallest first so a budget stop cuts the largest. Deterministic."""
    if n <= 0 or n >= len(sessions):
        chosen = list(sessions)
    else:
        sizes = _session_sizes(data_dir, sessions)
        ranked = sorted(sessions, key=lambda sid: (sizes.get(sid, 0), sid))
        step = (len(ranked) - 1) / (n - 1) if n > 1 else 0
        chosen = list(dict.fromkeys(ranked[round(i * step)] for i in range(n)))
    sizes = _session_sizes(data_dir, chosen)
    return sorted(chosen, key=lambda sid: (sizes.get(sid, 0), sid))


def _session_sizes(data_dir: Path, sessions: list[str]) -> dict[str, int]:
    uri = f"file:{data_dir / 'data.db'}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        out = {}
        for sid in sessions:
            row = conn.execute("SELECT COALESCE(SUM(LENGTH(content)),0) FROM messages "
                               "WHERE session_id=?", (sid,)).fetchone()
            out[sid] = int(row[0])
    return out


def _cost(usage: dict[str, int], price_in: float, price_out: float) -> float:
    return (usage["input_tokens"] * price_in + usage["output_tokens"] * price_out) / 1_000_000


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {key: after[key] - before.get(key, 0) for key in after}


def _drain(workspace: Path, judge_factory: Callable[[], Any] | None, max_steps: int,
           *, summary: bool = False) -> int:
    """Process queued candidates under the real owner lock until the lanes are idle.

    Maintenance keeps its production cadence (a day out). The daily summary lane runs
    only when *summary* is set (the last drain), as the worker does once at startup."""
    from core.ingestion import api
    from core.ingestion.owner import acquire
    from core.ingestion.worker import IngestionWorker
    from core.ingestion.writer import recover

    owner = acquire()
    if owner is None:
        raise SystemExit("refused: another CDF worker holds this data dir's owner lock")
    steps = 0
    try:
        conn = api.connect_rw()
        try:
            owner.adopt(conn)
            recover(conn)
            worker = IngestionWorker(owner, workspace, judge_factory=judge_factory)
            later = datetime.now(timezone.utc) + timedelta(days=1)
            worker._maintenance_due = later
            if not summary:
                worker._summary_due = later
            while steps < max_steps and worker.run_once(conn):
                steps += 1
        finally:
            conn.close()
    finally:
        owner.release()
    return steps


def _items(source: str, data_dir: Path, workspace: Path, since: datetime,
           s2_sample: int = 0) -> list[Any]:
    """What one source would replay in the window (no model call)."""
    if source == "s2":
        sessions = _sessions_in_window(data_dir, since)
        return sample_by_size(data_dir, sessions, s2_sample) if s2_sample else sessions
    if source == "s4":
        from core.ddd_bindings import governed_repo_roots
        from jobs.handlers.ddd_refresh import _recent_commits
        out = []
        projects = workspace / "Projects"
        for project_dir in sorted(projects.iterdir()) if projects.is_dir() else []:
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            for repo in governed_repo_roots(project_dir):
                rows = _recent_commits(repo, since=since.isoformat())
                if rows:
                    out.append((project_dir.name, repo, rows))
        return out
    if source == "s5":
        evolution = workspace / ".context" / "EVOLUTION.md"
        return [evolution] if evolution.is_file() else []
    if source == "s6":
        digest = workspace / "Services" / "signals" / "signal_digest.json"
        return [digest] if digest.is_file() else []
    raise ValueError(source)


def _enqueue(source: str, item: Any, workspace: Path, llm: Any | None) -> int:
    """Hand one item to the production entry point; return the receipts it produced."""
    if source == "s2":
        from core.ingestion.sources.s2_session import extract_session_sync
        result = extract_session_sync(item, llm=llm)  # what the S2 job executor runs
        return sum(len(v) for v in result.values())
    if source == "s4":
        from core.ingestion.sources.s4_code import extract_commits
        project, repo, rows = item
        return len(extract_commits(rows, workspace=workspace, project_hint=project,
                                   project_snapshot={"project": project, "repo": str(repo)},
                                   llm=llm))
    if source == "s5":
        from core.evolution.governance_miner import enqueue_governance_proposals
        return enqueue_governance_proposals(item, llm=llm)
    if source == "s6":
        from hooks.signal_ddd_bridge import bridge_signals_to_ddd
        return bridge_signals_to_ddd(str(workspace), llm=llm)
    raise ValueError(source)


def _outcomes() -> dict[str, dict[str, int]]:
    """Unit states per source after the drains: {source: {state: count}}."""
    from contextlib import closing
    from core.ingestion import api
    with closing(api.connect_rw()) as conn:
        rows = conn.execute(
            """SELECT i.source AS source, COALESCE(u.state, 'no_unit') AS unit_state, COUNT(*)
               FROM ingestion_inbox i LEFT JOIN ingestion_units u ON u.candidate_id = i.id
               GROUP BY i.source, unit_state""").fetchall()
    out: dict[str, dict[str, int]] = {}
    for source, state, count in rows:
        out.setdefault(str(source), {})[str(state)] = int(count)
    return out


def migrate_app_db() -> None:
    """Run data.db's migrations on the copy, as backend startup does after the rebuild.

    The live data.db is copied from the running (older) daemon, so tables the new code
    reads (session_inputs, v16/v17) are not there yet. Without this every S2 item
    failed with "no such table: session_inputs" (rehearsal 2026-10-28)."""
    from database import db
    asyncio.run(db.initialize(skip_init=True))


def replay(data_dir: Path, workspace: Path, *, days: int = 7,
           sources: tuple[str, ...] = SOURCES, max_usd: float = 0.0,
           price_in: float = 3.0, price_out: float = 15.0, dry_run: bool = False,
           llm: Any | None = None, judge_factory: Callable[[], Any] | None = None,
           max_steps: int = 100_000, s2_sample: int = 0) -> dict[str, Any]:
    """Replay *sources* over the copy; stop before the next item once *max_usd* is spent.

    The caller must have pointed SWARM_DATA_DIR / SWARM_WORKSPACE at the copy before
    any backend module was imported (``main`` does)."""
    from core.ingestion.llm_usage import totals as usage_snapshot

    since = datetime.now(timezone.utc) - timedelta(days=days)
    started = time.monotonic()
    report: dict[str, Any] = {"since": since.isoformat(), "dry_run": dry_run,
                              "max_usd": max_usd, "sources": {}, "stopped": None}
    for source in sources:
        items = _items(source, data_dir, workspace, since, s2_sample)
        entry = {"items": len(items), "receipts": 0, "errors": 0, "drain_steps": 0}
        if source == "s2" and s2_sample:
            entry["sampled"] = list(items)
            entry["done"] = []
        report["sources"][source] = entry
        if dry_run:
            continue
        before = usage_snapshot()
        for item in items:
            spent = _cost(usage_snapshot(), price_in, price_out)
            if max_usd and spent >= max_usd:
                report["stopped"] = f"budget: ${spent:.2f} spent before {source}"
                break
            try:
                entry["receipts"] += _enqueue(source, item, workspace, llm)
                if "done" in entry:
                    entry["done"].append(item)
            except Exception as exc:  # one bad item is reported, the replay goes on
                entry["errors"] += 1
                entry.setdefault("first_error", f"{type(exc).__name__}: {exc}")
                entry.setdefault("error_kinds", {})
                kind = type(exc).__name__
                entry["error_kinds"][kind] = entry["error_kinds"].get(kind, 0) + 1
                import traceback
                print(f"[{source}] item failed: {item!r:.200}\n{traceback.format_exc()}",
                      file=sys.stderr, flush=True)
            # drain per item, as the live worker does, so the budget check above also
            # sees the cards' spend before the next item
            entry["drain_steps"] += _drain(workspace, judge_factory, max_steps)
        entry["usage"] = _delta(usage_snapshot(), before)
        if report["stopped"]:
            break
    if not dry_run:
        report["summary_drain_steps"] = _drain(workspace, judge_factory, max_steps, summary=True)
        report["outcomes"] = _outcomes()
    usage = usage_snapshot()
    report["usage"] = usage
    report["cost_usd_estimate"] = round(_cost(usage, price_in, price_out), 2)
    report["price_per_million"] = {"input": price_in, "output": price_out}
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", required=True, type=Path,
                        help="the COPY of the app data dir (holds data.db)")
    parser.add_argument("--workspace", required=True, type=Path,
                        help="the COPY of SwarmWS")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--max-usd", type=float, default=0.0,
                        help="stop before the next item once this much is spent (0 = no cap)")
    parser.add_argument("--price-in", type=float, default=3.0, help="USD per million input tokens")
    parser.add_argument("--price-out", type=float, default=15.0, help="USD per million output tokens")
    parser.add_argument("--dry-run", action="store_true", help="count items, call no model")
    parser.add_argument("--s2-sample", type=int, default=0,
                        help="replay only N S2 sessions spread over size, smallest first (0 = all)")
    parser.add_argument("--report", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    unknown = set(sources) - set(SOURCES)
    if unknown:
        parser.error(f"unknown source(s): {sorted(unknown)}")
    guard_paths(args.data_dir, args.workspace)
    if not args.dry_run and args.max_usd <= 0:
        parser.error("a real replay needs --max-usd (it calls Bedrock)")
    # Point every backend module at the copy BEFORE importing any of them.
    os.environ["SWARM_DATA_DIR"] = str(args.data_dir.resolve())
    os.environ["SWARM_WORKSPACE"] = str(args.workspace.resolve())
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    if not args.dry_run:
        migrate_app_db()
    report = replay(args.data_dir.resolve(), args.workspace.resolve(), days=args.days,
                    sources=sources, max_usd=args.max_usd, price_in=args.price_in,
                    price_out=args.price_out, dry_run=args.dry_run, s2_sample=args.s2_sample)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
