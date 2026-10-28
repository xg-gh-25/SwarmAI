"""Step 0 of the session review (docs/design/2026-10-29-cdf-session-review.md §13).

Run by a person with Bedrock credentials, against one existing CLI conversation:

    python scripts/cdf_review_probe.py --sdk-session-id <uuid>
    python scripts/cdf_review_probe.py --latest      # the newest CLI transcript

It checks the three assumptions the design rests on. It writes nothing to SwarmAI's
data (token usage goes to a temp dir); the fork's own CLI transcript is removed after.
1. the conversation resumes as a fork (``resume`` + ``fork_session``) from its own cwd;
2. ``output_format`` returns a structured answer matching the review schema;
3. the original conversation file is byte-for-byte unchanged afterwards.
It prints the answer, the time taken and the token usage (from the SDK result).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _latest() -> str:
    from core.leak_transcript import _projects_dir
    files = sorted(_projects_dir().glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit("no CLI transcript found")
    return files[-1].stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sdk-session-id")
    group.add_argument("--latest", action="store_true")
    parser.add_argument("--data-dir", type=Path,
                        help="where the probe records token usage (default: a new temp dir; "
                             "never the live ~/.swarm-ai)")
    args = parser.parse_args(argv)
    import os
    import tempfile
    os.environ["SWARM_DATA_DIR"] = str(args.data_dir or tempfile.mkdtemp(prefix="cdf-probe-"))

    from core.ingestion.sources import extract_llm, s7_review
    from core.leak_transcript import find_transcript
    sdk = _latest() if args.latest else args.sdk_session_id
    path = find_transcript(sdk)
    if path is None:
        raise SystemExit(f"no CLI transcript for {sdk}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cwd = s7_review._cwd_of(sdk)
    sig = {"rule_messages": [], "user_messages": 0, "tool_calls": 0, "files_written": []}
    prompt = s7_review.build_prompt(s7_review.ALLOWED_KINDS, sig, "")
    prompt = prompt.replace("(no citable evidence: record nothing)",
                            "(probe run: there is no index; record nothing, or at most one "
                            "unit with evidence [\"event:probe\"])")
    schema = extract_llm.flat_unit_schema(s7_review.ALLOWED_KINDS)
    started = time.monotonic()
    answer = s7_review.sdk_runner(sdk, cwd, prompt, schema)
    seconds = round(time.monotonic() - started, 1)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    from core.ingestion import llm_usage
    report = {
        "sdk_session_id": sdk, "cwd": cwd, "seconds": seconds,
        "structured_answer_is_object": isinstance(answer, dict) and "units" in answer,
        "original_unchanged": before == after,
        "answer": answer,
        "usage_recorded_total": llm_usage.totals(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["structured_answer_is_object"] and report["original_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
