"""Snapshot, verify, and restore CDF cutover state.

- ``snapshot`` captures governed files and ingestion.db with byte hashes.
- ``verify`` checks a snapshot without changing live state.
- ``restore`` requires the CDF owner lock to be free and restores exact bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_lock import flock_exclusive_nb, flock_unlock


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _snapshot_root(data_dir: Path) -> Path:
    return data_dir.resolve(strict=False) / "state" / "cutover-snapshot"


def _latest_snapshot(data_dir: Path) -> Path:
    root = _snapshot_root(data_dir)
    candidates = sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    if not candidates:
        raise FileNotFoundError("no cutover snapshot exists")
    return candidates[-1]


def _select_snapshot(data_dir: Path, value: Path | None) -> Path:
    selected = value.resolve(strict=False) if value else _latest_snapshot(data_dir)
    root = _snapshot_root(data_dir)
    if not _within(selected, root) or selected.parent != root:
        raise ValueError("snapshot must be a direct child of the cutover snapshot root")
    return selected


def _governed(workspace: Path) -> list[Path]:
    from config import get_swarmws
    from core.ingestion.materializer import iter_governed_files

    actual = Path(get_swarmws()).resolve(strict=False)
    if actual != workspace.resolve(strict=False):
        raise ValueError("workspace must match the configured SWARM_WORKSPACE")
    return sorted(iter_governed_files())


def snapshot(data_dir: Path, workspace: Path) -> Path:
    """Capture current governed bytes and a WAL-consistent ingestion database."""
    data = data_dir.resolve(strict=False)
    ws = workspace.resolve(strict=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = _snapshot_root(data) / stamp
    destination.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    for source in _governed(ws):
        relative = source.resolve().relative_to(ws).as_posix()
        payload = source.read_bytes()
        target = destination / "workspace" / relative
        _atomic_bytes(target, payload)
        entries.append({"root": "workspace", "path": relative,
                        "sha256": _digest(payload), "size": len(payload)})

    database = data / "ingestion.db"
    if not database.is_file():
        raise FileNotFoundError(database)
    db_target = destination / "data" / "ingestion.db"
    db_target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(db_target) as target_db:
            source_db.backup(target_db)
    db_payload = db_target.read_bytes()
    entries.append({"root": "data", "path": "ingestion.db",
                    "sha256": _digest(db_payload), "size": len(db_payload)})

    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(ws),
        "data_dir": str(data),
        "files": sorted(entries, key=lambda item: (item["root"], item["path"])),
    }
    _atomic_bytes(
        destination / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    verify(data, ws, snapshot_dir=destination)
    return destination


def _manifest(data_dir: Path, workspace: Path, snapshot_dir: Path) -> dict[str, Any]:
    root = _snapshot_root(data_dir)
    if not _within(snapshot_dir, root) or snapshot_dir.parent != root:
        raise ValueError("snapshot escapes the cutover snapshot root")
    value = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("files"), list):
        raise ValueError("unsupported cutover snapshot manifest")
    if Path(str(value.get("workspace"))).resolve(strict=False) != workspace.resolve(strict=False):
        raise ValueError("snapshot belongs to a different workspace")
    if Path(str(value.get("data_dir"))).resolve(strict=False) != data_dir.resolve(strict=False):
        raise ValueError("snapshot belongs to a different data directory")
    return value


def verify(data_dir: Path, workspace: Path, *, snapshot_dir: Path | None = None) -> dict[str, Any]:
    """Verify every manifest entry and reject paths outside the snapshot roots."""
    data = data_dir.resolve(strict=False)
    ws = workspace.resolve(strict=False)
    selected = _select_snapshot(data, snapshot_dir)
    manifest = _manifest(data, ws, selected)
    for entry in manifest["files"]:
        root_name = entry.get("root")
        if root_name not in {"workspace", "data"}:
            raise ValueError("invalid manifest root")
        relative = Path(str(entry.get("path", "")))
        snapshot_root = selected / root_name
        source = (snapshot_root / relative).resolve(strict=False)
        if relative.is_absolute() or not _within(source, snapshot_root):
            raise ValueError("manifest path escapes snapshot root")
        payload = source.read_bytes()
        if len(payload) != int(entry.get("size", -1)) or _digest(payload) != entry.get("sha256"):
            raise ValueError(f"snapshot hash mismatch: {root_name}/{relative.as_posix()}")
    return manifest


def _live_path(entry: dict[str, Any], data: Path, workspace: Path) -> Path:
    root = workspace if entry["root"] == "workspace" else data
    relative = Path(str(entry["path"]))
    target = (root / relative).resolve(strict=False)
    if relative.is_absolute() or not _within(target, root):
        raise ValueError("manifest path escapes live root")
    return target


def restore(data_dir: Path, workspace: Path, *, snapshot_dir: Path | None = None) -> Path:
    """Restore the selected snapshot only while no CDF owner holds its lock."""
    data = data_dir.resolve(strict=False)
    ws = workspace.resolve(strict=False)
    selected = _select_snapshot(data, snapshot_dir)
    manifest = verify(data, ws, snapshot_dir=selected)
    lock_path = data / "state" / "ingestion_owner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    try:
        try:
            flock_exclusive_nb(lock_file)
        except (BlockingIOError, OSError) as exc:
            raise RuntimeError("CDF owner lock is live; stop the backend before restore") from exc

        expected_workspace = {
            str(entry["path"]) for entry in manifest["files"] if entry["root"] == "workspace"
        }
        for current in _governed(ws):
            relative = current.resolve().relative_to(ws).as_posix()
            if relative not in expected_workspace:
                current.unlink()
        for entry in manifest["files"]:
            source = selected / entry["root"] / str(entry["path"])
            target = _live_path(entry, data, ws)
            _atomic_bytes(target, source.read_bytes())
        for sidecar in (data / "ingestion.db-wal", data / "ingestion.db-shm"):
            sidecar.unlink(missing_ok=True)
        for entry in manifest["files"]:
            target = _live_path(entry, data, ws)
            if _digest(target.read_bytes()) != entry["sha256"]:
                raise OSError(f"restored hash mismatch: {entry['root']}/{entry['path']}")
        return selected
    finally:
        try:
            flock_unlock(lock_file)
        except OSError:
            pass
        lock_file.close()


def main(argv: list[str] | None = None) -> int:
    """Run one cutover-state operation and print its selected snapshot path."""
    parser = argparse.ArgumentParser(description="Manage CDF cutover snapshots")
    parser.add_argument("action", choices=("snapshot", "verify", "restore"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--snapshot", dest="snapshot_dir", type=Path)
    args = parser.parse_args(argv)
    os.environ["SWARM_DATA_DIR"] = str(args.data_dir.resolve(strict=False))
    os.environ["SWARM_WORKSPACE"] = str(args.workspace.resolve(strict=False))
    if args.action == "snapshot":
        selected = snapshot(args.data_dir, args.workspace)
    elif args.action == "verify":
        selected = _select_snapshot(args.data_dir.resolve(strict=False), args.snapshot_dir)
        verify(args.data_dir, args.workspace, snapshot_dir=selected)
    else:
        selected = restore(args.data_dir, args.workspace, snapshot_dir=args.snapshot_dir)
    print(json.dumps({"status": "ok", "snapshot": str(selected)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
