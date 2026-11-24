"""The Hive backend on an SSH machine (design docs/design/2026-11-24-swarm-out-ssh-machine.md §5.2).

The mode is on when ``SWARMAI_CARRIER=ssh`` and ``SWARMAI_MODE=hive`` (``active``); the Host's
installer (hive/ssh_provisioner) writes both into the container's environment file. Inert
outside the mode: the EC2 Hive, the AgentCore Hive and the desktop behave exactly as before.

What the mode changes, and where:

- ``core/swarm_out_bridge``: the Hive's name, bucket and region come from the environment
  (``identity``: ``SWARMAI_HIVE_NAME``, ``SWARMAI_HIVE_BUCKET``, ``AWS_REGION``). IMDS is never
  read: on a Cloud Desktop it answers with the company account's role, whose name is not
  ``SwarmAI-Hive-<name>``, and the bridge would turn itself off.
- ``routers/chat`` ``admission-state``: ``ssh_cert_valid_until``, the end of the latest SSH
  certificate in ``~/.ssh/*-cert.pub`` (``cert_valid_until``). The Host does not give this
  machine new tasks once it has passed (on a Cloud Desktop the certificate is Midway's: git
  and brazil stop working with it). No certificate: None, nothing is blocked.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CARRIER = "ssh"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
BUCKET_RE = re.compile(r"^[a-z0-9.-]{3,63}$")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
#: ``Valid: from 2026-11-24T01:33:52 to 2026-11-24T13:33:52`` (local time of the machine)
_VALID_RE = re.compile(r"Valid:\s+(?:from\s+\S+\s+)?to\s+(\S+)")
#: hang guard for one ``ssh-keygen -L``
KEYGEN_TIMEOUT_S = 5

_cache_lock = threading.Lock()
#: path -> (mtime_ns, size, valid-to or None)
_cache: dict[str, tuple[int, int, Optional[float]]] = {}


def active() -> bool:
    return os.environ.get("SWARMAI_CARRIER") == CARRIER and os.environ.get("SWARMAI_MODE") == "hive"


def identity() -> Optional[tuple[str, str, str]]:
    """(hive name, bucket, region) from the environment, or None when a part is missing or
    has the wrong shape (they all go into S3 keys and the S3 endpoint)."""
    name = os.environ.get("SWARMAI_HIVE_NAME", "")
    bucket = os.environ.get("SWARMAI_HIVE_BUCKET", "")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    if not NAME_RE.fullmatch(name) or not BUCKET_RE.fullmatch(bucket) or not REGION_RE.fullmatch(region):
        return None
    return name, bucket, region


def _valid_to(text: str) -> Optional[float]:
    """The ``to`` of ``ssh-keygen -L``'s Valid line as Unix seconds: ``forever`` is
    infinity; anything else that is not a local ``YYYY-MM-DDTHH:MM:SS`` is None."""
    m = _VALID_RE.search(text)
    if not m:
        return None
    to = m.group(1)
    if to == "forever":
        return float("inf")
    try:
        return time.mktime(time.strptime(to, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _read_one(path: Path) -> Optional[float]:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        out = subprocess.run(["ssh-keygen", "-L", "-f", str(path)], capture_output=True, text=True,  # noqa: S603,S607 — fixed command
                             timeout=KEYGEN_TIMEOUT_S, check=False).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.info("hive_ssh_mode: reading %s failed (%s)", path.name, type(e).__name__)
        return None
    got = _valid_to(out)
    with _cache_lock:
        _cache[key] = (st.st_mtime_ns, st.st_size, got)
    return got


def cert_valid_until(home: Optional[Path] = None) -> Optional[float]:
    """The latest end among the SSH certificates in ``~/.ssh/*-cert.pub`` (Unix seconds;
    a certificate valid forever counts as far in the future), or None when there is none
    or none can be read. Blocking (``ssh-keygen``, cached by file): call it off the loop."""
    d = (home or Path.home()) / ".ssh"
    try:
        paths = sorted(d.glob("*-cert.pub"))
    except OSError:
        return None
    ends = [v for v in (_read_one(p) for p in paths) if v is not None]
    if not ends:
        return None
    best = max(ends)
    return 4102444800.0 if best == float("inf") else best      # 2100-01-01: JSON has no infinity
