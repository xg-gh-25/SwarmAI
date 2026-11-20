"""The files a Hive run writes, brought back into the run's Brain (design
docs/design/2026-10-30-hive-run-files-back.md).

**On the Hive** (``stage_files`` then ``seal_files``, from ``hive_records.seal_turn``):
each file the turn's ``turn_deliverables`` block lists is sealed as a ``file`` entry of
the run record whose manifest line carries ``path``, the file's path relative to the
workspace. The file is read once, before the turn's entry, through directory descriptors
opened without following a link and only as a regular file, into a temporary file of the
record; it is sealed from there after the turn's entry. Not sealed: a path outside the
workspace, under ``Hive/`` (repositories come back as bundles, the record and inbox live
there), a secret path, a link, a file over ``hive_records.MAX_BYTES["file"]`` (never cut),
a file that is gone. Each listed file not sealed is named with its reason (one of
``NOT_SEALED``) and, when known, its ``path`` in the turn entry's ``not_sealed`` list,
which a host before it ignores. A file read but then not sealed (sealing it failed after
the turn's entry) has no reason: it holds the run like any file not heard of.

**On the host** (``place``, from ``hive_records.pull`` once an entry is kept and its
sha256 checked): the entry's ``path`` passed ``safe_rel`` at validation; the bytes are
written to ``<Brain dir>/deliverables/hive/<YYYY-MM-DD>-<dispatch>/<path>``, the Brain
being the dispatch row's ``project_agent.brain`` (the General Brain when none),
through no-follow directory descriptors to a fresh name renamed into place
(core/brain_sync); the kept copy in the record is then removed. A file that could not be
placed is tried again at the next pull and its reason kept (``placed``). Returned files
are deliverables, not knowledge: the Library index skips ``deliverables/hive/``
(core/knowledge_store), so nothing a Hive wrote reaches recall except through CDF. After
each pull ``note_not_sealed`` keeps the reasons the new turn entries give
(``not-sealed.json``, the newest per listed path, with its turn's seq); ``not_sealed``
reads them: a ``gone`` one only for a file that never came back in any entry (once it
came, its placement decides), any other one unless the file came back after it.

- ``safe_rel(rel)``                         — a plain relative path a file may come back as
- ``deliverable_paths(rows)``               — absolute paths the rows' blocks list
- ``stage_files(workspace, session, rows)`` — Hive: read them, or say why not; never raises
- ``seal_files(workspace, session, staged)`` — Hive: seal what was read; never raises
- ``destination(workspace, row)``           — host: (Brain dir, the run's folder under it)
- ``place_pending(row, record_dir)``        — host: place every kept entry not placed yet
- ``placed(dispatch_id)``                   — host: where each one went, or why not
- ``note_not_sealed(record_dir)`` / ``not_sealed(dispatch_id)`` — host: why a listed file
  never came
"""
from __future__ import annotations

import errno
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
NOT_SEALED_FILE = "not-sealed.json"
_MAX_REL = 400

#: why a listed file was not sealed (the turn entry's ``not_sealed``). Two let the host stop
#: waiting for it (core/swarm_out_settle._files_here): ``gone`` (the file, or a directory on
#: its way, does not exist: nothing of it is left for the release to remove) and ``hive``
#: (it lies under ``Hive/``, which never comes back, so no wait can bring it). Every other
#: reason may leave bytes that only the Hive has and that a later turn could still return.
NOT_SEALED = ("gone", "link", "not_regular", "unreadable", "too_large", "outside", "hive",
              "secret", "not_returnable", "error")
#: reasons one turn entry gives (a turn_deliverables block lists at most 200 files)
MAX_NOT_SEALED = 200


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
    """A listed file that is not sealed; ``why`` is one of NOT_SEALED, ``rel`` its path
    relative to the workspace once known."""

    def __init__(self, why: str, rel: Optional[str] = None) -> None:
        super().__init__(why)
        self.why = why
        self.rel = rel


def _path_class(rel: str) -> Optional[str]:
    """None for a path that comes back (``safe_rel``), else why it does not."""
    from core.brain_bootstrap.repo_read import is_secret_path
    if safe_rel(rel):
        return None
    parts = rel.split("/")
    if not any(p in ("", ".", "..") for p in parts) and parts[0] == "Hive":
        return "hive"
    return "secret" if rel and is_secret_path(rel) else "not_returnable"


def _copy(workspace: Path, abs_path: str, cap: int, out: Any) -> str:
    """Copy one deliverable into the binary file ``out``; its path relative to the
    workspace, or _Skip with the reason. A missing file or directory on the way
    (ENOENT) is ``gone``; a link there (ELOOP, or ENOTDIR for a file where a directory
    belongs) is ``link``, never ``gone``."""
    from core import brain_sync
    p = os.path.normpath(abs_path)
    # the watcher may report the workspace by its link-resolved name or as configured
    ws = next((w for w in (os.path.abspath(str(workspace)), os.path.realpath(str(workspace)))
               if p.startswith(w + os.sep)), None)
    if ws is None:
        raise _Skip("outside")
    rel = p[len(ws) + 1:].replace(os.sep, "/")
    why = _path_class(rel)
    if why:
        raise _Skip(why)
    root = os.open(os.path.realpath(str(workspace)), brain_sync._DIR_FLAGS)   # rel is the same under either form
    try:
        try:
            dfd, name = brain_sync._walk_to(root, rel, create=False)
        except FileNotFoundError:
            raise _Skip("gone", rel) from None
        except brain_sync.SyncError:
            raise _Skip("link", rel) from None
    finally:
        os.close(root)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                         dir_fd=dfd)
        except FileNotFoundError:
            raise _Skip("gone", rel) from None
        except OSError as exc:
            raise _Skip("link" if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)) else "unreadable",
                        rel) from None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise _Skip("not_regular", rel)
            if st.st_size > cap:
                raise _Skip("too_large", rel)
            size = 0
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                size += len(b)
                if size > cap:
                    raise _Skip("too_large", rel)
                out.write(b)
            return rel
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


def stage_files(workspace: Path, session_id: str, rows: list[dict]) -> tuple[list[tuple[str, str, Path]], list[dict]]:
    """Read each deliverable the rows list, and the report of each pipeline run that
    ended in them (``report_paths``), into a temporary file of the session's record, once,
    before the turn's entry is sealed: ``(staged, not_sealed)``. ``staged``:
    ``[(listed path, path relative to the workspace, temporary file)]`` for ``seal_files``;
    ``not_sealed``: ``[{"listed", "why"[, "path"]}]`` for each listed file that is not
    read, ``why`` one of NOT_SEALED, ``path`` relative to the workspace when it is one (a
    report no turn lists is only logged), at most MAX_NOT_SEALED.
    Never raises: what could not be staged is not sealed."""
    from core import hive_records
    staged: list[tuple[str, str, Path]] = []
    not_sealed: list[dict] = []
    try:
        listed = deliverable_paths(rows)
        try:
            reports = report_paths(workspace, rows)
        except OSError:
            reports = []
        for ap in [*listed, *[r for r in reports if r not in listed]]:
            tmp: Optional[Path] = None
            why: Optional[str] = None
            rel: Optional[str] = None
            try:
                if len(ap) > hive_records.LISTED_MAX:
                    raise _Skip("not_returnable")
                tmp = hive_records.tmp_path(workspace, session_id, "file")
                with open(tmp, "xb") as out:
                    rel = _copy(Path(workspace), ap, hive_records.MAX_BYTES["file"], out)
                staged.append((ap, rel, tmp))
                continue
            except _Skip as skip:
                why, rel = skip.why, skip.rel
                logger.info("hive_files: %s not sealed for %s (%s)", os.path.basename(ap), session_id, why)
            except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
                why = "error"
                logger.warning("hive_files: %s not sealed for %s (%s)", os.path.basename(ap), session_id,
                               type(exc).__name__)
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            if ap in listed and len(ap) <= hive_records.LISTED_MAX and len(not_sealed) < MAX_NOT_SEALED:
                not_sealed.append({"listed": ap, "why": why, **({"path": rel} if rel else {})})
    except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
        logger.warning("hive_files: files of %s not staged (%s)", session_id, type(exc).__name__)
    return staged, not_sealed


def seal_files(workspace: Path, session_id: str, staged: list[tuple[str, str, Path]]) -> list[dict]:
    """Seal each file ``stage_files`` read as a ``file`` entry (its temporary file is moved
    into the record); the sealed entries. Never raises; a temporary file left over is
    removed (``discard_staged``)."""
    from core import hive_records
    out = []
    for ap, rel, tmp in staged:
        try:
            out.append(hive_records.append(workspace, session_id, "file", src=tmp, path=rel, listed=ap))
        except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
            logger.warning("hive_files: %s not sealed for %s (%s)", os.path.basename(ap), session_id,
                           type(exc).__name__)
    discard_staged(staged)
    return out


def discard_staged(staged: list[tuple[str, str, Path]]) -> None:
    """Remove the temporary files of ``staged`` not moved into the record. Never raises."""
    for _ap, _rel, tmp in staged:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass


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


def _valid_not_sealed(items: Any) -> list[dict]:
    """The well-formed ``{listed, why[, path]}`` items of a turn entry's ``not_sealed``
    (the Hive wrote them: anything else is dropped, so its file still holds)."""
    from core import hive_records
    out = []
    for x in (items if isinstance(items, list) else [])[:MAX_NOT_SEALED]:
        if isinstance(x, dict) and isinstance(x.get("listed"), str) and x["listed"] \
                and len(x["listed"]) <= hive_records.LISTED_MAX and x.get("why") in NOT_SEALED:
            path = x.get("path")
            out.append({"listed": x["listed"], "why": x["why"],
                        **({"path": path} if isinstance(path, str) and safe_rel(path) else {})})
    return out


def _load_not_sealed(d: Path) -> tuple[int, dict[str, dict]]:
    """(the last seq read, ``{listed: {why, seq[, path]}}``) of ``not-sealed.json``."""
    try:
        got = json.loads((d / NOT_SEALED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, {}
    if not isinstance(got, dict):
        return 0, {}
    through = got.get("through") if type(got.get("through")) is int else 0
    why = got.get("why") if isinstance(got.get("why"), dict) else {}
    return through, {k: v for k, v in why.items() if isinstance(k, str) and isinstance(v, dict)
                     and v.get("why") in NOT_SEALED and type(v.get("seq")) is int}


def note_not_sealed(record_dir: Path) -> dict[str, dict]:
    """Keep the reasons the kept turn entries not read yet give for their listed files not
    sealed: ``not-sealed.json`` = ``{through: the last seq read, why: {listed: {why, seq,
    path?}}}``, ``seq`` the turn entry's, a later turn's reason replacing an earlier one's.
    A turn kept without its bytes (over the record's budget) gives none; one whose body
    cannot be read stops the reading there, so the next pull reads it again. Written
    under the record's lock; never raises."""
    from core import hive_records
    d = Path(record_dir)
    try:
        with hive_records._Lock(d, ".not-sealed.lock"):
            through, why = _load_not_sealed(d)
            new = [e for e in hive_records._read_manifest(d) if e["seq"] > through]
            read_to = through
            for e in new:
                if e.get("kind") == "turn" and not e.get("skipped"):
                    p = hive_records._entry_path(d, e)
                    try:
                        body = json.loads(p.read_text(encoding="utf-8")) if p is not None else None
                    except (OSError, ValueError):
                        break                    # read again at the next pull
                    for x in _valid_not_sealed(body.get("not_sealed") if isinstance(body, dict) else None):
                        why[x["listed"]] = {"why": x["why"], "seq": e["seq"],
                                            **({"path": x["path"]} if "path" in x else {})}
                read_to = e["seq"]
            if read_to == through:
                return why
            tmp = d / f".{NOT_SEALED_FILE}.{secrets.token_hex(6)}.tmp"
            tmp.write_text(json.dumps({"through": read_to, "why": why}, ensure_ascii=False,
                                      sort_keys=True), encoding="utf-8")
            os.replace(tmp, d / NOT_SEALED_FILE)
            return why
    except Exception as exc:  # noqa: BLE001 — the record's state never breaks the pull
        logger.warning("hive_files: reasons of %s not kept (%s)", d.name, type(exc).__name__)
        return {}


def not_sealed(dispatch_id: str) -> dict[str, str]:
    """``{listed path: reason}`` of the run's listed files whose newest word from its Hive
    is that it did not seal them (``note_not_sealed``). A ``gone`` file that came back in
    any kept ``file`` entry is left out: its bytes came here, so its placement decides. Any
    other reason is left out only when the file came back after it (a newer copy). A file
    matches an entry by the path it was listed by or by its path in the workspace (the
    watcher may name the workspace by its link-resolved form)."""
    from core import hive_records
    try:
        d = hive_records.local_dir(dispatch_id)
    except ValueError:
        return {}
    _through, kept = _load_not_sealed(d)
    if not kept:
        return {}
    came: dict[str, int] = {}                    # listed or path -> newest file entry's seq
    for e in hive_records._read_manifest(d):
        if e.get("kind") == "file":
            for k in (e.get("listed"), e.get("path")):
                if isinstance(k, str):
                    came[k] = max(came.get(k, 0), e["seq"])
    out: dict[str, str] = {}
    for listed, r in kept.items():
        last = max((came.get(k, 0) for k in (listed, r.get("path")) if isinstance(k, str)), default=0)
        if (r["why"] == "gone" and last) or last > r["seq"]:
            continue
        out[listed] = r["why"]
    return out
