"""Seals for the evidence the pipeline test runner writes.

The test runner (``backend/scripts/pipeline_test.py``) writes its reports under a run's
``evidence/{tests,proofs,probes,mutations}/``: test results that decide landing, break
-test proofs that close Gate-2 findings, the round snapshots and the old-side cache.
That directory is writable by the agent the reports judge, so a hand-written
``latest-regression.json`` or ``prove-R1-x.json`` would otherwise count as the runner's.

A seal is the sha256 of the bytes the runner last wrote to a file, kept in a per-run
store in the protected agent-audit directory (``state/pipeline_agent_audit/
evidence_<run_id>.json``, next to the hand-out ledger), which ``security_hooks``'
audit-dir write gate denies to the agent's own commands. Readers accept a runner file
only when its bytes match its latest seal; anything else is present but unusable.

Public API:
- ``run_of(path)``: the run id of a runner-written evidence path, else None
- ``enable(run_id)``: create the run's store (at run creation); returns warnings
- ``write_sealed(path, text)``: atomic write + seal under the store's lock
- ``read_checked(path)``: ``(bytes | None, ok, reason)``: the bytes that were hashed,
  so a reader parses exactly what it checked (no swap between check and use)
- ``check(path)``: ``(ok, reason)``; ``ok`` False = do not use this file

Rules:
- Checked only for runs that were enabled. "Enabled" is recorded twice: in the store
  and as ``records.evidence_seal`` in the run's hand-out ledger, so a store that goes
  missing after enable() reads as "no seal" (rejected), not as an old run.
- A store or ledger that cannot be read fails OPEN with a reason: an infra error must
  never lock a run out of its own evidence.
- Only the four runner subdirs are checked; files the CLI writes elsewhere under
  ``evidence/`` (prompts, gate2-v2.json, fixing specs) are not.
- Paths are taken as written (``abspath``, never ``realpath``), and a checked path with
  a symlink anywhere from ``runs/<run_id>`` down is rejected: a link could otherwise
  point a report at a file outside the run, or at an older sealed report.
Residual: a script the agent writes can still call ``write_sealed`` (the audit-dir
gate matches path text in commands, not imports) — the same boundary as the review
markers next to it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SUBDIRS = frozenset({"tests", "proofs", "probes", "mutations"})
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _store_dir() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "state" / "pipeline_agent_audit"


def _store(run_id: str) -> Path:
    return _store_dir() / f"evidence_{run_id}.json"


def _locate(path) -> "tuple[str, int] | None":
    """(run_id, index of the ``runs`` component) for a runner evidence path."""
    parts = Path(os.path.abspath(str(path))).parts
    for i in range(len(parts) - 3, 1, -1):
        if parts[i] == "evidence" and parts[i - 2] == "runs" and parts[i + 1] in SUBDIRS \
                and _RUN_ID.fullmatch(parts[i - 1]):
            return parts[i - 1], i - 2
    return None


def _key(path) -> str:
    """The seal key: the part above ``runs/`` resolved (so two spellings of the
    workspace give one key), ``runs/<run_id>/...`` taken as written (never resolved:
    a link there is rejected by _symlinked, not followed)."""
    ab = os.path.abspath(str(path))
    loc = _locate(ab)
    if loc is None:
        return ab
    parts = Path(ab).parts
    return os.path.join(os.path.realpath(os.path.join(*parts[:loc[1]])), *parts[loc[1]:])


def run_of(path) -> "str | None":
    """``<...>/runs/<run_id>/evidence/<subdir>/...`` with subdir one of SUBDIRS."""
    loc = _locate(path)
    return loc[0] if loc else None


def _symlinked(path) -> bool:
    """A symlink at ``runs/<run_id>`` or any component below it, the file included."""
    loc = _locate(path)
    if loc is None:
        return False
    parts = Path(os.path.abspath(str(path))).parts
    cur = Path(*parts[:loc[1] + 1])
    for comp in parts[loc[1] + 1:]:
        cur = cur / comp
        if os.path.islink(cur):
            return True
    return False


def _read(p: Path) -> "tuple[dict | None, str | None]":
    """(data, None) · (None, None) absent · (None, reason) unreadable."""
    try:
        if not p.exists():
            return None, None
        d = json.loads(p.read_text(encoding="utf-8"))
        return (d, None) if isinstance(d, dict) else (None, "not a JSON object")
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}"


def _ledger_enabled(run_id: str) -> "bool | None":
    """``records.evidence_seal`` of the run's hand-out ledger; None when unreadable."""
    d, bad = _read(_store_dir() / f"handout_{run_id}.json")
    if bad:
        return None
    return bool(((d or {}).get("records") or {}).get("evidence_seal"))


def _locked(run_id: str):
    from utils.file_lock import flock_exclusive, flock_unlock
    lk = open(_store(run_id).with_suffix(".lock"), "a+")
    flock_exclusive(lk.fileno())
    return lk, flock_unlock


def _save(run_id: str, data: dict) -> None:
    p = _store(run_id)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def enable(run_id: str) -> "list[str]":
    """Create the run's store (kept if it already exists). Returns warnings."""
    if not _RUN_ID.fullmatch(run_id or ""):
        return [f"evidence seal: unsafe run id {run_id!r}"]
    try:
        _store_dir().mkdir(parents=True, exist_ok=True)
        lk, unlock = _locked(run_id)
        try:
            d, _bad = _read(_store(run_id))
            d = d or {}
            d.setdefault("run_id", run_id)
            d["enabled"] = True
            d.setdefault("seals", {})
            _save(run_id, d)
        finally:
            unlock(lk.fileno())
            lk.close()
        return []
    except Exception as exc:  # noqa: BLE001 — reported to the caller, which decides
        return [f"evidence seal store not written ({type(exc).__name__}: {exc})"]


def write_sealed(path, text: str) -> "str | None":
    """Atomic write of ``text`` to ``path``; for a runner evidence file of an enabled run,
    the replace and the seal happen under the store's lock, so two writers cannot leave
    the store holding the other's sha. Returns a warning when the seal could not be
    recorded (the file is then unusable for an enabled run until written again)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    rid = run_of(path)
    if rid is None or not _store(rid).exists():
        os.replace(tmp, path)
        if rid is not None and _ledger_enabled(rid):
            return (f"evidence seal store of {rid} is missing: {path.name} is unsealed and "
                    "will not count")
        return None
    try:
        lk, unlock = _locked(rid)
    except Exception as exc:  # noqa: BLE001 — the write itself must still happen
        os.replace(tmp, path)
        return f"evidence seal lock failed ({type(exc).__name__}): {path.name} is unsealed"
    try:
        os.replace(tmp, path)
        d, bad = _read(_store(rid))
        if bad:
            return f"evidence seal store unreadable ({bad}): {path.name} is unsealed"
        d = d or {"run_id": rid, "enabled": True}
        if not isinstance(d.get("seals"), dict):
            d["seals"] = {}
        d["seals"][_key(path)] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        _save(rid, d)
        return None
    except Exception as exc:  # noqa: BLE001 — report, never break the runner's write
        return f"evidence seal not recorded ({type(exc).__name__}): {path.name} is unsealed"
    finally:
        unlock(lk.fileno())
        lk.close()


def _read_nofollow(path) -> bytes:
    fd = os.open(_key(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                return b"".join(chunks)
            chunks.append(b)
    finally:
        os.close(fd)


def _open(reason: str) -> "tuple[bool, str]":
    logger.warning("evidence seal not checked (fail open): %s", reason)
    return True, reason


def read_checked(path) -> "tuple[bytes | None, bool, str | None]":
    """The file's bytes and whether a reader may use them: ``(data, True, None)``
    unchecked or sealed; ``(data, True, reason)`` could not be checked (fail open);
    ``(None, False, reason)`` reject. OSError propagates for an unreadable file."""
    rid = run_of(path)
    if rid is None:
        return Path(path).read_bytes(), True, None
    d, bad = _read(_store(rid))
    if bad:
        ok, why = _open(f"evidence seal store unreadable ({bad})")
        return Path(path).read_bytes(), ok, why
    if d is None:
        led = _ledger_enabled(rid)
        if led is None:
            ok, why = _open("hand-out ledger unreadable")
            return Path(path).read_bytes(), ok, why
        if not led:
            return Path(path).read_bytes(), True, None  # a run made before seals
        return None, False, "the run's evidence seal store is missing"
    if not d.get("enabled"):
        return Path(path).read_bytes(), True, None
    if _symlinked(path):
        return None, False, "a symlink in the evidence path (only the runner's own files count)"
    seals = d.get("seals") if isinstance(d.get("seals"), dict) else {}
    want = seals.get(_key(path))
    if not want:
        return None, False, "not written by the test runner (no seal)"
    data = _read_nofollow(path)
    if hashlib.sha256(data).hexdigest() != want:
        return None, False, "changed since the test runner wrote it (seal mismatch)"
    return data, True, None


def sealed_missing(path) -> "str | None":
    """A reason when ``path`` is absent but the runner sealed it: deleting a sealed
    report (e.g. snapshots.json, so any snapshot's proof would count) is not absence."""
    rid = run_of(path)
    if rid is None or os.path.lexists(_key(path)):
        return None
    d, _bad = _read(_store(rid))
    seals = (d or {}).get("seals") if isinstance((d or {}).get("seals"), dict) else {}
    return "sealed by the test runner, then removed" if seals.get(_key(path)) else None


def check(path) -> "tuple[bool, str | None]":
    """Whether a reader may use ``path``. ``(True, None)`` not checked or sealed;
    ``(True, reason)`` cannot be checked (fail open); ``(False, reason)`` reject."""
    try:
        _data, ok, why = read_checked(path)
    except OSError as exc:
        return False, f"unreadable ({type(exc).__name__})"
    return ok, why
