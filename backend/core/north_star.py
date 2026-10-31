"""Swarm's north star, always at hand (SWARMAI.md § My North Star).

The measures of whether Swarm is getting better — the cognition level
(``core.ingestion.cognition_level.level``), the health of the knowledge flow that feeds
it (``core.ingestion.health.overview``) and what waits on the person
(``core.attention_authority.collect``) — are computed here into one snapshot:

- ``headline``: three short lines, injected into every desktop prompt (the prompt reads
  the file only; it never reads a database: SwarmAI TECH, system-prompt purity);
- ``detail``: the full picture, printed by ``python -m scripts.north_star``.

Written to ``<app data>/north_star.json`` by ``refresh()``: at the end of every health
pass, by the lifecycle sweep (about every 30 minutes) and by the command. Every number
carries its window; the headline carries the time it was computed.

Public: ``refresh(now=None)``, ``compute(now=None)``, ``headline_for_prompt(now=None)``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILENAME = "north_star.json"
#: how far the latest week may sit from the earlier weeks' mean and still be "flat"
FLAT_BAND = 0.05


def path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / FILENAME


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def _cognition(level: dict) -> tuple[str, list[str]]:
    """The headline line and the detail lines of the cognition level."""
    weeks = [w for w in level.get("weeks") or [] if w.get("not_corrected_rate") is not None]
    detail = ["Cognition level (outcomes, not storage):"]
    if not weeks:
        return "Cognition: no week with enough sessions yet.", detail + ["  no data"]
    corrected = [(w["week"], 1 - w["not_corrected_rate"], w["sessions"]) for w in weeks]
    week, rate, sessions = corrected[-1]
    earlier = [r for _w, r, _n in corrected[:-1]]
    if earlier:
        mean = sum(earlier) / len(earlier)
        # fewer corrected sessions is better
        move = ("better than" if rate < mean - FLAT_BAND else "worse than" if rate > mean + FLAT_BAND
                else "about the same as")
        trend = f"{move} the {len(earlier)} weeks before (mean {_pct(mean)}, range " \
                f"{_pct(min(earlier))}–{_pct(max(earlier))})"
    else:
        trend = "no earlier week to compare"
    latest = level.get("latest") or {}
    repeats = (f"repeats {_pct(latest.get('repeat_rate'))} of {latest.get('corrections')} recorded corrections"
               if latest.get("repeat_rate") is not None else "repeats: not measured yet")
    audit = level.get("audit") or {}
    outcomes = audit.get("outcomes") or {}
    total = audit.get("total") or 0
    if total:
        top = sorted(outcomes.items(), key=lambda kv: -kv[1])[:2]
        sources = f"; mistakes ({audit.get('month', '')[:7]} audit, {total}): " + ", ".join(
            f"{k} {_pct(v / total)}" for k, v in top)
    else:
        sources = ""
    line = (f"Cognition ({week}, {sessions} sessions): you corrected me in {_pct(rate)} of them, {trend}; "
            f"{repeats}{sources}.")
    for w, r, n in corrected:
        detail.append(f"  {w}: corrected {_pct(r)} of {n} sessions")
    detail.append(f"  {repeats}")
    if total:
        detail.append(f"  where mistakes come from ({audit.get('month')}, {total}): " +
                      ", ".join(f"{k} {v}" for k, v in outcomes.items()))
    used = level.get("used") or {}
    detail.append(f"  knowledge used: {_pct(used.get('used_rate'))} "
                  f"({used.get('mature_used', 0)} of {used.get('mature_written', 0)} entries "
                  f"older than {used.get('window_days', 14)} days)")
    agree = level.get("agreement") or {}
    detail.append(f"  judge vs the review's record: corrected {agree.get('corrected_agreed', 0)}/"
                  f"{agree.get('corrected_total', 0)}, clean {agree.get('clean_agreed', 0)}/"
                  f"{agree.get('clean_total', 0)}")
    return line, detail


def _cdf(overview: dict) -> tuple[str, list[str]]:
    from core.ingestion.health import CHECK_NAMES
    val = overview.get("value") or {}
    low = [f"{d['key']} {_pct(d['rate'])}" for d in val.get("destinations") or []
           if d.get("rate") is not None and d["rate"] < d.get("target", 0)]
    checks = overview.get("checks") or []
    failing = [c for c in checks if c.get("status") == "fail"]
    measured = [c for c in checks if c.get("status") != "no_data"]
    cost = overview.get("cost") or {}
    line = (f"CDF (7 days): useful {_pct(val.get('rate'))} vs target {_pct(val.get('target'))}"
            + (f" (below target: {', '.join(low)})" if low else "")
            + f"; checks {len(measured) - len(failing)}/{len(checks)} pass"
            + (f", failing: {', '.join(c['id'] + ' ' + CHECK_NAMES.get(c['id'], ('',))[0] for c in failing)}"
               if failing else "")
            + (f"; cost ${cost.get('cdf_usd', 0):.0f} ({_pct(cost.get('share'))} of all)" if cost else "")
            + ".")
    detail = ["CDF health (7 days):",
              f"  useful {_pct(val.get('rate'))} of {val.get('scored', 0)} scored, target {_pct(val.get('target'))}"]
    for d in val.get("destinations") or []:
        detail.append(f"  {d['key']}: {_pct(d.get('rate'))} (target {_pct(d.get('target'))}, "
                      f"{d.get('scored', 0)} scored, {d.get('written', 0)} written)")
    for c in checks:
        name = CHECK_NAMES.get(c["id"], ("",))[0]
        detail.append(f"  {c['id']} {name}: {c['status']} {json.dumps(c.get('value'), ensure_ascii=False)} "
                      f"(needs {c.get('requirement')})")
    if cost:
        detail.append(f"  cost: CDF ${cost.get('cdf_usd', 0):.2f} of ${cost.get('system_usd', 0):.2f}"
                      f" ({_pct(cost.get('share'))}); per useful entry ${cost.get('per_useful_usd') or 0:.2f}")
        for f in cost.get("features") or []:
            detail.append(f"    {f['feature']}: ${f['usd']:.2f} over {f['calls']} calls")
    reading = (overview.get("reading") or {}).get("en") or {}
    for part in ("seen", "why", "suggest"):
        for text in reading.get(part) or []:
            detail.append(f"  {part}: {text}")
    return line, detail


def _needs_you(attention: dict) -> tuple[str, list[str]]:
    items = attention.get("items") or []
    counts = attention.get("counts") or {}
    if not items:
        return "Needs You: nothing.", ["Needs You: nothing."]
    shown = "; ".join(f"[{it['tier']}] {it['title']}" for it in items[:3])
    more = f"; +{len(items) - 3} more" if len(items) > 3 else ""
    line = (f"Needs You: {counts.get('blocking', 0)} blocking, {counts.get('review', 0)} to review"
            f" — {shown}{more}.")
    detail = ["Needs You:"] + [f"  [{it['tier']}] {it['source']} · {it['title']} · {it.get('summary', '')}"
                               for it in items]
    return line, detail


def compute(now: datetime | None = None) -> dict:
    """The snapshot: ``{"at", "headline", "detail"}``. A part that cannot be read says
    so in its line; it never fails the whole."""
    now = now or datetime.now(timezone.utc)
    lines: list[str] = []
    detail: list[str] = []
    parts = (
        ("cognition", lambda: _cognition(__import__("core.ingestion.cognition_level",
                                                    fromlist=["level"]).level(now))),
        ("cdf", lambda: _cdf(__import__("core.ingestion.health", fromlist=["overview"]).overview("7d", now=now))),
        ("needs_you", lambda: _needs_you(_attention())),
    )
    for name, fn in parts:
        try:
            line, more = fn()
        except Exception as exc:  # noqa: BLE001 -- one unreadable part is said, never fatal
            logger.warning("north star: %s unreadable", name, exc_info=True)
            line, more = f"{name}: unreadable ({type(exc).__name__})", []
        lines.append(line)
        detail += more + [""]
    return {"at": now.isoformat(timespec="seconds"), "headline": lines, "detail": detail}


def _attention() -> dict:
    from config import get_swarmws
    from core.attention_authority import collect
    return collect(Path(get_swarmws())).to_dict()


def refresh(now: datetime | None = None) -> dict:
    """Compute the snapshot and write it (atomically). Returns it. A worker Hive keeps
    none (its prompt carries no north star)."""
    from core.app_role import is_worker
    if is_worker():
        return {}
    snap = compute(now)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".north_star.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return snap


def headline_for_prompt(now: datetime | None = None) -> str:
    """The headline as the prompt shows it, with its age, read from the file only
    ("" when there is no snapshot yet)."""
    try:
        snap = json.loads(path().read_text(encoding="utf-8"))
        at = datetime.fromisoformat(snap["at"])
        lines = [str(x) for x in snap.get("headline") or []]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    if not lines:
        return ""
    now = now or datetime.now(timezone.utc)
    minutes = max(0, int((now - at).total_seconds() // 60))
    age = f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"
    return "\n".join([f"(as of {at.isoformat(timespec='minutes')}, {age}; detail: "
                      '`"$SWARM_BACKEND_BIN" py -m scripts.north_star`)'] + [f"- {x}" for x in lines])
