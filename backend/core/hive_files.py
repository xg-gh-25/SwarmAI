"""The files a Hive run writes, brought back into the run's Brain (design
docs/design/2026-10-30-hive-run-files-back.md).

**On the Hive** (``seal_files``, from ``hive_records.seal_turn``): each file the turn's
``turn_deliverables`` block lists is sealed as a ``file`` entry of the run record whose
manifest line carries ``path``, the file's path relative to the workspace. The file is
read through directory descriptors opened without following a link, and only as a
regular file. Not sealed (logged): a path outside the workspace, under ``Hive/``
(repositories come back as bundles, the record and inbox live there), a secret path, a
file over ``hive_records.MAX_BYTES["file"]`` (never cut).

**On the host** (``place``, from ``hive_records.pull`` once an entry is kept and its
sha256 checked): the entry's ``path`` passed ``safe_rel`` at validation; the bytes are
written to ``<Brain dir>/deliverables/hive/<YYYY-MM-DD>-<dispatch>/<path>``, the Brain
being the dispatch row's ``project_agent.brain`` (the General Brain when none),
through no-follow directory descriptors to a fresh name renamed into place
(core/brain_sync); the kept copy in the record is then removed. A file that could not be
placed is tried again at the next pull and its reason kept (``placed``). Returned files
are deliverables, not knowledge: the Library index skips ``deliverables/hive/``
(core/knowledge_store), so nothing a Hive wrote reaches recall except through CDF.

- ``safe_rel(rel)``                         — a plain relative path a file may come back as
- ``deliverable_paths(rows)``               — absolute paths the rows' blocks list
- ``seal_files(workspace, session, rows)``  — Hive: seal them; never raises
- ``destination(workspace, row)``           — host: (Brain dir, the run's folder under it)
- ``place_pending(row, record_dir)``        — host: place every kept entry not placed yet
- ``placed(dispatch_id)``                   — host: where each one went, or why not
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: where returned files go, under the Brain's directory
DELIVERABLES = ("deliverables", "hive")
PLACED = "placed.json"
_MAX_REL = 400


def is_returned(parts: Any) -> bool:
    """True for a path (its parts) under a ``deliverables/hive/`` folder: a file a Hive run
    brought back. Readers of knowledge skip it (Library index, CDF's corpus): it is a
    deliverable on disk, and what a Hive wrote reaches cognition only through CDF."""
    parts = tuple(parts)
    return any(parts[k:k + 2] == DELIVERABLES for k in range(len(parts) - 1))


def safe_rel(rel: Any) -> bool:
    """A ``/``-separated relative path with plain segments, not secret, not under ``Hive/``."""
    from core.brain_bootstrap.repo_read import is_secret_path
    if not isinstance(rel, str) or not rel or len(rel) > _MAX_REL or rel.startswith("/") \
            or "\\" in rel or "\0" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] == "Hive":
        return False
    return not is_secret_path(rel)


def deliverable_paths(rows: list[dict]) -> list[str]:
    """The ``absolutePath`` of each file the rows' ``turn_deliverables`` blocks list, in
    order, each once (a later listing of the same file is the same file)."""
    out: list[str] = []
    for r in rows or []:
        content = r.get("content") if isinstance(r, dict) else None
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict) or b.get("type") != "turn_deliverables":
                continue
            for f in b.get("files") if isinstance(b.get("files"), list) else []:
                ap = f.get("absolutePath") if isinstance(f, dict) else None
                if isinstance(ap, str) and ap and ap not in out:
                    out.append(ap)
    return out


class _Skip(Exception):
    pass


def _read(workspace: Path, abs_path: str, cap: int) -> tuple[str, bytes]:
    """(path relative to the workspace, bytes) of one deliverable; _Skip with the reason."""
    from core import brain_sync
    p = os.path.normpath(abs_path)
    # the watcher may report the workspace by its link-resolved name or as configured
    ws = next((w for w in (os.path.abspath(str(workspace)), os.path.realpath(str(workspace)))
               if p.startswith(w + os.sep)), None)
    if ws is None:
        raise _Skip("outside the workspace")
    rel = p[len(ws) + 1:].replace(os.sep, "/")
    if not safe_rel(rel):
        raise _Skip("not a path that comes back")
    root = os.open(os.path.realpath(str(workspace)), brain_sync._DIR_FLAGS)   # rel is the same under either form
    try:
        try:
            dfd, name = brain_sync._walk_to(root, rel, create=False)
        except (brain_sync.SyncError, FileNotFoundError):
            raise _Skip("a directory on the way is a link or gone") from None
    finally:
        os.close(root)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                         dir_fd=dfd)
        except OSError:
            raise _Skip("gone or a link") from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise _Skip("not a regular file")
            if st.st_size > cap:
                raise _Skip("larger than a record holds")
            chunks, size = [], 0
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                size += len(b)
                if size > cap:
                    raise _Skip("larger than a record holds")
                chunks.append(b)
            return rel, b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


_RUN_ID_RE = re.compile(r"run_[0-9a-f]{8,32}")


def report_paths(workspace: Path, rows: list[dict]) -> list[str]:
    """The ``REPORT.md`` of each pipeline run that ended in these rows (a
    ``pipeline_summary`` block names it; the run engine writes it, so no
    ``turn_deliverables`` lists it), at ``Projects/<P>/.artifacts/runs/<id>/`` (design
    dispatch-center §3: the run's report comes back with the run)."""
    ids: list[str] = []
    for r in rows or []:
        content = r.get("content") if isinstance(r, dict) else None
        for b in content if isinstance(content, list) else []:
            rid = b.get("runId") if isinstance(b, dict) and b.get("type") == "pipeline_summary" else None
            if isinstance(rid, str) and _RUN_ID_RE.fullmatch(rid) and rid not in ids:
                ids.append(rid)
    out: list[str] = []
    for rid in ids:
        for p in sorted(Path(workspace).glob(f"Projects/*/.artifacts/runs/{rid}/REPORT.md"))[:1]:
            out.append(str(p))
    return out


def seal_files(workspace: Path, session_id: str, rows: list[dict]) -> list[dict]:
    """Seal each deliverable the rows list, and the report of each pipeline run that
    ended in them (``report_paths``), as a ``file`` entry; the sealed entries. Never
    raises."""
    from core import hive_records
    out = []
    try:
        reports = report_paths(workspace, rows)
    except OSError:
        reports = []
    for ap in [*deliverable_paths(rows), *[r for r in reports if r not in deliverable_paths(rows)]]:
        try:
            rel, data = _read(Path(workspace), ap, hive_records.MAX_BYTES["file"])
            out.append(hive_records.append(workspace, session_id, "file", data=data, path=rel, listed=ap))
        except _Skip as why:
            logger.info("hive_files: %s not sealed for %s (%s)", os.path.basename(ap), session_id, why)
        except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
            logger.warning("hive_files: %s not sealed for %s (%s)", os.path.basename(ap), session_id,
                           type(exc).__name__)
    return out


# ── host ───────────────────────────────────────────────────────────────────────

def destination(workspace: Path, row: dict) -> tuple[Path, str]:
    """(the Brain's directory here, the run's folder relative to it). The row's Brain,
    or the General Brain when it names none or that Brain is no longer here (a
    directory is never made for a Brain that does not exist: every directory under
    Projects/ reads as a project). Raises brain_sync.SyncError for a bad Brain name."""
    from core import brain_sync
    from core.project_agent import GENERAL_BRAIN
    pa = row.get("project_agent") if isinstance(row.get("project_agent"), dict) else {}
    brain = pa.get("brain") if isinstance(pa.get("brain"), str) and pa.get("brain") else GENERAL_BRAIN
    root, _general = brain_sync.brain_root(workspace, brain)
    if not root.is_dir():
        root, _general = brain_sync.brain_root(workspace, GENERAL_BRAIN)
    day = str(row.get("sent_at") or "")[:10] or "undated"
    return root, "/".join((*DELIVERABLES, f"{day}-{row.get('dispatch_id')}"))


def _place_one(ws: Path, row: dict, src: Path, rel: str) -> str:
    from core import brain_sync
    if not safe_rel(rel):
        raise ValueError("not a path that comes back")
    root, folder = destination(ws, row)
    data = src.read_bytes()
    pfd = os.open(str(root.parent), brain_sync._DIR_FLAGS)
    try:
        rfd = brain_sync._open_dir(pfd, root.name, create=False)
    finally:
        os.close(pfd)
    try:
        brain_sync._write(rfd, f"{folder}/{rel}", data)
    finally:
        os.close(rfd)
    return str((root / folder / rel).relative_to(ws))


def place_pending(row: dict, record_dir: Path) -> dict[str, dict]:
    """Place every kept ``file`` entry not placed yet (a failure is tried again on the
    next pull) and remove the kept copy of each one placed. ``placed.json`` =
    ``{seq: {path, listed, local, error}}``, written under the record's lock. Never raises."""
    from core import hive_records
    from core.project_registry import get_swarmws
    d = Path(record_dir)
    try:
        with hive_records._Lock(d, ".placed.lock"):
            done = _load(d)
            ws = Path(get_swarmws())
            changed = False
            fresh: list[str] = []
            for e in hive_records._read_manifest(d):
                if e.get("kind") != "file" or (done.get(str(e["seq"])) or {}).get("local"):
                    continue
                src = d / hive_records._name(e["seq"], "file")
                if not src.is_file() or src.is_symlink():
                    continue                     # skipped when taken (over the budget)
                info = {"path": e.get("path"), "listed": e.get("listed"), "local": None, "error": None}
                try:
                    info["local"] = _place_one(ws, row, src, e.get("path"))
                    src.unlink()                 # the copy in the Brain is the one kept
                    fresh.append(info["local"])
                except Exception as exc:  # noqa: BLE001 — one file never stops the pull
                    info["error"] = type(exc).__name__
                    logger.warning("hive_files: %s of %s not placed (%s: %s)", e["seq"],
                                   row.get("dispatch_id"), type(exc).__name__, str(exc)[:120])
                done[str(e["seq"])] = info
                changed = True
            if changed:
                tmp = d / f".{PLACED}.{secrets.token_hex(6)}.tmp"
                tmp.write_text(json.dumps(done, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                os.replace(tmp, d / PLACED)
                if fresh and row.get("dispatch_id"):
                    # landing a run's files on this host is the product's own decision
                    # (design 2026-11-02-hive-run-decision-record §3)
                    from core import swarm_out_record
                    swarm_out_record.append_quietly(row["dispatch_id"], "product", "place_files",
                                                    f"{len(fresh)} file(s) placed in the Brain")
            return done
    except Exception as exc:  # noqa: BLE001 — the record's state never breaks the pull
        logger.warning("hive_files: placing for %s failed (%s)", row.get("dispatch_id"), type(exc).__name__)
        return {}


def _load(d: Path) -> dict[str, dict]:
    try:
        done = json.loads((d / PLACED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in done.items() if isinstance(v, dict)} if isinstance(done, dict) else {}


def placed(dispatch_id: str) -> list[dict]:
    """``[{path, listed, local, error}]`` of the run's returned files (``local`` and
    ``error`` workspace-relative path / reason, one of them set)."""
    from core import hive_records
    try:
        return list(_load(hive_records.local_dir(dispatch_id)).values())
    except ValueError:
        return []
