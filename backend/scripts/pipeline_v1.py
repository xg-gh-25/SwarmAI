#!/usr/bin/env python3
"""Pipeline v1 command line client (B-class design B-a/B-b).

Every command runs IN THE DAEMON (``core/run_protocol/cli_commands.py``): this
script sends its argv, the one-time hook token the session's PreToolUse hook put in
front of it (``SWARM_HOOK_TOKEN``), its SDK transcript id and, for ``--artifact -``,
its stdin, and prints the one JSON object the daemon answers. It never loads a run
or a signing key. With the daemon down it fails; there is no local fallback.

- ``parser()`` — the CLI's argparse definition (re-exported for tests and cards).
- ``main(argv)`` — exit code: 0 ok, 1 ``ok: false``, 2 refused / unavailable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def parser():
    from core.run_protocol.cli_commands import parser as _parser
    return _parser()


def main(argv: "list[str] | None" = None) -> int:
    from core import hook_token
    from core.run_protocol import daemon_exec
    argv = list(sys.argv[1:] if argv is None else argv)
    parser().parse_args(argv)        # a usage error is reported here (exit 2), not sent
    stdin = None
    if "--artifact=-" in argv or any(
            a == "--artifact" and i + 1 < len(argv) and argv[i + 1] == "-"
            for i, a in enumerate(argv)):
        stdin = sys.stdin.read()
    out, rc = daemon_exec.call(os.environ.get(hook_token.TOKEN_ENV), argv, stdin=stdin,
                               transcript_id=(os.environ.get("CLAUDE_CODE_SESSION_ID")
                                              or "").strip() or None)
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
