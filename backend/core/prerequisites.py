"""Checks for the tools the app needs on this machine before chat can work.

``check_prerequisites()`` returns one item per check::

    {"name": "git" | "git_bash", "ok": True | False | None, "hint": str}

``ok`` is ``None`` when the check itself could not run (an error or a timeout);
the hint then says why. A check never raises.

- git: found on PATH. On macOS, ``/usr/bin/git`` is only a stub until the
  Command Line Tools are installed, so there it also needs ``xcode-select -p``
  to succeed.
- git_bash (Windows only): Claude Code runs its shell commands through Git Bash.
  It is found through ``CLAUDE_CODE_GIT_BASH_PATH`` when that is set, otherwise
  in the standard Git for Windows install places.
"""
from __future__ import annotations

import logging
import ntpath
import os
import platform
import shutil
import subprocess
from typing import Callable, Optional

logger = logging.getLogger(__name__)

GIT_BASH_ENV = "CLAUDE_CODE_GIT_BASH_PATH"
_TIMEOUT_S = 5
_XCODE_SELECT = ["/usr/bin/xcode-select", "-p"]
_INSTALL_CLT_HINT = "Run: xcode-select --install"

_MISSING_GIT_HINTS = {
    "Darwin": _INSTALL_CLT_HINT,
    "Windows": "Install Git for Windows from https://git-scm.com/download/win",
}
_MISSING_GIT_HINT_OTHER = "Install git with your package manager"


def _is_file(path: str) -> bool:
    return os.path.isfile(path)


def _item(name: str, ok: Optional[bool], hint: str = "") -> dict:
    return {"name": name, "ok": ok, "hint": hint}


def _check_git(system: str) -> dict:
    path = shutil.which("git")
    if not path:
        return _item("git", False, _MISSING_GIT_HINTS.get(system, _MISSING_GIT_HINT_OTHER))
    if system == "Darwin" and path == "/usr/bin/git":
        try:
            result = subprocess.run(
                _XCODE_SELECT, capture_output=True, text=True,
                timeout=_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            return _item("git", None, f"Could not check git: xcode-select timed out after {_TIMEOUT_S}s")
        except OSError:
            return _item("git", None, "Could not check git: xcode-select could not be run")
        if result.returncode != 0:
            return _item("git", False, _INSTALL_CLT_HINT)
    return _item("git", True)


def _git_bash_candidates() -> list[str]:
    candidates = []
    for env_key, default in (("ProgramFiles", r"C:\Program Files"),
                             ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidates.append(ntpath.join(os.environ.get(env_key) or default, "Git", "bin", "bash.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(ntpath.join(local, "Programs", "Git", "bin", "bash.exe"))
    # A custom install: git.exe lives in <Git>\cmd, bash.exe in <Git>\bin.
    git = shutil.which("git")
    if git:
        git_root = ntpath.dirname(ntpath.dirname(git))
        candidates.append(ntpath.join(git_root, "bin", "bash.exe"))
    return candidates


def _check_git_bash() -> dict:
    configured = os.environ.get(GIT_BASH_ENV)
    if configured:
        if _is_file(configured):
            return _item("git_bash", True)
        return _item(
            "git_bash", False,
            f"{GIT_BASH_ENV} is set to {configured}, but that file does not exist. "
            "Point it at bash.exe or remove it.",
        )
    if any(_is_file(path) for path in _git_bash_candidates()):
        return _item("git_bash", True)
    return _item(
        "git_bash", False,
        "Git Bash not found. Install Git for Windows from https://git-scm.com/download/win, "
        f"or set {GIT_BASH_ENV} to the full path of bash.exe.",
    )


def _safe(name: str, check: Callable[[], dict]) -> dict:
    try:
        return check()
    except Exception:  # a check never raises; the detail goes to the log, not the client
        logger.warning("prerequisite check %s failed", name, exc_info=True)
        return _item(name, None, f"Could not check {name}")


def check_prerequisites() -> list[dict]:
    """Return the prerequisite items for this machine (see the module docstring)."""
    try:
        system = platform.system()
    except OSError as e:
        logger.debug("prerequisites: platform unknown: %s", e)
        system = ""
    items = [_safe("git", lambda: _check_git(system))]
    if system == "Windows":
        items.append(_safe("git_bash", _check_git_bash))
    return items
