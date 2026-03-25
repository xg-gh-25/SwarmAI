"""
Lightweight cron expression evaluator.

Replaces croniter dependency with a simple stdlib-only implementation.
Supports standard 5-field cron: minute hour day-of-month month day-of-week.
Supports: *, */N, N, N-M, comma-separated values.

Only answers: "should this job have run since last_run?"
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def is_cron_due(cron_expr: str, last_run: datetime, now: datetime | None = None) -> bool:
    """
    Check if a cron schedule should have triggered between last_run and now.

    Args:
        cron_expr: Standard 5-field cron expression
        last_run: Last time the job ran (timezone-aware)
        now: Current time (defaults to utcnow)

    Returns:
        True if the job should run
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Ensure timezone-aware
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {cron_expr}")

    minute_spec, hour_spec, dom_spec, month_spec, dow_spec = parts

    # Walk each minute from last_run+1min to now, check if any matches
    # Optimization: cap at 48 hours of checking to avoid runaway
    check = last_run.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_check = min(now, last_run + timedelta(hours=48))

    while check <= max_check:
        if (
            _matches(minute_spec, check.minute, 0, 59) and
            _matches(hour_spec, check.hour, 0, 23) and
            _matches(dom_spec, check.day, 1, 31) and
            _matches(month_spec, check.month, 1, 12) and
            _matches(dow_spec, check.weekday(), 0, 6, is_dow=True)
        ):
            return True
        check += timedelta(minutes=1)

    return False


def _matches(spec: str, value: int, min_val: int, max_val: int, is_dow: bool = False) -> bool:
    """Check if a value matches a cron field specification."""
    # Handle comma-separated values
    for part in spec.split(","):
        if _matches_single(part.strip(), value, min_val, max_val, is_dow):
            return True
    return False


def _matches_single(spec: str, value: int, min_val: int, max_val: int, is_dow: bool = False) -> bool:
    """Check a single cron field part."""
    if spec == "*":
        return True

    # */N — step
    if spec.startswith("*/"):
        try:
            step = int(spec[2:])
            return value % step == 0
        except ValueError:
            return False

    # N-M — range
    if "-" in spec:
        try:
            low, high = spec.split("-", 1)
            low_val = int(low)
            high_val = int(high)
            # DOW: cron uses 0=Sunday, Python weekday() uses 0=Monday
            if is_dow:
                value = (value + 1) % 7  # convert Python weekday to cron DOW
            return low_val <= value <= high_val
        except ValueError:
            return False

    # Exact value
    try:
        target = int(spec)
        if is_dow:
            value = (value + 1) % 7  # convert Python weekday to cron DOW
        return value == target
    except ValueError:
        return False
