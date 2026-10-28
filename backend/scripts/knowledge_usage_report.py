"""knowledge_usage_report — is the knowledge an agent was given, or could find, used?

Knowledge addressing design §5 (docs/design/2026-10-28-knowledge-addressing.md). Two
sources, both read-only:

- Agent transcripts (JSONL, one tool call per ``tool_use`` item; Claude Code writes them
  under ``~/.claude/projects/<cwd>/``): per session, how many ``knowledge find|get|map``
  calls, how many Read / Grep / Bash calls touched a DDD file (``Projects/<P>/2-understanding/``
  or a ``*-archive*.md``), and how many ``[DDD:<project>]`` hint blocks were followed by a
  DDD read within the next 15 tool calls.
- Pipeline runs (``Projects/*/.artifacts/runs/run_*/artifacts/``): per run, the ids in
  ``knowledge_refs`` of REQUIREMENTS / DESIGN_SPEC and the searches in DESIGN_SPEC's
  ``knowledge_checked``.

    "$SWARM_BACKEND_BIN" py -m scripts.knowledge_usage_report [--days 14]
        [--transcripts '<glob of *.jsonl>'] [--workspace <SwarmWS>] [--json]

Prints one summary (or JSON). Large transcripts are streamed line by line.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

_DDD_PATH = re.compile(r"/Projects/[^/\s\"']+/(?:2-understanding/|[^/\s\"']*-archive[^/\s\"']*\.md)")
_KNOWLEDGE_CMD = re.compile(r"\bknowledge\s+(find|get|map)\b")
# the hint block's heading; matched without its dash, which a transcript may store
# escaped (\u2014)
_HINT_PARTS = ("## Project DDD", "[DDD:")
FOLLOW_WINDOW = 15


def _tool_uses(obj: dict):
    msg = obj.get("message") or {}
    for c in msg.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_use":
            yield c


def scan_transcript(path: Path) -> dict:
    """Counts for one transcript (see the module docstring). Never raises on a bad line."""
    out = {"knowledge_calls": 0, "ddd_reads": 0, "hints": 0, "hints_followed": 0}
    pending: list[int] = []                      # tool calls left in each open hint window
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            if all(p in line for p in _HINT_PARTS) and '"type":"user"' in line.replace(" ", ""):
                out["hints"] += 1
                pending.append(FOLLOW_WINDOW)
                continue
            if '"tool_use"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            for call in _tool_uses(obj):
                text = json.dumps(call.get("input") or {}, ensure_ascii=False)
                is_cmd = bool(_KNOWLEDGE_CMD.search(text))
                is_read = bool(_DDD_PATH.search(text))
                out["knowledge_calls"] += is_cmd
                out["ddd_reads"] += is_read
                still = []
                for left in pending:
                    if is_cmd or is_read:
                        out["hints_followed"] += 1
                    elif left > 1:
                        still.append(left - 1)
                pending = still
    return out


def scan_runs(workspace: Path, since: float) -> list[dict]:
    """Per pipeline run modified since *since*: cited ids and searches."""
    rows = []
    for run in sorted(workspace.glob("Projects/*/.artifacts/runs/run_*")):
        art = run / "artifacts"
        try:
            if run.stat().st_mtime < since or not art.is_dir():
                continue
        except OSError:
            continue
        refs, searches = set(), None
        for name in ("requirements.json", "design_spec.json"):
            try:
                value = json.loads((art / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                refs |= {str(r) for r in value.get("knowledge_refs") or () if isinstance(r, str)}
                if isinstance(value.get("knowledge_checked"), list):
                    searches = (searches or 0) + len(value["knowledge_checked"])
        rows.append({"run": run.name, "project": run.parents[2].name,
                     "has_knowledge_md": (art / "knowledge.md").is_file(),
                     "cited": len(refs), "searches": searches})
    return rows


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=float, default=14)
    p.add_argument("--transcripts", default=os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    p.add_argument("--workspace", default=os.environ.get("SWARM_WORKSPACE")
                   or os.path.expanduser("~/.swarm-ai/SwarmWS"))
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    since = time.time() - args.days * 86400
    sessions = []
    for f in glob.glob(args.transcripts):
        path = Path(f)
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        counts = scan_transcript(path)
        if any(counts.values()):
            sessions.append({"transcript": path.name, **counts})
    runs = scan_runs(Path(args.workspace), since)
    total = {k: sum(s[k] for s in sessions) for k in ("knowledge_calls", "ddd_reads", "hints", "hints_followed")}
    report = {"days": args.days, "sessions_with_activity": len(sessions), **total,
              "runs": len(runs), "runs_citing": sum(1 for r in runs if r["cited"]),
              "runs_searching": sum(1 for r in runs if r["searches"]), "run_rows": runs}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"Last {args.days:g} days")
    print(f"  sessions using the DDD: {len(sessions)} · knowledge calls {total['knowledge_calls']} · "
          f"DDD file reads {total['ddd_reads']}")
    print(f"  [DDD:] hints {total['hints']} · followed by a DDD read within {FOLLOW_WINDOW} tool calls "
          f"{total['hints_followed']}")
    print(f"  pipeline runs {len(runs)} · citing knowledge {report['runs_citing']} · "
          f"searching the DDD {report['runs_searching']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
