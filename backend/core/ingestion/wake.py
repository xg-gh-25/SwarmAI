"""Best-effort wake signaling for the CDF owner worker.

- ``install_owner_event`` creates the process credential and binds an event.
- ``wake`` signals locally or POSTs to the registered loopback backend.
- ``authenticate`` validates loopback peers and credentials.
"""
from __future__ import annotations

import logging
import asyncio
import hmac
import json
import os
import secrets
import urllib.request
from pathlib import Path

from config import get_app_data_dir

from .api import Lane

_owner_event: asyncio.Event | None = None
_owner_loop: asyncio.AbstractEventLoop | None = None


def key_path() -> Path:
    """Return the per-start wake credential path."""
    return get_app_data_dir() / "state" / "ingestion_wake.key"


def install_owner_event(event: asyncio.Event, loop: asyncio.AbstractEventLoop) -> str:
    """Bind the owner event and atomically create a mode-0600 credential."""
    global _owner_event, _owner_loop
    _owner_event, _owner_loop = event, loop
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    credential = secrets.token_urlsafe(32)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, credential.encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    return credential


def uninstall_owner_event() -> None:
    """Forget the local owner event and remove this start's credential."""
    global _owner_event, _owner_loop
    _owner_event = None
    _owner_loop = None
    key_path().unlink(missing_ok=True)


def signal_local() -> bool:
    """Set the owner event thread-safely when this process owns it."""
    event, loop = _owner_event, _owner_loop
    if event is None or loop is None or loop.is_closed():
        return False
    loop.call_soon_threadsafe(event.set)
    return True


def authenticate(peer: str | None, supplied: str | None) -> bool:
    """Require an IP loopback peer and constant-time credential match."""
    import ipaddress

    try:
        if peer is None or not ipaddress.ip_address(peer).is_loopback or supplied is None:
            return False
        expected = key_path().read_text(encoding="ascii")
        return hmac.compare_digest(expected, supplied)
    except (OSError, ValueError):
        return False


def wake(lane: Lane | None = None) -> None:
    """Wake the owner, swallowing all local and cross-process errors."""
    del lane
    try:
        if signal_local():
            return
        root = get_app_data_dir()
        info = json.loads((root / "backend.json").read_text(encoding="utf-8"))
        port = int(info["port"])
        credential = key_path().read_text(encoding="ascii")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ingestion/wake",
            data=b"",
            method="POST",
            headers={"X-Swarm-Wake": credential},
        )
        with urllib.request.urlopen(request, timeout=0.25):  # noqa: S310 -- fixed loopback URL
            pass
    except Exception as exc:  # best-effort nudge: the 60 s poll picks the row up anyway
        logging.getLogger(__name__).debug("CDF wake POST failed: %s", exc)
        return
