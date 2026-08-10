#!/usr/bin/env python3
"""One-shot MEMORY.md LLM-judge audit — REPORT ONLY, never deletes.

Runs every real knowledge entry (excl. the auto-generated Memory Index) through the
self_adversarial judge, self-rate-limited to dodge the 60/300s budget + Bedrock
throttle, with a retry on fail-closed (the 44%-fail-closed trap from run_0d60e04e:
an infra failure is NOT a verdict). Emits a JSON report bucketed pass / suspect /
noise / fail-closed. Deletion is a SEPARATE human-reviewed step — this only measures.
"""
from __future__ import annotations
import sys, re, json, time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from core.ingestion_gate import _self_adversarial_judge_impl, _append_judge_telemetry  # noqa: E402

MEM = Path("/Users/gawan/.swarm-ai/SwarmWS/.context/MEMORY.md")
OUT = Path("/Users/gawan/.swarm-ai/SwarmWS/Knowledge/JobResults")
SLEEP_S = 5.5          # > 300/60 so we never trip the budget window
RETRY_FAILCLOSED = 2   # infra failure ≠ verdict → retry before believing it
RETRY_BACKOFF_S = 20


def _parse_entries():
    lines = MEM.read_text(encoding="utf-8").splitlines()
    section = None
    entries = []
    cur = None
    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:].strip()
            continue
        if section == "Memory Index":
            continue
        if ln.startswith("- ["):
            cur = {"section": section, "text": ln, "title": _title(ln)}
            entries.append(cur)
        elif cur is not None and ln.startswith("  ") and not ln.strip().startswith("<!--"):
            cur["text"] += " " + ln.strip()
    return entries


def _title(t):
    m = re.search(r"\*\*(.+?)\*\*", t)
    return (m.group(1) if m else t[:60]).strip()


def _content(t):
    m = re.match(r"^- \[[a-z]+\]\s*(.*)$", t)
    return (m.group(1) if m else t).strip()


def _judge_with_retry(text, section):
    """Return (verdict, reason, failclosed_bool). Retry on infra-failure suspect."""
    for attempt in range(RETRY_FAILCLOSED + 1):
        verdict, reason = _self_adversarial_judge_impl(text, section, [])
        is_failclosed = verdict == "suspect" and any(
            k in reason.lower() for k in ("error", "unparseable", "empty", "timeout"))
        # telemetry (real audit traffic — record it)
        try:
            _append_judge_telemetry(text, section, verdict, reason)
        except Exception:
            pass
        if not is_failclosed:
            return verdict, reason, False
        if attempt < RETRY_FAILCLOSED:
            time.sleep(RETRY_BACKOFF_S)
    return verdict, reason, True  # still fail-closed after retries


def main():
    entries = _parse_entries()
    buckets = {"pass": [], "suspect": [], "noise": [], "failclosed": []}
    t0 = time.time()
    for i, e in enumerate(entries):
        c = _content(e["text"])
        verdict, reason, fc = _judge_with_retry(c, e["section"])
        row = {"section": e["section"], "title": e["title"],
               "verdict": verdict, "reason": reason, "preview": c[:180]}
        if fc:
            buckets["failclosed"].append(row)
        else:
            buckets[verdict].append(row)
        # progress heartbeat every 10
        if (i + 1) % 10 == 0:
            el = int(time.time() - t0)
            sys.stderr.write(f"[{i+1}/{len(entries)}] {el}s  "
                             f"pass={len(buckets['pass'])} suspect={len(buckets['suspect'])} "
                             f"noise={len(buckets['noise'])} fc={len(buckets['failclosed'])}\n")
            sys.stderr.flush()
        time.sleep(SLEEP_S)

    total = len(entries)
    real = total - len(buckets["failclosed"])
    summary = {
        "total": total,
        "real_judgments": real,
        "counts": {k: len(v) for k, v in buckets.items()},
        "pass_rate_of_real": round(len(buckets["pass"]) / real, 3) if real else 0.0,
        "failclosed_rate": round(len(buckets["failclosed"]) / total, 3) if total else 0.0,
        "elapsed_s": int(time.time() - t0),
        "buckets": buckets,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "memory-judge-audit.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stderr.write(f"DONE → {out}\n")
    print(json.dumps({k: summary[k] for k in
                      ("total", "real_judgments", "counts", "pass_rate_of_real",
                       "failclosed_rate", "elapsed_s")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
