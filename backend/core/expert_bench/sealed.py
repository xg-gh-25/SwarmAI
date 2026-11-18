"""The sealed case: the tree a benchmark review reads, with no way to the answer.

Design: docs/design/2026-11-18-expert-review-benchmark.md §4. ``build`` exports the case's
base tree (B's parent) into a fresh repository with one commit and no remote or other
ref, then applies B's change to the working tree uncommitted, as a native review sees
it. Nothing later than B is in it, so ``git log --all`` or a search of the tree cannot
show the fix.

Public: ``build``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping

from core.expert_bench.cases import _export, _git

_IDENTITY = {"GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@localhost",
             "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@localhost",
             "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
             "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _local(dest: Path, *args: str, stdin: bytes | None = None) -> None:
    subprocess.run(["git", "-C", str(dest), "-c", "core.hooksPath=/dev/null", *args],
                   input=stdin, check=True, capture_output=True, timeout=120,
                   env={**os.environ, **_IDENTITY})


def build(repo: Path | str, case: Mapping, dest: Path) -> Path:
    """A fresh repository at *dest*: the case's base committed once, its head's change
    applied on top and left uncommitted."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    _export(repo, str(case["base"]), dest)
    _local(dest, "init", "-q", "-b", "main")
    _local(dest, "add", "-A", "-f")   # -f: the base's own .gitignore must not drop a tracked file
    _local(dest, "commit", "-q", "--allow-empty", "-m", "base")
    patch = _git(repo, "diff", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv",
                 str(case["base"]), str(case["head"]), binary=True)
    if patch:
        _local(dest, "apply", "--whitespace=nowarn", "-", stdin=patch)
    return dest
