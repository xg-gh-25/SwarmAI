"""backend_bin — how product code runs the backend program and its bundled Python (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2).

One rule for every place that used to spawn a system ``python``:

- ``backend_bin()`` — the backend program. Packaged: the frozen binary itself
  (``sys.executable``, the copy in ``runtime/<version>/``, never the moving ``current``).
  Dev: the shim ``backend/bin/python-backend``, which runs the same entry point
  (``desktop_main.py``) with the checkout's venv.
- ``python_argv()`` — the argv prefix that runs a Python script / ``-m`` / ``-c``:
  packaged ``[<binary>, "py"]`` (no system Python needed), dev ``[sys.executable]``.
- ``py_command(*args)`` — ``[<backend program>, "py", *args]``, for commands stored as text
  (scheduled jobs): the same form packaged and in dev.
- ``bundled_path(*parts)`` — a file the program ships (``_MEIPASS`` / ``backend/``).
- ``export_backend_bin()`` — put ``SWARM_BACKEND_BIN`` into this process's environment so
  every child (agent sessions, scheduled jobs, MCP servers) inherits it. Called once at
  daemon startup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV = "SWARM_BACKEND_BIN"
_SHIM = Path(__file__).resolve().parents[1] / "bin" / "python-backend"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def backend_bin() -> str:
    if is_frozen():
        return str(Path(sys.executable).resolve())
    return str(_SHIM)


def python_argv() -> "list[str]":
    if is_frozen():
        return [backend_bin(), "py"]
    return [sys.executable]


def py_command(*args: str) -> "list[str]":
    """``[<backend program>, "py", *args]`` — the same form packaged and in dev (P7)."""
    return [backend_bin(), "py", *args]


def bundled_path(*parts: str) -> Path:
    """A file this program ships (skills, scripts' data): under ``sys._MEIPASS`` when
    packaged, under ``backend/`` in dev."""
    base = Path(getattr(sys, "_MEIPASS", "")) if is_frozen() else Path(__file__).resolve().parents[1]
    return base.joinpath(*parts)


def export_backend_bin() -> str:
    value = backend_bin()
    os.environ[ENV] = value
    return value


__all__ = ["ENV", "backend_bin", "bundled_path", "export_backend_bin", "is_frozen",
           "py_command", "python_argv"]
