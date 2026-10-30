"""Skill health status folding — SkillStats → a qualitative, scannable status enum.

The Capabilities panel shows an at-a-glance HEALTH dot per skill. This module folds the
``SkillMetricsStore`` usage statistics and the after-session review's corrections into a
small qualitative enum so the panel can render a colored dot WITHOUT surfacing raw counts
on the row (R30#4 — volatile decision-inert numbers do not belong on a scannable surface;
success_rate/last_used are carried for the DETAIL drawer only).

Pure functions — NO I/O, NO session context. The router (``routers/skills.py``) reads the
store and the CDF corrections and calls :func:`build_health_map`.

Success comes from the corrections the after-session review recorded that NAME the
skill (``core.ingestion.queries.corrections``, the unit's ``skill``): the agent that had
the whole session said the user corrected how that skill behaved. Nothing reads the
user's next message for keywords any more (design 2026-10-30-cdf-agent-decides D12).
It is measured over the last ``WINDOW_DAYS`` (the CDF keeps its records that long):
``success_rate = 1 - corrections / invocations`` in the window, floored at 0.

The 4 statuses (severity-ordered, most-important-to-surface first):

- ``never_used``  — no metric rows at all (a candidate to RETIRE — the whole point of the dot)
- ``low_success`` — success_rate in the window < 0.7 AND enough invocations in it to trust it
- ``stale``       — last used longer ago than the staleness window
- ``healthy``     — recently used, not corrected often

A floor of ``MIN_INVOCATIONS_FOR_LOW_SUCCESS`` invocations in the window prevents a
rarely-run skill from showing ``low_success`` on no real signal.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from core.skill_metrics import SkillStats

# The qualitative status enum. Kept as a tuple so the router + tests share ONE source.
SKILL_HEALTH_STATUSES = ("healthy", "low_success", "never_used", "stale")

# A skill idle longer than this many days is `stale`. 30d mirrors the EVOLUTION
# idle-decay convention (evolution_maintenance_hook) — a standing project constant.
STALENESS_DAYS = 30

# Minimum invocations in the window before a low success_rate is trusted as `low_success`.
MIN_INVOCATIONS_FOR_LOW_SUCCESS = 5

# The success_rate below which a well-exercised skill is flagged low_success.
LOW_SUCCESS_THRESHOLD = 0.7

# The window success is measured over: the CDF keeps its records 30 days.
WINDOW_DAYS = 30


def _days_since(last_used: str) -> Optional[int]:
    """Days since ``last_used`` (a ``YYYY-MM-DD`` string), or None if unparseable.

    ``SkillStats.last_used`` is ``MAX(invocation_date)`` and can be an empty string
    (``or ""`` in the store). ``date.fromisoformat("")`` raises — so this guards per-row
    (Gate-1 WARN-1): a missing/malformed date is "unknown recency", NOT a crash and NOT
    an assumed-stale. Returns None → the caller treats recency as unknown.
    """
    if not last_used:
        return None
    try:
        used = date.fromisoformat(last_used)
    except (ValueError, TypeError):
        return None
    return (date.today() - used).days


def success_in_window(invocations: int, corrections: int) -> Optional[float]:
    """``1 - corrections / invocations`` floored at 0; None when not invoked in the window."""
    if invocations <= 0:
        return None
    return max(0.0, 1.0 - corrections / invocations)


def fold_status(stats: Optional[SkillStats], staleness_days: int = STALENESS_DAYS, *,
                recent_invocations: int = 0, corrections: int = 0) -> str:
    """Fold a skill's stats into one qualitative status.

    Severity order (first match wins — most-important-to-surface first):
    ``never_used`` > ``low_success`` > ``stale`` > ``healthy``.

    - ``stats is None`` → ``never_used`` (no metric rows exist for this skill).
    - ``low_success`` requires BOTH the window's success below the threshold AND enough
      invocations in the window to trust it — so a rarely-run skill is never libeled.
    - ``stale`` only fires when recency is KNOWN (parseable date) and exceeds the window;
      an unknown/dateless recency is never assumed stale.
    - otherwise ``healthy``.
    """
    if stats is None:
        return "never_used"

    success = success_in_window(recent_invocations, corrections)
    if (recent_invocations >= MIN_INVOCATIONS_FOR_LOW_SUCCESS and success is not None
            and success < LOW_SUCCESS_THRESHOLD):
        return "low_success"

    days = _days_since(stats.last_used)
    if days is not None and days > staleness_days:
        return "stale"

    return "healthy"


def canon(name: str) -> str:
    """The skill's name without the ``s_`` prefix (the store records both forms)."""
    return name[2:] if name.startswith("s_") else name


def build_health_map(
    all_stats: list[SkillStats],
    skill_names: list[str],
    staleness_days: int = STALENESS_DAYS,
    *,
    recent_invocations: Optional[dict[str, int]] = None,
    corrections: Optional[dict[str, int]] = None,
) -> dict[str, dict]:
    """Build a ``{folder_name: {status, success_rate, last_used, invocation_count}}`` map.

    Keys are strictly the ``skill_names`` list (the caller-VISIBLE skill folder names) —
    NOT the metrics keys. This is load-bearing security (Gate-1 BLOCK-3): folding over the
    metrics keys would emit a health entry for a skill the caller cannot see (an internal
    skill filtered out of the visible list), leaking its name via the map key. So a
    metrics row whose skill is absent from ``skill_names`` is dropped; a visible skill
    with no metrics row becomes ``never_used``.

    ``recent_invocations`` / ``corrections``: per skill, the invocations and the review's
    corrections naming it in the last ``WINDOW_DAYS`` (any key form, canonicalized here).
    ``success_rate`` (None when not invoked in the window) / ``last_used`` are carried for
    the DETAIL drawer; the row uses only
    ``status`` (no raw counts on the scannable surface — R30#4). ``invocation_count`` is
    the RAW frequency — it drives the Most-Used strip + within-group sort ORDER on the
    frontend, and is NOT rendered as a number on any card (R30#4); ``None`` when never used.

    NAME CANONICALIZATION + MERGE (Gate-2 HIGH + meta-review cross-fix HIGH, run_a85e6641):
    the metrics store records ``skill_name`` in BOTH formats — bare (``pdf``, from the SDK
    tool_use input path) and ``s_``-prefixed (``s_pdf``, from the summary-parse path); the
    prod DB has BOTH live for the same skill (6 collision pairs, e.g. deep-research: a bare
    row with 199 recent invocations AND an s_ row with 17 old ones). The visible list is
    always ``folder_name`` (``s_pdf``). So two things:
    (1) the join is keyed on the ``s_``-STRIPPED canonical name on BOTH sides — else a skill
        recorded under its bare name folds to ``never_used`` despite heavy use;
    (2) colliding rows are MERGED, not last-write-replaced — a naive
        ``{canon: s for s in all_stats}`` silently drops one row (SQL GROUP BY order decides
        which, non-deterministic across SQLite versions), so deep-research would take the OLD
        minority row → a wrong/stale dot for a healthy skill. Merge = SUM invocations,
        MAX(last_used).
    """
    _canon = canon

    def _by_canon(counts: Optional[dict[str, int]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, count in (counts or {}).items():
            out[canon(name)] = out.get(canon(name), 0) + int(count)
        return out

    recent, corrected = _by_canon(recent_invocations), _by_canon(corrections)

    # Merge all metrics rows sharing a canonical name into one aggregate (SUM invocations,
    # invocation-weighted success_rate, MAX last_used) — never last-write-replace.
    merged: dict[str, SkillStats] = {}
    for s in all_stats:
        key = _canon(s.skill_name)
        prev = merged.get(key)
        if prev is None:
            merged[key] = s
            continue
        total = prev.invocation_count + s.invocation_count
        weighted = (
            (prev.success_rate * prev.invocation_count + s.success_rate * s.invocation_count) / total
            if total else 0.0
        )
        merged[key] = SkillStats(
            skill_name=key,
            invocation_count=total,
            success_rate=weighted,
            avg_duration=max(prev.avg_duration, s.avg_duration),
            correction_rate=(
                (prev.correction_rate * prev.invocation_count + s.correction_rate * s.invocation_count) / total
                if total else 0.0
            ),
            last_used=max(prev.last_used or "", s.last_used or ""),  # ISO date strings sort correctly
        )

    result: dict[str, dict] = {}
    for name in skill_names:
        stats = merged.get(_canon(name))
        runs, fixes = recent.get(_canon(name), 0), corrected.get(_canon(name), 0)
        result[name] = {
            "status": fold_status(stats, staleness_days, recent_invocations=runs, corrections=fixes),
            "success_rate": success_in_window(runs, fixes) if stats else None,
            "last_used": (stats.last_used or None) if stats else None,
            # RAW frequency — drives the frontend Most-Used strip + within-group sort ORDER
            # (never shown as a count on a card — R30#4). None for a never-used skill so it
            # sinks below used skills instead of tying at 0 (run_ff4adc88).
            "invocation_count": stats.invocation_count if stats else None,
        }
    return result
