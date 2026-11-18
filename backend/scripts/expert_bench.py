"""Review expert benchmark — command line (design docs/design/2026-11-18-expert-review-benchmark.md).

Phase 1, step 1: extract the S1 cases of a repository (no model is called).

    "$SWARM_BACKEND_BIN" py -m scripts.expert_bench extract --repo <checkout> --out cases.jsonl \\
        [--test-cmd "<command with {tests}>" [--test-cwd <dir>] [--red-pattern <regex>]] \\
        [--grep <regex>] [--reviewed-pattern <regex>] [--start N] [--limit N] \\
        [--max-files 6] [--owner-share 0.8] [--max-diff-lines 800] [--timeout 300]

Without ``--test-cmd`` the red/green check is skipped and each case says
``confirmed: false``. ``--red-pattern`` is the test runner's own mark of a failed test
(``^FAILED `` for pytest): a red run without it (tests that do not load) is dropped.
``--start`` / ``--limit`` select a slice of the fix candidates (newest first). Each case is
appended to ``--out`` as soon as it is kept, and every candidate's outcome to
``<out>.outcomes.jsonl``; a run that is stopped keeps what it decided, and the next run
skips those candidates. Prints the count of this run's outcomes as one JSON object.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from core.expert_bench import cases as bc


def _rows(path: Path) -> list[dict]:
    """The JSON lines of *path*; a line a stopped run left half written is skipped."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("--repo", required=True, type=Path)
    ex.add_argument("--out", required=True, type=Path)
    ex.add_argument("--test-cmd", default=None)
    ex.add_argument("--test-cwd", default="")
    ex.add_argument("--red-pattern", default=None)
    ex.add_argument("--grep", default=bc.DEFAULT_GREP)
    ex.add_argument("--reviewed-pattern", default=None)
    ex.add_argument("--start", type=int, default=0)
    ex.add_argument("--limit", type=int, default=None)
    ex.add_argument("--max-files", type=int, default=6)
    ex.add_argument("--owner-share", type=float, default=0.8)
    ex.add_argument("--max-diff-lines", type=int, default=800)
    ex.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    test_cmd = shlex.split(args.test_cmd) if args.test_cmd else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    log = args.out.with_name(args.out.name + ".outcomes.jsonl")
    done = [r["fix"] for r in _rows(log) if "fix" in r]
    kept = {r["id"]: r.get("head") for r in _rows(args.out) if "id" in r}

    def keep(fix: str, outcome: str, case: dict | None, detail: str) -> None:
        if case is not None and case["id"] not in kept:   # a run stopped before its outcome line
            kept[case["id"]] = case["head"]
            with args.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fix": fix, "outcome": outcome, **({"detail": detail} if detail else {})})
                    + "\n")

    _cases, counts = bc.extract(
        args.repo, test_cmd=test_cmd, grep=args.grep, test_cwd=args.test_cwd,
        max_files=args.max_files, owner_share=args.owner_share,
        max_diff_lines=args.max_diff_lines, reviewed_pattern=args.reviewed_pattern,
        start=args.start, limit=args.limit, timeout=args.timeout, red_pattern=args.red_pattern,
        skip=done, kept_b=[h for h in kept.values() if h], on_outcome=keep)
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
