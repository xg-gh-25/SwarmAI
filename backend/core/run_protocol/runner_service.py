"""The daemon's side of the test runner (B-class B-b): leases, signing, seals.

``pipeline_test.py`` opens a lease with the one-time hook token of its own command
(scope ``test`` on one run). The daemon checks the token's session is the run's
signed owner, then serves that one run until the lease ends: the verified event log,
appends of the runner's own kinds signed as ``runner``, evidence seals, and the stage
completion that follows a call's results. Nothing else can be reached with a lease.

- ``handle(req, port)`` -> the reply object (``daemon_exec`` dispatches here)
- ``owner_refusal`` / ``after_tests`` — the same, called directly in the daemon's own
  process (tests playing it)
- ``clear()`` — drop every lease (the service stopped)
- ``RUNNER_KINDS`` — the event kinds a lease may append
"""
from __future__ import annotations

import base64
import contextlib
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

RUNNER_KINDS = frozenset({"test_call", "metric", "integrity", "proof", "infrastructure_failed"})
_IDLE_S = 1800.0
_MAX_S = 3 * 3600.0
_LEASES: "dict[str, dict]" = {}
_LOCK = threading.Lock()


def clear() -> None:
    with _LOCK:
        _LEASES.clear()


def _start_time(pid: "int | None") -> "str | None":
    """The process's start time from the kernel, in microseconds (macOS
    ``proc_pidinfo(PROC_PIDTBSDINFO)``, Linux ``/proc/<pid>/stat`` start ticks): with
    the pid it identifies ONE process, so a reused pid is not the lease's runner. No
    subprocess, so it is cheap and needs no ``ps`` (B-e review). None when the process
    is gone or cannot be read."""
    if not pid:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if sys.platform == "darwin":
        return _darwin_start(pid)
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return "proc:" + raw.rsplit(")", 1)[1].split()[19]   # field 22: starttime
    except (OSError, IndexError):
        return None


_LIBPROC = None


def _darwin_start(pid: int) -> "str | None":
    import ctypes
    import ctypes.util
    import struct
    global _LIBPROC
    try:
        if _LIBPROC is None:
            _LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
        buf = ctypes.create_string_buffer(136)              # struct proc_bsdinfo
        if _LIBPROC.proc_pidinfo(pid, 3, ctypes.c_uint64(0), buf, 136) != 136:
            return None
        if struct.unpack_from("I", buf, 12)[0] != pid:       # pbi_pid
            return None
        sec, usec = struct.unpack_from("QQ", buf, 120)        # pbi_start_tvsec/usec
        return f"bsd:{sec}.{usec:06d}"
    except (OSError, AttributeError, struct.error):
        return None


def reap() -> None:
    """Drop the leases whose runner process has exited or whose pid was reused."""
    with _LOCK:
        items = list(_LEASES.items())
    gone = [k for k, v in items if v.get("pid") is not None
            and _start_time(v["pid"]) != v.get("start")]
    with _LOCK:
        for k in gone:
            _LEASES.pop(k, None)


def _engine(rec: dict, producer: str = "runner"):
    from config import get_app_data_dir
    from core.run_protocol.cli_commands import _workspace
    from core.run_protocol.engine import RunEngine
    return RunEngine.load(data_dir=get_app_data_dir(), workspace=_workspace(),
                          project=rec["project"], run_id=rec["run_id"], producer=producer)


def _open(req: dict, port: "int | None") -> dict:
    from core import hook_token
    rec = hook_token.redeem(req.get("token"))
    run_id, project = req.get("run_id"), req.get("project")
    if rec is None:
        return {"ok": False, "error": "no valid hook token for this test call"}
    if rec.get("subcommand") != "test" or rec.get("run_id") != run_id \
            or not isinstance(project, str):
        return {"ok": False, "error": "hook token was issued for a different command"}
    return open_for(rec.get("session_id"), project, run_id, port=port)


def open_for(session: Any, project: Any, run_id: Any, *, port: "int | None") -> dict:
    """A lease for *session* on *run_id*, when it is the run's signed owner."""
    if not isinstance(session, str) or not isinstance(project, str) \
            or not isinstance(run_id, str):
        return {"ok": False, "error": "malformed runner lease request"}
    lease = {"run_id": run_id, "project": project, "session": session,
             "opened": time.time(), "used": time.time()}
    try:
        engine = _engine(lease)
    except Exception as exc:  # noqa: BLE001 — an unreadable run is not tested
        return {"ok": False, "error": f"run {run_id} cannot be read: "
                                      f"{type(exc).__name__}: {exc}"[:300]}
    owner = engine.owner_session()
    if lease["session"] != owner:
        return {"ok": False, "error": f"run {run_id} belongs to session {owner}; only that "
                                      "session runs its tests (this call wrote nothing)"}
    reap()
    key = f"{port}.{secrets.token_hex(32)}"
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _LEASES.items() if now - v["used"] > _IDLE_S
                  or now - v["opened"] > _MAX_S]:
            _LEASES.pop(k, None)
        _LEASES[key] = lease
    return {"ok": True, "lease": key}


_OWNER_LOCK_TIMEOUT = 30.0


class LeaseOwnerChanged(RuntimeError):
    """The run's signed owner is no longer the session the lease was opened for."""


@contextlib.contextmanager
def _as_owner(rec: dict):
    """Hold the run's operation lock and require the lease's session to be the run's
    CURRENT signed owner for the write inside: a lease opened before an owner transfer
    writes nothing after it (B-e re-audit HIGH). The lease is dropped on refusal."""
    from core.run_protocol.oplock import operation
    engine = _engine(rec)
    with operation(engine.audit_root, engine.run_id, timeout=_OWNER_LOCK_TIMEOUT):
        if engine.owner_session() != rec["session"]:
            with _LOCK:
                for key in [k for k, v in _LEASES.items() if v is rec]:
                    _LEASES.pop(key, None)
            raise LeaseOwnerChanged(
                f"run {rec['run_id']} now belongs to session {engine.owner_session()}; "
                "this runner's lease is revoked (it wrote nothing)")
        yield engine


def _refusal(spec: Any, authority):
    from core.run_protocol import lifecycle
    if not spec:
        return None
    if spec == ["infrastructure"]:
        return lambda chain: lifecycle.infrastructure_refusal(chain, authority)
    if isinstance(spec, list) and len(spec) == 4 and spec[0] == "cap":
        _name, cap, stage, attempt = spec
        return lambda chain: lifecycle.cap_already_recorded(chain, cap, stage, attempt)
    raise ValueError("unknown refusal")


def _append(rec: dict, args: dict) -> Any:
    from config import get_app_data_dir
    from core.run_protocol import keys
    from core.run_protocol.engine import _store
    from core.run_protocol.store import NEXT
    kind = args.get("kind")
    if kind not in RUNNER_KINDS:
        raise ValueError(f"a runner lease cannot record {kind!r}")
    payload = args.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if kind == "metric" and not str(payload.get("name") or "").startswith("cap_exceeded."):
        raise ValueError("the runner records only cap_exceeded metrics")
    audit_root = Path(get_app_data_dir()) / "state" / "pipeline_agent_audit"
    authority = keys.load(get_app_data_dir(), rec["run_id"])
    store = _store(audit_root, rec["run_id"], authority, producer="runner")
    ordinal = args.get("ordinal")
    event, _status = store.append_new(
        writer=authority.writer("runner"), kind=kind, at=args.get("at"), payload=payload,
        stage=args.get("stage"), round=args.get("round"), attempt=args.get("attempt"),
        ordinal=NEXT if ordinal == "next" else ordinal,
        refuse=_refusal(args.get("refuse"), authority))
    return event


def _own_evidence(rec: dict, path: str) -> str:
    from core import evidence_seal
    if evidence_seal.run_of(path) != rec["run_id"]:
        raise ValueError("not this run's evidence")
    return path


def owner_refusal(session: str, project: str, run_id: str) -> "str | None":
    """Why *session* may not test *run_id* (None: it is the run's signed owner)."""
    out = open_for(session, project, run_id, port=None)
    if out.get("ok") is not True:
        return str(out.get("error"))
    with _LOCK:
        _LEASES.pop(out["lease"], None)        # the check only: no lease is kept
    return None


def after_tests(session: str, project: str, run_id: str) -> dict:
    """In the daemon's own process: the stage completion after a call's results."""
    return _after_tests({"session": session, "project": project, "run_id": run_id}, {})


def _after_tests(rec: dict, args: dict) -> dict:
    from core.run_protocol.next_step import next_step
    from core.run_protocol.oplock import operation
    engine = _engine(rec, producer="cli")
    # the owner check and the completion under ONE operation lock: an owner transfer
    # cannot land between them (B-e re-audit HIGH)
    with operation(engine.audit_root, engine.run_id, timeout=_OWNER_LOCK_TIMEOUT):
        if engine.owner_session() != rec["session"]:
            return {"next": next_step(engine)}
        return _complete_after_tests(engine)


def _complete_after_tests(engine) -> dict:
    from core.run_protocol import cli_commands
    from core.run_protocol.next_step import auto_completable, next_step
    state = engine.projection()
    if not auto_completable(engine, state):
        return {"next": next_step(engine)}
    try:
        result = cli_commands._with_next(
            engine, engine.stage_complete(None, expected_stage=state["stage"]),
            previous_stage=state["stage"], mutating=True)
    except Exception as exc:  # noqa: BLE001 — the results are already recorded
        return {"stage_complete": {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]},
                "next": next_step(engine)}
    return {"stage_complete": {key: result.get(key) for key in ("ok", "error", "attempt")},
            "next": result.get("next") or next_step(engine)}


def _serve(rec: dict, name: str, args: dict) -> Any:
    from core import evidence_seal
    from core.run_protocol import lifecycle
    if name == "events":
        return _engine(rec).store.read_events()
    if name == "stage_attempt":
        engine = _engine(rec)
        state = lifecycle.fold(engine.store.read_events(), engine.authority)
        stage = str(state.get("stage") or "run")
        return {"stage": stage,
                "attempt": int((state.get("stage_attempts") or {}).get(stage) or 1)}
    if name == "append":
        with _as_owner(rec):
            return _append(rec, args)
    if name == "seal_write":
        with _as_owner(rec):
            return evidence_seal.write_sealed(_own_evidence(rec, str(args.get("path"))),
                                              str(args.get("text")))
    if name == "seal_read":
        try:
            data, ok, why = evidence_seal.read_checked(_own_evidence(rec, str(args.get("path"))))
        except OSError as exc:
            return {"data": None, "ok": False, "reason": f"unreadable ({type(exc).__name__})",
                    "oserror": True}
        return {"data": base64.b64encode(data).decode() if data is not None else None,
                "ok": ok, "reason": why}
    if name == "sealed_missing":
        return evidence_seal.sealed_missing(_own_evidence(rec, str(args.get("path"))))
    if name == "after_tests":
        return _after_tests(rec, args)
    raise ValueError(f"unknown runner call {name!r}")


def handle(req: dict, port: "int | None", peer_pid: "int | None" = None) -> dict:
    """*peer_pid*: the connecting process (from the socket), so a lease is bound to
    the runner process that opened it and dies with it (B round 1 MED)."""
    if req.get("op") == "runner_open":
        if not peer_pid:
            # a lease is bound to its process; a peer that cannot be identified gets
            # none (B round 2: None would match every other unidentified peer)
            return {"ok": False, "error": "the runner's process cannot be identified"}
        start = _start_time(peer_pid)
        if start is None:
            # pid + start time identify ONE process; without the start time a reused
            # pid would pass for the runner (B-e re-audit LOW): no lease
            return {"ok": False, "error": "the runner's process start cannot be read"}
        out = _open(req, port)
        if out.get("ok") is True:
            with _LOCK:
                _LEASES[out["lease"]].update(pid=peer_pid, start=start)
        return out
    key = req.get("lease")
    # the process check runs outside the lock (B-e re-audit LOW)
    with _LOCK:
        held = _LEASES.get(key) if isinstance(key, str) else None
        held_pid = held.get("pid") if held is not None else None
    live_start = _start_time(held_pid) if held_pid is not None and held_pid == peer_pid \
        else None
    with _LOCK:
        rec = _LEASES.get(key) if isinstance(key, str) else None
        now = time.time()
        if rec is not None and rec.get("pid") is not None and rec["pid"] == peer_pid and (
                now - rec["used"] > _IDLE_S or now - rec["opened"] > _MAX_S
                or live_start != rec.get("start")):
            _LEASES.pop(key, None)               # expired, or its process is gone/reused
            rec = None
        elif rec is not None and rec.get("pid") is not None and rec["pid"] != peer_pid:
            rec = None                           # another process: refused, lease kept
        if rec is not None:
            rec["used"] = now
    if rec is None:
        return {"ok": False, "error": "unknown or expired runner lease"}
    name = req.get("call")
    if name == "close":
        with _LOCK:
            _LEASES.pop(key, None)
        return {"ok": True, "value": None}
    args = req.get("args") if isinstance(req.get("args"), dict) else {}
    try:
        return {"ok": True, "value": _serve(rec, str(name), args)}
    except Exception as exc:  # noqa: BLE001 — a refusal is the runner's to report
        from core.run_protocol.store import StoreError
        # a store refusal travels as its own text: the runner matches on it
        text = str(exc) if isinstance(exc, StoreError) else f"{type(exc).__name__}: {exc}"
        return {"ok": False, "error": text[:500]}


__all__ = ["RUNNER_KINDS", "clear", "handle"]
