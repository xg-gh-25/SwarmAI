"""code_intel.parser._gitignored_subset feeds git from a file, not a pipe.

2026-10-22: the 300s full-reindex job timed out at this call. With ``input=``
(a stdin pipe) Python blocked in ``os.write`` on git's stdin while git blocked
writing its per-line flushed stdout; the subprocess timeout never ran, because
the blocked write never returned to it. Reproduced in this host with 40+ paths;
the file-stdin form returned in 0.23s for the whole repo (2038 paths).

Properties:
- stdin is a real file (never ``input=``), so the call cannot deadlock on pipes;
- the result is still the ignored subset (tracked and unignored paths kept);
- many paths (well past the deadlock size) finish.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.code_intel import parser as P


def _repo(tmp_path: Path) -> Path:
    root = Path(os.path.realpath(tmp_path)) / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("out/\n")
    return root


def test_ignored_subset_with_many_paths(tmp_path):
    root = _repo(tmp_path)
    (root / "out").mkdir()
    keep = [root / f"src_{i}.py" for i in range(300)]
    drop = [root / "out" / f"gen_{i}_{'x' * 60}.py" for i in range(300)]
    for p in keep + drop:
        p.write_text("x = 1\n")
    got = P._gitignored_subset(root, keep + drop)
    assert got == set(drop)


def test_stdin_is_a_file_not_a_pipe(tmp_path, monkeypatch):
    import utils.stdin_file as sf
    root = _repo(tmp_path)
    seen = {}
    real = subprocess.run

    def spy(cmd, *a, **k):
        seen.update(k)
        return real(cmd, *a, **k)

    monkeypatch.setattr(sf.subprocess, "run", spy)
    P._gitignored_subset(root, [root / "a.py"])
    assert "input" not in seen and hasattr(seen.get("stdin"), "fileno")


def test_run_with_stdin_file_feeds_str_or_bytes_and_refuses_input():
    import pytest
    from utils.stdin_file import run_with_stdin_file
    assert run_with_stdin_file(["cat"], "héllo", capture_output=True).stdout == "héllo".encode()
    assert run_with_stdin_file(["cat"], b"\0x", capture_output=True).stdout == b"\0x"
    with pytest.raises(TypeError):
        run_with_stdin_file(["cat"], "x", input="y")
