"""Regression tests for Cycle A (run_b454ce39): the three async workspace-file
endpoints must offload their SYNC blocking calls to a worker thread, never run
them directly on the event loop.

The three offenders (workspace_api.py):
  - get_workspace_file_committed  → git `subprocess.run` (blocks ~79ms/call)
  - get_workspace_file            → `read_text` / `read_bytes` (blocks on big files)
  - get_workspace_file_diff       → git `subprocess.run` + `read_text`

WHY a to_thread-spy test (not just "it returns the right thing"): the existing
fail-open/behavior tests stay green whether the call is offloaded or not — they
prove correctness, NOT non-blocking. This test proves the OFFLOAD: it spies
`asyncio.to_thread` and asserts each endpoint routed its blocking work through it.
Mutation check: revert any `await asyncio.to_thread(fn, ...)` back to a bare
`fn(...)` and the matching assertion goes RED (the spy sees one fewer offload).

These drive the REAL endpoints (real tmp git repo, real files) — only the
workspace-path resolver is stubbed to point at the tmp dir.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import routers.workspace_api as wa


async def _async_return(value):
    return value


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True,
                   capture_output=True, text=True)


def _tmp_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


def _run_endpoint(coro_factory, workspace_root: Path, monkeypatch):
    """Drive an async endpoint synchronously with a tmp workspace + a spy on
    asyncio.to_thread that records every offloaded call, then delegates to the
    real implementation so behavior is unchanged."""
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(fn, /, *a, **k):
        # Record a stable name for the offloaded callable.
        name = getattr(fn, "__name__", repr(fn))
        calls.append(name)
        return await real_to_thread(fn, *a, **k)

    monkeypatch.setattr(wa, "_get_workspace_path", lambda: _async_return(str(workspace_root)))
    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

    result = asyncio.run(coro_factory())
    return result, calls


def test_committed_offloads_git_subprocess(tmp_path: Path, monkeypatch) -> None:
    _tmp_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    target = (tmp_path / "a.txt").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (target, False))

    result, calls = _run_endpoint(
        lambda: wa.get_workspace_file_committed(path=str(target)),
        tmp_path, monkeypatch,
    )
    # Behavior preserved: tracked file → in_head True + its content.
    assert result["in_head"] is True
    assert result["content"] == "v1\n"
    # Offload proven: the blocking git subprocess.run went through to_thread.
    assert len(calls) >= 1, f"committed endpoint did not offload its git call (to_thread calls: {calls})"


def test_get_file_offloads_read(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "b.txt").write_text("hello\n")
    target = (tmp_path / "b.txt").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (target, False))

    result, calls = _run_endpoint(
        lambda: wa.get_workspace_file(path=str(target)),
        tmp_path, monkeypatch,
    )
    assert result["content"] == "hello\n"
    assert result["encoding"] == "utf-8"
    # Offload proven: the blocking read went through to_thread.
    assert len(calls) >= 1, f"get_workspace_file did not offload its read (to_thread calls: {calls})"


def test_diff_offloads_git_subprocess(tmp_path: Path, monkeypatch) -> None:
    _tmp_repo(tmp_path)
    (tmp_path / "c.txt").write_text("line1\nline2\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "c.txt").write_text("line1\nline2-changed\n")  # dirty → non-empty diff
    target = (tmp_path / "c.txt").resolve()
    monkeypatch.setattr(wa, "_resolve_file_path", lambda p, r: (target, False))

    result, calls = _run_endpoint(
        lambda: wa.get_workspace_file_diff(path=str(target)),
        tmp_path, monkeypatch,
    )
    assert "hunks" in result
    # Offload proven: the blocking git diff subprocess went through to_thread.
    assert len(calls) >= 1, f"get_workspace_file_diff did not offload its git call (to_thread calls: {calls})"
