"""ToDo history statistics — pure aggregation helper (run_d28de5fd, 2026-08-01).

Computes the 5 History-view charts over a combined set of todo rows (DB recent +
todo-archive.jsonl cold rows). PURE: no DB, no filesystem, no network — callers
pass already-loaded row dicts, so this is trivially unit-testable and cannot drag
the request path. Design: Knowledge/Designs/2026-08-01-todo-flow-closure-design.md.

The 5 aggregations (XG-locked set):
  1. throughput_weekly    — per-ISO-week {created, completed} counts (bar chart)
  2. completion_rate      — completed / created
  3. source_distribution  — count by source_type (pie)
  4. confirm_vs_auto      — {manual, auto} counts from review_kind (of confirmed)
  5. reject_rate          — rejected / reviewed  (reviewed = confirmed + rejected)

"Created" counts every row (created_at). "Completed" counts rows that reached a
completed/confirmed/rejected review_state OR a terminal handled status — i.e. work
the AI actually delivered, regardless of the later human verdict.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp (tolerant of 'Z' and naive strings). None-safe."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _iso_week(dt: datetime) -> str:
    """ISO year-week key, e.g. '2026-W31' — stable, sortable."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _is_completed_row(row: dict) -> bool:
    """A row counts as 'completed' (AI delivered) if it reached any review state
    or the terminal 'handled' status. Rejected still counts as completed work —
    the AI delivered; the human just rejected the result."""
    rs = row.get("review_state")
    if rs in ("completed", "confirmed", "rejected"):
        return True
    return row.get("status") == "handled"


def compute_history_stats(rows: Iterable[dict]) -> dict:
    """Compute the 5 History aggregations from a combined row set.

    Args:
        rows: todo dicts from DB + archive. Each may have created_at, completed_at,
              review_state, review_kind, source_type, status.

    Returns:
        {
          "throughput_weekly": [{"week": "2026-W30", "created": N, "completed": M}, ...],
          "completion_rate": float,   # 0.0..1.0 (0.0 when no rows)
          "source_distribution": {source_type: count, ...},
          "confirm_vs_auto": {"manual": N, "auto": M},
          "reject_rate": float,       # rejected / (confirmed + rejected); 0.0 when none reviewed
          "totals": {"created": N, "completed": M, "confirmed": C, "rejected": R, "reviewed": V},
        }
    """
    rows = list(rows)

    weekly_created: dict[str, int] = defaultdict(int)
    weekly_completed: dict[str, int] = defaultdict(int)
    source_dist: dict[str, int] = defaultdict(int)
    manual = auto = 0
    created = completed = confirmed = rejected = 0

    for row in rows:
        created += 1
        c_dt = _parse_dt(row.get("created_at"))
        if c_dt:
            weekly_created[_iso_week(c_dt)] += 1

        source_dist[row.get("source_type") or "manual"] += 1

        if _is_completed_row(row):
            completed += 1
            # bucket completion into the week it was delivered (fallback: created week)
            done_dt = _parse_dt(row.get("completed_at")) or c_dt
            if done_dt:
                weekly_completed[_iso_week(done_dt)] += 1

        rs = row.get("review_state")
        if rs == "confirmed":
            confirmed += 1
            if row.get("review_kind") == "auto":
                auto += 1
            else:
                manual += 1
        elif rs == "rejected":
            rejected += 1

    reviewed = confirmed + rejected

    # union of all weeks that appear, sorted ascending
    all_weeks = sorted(set(weekly_created) | set(weekly_completed))
    throughput_weekly = [
        {"week": w, "created": weekly_created.get(w, 0), "completed": weekly_completed.get(w, 0)}
        for w in all_weeks
    ]

    return {
        "throughput_weekly": throughput_weekly,
        "completion_rate": (completed / created) if created else 0.0,
        "source_distribution": dict(source_dist),
        "confirm_vs_auto": {"manual": manual, "auto": auto},
        "reject_rate": (rejected / reviewed) if reviewed else 0.0,
        "totals": {
            "created": created,
            "completed": completed,
            "confirmed": confirmed,
            "rejected": rejected,
            "reviewed": reviewed,
        },
    }
