"""Swarm's north star, always at hand (SWARMAI.md § My North Star).

The measures of whether Swarm is getting better — the cognition level
(``core.ingestion.cognition_level.level``), the health of the knowledge flow that feeds
it (``core.ingestion.health.overview``) and what waits on the person
(``core.attention_authority.collect``) — are computed here into one snapshot:

- ``headline``: three short lines, injected into every desktop prompt (the prompt reads
  the file only; it never reads a database: SwarmAI TECH, system-prompt purity). The CDF
  line ends with the sizes of MEMORY and EVOLUTION, injected whole with no budget, so the
  agent sees a rise and consolidates by declaration (``_every_turn_sizes``);
- ``detail``: the full picture, printed by ``python -m scripts.north_star``, with
  each accepted governance rule's repeats before and after its acceptance;
- ``measures``: the same readings as numbers, for the C&M overlay's rail
  (``measures_for_ui``, served by ``GET /api/eval/context-health/lite``).

Written to ``<app data>/north_star.json`` by ``refresh()``: at the end of every health
pass, by the lifecycle sweep (about every 30 minutes) and by the command. Every number
carries its window; the headline carries the time it was computed.

Public: ``refresh(now=None)``, ``compute(now=None)``, ``headline_for_prompt(now=None)``,
``measures_for_ui()``.
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


def path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / FILENAME


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def trend_verdict(trend: dict | None) -> str:
    """The verdict on the corrected share's trend (``cognition_level.trend``), in the words
    the cognition tab and the C&M rail also use (en locale ``cogLevel.verdict``; both are
    held to ``tests/fixtures/north_star_trend_wording.json``): "better" or "worse" only
    when the 95% interval excludes zero, otherwise "no visible change (±X points)"."""
    direction = (trend or {}).get("direction")
    if direction in ("better", "worse"):
        return direction
    if direction == "same":
        return f"no visible change (±{trend['margin']} points)"
    return "too few sessions for a trend"


def _points(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100):+d}"


def _cognition(level: dict) -> tuple[str, list[str], dict | None]:
    """The headline line, the detail lines and the measures of the cognition level. The
    trend is the backend's one (``level["trend"]``: four weeks pooled against the four
    before, with its 95% interval); this only words it."""
    weeks = [w for w in level.get("weeks") or [] if w.get("not_corrected_rate") is not None]
    detail = ["Cognition level (outcomes, not storage):"]
    tr = level.get("trend") or None
    now = (tr or {}).get("now") or {}
    before = (tr or {}).get("before") or {}
    verdict = trend_verdict(tr)
    if now.get("rate") is not None:
        week, rate, sessions = now["to"], now["rate"], now["judged"]
        compared = (f", {_pct(before['rate'])} in the {tr['weeks']} weeks before"
                    if before.get("rate") is not None else "")
        head = (f"Cognition ({tr['weeks']} weeks to {week}, {sessions} sessions judged): "
                f"you corrected me in {_pct(rate)} of them{compared}: {verdict}")
    elif weeks:
        week, rate, sessions = weeks[-1]["week"], 1 - weeks[-1]["not_corrected_rate"], weeks[-1]["sessions"]
        head = f"Cognition ({week}, {sessions} sessions): you corrected me in {_pct(rate)} of them; {verdict}"
    else:
        return "Cognition: no week with enough sessions yet.", detail + ["  no data"], None
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
    line = f"{head}; {repeats}{sources}."
    for w in weeks:
        detail.append(f"  {w['week']}: corrected {_pct(1 - w['not_corrected_rate'])} of {w['sessions']} sessions")
    if now.get("judged") is not None:
        interval = (f"; difference {_points(tr['difference'])} points, 95% interval "
                    f"{_points(tr['low'])} to {_points(tr['high'])}" if tr.get("low") is not None else "")
        detail.append(f"  trend: {now['from']}–{now['to']} corrected {_pct(now.get('rate'))} of {now['judged']} "
                      f"judged, {before['from']}–{before['to']} {_pct(before.get('rate'))} of "
                      f"{before['judged']}{interval}: {verdict}")
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
    measures = {
        # the trend's window when there is one (four weeks to ``week``), else the latest week
        "week": week, "sessions": sessions, "corrected_rate": round(rate, 4),
        "window_weeks": tr["weeks"] if now.get("rate") is not None else 1,
        "before_rate": before.get("rate") if now.get("rate") is not None else None,
        "margin": (tr or {}).get("margin"), "low": (tr or {}).get("low"), "high": (tr or {}).get("high"),
        "direction": (tr or {}).get("direction") if now.get("rate") is not None else None,
        "repeat_rate": latest.get("repeat_rate"), "corrections": latest.get("corrections"),
    }
    return line, detail, measures


def _cdf(overview: dict) -> tuple[str, list[str], dict]:
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
    measures = {
        "useful_rate": val.get("rate"), "target": val.get("target"),
        "below_target": [d["key"] for d in val.get("destinations") or []
                         if d.get("rate") is not None and d["rate"] < d.get("target", 0)],
        "checks_total": len(checks), "checks_failing": [c["id"] for c in failing],
    }
    return line, detail, measures


# The every-turn files injected whole with no budget, as prompt_metrics names their sections.
_EVERY_TURN = (("EVOLUTION", "Evolution Registry"), ("MEMORY", "Memory"))
SIZE_DAYS = 8          # today and the 7 days before it


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}K"


def _every_turn_sizes(now: datetime) -> tuple[str, dict, dict]:
    """``("; every-turn files: EVOLUTION 24.3K tokens (18.1K 7 days ago), ...", measures, days)``.

    MEMORY and EVOLUTION are injected whole; a clear rise is answered by the agent
    consolidating the file by declaration (design 2026-10-30-cdf-agent-decides §4), which
    it can do only if it sees the size in every prompt (design
    2026-11-02-knowledge-reaches-judgment ⑤). The newest and the oldest day on record in
    the window, and ``days`` how many days lie between them per file (0: one day on
    record, no earlier value; a reader must not call the older value "7 days ago" unless
    it is); "" when there is no record."""
    from core import prompt_metrics
    days = prompt_metrics.daily(SIZE_DAYS, today=now)
    parts, measures, spans = [], {}, {}
    for name, section in _EVERY_TURN:
        seen = [(d["day"], (d.get("files") or {})[section]["last"]) for d in days
                if section in (d.get("files") or {})]
        if not seen:
            continue
        (first_day, first), (last_day, last) = seen[0], seen[-1]
        measures[name] = [last, first]
        span = (datetime.fromisoformat(last_day) - datetime.fromisoformat(first_day)).days
        spans[name] = span
        parts.append((name, last, first, span))
    if not parts:
        return "", {}, {}
    shown = []
    for i, (name, last, first, span) in enumerate(parts):
        unit = " tokens" if i == 0 else ""
        # each file its own span; one day on record has no earlier value to show
        before = "" if span <= 0 else f" ({_k(first)} {span} days ago)" if i == 0 or span != parts[0][3] \
            else f" ({_k(first)})"
        shown.append(f"{name} {_k(last)}{unit}{before}")
    return "; every-turn files: " + ", ".join(shown), measures, spans


def _needs_you(attention: dict) -> tuple[str, list[str], None]:
    items = attention.get("items") or []
    if not items:
        return "Needs You: nothing.", ["Needs You: nothing."], None
    mine = [it for it in items if it.get("handler", "person") == "person"]
    swarm = len(items) - len(mine)
    handled = f"; Swarm handles {swarm} itself" if swarm else ""
    if not mine:
        line = f"Needs You: nothing for you{handled}."
    else:
        shown = "; ".join(f"[{it['tier']}] {it['title']}" for it in mine[:3])
        more = f"; +{len(mine) - 3} more" if len(mine) > 3 else ""
        line = f"Needs You: {len(mine)} for you — {shown}{more}{handled}."
    detail = ["Needs You:"] + [f"  [{it['tier']}] {it['source']} · {it['title']} · {it.get('summary', '')}"
                               + ("" if it.get("handler", "person") == "person" else " · Swarm handles it")
                               for it in items]
    return line, detail, None


def _with_sizes(part: tuple[str, list[str], dict], now: datetime) -> tuple[str, list[str], dict]:
    """The CDF part with the every-turn file sizes on its line (the headline stays three
    lines); an unreadable size record leaves the line as it was."""
    line, detail, measures = part
    try:
        sizes, numbers, spans = _every_turn_sizes(now)
    except Exception:  # noqa: BLE001 -- the sizes never cost the CDF line
        logger.warning("north star: every-turn sizes unreadable", exc_info=True)
        return part
    if not sizes:
        return part
    line = line[:-1] + sizes + "." if line.endswith(".") else line + sizes
    measures = dict(measures or {}, every_turn=numbers, every_turn_days=spans)
    return line, detail + [f"  every-turn files (tokens, newest vs oldest of {SIZE_DAYS} days): "
                           + ", ".join(f"{k} {v[0]} vs {v[1]}" for k, v in numbers.items())], measures


def compute(now: datetime | None = None) -> dict:
    """The snapshot: ``{"at", "headline", "detail", "measures"}``. A part that cannot be
    read says so in its line and has no measures (None); it never fails the whole."""
    now = now or datetime.now(timezone.utc)
    lines: list[str] = []
    detail: list[str] = []
    measures: dict[str, Any] = {}
    parts = (
        ("cognition", lambda: _cognition(__import__("core.ingestion.cognition_level",
                                                    fromlist=["level"]).level(now))),
        ("cdf", lambda: _with_sizes(_cdf(__import__("core.ingestion.health", fromlist=["overview"]).overview("7d", now=now)), now)),
        ("needs_you", lambda: _needs_you(_attention())),
    )
    for name, fn in parts:
        try:
            line, more, measure = fn()
        except Exception as exc:  # noqa: BLE001 -- one unreadable part is said, never fatal
            logger.warning("north star: %s unreadable", name, exc_info=True)
            line, more, measure = f"{name}: unreadable ({type(exc).__name__})", [], None
        lines.append(line)
        detail += more + [""]
        if name != "needs_you":
            measures[name] = measure
    try:
        detail += _governance_lines(_accepted(now))
    except Exception:  # noqa: BLE001 -- the accepted rules are detail only, never fatal
        logger.warning("north star: accepted rules unreadable", exc_info=True)
    return {"at": now.isoformat(timespec="seconds"), "headline": lines, "detail": detail,
            "measures": measures}


def _governance_lines(accepted: list[dict]) -> list[str]:
    """Whether each accepted rule stopped its mistake recurring (detail only)."""
    if not accepted:
        return []
    out = ["Accepted rules (repeats of their mistake, the same number of days before and after):"]
    for a in accepted:
        if a.get("measured"):
            out.append(f"  {a['gc_id']} accepted {a['accepted_on']} ({a['days']} days): "
                       f"{a['before']} before, {a['after']} after — {a['title']}")
        else:
            out.append(f"  {a['gc_id']} accepted {a['accepted_on']}: not measured ({a.get('reason')}) — {a['title']}")
    return out + [""]


def _accepted(now: datetime) -> list[dict]:
    from core.evolution.governance_miner import accepted_outcomes, workspace_gc_table
    return accepted_outcomes(workspace_gc_table(), now=now)


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


def measures_for_ui() -> dict | None:
    """``{"at", "measures"}`` from the snapshot file, for the C&M overlay (read from the
    file only, like the prompt). None when there is no snapshot; ``measures`` is ``{}``
    for a snapshot written before measures existed (the next refresh adds them)."""
    try:
        snap = json.loads(path().read_text(encoding="utf-8"))
        at = str(snap["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    measures = snap.get("measures")
    return {"at": at, "measures": measures if isinstance(measures, dict) else {}}
