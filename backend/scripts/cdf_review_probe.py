"""Step 0 of the session review (docs/design/2026-10-29-cdf-session-review.md §13).

Run by a person with Bedrock credentials, against one existing CLI conversation:

    python scripts/cdf_review_probe.py --sdk-session-id <uuid>
    python scripts/cdf_review_probe.py --latest   # newest conversation idle >= 10 min

It checks the assumptions the design rests on. It writes nothing to SwarmAI's data
(token usage goes to a temp dir), and the fork's own CLI transcript is removed after:
1. the conversation resumes as a fork (``resume`` + ``fork_session``) from its own cwd,
   and the fork SEES the conversation: it must name the last user request, which the
   report prints next to the real one from the transcript, for a person to compare;
2. ``output_format`` returns a structured answer;
3. the fork writes nothing into the original file: the original's bytes before the
   probe are still its prefix, and no appended line carries the fork's session id
   (a live session may append its own turns meanwhile; those are reported, not failed).
It prints the time taken and the raw token usage from the SDK result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

IDLE_SECONDS = 600


def _latest_idle() -> str:
    from core.leak_transcript import _projects_dir
    now = time.time()
    files = [p for p in _projects_dir().glob("*/*.jsonl") if now - p.stat().st_mtime >= IDLE_SECONDS]
    if not files:
        raise SystemExit("no CLI transcript idle for 10 minutes")
    return max(files, key=lambda p: p.stat().st_mtime).stem


def _last_user_text(path: Path) -> str:
    last = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "user" and msg.get("role") == "user" and not rec.get("isMeta"):
            content = msg.get("content")
            text = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                last = text.strip()
    return last[:300]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sdk-session-id")
    group.add_argument("--latest", action="store_true")
    parser.add_argument("--data-dir", type=Path,
                        help="where token usage is recorded (default: a new temp dir)")
    args = parser.parse_args(argv)
    os.environ["SWARM_DATA_DIR"] = str(args.data_dir or tempfile.mkdtemp(prefix="cdf-probe-"))

    from claude_agent_sdk import ResultMessage, query
    from core.ingestion.sources import extract_llm, s7_review
    from core.leak_transcript import find_transcript
    sdk = _latest_idle() if args.latest else args.sdk_session_id
    path = find_transcript(sdk)
    if path is None:
        raise SystemExit(f"no CLI transcript for {sdk}")
    before = path.read_bytes()
    cwd = s7_review._cwd_of(sdk)
    schema = extract_llm.unit_schema(s7_review.ALLOWED_KINDS)
    schema = {**schema, "properties": {
        "last_user_request": {"type": "string",
                              "description": "the user's last request in this conversation, quoted"},
        **schema["properties"]}, "required": ["last_user_request", *schema["required"]]}
    prompt = ("Probe of the after-session review. In last_user_request quote the user's last "
              "request in this conversation (start of it is enough). Record no units.")
    started = time.monotonic()
    result: dict = {}

    async def run() -> None:
        async for message in query(prompt=prompt, options=s7_review.review_options(sdk, cwd, schema)):
            if isinstance(message, ResultMessage):
                result.update(fork=message.session_id, error=message.is_error,
                              errors=message.errors, usage=message.usage,
                              answer=message.structured_output)
                s7_review._drop_fork(message.session_id, sdk)

    asyncio.run(asyncio.wait_for(run(), s7_review.REVIEW_TIMEOUT_S))
    after = path.read_bytes()
    appended = after[len(before):].decode("utf-8", errors="replace").splitlines() \
        if after.startswith(before) else None
    fork = result.get("fork")
    fork_left = find_transcript(fork) if fork and fork != sdk else None
    report = {
        "sdk_session_id": sdk, "cwd": cwd, "seconds": round(time.monotonic() - started, 1),
        "fork_session_id": fork, "fork_transcript_removed": fork_left is None,
        "structured_answer_is_object": isinstance(result.get("answer"), dict),
        "fork_says_last_user_request": (result.get("answer") or {}).get("last_user_request"),
        "transcript_last_user_request": _last_user_text(path),
        "original_prefix_unchanged": appended is not None,
        "lines_the_live_session_appended": None if appended is None else len(appended),
        "fork_lines_in_original": None if appended is None else sum(
            1 for line in appended if fork and fork in line),
        "error": result.get("error"), "errors": result.get("errors"),
        "usage_raw": result.get("usage"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    ok = (report["structured_answer_is_object"] and report["original_prefix_unchanged"]
          and report["fork_lines_in_original"] == 0 and report["fork_transcript_removed"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
