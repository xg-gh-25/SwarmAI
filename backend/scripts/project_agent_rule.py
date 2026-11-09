"""project_agent_rule — add, remove or list a Brain's rules (design
docs/design/2026-11-09-project-agent-contract.md §4.3).

    "$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule add --brain <B> --said "<your words>"
    "$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule remove --brain <B> --said "<the rule>"
    "$SWARM_BACKEND_BIN" py -m scripts.project_agent_rule list --brain <B>

A rule is a standing requirement the person gave for one Brain, kept as their words.
``add`` accepts only words that occur verbatim in the person's message of this turn
(desktop chat), as one whole clause or several, and records the verification
(core.project_agent_rules); ``remove`` takes a rule out, also only in a turn the person
opened; ``list`` shows the verified rules and the ones that are not. The session is
``SWARM_APP_SESSION_ID``: the run owner gate puts this session's id in front of the call
and refuses one that sets the variable itself or comes from a sub-agent.
Output: one JSON object on stdout; exit 0, or 2 when refused (``detail`` says why).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="project_agent_rule", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("--brain", required=True)
        p.add_argument("--said", required=True)
    sub.add_parser("list").add_argument("--brain", required=True)
    a = ap.parse_args(argv)

    from config import get_swarmws
    from core import project_agent_rules as rules
    workspace = Path(get_swarmws())
    try:
        if a.cmd == "add":
            out = rules.add_rule(workspace, a.brain, a.said,
                                 os.environ.get("SWARM_APP_SESSION_ID", "").strip())
        elif a.cmd == "remove":
            out = rules.remove_rule(workspace, a.brain, a.said,
                                    os.environ.get("SWARM_APP_SESSION_ID", "").strip())
        else:
            from core.project_agent_session import PinError, brain_dir
            try:
                verified, unverified = rules.verified_rules(brain_dir(workspace, a.brain))
            except PinError as exc:
                raise rules.RuleError(str(exc)) from None
            out = {"brain": a.brain, "verified": verified, "unverified": unverified}
    except rules.RuleError as exc:
        print(json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
