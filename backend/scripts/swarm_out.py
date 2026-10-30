"""Swarm Out command line: list Hives, dispatch a task to one, list runs.

Calls the local daemon's /api/swarm-out routes on loopback; Hive credentials stay
inside the daemon. Used by the agent of the person's own session (desktop, or their own
channel DM): a non-owner channel session is refused (``knowledge_cli.RESTRICTED_ENV``,
and the Bash permission handler denies the command there too). Without ``--hive`` the
daemon picks the least busy running Hive. A dispatch's result is shown as a card the
backend builds from the dispatch log (core/swarm_out_card).

    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out hives
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out route --task-file <f> [--brain <B>]
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out spec --brain <B> --task-file <f> --acceptance <a> --execution native|pipeline:<profile> --why <one sentence> [--mode <m>]
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title <t> --brief <one or two sentences> --brain <B> --task-file <f> --acceptance <a> --execution native|pipeline:<profile> --why <one sentence> --revision <r> [--hive <name>] [--mode <m>]
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out runs

Every task goes under a Brain's Project Agent (``General`` = the Runner Agent, for a
task that belongs to no Brain); its repositories come from the definition. How it runs
(``--execution``) is chosen here for this request, not by the Project Agent.

There is deliberately no answer command: a Hive's questions and approvals are answered
by the person in the Swarm Out panel. An agent that reads Hive-written text (``runs``)
must not be one command away from approving what that text asks for.

Prints the daemon's JSON reply; exits 1 when the daemon refuses. A dispatch that may
have reached the Hive without an answer prints ``"maybe_sent": true``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DISPATCH_TIMEOUT_S = 300


def _base() -> str:
    port = os.environ.get("SWARMAI_PORT", "18321")
    if not port.isdigit():
        port = "18321"
    return f"http://127.0.0.1:{port}/api/swarm-out"


def _call(method: str, path: str, body: dict | None = None, timeout: int = 120) -> int:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_base() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            print(json.dumps(json.load(r), ensure_ascii=False, indent=2))
            return 0
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("detail")
        except Exception:
            detail = None
        out = {"ok": False, "status": e.code, "detail": detail}
        if e.code == 504 and path == "/dispatch":
            out["maybe_sent"] = True   # the Hive may have taken it (core/swarm_out_card)
        print(json.dumps(out, ensure_ascii=False))
        return 1
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            return _timed_out(path)
        print(json.dumps({"ok": False, "detail": "the SwarmAI daemon is not reachable"}))
        return 1
    except TimeoutError:
        return _timed_out(path)


def _timed_out(path: str) -> int:
    if path == "/dispatch":
        print(json.dumps({"ok": False, "maybe_sent": True, "detail": (
            "no answer in time; the task may be running on the Hive. Check the Hive's own "
            "page (or `runs`) before sending it again")}))
    else:
        print(json.dumps({"ok": False, "detail": "no answer from the SwarmAI daemon in time"}))
    return 1


def _task_args(p: argparse.ArgumentParser, brain_required: bool) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--task-file")
    g.add_argument("--task")
    p.add_argument("--brain", required=brain_required)


def _task(args) -> str:
    return args.task if args.task is not None else open(args.task_file, encoding="utf-8").read()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swarm_out")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route")
    _task_args(r, brain_required=False)
    r.add_argument("--file", help="a file the task is about (its path decides the Brain)")
    r.add_argument("--bound-project", help="the project whose bound repository this session works in")
    for name in ("spec", "dispatch"):
        d = sub.add_parser(name)
        if name == "dispatch":
            d.add_argument("--hive", help="a Hive by name; left out, the daemon picks one")
            d.add_argument("--title", required=True)
            d.add_argument("--brief", default="", help="one or two sentences of what the task is")
        _task_args(d, brain_required=True)
        d.add_argument("--acceptance", required=True)
        d.add_argument("--mode")
        d.add_argument("--execution", required=True, help="native, or pipeline:<profile>")
        d.add_argument("--why", default="", help="one sentence: why this execution")
        if name == "dispatch":
            d.add_argument("--revision", required=True, help="the revision `spec` showed")
    sub.add_parser("runs")
    sub.add_parser("hives")
    args = p.parse_args(argv)
    from core.knowledge_cli import RESTRICTED_ENV
    if os.environ.get(RESTRICTED_ENV):
        # a non-owner channel session: Swarm Out is the owner's (their Hives, their code)
        print(json.dumps({"ok": False, "detail": "Swarm Out is only available to the owner"}))
        return 1

    if args.cmd == "route":
        return _call("POST", "/route", {"text": _task(args), "brain": args.brain,
                                        "editor_file_path": args.file,
                                        "session_bound_project": args.bound_project})
    if args.cmd in ("spec", "dispatch"):
        body = {"brain": args.brain, "task": _task(args), "acceptance": args.acceptance,
                "mode": args.mode, "execution": args.execution, "why": args.why}
        if args.cmd == "spec":
            return _call("POST", "/spec", body)
        # a dispatch may sync the Brain first (up to 2 minutes) and then waits for the
        # Hive to name the session: wait longer than the daemon can take
        return _call("POST", "/dispatch", {**body, "hive": args.hive, "title": args.title,
                                           "brief": args.brief, "revision": args.revision},
                     timeout=DISPATCH_TIMEOUT_S)
    if args.cmd == "hives":
        return _call("GET", "/hives")
    return _call("GET", "/runs")


if __name__ == "__main__":
    sys.exit(main())
