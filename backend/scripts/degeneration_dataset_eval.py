"""Replay real CLI transcripts through the output-degeneration detector.

Local-only evaluation (the transcripts are private and never leave this
machine — nothing here is committed as data). It answers the question the
constants in ``core.output_degeneration`` rest on: on real assistant output,
does the live detector separate legitimate text / tool input from repetition
loops, and with what margin?

For every assistant text block and tool_use input (JSON-serialized) of at
least ``WINDOW`` chars in ``~/.claude/projects/*/*.jsonl`` (plus any ``--extra``
transcript paths, e.g. a backup holding a block that was since cleaned):

1. stream it through ``DegenerationDetector`` in random 1-40 char chunks,
   exactly as deltas arrive live, and record whether it fired;
2. compute its peak window ratio on the live check schedule.

A block is labelled degenerate by its peak ratio. The run FAILS (exit 1) if
the streamed detector disagrees with that label anywhere, if any block sits in
the gray zone (``GRAY_LOW`` <= peak < threshold — the margin claim would no
longer hold), or if fewer degenerate blocks are found than ``--expect-degenerate``.

Usage:
    python backend/scripts/degeneration_dataset_eval.py \\
        [--extra PATH ...] [--since-days 400] [--expect-degenerate 8]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.output_degeneration import (  # noqa: E402
    RATIO_THRESHOLD,
    STEP,
    WINDOW,
    DegenerationDetector,
    compress_ratio,
)

GRAY_LOW = 6.0


def _peak(text: str) -> float:
    best = 0.0
    for end in range(WINDOW, len(text) + 1, STEP):
        best = max(best, compress_ratio(text[end - WINDOW:end]))
    return best


def _streamed_fires(text: str, rng: random.Random) -> bool:
    det = DegenerationDetector()
    i = 0
    while i < len(text):
        n = rng.randint(1, 40)
        if det.feed(text[i:i + n]):
            return True
        i += n
    return False


def _blocks(path: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line or len(line) < WINDOW:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    kind, s = "text", b.get("text") or ""
                elif b.get("type") == "tool_use":
                    kind, s = "tool_input", json.dumps(b.get("input", {}), ensure_ascii=False)
                else:
                    continue
                if len(s) >= WINDOW:
                    yield kind, s, msg.get("model", "?"), rec.get("timestamp", "")[:19]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extra", nargs="*", default=[], help="extra transcript paths")
    ap.add_argument("--since-days", type=float, default=400.0)
    ap.add_argument("--expect-degenerate", type=int, default=0)
    args = ap.parse_args()

    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    cutoff = time.time() - args.since_days * 86400
    paths = [p for p in glob.glob(os.path.join(root, "projects", "*", "*.jsonl"))
             if os.path.getmtime(p) >= cutoff]
    paths += [p for p in args.extra if os.path.isfile(p)]

    rng = random.Random(20261017)
    counts = {"text": 0, "tool_input": 0}
    peaks = {"text": 0.0, "tool_input": 0.0}
    degenerate, gray, mismatch = [], [], []
    for path in paths:
        for kind, s, model, ts in _blocks(path):
            peak = _peak(s)
            fired = _streamed_fires(s, rng)
            is_degen = peak >= RATIO_THRESHOLD
            where = f"{os.path.basename(path)[:8]} {ts} {model} {kind} len={len(s)} peak={peak:.1f}"
            if fired != is_degen:
                mismatch.append(where + f" fired={fired}")
            if is_degen:
                degenerate.append(where)
                continue
            counts[kind] += 1
            peaks[kind] = max(peaks[kind], peak)
            if peak >= GRAY_LOW:
                gray.append(where)

    print(f"transcripts scanned: {len(paths)}")
    for kind in counts:
        print(f"legit {kind:10s}: {counts[kind]:5d} blocks, peak ratio {peaks[kind]:.2f}")
    print(f"degenerate blocks: {len(degenerate)} (threshold {RATIO_THRESHOLD})")
    for d in degenerate:
        print("   ", d)
    print(f"gray zone [{GRAY_LOW}, {RATIO_THRESHOLD}): {len(gray)}")
    for g in gray:
        print("   ", g)
    print(f"stream/offline mismatches: {len(mismatch)}")
    for m in mismatch:
        print("   ", m)
    ok = not gray and not mismatch and len(degenerate) >= args.expect_degenerate
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
