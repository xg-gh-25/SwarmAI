"""Judge a post-deploy canary record: exit 0 when it passed, 1 (with the reason) when not.

    curl -sf -X POST http://127.0.0.1:<port>/api/eval/canary | python3 backend/scripts/canary_check.py

Reads the run record ``/api/eval/canary`` returns (``eval_runner.run_eval``). It passes
only when the record says so in the fields that can say it failed: the BVT is green
(no failed, errored or timed-out case, and at least one passed) and no red-line case
was violated. A missing field, a body that is not a record (an HTTP error page) or an
empty body is a failure, never a pass. Standard library only: prod.sh runs it with
the system Python.
"""
from __future__ import annotations

import json
import sys


def canary_problem(record: object) -> str | None:
    """None when the canary passed, else one line saying why it did not."""
    if not isinstance(record, dict):
        return "the canary did not return a run record"
    bvt = record.get("bvt")
    if not isinstance(bvt, dict) or "green" not in bvt:
        return "the run record has no BVT result"
    redline = record.get("redline")
    if not isinstance(redline, dict) or "violated" not in redline:
        return "the run record has no red-line result"
    if redline.get("violated"):
        ids = [v.get("id") for v in redline.get("violations") or [] if isinstance(v, dict)]
        return f"red-line case(s) violated: {', '.join(map(str, ids)) or '?'}"
    if bvt.get("green") is not True:
        return (f"BVT not green: {bvt.get('passed', 0)}/{bvt.get('total', 0)} passed, "
                f"{bvt.get('failed', 0)} failed, {bvt.get('error', 0)} error, "
                f"{bvt.get('timeout', 0)} timed out")
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        record = json.loads(raw)
    except ValueError:
        print("the canary response is not JSON")
        return 1
    problem = canary_problem(record)
    if problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
