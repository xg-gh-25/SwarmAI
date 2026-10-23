"""The test runner's side of the daemon (B-class B-b): the runner never loads a key.

``pipeline_test.py`` opens a lease with the one-time hook token of its own command
(``open_lease``); every event it records, every evidence seal it writes or checks,
and the stage completion after its results go to the daemon under that lease
(``daemon_exec`` op ``runner``). The daemon signs as ``runner`` only, and only the
runner's event kinds, for the run the token was issued for, after checking the
token's session is the run's owner.

The lease lives in this process's memory only (never in the environment the tests
inherit). In the daemon itself (tests playing it, in-process callers) no lease is
installed and callers use the local store.

- ``open_lease(token, project, run_id)`` -> (ok, error) and installs the lease
- ``active()`` -> whether a lease is installed in this process
- ``in_daemon()`` -> this is the daemon's own process (no lease, local store)
- ``call(name, **args)`` -> the daemon's answer (raises ``RunnerServiceError``)
- ``RemoteStore`` — ``read_events`` / ``append_new`` with the store's signature
"""
from __future__ import annotations

import base64
from typing import Any, Optional

_LEASE: Optional[str] = None


class RunnerServiceError(RuntimeError):
    """The daemon refused or could not be reached."""


def in_daemon() -> bool:
    """This is the daemon's own process (tests play it): it holds the keys, so the
    runner code here uses the local store and needs no lease."""
    from core.run_protocol import events
    return events.hook_writer_allowed()


def active() -> bool:
    """A lease is installed in this (runner) process."""
    return _LEASE is not None and not in_daemon()


def open_lease(token: Optional[str], project: str, run_id: str) -> "tuple[bool, str]":
    global _LEASE
    from core.run_protocol import daemon_exec
    out = daemon_exec.request({"op": "runner_open", "token": token, "project": project,
                               "run_id": run_id}, token=token)
    if out.get("ok") is not True or not isinstance(out.get("lease"), str):
        return False, str(out.get("error") or "the daemon refused the runner lease")
    _LEASE = out["lease"]
    return True, ""


def close() -> None:
    global _LEASE
    if _LEASE is not None and not in_daemon():
        try:
            call("close")
        except RunnerServiceError:
            pass
    _LEASE = None


def call(name: str, **args: Any) -> Any:
    from core.run_protocol import daemon_exec
    if _LEASE is None:
        raise RunnerServiceError("no runner lease in this process")
    out = daemon_exec.request({"op": "runner", "lease": _LEASE, "call": name, "args": args},
                              token=_LEASE)
    if out.get("ok") is not True:
        raise RunnerServiceError(str(out.get("error") or "runner request refused"))
    return out.get("value")


class RemoteStore:
    """The runner's event store, kept by the daemon."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.authority = None          # the key never leaves the daemon

    def read_events(self) -> list[dict[str, Any]]:
        return list(call("events"))

    def append_new(self, *, writer: Any = None, kind: str, at: str, payload: dict,
                   stage: Any = None, round: Any = None, attempt: Any = None,
                   ordinal: Any = None, refuse: Any = None, blobs: Any = None):
        from core.run_protocol.store import NEXT, StoreError
        if blobs:
            raise StoreError("the runner records no blobs")
        if refuse is not None and not isinstance(refuse, tuple):
            raise StoreError("a remote refusal is named, e.g. ('cap', cap, stage, attempt)")
        try:
            event = call("append", kind=kind, at=at, payload=payload, stage=stage,
                         round=round, attempt=attempt,
                         ordinal="next" if ordinal is NEXT else ordinal,
                         refuse=list(refuse) if refuse else None)
        except RunnerServiceError as exc:
            raise StoreError(str(exc)) from exc
        return event, None


def seal_write(path: str, text: str) -> "str | None":
    return call("seal_write", path=str(path), text=text)


def seal_read(path: str) -> "tuple[bytes | None, bool, str | None]":
    value = call("seal_read", path=str(path))
    data = base64.b64decode(value["data"]) if value.get("data") is not None else None
    return data, bool(value.get("ok")), value.get("reason")


__all__ = ["RemoteStore", "RunnerServiceError", "active", "call", "close", "open_lease",
           "seal_read", "seal_write"]
