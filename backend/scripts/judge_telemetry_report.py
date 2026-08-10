#!/usr/bin/env python3
"""Judge Telemetry Report — the READ side of judge telemetry.

The judge (self_adversarial_judge) is the single chokepoint every ingestion door
funnels through (P8). ingestion_gate logs EVERY verdict to
`.context/judge-telemetry.jsonl`. This script turns that raw log into the gauge we
were missing: the judge's real pass/suspect/noise distribution + a human-eyeballable
list of what it DISCARDED (so "the judge rejected 21/21" is measurable, not blind).

Why this exists (run this session): we removed the human-review queue in favour of
"judge decides, non-pass → recoverable archive". That is only safe if the judge is
well-calibrated. A discard pile nobody reads = silent knowledge loss. This report is
the weekly look at that pile: if real knowledge shows up in the discard list, the
judge is too harsh; if it's all junk, autonomy-first is validated.

Usage:
    python judge_telemetry_report.py [--days 7] [--json] [--write]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _telemetry_path() -> Path:
    from jobs.paths import CONTEXT_DIR
    return CONTEXT_DIR / "judge-telemetry.jsonl"


def _job_results_dir() -> Path:
    from jobs.paths import JOB_RESULTS_DIR
    return JOB_RESULTS_DIR


def _load_rows(path: Path, since: datetime | None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # tolerate a partial/corrupt tail line
        if since is not None:
            ts = r.get("ts", "")
            try:
                when = datetime.fromisoformat(ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                when = None
            if when is not None and when < since:
                continue
        rows.append(r)
    return rows


def analyze(rows: list[dict]) -> dict:
    total = len(rows)
    verdicts = Counter(r.get("verdict", "?") for r in rows)
    by_section = Counter(r.get("section", "?") for r in rows)
    # fail-closed count: verdict==suspect with an error/unparseable reason
    fail_closed = sum(
        1 for r in rows
        if r.get("verdict") == "suspect"
        and any(k in (r.get("reason", "").lower())
                for k in ("error", "unparseable", "empty", "timeout", "budget"))
    )
    passed = verdicts.get("pass", 0)
    discarded = [r for r in rows if r.get("verdict") in ("suspect", "noise")]
    return {
        "total": total,
        "verdicts": dict(verdicts),
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "discard_rate": round(len(discarded) / total, 4) if total else 0.0,
        "fail_closed": fail_closed,
        "by_section": dict(by_section.most_common(15)),
        "discarded": discarded,
    }


def render(stats: dict, days: int) -> str:
    t = stats["total"]
    lines: list[str] = []
    lines.append(f"# Judge Telemetry Report — last {days}d")
    lines.append("")
    if t == 0:
        lines.append("_No judge verdicts logged in window. Either the daemon hasn't "
                     "run distillation/cultivation since deploy, or telemetry isn't "
                     "wired on the running binary._")
        return "\n".join(lines)
    v = stats["verdicts"]
    lines.append(f"**{t}** verdicts · "
                 f"pass **{stats['pass_rate']*100:.0f}%** ({v.get('pass',0)}) · "
                 f"suspect {v.get('suspect',0)} · noise {v.get('noise',0)} · "
                 f"discard **{stats['discard_rate']*100:.0f}%**")
    lines.append("")
    # Calibration alarms — the whole point of the gauge.
    # ORDER MATTERS: a high fail-closed share means the judge didn't actually JUDGE
    # (infra failure faked as 'suspect'), which INVALIDATES any pass/discard read —
    # so it's checked FIRST and dominates the verdict-distribution signal.
    lines.append("## Calibration signal")
    fc = stats["fail_closed"]
    fc_rate = fc / t if t else 0.0
    real_judged = t - fc  # verdicts the judge actually produced
    if fc_rate >= 0.25 and t >= 4:
        lines.append(f"- 🔴 **{fc_rate*100:.0f}% fail-closed** ({fc}/{t}) — the judge did "
                     f"NOT actually run on these (Bedrock error/timeout/budget → forced "
                     f"'suspect'). The pass/discard numbers below are UNRELIABLE until infra "
                     f"is fixed; only **{real_judged}** verdicts are real judgments.")
    elif fc:
        lines.append(f"- ⚠️ **{fc}** fail-closed verdicts (infra, not judgment) — "
                     f"excluded from the calibration read below.")
    # pass/discard calibration — computed over REAL judgments only
    if real_judged >= 5:
        real_pass = stats["verdicts"].get("pass", 0)
        real_pass_rate = real_pass / real_judged
        if real_pass_rate == 0.0:
            lines.append(f"- 🔴 **0% pass over {real_judged} real judgments** — judge may be "
                         f"broken-strict (indistinguishable from a dead judge). Eyeball discards.")
        elif real_pass_rate >= 0.95:
            lines.append(f"- 🟡 **≥95% pass** ({real_pass}/{real_judged}) — judge may be "
                         f"rubber-stamping. Spot-check a few passes.")
        else:
            lines.append(f"- 🟢 real-judgment pass rate {real_pass_rate*100:.0f}% "
                         f"({real_pass}/{real_judged}) within a plausible band.")
    else:
        lines.append(f"- ℹ️ only {real_judged} real judgments — too few to call calibration.")
    lines.append("")
    lines.append("## By section (top 15)")
    for sec, n in stats["by_section"].items():
        lines.append(f"- `{sec}` — {n}")
    lines.append("")
    # The discard pile — human eyeball to catch a too-harsh judge
    disc = stats["discarded"]
    lines.append(f"## Discarded pile ({len(disc)}) — eyeball for real knowledge wrongly killed")
    if not disc:
        lines.append("_Nothing discarded._")
    else:
        for r in disc[:100]:
            preview = (r.get("text", "") or "").replace("\n", " ")[:200]
            lines.append(f"- **{r.get('verdict','?')}** "
                         f"[`{r.get('section','?')}`] "
                         f"({r.get('reason','')}) — {preview}")
        if len(disc) > 100:
            lines.append(f"- …and {len(disc)-100} more (see raw jsonl).")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Judge telemetry — pass/discard gauge + discard pile")
    ap.add_argument("--days", type=int, default=7, help="window in days (0 = all)")
    ap.add_argument("--json", action="store_true", help="emit JSON stats to stdout")
    ap.add_argument("--write", action="store_true", help="write markdown report to JobResults/")
    args = ap.parse_args()

    since = None
    if args.days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
    rows = _load_rows(_telemetry_path(), since)
    stats = analyze(rows)

    if args.json:
        # drop the heavy 'discarded' text blob from stdout json summary
        summary = {k: v for k, v in stats.items() if k != "discarded"}
        summary["discarded_count"] = len(stats["discarded"])
        print(json.dumps(summary, ensure_ascii=False))
        return

    report = render(stats, args.days)
    if args.write:
        out_dir = _job_results_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = out_dir / f"{stamp}-judge-telemetry.md"
        out.write_text(report, encoding="utf-8")
        print(json.dumps({"report": str(out), "total": stats["total"]}))
    else:
        print(report)


if __name__ == "__main__":
    main()
