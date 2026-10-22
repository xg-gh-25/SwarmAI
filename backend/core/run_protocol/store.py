"""Durable per-run event and blob storage for Pipeline v1.

Public symbols:
- ``AuditStore`` — locked append/read access to one run's audit directory.
- ``AuditPaths`` — canonical paths for the run and session audit layouts.
- ``StoreError`` — fail-closed persistence or integrity failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.run_protocol import events as ev
from core.run_protocol.identifiers import IdentifierError, validate_run_id
from utils.file_lock import flock_exclusive, flock_unlock

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class StoreError(RuntimeError):
    """Audit data is unsafe, malformed, conflicting, or unavailable."""


@dataclass(frozen=True)
class AuditPaths:
    """Canonical v1 audit paths rooted below an injected state directory."""

    root: Path
    run_id: str

    @property
    def run(self) -> Path:
        return self.root / "runs" / self.run_id

    @property
    def events(self) -> Path:
        return self.run / "events.jsonl"

    @property
    def blobs(self) -> Path:
        return self.run / "blobs"

    @property
    def contract(self) -> Path:
        return self.run / "contract.json"

    @property
    def seals(self) -> Path:
        return self.run / "seals.json"

    @property
    def lock(self) -> Path:
        return self.run / ".events.lock"

    def session(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id or ""):
            raise StoreError("unsafe session_id")
        return self.root / "sessions" / session_id


class AuditStore:
    """Append-only storage for events signed by one injected run authority."""

    def __init__(self, root: str | Path, run_id: str, authority: ev.EventAuthority):
        try:
            safe_id = validate_run_id(run_id)
        except IdentifierError as exc:
            raise StoreError(str(exc)) from exc
        if not isinstance(authority, ev.EventAuthority):
            raise StoreError("EventAuthority is required")
        self.paths = AuditPaths(Path(root), safe_id)
        self.authority = authority

    def _ensure(self) -> None:
        self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.paths.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _locked(self):
        self._ensure()
        handle = open(self.paths.lock, "a+", encoding="utf-8")
        flock_exclusive(handle.fileno())
        return handle

    @staticmethod
    def _atomic_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_json(path: Path, *, missing: Any) -> Any:
        if not path.exists():
            return missing
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError(f"unreadable {path.name}: {exc}") from exc

    def read_events(self) -> list[dict[str, Any]]:
        """Read and verify the chain; only a torn final line is ignored."""
        path = self.paths.events
        if not path.exists():
            return []
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StoreError(f"events unreadable: {exc}") from exc
        lines = raw.splitlines(keepends=True)
        if lines and not lines[-1].endswith(b"\n"):
            lines.pop()
        result: list[dict[str, Any]] = []
        for number, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StoreError(f"corrupt events line {number}: {exc}") from exc
            if not isinstance(item, dict):
                raise StoreError(f"corrupt events line {number}: not an object")
            result.append(item)
        errors = ev.verify_chain(result, self.authority)
        if errors:
            raise StoreError(f"invalid event chain: {errors[0]}")
        return result

    def _discard_torn_tail(self) -> None:
        """Truncate only an unterminated final JSONL fragment while holding the lock."""
        path = self.paths.events
        if not path.exists():
            return
        with open(path, "r+b") as handle:
            raw = handle.read()
            if not raw or raw.endswith(b"\n"):
                return
            end = raw.rfind(b"\n") + 1
            handle.truncate(end)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _blob_refs(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            if set(value) == {"blob"} and isinstance(value.get("blob"), str):
                return {value["blob"]}
            return {digest for item in value.values()
                    for digest in AuditStore._blob_refs(item)}
        if isinstance(value, list):
            return {digest for item in value for digest in AuditStore._blob_refs(item)}
        return set()

    def _write_blob(self, digest: str, data: bytes) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(data).hexdigest() != digest:
            raise StoreError("blob digest mismatch")
        path = self.paths.blobs / digest
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                current = path.read_bytes()
            except OSError as exc:
                raise StoreError(f"existing blob unreadable: {exc}") from exc
            if current != data:
                raise StoreError("content-addressed blob conflict")
            return
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    def _append_locked(
        self, chain: list[dict[str, Any]], event: dict[str, Any], blobs: Mapping[str, bytes],
    ) -> str:
        updated, status, generated = ev.add_event(self.authority, chain, event)
        available = {**dict(blobs), **generated}
        refs = {digest for item in updated[len(chain):]
                for digest in self._blob_refs(item.get("payload"))}
        for digest in refs:
            path = self.paths.blobs / digest
            if digest not in available and not path.is_file():
                raise StoreError(f"event references missing blob {digest}")
        for digest, data in available.items():
            self._write_blob(digest, data)
        if status != "duplicate":
            additions = updated[len(chain):]
            with open(self.paths.events, "ab") as handle:
                for item in additions:
                    handle.write(ev.encode_event(item))
                handle.flush()
                os.fsync(handle.fileno())
        return status

    def append(self, event: dict[str, Any], blobs: Mapping[str, bytes] | None = None) -> str:
        """Persist a prebuilt event; callers needing concurrency use ``append_new``."""
        if event.get("run_id") != self.paths.run_id:
            raise StoreError("event belongs to another run")
        lock = self._locked()
        try:
            self._discard_torn_tail()
            return self._append_locked(self.read_events(), event, blobs or {})
        except (ev.EventError, OSError) as exc:
            raise StoreError(str(exc)) from exc
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def append_new(self, *, writer: ev.WriterCapability, kind: str, at: str,
                   payload: Any, **envelope: Any) -> tuple[dict[str, Any], str]:
        """Create and append one event while holding the run lock and current tail."""
        lock = self._locked()
        try:
            self._discard_torn_tail()
            chain = self.read_events()
            event, blobs = ev.make_event(
                writer=writer, run_id=self.paths.run_id, kind=kind, at=at,
                payload=payload,
                prev_hash=ev.event_digest(chain[-1]) if chain else None,
                **envelope,
            )
            return event, self._append_locked(chain, event, blobs)
        except (ev.EventError, OSError) as exc:
            raise StoreError(str(exc)) from exc
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def put_blob(self, data: bytes) -> str:
        """Persist bytes content-addressed and return sha256 under the run lock."""
        if not isinstance(data, bytes):
            raise StoreError("blob data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        lock = self._locked()
        try:
            self._write_blob(digest, data)
            return digest
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def write_contract_once(self, contract: Mapping[str, Any]) -> None:
        """Create immutable contract.json, accepting only an exact replay."""
        lock = self._locked()
        try:
            current = self._read_json(self.paths.contract, missing=None)
            value = dict(contract)
            value.setdefault("run_id", self.paths.run_id)
            value.setdefault("event_authority_id", self.authority.authority_id)
            if value.get("run_id") != self.paths.run_id:
                raise StoreError("contract belongs to another run")
            if "event_authority_secret" in value:
                raise StoreError("authority secret must never be persisted")
            if current is not None and current != value:
                raise StoreError("immutable contract conflict")
            if current is None:
                self._atomic_json(self.paths.contract, value)
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def write_seals(self, seals: Mapping[str, Any]) -> None:
        """Atomically replace derived seal metadata under the run lock."""
        lock = self._locked()
        try:
            value = dict(seals)
            value.setdefault("run_id", self.paths.run_id)
            if value.get("run_id") != self.paths.run_id:
                raise StoreError("seals belong to another run")
            self._atomic_json(self.paths.seals, value)
        finally:
            flock_unlock(lock.fileno())
            lock.close()

    def copy_to_session(self, session_id: str, event: Mapping[str, Any]) -> None:
        """Copy a validated answer event into the owning session audit stream."""
        item = dict(event)
        if item.get("run_id") != self.paths.run_id or item.get("kind") != "answer":
            raise StoreError("session copies accept this run's answer events only")
        try:
            ev.validate_event(item, self.authority)
        except ev.EventError as exc:
            raise StoreError(str(exc)) from exc
        directory = self.paths.session(session_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = directory / ".events.lock"
        with open(lock_path, "a+", encoding="utf-8") as lock:
            flock_exclusive(lock.fileno())
            try:
                events_path = directory / "events.jsonl"
                existing: list[dict[str, Any]] = []
                if events_path.exists():
                    for number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise StoreError(f"corrupt session event line {number}") from exc
                        if not isinstance(row, dict):
                            raise StoreError(f"corrupt session event line {number}")
                        existing.append(row)
                key = (self.paths.run_id, item["event_id"])
                if not any((row.get("run_id"), row.get("event_id")) == key for row in existing):
                    with open(events_path, "ab") as handle:
                        handle.write(ev.encode_event(item))
                        handle.flush()
                        os.fsync(handle.fileno())
                runs_path = directory / "runs.json"
                runs = self._read_json(runs_path, missing=[])
                if not isinstance(runs, list) or any(not isinstance(value, str) for value in runs):
                    raise StoreError("corrupt session runs.json")
                if self.paths.run_id not in runs:
                    self._atomic_json(runs_path, [*runs, self.paths.run_id])
            finally:
                flock_unlock(lock.fileno())
