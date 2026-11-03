"""``pipeline-v1 child …``: a lead run's commands on its Swarm Run children (design
docs/design/2026-10-31-swarm-run.md §5; impl docs/design/2026-11-03-swarm-run-impl.md
step 4).

Every command is the run owner's (``cli_commands._not_owner``, checked first). The
children are ordinary Swarm Out dispatches: this module only adds what is the run's
(its plan, which dispatch carries which work package) and calls the daemon's same routes
with ``by: agent`` and this session, so every rule there holds unchanged (the actor
checks, the funnel, the budgets). It holds the run's operation lock only for the short
writes of the run's own log and worktree, never while a Hive is being waited on (a
dispatch takes minutes).

- ``plan --file F``       the work packages (``children.json`` in the run directory)
- ``dispatch W… [--again W --note-file F]``  dispatch packages, several at once
- ``status``              the children's rows (Host fields) and the packages not sent yet
- ``wait [--since T]``    ``watch`` on the children: returns when one changes
- ``accept|rerun|stop|discard <dispatch>``  the lead run's commands on a child
- ``card|answer|escalate <dispatch> <request>``  a child's wait
- ``merge <dispatch>…`` / ``apply``  take children's commits into the run (child_merge)

Text a Hive wrote reaches the agent only framed as data (``card``), never in ``status``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from core.run_protocol.engine import EngineError, RunEngine

PLAN_FILE = "children.json"
MAX_CHILDREN = 20
MAX_TASK_CHARS = 20000
DISPATCH_TIMEOUT_S = 540
WATCH_TIMEOUT_S = 570
_WP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
_DID_RE = re.compile(r"so_[0-9a-f]{12}")
_PLAN_KEYS = {"wp", "title", "brief", "task", "acceptance", "execution", "why", "returns", "mode",
              "base", "after"}


class ChildError(ValueError):
    """A child command that cannot run as given; the message says why."""


# ── the daemon ──────────────────────────────────────────────────────────────

def _session() -> Optional[str]:
    from core.run_protocol.request_ctx import current
    return current().session_id or None


def _http(method: str, path: str, body: Optional[dict] = None, timeout: float = 120) -> dict[str, Any]:
    """One call to the local daemon's Swarm Out API; a refusal comes back as
    ``{"ok": False, "status", "detail"}`` (never raised)."""
    port = os.environ.get("SWARMAI_PORT", "18321")
    port = port if port.isdigit() else "18321"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/swarm-out{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
            return out if isinstance(out, dict) else {"ok": True, "value": out}
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail")
        except Exception:  # noqa: BLE001 — the status is enough
            detail = None
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)
        return {"ok": False, "status": e.code, "detail": detail,
                **({"maybe_sent": True} if e.code == 504 and path == "/dispatch" else {})}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "status": None,
                "detail": f"the SwarmAI daemon did not answer ({type(getattr(e, 'reason', e)).__name__})",
                **({"maybe_sent": True} if path == "/dispatch" else {})}


def _framed(data: Any) -> str:
    from scripts.swarm_out import _framed as framed
    return framed(data)


# ── the plan ────────────────────────────────────────────────────────────────

def _plan_path(engine: RunEngine) -> Path:
    return engine.run_dir / PLAN_FILE


def read_plan(engine: RunEngine) -> list[dict[str, Any]]:
    try:
        data = json.loads(_plan_path(engine).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("children") if isinstance(data, dict) else None
    return [c for c in items or [] if isinstance(c, dict)]


def _clean_plan(raw: Any) -> list[dict[str, Any]]:
    items = raw.get("children") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ChildError('the plan is {"children": [{"wp", "title", "task", "acceptance", "execution", '
                         '"returns", ...}, ...]}')
    if len(items) > MAX_CHILDREN:
        raise ChildError(f"at most {MAX_CHILDREN} work packages in one Swarm Run")
    out, seen = [], set()
    for i, c in enumerate(items):
        if not isinstance(c, dict):
            raise ChildError(f"work package {i + 1} is not an object")
        extra = set(c) - _PLAN_KEYS
        if extra:
            raise ChildError(f"work package {i + 1}: unknown field(s) {', '.join(sorted(extra))}")
        wp = str(c.get("wp") or "")
        if not _WP_RE.fullmatch(wp) or wp in seen:
            raise ChildError(f"work package {i + 1}: wp must be a unique id (letters, digits, - or _)")
        seen.add(wp)
        for key in ("title", "task", "acceptance", "execution", "returns"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                raise ChildError(f"{wp}: {key} is required (one complete text)")
        if len(c["task"]) > MAX_TASK_CHARS:
            raise ChildError(f"{wp}: the task is longer than {MAX_TASK_CHARS} characters")
        after = c.get("after") or []
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
            raise ChildError(f"{wp}: after is a list of work package ids")
        base = c.get("base")
        if base is not None and not (isinstance(base, list) and all(isinstance(b, str) for b in base)):
            raise ChildError(f"{wp}: base is a list like [\"<ref>\"] or [\"<repo>=<ref>\"]")
        out.append({k: c[k] for k in _PLAN_KEYS if k in c})
    ids = {c["wp"] for c in out}
    for c in out:
        missing = [a for a in c.get("after") or [] if a not in ids]
        if missing:
            raise ChildError(f"{c['wp']}: after names {', '.join(missing)}, not in the plan")
    return out


#: what a work package that went out keeps when the plan is written again: the work it
#: was given (a renamed or rewritten package would be a new one under an old count)
_SENT_KEYS = ("task", "acceptance", "execution", "returns", "mode", "base")


def _sent_wps(engine: RunEngine) -> set[str]:
    """Work packages that went out: the run's log, and Swarm Out's rows when the daemon
    answers (a dispatch whose event was not written is still in its log)."""
    sent = {str(c.get("wp")) for c in engine.children_recorded() if c.get("wp")}
    rows, err = _rows(engine.run_id)
    if err is None:
        sent |= {str(r.get("wp")) for r in rows if r.get("wp")}
    return sent


def write_plan(engine: RunEngine, raw: Any) -> list[dict[str, Any]]:
    """Check *raw* whole and keep it as the run's plan. A package that went out stays in
    it as it was sent (see _SENT_KEYS); titles, briefs, ``why`` and ``after`` may change,
    and packages not sent may be added, changed or removed."""
    plan = _clean_plan(raw)
    old = {c["wp"]: c for c in read_plan(engine)}
    new = {c["wp"]: c for c in plan}
    for wp in sorted(_sent_wps(engine) & set(old)):
        if wp not in new:
            raise ChildError(f"{wp} went out already: it stays in the plan")
        changed = [k for k in _SENT_KEYS if old[wp].get(k) != new[wp].get(k)]
        if changed:
            raise ChildError(f"{wp} went out already: its {', '.join(changed)} stay as sent "
                             "(a correction goes with `child dispatch --again <wp> --note-file F`)")
    path = _plan_path(engine)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"children": plan}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return plan


# ── the children as Swarm Out sees them ─────────────────────────────────────

def _rows(run_id: str) -> tuple[list[dict], Optional[str]]:
    out = _http("GET", "/runs")
    if out.get("ok") is False:
        return [], str(out.get("detail") or "the daemon did not list the runs")
    return [r for r in out.get("runs") or [] if isinstance(r, dict) and r.get("parent_run") == run_id], None


_ROW_KEYS = ("dispatch_id", "wp", "title", "hive", "state", "state_reason", "stage", "why", "sent_at")


def _row_view(r: dict) -> dict[str, Any]:
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    out = {k: r.get(k) for k in _ROW_KEYS}
    out["commands"] = [a.get("key") + (":" + a["variant"] if a.get("variant") else "")
                       for a in r.get("lead_actions") or [] if isinstance(a, dict) and a.get("key")]
    out["result"] = {k: res.get(k) for k in ("ended", "ended_at", "provisional")} if res else None
    out["waits"] = [{k: w.get(k) for k in ("request_id", "kind", "who", "phase", "approval_class")}
                    for w in r.get("waits") or [] if isinstance(w, dict)]
    out["settle"] = {k: (r.get("settle") or {}).get(k) for k in ("code", "why_not")}
    return out


def _own(run_id: str, dispatch_id: str) -> dict:
    if not _DID_RE.fullmatch(dispatch_id or ""):
        raise ChildError("name a child by its dispatch id (so_…), as `child status` lists it")
    rows, err = _rows(run_id)
    if err:
        raise ChildError(err)
    row = next((r for r in rows if r.get("dispatch_id") == dispatch_id), None)
    if row is None:
        raise ChildError(f"{dispatch_id} is not a child of {run_id}")
    return row


def status(engine: RunEngine) -> dict[str, Any]:
    rows, err = _rows(engine.run_id)
    plan = read_plan(engine)
    sent = {r.get("wp") for r in rows}
    out: dict[str, Any] = {"ok": err is None, "children": [_row_view(r) for r in rows],
                           "not_dispatched": [{"wp": c["wp"], "title": c["title"], "after": c.get("after") or []}
                                              for c in plan if c["wp"] not in sent]}
    if err:
        out["detail"] = err
    return out


# ── dispatch ────────────────────────────────────────────────────────────────

WORKTREE_BASE = "@worktree"
_BASE_REF_RE = re.compile(r"/b(\d+)-[^/]*$")


def _next_base(wt: Path, run_id: str) -> int:
    """One more than the highest ``b<n>-`` base ref of this run in *wt*'s repository."""
    from core import run_worktree as rw
    refs = rw._out(wt, "for-each-ref", "--format=%(refname)", f"{rw.SNAPSHOT_REF_PREFIX}{run_id}/")
    return 1 + max((int(m.group(1)) for m in map(_BASE_REF_RE.search, refs.split()) if m), default=0)


def worktree_base(engine: RunEngine) -> list[str]:
    """``base`` for a child that starts from this run's worktree as it is now (a contract
    the lead run wrote first: design §7 point 1): one commit per repository of the
    change, untracked new files included, parent the run's base, at a new ref
    ``refs/swarm/snapshots/<run>/b<n>-<repo>`` each time: a child dispatched from an
    earlier one keeps its start (its commits are taken back over it, core/hive_return),
    and the run's snapshot refs stay until the run's worktree goes."""
    from core import run_worktree as rw
    from core.run_protocol import child_merge
    from core.run_protocol.oplock import operation
    from config import get_app_data_dir
    repos = engine.effective_repos()
    if not repos:
        raise ChildError("this run has no worktree yet: a child can start from it in a code stage")
    out = []
    with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", engine.run_id):
        for name, path in repos.items():
            wt = Path(path)
            commit = child_merge._commit(wt, child_merge._worktree_tree(wt, engine.run_id),
                                         rw._rev(wt, "HEAD"), f"swarm run {engine.run_id}: a child's base")
            ref = rw.snapshot_ref(engine.run_id, child_merge._label(f"b{_next_base(wt, engine.run_id)}", name))
            # a new name each time (under the run's lock, so two calls never pick one)
            rw._out(wt, "update-ref", ref, commit)
            out.append(ref if len(repos) == 1 else f"{name}={ref}")
    return out


def _send_one(engine: RunEngine, item: dict, session: Optional[str], note: str = "",
              again_of: str = "") -> dict[str, Any]:
    task = item["task"]
    if note:
        task += ("\n\n## Correction after the previous attempt\n\n"
                 "The previous attempt at this work package did not meet the acceptance. "
                 "Do the task again with this in mind:\n\n" + note.strip())
    body = {"brain": engine.project, "task": task, "acceptance": item["acceptance"],
            "mode": item.get("mode"), "execution": item["execution"], "why": item.get("why") or "",
            "returns": item["returns"], **({"base": item["base"]} if item.get("base") else {})}
    spec = _http("POST", "/spec", {**body, "parent_run": engine.run_id})
    if spec.get("ok") is False:
        return {"wp": item["wp"], "ok": False, "detail": spec.get("detail")}
    if spec.get("refusals"):
        return {"wp": item["wp"], "ok": False, "refused": True, "detail": "; ".join(spec["refusals"])}
    revision = (spec.get("spec") or {}).get("revision")
    row = _http("POST", "/dispatch", {**body, "title": f"{item['wp']} · {item['title']}"[:120],
                                       "brief": item.get("brief") or "", "revision": revision,
                                       "session": session, "parent_run": engine.run_id, "wp": item["wp"],
                                       **({"again": True} if again_of else {})},
                timeout=DISPATCH_TIMEOUT_S)
    if row.get("ok") is False:
        return {"wp": item["wp"], "ok": False, "detail": row.get("detail"),
                **({"maybe_sent": True} if row.get("maybe_sent") else {})}
    return {"wp": item["wp"], "ok": True, "dispatch_id": row.get("dispatch_id"), "hive": row.get("hive"),
            **({"again_of": again_of} if again_of else {}),
            **({"frozen": row["frozen"]} if isinstance(row.get("frozen"), dict) else {})}


def dispatch(engine: RunEngine, wps: list[str], *, again: str = "", note: str = "") -> dict[str, Any]:
    plan = {c["wp"]: c for c in read_plan(engine)}
    if not plan:
        raise ChildError("write the plan first: `pipeline-v1 child plan --file -`")
    wps = list(dict.fromkeys(wps))          # a package named twice goes out once
    if again:
        wps = [again]
        if not note.strip():
            raise ChildError("--again needs --note-file: what the next attempt must do differently")
    unknown = [w for w in wps if w not in plan]
    if not wps or unknown:
        raise ChildError(f"name work packages of the plan ({', '.join(plan)}); not in it: {', '.join(unknown)}")
    rows, err = _rows(engine.run_id)
    if err:
        raise ChildError(err)
    sent_wps = {r.get("wp") for r in rows}
    open_wps = {r.get("wp") for r in rows if r.get("stage") not in ("accepted", "closed")}
    if not again:
        already = [w for w in wps if w in sent_wps]
        if already:
            raise ChildError(f"{', '.join(already)} went out already: correct one with `child rerun` "
                             "(native) or `child dispatch --again <wp> --note-file F`")
    elif again in open_wps:
        raise ChildError(f"{again} still has an open child: discard or take it first")
    previous = next((r.get("dispatch_id") for r in reversed(rows) if r.get("wp") == again), "") if again else ""
    session = _session()            # read here: the request context does not reach the threads
    if any(plan[w].get("base") == [WORKTREE_BASE] for w in wps):
        base = worktree_base(engine)    # once, for every child of this call that asks for it
        plan = {w: ({**c, "base": base} if c.get("base") == [WORKTREE_BASE] else c) for w, c in plan.items()}
    with ThreadPoolExecutor(max_workers=min(4, len(wps))) as pool:
        results = list(pool.map(lambda w: _send_one(engine, plan[w], session, note, previous), wps))
    frozen = next((r.pop("frozen") for r in results if isinstance(r.get("frozen"), dict)), None)
    for r in results:
        r.pop("frozen", None)
    if frozen and engine.frozen() is None:
        try:                       # what the children share, for REPORT (the daemon keeps the bytes)
            engine.record_frozen(str(frozen.get("brain") or ""), str(frozen.get("digest") or ""),
                                 str(frozen.get("revision") or ""), str(frozen.get("model") or ""))
        except Exception:  # noqa: BLE001 — a duplicate or a later record is not the dispatch's failure
            pass
    for r in results:
        if r.get("ok") and r.get("dispatch_id"):
            try:
                engine.record_child(r["dispatch_id"], r["wp"], hive=str(r.get("hive") or ""),
                                    again_of=str(r.get("again_of") or ""))
            except Exception as exc:  # noqa: BLE001 — Swarm Out has the row; the log is the run's note
                r["record_error"] = type(exc).__name__
    return {"ok": all(r.get("ok") for r in results), "dispatched": results}


# ── the lead run's commands on a child ──────────────────────────────────────

def command(engine: RunEngine, verb: str, dispatch_id: str, why: str, *, ended_at: str = "",
            text: str = "") -> dict[str, Any]:
    if not (why or "").strip():
        raise ChildError("say why (--why): it is kept on the child's record")
    _own(engine.run_id, dispatch_id)
    actor = {"by": "agent", "session": _session(), "why": why}
    if verb == "accept":
        if not ended_at:
            raise ChildError("--ended-at: the result you took, as `child status` shows it")
        return _http("POST", f"/runs/{dispatch_id}/verdict", {**actor, "value": "pass", "ended_at": ended_at})
    if verb == "rerun":
        if not text.strip():
            raise ChildError("--text-file: the correction the child gets")
        return _http("POST", f"/runs/{dispatch_id}/rerun", {**actor, "text": text})
    if verb == "stop":
        return _http("POST", f"/runs/{dispatch_id}/stop", actor)
    if verb == "discard":
        return _http("POST", f"/runs/{dispatch_id}/discard", {**actor, "note": why})
    raise ChildError(f"unknown command {verb}")


def wait_card(engine: RunEngine, dispatch_id: str, request_id: str) -> dict[str, Any]:
    _own(engine.run_id, dispatch_id)
    out = _http("POST", f"/runs/{dispatch_id}/card", {"request_id": request_id})
    if out.get("ok") is False:
        return out
    return {"ok": True, "card": _framed(out)}


def answer(engine: RunEngine, dispatch_id: str, request_id: str, pick: str, why: str) -> dict[str, Any]:
    _own(engine.run_id, dispatch_id)
    if not pick or not why:
        raise ChildError("--pick (one of the card's option labels) and --why")
    return _http("POST", f"/runs/{dispatch_id}/waits/{request_id}/answer-host",
                 {"session": _session(), "pick": pick, "why": why})


def escalate(engine: RunEngine, dispatch_id: str, request_id: str, why: str) -> dict[str, Any]:
    _own(engine.run_id, dispatch_id)
    if not why:
        raise ChildError("--why: what the person must decide, and why you cannot")
    return _http("POST", f"/runs/{dispatch_id}/waits/{request_id}/escalate",
                 {"session": _session(), "why": why})


def wait(engine: RunEngine, since: str = "") -> dict[str, Any]:
    rows, err = _rows(engine.run_id)
    if err:
        raise ChildError(err)
    ids = [r["dispatch_id"] for r in rows if r.get("stage") not in ("accepted", "closed")][:20]
    if not ids:
        return {"ok": True, "changed": False, "detail": "no child is open"}
    return _http("POST", "/watch", {"ids": ids, "since": since or None, "session": _session()},
                 timeout=WATCH_TIMEOUT_S)


# ── argparse and the dispatch from cli_commands ─────────────────────────────

def add_parser(sub) -> None:
    import argparse
    c = sub.add_parser("child", help="a Swarm Run's children: work packages dispatched to Hives")
    # --project / --run-id before or after the child command (``next`` writes them after:
    # next_step._cmd); a later one wins, a missing one is refused (cli_commands)
    c.add_argument("--project", default=None)
    c.add_argument("--run-id", default=None)
    run_args = argparse.ArgumentParser(add_help=False)
    run_args.add_argument("--project", default=argparse.SUPPRESS)
    run_args.add_argument("--run-id", default=argparse.SUPPRESS)
    cs = c.add_subparsers(dest="child_command", required=True)
    _add = cs.add_parser
    cs.add_parser = lambda name, **kw: _add(name, parents=[run_args], **kw)   # type: ignore[method-assign]
    p = cs.add_parser("plan")
    p.add_argument("--file", required=True, help="the plan as JSON, - for stdin")
    d = cs.add_parser("dispatch")
    d.add_argument("wp", nargs="*")
    d.add_argument("--again", default="", help="dispatch this package again (a new child)")
    d.add_argument("--note-file", default="", help="with --again: the correction, - for stdin")
    cs.add_parser("status")
    w = cs.add_parser("wait")
    w.add_argument("--since", default="")
    for verb in ("accept", "rerun", "stop", "discard"):
        x = cs.add_parser(verb)
        x.add_argument("dispatch_id")
        x.add_argument("--why", required=True)
        if verb == "accept":
            x.add_argument("--ended-at", required=True)
        if verb == "rerun":
            x.add_argument("--text-file", required=True)
    for verb in ("card", "answer", "escalate"):
        x = cs.add_parser(verb)
        x.add_argument("dispatch_id")
        x.add_argument("request_id")
        if verb == "answer":
            x.add_argument("--pick", required=True)
        if verb in ("answer", "escalate"):
            x.add_argument("--why", required=True)
    m = cs.add_parser("merge")
    m.add_argument("dispatch_id", nargs="+")
    cs.add_parser("apply")


def _text(value: str) -> str:
    from core.run_protocol.request_ctx import current
    if value == "-":
        return current().stdin or ""
    try:
        return Path(value).read_text(encoding="utf-8")
    except OSError as exc:
        raise ChildError(f"cannot read {value}: {type(exc).__name__}") from None


def run(args, engine: RunEngine) -> dict[str, Any]:
    """The child command *args* on the loaded, owner-checked *engine*; a failure is
    ``{"ok": false, "error": …}`` whichever side refused (``result``)."""
    return result(_run(args, engine))


def _run(args, engine: RunEngine) -> dict[str, Any]:
    verb = args.child_command
    try:
        if verb == "plan":
            raw = _text(args.file)
            try:
                plan = write_plan(engine, json.loads(raw))
            except ValueError as exc:
                raise ChildError(str(exc) if isinstance(exc, ChildError) else "the plan is not JSON") from None
            return {"ok": True, "plan": [{"wp": c["wp"], "title": c["title"], "after": c.get("after") or []}
                                         for c in plan]}
        if verb == "dispatch":
            note = _text(args.note_file) if args.note_file else ""
            return dispatch(engine, list(args.wp), again=args.again, note=note)
        if verb == "status":
            return status(engine)
        if verb == "wait":
            return wait(engine, args.since)
        if verb in ("accept", "rerun", "stop", "discard"):
            text = _text(args.text_file) if verb == "rerun" else ""
            return command(engine, verb, args.dispatch_id, args.why,
                           ended_at=getattr(args, "ended_at", "") or "", text=text)
        if verb == "card":
            return wait_card(engine, args.dispatch_id, args.request_id)
        if verb == "answer":
            return answer(engine, args.dispatch_id, args.request_id, args.pick, args.why)
        if verb == "escalate":
            return escalate(engine, args.dispatch_id, args.request_id, args.why)
        if verb in ("merge", "apply"):
            from core.run_protocol import child_merge
            return child_merge.merge(engine, list(args.dispatch_id)) if verb == "merge" \
                else child_merge.apply(engine)
    except (ChildError, EngineError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"unknown child command {verb}"}


def result(out: dict[str, Any]) -> dict[str, Any]:
    """One failure shape for every child command: ``ok`` false carries ``error`` (a
    daemon refusal's ``detail`` becomes it)."""
    if isinstance(out, dict) and out.get("ok") is False and not out.get("error"):
        items = [x for k in ("merged", "applied", "dispatched") for x in out.get(k) or []
                 if isinstance(x, dict) and x.get("ok") is False]
        first = next((x.get("detail") for x in items if x.get("detail")), None)
        out = {**out, "error": str(out.get("detail") or (f"{len(items)} failed; the first: {first}" if first
                                                         else "the command failed"))}
    return out
