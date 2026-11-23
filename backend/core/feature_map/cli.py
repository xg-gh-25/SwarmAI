"""The ``feature_map`` CLI's arguments, in one place: the CLI (``scripts/feature_map.py``)
parses with it, and the commit notice (``core/feature_map/notice.py``) reads a replay or
verify command with the same parser, so every spelling argparse accepts (``--ba`` for
``--base``, ``--repo-root=X``) means the same thing to both. Exports: parser."""
from __future__ import annotations

import argparse

COMMANDS = ("where", "verify", "replay", "affected", "status")


def parser(description: str = "", *, exit_on_error: bool = True) -> argparse.ArgumentParser:
    """The CLI exits 2 with its usage on a bad command line (argparse's default); the
    notice passes ``exit_on_error=False`` and reads a refusal as "not a replay"."""
    ap = argparse.ArgumentParser(prog="feature_map", description=description or None,
                                 exit_on_error=exit_on_error)
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("features", nargs="*")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--project")
    ap.add_argument("--repo")
    ap.add_argument("--base")
    ap.add_argument("--run")
    ap.add_argument("--affected", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--keep-out-of-repo", action="store_true")
    return ap
