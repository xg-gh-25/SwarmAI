"""What a dispatched Swarm Out run ended with, fetched from its Hive and kept here.

Design: docs/design/2026-10-28-swarm-out-run-results.md §4.2–§4.5. The Host reads the
run's session from the Hive's own messages API (``/api/chat/sessions/{id}/messages``,
the last ``FETCH_TURNS`` user turns, each whole, with the previous ETag), turns the
blocks the Hive persisted into one result (``summarize``) and keeps it in
``state/swarm-out/results/<dispatch_id>.json``. A Hive may be stopped or deleted
later; the kept file is then the only copy, so nothing here deletes one.

Everything read from a Hive is data: each field is checked for type and length and a
field that is not as expected is dropped and counted (``dropped``). ``public`` builds
the ``/runs`` copy, which the CLI prints and the local agent reads, from an allowlist
of enums, numbers, shas and accepted repository names: no text the Hive's agent wrote
gets in. The whole record is served to the panel (``/runs/{id}/result``).

- ``summarize(messages, has_more)`` — pure: the result of one run.
- ``load(dispatch_id)`` / ``save(record)`` — the kept record (atomic write).
- ``fetch(row)``   — check the Hive once (async; never raises).
- ``attach(rows, status_of)`` — add each row's kept result, start the checks due.
- ``spend_of(dispatch_ids)`` — each run's spend in the usage ledger (sync, read-only).
- ``public(record, declared, agent, spend)`` — the record as ``/runs`` carries it.

Tokens and cost come from the usage ledger (core/usage_accounting, the platform's one
definition of spend), whose rows for a dispatched session the host pulls from its Hive
(core/usage_hive_sync, tied to the dispatch). The record's own figures are the
fallback until those rows arrive: its cost is the CLI's process total (sub-agents
included) but its tokens are the CLI result's ``usage``, the main conversation only,
so a run that started sub-agents used more; such tokens are marked ``tokens_partial``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from core.turn_outcome import turn_costs

logger = logging.getLogger(__name__)

#: user turns read per check (the messages API's ``turns`` page, each turn whole)
FETCH_TURNS = 20
#: a run stopped this recently is checked on every poll (the panel's recent window)
RECENT_S = 24 * 3600
#: checks running at once, across all Hives
MAX_CONCURRENT_CHECKS = 2
MAX_DELIVERABLES = 50
MAX_TOOL_NAMES = 20
#: bumped whenever ``summarize`` changes what it derives: a kept result of another
#: version is fetched again in full (no ETag), so it is re-summarised instead of
#: staying as the older rules made it
SUMMARY_VERSION = 13

DISPATCH_ID_RE = re.compile(r"so_[0-9a-f]{12}")
_SHA_RE = re.compile(r"[0-9a-f]{7,64}")
#: a pipeline run id (core/run_protocol: ``run_`` and 8 hex): the one Hive-reported id
#: /runs carries, by its shape (the host agent names a Hive run by it)
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{8}")
_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

ENDED = ("completed", "turn_limit", "error", "crashed", "paused", "no_terminal", "stopped",
         "restarted", "superseded", "gave_up", "no_record")
_NOTICE_REASONS = ("restarted", "superseded", "gave_up")
_VERDICTS = ("PASS", "WARN", "BLOCK", "NA", "N/A", "PENDING")


# ── field checks ──────────────────────────────────────────────────────────────

class _Drops:
    def __init__(self) -> None:
        self.n = 0

    def s(self, v: Any, n: int) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            self.n += 1
            return None
        return v[:n]

    def i(self, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            self.n += 1
            return None
        return v


def _blocks(row: Any) -> list[dict]:
    content = row.get("content") if isinstance(row, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _repo_fact(f: Any, d: _Drops) -> Optional[dict]:
    if not isinstance(f, dict):
        d.n += 1
        return None
    files = []
    for x in (f.get("files") or [])[:MAX_DELIVERABLES] if isinstance(f.get("files"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("path"), str):
            files.append({"path": x["path"][:300], "added": d.i(x.get("added")),
                          "deleted": d.i(x.get("deleted"))})
    commits = [c for c in (f.get("commits") or []) if isinstance(c, str) and _SHA_RE.fullmatch(c)] \
        if isinstance(f.get("commits"), list) else []
    pushed = f.get("pushed") if f.get("pushed") in ("yes", "no", "nothing_to_push", "unknown") else "unknown"
    present = f.get("present") if isinstance(f.get("present"), bool) else None
    om = f.get("origin_matches") if isinstance(f.get("origin_matches"), bool) else None
    return {"url": d.s(f.get("url"), 300), "ref": d.s(f.get("ref"), 100), "dir": d.s(f.get("dir"), 300),
            "present": present, "origin_matches": om,
            "base": f["base"] if isinstance(f.get("base"), str) and _SHA_RE.fullmatch(f["base"]) else None,
            "head": f["head"] if isinstance(f.get("head"), str) and _SHA_RE.fullmatch(f["head"]) else None,
            "commits": commits[:20], "commits_total": d.i(f.get("commits_total")) or 0,
            "files": files, "files_total": d.i(f.get("files_total")) or 0,
            "added": d.i(f.get("added")) or 0, "deleted": d.i(f.get("deleted")) or 0,
            "untracked": d.i(f.get("untracked")) or 0, "pushed": pushed,
            "error": d.s(f.get("error"), 200), "bundle": _bundle_fact(f.get("bundle"))}


def _bundle_fact(b: Any) -> Optional[dict]:
    """A Hive bundle's key, sha256 and size (core/hive_bundle), or its error as one of
    the fixed texts; None when there is none."""
    from core import hive_bundle
    if not isinstance(b, dict) or not isinstance(b.get("key"), str) or not hive_bundle.KEY_RE.fullmatch(b["key"]):
        return None
    if b.get("error") is not None:
        err = b["error"] if b["error"] in (hive_bundle.ERR_TOO_LARGE, hive_bundle.ERR_FAILED,
                                           hive_bundle.ERR_MOVED) else hive_bundle.ERR_FAILED
        return {"key": b["key"], "error": err}
    sha, size = b.get("sha256"), b.get("size")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) \
            or isinstance(size, bool) or not 0 < size <= hive_bundle.MAX_BUNDLE_BYTES:
        return None
    seq = b.get("seq")
    return {"key": b["key"], "sha256": sha, "size": size,
            # the run-record entry it was sealed as (core/hive_records), on a Hive that keeps one
            **({"seq": seq} if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else {})}


# ── summarize ─────────────────────────────────────────────────────────────────

def _is_turn_start(row: dict) -> bool:
    """A user row that starts a turn: not a steer, which the running turn reads
    mid-way (``metadata.steer`` / ``metadata.steered``, core/session_pending); the
    turn's one outcome may land after it."""
    if row.get("role") != "user":
        return False
    meta = row.get("metadata")
    return not (isinstance(meta, dict) and ("steered" in meta or "steer" in meta))


#: what the Host can say about a required check: it saw it run, saw it error, saw it run
#: with no result, or saw no record of it (``not_seen``). Never "not run": a check run in
#: a shell loop, a script or a target is not seen, and a Host that says "not run" for it
#: says something false (design 2026-10-31-swarm-out-agent-owns-its-runs §2.7).
_CHECK_STATUSES = ("ran", "errored", "unknown", "not_seen")


def check_status(v: Any) -> str:
    """A kept check status as it is shown: a value from before ``not_seen`` (``not_run``)
    or any value not in the set reads as ``not_seen``. Used on every read of a kept
    record, which is never rewritten for it."""
    return v if v in _CHECK_STATUSES else "not_seen"
_REFUSAL_KEY_RE = re.compile(r"[A-Za-z0-9_.:-]{1,90}")
# where a command starts in a shell line: its beginning, or after a separator
_CMD_START = r"(?:^|&&|\|\||;|\||\n|\(|\$\()\s*"
# what may run a command without being the command: a wall-clock cap (perl -e '...'
# exec @ARGV, timeout / gtimeout N), env [-u X] [K=V ...], K=V, nice, time. A check run
# under one (every SwarmAI agent caps its tests this way) is still that check. Each
# branch starts with a different token and env's own K=V words are the outer K=V branch,
# so a word matches one way only; a value never takes in a separator (; & | ( )), where
# a command can start again, except inside one $( ... ) (VAR=$(pwd) pytest). The match
# stays linear on Hive-written commands.
_MAX_CALL_CHARS = 4096       # a command line is matched within its first 4 KB
_WRAPPERS = (r"(?:(?:perl\s+-e\s+(?:'[^']*'|\"[^\"]*\")"
             r"|g?timeout(?:\s+-[A-Za-z-]+(?:=[^\s;&|()]+)?)*\s+\d+(?:\.\d+)?[smhd]?"
             r"|env(?:\s+(?:-u\s+[^\s;&|()]+|-i))*"
             r"|[A-Za-z_][A-Za-z0-9_]*=(?:\$\([^()]*\)|[^\s;&|()])*"
             r"|nice(?:\s+-n\s*-?\d+)?"
             r"|time)\s+)*")


def _norm(cmd: Any) -> str:
    return " ".join(cmd.split()) if isinstance(cmd, str) else ""


def _runs(call: str, check: str) -> bool:
    """True when the shell line *call* runs *check* as a command of its own (at its
    start or after ``&&``, ``;``, ``|``, a newline, ``(``, past any ``_WRAPPERS``), with
    any arguments — not when it only mentions it (``grep "pytest -q"``, ``echo ...``)
    or writes it into a heredoc (a match after ``<<``)."""
    call = call[:_MAX_CALL_CHARS]
    for m in re.finditer(_CMD_START + _WRAPPERS + re.escape(check) + r"(?=$|\s|;|&|\||\))", call):
        if "<<" not in call[:m.start()]:
            return True
    return False


def _checks(blocks: list[dict], agent: Optional[dict],
            tests: Optional[list[dict]] = None,
            records: Optional[list[dict]] = None) -> Optional[list[dict]]:
    """Each check the dispatch expected (``agent.validations``, the host's own list).

    With ``records`` (the Hive's check record, one line per check that
    ``scripts/task_checks`` ran, by id: ``{id, exit, ...}``) each check's status is the
    record's: ``ran`` for exit 0, ``errored`` for any other exit, ``unknown`` for a check
    started and never ended, ``not_seen`` for a check with no line. The record is written
    by the program that ran the check, so how the session's Bash calls were written and
    what the agent's reply claims no longer decide the status. It is the Hive's record,
    not proof: the Hive's agent runs as the same OS user and could write that file (on a
    Hive no file is out of the agent's reach, core/hive_workspace module doc), so it
    removes accidental misreports, not a deliberate forgery. The rest of this docstring
    is the fallback for a Hive that keeps no record (``checks_inferred``).

    First from the run's own TEST report (``tests``: the commands of the session's
    pipeline summary, the record the run's REPORT shows): ``ran`` for a ``pass``,
    ``errored`` for a ``fail``, ``unknown`` otherwise, by the last command that runs the
    check. A check the report does not list is read from the Bash calls: ``ran`` when a
    call runs it and its result was not an error, ``errored`` when the last such call
    errored, ``not_seen`` otherwise: the Host found no call or report row that runs it,
    which is not "it did not run" (the persisted call: ``display.object``, the command as
    shown, cut at its display limit). A report row the run marked ``not_run`` is
    ``not_seen`` too. Never "passed": both are the Hive's own word on whether a command
    errored, not on what it found."""
    wanted = [v for v in (agent or {}).get("validations") or [] if isinstance(v, dict)
              and isinstance(v.get("id"), str) and _norm(v.get("command"))]
    if not wanted:
        return None
    if records is not None:
        last: dict[str, dict] = {}
        for line in records:
            if isinstance(line, dict) and isinstance(line.get("id"), str):
                last[line["id"]] = line          # the last line of a check is its latest run
        out = []
        for v in wanted:
            line = last.get(v["id"])
            code = line.get("exit") if line else None
            status = ("not_seen" if line is None
                      else "unknown" if not isinstance(code, int) or isinstance(code, bool)
                      else "ran" if code == 0 else "errored")
            out.append({"id": v["id"], "status": status})
        return out
    reported = [("\n".join(_norm(x) for x in str(t.get("command")).splitlines()), t.get("result"))
                for t in tests or [] if isinstance(t, dict) and isinstance(t.get("command"), str)]
    errored = {b.get("tool_use_id"): b.get("is_error") is True for b in blocks
               if b.get("type") == "tool_result"}
    calls = []
    for b in blocks:
        if b.get("type") == "tool_use" and b.get("name") == "Bash":
            disp = b.get("display") if isinstance(b.get("display"), dict) else {}
            line = disp.get("object") or b.get("summary") or ""
            # whitespace inside a line collapsed, the lines kept (a newline separates commands)
            calls.append(("\n".join(_norm(x) for x in str(line).splitlines()), errored.get(b.get("id"))))
    out = []
    for v in wanted:
        said = [r for cmd, r in reported if _runs(cmd, _norm(v["command"]))]
        if said:
            out.append({"id": v["id"], "status": {"pass": "ran", "fail": "errored",
                                                  "pre_existing": "errored",
                                                  "not_run": "not_seen"}.get(said[-1], "unknown")})
            continue
        hits = [e for cmd, e in calls if _runs(cmd, _norm(v["command"]))]
        status = "not_seen" if not hits else "unknown" if hits[-1] is None \
            else "errored" if hits[-1] else "ran"
        out.append({"id": v["id"], "status": status})
    return out



def _interventions(all_blocks: list) -> "tuple[int, int, int, int, int, int]":
    """(approved, denied, permission prompts not answered, questions answered, questions
    not answered, handled by the host) over a run's blocks. The first five are the
    person's; what the dispatching agent answered itself (design
    2026-10-31-hive-approvals §2.4: ``decided_by`` / ``answered_by`` host) is the host's,
    counted apart, never as the person stepping in."""
    approved = denied = unanswered_perm = answered_q = unanswered_q = host_handled = 0
    for b in all_blocks:
        if b.get("type") == "cmd_permission_request":
            dec = b.get("decision")
            if dec in ("approve", "deny") and b.get("decided_by") == "host":
                host_handled += 1
            elif dec == "approve":
                approved += 1
            elif dec == "deny":
                denied += 1
            else:
                unanswered_perm += 1
        elif b.get("type") == "ask_user_question":
            if isinstance(b.get("answers"), dict) and b["answers"]:
                if b.get("answered_by") == "host":
                    host_handled += 1
                else:
                    answered_q += 1
            else:
                unanswered_q += 1
    return approved, denied, unanswered_perm, answered_q, unanswered_q, host_handled


def summarize(messages: Any, has_more: bool = False, agent: Optional[dict] = None,
              dispatch_id: Optional[str] = None,
              check_records: Optional[list[dict]] = None) -> dict[str, Any]:
    """The result of one run from its session's rows (chronological, as the
    messages API returns them). Pure; never raises on malformed input. ``agent``: the
    Project Agent the dispatch went under (the host's log row): its checks are read from
    ``check_records`` (the Hive's check record, ``_fetch``) when the run kept one, else
    looked up in the run's Bash calls (``checks_inferred``)."""
    d = _Drops()
    rows = [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
    last_user = max((i for i, r in enumerate(rows) if _is_turn_start(r)), default=-1)
    turns = sum(1 for r in rows if _is_turn_start(r))
    # what the person said in the session after the task itself: each later user
    # message, a new turn or a steer read mid-turn (a re-run with a correction is one;
    # so is anything typed in the Hive tab). With older turns not read, the first row
    # read is not the task, so every one counts.
    said = sum(1 for r in rows if r.get("role") == "user") - (0 if has_more else 1)
    said = max(0, said)
    all_blocks = [b for r in rows if r.get("role") == "assistant" for b in _blocks(r)]
    after_rows = [r for r in rows[last_user + 1:] if r.get("role") == "assistant"]
    after = [b for r in after_rows for b in _blocks(r)]

    outcomes = [b for b in all_blocks if b.get("type") == "turn_outcome"]
    after_outcomes = [b for b in after if b.get("type") == "turn_outcome"]
    last_outcome = after_outcomes[-1] if after_outcomes else None
    stopped = any(b.get("type") == "turn_stop" for b in after)
    notices = [b.get("reason") for b in after if b.get("type") == "turn_notice"
               and b.get("reason") in _NOTICE_REASONS]

    ended_raw = last_outcome.get("ended") if last_outcome else None
    if stopped:
        ended = "stopped"
    elif ended_raw in ("completed", "turn_limit", "error", "crashed", "paused"):
        ended = ended_raw
    elif notices:
        ended = notices[-1]
    elif ended_raw == "none":
        ended = "no_terminal"        # recorded: the turn ended with no terminal event
    else:
        ended = "no_record"
    provisional = ended in ("paused", "no_record") and not stopped

    error = None
    if ended == "error" and last_outcome:
        error = {"code": d.s(last_outcome.get("code"), 64) or d.s(last_outcome.get("subtype"), 32),
                 "message": d.s(last_outcome.get("message"), 300)}
    elif ended == "crashed" and last_outcome:
        error = {"code": "crashed", "message": d.s(last_outcome.get("exception"), 64)}

    ended_at = d.s(last_outcome.get("at"), 40) if last_outcome else None
    if ended_at is None and rows:
        ended_at = d.s(rows[-1].get("created_at"), 40)

    active_ms = sum(d.i(o.get("duration_ms")) or 0 for o in outcomes)
    # the four kinds of token in each turn's CLI ``usage``: input not read from the cache,
    # output, cache reads and cache writes. That ``usage`` covers the main conversation
    # only, while the cost (the CLI's process total) also counts its sub-agents, so these
    # tokens are a lower bound; ``public`` takes the ledger's figures when it has them.
    tokens = dict.fromkeys(("input", "output", "cache_read", "cache_write"), 0)
    for o in outcomes:
        u = o.get("usage")
        if isinstance(u, dict):
            tokens["input"] += d.i(u.get("input_tokens")) or 0
            tokens["output"] += d.i(u.get("output_tokens")) or 0
            tokens["cache_read"] += d.i(u.get("cache_read_input_tokens")) or 0
            tokens["cache_write"] += d.i(u.get("cache_creation_input_tokens")) or 0
    costs, missing = turn_costs(outcomes)
    known = [c for c in costs if c is not None]
    cost = round(sum(known), 6) if known else None
    # a user turn with no outcome of its own (killed, or a Hive before the record)
    # left its spend out; counted per turn, as continuations and injected turns add
    # outcomes without a user row
    per_turn: list[int] = []
    for r in rows:
        if _is_turn_start(r):
            per_turn.append(0)
        elif per_turn and r.get("role") == "assistant":
            per_turn[-1] += sum(1 for b in _blocks(r) if b.get("type") == "turn_outcome")
    cost_partial = bool(missing or has_more or not outcomes or any(n == 0 for n in per_turn))

    texts = [b.get("text") for b in after if b.get("type") == "text"
             and isinstance(b.get("text"), str) and b["text"].strip()]
    report_full = texts[-1] if texts else ""

    deliverables: list[dict] = []
    seen: set[str] = set()
    deliverables_omitted = 0
    for b in all_blocks:
        if b.get("type") != "turn_deliverables":
            continue
        deliverables_omitted += d.i(b.get("omitted")) or 0
        for f in b.get("files") or [] if isinstance(b.get("files"), list) else []:
            if not isinstance(f, dict) or not isinstance(f.get("path"), str) or f["path"] in seen:
                continue
            seen.add(f["path"])
            if len(deliverables) >= MAX_DELIVERABLES:
                deliverables_omitted += 1
                continue
            kind = f.get("kind") if f.get("kind") in ("content", "knowledge", "external-diff",
                                                       "external-nodiff") else "other"
            ap = f.get("absolutePath")
            deliverables.append({"path": f["path"][:300], "kind": kind,
                                 # the file's identity, for its copy here (attach_local)
                                 **({"abs": ap} if isinstance(ap, str) and len(ap) <= 1000 else {})})

    bound: dict[str, dict] = {}            # turn_changes, by git root
    for b in all_blocks:
        if b.get("type") != "turn_changes":
            continue
        for f in b.get("files") or [] if isinstance(b.get("files"), list) else []:
            if not isinstance(f, dict):
                continue
            root = d.s(f.get("gitRoot"), 300) or "?"
            e = bound.setdefault(root, {"root": root, "paths": set(), "added": 0, "deleted": 0, "landed": []})
            if isinstance(f.get("path"), str):
                e["paths"].add(f["path"][:300])
            e["added"] += d.i(f.get("added")) or 0
            e["deleted"] += d.i(f.get("deleted")) or 0
        # a landing (core/session_landings._landing): its commits ``onto..new`` and its
        # files, which turn_changes lists under the landing instead of in ``files``
        for land in b.get("landed") or [] if isinstance(b.get("landed"), list) else []:
            if not isinstance(land, dict):
                continue
            root = d.s(land.get("gitRoot"), 300) or "?"
            e = bound.setdefault(root, {"root": root, "paths": set(), "added": 0, "deleted": 0, "landed": []})
            if isinstance(land.get("new"), str) and _SHA_RE.fullmatch(land["new"]):
                e["landed"].append(land["new"][:12])
            for f in land.get("files") or [] if isinstance(land.get("files"), list) else []:
                if isinstance(f, dict) and isinstance(f.get("path"), str):
                    e["paths"].add(f["path"][:300])
                    e["added"] += d.i(f.get("added")) or 0
                    e["deleted"] += d.i(f.get("deleted")) or 0
    changes = [{"root": e["root"], "repo": os.path.basename(e["root"]), "files": len(e["paths"]), "added": e["added"],
                "deleted": e["deleted"], "landed": e["landed"][:20]} for e in bound.values()]

    repos = None
    for o in reversed(outcomes):
        if isinstance(o.get("repos"), list):
            repos = [r for r in (_repo_fact(x, d) for x in o["repos"][:10]) if r]
            break

    tools: dict[str, int] = {}
    steps_total = failed = agents = 0
    for b in all_blocks:
        if b.get("type") == "tool_use":
            steps_total += 1
            name = b.get("name") if isinstance(b.get("name"), str) and _NAME_RE.fullmatch(b["name"]) else "other"
            tools[name] = tools.get(name, 0) + 1
            if name in ("Agent", "Task"):
                agents += 1
        elif b.get("type") == "tool_result" and b.get("is_error") is True:
            failed += 1
    top = dict(sorted(tools.items(), key=lambda kv: -kv[1])[:MAX_TOOL_NAMES])

    approved, denied, unanswered_perm, answered_q, unanswered_q, host_handled = _interventions(all_blocks)

    pipeline = None
    summaries = [b for b in all_blocks if b.get("type") == "pipeline_summary"]
    # every run the session worked in, in order, from the blocks written while a run is
    # in progress (pipeline_run, pipeline_stage) as well as at its end: a run that
    # stopped short of an end status has no summary but still ran (execution_check)
    runs_seen: list[dict[str, Any]] = []
    for b in all_blocks:
        if b.get("type") in ("pipeline_run", "pipeline_stage", "pipeline_summary") and isinstance(b.get("runId"), str):
            rid = d.s(b["runId"], 64)
            if rid and all(x["run_id"] != rid for x in runs_seen):
                runs_seen.append({"run_id": rid, "profile": d.s(b.get("profile"), 20)})
    own = agent.get("run_id") if isinstance(agent, dict) else None
    if own:
        # the runs this dispatch named (its first, and each correction's:
        # core/swarm_out.pipeline_run_id), the last one the session ended (another
        # run's summary may be here too: it abandoned a stale run, say)
        from core.swarm_out import pipeline_run_ids
        ids = (pipeline_run_ids(dispatch_id) if dispatch_id else set()) | {own}
        summaries = [b for b in summaries if b.get("runId") in ids] or summaries
    if summaries:
        s = summaries[-1]
        audit = []
        for r in s.get("audit") or [] if isinstance(s.get("audit"), list) else []:
            if isinstance(r, dict) and isinstance(r.get("dim"), str):
                v = r.get("verdict") if r.get("verdict") in _VERDICTS else None
                audit.append({"dim": r["dim"][:40], "verdict": v, "detail": d.s(r.get("detail"), 200)})
        landed = s.get("landedSha")
        pipeline = {"run_id": d.s(s.get("runId"), 64), "profile": d.s(s.get("profile"), 20),
                    "status": d.s(s.get("status"), 20), "landing": d.s(s.get("landing"), 20),
                    "landed_sha": landed[:12] if isinstance(landed, str) and _SHA_RE.fullmatch(landed) else None,
                    "audit": audit[:5]}

    # a file written inside a declared repository is that repository's change (its
    # line in ``repos``), not a document of its own
    repo_dirs = ["/" + r["dir"].strip("/") + "/" for r in repos or [] if isinstance(r.get("dir"), str) and r["dir"]]
    deliverables = [f for f in deliverables
                    if not any(rd in "/" + f["path"].lstrip("/") for rd in repo_dirs)]

    kinds = []
    if pipeline:
        kinds.append("pipeline")
    if changes or any((r.get("files_total") or r.get("commits_total")) for r in repos or []):
        kinds.append("code")
    if deliverables:
        kinds.append("files")
    # "only an answer" is said of a run that finished: a stopped or failed one left its
    # last words mid-way, which is not its answer
    if not kinds and report_full and ended in ("completed", "turn_limit"):
        kinds.append("answer")

    # None: no outcome says (a Hive that does not count them, or not a Project Agent run)
    refusals: Optional[dict[str, int]] = None
    for o in outcomes:
        g = o.get("gate_refusals")
        if not isinstance(g, dict):
            continue
        refusals = refusals if refusals is not None else {}
        for k, n in g.items():
            if isinstance(k, str) and _REFUSAL_KEY_RE.fullmatch(k) and d.i(n):
                refusals[k] = refusals.get(k, 0) + d.i(n)

    return {
        "ended": ended, "provisional": provisional, "outcome_recorded": bool(after_outcomes),
        "checks": _checks(all_blocks, agent, summaries[-1].get("tests") if summaries else None,
                          check_records),
        # the statuses were inferred from the session's command text: the Hive keeps no
        # check record for this run (an older Hive, or a task sent without task_checks)
        "checks_inferred": check_records is None and bool((agent or {}).get("validations")),
        # the statuses are the Hive's own record (scripts/task_checks): the card says so,
        # since the Hive's agent could have written that record too (``_checks``)
        "checks_from_record": check_records is not None and bool((agent or {}).get("validations")),
        "gate_refusals": refusals,
        # older turns were not read: a check or refusal there is not seen
        "checks_partial": bool(has_more),
        # any turn of the session has an outcome: the Hive records them, so a missing one
        # means the turn did not reach its end (a killed process), not an old Hive
        "hive_records_outcomes": bool(outcomes),
        "error": error, "ended_at": ended_at, "turns": turns,
        "older_turns_not_read": bool(has_more),
        "active_s": round(active_ms / 1000) if outcomes else None,
        "tokens": tokens if outcomes else None,
        "cost_usd": cost, "cost_partial": cost_partial,
        # the whole reply: a reply is one unit, shown whole or not at all (the list view
        # carries only its length, core/swarm_out._result_brief)
        "report": report_full, "report_chars": len(report_full),
        "kinds": kinds,
        "work": {
            "repos": repos, "changes": changes,
            "files": deliverables, "files_omitted": deliverables_omitted,
            "steps": {"total": steps_total, "failed": failed, "agents": agents, "by_tool": top},
            "human": {"approved": approved, "denied": denied, "unanswered": unanswered_perm + unanswered_q,
                      "answered_questions": answered_q, "said": said},
            "host_handled": host_handled,
        },
        "pipeline": pipeline,
        "pipeline_runs": runs_seen[:10],
        "dropped": d.n,
    }


# ── the kept record ───────────────────────────────────────────────────────────

def _dir() -> Path:
    from core.swarm_out import _state_dir
    p = _state_dir() / "results"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(dispatch_id: str) -> Optional[Path]:
    return _dir() / f"{dispatch_id}.json" if DISPATCH_ID_RE.fullmatch(dispatch_id or "") else None


def load(dispatch_id: str) -> Optional[dict]:
    p = _path(dispatch_id)
    if p is None or not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("swarm_out_results: unreadable record %s", dispatch_id)
        return None
    return rec if isinstance(rec, dict) else None


def _superseded(record: dict) -> None:
    """A result that ended before the last re-send (``resent_at``, ``turn_started``) is
    the turn the re-send replaces: it is not final (``provisional``) and says so
    (``superseded``), so nothing accepts it while the new turn runs or before its result
    is read. Applied at the one write point (``save``), so no reader has to remember."""
    res = record.get("result")
    sent = _parse_time(record.get("resent_at"))
    if not isinstance(res, dict) or sent is None:
        return
    ended = _parse_time(res.get("ended_at"))
    if ended is not None and ended < sent:
        res["provisional"] = True
        res["superseded"] = True


def _later_resend(record: dict, p: Path) -> None:
    """Keep the latest ``resent_at`` of the record being written and the one on disk."""
    try:
        on_disk = json.loads(p.read_text(encoding="utf-8")).get("resent_at") if p.exists() else None
    except (OSError, ValueError, AttributeError):
        on_disk = None
    mine, theirs = _parse_time(record.get("resent_at")), _parse_time(on_disk)
    if theirs is not None and (mine is None or theirs > mine):
        # the re-send came after this record was loaded: it also restarted the
        # "no ending recorded" timer (``turn_started``), which this copy predates
        record["resent_at"] = on_disk
        record["no_record_since"] = record["no_record_of"] = None


def _parse_time(v: Any) -> Optional[datetime]:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def save(record: dict) -> None:
    """Write the record atomically (a crash leaves the old file or the new one). The
    re-send mark is the later of this record's and the one on disk: ``turn_started``
    writes it without the run's check lock, so a check that loaded the record before
    the re-send and saves after it keeps the mark (``_later_resend``). Then a result older
    than that re-send is marked superseded (``_superseded``)."""
    p = _path(record.get("dispatch_id") or "")
    if p is None:
        raise ValueError("record without a valid dispatch_id")
    _later_resend(record, p)
    _superseded(record)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── fetch ─────────────────────────────────────────────────────────────────────

_locks: dict[tuple[int, str], asyncio.Lock] = {}   # one check per run at a time, per loop


def _lock_for(did: str) -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    if any(k[0] != loop_id for k in _locks):   # a lock binds to one loop (as _sems)
        _locks.clear()
    key = (loop_id, did)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock      # kept for the process (one small lock per dispatched run)




async def fetch(row: dict) -> dict:
    """Check the run's session on its Hive once and keep what came back; returns the
    kept record. Never raises: a failure is written as ``fetch_error`` and the
    previous result stays. Checks of one run (the background check, the card's
    refresh) run one after the other, so each starts from what the last one kept and
    an older answer never replaces a newer one."""
    async with _lock_for(row.get("dispatch_id") or ""):
        return await _fetch(row)


async def _fetch(row: dict) -> dict:
    from core import swarm_out
    did, sid = row.get("dispatch_id") or "", row.get("session_id")
    prev = await asyncio.to_thread(load, did) or {}
    rec = {"dispatch_id": did, "hive_id": row.get("hive_id"), "session_id": sid,
           "etag": prev.get("etag"), "transcript_rev": prev.get("transcript_rev"),
           "fetched_at": prev.get("fetched_at"), "checked_at": _now(),
           "fetch_error": None, "result": prev.get("result"),
           "summary_version": prev.get("summary_version"), "lessons": prev.get("lessons"),
           "records": prev.get("records"), "no_record_since": prev.get("no_record_since"),
           "no_record_of": prev.get("no_record_of"), "resent_at": prev.get("resent_at"),
           "check_records": prev.get("check_records")}
    if not isinstance(sid, str) or not swarm_out.KEY_RE.fullmatch(sid):
        rec["fetch_error"] = "no session"
        return rec
    try:
        inst = await swarm_out._instance(row.get("hive_id") or "")
        # the Hive's check record (scripts/task_checks), for a task sent with checks by
        # id; kept here, because the Hive removes it with the workspace
        await _take_check_records(row, inst, rec)
        # a Hive that keeps run records (core/hive_records): take the new entries, then
        # read only the copy kept here
        records = False
        if DISPATCH_ID_RE.fullmatch(did):
            from core import hive_records
            pulled = await asyncio.wait_for(hive_records.pull(row, inst), PULL_TIMEOUT_S)
            records = pulled["supported"]
            if not records:
                rec["records"] = None
            elif await _from_records(row, rec, prev, pulled):
                return await _keep(rec)
            # a record that does not start at the session's first turn (a session older
            # than its Hive's records): its result is still read the old way below
        current = prev.get("summary_version") == SUMMARY_VERSION
        # the check record is an input of the summary too: when it changed, the
        # messages are read again even if they did not (their ETag covers only them)
        same_checks = rec.get("check_records") == prev.get("check_records")
        headers = {"If-None-Match": rec["etag"]} \
            if rec["etag"] and rec["result"] is not None and current and same_checks else {}
        async with swarm_out._client(inst) as client:
            r = await client.get(f"/api/chat/sessions/{sid}/messages",
                                 params={"turns": FETCH_TURNS}, headers=headers)
        if r.status_code == 304:
            pass
        elif r.status_code == 404:
            rec["fetch_error"] = "session_gone"
        elif r.status_code != 200:
            rec["fetch_error"] = f"http_{r.status_code}"
        else:
            body = r.json()
            if not isinstance(body, list):
                rec["fetch_error"] = "unexpected_body"
            else:
                agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else None
                # a summary reads Hive-written text: off the event loop
                rec["result"] = await asyncio.to_thread(summarize, body, r.headers.get("x-has-more") == "1", agent,
                                                        rec.get("dispatch_id"), _records_of(row, rec))
                rec["summary_version"] = SUMMARY_VERSION
                etag = r.headers.get("etag")
                rec["etag"] = etag[:200] if isinstance(etag, str) else None
                rev = r.headers.get("x-transcript-rev")
                rec["transcript_rev"] = int(rev) if isinstance(rev, str) and rev.isdigit() else None
                rec["fetched_at"] = rec["checked_at"]
        if rec["fetch_error"] is None and not records:
            # the run's lessons, kept here and queued for this machine's CDF (core/hive_lessons)
            from core import hive_lessons
            got = await hive_lessons.take(row, inst, prev.get("lessons"))
            if got is not None:
                rec["lessons"] = got
    except swarm_out.SwarmOutError as e:
        rec["fetch_error"] = "hive_not_running" if "not running" in str(e) else "hive_unusable"
    except asyncio.TimeoutError:
        rec["fetch_error"] = "hive_silent"     # the record pull's own bound: kept what it kept
    except Exception as e:  # noqa: BLE001 — Hive slow or unreachable: keep the old result
        logger.warning("swarm_out_results: checking %s failed (%s)", did, type(e).__name__)
        rec["fetch_error"] = "hive_silent"
    return await _keep(rec)


#: lines of a check record taken from a Hive (a task has at most a few dozen checks,
#: each run a few times)
MAX_CHECK_LINES = 500


def _records_of(row: dict, rec: dict) -> Optional[list[dict]]:
    """The check record to read the checks from: the one kept for a task sent with
    checks by id (an empty list when the Hive ran none yet), else None (the statuses are
    then inferred from the session's command text)."""
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    if agent.get("checks_by_id") is not True:
        return None
    got = rec.get("check_records")
    return got if isinstance(got, list) else []


async def _take_check_records(row: dict, inst: dict, rec: dict) -> None:
    """Read the Hive's check record of this task (``GET /api/hive/work/<id>/checks``)
    into ``rec["check_records"]``. A failed read keeps what an earlier check kept: the
    record is gone from the Hive once the workspace is released."""
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    did = row.get("dispatch_id") or ""
    if agent.get("checks_by_id") is not True or not DISPATCH_ID_RE.fullmatch(did):
        return
    from core import swarm_out
    try:
        async with swarm_out._client(inst, swarm_out.PROBE_TIMEOUT_S) as client:
            r = await client.get(f"/api/hive/work/{did}/checks")
        body = r.json() if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001 — Hive slow or unreachable: keep the kept record
        logger.warning("swarm_out_results: check record of %s not read (%s)", did, type(e).__name__)
        return
    lines = body.get("checks") if isinstance(body, dict) else None
    if not isinstance(lines, list):
        return
    keep = []
    for x in lines[-MAX_CHECK_LINES:]:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or len(x["id"]) > 100:
            continue
        code = x.get("exit")
        keep.append({"id": x["id"],
                     "exit": code if isinstance(code, int) and not isinstance(code, bool) else None,
                     "started_at": x.get("started_at") if isinstance(x.get("started_at"), str) else None,
                     "ended_at": x.get("ended_at") if isinstance(x.get("ended_at"), str) else None})
    rec["check_records"] = keep


def _note_no_record(rec: dict) -> None:
    """Since when the kept result has said ``no_record`` (the last turn has no end
    record) for the same turn (``no_record_of``: the result's ``ended_at``): the earlier
    check's time while it still does (``_fetch`` carries both over), this check's on
    the first or once the turn changed, None once there is an ending. A new turn sent
    from here clears it (``turn_started``). Read by core/swarm_out_settle.interrupted."""
    res = rec.get("result") if isinstance(rec.get("result"), dict) else {}
    if res.get("ended") == "no_record":
        same = rec.get("no_record_since") and rec.get("no_record_of") == res.get("ended_at")
        rec["no_record_since"] = rec["no_record_since"] if same else rec.get("checked_at")
        rec["no_record_of"] = res.get("ended_at")
    else:
        rec["no_record_since"] = rec["no_record_of"] = None


def turn_started(dispatch_id: str) -> None:
    """A new turn was sent into the run's session (a re-run): the kept result belongs
    to the turn before, so it is no longer final (``resent_at``, ``_superseded``), and
    its "no ending recorded" time starts again."""
    rec = load(dispatch_id)
    if not isinstance(rec, dict):
        return
    rec["resent_at"] = datetime.now(timezone.utc).isoformat()
    rec["no_record_since"] = rec["no_record_of"] = None
    try:
        save(rec)
    except (OSError, ValueError) as e:
        logger.warning("swarm_out_results: %s: %s", dispatch_id, e)


async def _keep(rec: dict) -> dict:
    _note_no_record(rec)
    try:
        await asyncio.to_thread(save, rec)
    except Exception as e:  # noqa: BLE001 — the next check writes again
        logger.error("swarm_out_results: keeping %s failed: %s", rec.get("dispatch_id"), e)
    return rec


#: a record check that failed for a reason the Hive could fix by answering again
_RECORD_TRANSIENT = ("hive_silent", "hive_unusable")


#: one record pull's wall clock: a Hive streaming slowly never holds the run's lock longer
PULL_TIMEOUT_S = 120


def attach_local(result: dict, placed: list[dict]) -> None:
    """Give each listed file that came back from the Hive (core/hive_files.placed) its
    ``local`` path here, or ``not_placed`` when it came back and could not be put in
    the run's Brain. Matched by identity: the path its turn's block listed it under,
    exactly (never by a suffix: two files can end the same way)."""
    by_listed = {p["listed"]: p for p in placed if isinstance(p, dict) and isinstance(p.get("listed"), str)}
    files = ((result or {}).get("work") or {}).get("files")
    for f in files if isinstance(files, list) else []:
        got = by_listed.get(f.get("abs")) if isinstance(f, dict) else None
        if not got:
            continue
        if isinstance(got.get("local"), str):
            f["local"] = got["local"]
        elif got.get("error"):
            f["not_placed"] = True


async def _from_records(row: dict, rec: dict, prev: dict, pulled: dict) -> bool:
    """Fill ``rec`` from the run's record kept here (core/hive_records): the record's
    state, the lessons, and the result when the record starts at the session's first turn.
    Returns whether the result came from the record (False: read it the old way)."""
    from core import hive_lessons, hive_records
    did = rec["dispatch_id"]
    err = pulled.get("error")
    rec["records"] = {"seq": pulled.get("seq", 0), "closed": pulled.get("closed") is True,
                      "last_at": pulled.get("last_at"), "error": err}
    got = hive_lessons._agent_of(row)
    if got is not None:
        rec["lessons"] = await asyncio.to_thread(hive_lessons.settle, got[0], got[1],
                                                 prev.get("lessons"))
    rows, complete, partial = await asyncio.to_thread(hive_records.turn_rows, did)
    if rows and not complete:
        return False
    agent = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else None
    if rows:
        rec["result"] = await asyncio.to_thread(summarize, rows, partial, agent, did, _records_of(row, rec))
        from core import hive_files
        attach_local(rec["result"], await asyncio.to_thread(hive_files.placed, did))
        rec["summary_version"] = SUMMARY_VERSION
        rec["fetched_at"] = rec["checked_at"]
    rec["etag"] = rec["transcript_rev"] = None
    rec["fetch_error"] = (err if err in _RECORD_TRANSIENT else "record_check_failed") if err else None
    return True


# ── checks started from /runs ─────────────────────────────────────────────────

_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()
_sems: dict[int, asyncio.Semaphore] = {}     # per event loop (a semaphore binds to one)


async def _check(row: dict) -> None:
    loop_id = id(asyncio.get_running_loop())
    sem = _sems.get(loop_id)
    if sem is None:
        _sems.clear()
        sem = _sems[loop_id] = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    async with sem:
        await fetch(row)


def _start_check(row: dict) -> bool:
    did = row.get("dispatch_id") or ""
    if did in _inflight or not DISPATCH_ID_RE.fullmatch(did):
        return False
    _inflight.add(did)
    task = asyncio.get_running_loop().create_task(_check(dict(row)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    # in the done callback, not the coroutine: a task cancelled before it first runs
    # never runs its own finally
    task.add_done_callback(lambda _t, d=did: _inflight.discard(d))
    return True


def _age_s(sent_at: Any) -> Optional[float]:
    try:
        t = datetime.strptime(str(sent_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return time.time() - t.timestamp()


#: after a record says it is complete, how often it is asked again (a session may be
#: resumed and produce more)
CLOSED_RECHECK_S = 600


def check_due(row: dict, hive_status: Optional[str], kept: Optional[dict]) -> bool:
    """A stopped run on a running Hive is checked on every poll, and once when it has
    never been checked. A run whose Hive keeps records (core/hive_records) until its
    record is ``closed`` (then every ``CLOSED_RECHECK_S``), and never after 14 days
    without a new entry. Without records, while it is recent (``RECENT_S`` from sending)."""
    if hive_status != "running" or not row.get("session_id"):
        return False
    if row.get("state") != "stopped" or row.get("state_reason") not in ("idle", "not_held"):
        return False
    if kept is None:
        return True
    recs = kept.get("records")
    if isinstance(recs, dict):
        from core import hive_records
        if hive_records.backstop_passed(recs.get("last_at") or row.get("sent_at")):
            return False
        if recs.get("closed"):
            age = _age_s(kept.get("checked_at"))
            return age is None or age > CLOSED_RECHECK_S
        return True
    age = _age_s(row.get("sent_at"))
    return age is not None and age <= RECENT_S


_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?")
_KINDS = ("pipeline", "code", "files", "answer")
_FILE_KINDS = ("content", "knowledge", "external-diff", "external-nodiff", "other")
_DIMS = ("Run Isolation", "Quality", "Autonomy", "Speed", "Token Cost")
#: the error codes the product emits (its error events, turn_outcome's ``crashed`` and
#: the CLI's result subtypes); any other code is ``other`` in /runs, as it is Hive text
ERROR_CODES = frozenset({
    "crashed", "AGENT_EXECUTION_ERROR", "AGENT_TIMEOUT", "ALL_RETRIES_EXHAUSTED",
    "ACCOUNT_NOT_CONNECTED", "CONVERSATION_ERROR", "CREDENTIALS_EXPIRED", "EMPTY_ANSWER", "EMPTY_MESSAGE",
    "MODEL_ERROR", "NOT_INITIALIZED", "OOM_DETECTED", "OOM_LIMIT_REACHED",
    "PERMISSION_EXPIRED", "POLICY_VIOLATION", "QUEUE_TIMEOUT", "RESOURCE_EXHAUSTED",
    "RETRY_SPAWN", "SDK_ERROR", "SERIALIZATION_ERROR", "SERVICE_UNAVAILABLE",
    "SESSION_BUSY", "SESSION_NOT_FOUND", "SPAWN_FAILED", "TURN_ABANDONED",
    "error_max_turns", "error_during_execution", "turn_limit_reached",
})
_LANDINGS = ("landed", "not_landed", "no_code", "human_override")


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _opt_int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _match(rx: re.Pattern, v: Any) -> Optional[str]:
    return v if isinstance(v, str) and rx.fullmatch(v) else None


def _one_of(v: Any, allowed: Iterable[str]) -> Optional[str]:
    return v if isinstance(v, str) and v in allowed else None


def _declared(declared: Any) -> dict[str, dict]:
    """The dispatch's own repositories (this Host's log row), by directory."""
    from core import task_repos
    try:
        return {r["dir"]: r for r in task_repos.parse(declared or [])}
    except task_repos.TaskRepoError:
        return {}


def _public_repo(r: dict, host: dict) -> dict:
    """A declared repository's numbers under the name this Host sent (``host``); its
    error only when it is one task_repos writes."""
    from core import task_repos
    err = r.get("error")
    if err is not None:
        err = err if err in task_repos.ERRORS else task_repos.ERROR_UNREADABLE
    return {"url": host["url"], "ref": host["ref"], "dir": host["dir"],
            "present": r.get("present") if isinstance(r.get("present"), bool) else None,
            "origin_matches": r.get("origin_matches") if isinstance(r.get("origin_matches"), bool) else None,
            "base": _match(_SHA_RE, r.get("base")), "head": _match(_SHA_RE, r.get("head")),
            "commits": [], "commits_total": _int(r.get("commits_total")),
            "files": [], "files_total": _int(r.get("files_total")),
            "added": _int(r.get("added")), "deleted": _int(r.get("deleted")),
            "untracked": _int(r.get("untracked")),
            "pushed": r.get("pushed") if r.get("pushed") in ("yes", "no", "nothing_to_push", "unknown") else "unknown",
            "error": err,
            # whether the commits can be taken back (and why not), no Hive text
            "bundle": None if not isinstance(r.get("bundle"), dict) else
            ({"available": True} if r["bundle"].get("sha256") else {"available": False, "error": r["bundle"].get("error")})}


#: why the run did not run the way it was dispatched to (``execution_check``)
EXECUTION_DIFFS = ("made_a_run", "no_run", "other_profile", "other_run")


def execution_check(res: dict, agent: Any, dispatch_id: Optional[str] = None) -> Optional[dict]:
    """Did the run run the way the Host pinned it (design 2026-10-30-run-spec §4.2, the
    evidence layer)? ``{"state": "matches" | "differs" | "not_yet", "why": code}`` from
    the row's ``execution`` / ``profile`` / ``run_id`` and the run's own pipeline record
    (``res["pipeline"]``); None for a row from before the choice was made per dispatch.
    ``not_yet``: a pipeline task whose record shows no run yet, before the run ended."""
    if not isinstance(agent, dict) or agent.get("execution") not in ("native", "pipeline"):
        return None
    seen = [r for r in res.get("pipeline_runs") or [] if isinstance(r, dict) and r.get("run_id")]
    summary = res.get("pipeline") if isinstance(res.get("pipeline"), dict) else None
    if summary and summary.get("run_id") and all(r["run_id"] != summary["run_id"] for r in seen):
        seen.append({"run_id": summary["run_id"], "profile": summary.get("profile")})   # an older record
    if agent["execution"] == "native":
        return {"state": "differs", "why": "made_a_run"} if seen else {"state": "matches", "why": None}
    if not seen:
        ended = res.get("ended") in ENDED and res.get("provisional") is not True
        return {"state": "differs", "why": "no_run"} if ended else {"state": "not_yet", "why": None}
    # the task's own runs: the first, and a correction's (core/swarm_out.pipeline_run_id)
    if agent.get("run_id"):
        from core.swarm_out import pipeline_run_ids
        ids = pipeline_run_ids(dispatch_id) if dispatch_id else set()
        ids.add(agent["run_id"])
        own = [r for r in seen if r["run_id"] in ids]
    else:
        own = seen[-1:]
    if not own:
        return {"state": "differs", "why": "other_run"}
    if agent.get("profile") and own[0].get("profile") and own[0]["profile"] != agent["profile"]:
        return {"state": "differs", "why": "other_profile"}
    return {"state": "matches", "why": None}


def _public_agent(res: dict, agent: Any, dispatch_id: Optional[str] = None) -> dict:
    """The Project Agent part of ``/runs``: the brain, mode, profile and delivery from
    the host's own row; check statuses only for the row's check ids; gate refusals only
    under the row's gate names (or ``integrity``)."""
    if not isinstance(agent, dict) or not isinstance(agent.get("brain"), str):
        return {"project_agent": None, "checks": None, "gate_refusals": None, "checks_partial": False,
                "checks_inferred": False, "checks_from_record": False, "execution_check": None}
    ids = [v.get("id") for v in agent.get("validations") or [] if isinstance(v, dict)]
    got = {c.get("id"): c.get("status") for c in res.get("checks") or [] if isinstance(c, dict)}
    names = {g for g in agent.get("gates") or [] if isinstance(g, str)}
    gates = names | {f"error:{g}" for g in names} | {"integrity"}
    ref = res.get("gate_refusals") if isinstance(res.get("gate_refusals"), dict) else None
    return {
        "project_agent": {**{k: agent.get(k) if isinstance(agent.get(k), str) else None
                             for k in ("brain", "mode", "profile", "delivery")},
                          # how the run was dispatched to run (native / pipeline); a row from
                          # before the choice was made per dispatch has none
                          "execution": agent.get("execution") if agent.get("execution") in ("native", "pipeline")
                          else None},
        "checks": [{"id": i, "status": check_status(got.get(i))}
                   for i in ids if isinstance(i, str)] if ids else None,
        "gate_refusals": None if ref is None else {g: _int(ref.get(g)) for g in sorted(gates) if _int(ref.get(g))},
        "checks_partial": res.get("checks_partial") is True,
        "checks_inferred": res.get("checks_inferred") is True,
        "checks_from_record": res.get("checks_from_record") is True,
        "execution_check": execution_check(res, agent, dispatch_id),
    }


#: a ledger total this far under the record's CLI total has not caught up yet
_COST_EPS = 0.005
#: a ledger row this long after the run's recorded end still belongs to it (the row is
#: written when the CLI result arrives, the outcome a moment later, both on the Hive)
_END_SLACK_S = 120
#: how a run ended when nothing more will be added to it
_FINAL = frozenset({"completed", "turn_limit", "error", "crashed", "stopped"})
_KINDS = ("input", "output", "cache_read", "cache_write")


def _utc(at: Any) -> Optional[datetime]:
    """An ISO time as UTC; a time with no zone is taken as UTC (a Hive runs in UTC)."""
    if not isinstance(at, str):
        return None
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def spend_of(dispatch_ids: Iterable[str], conn: Any = None,
             until: Optional[dict[str, Any]] = None) -> dict[str, dict]:
    """Each run's spend rows in the usage ledger, summed: ``{dispatch_id: {tokens, cost_usd,
    models, rows}}`` (a run with no row is absent). ``until``: a run's recorded end
    (``ended_at``); its rows after that (plus ``_END_SLACK_S``) are a later turn the
    record does not show yet and are left out. ``models``: the registry's short names,
    sorted; a model the registry does not know is ``"other"`` (a Hive's text never gets
    in). Read-only; an unreadable ledger is an empty answer."""
    ids = sorted({i for i in dispatch_ids if isinstance(i, str) and DISPATCH_ID_RE.fullmatch(i)})
    if not ids:
        return {}
    from datetime import timedelta
    from core import usage_report
    from model_registry import MODEL_NAMES, normalize_short_name
    ends = {i: e + timedelta(seconds=_END_SLACK_S) for i in ids
            if (e := _utc((until or {}).get(i))) is not None}
    own = conn is None
    try:
        conn = conn or usage_report.connect()
    except Exception as e:  # noqa: BLE001 — the result still shows the record's figures
        logger.debug("swarm_out_results: ledger not readable (%s)", type(e).__name__)
        return {}
    out: dict[str, dict] = {}
    try:
        for start in range(0, len(ids), 500):            # under SQLite's variable limit
            part = ids[start:start + 500]
            marks = ",".join("?" * len(part))
            for did, at, model, i, o, cr, cw, cost in conn.execute(
                    "SELECT dispatch_id, at_utc, model, input, output, cache_read, cache_write, cost_usd "
                    f"FROM usage_ledger WHERE kind = 'spend' AND dispatch_id IN ({marks})", part):
                end = ends.get(did)
                t = _utc(at)
                if end is not None and t is not None and t > end:
                    continue
                s = out.setdefault(did, {"tokens": dict.fromkeys(_KINDS, 0), "cost_usd": None,
                                         "models": set(), "rows": 0})
                for k, v in zip(_KINDS, (i, o, cr, cw)):
                    s["tokens"][k] += int(v or 0)
                if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0:
                    s["cost_usd"] = (s["cost_usd"] or 0.0) + float(cost)
                name = normalize_short_name(model) if model else ""
                if name:
                    s["models"].add(name if name in MODEL_NAMES else "other")
                s["rows"] += 1
    except Exception as e:  # noqa: BLE001
        logger.debug("swarm_out_results: ledger read failed (%s)", type(e).__name__)
        return {}
    finally:
        if own:
            conn.close()
    for s in out.values():
        s["models"] = sorted(s["models"])
    return out


def _usage(res: dict, spend: Optional[dict]) -> dict[str, Any]:
    """The tokens, cost and models a result shows. The ledger's rows are the spend once
    the run has ended and they hold at least what the CLI counted; until then the
    record's figures stand in, each kind at least the larger of the two counts, and the
    tokens are a lower bound (``tokens_partial``). A kind the record never counted (a
    record kept before the cache was counted) stays unknown unless the ledger has it."""
    tokens = res.get("tokens")
    rec = ({"input": _int(tokens.get("input")), "output": _int(tokens.get("output")),
            "cache_read": _opt_int(tokens.get("cache_read")),
            "cache_write": _opt_int(tokens.get("cache_write"))}
           if isinstance(tokens, dict) else None)
    cost = res.get("cost_usd")
    rec_cost = (cost if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                and math.isfinite(cost) and cost >= 0 else None)
    rec_partial = res.get("cost_partial") is not False
    if not spend or not spend.get("rows"):
        return {"tokens": rec, "tokens_partial": rec is not None, "cost_usd": rec_cost,
                "cost_partial": rec_partial, "models": None}
    led, led_cost = spend["tokens"], spend.get("cost_usd")
    models = list(spend.get("models") or [])
    final = res.get("ended") in _FINAL and res.get("provisional") is not True
    if final and led_cost is not None and (rec_cost is None or led_cost >= rec_cost - _COST_EPS):
        # an ended run whose ledger holds at least what the CLI counted: it is the spend
        return {"tokens": dict(led), "tokens_partial": False, "cost_usd": round(led_cost, 6),
                "cost_partial": rec_partial and not (rec_cost is not None and led_cost > rec_cost + _COST_EPS),
                "models": models}
    both: dict[str, Optional[int]] = {}
    for k in _KINDS:
        r, l = (rec or {}).get(k), int(led.get(k) or 0)
        both[k] = None if (r is None and l == 0 and rec is not None) else max(l, r or 0)
    best = max((c for c in (rec_cost, led_cost) if c is not None), default=None)
    return {"tokens": both, "tokens_partial": True, "cost_usd": best,
            "cost_partial": rec_partial or best is None or not final, "models": models}


def public(record: Optional[dict], declared: Any = None, agent: Any = None,
           spend: Optional[dict] = None) -> Optional[dict]:
    """The result as ``/runs`` carries it, which the CLI prints and the local agent
    reads: built field by field from enums, fixed value lists, numbers, shas and a
    pipeline run id of its fixed shape. No other string the Hive chose gets in: a repository appears under the URL, ref and
    directory this Host sent (``declared``, the dispatch log row's ``repos``; a
    repository the Hive reports that was not declared is left out), and a change card
    of another repository has no name. The reply, error messages, audit details,
    file paths and tool names are served to the panel only (``/runs/{id}/result``)."""
    from core.run_protocol.lifecycle import PROFILE_STAGES
    from core.pipeline_stage_events import RUN_END_STATUSES
    res = (record or {}).get("result")
    if not isinstance(res, dict):
        return None
    hosts = _declared(declared)
    work = res.get("work") if isinstance(res.get("work"), dict) else {}
    steps = work.get("steps") if isinstance(work.get("steps"), dict) else {}
    human = work.get("human") if isinstance(work.get("human"), dict) else {}
    err = res.get("error")
    error = None
    if isinstance(err, dict):
        error = {"code": err.get("code") if err.get("code") in ERROR_CODES else "other"}
    usage = _usage(res, spend)
    p = res.get("pipeline")
    pipeline = None
    if isinstance(p, dict):
        audit = [{"dim": a["dim"], "verdict": a.get("verdict") if a.get("verdict") in _VERDICTS else None,
                  "detail": None}
                 for a in (p.get("audit") or []) if isinstance(a, dict) and a.get("dim") in _DIMS]
        pipeline = {"run_id": _match(_RUN_ID_RE, p.get("run_id")), "profile": _one_of(p.get("profile"), PROFILE_STAGES),
                    "status": _one_of(p.get("status"), RUN_END_STATUSES),
                    "landing": _one_of(p.get("landing"), _LANDINGS),
                    "landed_sha": _match(_SHA_RE, p.get("landed_sha")), "audit": audit[:5]}
    repos = work.get("repos")
    names = {d.rsplit("/", 1)[-1] for d in hosts}
    return {
        "ended": res.get("ended") if res.get("ended") in ENDED else "no_record",
        "provisional": res.get("provisional") is True,
        "outcome_recorded": res.get("outcome_recorded") is True,
        "hive_records_outcomes": res.get("hive_records_outcomes") is True,
        "error": error, "ended_at": _match(_TIME_RE, res.get("ended_at")),
        "turns": _int(res.get("turns")), "older_turns_not_read": res.get("older_turns_not_read") is True,
        "active_s": _opt_int(res.get("active_s")),
        # the ledger's spend when it has the run's rows, else the record's (_usage); a
        # record kept before the cache was counted has no cache keys: None, so the panel
        # says the total is not known rather than showing input + output alone
        **usage,
        "report_chars": _int(res.get("report_chars")),
        "kinds": [k for k in (res.get("kinds") or []) if k in _KINDS],
        "work": {
            "repos": [_public_repo(r, hosts[r["dir"]]) for r in repos
                      if isinstance(r, dict) and isinstance(r.get("dir"), str) and r["dir"] in hosts]
            if isinstance(repos, list) else None,
            "changes": [{"root": None, "repo": _one_of(c.get("repo"), names),
                         "files": _int(c.get("files")), "added": _int(c.get("added")),
                         "deleted": _int(c.get("deleted")),
                         "landed": [x for x in (c.get("landed") or []) if _match(_SHA_RE, x)][:20]}
                        for c in (work.get("changes") or []) if isinstance(c, dict)],
            "files": [{"kind": f.get("kind") if f.get("kind") in _FILE_KINDS else "other"}
                      for f in (work.get("files") or []) if isinstance(f, dict)],
            "files_omitted": _int(work.get("files_omitted")),
            "steps": {"total": _int(steps.get("total")), "failed": _int(steps.get("failed")),
                      "agents": _int(steps.get("agents")), "by_tool": {}},
            "human": {k: _int(human.get(k)) for k in ("approved", "denied", "unanswered", "answered_questions", "said")},
            "host_handled": _int(work.get("host_handled")),
        },
        "pipeline": pipeline,
        "dropped": _int(res.get("dropped")),
        **_public_agent(res, agent, (record or {}).get("dispatch_id")),
        "lessons": _public_lessons(record),
    }


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_LESSON_ERROR_RE = re.compile(r"not_supported|bad_session|too_large|hive_unusable|hive_silent|unexpected_body|not_queued|http_\d{3}")


def _public_lessons(record: Optional[dict]) -> Optional[dict]:
    """The run's lessons as counts (core/hive_lessons): how many are kept here, how many
    wait for the person (``pending``), how many went to this machine's judge and what it
    decided (``written`` / ``rejected`` / ``judging``, from ``lesson_states`` read by
    ``_read_kept``), how many were skipped (another scope or kind) or refused."""
    les = (record or {}).get("lessons")
    if not isinstance(les, dict):
        return None
    st = (record or {}).get("lesson_states")
    st = st if isinstance(st, dict) else {}
    cands = les.get("candidates")
    err = les.get("error")
    return {"returned": _int(les.get("returned")),
            # waiting for the person (design 2026-10-29 §7 D3), and whether they took some
            "pending": _int(les.get("pending")), "accepted": isinstance(les.get("accepted_at"), str),
            "sent": len(cands) if isinstance(cands, list) else 0,
            "written": _int(st.get("written")), "rejected": _int(st.get("rejected")),
            "judging": _int(st.get("pending")),
            "skipped": _int(les.get("skipped")),
            "refused": len(les["refused"]) if isinstance(les.get("refused"), list) else 0,
            "truncated": les.get("truncated") is True,
            # the waiting units, as this machine's hashes: what an accept takes
            "digests": [x for x in (les.get("digests") if isinstance(les.get("digests"), list) else [])
                        if isinstance(x, str) and _DIGEST_RE.fullmatch(x)][:_int(les.get("pending"))],
            "error": err if isinstance(err, str) and _LESSON_ERROR_RE.fullmatch(err) else None}


def _status(row: dict, hive_status: Optional[str], kept: Optional[dict]) -> str:
    if not row.get("session_id"):
        return "not_applicable"
    if kept and kept.get("result") is not None:
        return "fetched"
    if hive_status == "gone":
        return "not_applicable"
    if kept and kept.get("fetch_error"):
        return "failed"
    return "pending"


def _read_kept(ids: list[str]) -> dict[str, Optional[dict]]:
    out = {i: load(i) for i in ids}
    for rec in out.values():
        les = (rec or {}).get("lessons")
        if isinstance(les, dict) and les.get("candidates"):
            from core import hive_lessons
            rec["lesson_states"] = hive_lessons.states(les["candidates"])
    return out


async def attach(rows: list[dict], status_of: dict[str, Optional[str]]) -> None:
    """Add ``result`` / ``result_status`` / ``result_error`` / ``result_checked_at`` to
    each row from its kept record, and start the checks that are due (in the
    background: this poll returns what is kept, the next one sees the new result)."""
    ids = [r.get("dispatch_id") or "" for r in rows]
    def _read() -> tuple[dict, dict]:
        kept = _read_kept(ids)
        # only a run with a kept result shows tokens and cost; up to its recorded end
        ends = {i: k["result"].get("ended_at") for i, k in kept.items()
                if k and isinstance(k.get("result"), dict)}
        return kept, spend_of(list(ends), until=ends)
    kept, spend = await asyncio.to_thread(_read)
    for r in rows:
        k = kept.get(r.get("dispatch_id") or "")
        st = status_of.get(r.get("hive_id"))
        r["result"] = public(k, r.get("repos"), r.get("project_agent"), spend.get(r.get("dispatch_id") or ""))
        r["result_status"] = _status(r, st, k)
        r["result_error"] = (k or {}).get("fetch_error")
        r["result_checked_at"] = (k or {}).get("checked_at")
        if check_due(r, st, k):
            _start_check(r)
