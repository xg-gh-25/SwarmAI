"""Pipeline v1 command line: ``"$SWARM_BACKEND_BIN" pipeline-v1 <sub> ...`` (design
usable-by-anyone §3.2, §3.3.5). ``desktop_main`` dispatches the ``pipeline-v1``
subcommand here; there is no other way to run it (S6-5 removed the
python-plus-script-path form, and its script).

Every command runs in this process (``core/run_protocol/cli_commands.py``), whether
or not the SwarmAI daemon is up. The caller is the app session in
``SWARM_APP_SESSION_ID``; ``CLAUDE_CODE_SESSION_ID`` names its transcript (usage is
measured from it); ``--artifact -`` reads stdin. Prints one JSON object.

- ``parser()`` — the CLI's argparse definition (re-exported for tests and cards).
- ``main(argv)`` — exit code: 0 ok, 1 ``ok: false``, 2 refused / unavailable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

def parser():
    from core.run_protocol.cli_commands import parser as _parser
    return _parser()


def _localize(argv: "list[str]") -> "tuple[list[str], str | None]":
    """Resolve what names a file here, in the agent's cwd, once. The values are the ones argparse itself takes (the LAST ``--artifact``); the argv
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
    from core.run_protocol import cli_commands, request_ctx
    argv = list(sys.argv[1:] if argv is None else argv)
    parser().parse_args(argv)        # a usage error is reported here (exit 2)
    try:
        argv, stdin = _localize(argv)
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"artifact unreadable: {exc}"}))
        return 2
    transcript = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    req = request_ctx.Request(
        session_id=(os.environ.get("SWARM_APP_SESSION_ID") or "").strip(),
        transcript_ids=(transcript,) if transcript else (), stdin=stdin)
    with request_ctx.active(req):
        out, rc = cli_commands.run(cli_commands.parser().parse_args(argv))
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return rc

