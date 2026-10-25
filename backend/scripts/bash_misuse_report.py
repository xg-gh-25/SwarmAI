"""bash_misuse_report — how agents' Bash calls go wrong, measured from SDK transcripts.

One command for the questions of the 2026-10-21 gate audit, so every later change
is judged with the same measure (run before and after, compare the JSON):

- ``bash.calls`` / ``bash.errors``: Bash tool calls and how many returned an error;
- ``gates``: denials per PreToolUse gate (recognised by the gate's own deny text),
  and ``repeat_sessions``: sessions where the same gate denied 3+ times, the sign
  that its message did not tell the agent what to do;
- ``auto_backgrounded``: foreground calls the CLI moved to the background (the call
  ran past its Bash timeout and returned no output);
- ``inline_state_reads``: ``python -c`` / stdin python that loads run.json or
  evidence/ instead of ``run-get --brief`` / ``--field``;
- ``run_json_writes``: python that loads a run's run.json and writes JSON back.

Usage::

    python backend/scripts/bash_misuse_report.py [--days 7 | --since D [--until D]]
        [--root ~/.claude/projects] [--samples N] [--save docs/metrics/<name>.json]

Output is one JSON object on stdout. ``--save`` writes the same object without
samples (counts only: no command text leaves the transcripts). Read-only.

Public: ``scan(root, since)``, ``classify_gate(text)``, ``main()``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A gate's deny text → its name. First match wins; order: specific before generic.
GATE_PATTERNS: list[tuple[str, re.Pattern]] = [(n, re.compile(p)) for n, p in [
    ("run_owner_gate", r"DENY \(run owner gate\)"),
    ("worktree_write_gate", r"DENY \(worktree gate\)"),
    ("audit_marker_write_gate", r"this writes, moves or deletes files in"),
    ("session_link_gate", r"this command removes or overrides"),
    ("run_amend_gate", r"DENY \(git commit --amend\)"),
    ("active_run_commit_gate", r"git commit → DENY: this commit touches file\(s\) owned"),
    ("bash_syntax_guard", r"has a SYNTAX ERROR"),
    ("background_command_guard", r"Background execution is disabled for this command"),
    ("dangerous_command_gate", r"Matches dangerous command pattern|Dangerous command blocked|\[rule: "
                               r"|Approval timed out after"),
    ("expert_budget_gate", r"Tool budget used \(\d+ calls\)"),
]]
_HOOK_ERROR = re.compile(r"^\s*PreToolUse:\w+ hook error:")
_AUTO_BG = re.compile(r"Command running in background with ID: (\w+)")
# Inline python: `python -c …`, or a program on stdin (`python - <<EOF`, `python <<EOF`).
_PY_CODE = re.compile(r"\bpython[\d.]*(?:\s+-[A-Za-z]+)*\s+(?:-c\b|-?\s*<<)")
_STATE_FILE = re.compile(r"run\.json|evidence/")
_READS = re.compile(r"json\.loads?\(|\.read_text\(|\bopen\(")
_WRITES = re.compile(r"json\.dump\(|\.write_text\(|open\([^)]*,\s*['\"][wa]")
_RUN_JSON = re.compile(r"run\.json")
_ASSIGN = re.compile(r"(?m)^\s*(\w+)\s*=\s*[^\n=][^\n]*run\.json")


def _writes_run_json(code: str) -> bool:
    """True when the write targets run.json itself: a write call whose line names
    run.json, or one on a variable assigned from a run.json path (reading run.json
    and writing another file does not count)."""
    names = set(_ASSIGN.findall(code))
    for line in code.splitlines():
        if not _WRITES.search(line):
            continue
        if _RUN_JSON.search(line):
            return True
        if any(re.search(rf"\b{re.escape(n)}\b", line) for n in names):
            return True
    return False


def classify_gate(text: str) -> "str | None":
    """The gate that wrote this deny *text*, ``"other_hook"`` for an unknown hook
    error, or None when it is not a denial."""
    for name, pat in GATE_PATTERNS:
        if pat.search(text[:600]):
            return name
    return "other_hook" if _HOOK_ERROR.search(text) else None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def _when(ts) -> "datetime | None":
    """A transcript timestamp as an aware UTC instant, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _blocks(line: str):
    """(instant, block) for each content block of one CANONICAL transcript line: a
    timestamped ``assistant`` (tool_use) or ``user`` (tool_result) record. Other
    records (e.g. ``api-request-blob``, which repeats earlier blocks without a
    timestamp) are skipped, so nothing is counted twice."""
    try:
        d = json.loads(line)
    except ValueError:
        return
    if d.get("type") not in ("assistant", "user"):
        return
    when = _when(d.get("timestamp"))
    content = (d.get("message") or {}).get("content")
    if when is None or not isinstance(content, list):
        return
    for b in content:
        if isinstance(b, dict):
            yield when, b


def scan(root: Path, since: datetime, samples: int = 0, until: "datetime | None" = None) -> dict:
    """Count the report's measures over transcript entries in [*since*, *until*)."""
    # Transcripts are append-only: a file last written before *since* holds no entry
    # in the window, so the mtime test only skips work (a file restored with an old
    # mtime would be missed; none are).
    cutoff = since.timestamp()
    files = [p for p in root.rglob("*.jsonl") if p.stat().st_mtime >= cutoff]
    used = 0
    bash = Counter()
    gates = Counter()
    per_session = defaultdict(Counter)
    auto_bg = 0
    examples = defaultdict(list)
    state_reads = writes = 0
    for f in files:
        session = f.stem
        calls: dict[str, dict] = {}   # this file's tool_use_id -> input, until its result
        seen_entry = False
        try:
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                for when, b in _blocks(line):
                    if when < since or (until is not None and when >= until):
                        continue
                    seen_entry = True
                    if b.get("type") == "tool_use" and b.get("name") == "Bash":
                        inp = b.get("input") or {}
                        calls[b.get("id", "")] = inp
                        bash["calls"] += 1
                        cmd = inp.get("command", "") or ""
                        if _PY_CODE.search(cmd) and _STATE_FILE.search(cmd) and _READS.search(cmd):
                            state_reads += 1
                            if _writes_run_json(cmd):
                                writes += 1
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                        inp = calls.pop(b["tool_use_id"])
                        text = _text(b.get("content"))
                        if _AUTO_BG.search(text[:300]) and not inp.get("run_in_background"):
                            auto_bg += 1
                        if not b.get("is_error"):
                            continue
                        bash["errors"] += 1
                        gate = classify_gate(text)
                        if gate:
                            gates[gate] += 1
                            per_session[session][gate] += 1
                            if len(examples[gate]) < samples:
                                examples[gate].append((inp.get("command") or "")[:200])
        used += seen_entry
    repeat = Counter(g for c in per_session.values() for g, n in c.items() if n >= 3)
    out = {
        "since": since.isoformat(timespec="seconds"),
        "until": until.isoformat(timespec="seconds") if until else None,
        "transcripts": used,
        "bash": {"calls": bash["calls"], "errors": bash["errors"],
                 "error_rate": round(bash["errors"] / bash["calls"], 4) if bash["calls"] else 0.0},
        "gates": {g: {"denials": n, "repeat_sessions": repeat.get(g, 0)}
                  for g, n in gates.most_common()},
        "auto_backgrounded": auto_bg,
        "inline_state_reads": state_reads,
        "run_json_writes": writes,
    }
    if samples:
        out["samples"] = {g: v for g, v in examples.items()}
    return out


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--days", type=float, default=7.0, help="window ending now (ignored with --since)")
    ap.add_argument("--since", help="window start, UTC ISO date/time (e.g. 2026-10-14)")
    ap.add_argument("--until", help="window end, UTC ISO date/time (exclusive)")
    ap.add_argument("--root", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--samples", type=int, default=0, help="N sample commands per gate (stdout only)")
    ap.add_argument("--save", help="also write the counts (no samples) to this JSON file")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(json.dumps({"error": f"no transcript directory: {root}"}), file=sys.stderr)
        return 1
    def _at(v):
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    since = _at(args.since) if args.since else datetime.now(timezone.utc) - timedelta(days=args.days)
    until = _at(args.until) if args.until else None
    t0 = time.monotonic()
    report = scan(root, since, args.samples, until)
    report["scan_seconds"] = round(time.monotonic() - t0, 1)
    if args.save:
        saved = {k: v for k, v in report.items() if k != "samples"}
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
