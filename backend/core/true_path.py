"""true_path — a path spelled the way the disk stores it, for guards that compare paths
as strings.

A case-insensitive disk (macOS's and Windows' default) opens ``~/.SSH`` as ``~/.ssh``,
and ``os.path.realpath`` / ``Path.resolve`` keep the spelling they were given, so a
guard comparing the resolved string with a protected location (``~/.ssh``, SwarmWS,
the app data directory) misses the other spelling. ``true_case`` replaces each
existing component with the name its directory lists for it, so every spelling of one
folder compares equal; a component that does not exist is kept as given.

On a case-sensitive disk an existing exact name is always kept; a name that does not
exist but differs only in case from one entry is mapped to that entry, which can only
make a guard refuse more (fail-closed), never less.

Public: ``true_case(path)``.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path


def _fold(name: str) -> str:
    # APFS also ignores Unicode normalisation: "ü" written as one or two code points
    return unicodedata.normalize("NFC", name).casefold()


def true_case(path: str | Path) -> Path:
    """*path* (absolute; call it on a resolved path) with every existing component
    spelled as its parent directory lists it."""
    p = Path(path)
    if not p.is_absolute():
        return p
    parts = p.parts
    out = Path(parts[0])
    for i, name in enumerate(parts[1:], start=1):
        try:
            names = os.listdir(out)
        except OSError:                       # not a directory, or not readable
            return out.joinpath(*parts[i:])
        if name in names:
            out = out / name
            continue
        match = [n for n in names if _fold(n) == _fold(name)]
        if len(match) != 1:                   # absent (or ambiguous): the rest as given
            return out.joinpath(*parts[i:])
        out = out / match[0]
    return out
