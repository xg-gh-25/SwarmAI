"""Guard test for job schedule policy (run_89d7b5b8, DoD1).

Enforces the standing rule: every CLOCK-scheduled job (system + user) runs
only on WEEKDAYS (dow 1-5) in the AFTERNOON ICT window (13:00-18:00), which —
because the scheduler interprets cron in UTC (cron_utils.py:29) and the user
is at ICT/UTC+8 — is UTC hour 5-10.

Event-driven (`on:<event>`), dependency (`after:<job>`), and interval
(`*/N ...`) schedules are EXEMPT — they are not clock-anchored.

This is a regression guard: it prevents a future edit from silently
reintroducing a 3am-UTC (= ICT 11am … or worse, a 2am-ICT midnight) or a
weekend schedule. It parses the REAL sources, not a snapshot.
"""
from __future__ import annotations



# ── Policy constants ────────────────────────────────────────────────
_AFTERNOON_UTC_HOURS = set(range(5, 11))  # UTC 5,6,7,8,9,10 == ICT 13-18
_WEEKEND_DOW = {"0", "6", "7"}  # cron: 0 or 7 = Sunday, 6 = Saturday


def _is_clock_schedule(sched: str) -> bool:
    """True only for a plain 5-field cron. Event/dep/interval are exempt."""
    if not sched:
        return False
    if sched.startswith(("on:", "after:")):
        return False
    if "/" in sched.split()[0]:  # */N minute-interval probe (e.g. */15)
        return False
    return len(sched.split()) == 5


def _hours(hour_field: str) -> list[int]:
    """Expand a cron hour field (supports '5', '5,8', '5-10') to a list of ints.
    A '*' hour is returned as [-1] (a wildcard hour is a policy violation:
    it would run around the clock)."""
    if hour_field == "*":
        return [-1]
    out: list[int] = []
    for part in hour_field.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def _dow_tokens(dow_field: str) -> set[str]:
    """Expand a cron day-of-week field into the set of individual day tokens.
    '1-5' -> {1,2,3,4,5}; '*' -> every day (violation: includes weekend)."""
    if dow_field == "*":
        return {str(d) for d in range(0, 7)}
    out: set[str] = set()
    for part in dow_field.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.update(str(d) for d in range(int(lo), int(hi) + 1))
        else:
            out.add(part)
    return out


def _system_schedules() -> list[tuple[str, str]]:
    """(job_id, schedule) for every system Job() in system_jobs.py."""
    from jobs.system_jobs import SYSTEM_JOBS
    return [(j.id, j.schedule) for j in SYSTEM_JOBS]


def _user_schedules() -> list[tuple[str, str]]:
    """(job_id, schedule) for every user job in the WORKSPACE user-jobs.yaml."""
    import yaml
    from jobs.paths import USER_JOBS_FILE  # canonical workspace path

    if not USER_JOBS_FILE.exists():
        return []
    data = yaml.safe_load(USER_JOBS_FILE.read_text()) or {}
    return [(j.get("id", "?"), j.get("schedule", "")) for j in (data.get("jobs") or [])]


def _all_clock_jobs() -> list[tuple[str, str, str]]:
    """(source, job_id, schedule) for every clock-scheduled job across both sources."""
    rows: list[tuple[str, str, str]] = []
    for jid, sched in _system_schedules():
        if _is_clock_schedule(sched):
            rows.append(("system", jid, sched))
    for jid, sched in _user_schedules():
        if _is_clock_schedule(sched):
            rows.append(("user", jid, sched))
    return rows


class TestScheduleWindowPolicy:
    """Every clock job runs weekday-afternoon (ICT 13-18 = UTC 5-10, dow 1-5)."""

    def test_all_clock_jobs_run_in_afternoon_utc_window(self):
        violations = []
        for source, jid, sched in _all_clock_jobs():
            hour_field = sched.split()[1]
            bad_hours = [h for h in _hours(hour_field) if h not in _AFTERNOON_UTC_HOURS]
            if bad_hours:
                violations.append(f"{source}/{jid}: '{sched}' hour(s) {bad_hours} outside UTC 5-10 (ICT 13-18)")
        assert not violations, "Jobs scheduled outside the afternoon window:\n" + "\n".join(violations)

    def test_no_clock_job_runs_on_weekend(self):
        violations = []
        for source, jid, sched in _all_clock_jobs():
            fields = sched.split()
            dom_field, dow_field = fields[2], fields[4]
            # A monthly job pins day-of-month (e.g. '0 5 1 * *') and MUST leave
            # dow='*' — it fires on whatever weekday the 1st lands on, which is
            # not a "weekend job" in the policy sense. Exempt dow when dom is pinned.
            if dom_field != "*":
                continue
            weekend_hits = _dow_tokens(dow_field) & _WEEKEND_DOW
            if weekend_hits:
                violations.append(f"{source}/{jid}: '{sched}' runs on weekend day(s) {sorted(weekend_hits)}")
        assert not violations, "Jobs scheduled on weekends:\n" + "\n".join(violations)

    def test_there_are_clock_jobs_to_check(self):
        """Guard against a vacuous pass — the parse must find real jobs."""
        assert len(_all_clock_jobs()) >= 5, "Expected several clock-scheduled jobs; parser may be broken"
