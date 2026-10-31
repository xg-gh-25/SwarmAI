#!/usr/bin/env python3
"""CDF rehearsal replay: run the real session-review and S4 sources over a COPY of a workspace.

Design 2026-10-26 §14 stage B, step 2 of the full rehearsal. It points the backend at
a data dir and workspace given on the command line (a copy, never the live
``~/.swarm-ai``), replays the last N days through the production entry points, drains
the worker and writer, and prints one JSON report.

- s2 (the session source): ``s7_review.review_session`` from the session record, no fork (the
  copy has no CLI transcripts of its own), for every session updated in the window.
- S4: ``ddd_refresh._recent_commits`` + ``s4_code.extract_commits`` per governed repo.
- Governance proposals are not a source: the GC table is read directly by the people
  who decide them (governance_miner, the C&M dashboard and Needs You).
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
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
SOURCES = ("s2", "s4")


def guard_paths(data_dir: Path, workspace: Path) -> None:
    """Refuse the live app data dir, anything inside it, or a missing copy."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from config import default_app_data_dir, get_app_data_dir
    lives = {Path(default_app_data_dir()).resolve(), Path(get_app_data_dir()).resolve()}
    for label, path in (("data dir", data_dir), ("workspace", workspace)):
        resolved = path.expanduser().resolve()
        if any(resolved == live or live in resolved.parents for live in lives):
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


def _drain(workspace: Path, max_steps: int, *, summary: bool = False) -> int:
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
            worker = IngestionWorker(owner, workspace)
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
    raise ValueError(source)


def _enqueue(source: str, item: Any, workspace: Path, llm: Any | None) -> int:
    """Hand one item to the production entry point; return the receipts it produced."""
    if source == "s2":
        # the session's review from its record (the job's path for an unresumable
        # session); *llm* is the review runner here (s7_review.Runner), the SDK if None
        from core.ingestion.sources import s7_review
        outcome = s7_review.review_session(item, sdk_session_id=None, watermark=None, runner=llm)
        return int(outcome.get("units", 0))
    if source == "s4":
        from core.ingestion.sources.s4_code import extract_commits
        project, repo, rows = item
        return len(extract_commits(rows, workspace=workspace, project_hint=project,
                                   project_snapshot={"project": project, "repo": str(repo)},
                                   llm=llm))
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


def backfill_user_inputs(data_dir: Path, session_id: str) -> int:
    """Record a historical session's user messages as the send path records them now.

    The copy comes from a daemon older than session_inputs (v16): its sessions have no
    user events, so S2 had nothing it could cite and nothing for the user-utterance
    pass (rehearsal 2026-10-29). Every sent user row is taken as person-typed; old
    rows do not say whether a person or a system prompt sent them, so the rehearsal
    over-counts user trust for system-sent rows. Production does not backfill: only
    sessions sent after the upgrade carry user events. Returns messages recorded."""
    from core import session_inputs
    with sqlite3.connect(data_dir / "data.db") as conn:
        if conn.execute("SELECT 1 FROM session_inputs WHERE session_id=? AND kind='user_message' "
                        "LIMIT 1", (session_id,)).fetchone():
            return 0
        rows = conn.execute(
            "SELECT id, content FROM messages WHERE session_id=? AND role='user' "
            "AND (sent IS NULL OR sent != 0) ORDER BY created_at, rowid", (session_id,)).fetchall()
    for message_id, raw in rows:
        value: Any = raw
        if isinstance(raw, str) and raw[:1] in ("[", "{"):
            try:
                value = json.loads(raw)
            except ValueError:
                pass
        session_inputs.record_user_input_parts(
            session_id, value if isinstance(value, str) else None,
            value if isinstance(value, list) else None, ref=message_id, origin="person")
    return len(rows)


def replay(data_dir: Path, workspace: Path, *, days: int = 7,
           sources: tuple[str, ...] = SOURCES, max_usd: float = 0.0,
           price_in: float = 3.0, price_out: float = 15.0, dry_run: bool = False,
           llm: Any | None = None,
           max_steps: int = 100_000, s2_sample: int = 0) -> dict[str, Any]:
    """Replay *sources* over the copy; stop before the next item once *max_usd* is spent.

    The caller must have pointed SWARM_DATA_DIR / SWARM_WORKSPACE at the copy before
    any backend module was imported (``main`` does)."""
    from core.ingestion.llm_usage import totals as usage_snapshot

    since = datetime.now(timezone.utc) - timedelta(days=days)
    started = time.monotonic()
    report: dict[str, Any] = {"since": since.isoformat(), "dry_run": dry_run,
                              "max_usd": max_usd, "sources": {}, "stopped": None}
    baseline = usage_snapshot()
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
            # this run's spend only: the copy's ingestion.db already holds the live
            # daemon's usage rows (rehearsal 2026-10-29 stopped at "$44.63" before any call)
            spent = _cost(_delta(usage_snapshot(), baseline), price_in, price_out)
            if max_usd and spent >= max_usd:
                report["stopped"] = f"budget: ${spent:.2f} spent before {source}"
                break
            try:
                if source == "s2":
                    entry["backfilled_user_messages"] = (
                        entry.get("backfilled_user_messages", 0) + backfill_user_inputs(data_dir, item))
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
            entry["drain_steps"] += _drain(workspace, max_steps)
        entry["usage"] = _delta(usage_snapshot(), before)
        if report["stopped"]:
            break
    if not dry_run:
        report["summary_drain_steps"] = _drain(workspace, max_steps, summary=True)
        report["outcomes"] = _outcomes()
    usage = _delta(usage_snapshot(), baseline)
    report["usage_in_copy_before_run"] = baseline
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
