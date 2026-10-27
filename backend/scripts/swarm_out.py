"""Swarm Out command line: list Hives, dispatch a task to one, list runs.

Calls the local daemon's /api/swarm-out routes on loopback; Hive credentials stay
inside the daemon. Used by the Swarm Out chat agent after the person has agreed
to send a task out.

    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out hives
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --hive <name> --title <t> --task-file <f>
    "$SWARM_BACKEND_BIN" py -m scripts.swarm_out runs

There is deliberately no answer command: a Hive's questions and approvals are answered
by the person in the Swarm Out panel. An agent that reads Hive-written text (``runs``)
must not be one command away from approving what that text asks for.

Prints the daemon's JSON reply; exits 1 when the daemon refuses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


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
        print(json.dumps({"ok": False, "status": e.code, "detail": detail}, ensure_ascii=False))
        return 1
    except urllib.error.URLError:
        print(json.dumps({"ok": False, "detail": "the SwarmAI daemon is not reachable"}))
        return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swarm_out")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dispatch")
    d.add_argument("--hive", required=True)
    d.add_argument("--title", required=True)
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--task-file")
    g.add_argument("--task")
    sub.add_parser("runs")
    sub.add_parser("hives")
    args = p.parse_args(argv)

    if args.cmd == "dispatch":
        task = args.task if args.task is not None else open(args.task_file, encoding="utf-8").read()
        return _call("POST", "/dispatch", {"hive": args.hive, "title": args.title, "task": task})
    if args.cmd == "hives":
        return _call("GET", "/hives")
    return _call("GET", "/runs")


if __name__ == "__main__":
    sys.exit(main())
