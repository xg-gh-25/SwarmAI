"""Project Agent command line: the agent confirms its own Brain's definition.

Design docs/design/2026-11-01-project-agent-owned-by-agent.md. The agent owns the
Project Agent: after it changes the definition or a skill it uses, and after a new
Brain's draft, it confirms the latest itself. The daemon confirms only when the
definition validates, every command's source is in the repository, and the authority
part (reserved actions, enforced gates, delivery) is no wider than what the person last
confirmed. When it is wider, ``card`` builds a question card for AskUserQuestion; the
person's pick is read by the daemon, which confirms as the person (there is no command
that confirms as the person).

    "$SWARM_BACKEND_BIN" py -m scripts.project_agent confirm --brain <B> --why "<one sentence>"
    "$SWARM_BACKEND_BIN" py -m scripts.project_agent card --brain <B>
    "$SWARM_BACKEND_BIN" py -m scripts.project_agent card-status --brain <B>

``confirm`` prints the daemon's ``status``: ``already`` / ``confirmed`` / ``problems``
(fix them, then confirm again) / ``widened`` (ask with ``card``, or change those back).
Every command names this session (``SWARM_APP_SESSION_ID``, which run_owner_gate exports
before every line naming this module). Exits 1 when the daemon refuses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import quote


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="project_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("confirm")
    c.add_argument("--brain", required=True)
    c.add_argument("--why", required=True, help="one sentence: why the agent confirms it now")
    for name in ("card", "card-status"):
        sub.add_parser(name).add_argument("--brain", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from core.knowledge_cli import RESTRICTED_ENV
    from scripts.swarm_out import _call, _session
    if os.environ.get(RESTRICTED_ENV):
        print(json.dumps({"ok": False, "detail": "the Project Agent is only the owner's"}))
        return 1
    path = f"/brains/{quote(args.brain, safe='')}/project-agent"
    if args.cmd == "confirm":
        return _call("POST", f"{path}/agent-confirm", {"session": _session(), "why": args.why}, api="ddd")
    if args.cmd == "card":
        return _call("POST", f"{path}/agent-card", {"session": _session()}, api="ddd")
    return _call("GET", f"{path}/agent-card", api="ddd")


if __name__ == "__main__":
    sys.exit(main())
