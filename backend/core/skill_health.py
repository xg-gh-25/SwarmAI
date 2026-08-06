"""Skill health status folding — SkillStats → a qualitative, scannable status enum.

The Capabilities panel (run_a85e6641) shows an at-a-glance HEALTH dot per skill. This
module folds the existing ``SkillMetricsStore`` statistics into a small qualitative enum
so the panel can render a colored dot WITHOUT surfacing raw invocation counts on the row
(R30#4 — volatile decision-inert numbers do not belong on a scannable surface; the raw
success_rate/last_used are carried for the DETAIL drawer only).

Pure functions — NO I/O, NO session context. The router (``routers/skills.py``) reads
``SkillMetricsStore.get_all_stats()`` and calls :func:`build_health_map`; that keeps this
module trivially unit-testable and keeps the HIGH-risk (20-caller) metrics store untouched.

The 4 statuses (severity-ordered, most-important-to-surface first):

- ``never_used``  — no metric rows at all (a candidate to RETIRE — the whole point of the dot)
- ``low_success`` — lifetime success_rate < 0.7 AND enough invocations to trust it
- ``stale``       — last used longer ago than the staleness window
- ``healthy``     — recently used, succeeding

Naming honesty (Gate-1, run_a85e6641): the metric is LIFETIME ``success_rate`` — it has NO
recency — so the status is ``low_success``, NOT ``recently_failed`` (that name would be a
lie; R30#4 bans a misleading label). A floor of ``invocation_count >= 5`` (mirrors
``SkillMetricsStore.get_evolution_candidates``, skill_metrics.py:173 — the system's own
health heuristic) prevents a 1-of-2-failed skill from showing ``low_success`` forever on
no real signal.
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

# Minimum lifetime invocations before a low success_rate is trusted as `low_success`.
# Mirrors get_evolution_candidates (skill_metrics.py:173) — the system's own floor.
MIN_INVOCATIONS_FOR_LOW_SUCCESS = 5

# The success_rate below which a well-exercised skill is flagged low_success.
# Mirrors get_evolution_candidates' success_rate<0.7 threshold (skill_metrics.py:175).
LOW_SUCCESS_THRESHOLD = 0.7


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


def fold_status(stats: Optional[SkillStats], staleness_days: int = STALENESS_DAYS) -> str:
    """Fold a skill's stats into one qualitative status.

    Severity order (first match wins — most-important-to-surface first):
    ``never_used`` > ``low_success`` > ``stale`` > ``healthy``.

    - ``stats is None`` → ``never_used`` (no metric rows exist for this skill).
    - ``low_success`` requires BOTH success_rate < threshold AND enough invocations to
      trust it (the floor) — so a rarely-run skill is never libeled.
    - ``stale`` only fires when recency is KNOWN (parseable date) and exceeds the window;
      an unknown/dateless recency is never assumed stale.
    - otherwise ``healthy``.
    """
    if stats is None:
        return "never_used"

    if (
        stats.invocation_count >= MIN_INVOCATIONS_FOR_LOW_SUCCESS
        and stats.success_rate < LOW_SUCCESS_THRESHOLD
    ):
        return "low_success"

    days = _days_since(stats.last_used)
    if days is not None and days > staleness_days:
        return "stale"

    return "healthy"


def build_health_map(
    all_stats: list[SkillStats],
    skill_names: list[str],
    staleness_days: int = STALENESS_DAYS,
) -> dict[str, dict]:
    """Build a ``{folder_name: {status, success_rate, last_used}}`` map.

    Keys are strictly the ``skill_names`` list (the caller-VISIBLE skill folder names) —
    NOT the metrics keys. This is load-bearing security (Gate-1 BLOCK-3): folding over the
    metrics keys would emit a health entry for a skill the caller cannot see (an internal
    skill filtered out of the visible list), leaking its name via the map key. So a
    metrics row whose skill is absent from ``skill_names`` is dropped; a visible skill
    with no metrics row becomes ``never_used``.

    ``success_rate``/``last_used`` are carried for the DETAIL drawer; the row uses only
    ``status`` (no raw counts on the scannable surface — R30#4).

    NAME CANONICALIZATION (Gate-2 HIGH, run_a85e6641): the metrics store records
    ``skill_name`` in BOTH formats — bare (``pdf``, from the SDK tool_use input path) and
    ``s_``-prefixed (``s_pdf``, from the summary-parse path); the prod DB has both live.
    The visible list is always ``folder_name`` (``s_pdf``). So the join is keyed on the
    ``s_``-STRIPPED canonical name on BOTH sides — else a skill recorded under its bare
    name would falsely fold to ``never_used`` despite heavy use. (Same canonicalization the
    recorder already uses for eval JSONL — skill_metrics_hook ``removeprefix('s_')``.)
    """
    def _canon(n: str) -> str:
        return n[2:] if n.startswith("s_") else n

    by_name = {_canon(s.skill_name): s for s in all_stats}
    result: dict[str, dict] = {}
    for name in skill_names:
        stats = by_name.get(_canon(name))
        result[name] = {
            "status": fold_status(stats, staleness_days),
            "success_rate": stats.success_rate if stats else None,
            "last_used": (stats.last_used or None) if stats else None,
        }
    return result
