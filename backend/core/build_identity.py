"""This install's build identity (scripts/build_identity.py): what a Host compares with a
Hive's to tell whether the Hive runs the Host's code, when both carry the same version string.

Where it comes from, first found wins, read once per process (``current``):

1. ``BUILD.json`` beside VERSION at the install root: a Hive package (hive/release.sh writes
   it into the package; /opt/swarmai/BUILD.json on a Hive);
2. ``BUILD.json`` in the PyInstaller bundle: the desktop backend (desktop/scripts/build-backend.sh);
3. computed from the checkout the backend runs from (a Host started from source), with the
   same function the two build scripts use, so a package built from that checkout matches.

None when none of them gives one (an older package, a source tarball without the file):
such a Host compares versions only, and a Hive that reports none is out of date.

- ``current()`` -> the identity dict or None (blocking the first time: call it off the loop).
- ``current_id()`` -> its id or None.
- ``reported_id(value)`` -> the id of an identity a Hive reported, or None.
- ``image_tag(version, build_id)`` -> the Elastic Workers image tag of that build
  (``<version>-b<id>``; the version alone without an id); ``display_version(tag)`` undoes it.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

FILE = "BUILD.json"
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND_DIR.parent
_TAG_SUFFIX_RE = re.compile(r"-b[0-9a-f]{16}$")

_lock = threading.Lock()
#: [loaded?, identity]
_CACHE: list[Any] = [False, None]


def _tool():
    from scripts import build_identity
    return build_identity


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        return _tool().valid(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _load() -> Optional[dict[str, Any]]:
    tool = _tool()
    for where in (_ROOT / FILE, Path(getattr(sys, "_MEIPASS", "") or "/nonexistent") / FILE):
        if where.is_file():
            got = _read(where)
            if got is None:
                logger.warning("build_identity: %s is not a valid build identity", where)
            return got
    if (_ROOT / ".git").exists():
        try:
            return tool.valid(tool.compute(_ROOT))
        except Exception as exc:  # noqa: BLE001 — no identity: versions alone decide
            logger.warning("build_identity: computing it from %s failed (%s)", _ROOT, type(exc).__name__)
    return None


def current() -> Optional[dict[str, Any]]:
    """This install's build identity, or None. Blocking the first time (a git call when it
    runs from a checkout); cached for the process."""
    if not _CACHE[0]:
        with _lock:
            if not _CACHE[0]:
                _CACHE[1] = _load()
                _CACHE[0] = True
    return _CACHE[1]


def current_id() -> Optional[str]:
    got = current()
    return got["id"] if got else None


def reported_id(value: Any) -> Optional[str]:
    """The id of a build identity a Hive reported (checked: the id its commit and digest
    make), else None."""
    got = _tool().valid(value)
    return got["id"] if got else None


def image_tag(version: str, build_id: Optional[str]) -> str:
    """The image tag hive/agentcore/build_image.sh gives a build: ``<version>-b<id>``."""
    version = str(version).removeprefix("v")
    return f"{version}-b{build_id}" if build_id else version


def display_version(tag: Optional[str]) -> Optional[str]:
    """The version a tag of ``image_tag`` names."""
    return _TAG_SUFFIX_RE.sub("", tag) if isinstance(tag, str) else tag
