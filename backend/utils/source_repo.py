"""Where this backend's SOURCE checkout is, for the pipeline scripts the agent runs.

The pipeline CLI (``scripts/pipeline_v1.py``) and runner (``scripts/pipeline_test.py``)
run in the agent's process with a real Python. In a dev checkout they sit next to this
module. The packaged daemon is a PyInstaller bundle: its ``scripts`` are archived
modules and ``sys.executable`` is the frozen binary, so neither can be run. There the
source checkout is the one the bundle was BUILT from, recorded at build time in
``source_repo.json`` (``desktop/scripts/build-backend.sh``). A recorded path that is not
a SwarmAI checkout on this machine (a CI-built release) gives None: pipeline commands
then fail with a clear message instead of naming a file that does not run.

- ``root()`` — the source checkout (a directory with ``backend/scripts/pipeline_v1.py``) or None.
- ``pipeline_script(tool)`` — ``<root>/backend/scripts/<tool>.py`` or None.
- ``pipeline_python()`` — the checkout's venv python, else a real interpreter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = ("pipeline_v1", "pipeline_test")


def _is_checkout(path: Path) -> bool:
    return all((path / "backend" / "scripts" / f"{tool}.py").is_file() for tool in _TOOLS)


def root() -> "Path | None":
    if not getattr(sys, "frozen", False):
        here = Path(__file__).resolve().parents[2]
        return here if _is_checkout(here) else None
    meipass = getattr(sys, "_MEIPASS", None)
    try:
        rec = json.loads((Path(meipass) / "source_repo.json").read_text(encoding="utf-8")) \
            if meipass else None
    except (OSError, ValueError):
        return None
    raw = rec.get("root") if isinstance(rec, dict) else None
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        return None
    path = Path(raw).resolve()
    return path if _is_checkout(path) else None


def pipeline_script(tool: str) -> "Path | None":
    base = root()
    return (base / "backend" / "scripts" / f"{tool}.py").resolve() if base else None


def pipeline_python() -> str:
    base = root()
    venv = base / "backend" / ".venv" / "bin" / "python" if base else None
    if venv is not None and venv.exists():
        return str(venv)
    from utils.bundle_paths import get_python_executable
    return get_python_executable()


__all__ = ["pipeline_python", "pipeline_script", "root"]
