"""hive_diagnostics — why a Hive run's session stopped, read from its Hive over SSM.

Design: docs/design/2026-11-02-swarm-out-hive-deaths-diagnostics-states.md §2.

When a run's Hive session dies, the Host sees only effects (an exit code, a probe that
does not answer). The cause is on the Hive: the kernel's OOM killer, systemd stopping
the service, memory. ``read(dispatch_id)`` reads it with ONE fixed, read-only script sent
by SSM Run Command, the channel the Host already uses for updates and health
(hive/provisioner). SSM, not an HTTP route on the Hive: the case that matters is the
daemon being down or restarting, when no route answers.

Safety. The script runs as root on the Hive, so it is fixed text: the only values put
into it are two timestamps this module formats from datetimes it parsed, and the run's
session id, which must match ``SESSION_ID_RE`` (no leading ``-``, so it can never read
as an option, and no shell character). Nothing a caller sends reaches it: a caller gives
a dispatch id, and everything else comes from the Host's own records. Only the SwarmAI
account's ``manage`` session is used (hive.accounts.session_for); a Hive in another
account answers ``not_reachable`` without SSM. The text that comes back is Hive-written
and untrusted (the CLI frames it as data).

Output. SSM returns at most 24,000 characters of standard output, so the script keeps
each section within its own byte budget (the whole under 20 KB): a section keeps its
newest WHOLE lines and says how many it left out. Sections, in order: ``state`` (the
service's memory settings and restarts, ``free``, ``uptime``), ``systemd`` (systemd's own
lines for the unit: failures, stops, starts), ``kernel`` (OOM killer lines), ``service``
(the daemon's memory samples and warnings, ``disconnect_all``, startup) and ``session``
(the daemon's lines naming this run's session, without uvicorn access lines, which name
it on every records poll). ``findings`` is a deterministic list built from them, no
model: ``oom_kill``, ``service_stopped`` (one per stop), ``session_error``, or
``none_seen``. A repeat within ``CACHE_S`` returns the kept answer without SSM.

Public: ``read``, ``script``, ``findings``, ``parse``, ``SESSION_ID_RE``.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: a session id as the Hive writes it, safe to put in a root shell script: starts with a
#: letter or digit (never an option), then only letters, digits, ``_`` and ``-``
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}")
CACHE_S = 60
#: each section's byte budget on the Hive (newest whole lines within it); with the
#: section headers the whole stays under 20 KB, below SSM's 24,000-character output limit
BUDGETS = (("state", 2000), ("systemd", 3000), ("kernel", 3000), ("service", 4000), ("session", 7000))
MAX_OUTPUT = 20_000
#: the window around the run the journals are read for
PAD = timedelta(minutes=5)
SSM_TIMEOUT_S = 60
POLL_S = 2

#: reads running at once (each is a root command on a Hive and a worker thread here for
#: up to about 90 s); above it a read answers ``busy``
MAX_READS = 2

_cache: dict[str, tuple[float, dict]] = {}
#: the read running for a dispatch id: a second caller waits for it instead of sending
#: its own command (one event loop: the daemon's)
_inflight: dict[str, "asyncio.Future"] = {}


class NotReachable(Exception):
    """The Hive cannot be read now; the message says why."""


def _ts(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# The script, as pinned text. ``{since}`` / ``{until}`` / ``{sid}`` are the only holes;
# ``script`` fills them after checking each. A section prints ``@@ <name> <total> <kept>``
# then its kept lines, newest last.
_TEMPLATE = r"""set +u
export LC_ALL=C
S='{since} UTC'
U='{until} UTC'
SID='{sid}'
T=$(mktemp -d) || exit 1
trap 'rm -rf "$T"' EXIT
{{ systemctl show swarmai-hive -p MemoryMax,MemoryCurrent,OOMPolicy,NRestarts,ActiveEnterTimestamp,Result; free -m; uptime; }} > "$T/state" 2>&1
journalctl -u swarmai-hive _PID=1 --since "$S" --until "$U" --no-pager -o short-iso > "$T/systemd" 2>/dev/null
journalctl -k --since "$S" --until "$U" --no-pager -o short-iso 2>/dev/null | grep -iE 'oom-kill|killed process|out of memory' > "$T/kernel"
journalctl -u swarmai-hive --since "$S" --until "$U" --no-pager -o short-iso 2>/dev/null > "$T/all"
grep -E 'lifecycle_manager\.memory_(sample|warning)|disconnect_all|Application startup complete|Started server process' "$T/all" > "$T/service"
grep -F -- "$SID" "$T/all" | grep -vE 'HTTP/1\.[01]" [0-9]{{3}}|uvicorn\.access' > "$T/session"
for pair in {budgets}; do
  n=${{pair%%:*}}; b=${{pair##*:}}
  total=$(( $(wc -l < "$T/$n") ))
  tac "$T/$n" | awk -v b="$b" '{{ c += length($0) + 1; if (c > b) exit; print }}' | tac > "$T/$n.kept"
  echo "@@ $n $total $(( $(wc -l < "$T/$n.kept") ))"
  cat "$T/$n.kept"
done
"""


def script(session_id: str, since: datetime, until: datetime) -> str:
    """The script for one run, or ValueError before anything is sent."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("not a session id this read can use")
    if not (isinstance(since, datetime) and isinstance(until, datetime)) or since >= until:
        raise ValueError("the window must be two datetimes, the first earlier")
    budgets = " ".join(f"{n}:{b}" for n, b in BUDGETS)
    return _TEMPLATE.format(since=_ts(since), until=_ts(until), sid=session_id, budgets=budgets)


_HEAD_RE = re.compile(r"@@ (\w+)\s+(\d+)\s+(\d+)")


def parse(output: str) -> dict[str, dict]:
    """The sections of the script's output: ``{name: {"lines", "total", "omitted"}}``."""
    sections: dict[str, dict] = {}
    cur: Optional[dict] = None
    for line in (output or "").splitlines():
        m = _HEAD_RE.fullmatch(line.strip())
        if m and m.group(1) in dict(BUDGETS):
            total, kept = int(m.group(2)), int(m.group(3))
            cur = sections[m.group(1)] = {"lines": [], "total": total, "omitted": max(0, total - kept)}
        elif cur is not None:
            cur["lines"].append(line)
    return sections


_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_KILLED_RE = re.compile(r"Killed process (\d+) \(([^)]*)\).*?anon-rss:(\d+)kB")
_MEMCG_RE = re.compile(r"oom_memcg=([^,\s]+)")
_FAILED_RE = re.compile(r"Failed with result '([^']+)'")
_STOPPED_RE = re.compile(r"\bStopped\b")
_ERROR_RE = re.compile(r"\b(?:ERROR|CRITICAL)\b|stream_error|exit code \d+")


def _time(line: str) -> Optional[str]:
    m = _TIME_RE.match(line)
    return m.group(1) if m else None


def _seconds(at: Optional[str]) -> Optional[float]:
    try:
        return datetime.strptime(at or "", "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return None


def findings(sections: dict[str, dict]) -> list[dict]:
    """What the sections show, deterministically (the design's §2 list)."""
    out: list[dict] = []
    memcg = None
    for line in sections.get("kernel", {}).get("lines", []):
        m = _MEMCG_RE.search(line)
        if m:
            memcg = m.group(1)
        k = _KILLED_RE.search(line)
        if k:
            out.append({"kind": "oom_kill", "at": _time(line), "pid": int(k.group(1)), "victim": k.group(2),
                        "rss_mb": round(int(k.group(3)) / 1024), "memcg": memcg})
    failed_at: list[float] = []
    for line in sections.get("systemd", {}).get("lines", []):
        f = _FAILED_RE.search(line)
        at = _time(line)
        if f:
            out.append({"kind": "service_stopped", "at": at, "result": f.group(1)})
            s = _seconds(at)
            if s is not None:
                failed_at.append(s)
        elif _STOPPED_RE.search(line):
            s = _seconds(at)
            # the "Stopped" systemd writes after a failure belongs to that failure
            if s is None or not any(0 <= s - t <= 60 for t in failed_at):
                out.append({"kind": "service_stopped", "at": at, "result": "stopped"})
    errors = [line for line in sections.get("session", {}).get("lines", []) if _ERROR_RE.search(line)]
    if errors:
        out.append({"kind": "session_error", "count": len(errors), "last": errors[-5:]})
    return out or [{"kind": "none_seen"}]


async def _target(dispatch_id: str) -> dict[str, Any]:
    """Where and what to read for a run, from the Host's own records; NotReachable when
    its Hive cannot be read now."""
    from core import swarm_out, swarm_out_results
    from database import db
    from hive import accounts
    from hive.provisioner import HiveProvisioner
    row = await asyncio.to_thread(swarm_out._log_row_of, dispatch_id)
    sid = row.get("session_id")
    if not sid:
        raise NotReachable("no_session: the Hive never named this run's session")
    if not SESSION_ID_RE.fullmatch(str(sid)):
        raise ValueError("not a session id this read can use")
    if not row.get("hive_id"):
        raise NotReachable("hive_gone: the run's record names no Hive")
    p = HiveProvisioner(db.db_path)
    try:
        inst = await p._get_instance(row["hive_id"])
    except ValueError:
        raise NotReachable("hive_gone: the Hive is no longer in this Host's store") from None
    if inst.get("status") != "running":
        raise NotReachable(f"hive_not_running: the Hive is {inst.get('status')}")
    if not inst.get("ec2_instance_id") or not inst.get("region"):
        raise NotReachable("hive_incomplete: the Hive's record has no machine to read")
    try:
        acct = await p._get_account(inst["account_ref"])
    except ValueError:
        raise NotReachable("no_account: the Hive's account is not in this Host's store") from None
    try:
        session = await asyncio.to_thread(accounts.session_for, acct, inst["region"], "manage")
    except accounts.ACCOUNT_ERRORS as e:
        raise NotReachable(f"other_account: {str(e)[:200]}") from None
    from core.swarm_out_settle import _final
    kept = await asyncio.to_thread(swarm_out_results.load, dispatch_id) or {}
    res = kept.get("result") if isinstance(kept.get("result"), dict) else {}
    recs = kept.get("records") if isinstance(kept.get("records"), dict) else {}
    times = [t for t in (_parse(row.get("sent_at")), _parse(res.get("ended_at")), _parse(recs.get("last_at"))) if t]
    now = datetime.now(timezone.utc)
    since = (_parse(row.get("sent_at")) or now - timedelta(hours=1)) - PAD
    # a run whose result is not final (still running, cut off, its Hive silent) may have
    # died after its last record: read up to now. A final result's run: up to its last
    # activity, so later runs on the same Hive do not crowd its lines out of the budgets
    until = now if not _final(res or None) else min(now, max(times or [now]) + PAD)
    if until <= since:
        until = now
    return {"hive": row.get("hive") or inst.get("name"), "region": inst["region"],
            "ec2_id": inst["ec2_instance_id"], "session": session, "session_id": sid,
            "since": since, "until": until}


def _parse(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _ssm_run(session: Any, region: str, ec2_id: str, text: str) -> str:
    """Send the script and wait for its output. Bounded twice: the script stops on the
    Hive after ``SSM_TIMEOUT_S`` (``executionTimeout``; ``TimeoutSeconds`` is only how
    long SSM tries to deliver it), and this stops polling a little after that. Raises
    NotReachable when SSM refuses, fails or times out."""
    ssm = session.client("ssm", region_name=region)
    try:
        cid = ssm.send_command(InstanceIds=[ec2_id], DocumentName="AWS-RunShellScript",
                               Parameters={"commands": [text], "executionTimeout": [str(SSM_TIMEOUT_S)]},
                               TimeoutSeconds=SSM_TIMEOUT_S,
                               Comment="SwarmAI read-only diagnostics")["Command"]["CommandId"]
    except Exception as e:  # noqa: BLE001 — boto raises many kinds; the reason is told
        raise NotReachable(f"ssm_failed: {type(e).__name__}") from None
    deadline = time.monotonic() + SSM_TIMEOUT_S + 30
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        try:
            r = ssm.get_command_invocation(CommandId=cid, InstanceId=ec2_id)
        except Exception as e:  # noqa: BLE001 — the invocation may not exist for a moment
            if type(e).__name__ == "InvocationDoesNotExist":
                continue
            raise NotReachable(f"ssm_failed: {type(e).__name__}") from None
        if r.get("Status") == "Success":
            return r.get("StandardOutputContent") or ""
        if r.get("Status") in ("Failed", "Cancelled", "TimedOut"):
            raise NotReachable(f"ssm_failed: the command {r.get('Status')}")
    raise NotReachable("ssm_failed: no answer within the time")


async def read(dispatch_id: str) -> dict[str, Any]:
    """Why a run's Hive session stopped, from its Hive. ``reachable`` False with a
    ``reason`` when it cannot be read now (``busy`` when MAX_READS already run);
    ValueError for a session id this read cannot use (nothing is sent). Callers of the
    same run while its read runs share that read."""
    hit = _cache.get(dispatch_id)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return {**hit[1], "cached": True}
    running = _inflight.get(dispatch_id)
    if running is not None:
        return {**(await asyncio.shield(running)), "cached": True}
    if len(_inflight) >= MAX_READS:
        return {"dispatch_id": dispatch_id, "reachable": False,
                "reason": f"busy: {MAX_READS} reads already run; try again in a minute"}
    fut = asyncio.get_running_loop().create_future()
    _inflight[dispatch_id] = fut
    try:
        out = await _read(dispatch_id)
    except BaseException as e:
        fut.set_exception(e)
        fut.exception()                     # retrieved: no "never retrieved" warning
        raise
    else:
        fut.set_result(out)
        return out
    finally:
        _inflight.pop(dispatch_id, None)


async def _read(dispatch_id: str) -> dict[str, Any]:
    try:
        t = await _target(dispatch_id)
        text = script(t["session_id"], t["since"], t["until"])
        output = await asyncio.to_thread(_ssm_run, t["session"], t["region"], t["ec2_id"], text)
    except NotReachable as e:
        return {"dispatch_id": dispatch_id, "reachable": False, "reason": str(e)}
    sections = parse(output[:MAX_OUTPUT + 4000])
    state = sections.pop("state", {"lines": [], "omitted": 0})
    out = {"dispatch_id": dispatch_id, "reachable": True, "hive": t["hive"], "session_id": t["session_id"],
           "window": {"since": t["since"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "until": t["until"].strftime("%Y-%m-%dT%H:%M:%SZ")},
           "state": state["lines"], "sections": sections,
           "findings": findings(sections), "hive_written": True, "cached": False}
    _cache[dispatch_id] = (time.monotonic(), out)
    return out
