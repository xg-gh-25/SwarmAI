"""Per-run speed profile of an autonomous-pipeline run, from CLI transcripts.

Read-only, zero model cost. Answers "where did this run's wall time go?" with
numbers instead of impressions (design: docs/design/pipeline-speed-systematic-fix.md
§4-D). For one run it reports:

- wall / model / tool minutes (model time = gap between the last tool_result
  and the next response's first tool_use; tool time = tool_use → tool_result)
- API responses, tool calls, share of responses carrying exactly one call
- per-tool counts and p50/p90 exec seconds; per-category model+tool minutes
- mechanical jobs the model did by hand (pytest, stage-JSON assembly,
  publish/run-update/advance, git diff scoping, ...) — the F1 inventory
- reads of artifact_cli.py / pipeline_validator.py source
- test invocations and the most-repeated test target
- context tokens (peak, p50), output tokens, gate denials, exact-duplicate calls

Transcript selection: the top-level (non-subagent) ``*.jsonl`` files under
``$CLAUDE_CONFIG_DIR/projects`` (default ``~/.claude/projects``) that issued at
least one pipeline WRITE command (``publish``/``run-update``/``advance``/
``run-commit``/``run-report``/``run-checkpoint``) whose own ``artifact_cli.py``
invocation carries ``--run-id <run>`` (quoted or not) inside the run's time
window. A session that only reads another run's files is not counted. A write
whose run id lives in a shell variable cannot be seen; then pass
``--transcript`` (it overrides the selection; a warning is printed when no
owner is found). Records are deduped by uuid across files.

Usage:
    python scripts/pipeline_run_profile.py --run-id run_48d377ec [--project SwarmAI]
        [--transcript PATH ...] [--json] [--write-metrics]

Public symbols:
- ``profile_run(run_state, transcripts)`` — metrics dict for one run
- ``find_run_transcripts(run_id, lo, hi, base)`` — owning transcript paths
- ``classify_command(name, tool_input)`` — category of one tool call
- ``mechanical_jobs(command)`` — F1 job labels a Bash command matches
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/

# One locator for transcripts and their sub-agent files, shared with artifact_cli's
# token measurement (core/transcript_usage); find_subagent_transcripts is re-exported.
from core.transcript_usage import find_subagent_transcripts, resolve_transcripts  # noqa: E402

# ── command classification ──────────────────────────────────────────────────
_BOOK_RE = re.compile(
    r"artifact_cli|pipeline_validator|spawn_prompt|confidence_score|\.artifacts/runs"
    r"|s_autonomous-pipeline/(?:stages|INSTRUCTIONS|SKILL|REVIEW_PAT|OPERATIONAL)"
)
_TEST_RE = re.compile(
    r"\bpytest\b|vitest|\btsc\b|eslint|npm run build|vite build|ci_eval_gate"
    r"|python[\w.]*\s+(?:-\S+\s+)*\S*pipeline_test\.py\b"
)
# The pipeline's single test runner, as an INVOCATION (python … pipeline_test.py):
# a grep/sed/cat of the file is not a test run. Its executions are counted from
# the run's evidence (``runner_executions``), its wall time from the command's span.
_RUNNER_RE = re.compile(r"python[\w.]*\s+(?:-\S+\s+)*\S*pipeline_test\.py\b")
_EDIT_RE = re.compile(r"<<|\.write_text|open\([^)]*['\"]w|sed -i|cat >")
_GIT_RE = re.compile(r"(?:^|[\s;&|(])git\s")

# F1 inventory: mechanical jobs the model did by hand. One command may match
# several jobs (a pytest run that also writes /tmp JSON counts for both).
MECHANICAL_JOBS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(rx)) for name, rx in (
        ("pytest", r"\bpytest\b"),
        ("frontend_test_build", r"vitest|\btsc\b|eslint|vite build|npm run build"),
        ("stage_json_assembly", r"cat > /tmp/\S+\.(?:py|json)|json\.dump\(|stage_json|--stage-json"),
        ("publish_update_advance", r"artifact_cli\.py (?:publish|run-update|advance)"),
        ("run_state_queries", r"artifact_cli\.py (?:schema|run-get|discover|state|run-status|run-budget)"),
        ("read_cli_source", r"(?:sed|grep|awk|cat|head)[^|;]*(?:artifact_cli|pipeline_validator)\.py"),
        ("read_stage_docs_bash", r"(?:sed|grep|cat|head)[^|;]*(?:stages/|INSTRUCTIONS\.md|REVIEW_PATTERNS|SKILL\.md)"),
        ("change_scope", r"git (?:-C \S+ )?(?:diff|ls-files|status)\b(?!.*commit)"),
        ("git_staging", r"git (?:add|restore --staged|update-index)"),
        ("git_commit", r"git commit|run-commit"),
        ("report_cultivate_learn", r"run-report|run-cultivate|artifact_cli\.py learn"),
        ("baseline_copy", r"rsync|git archive|git worktree|cp -r|git show HEAD:"),
        ("hang_triage", r"\bps -|pkill|\bkill \d|faulthandler|\bsleep \d"),
        ("run_observe", r"run-observe"),
        ("spawn_prompt", r"spawn_prompt\.py"),
        ("wtf_gate", r"wtf_gate|confidence_score"),
    )
)

# Pipeline WRITE verbs: a session that runs one of these with --run-id <run>
# owns (part of) the run. Reads (run-get, schema, discover) do not count.
_WRITE_VERB_RE = re.compile(
    r"artifact_cli\.py\s+(?:publish|run-update|advance|run-commit|run-report|run-checkpoint"
    r"|stage-start|stage-complete)\b"
)
# ``R=run_x`` / ``RUN='run_x'`` shell assignments, so ``--run-id $R`` can be resolved.
_ASSIGN_RE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_]\w*)=(['"]?)([A-Za-z0-9_-]+)\2\s*$""")
_VAR_RUN_ID_RE = re.compile(r"""--run-id[ =]+(['"]?)\$\{?([A-Za-z_]\w*)\}?\1(?![\w-])""")
_TEST_TARGET_RE = re.compile(r"(?:tests/|src/)[\w/.-]+\.(?:py|tsx?)")
_CLI_SOURCE_RE = re.compile(r"(?:artifact_cli|pipeline_validator)\.py")
_PIPELINE_DOC_RE = re.compile(
    r"s_autonomous-pipeline/(?:stages/[\w.-]+\.md|INSTRUCTIONS\.md|REVIEW_PATTERNS\.md"
    r"|OPERATIONAL_PATTERNS\.md|SKILL\.md)"
)


def classify_command(name: str, tool_input: dict) -> str:
    """Category of one tool call: agent / edit / read / pipeline-bookkeeping /
    test-build / git / read-probe."""
    if name == "Agent" or name == "Task":
        return "agent"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return "edit"
    if name in ("Read", "Grep", "Glob"):
        return "read"
    if name != "Bash":
        return "other"
    cmd = str((tool_input or {}).get("command") or "")
    if _RUNNER_RE.search(cmd):
        return "test-build"  # before bookkeeping: a runner call often also reads run files
    if _BOOK_RE.search(cmd):
        return "pipeline-bookkeeping"
    if _TEST_RE.search(cmd):
        return "test-build"
    if _EDIT_RE.search(cmd):
        return "edit"
    if _GIT_RE.search(cmd):
        return "git"
    return "read-probe"


def mechanical_jobs(command: str) -> list[str]:
    """F1 job labels a Bash command matches (empty = not mechanical)."""
    return [name for name, rx in MECHANICAL_JOBS if rx.search(command or "")]


# ── transcript loading ──────────────────────────────────────────────────────
def _ts(value: str) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _load_records(path: str, seen: set | None = None) -> list[dict]:
    """Records of one transcript: deduped by uuid, timestamped, unparseable lines
    skipped. Pass a shared ``seen`` set to dedupe across files (a resumed session
    can repeat earlier records with the same uuids)."""
    out = []
    seen = set() if seen is None else seen
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or "timestamp" not in rec:
                continue
            uid = rec.get("uuid")
            if uid is not None:
                if uid in seen:
                    continue
                seen.add(uid)
            out.append(rec)
    return out


def _message(rec: dict) -> dict:
    msg = rec.get("message")
    return msg if isinstance(msg, dict) else {}


def _tool_uses(rec: dict):
    content = _message(rec).get("content")
    if rec.get("type") != "assistant" or not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
            yield block


def _transcript_base() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")


def find_run_transcripts(run_id: str, lo: float, hi: float, base: str | None = None,
                         recorded_ids=()) -> list[str]:
    """Top-level transcripts that issued a pipeline WRITE command for ``run_id``
    inside ``[lo, hi]``, plus the transcripts of ``recorded_ids`` (the CLI session
    ids artifact_cli records in run.json ``transcripts[]``; anything that is not an
    SDK session id is ignored). Sub-agent files (``agent-*.jsonl`` / ``subagents/``)
    are excluded: their work is already counted as the parent's Agent call time."""
    base = base or _transcript_base()
    owners = list(resolve_transcripts(recorded_ids or []))
    for path in glob.glob(os.path.join(base, "projects", "**", "*.jsonl"), recursive=True):
        if os.path.basename(path).startswith("agent-") or f"{os.sep}subagents{os.sep}" in path:
            continue
        try:
            if os.path.getmtime(path) < lo:
                continue  # last write before the run started — cannot hold it
            with open(path, encoding="utf-8", errors="replace") as fh:
                # Stream: parse only lines that mention the run, stop at the first owning write.
                for line in fh:
                    if run_id in line and _line_owns_run(line, run_id, lo, hi):
                        owners.append(path)
                        break
        except OSError:
            continue
    return sorted(dict.fromkeys(owners))


def _line_owns_run(line: str, run_id: str, lo: float, hi: float) -> bool:
    try:
        rec = json.loads(line)
    except ValueError:
        return False
    if not isinstance(rec, dict) or rec.get("isSidechain"):
        return False
    t = _safe_ts(rec)
    if t is None or not (lo <= t <= hi):
        return False
    return any(
        command_writes_run(str(_input(b).get("command") or ""), run_id) for b in _tool_uses(rec)
    )


def command_writes_run(command: str, run_id: str) -> bool:
    """True iff ONE ``artifact_cli.py`` invocation in ``command`` is a write verb
    carrying ``--run-id <run_id>`` (quoted or not), or ``--run-id $VAR`` /
    ``${VAR}`` where an EARLIER segment of the same command assigned
    ``VAR=<run_id>`` (the latest assignment wins). The verb and the id must sit
    in the same pipeline segment, so ``run-get --run-id A && publish --run-id B``
    does not credit A."""
    needle = re.compile(r"--run-id[ =]+['\"]?" + re.escape(run_id) + r"(?![\w-])")
    shell_vars: dict[str, str] = {}
    for seg in re.split(r"&&|\|\||;|\||\n", command):
        assign = _ASSIGN_RE.match(seg)
        if assign:
            shell_vars[assign.group(1)] = assign.group(3)
            continue
        if not _WRITE_VERB_RE.search(seg):
            continue
        if needle.search(seg):
            return True
        if any(shell_vars.get(v.group(2)) == run_id for v in _VAR_RUN_ID_RE.finditer(seg)):
            return True
    return False


def _input(block: dict) -> dict:
    inp = block.get("input")
    return inp if isinstance(inp, dict) else {}


def _safe_ts(rec: dict) -> float | None:
    try:
        return _ts(rec["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def _collect(records: list[dict], lo: float, hi: float):
    """Index one run's main-thread activity: tool uses, results, API responses."""
    uses: dict[str, tuple[str, dict, float, str]] = {}
    results: dict[str, tuple[float, bool, int, object]] = {}
    responses: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
    for rec in sorted(records, key=lambda r: _safe_ts(r) or 0.0):
        t = _safe_ts(rec)
        if t is None or rec.get("isSidechain") or not (lo <= t <= hi):
            continue
        msg = _message(rec)
        if rec.get("type") == "assistant":
            mid = msg.get("id") or rec.get("uuid")
            resp = responses.setdefault(mid, {"uses": [], "usage": None})
            if isinstance(msg.get("usage"), dict):
                resp["usage"] = msg["usage"]
            for block in _tool_uses(rec):
                if block["id"] in uses:
                    continue  # the same call logged twice (re-logged record)
                uses[block["id"]] = (str(block.get("name") or "?"), _input(block), t, mid)
                resp["uses"].append(block["id"])
        elif rec.get("type") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block.get("content")
                    size = len(json.dumps(body, ensure_ascii=False)) if body is not None else 0
                    results[block.get("tool_use_id")] = (t, bool(block.get("is_error")), size, body)
    return uses, results, responses


def _result_text(body: object) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return " ".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
    return ""


def _context_tokens(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def _time_split(uses, results, responses, lo: float):
    """Attribute wall time to model steps vs tool execution, and to categories.

    A response's model time runs from the previous response's end (its last
    tool_result, or its last tool_use if no result came back) to the response's
    LAST tool_use — so the streaming of later blocks in a multi-call response is
    model time. Its tool time runs from that last tool_use to its last
    tool_result. Both are split evenly across the response's calls when
    attributing to categories. ``gaps`` (the per-step distribution) keeps the
    pre-first-call gap only, which is the "thinking before acting" latency."""
    model_s = tool_s = 0.0
    gaps: list[float] = []
    by_cat: dict[str, list[float]] = collections.defaultdict(lambda: [0, 0.0, 0.0])
    last = lo
    for resp in responses.values():
        ids = resp["uses"]
        if not ids:
            continue
        first_use = min(uses[i][2] for i in ids)
        last_use = max(uses[i][2] for i in ids)
        gap = max(0.0, first_use - last)
        gaps.append(gap)
        model = gap + (last_use - first_use)
        model_s += model
        done = [results[i][0] for i in ids if i in results]
        span = max(0.0, max(done) - last_use) if done else 0.0
        tool_s += span
        for i in ids:
            cat = by_cat[classify_command(uses[i][0], uses[i][1])]
            cat[0] += 1
            cat[1] += model / len(ids)
            cat[2] += span / len(ids)
        # Never measure the next gap from an older point: a response whose
        # results never arrived ends at its last tool_use.
        last = max(last, max(done) if done else last_use)
    categories = {
        k: {"calls": v[0], "model_min": round(v[1] / 60, 1), "tool_min": round(v[2] / 60, 1)}
        for k, v in sorted(by_cat.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))
    }
    return model_s, tool_s, gaps, categories


def _gate_denial_label(text: str) -> str:
    """Short, stable label for a PreToolUse denial (first words of the reason)."""
    reason = text.split("hook error:", 1)[-1].strip()
    return re.sub(r"\s+", " ", reason)[:60]


# ── sub-agents: one row per spawn, against its stated cap and the dispatch plan ──
# A spawn prompt's own tool-call cap ("At most 14 tool calls", may wrap a line).
_TOOL_CAP_RE = re.compile(
    r"(?:at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?(?:\s+of)?|limit(?:ed)?\s+to|≤|<=)"
    r"\s*([1-9]\d*)\s+(?:more\s+)?tool\s+calls?", re.I)
# Same contract as runtime_hooks._extract_expert_id: the FIRST line starting EXPERT-ID.
_EXPERT_ID_LINE_RE = re.compile(r"^EXPERT-ID:[ \t]*([a-z][a-z0-9-]{0,40})[ \t]*$", re.M)
_EXPERT_ID_PREFIX_RE = re.compile(r"^EXPERT-ID:.*$", re.M)
_PLANNED_STAGES = ("review", "deliver")  # stages stage-start writes a dispatch_plan for


def _prompt_text(rec: dict) -> str:
    content = _message(rec).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _subagent_row(path: str, lo: float, hi: float) -> dict | None:
    """One spawn: description, expert_id, seconds, tool calls, stated cap. None when
    the spawn did not start inside the run window (another run's sub-agent)."""
    try:
        records = _load_records(path)
    except OSError:
        return None
    stamps = [t for t in (_safe_ts(r) for r in records) if t is not None]
    if not stamps or not (lo <= min(stamps) <= hi):
        return None
    prompt = next((_prompt_text(r) for r in records if r.get("type") == "user" and _prompt_text(r)), "")
    first_anchor = _EXPERT_ID_PREFIX_RE.search(prompt)
    m = _EXPERT_ID_LINE_RE.match(first_anchor.group(0)) if first_anchor else None
    cap = _TOOL_CAP_RE.search(prompt)
    calls = sum(1 for r in records for _b in _tool_uses(r))
    description, shape = "", None
    try:
        with open(path[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as fh:
            meta = json.load(fh) or {}
        description = str(meta.get("description") or "")
        shape = meta.get("requestShape")
    except (OSError, ValueError, AttributeError):
        pass
    tool_cap = int(cap.group(1)) if cap else None
    return {"start": min(stamps), "description": description or os.path.basename(path),
            "expert_id": m.group(1) if m else None,
            "seconds": round(max(stamps) - min(stamps), 1), "tool_calls": calls,
            "tool_cap": tool_cap, "over_cap": bool(tool_cap is not None and calls > tool_cap),
            # a background spawn ends the parent's visible stream — every pipeline spawn
            # is meant to be foreground (meta.json requestShape, absent on old CLIs)
            "background": shape == "background"}


def _stage_of(t: float, stage_timing: dict, hi: float) -> str:
    """The stage whose stage_timing window holds ``t`` (latest start wins), else
    'unattributed' — runs without stage_timing, restarted stages, between stages."""
    best, best_start = "unattributed", None
    for stage, w in (stage_timing or {}).items():
        if not isinstance(w, dict) or not w.get("started_at"):
            continue
        try:
            s = _ts(w["started_at"])
            e = _ts(w["completed_at"]) if w.get("completed_at") else hi
        except (ValueError, TypeError):
            continue
        if s <= t <= e and (best_start is None or s > best_start):
            best, best_start = stage, s
    return best


def subagent_report(run_state: dict, transcripts: list[str]) -> dict:
    """Report-only view of the run's sub-agents: per spawn stage / expert / seconds /
    tool calls vs its stated cap; expert ids spawned more than once; review/deliver
    spawns absent from ``dispatch_plan``. Never blocks anything. Sub-agent time is
    kept here, apart from the parent totals (it is already inside the Agent call)."""
    lo, hi = run_window(run_state)
    timing = run_state.get("stage_timing") if isinstance(run_state.get("stage_timing"), dict) else {}
    plan = run_state.get("dispatch_plan") if isinstance(run_state.get("dispatch_plan"), dict) else {}
    rows = [r for r in (_subagent_row(p, lo, hi) for p in find_subagent_transcripts(transcripts)) if r]
    rows.sort(key=lambda r: r["start"])
    by_expert: dict[str, list[str]] = collections.defaultdict(list)
    unplanned = []
    for r in rows:
        r["stage"] = _stage_of(r.pop("start"), timing, hi)
        if r["expert_id"]:
            by_expert[r["expert_id"]].append(r["stage"])
        planned = plan.get(r["stage"]) if r["stage"] in _PLANNED_STAGES else None
        if isinstance(planned, list) and r["expert_id"] not in planned:
            unplanned.append({"stage": r["stage"], "expert_id": r["expert_id"],
                              "description": r["description"]})
    return {
        "count": len(rows),
        "seconds_total": round(sum(r["seconds"] for r in rows), 1),
        "over_cap": sum(1 for r in rows if r["over_cap"]),
        "background": sum(1 for r in rows if r["background"]),
        "repeated_experts": {e: st for e, st in by_expert.items() if len(st) > 1},
        "unplanned": unplanned,
        "spawns": rows,
    }


def run_window(run_state: dict) -> tuple[float, float]:
    """(start, end) epoch seconds of a run: created_at → completed_at, else updated_at."""
    lo = _ts(run_state["created_at"])
    hi = _ts(run_state.get("completed_at") or run_state.get("updated_at") or run_state["created_at"])
    return lo, max(lo, hi)


_EVIDENCE_FILE_RE = re.compile(r"^([a-z]+)-\d{8}T\d{6}\.json$")


def _is_overlay(ev: dict) -> bool:
    """A recheck laid over verify/regression evidence: a rewrite, not another run.
    On disk only ``pipeline_test._overlay_evidence`` writes ``rechecked_from`` into
    an evidence file, and it keeps the BASE mode, so a non-recheck file carrying it
    is an overlay. (``overlaid_onto`` is NOT a marker: it is set on the recheck
    RUN's output after its file is written, and never reaches an evidence file.)"""
    return bool(ev.get("rechecked_from")) and ev.get("mode") != "recheck"


def runner_executions(run_dir: "Path | None") -> dict:
    """Test-runner executions of a run, from ``evidence/tests``.

    ``run_index.json`` is the authority (append-only, never pruned; ``kind=run``
    entries, cache hits included and also counted under ``cached``; ``overlay``
    entries are rewrites). Timestamped ``<mode>-<stamp>.json`` files no index entry
    names are added — runs from before the index existed — minus overlays.
    Retention keeps only the newest few such files per mode, so for a run with no
    index the count is a lower bound; ``sources`` says where each count came from."""
    out = {"executions": 0, "cached": 0, "by_mode": {},
           "sources": {"run_index": 0, "evidence_files": 0}}
    tdir = Path(run_dir) / "evidence" / "tests" if run_dir else None
    if tdir is None or not tdir.is_dir():
        return out
    by_mode: collections.Counter = collections.Counter()
    named: set = set()
    try:
        index = json.loads((tdir / "run_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = []
    for e in index if isinstance(index, list) else []:
        if not isinstance(e, dict):
            continue
        named.add(str(e.get("evidence") or ""))
        if e.get("kind") != "run":
            continue
        out["sources"]["run_index"] += 1
        out["cached"] += 1 if e.get("cached") else 0
        by_mode[str(e.get("mode") or "?")] += 1
    for f in sorted(tdir.iterdir()):
        m = _EVIDENCE_FILE_RE.match(f.name)
        if not m or f.name in named or not f.is_file():
            continue
        try:
            ev = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(ev, dict) or _is_overlay(ev):
            continue
        out["sources"]["evidence_files"] += 1
        out["cached"] += 1 if ev.get("cached") else 0
        by_mode[str(ev.get("mode") or m.group(1))] += 1
    out["by_mode"] = dict(by_mode)
    out["executions"] = sum(by_mode.values())
    return out


def profile_run(run_state: dict, transcripts: list[str], run_dir: "Path | None" = None) -> dict:
    """Speed profile of one run from its owning transcripts (see module docstring).
    ``run_dir`` adds the test-runner executions recorded in the run's evidence."""
    lo, hi = run_window(run_state)
    records: list[dict] = []
    seen: set = set()
    for path in dict.fromkeys(transcripts):  # same path given twice counts once
        records.extend(_load_records(path, seen))
    uses, results, responses = _collect(records, lo, hi)
    model_s, tool_s, gaps, categories = _time_split(uses, results, responses, lo)

    with_calls = [r for r in responses.values() if r["uses"]]
    per_tool: dict[str, list[float]] = collections.defaultdict(list)
    jobs = collections.Counter()
    job_secs = collections.Counter()
    test_kinds = collections.Counter()
    test_targets = collections.Counter()
    cli_source_reads = 0
    doc_chars = 0
    denials = collections.Counter()
    keys = collections.Counter()
    test_secs = 0.0
    for uid, (name, tool_input, t, _mid) in uses.items():
        res = results.get(uid)
        secs = (res[0] - t) if res else 0.0
        if res:
            per_tool[name].append(secs)
        cmd = str(tool_input.get("command") or "")
        path = str(tool_input.get("file_path") or "")
        keys[name + ":" + (cmd or json.dumps(tool_input, sort_keys=True))] += 1
        if name == "Bash":
            for job in mechanical_jobs(cmd):
                jobs[job] += 1
                job_secs[job] += secs
            if _TEST_RE.search(cmd) and "ci_eval_gate" not in cmd:
                kind = ("pipeline_test" if _RUNNER_RE.search(cmd) else
                        "pytest" if "pytest" in cmd else
                        "vitest" if "vitest" in cmd else "frontend-build")
                test_kinds[kind] += 1
                test_secs += secs
                for target in set(_TEST_TARGET_RE.findall(cmd)):
                    test_targets[target] += 1
            if mechanical_jobs(cmd).count("read_cli_source"):
                cli_source_reads += 1
        elif name == "Read":
            if _CLI_SOURCE_RE.search(path):
                cli_source_reads += 1
            if _PIPELINE_DOC_RE.search(path) and res:
                doc_chars += res[2]
        if res and res[1]:
            text = _result_text(res[3])
            if "PreToolUse" in text and "hook error" in text:
                denials[_gate_denial_label(text)] += 1

    contexts = [_context_tokens(r["usage"]) for r in responses.values() if r["usage"]]
    out_tokens = sum(int((r["usage"] or {}).get("output_tokens") or 0) for r in responses.values())
    top_target = test_targets.most_common(1)
    runner = runner_executions(run_dir)
    runner_cmds = test_kinds.get("pipeline_test", 0)
    # One runner command can write several evidence entries (an automatic recheck),
    # and a command can die before writing any: count what the runner recorded.
    test_calls = sum(test_kinds.values()) - runner_cmds + (runner["executions"] or runner_cmds)
    return {
        "run_id": run_state.get("id"),
        "profile": run_state.get("profile"),
        "status": run_state.get("status"),
        "transcripts": [os.path.basename(p) for p in transcripts],
        "wall_minutes": round((hi - lo) / 60, 1),
        "model_minutes": round(model_s / 60, 1),
        "tool_minutes": round(tool_s / 60, 1),
        "api_responses": len(responses),
        "tool_calls": len(uses),
        "tool_errors": sum(1 for u in uses if u in results and results[u][1]),
        "single_call_response_ratio": (
            round(sum(1 for r in with_calls if len(r["uses"]) == 1) / len(with_calls), 3)
            if with_calls else None
        ),
        "model_step_seconds": {"p50": round(_pct(gaps, .5), 1), "p90": round(_pct(gaps, .9), 1)},
        "per_tool": {
            name: {"calls": len(xs), "exec_min": round(sum(xs) / 60, 1),
                   "p50_s": round(_pct(xs, .5), 1), "p90_s": round(_pct(xs, .9), 1)}
            for name, xs in sorted(per_tool.items(), key=lambda kv: -sum(kv[1]))
        },
        "categories": categories,
        "mechanical": {
            "total_calls": sum(
                1 for (n, i, _t, _m) in uses.values()
                if n == "Bash" and mechanical_jobs(str(i.get("command") or ""))
            ),
            "bash_calls": sum(1 for (n, *_rest) in uses.values() if n == "Bash"),
            "jobs": {j: {"calls": c, "exec_min": round(job_secs[j] / 60, 1)}
                     for j, c in jobs.most_common()},
        },
        "cli_source_reads": cli_source_reads,
        "pipeline_doc_chars_read": doc_chars,
        "tests": {
            "calls": test_calls,
            "by_kind": dict(test_kinds),
            "exec_min": round(test_secs / 60, 1),
            "max_repeat": {"target": top_target[0][0], "runs": top_target[0][1]} if top_target else None,
            "runner": runner,
            # where each number came from: runner executions are the evidence's
            # count when it has one, else the runner commands seen in the transcript
            "sources": {
                "calls": ("direct test commands + runner evidence" if runner["executions"]
                          else "direct test commands + runner commands in the transcript"),
                "exec_min": "transcript tool time of test commands",
            },
        },
        "context_tokens": {"peak": max(contexts) if contexts else 0,
                           "p50": int(_pct(contexts, .5)) if contexts else 0},
        "output_tokens": out_tokens,
        "gate_denials": {"total": sum(denials.values()), "by_reason": dict(denials.most_common(8))},
        "exact_duplicate_calls": sum(c - 1 for c in keys.values() if c > 1),
        "subagents": subagent_report(run_state, transcripts),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
_RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _workspace() -> Path:
    ws = os.environ.get("SWARM_WORKSPACE")
    if not ws:
        try:
            from config import get_app_data_dir
            ws = str(get_app_data_dir() / "SwarmWS")
        except Exception:  # noqa: BLE001 — standalone use without the backend config
            ws = str(Path.home() / ".swarm-ai" / "SwarmWS")
    return Path(ws).expanduser().resolve()


def _run_dir(project: str, run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id) or not _RUN_ID_RE.fullmatch(project):
        raise ValueError(f"unsafe project/run id: {project!r} {run_id!r}")
    return _workspace() / "Projects" / project / ".artifacts" / "runs" / run_id


def write_metrics(run_dir: Path, profile: dict) -> None:
    """Merge ``profile`` into METRICS.json under ``speed_profile``.

    Never clobbers: an existing file that does not parse as a JSON object raises
    ValueError and is left untouched (it may be mid-write by another command).
    The write goes through a temp file + ``os.replace`` so a reader never sees a
    half-written file."""
    path = run_dir / "METRICS.json"
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"METRICS.json unreadable, not overwriting: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("METRICS.json is not a JSON object, not overwriting")
        data = loaded
    data["speed_profile"] = profile
    tmp = path.with_name(f".METRICS.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _render(p: dict) -> str:
    lines = [
        f"{p['run_id']} ({p['profile']}, {p['status']}) — wall {p['wall_minutes']} min = "
        f"model {p['model_minutes']} + tools {p['tool_minutes']}",
        f"transcripts: {', '.join(p['transcripts']) or '(none found)'}",
        f"responses {p['api_responses']} · tool calls {p['tool_calls']} · errors {p['tool_errors']}"
        f" · single-call responses {p['single_call_response_ratio']}"
        f" · model step p50 {p['model_step_seconds']['p50']}s p90 {p['model_step_seconds']['p90']}s",
        f"context peak {p['context_tokens']['peak'] // 1000}K p50 {p['context_tokens']['p50'] // 1000}K"
        f" · output {p['output_tokens'] // 1000}K tokens · pipeline docs read {p['pipeline_doc_chars_read'] // 1000}K chars",
        f"mechanical {p['mechanical']['total_calls']}/{p['mechanical']['bash_calls']} Bash calls"
        f" · artifact_cli/validator source reads {p['cli_source_reads']}"
        f" · gate denials {p['gate_denials']['total']} · exact duplicates {p['exact_duplicate_calls']}",
        f"tests {p['tests']['calls']} calls {p['tests']['by_kind']} {p['tests']['exec_min']} min"
        f" · most repeated {p['tests']['max_repeat']}"
        f" · runner executions {(p['tests'].get('runner') or {}).get('executions', 0)}"
        f" (cached {(p['tests'].get('runner') or {}).get('cached', 0)},"
        f" {(p['tests'].get('runner') or {}).get('by_mode', {})},"
        f" from {(p['tests'].get('runner') or {}).get('sources', {})})",
        "categories:",
    ]
    for name, c in p["categories"].items():
        lines.append(f"  {name:22} {c['calls']:5} calls  model {c['model_min']:6} min  tools {c['tool_min']:6} min")
    lines.append("mechanical jobs:")
    for name, j in p["mechanical"]["jobs"].items():
        lines.append(f"  {name:24} {j['calls']:5} calls  {j['exec_min']:6} min")
    s = p.get("subagents") or {}
    lines.append(f"sub-agents {s.get('count', 0)} · {s.get('seconds_total', 0)} s total"
                 f" · over cap {s.get('over_cap', 0)} · background {s.get('background', 0)} · repeated {s.get('repeated_experts') or {}}"
                 f" · not in dispatch plan {len(s.get('unplanned') or [])}")
    for r in s.get("spawns") or []:
        cap = f"/{r['tool_cap']}" if r["tool_cap"] is not None else ""
        flag = ("  OVER CAP" if r["over_cap"] else "") + ("  BACKGROUND" if r.get("background") else "")
        lines.append(f"  {r['stage']:12} {r['expert_id'] or '-':16} {r['seconds']:6.0f}s"
                     f" tools {r['tool_calls']}{cap}{flag}  {r['description'][:60]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Speed profile of one pipeline run (read-only).")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--project", default="SwarmAI")
    ap.add_argument("--transcript", action="append", default=None,
                    help="transcript path(s) to use instead of auto-detection")
    ap.add_argument("--json", action="store_true", help="print the metrics as JSON")
    ap.add_argument("--write-metrics", action="store_true",
                    help="also merge the profile into the run's METRICS.json (speed_profile)")
    args = ap.parse_args(argv)
    try:
        run_dir = _run_dir(args.project, args.run_id)
        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        lo, hi = run_window(run_state)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": f"cannot load run {args.run_id}: {exc}"}), file=sys.stderr)
        return 1
    recorded = run_state.get("transcripts") if isinstance(run_state.get("transcripts"), list) else []
    transcripts = args.transcript or find_run_transcripts(args.run_id, lo, hi, recorded_ids=recorded)
    if not transcripts:
        print(json.dumps({"warning": f"no transcript issued a pipeline write for {args.run_id} "
                          "in its window; pass --transcript PATH to choose one"}), file=sys.stderr)
    profile = profile_run(run_state, transcripts, run_dir=run_dir)
    if args.write_metrics:
        try:
            write_metrics(run_dir, profile)
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
    print(json.dumps(profile, indent=2, ensure_ascii=False) if args.json else _render(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
