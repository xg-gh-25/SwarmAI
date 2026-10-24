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


def _localize(argv: "list[str]") -> "tuple[list[str], str | None]":
    """Resolve what names a file in THIS process, whose cwd is the agent's: the daemon
    runs elsewhere, so a relative path would mean its directory (B-e re-audit MED).
    The values are the ones argparse itself takes (the LAST ``--artifact``); the argv
    sent carries exactly one ``--artifact``:
    - ``-`` → stdin, read once;
    - valid inline JSON → sent as is (even if a file has that name);
    - otherwise an existing file (``~`` expanded) → its text, sent as stdin (``-``);
    - anything else → sent as is (the daemon reports it is not JSON).
    Each ``--repo name=<path>`` gets ``~`` expanded and is made absolute here."""
    args = parser().parse_args(argv)
    artifact = getattr(args, "artifact", None)
    stdin: "str | None" = None
    if artifact == "-":
        stdin = sys.stdin.read()
    elif artifact is not None and not _is_json(artifact):
        path = os.path.expanduser(artifact)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                stdin = handle.read()
            artifact = "-"
    out: list[str] = []
    i = 0
    while i < len(argv):
        flag, eq, inline = argv[i].partition("=")
        if flag in ("--artifact", "--repo") and (eq or i + 1 < len(argv)):
            value = inline if eq else argv[i + 1]
            i += 1 if eq else 2
            if flag == "--repo":
                name, sep, path = value.partition("=")
                if sep and path:
                    path = os.path.expanduser(path)
                    value = f"{name}={path if os.path.isabs(path) else os.path.abspath(path)}"
                out += [flag, value]
            continue                    # every --artifact is dropped; ONE is re-added
        out.append(argv[i])
        i += 1
    if artifact is not None:
        out += ["--artifact", artifact]
    return out, stdin


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def main(argv: "list[str] | None" = None) -> int:
    from core import hook_token
    from core.run_protocol import daemon_exec
    argv = list(sys.argv[1:] if argv is None else argv)
    parser().parse_args(argv)        # a usage error is reported here (exit 2), not sent
    try:
        argv, stdin = _localize(argv)
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"artifact unreadable: {exc}"}))
        return 2
    out, rc = daemon_exec.call(os.environ.get(hook_token.TOKEN_ENV), argv, stdin=stdin,
                               transcript_id=(os.environ.get("CLAUDE_CODE_SESSION_ID")
                                              or "").strip() or None)
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
