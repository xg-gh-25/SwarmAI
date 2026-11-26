"""Pollinate works, read for the overlay: the work rows, one work's view and the eval.

Everything here is computed from files a registered work already holds, with no model
call and no second store (design: the Pollinate overlay mockup of 2026-11-26):

- ``<workspace>/.pollinate/works.json`` — the registry (absolute work folder paths, written
  by ``s_pollinate/scripts/work.py``);
- ``<work>/work.json`` — kind, title, size, slides, ``current``, the ``open`` revision
  (scope, note, base, session) and ``versions`` (id, by, status, at, scope, base, session);
- ``<work>/.versions/<id>/checks.json`` and ``guard.json`` — each cut's craft findings and
  guard result;
- ``<work>/brief.md``, ``<work>/out/`` (outputs, ``slides/NN-<id>.png``), and for a video
  ``storyboard.json`` and its scene frames (``out/`` or ``review/``, ``<scene>-*.jpg``).

A work is **in progress** while it is being made: a deck, poster, page or copy with an open
revision or with no passed version yet, and a video with no film (``out/*.mp4``); a work of
any other kind never is. A video's stage is read from the same files, in order brief →
storyboard → scenes → film. A video skill may register a video as soon as its brief is
written (``work.py adopt --kind video``); one registered after its film is listed as done.
A video's title, size and style come from work.json, else from its storyboard.json (a
video registered at its brief has no storyboard yet when work.json is written).

Reads are limited to registered folders: ``work_view`` looks a path up in the registry and
refuses any other (no file outside a registered work is opened), and every file inside a
work is resolved and checked to stay inside it (a symlink pointing out is not followed).

Public functions (all sync; the router runs them on ``executors.run_nav``):
- ``read_works(ws_root)`` -> {"works": [row], "skipped": n}
- ``work_view(ws_root, path)`` -> view dict, or None when ``path`` is not registered
- ``eval_summary(ws_root, now=None, days=15)`` -> eval dict

A revision (for the first-pass rate) is the agent versions that share one ``base``: work.py
writes the open revision's base into every cut it makes, so the first of them in list
order is the revision's first cut. "Versions by the person" are absorbed hand edits
(``by: user`` with ``found_on_disk``); the adopted v001 is the starting file, not an edit.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REGISTRY = (".pollinate", "works.json")
VIDEO = "video"
# A registry, work.json, checks.json or guard.json larger than this is not a real one.
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_BRIEF_BYTES = 16 * 1024
MAX_FINDINGS = 200

MAIN_OUTPUTS: dict[str, tuple[str, ...]] = {
    "deck": ("out/deck.html", "out/deck.pdf"),
    "poster": ("out/poster.png",),
    "page": ("out/page.html", "out/page.png"),
    "copy": ("src/copy.md",),
}
THUMBNAILS: dict[str, str] = {"poster": "out/poster.png", "page": "out/page.png"}


# A video's scene id as it may appear in a frame's file name (S01, s-02_intro): it is
# matched against file names, so nothing that could walk out of a folder.
SCENE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


# ── small readers ──────────────────────────────────────────────────────────

def _read_bounded(p: Path, limit: int, *, truncate: bool = False) -> bytes:
    """At most ``limit`` bytes of a REGULAR file. A device, FIFO or socket is refused
    before a byte is read (O_NONBLOCK, so opening a FIFO never waits); a file larger than
    ``limit`` raises ValueError, or is cut to ``limit`` when ``truncate``."""
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{p.name} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        if not truncate:
            raise ValueError(f"{p.name} is larger than {limit} bytes")
        data = data[:limit]
    return data


def _read_json(p: Path) -> Any:
    """Parse a small JSON file; raises OSError / ValueError when missing, huge, not a
    regular file or not JSON."""
    return json.loads(_read_bounded(p, MAX_JSON_BYTES).decode("utf-8"))


def _json_or_none(p: Path) -> Any:
    try:
        return _read_json(p)
    except (OSError, ValueError):
        return None


def _s(v: object) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _strs(v: object) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _inside(p: Path, root: Path) -> Optional[Path]:
    """``p`` resolved when it exists and stays inside ``root`` (already resolved), else None."""
    try:
        r = p.resolve()
        r.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return None
    return r if r.exists() else None


def _first_file(work_dir: Path, rels: tuple[str, ...]) -> Optional[Path]:
    for rel in rels:
        p = _inside(work_dir / rel, work_dir)
        if p is not None and p.is_file():
            return p
    return None


def _brain(work_dir: Path, ws_root: Path) -> str:
    """The project a work belongs to: Projects/<P>/… → P; anything else → General."""
    try:
        parts = work_dir.relative_to(ws_root).parts
    except ValueError:
        return "General"
    return parts[1] if len(parts) > 2 and parts[0] == "Projects" else "General"


# ── the registry ───────────────────────────────────────────────────────────

def registered_dirs(ws_root: Path) -> tuple[list[Path], int]:
    """The registered work folders (resolved, existing, de-duplicated, registry order) and
    how many entries were left out (not absolute, missing, or a broken registry)."""
    reg_path = ws_root.joinpath(*REGISTRY)
    if not reg_path.is_file():
        return [], 0
    try:
        reg = _read_json(reg_path)
    except (OSError, ValueError) as e:
        logger.warning("pollinate: works registry unreadable: %s", e)
        return [], 1
    entries = reg.get("works") if isinstance(reg, dict) else None
    if not isinstance(entries, list):
        logger.warning("pollinate: works registry has no works list")
        return [], 1
    out: list[Path] = []
    seen: set[Path] = set()
    skipped = 0
    for e in entries:
        raw = e.get("path") if isinstance(e, dict) else None
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            skipped += 1
            continue
        try:
            d = Path(raw).resolve()
        except (OSError, RuntimeError):
            skipped += 1
            continue
        if not d.is_dir():
            skipped += 1
            continue
        if d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out, skipped


def _registry_updated(ws_root: Path) -> dict[Path, str]:
    """work folder → the registry's ``updated`` stamp (when the entry has one)."""
    data = _json_or_none(ws_root.joinpath(*REGISTRY))
    out: dict[Path, str] = {}
    for e in (data.get("works") if isinstance(data, dict) and isinstance(data.get("works"), list) else []):
        if isinstance(e, dict) and _s(e.get("path")) and _s(e.get("updated")):
            try:
                out[Path(e["path"]).resolve()] = e["updated"]
            except (OSError, RuntimeError):
                continue
    return out


# ── one work ───────────────────────────────────────────────────────────────

def _work_json(work_dir: Path, name: str) -> Any:
    """A JSON file directly in the work folder, only when it resolves inside it."""
    p = _inside(work_dir / name, work_dir)
    return _json_or_none(p) if p is not None and p.is_file() else None


def _meta(work_dir: Path) -> Optional[dict]:
    m = _work_json(work_dir, "work.json")
    return m if isinstance(m, dict) else None


def _versions(meta: dict) -> list[dict]:
    """The version summary, in work.json order (the writer appends)."""
    out = []
    for v in meta.get("versions") if isinstance(meta.get("versions"), list) else []:
        if not isinstance(v, dict) or not _s(v.get("id")):
            continue
        st = v.get("status")
        out.append({
            "id": v["id"],
            "by": "user" if v.get("by") == "user" else "agent",
            "status": st if st in ("ok", "failed") else None,
            "at": _s(v.get("at")),
            "scope": _strs(v.get("scope")),
            "base": _s(v.get("base")),
            "note": _s(v.get("note")),
            "found_on_disk": bool(v.get("found_on_disk")),
            "session": _s(v.get("session")),
        })
    return out


def _open(meta: dict) -> Optional[dict]:
    op = meta.get("open")
    if not isinstance(op, dict):
        return None
    add = op.get("add")
    return {
        "scope": _strs(op.get("scope")),
        "remove": _strs(op.get("remove")),
        "add": add if isinstance(add, int) and add >= 0 else 0,
        "note": _s(op.get("note")),
        "at": _s(op.get("at")),
        "base": _s(op.get("base")),
        "session": _s(op.get("session")),
    }


def _size(meta: dict) -> tuple[Optional[int], Optional[int]]:
    sz = meta.get("size")
    if isinstance(sz, dict) and isinstance(sz.get("w"), int) and isinstance(sz.get("h"), int):
        return sz["w"], sz["h"]
    return None, None


def _seconds(v: object) -> Optional[float]:
    """A non-negative duration: a number, or a string holding one ("4.533"), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            return None
    if isinstance(v, (int, float)) and v >= 0 and v == v and v != float("inf"):
        return float(v)
    return None


def _storyboard(work_dir: Path) -> list[dict]:
    """A video's scenes: id, title, start and duration (seconds), from storyboard.json."""
    board = _work_json(work_dir, "storyboard.json")
    scenes = board.get("scenes") if isinstance(board, dict) else None
    out, start = [], 0.0
    for s in scenes if isinstance(scenes, list) else []:
        if not isinstance(s, dict) or not _s(s.get("id")) or not SCENE_ID.fullmatch(s["id"]):
            continue
        dur = _seconds(s.get("duration"))
        out.append({"id": s["id"], "title": _s(s.get("title")), "start_s": round(start, 2), "duration_s": dur})
        start += dur or 0.0
    return out


def _scene_frame(work_dir: Path, scene_id: str) -> Optional[Path]:
    """The first ``<scene_id>-*.jpg`` in out/ then review/ (names matched, never globbed)."""
    for sub in ("out", "review"):
        d = _inside(work_dir / sub, work_dir)
        if d is None or not d.is_dir():
            continue
        try:
            hits = sorted(f for f in d.iterdir()
                          if f.name.startswith(f"{scene_id}-") and f.name.lower().endswith(".jpg") and f.is_file())
        except OSError:
            hits = []
        for h in hits:
            r = _inside(h, work_dir)
            if r is not None:
                return r
    return None


def _video_main(work_dir: Path) -> Optional[Path]:
    d = _inside(work_dir / "out", work_dir)
    if d is None or not d.is_dir():
        return None
    try:
        mp4s = sorted(f for f in d.glob("*.mp4") if f.is_file())
    except OSError:
        return None
    for f in mp4s:
        r = _inside(f, work_dir)
        if r is not None:
            return r
    return None


def _video_thumb(work_dir: Path, scenes: list[dict]) -> Optional[Path]:
    for s in scenes:
        f = _scene_frame(work_dir, s["id"])
        if f is not None:
            return f
    for sub in ("out", "review"):
        d = _inside(work_dir / sub, work_dir)
        if d is not None and d.is_dir():
            try:
                imgs = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"))
            except OSError:
                imgs = []
            for f in imgs:
                r = _inside(f, work_dir)
                if r is not None:
                    return r
    return None


def _mtime_iso(p: Optional[Path]) -> Optional[str]:
    if p is None:
        return None
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _newest(*stamps: Optional[str]) -> Optional[str]:
    """The latest of some ISO stamps (compared as instants; an unreadable one is ignored)."""
    best, best_at = None, None
    for st in stamps:
        at = _when(st)
        if at is not None and (best_at is None or at > best_at):
            best, best_at = st, at
    return best


def _video_progress(work_dir: Path, scenes: list[dict], main: Optional[Path]) -> dict:
    """How far a video has got, from its files: the stage reached (brief, storyboard,
    scenes, film; None when it has none of them), the storyboard's scene count and how many
    of those scenes have a frame."""
    framed = sum(1 for s in scenes if _scene_frame(work_dir, s["id"]) is not None)
    if main is not None:
        stage = "film"
    elif framed:
        stage = "scenes"
    elif scenes or _first_file(work_dir, ("storyboard.json",)) is not None:
        stage = "storyboard"
    elif _first_file(work_dir, ("brief.md",)) is not None:
        stage = "brief"
    else:
        stage = None
    return {"stage": stage, "scenes": len(scenes), "framed": framed}


def _deck_thumb(work_dir: Path) -> Optional[Path]:
    slides = _inside(work_dir / "out" / "slides", work_dir)
    if slides is None or not slides.is_dir():
        return None
    try:
        hits = sorted(f for f in slides.glob("01-*.png") if f.is_file())
    except OSError:
        hits = []
    return _inside(hits[0], work_dir) if hits else None


class _Shown:
    """Path display for one work: workspace-relative inside the workspace (what
    /api/workspace/file/raw and the Canvas open take), absolute outside it."""

    def __init__(self, ws_root: Path, work_dir: Path):
        self.ws_root = ws_root
        try:
            self.rel_dir: Optional[Path] = work_dir.relative_to(ws_root)
        except ValueError:
            self.rel_dir = None

    @property
    def in_workspace(self) -> bool:
        return self.rel_dir is not None

    def __call__(self, p: Optional[Path]) -> Optional[str]:
        if p is None:
            return None
        if self.rel_dir is not None:
            try:
                return p.relative_to(self.ws_root).as_posix()
            except ValueError:
                return None
        return str(p)


def work_row(work_dir: Path, ws_root: Path, updated: Optional[str] = None) -> Optional[dict]:
    """One registered work as an overlay row, or None when its work.json is missing or
    broken. A missing or wrongly typed field is null or empty; the row still returns."""
    meta = _meta(work_dir)
    if meta is None:
        return None
    shown = _Shown(ws_root, work_dir)
    kind = _s(meta.get("kind")) or "unknown"
    versions = _versions(meta)
    newest = versions[-1] if versions else None
    w, h = _size(meta)
    title, style = _s(meta.get("title")), _s(meta.get("style"))
    scenes = _storyboard(work_dir) if kind == VIDEO else []
    if kind == VIDEO:
        board = _work_json(work_dir, "storyboard.json")
        board = board if isinstance(board, dict) else {}
        title = title or _s(board.get("title"))
        style = style or _s(board.get("style"))
        if w is None and h is None:
            bw, bh = board.get("width"), board.get("height")
            if isinstance(bw, int) and isinstance(bh, int) and not isinstance(bw, bool) and not isinstance(bh, bool):
                w, h = bw, bh
    if kind == VIDEO:
        main = _video_main(work_dir)
        thumb = _video_thumb(work_dir, scenes)
    else:
        main = _first_file(work_dir, MAIN_OUTPUTS.get(kind, ()))
        thumb = _deck_thumb(work_dir) if kind == "deck" else (
            _first_file(work_dir, (THUMBNAILS[kind],)) if kind in THUMBNAILS else None)
    slides = _strs(meta.get("slides"))
    duration = sum(s["duration_s"] or 0.0 for s in scenes) if scenes else None
    op = _open(meta)
    current = _s(meta.get("current"))
    if kind == VIDEO:
        progress: Optional[dict] = _video_progress(work_dir, scenes, main)
        in_progress = main is None
        # registered at its brief, a video is changed by its own skill, never by work.py:
        # the files' own times say when it last moved
        updated = _newest(updated, *(_mtime_iso(f) for f in (
            _first_file(work_dir, ("brief.md",)), _first_file(work_dir, ("storyboard.json",)), main)))
    else:
        progress = None
        in_progress = kind in MAIN_OUTPUTS and (op is not None or current is None)
        updated = updated or (newest["at"] if newest else None)
    return {
        "path": shown.rel_dir.as_posix() if shown.rel_dir is not None else str(work_dir),
        "in_workspace": shown.in_workspace,
        "kind": kind,
        "title": title or work_dir.name,
        "brain": _brain(work_dir, ws_root),
        "style": style,
        "width": w,
        "height": h,
        "slide_count": len(slides) if kind == "deck" else None,
        "current": current,
        "latest": newest["id"] if newest else None,
        "status": newest["status"] if newest else None,
        "updated": updated,
        "open": op,
        "in_progress": in_progress,
        "progress": progress,
        # the chat that registered a video (work.py adopt records it); a document's chats
        # are on its open revision and versions
        "session": _s(meta.get("session")),
        "versions": versions,
        "thumbnail": shown(thumb),
        "main_output": shown(main),
        "duration_s": round(duration, 1) if duration is not None else None,
        "scene_count": len(scenes) if kind == VIDEO else None,
    }


def read_works(ws_root: Path) -> dict:
    """Every registered work as a row, newest first; broken entries counted in ``skipped``."""
    ws_root = ws_root.resolve()
    dirs, skipped = registered_dirs(ws_root)
    stamps = _registry_updated(ws_root)
    rows = []
    for d in dirs:
        try:
            row = work_row(d, ws_root, stamps.get(d))
        except Exception:  # noqa: BLE001 — one bad work must not sink the list
            logger.warning("pollinate: failed to read a registered work", exc_info=True)
            row = None
        if row is None:
            skipped += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["updated"] is not None, r["updated"] or ""), reverse=True)
    return {"works": rows, "skipped": skipped}


# ── the view of one work ───────────────────────────────────────────────────

def _version_file(work_dir: Path, vid: str, name: str) -> Any:
    p = _inside(work_dir / ".versions" / vid / name, work_dir)
    return _json_or_none(p) if p is not None and p.is_file() else None


def _findings(checks: Any) -> list[dict]:
    raw = checks.get("findings") if isinstance(checks, dict) else None
    out = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict) or not _s(f.get("check")):
            continue
        out.append({"check": f["check"], "level": "fail" if f.get("level") == "fail" else "warn",
                    "slide": _s(f.get("slide")), "msg": _s(f.get("msg")) or _s(f.get("detail")),
                    "carried": bool(f.get("carried"))})
        if len(out) >= MAX_FINDINGS:
            break
    return out


def _guard(guard: Any) -> Optional[dict]:
    if not isinstance(guard, dict) or not isinstance(guard.get("ok"), bool):
        return None
    viol = []
    for v in guard.get("violations") if isinstance(guard.get("violations"), list) else []:
        if isinstance(v, dict):
            viol.append({"slide": _s(v.get("slide")), "kind": _s(v.get("kind")), "detail": _s(v.get("detail"))})
    return {"ok": guard["ok"], "violations": viol}


def _fail_counts(findings: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        if f["level"] == "fail":
            out[f["check"]] = out.get(f["check"], 0) + 1
    return out


def _outputs(work_dir: Path, shown: _Shown) -> list[dict]:
    d = _inside(work_dir / "out", work_dir)
    if d is None or not d.is_dir():
        return []
    out = []
    try:
        items = sorted(d.iterdir())
    except OSError:
        return []
    for f in items:
        r = _inside(f, work_dir)
        if r is None:
            continue
        if r.is_file():
            try:
                size = r.stat().st_size
            except OSError:
                size = None
            out.append({"path": shown(r), "name": r.name, "bytes": size, "files": None})
        elif r.is_dir():
            try:
                n = sum(1 for x in r.iterdir() if x.is_file())
            except OSError:
                n = 0
            out.append({"path": shown(r), "name": r.name + "/", "bytes": None, "files": n})
    return out


def _slide_images(work_dir: Path, shown: _Shown, order: list[str], changed: set[str]) -> list[dict]:
    d = _inside(work_dir / "out" / "slides", work_dir)
    by_id: dict[str, Path] = {}
    if d is not None and d.is_dir():
        try:
            for f in d.glob("*.png"):
                parts = f.stem.split("-", 1)
                if len(parts) == 2 and f.is_file():
                    r = _inside(f, work_dir)
                    if r is not None:
                        by_id[parts[1]] = r
        except OSError:
            pass
    return [{"n": i, "id": sid, "image": shown(by_id.get(sid)), "changed": sid in changed}
            for i, sid in enumerate(order, 1)]


def _actions(row: dict, session: Optional[str]) -> list[dict]:
    """The commands for this work's state, in order; the first is the primary one."""
    acts: list[dict] = []
    if row["in_workspace"] and row["main_output"]:
        acts.append({"key": "open_canvas", "path": row["main_output"]})
    if session:
        acts.append({"key": "open_session", "session": session})
    else:
        acts.append({"key": "continue_in_chat"})
    # No "open the folder": the workspace explorer cannot open at a path today, so the
    # card would offer a button that lands somewhere else.
    for i, a in enumerate(acts):
        a["primary"] = i == 0
    return acts


def _registered_dir(ws_root: Path, path: str) -> Optional[Path]:
    """The registered folder whose row path equals ``path`` (relative inside the workspace,
    absolute outside), or None. Only an exact registry match is accepted."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    dirs, _ = registered_dirs(ws_root)
    for d in dirs:
        try:
            row_path = d.relative_to(ws_root).as_posix()
        except ValueError:
            row_path = str(d)
        if row_path == path:
            return d
    return None


def work_view(ws_root: Path, path: str) -> Optional[dict]:
    """One registered work's big card, or None when ``path`` is not a registered work."""
    ws_root = ws_root.resolve()
    work_dir = _registered_dir(ws_root, path)
    if work_dir is None:
        return None
    row = work_row(work_dir, ws_root, _registry_updated(ws_root).get(work_dir))
    if row is None:
        return None
    meta = _meta(work_dir) or {}
    shown = _Shown(ws_root, work_dir)
    details = []
    for v in row["versions"]:
        checks = _version_file(work_dir, v["id"], "checks.json")
        guard = _guard(_version_file(work_dir, v["id"], "guard.json"))
        details.append({**v, "guard_ok": guard["ok"] if guard else None,
                        "fail_counts": _fail_counts(_findings(checks))})
    newest = row["versions"][-1] if row["versions"] else None
    findings = _findings(_version_file(work_dir, newest["id"], "checks.json")) if newest else []
    guard = _guard(_version_file(work_dir, newest["id"], "guard.json")) if newest else None
    brief = None
    bp = _inside(work_dir / "brief.md", work_dir)
    if bp is not None and bp.is_file():
        try:
            raw = _read_bounded(bp, MAX_BRIEF_BYTES, truncate=True)
            brief = raw.decode("utf-8", errors="replace").strip() or None
        except (OSError, ValueError):
            brief = None
    op = row["open"]
    changed = set(op["scope"]) if op else set(newest["scope"]) if newest else set()
    slides = _slide_images(work_dir, shown, _strs(meta.get("slides")), changed) if row["kind"] == "deck" else []
    scenes = []
    if row["kind"] == VIDEO:
        for s in _storyboard(work_dir):
            scenes.append({**s, "image": shown(_scene_frame(work_dir, s["id"]))})
    session = (op or {}).get("session") or (newest or {}).get("session") or row["session"]
    return {
        "work": row,
        "versions": details,
        "findings": findings,
        "guard": guard,
        "brief": brief,
        "outputs": _outputs(work_dir, shown),
        "slides": slides,
        "scenes": scenes,
        "session": session,
        "actions": _actions(row, session),
    }


# ── eval ───────────────────────────────────────────────────────────────────

def _when(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _revisions(versions: list[dict]) -> list[list[dict]]:
    """Agent versions grouped by base, in list order (one group = one revision)."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for v in versions:
        if v["by"] != "agent" or v["status"] is None:
            continue
        key = v["base"] or "<none>"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(v)
    return [groups[k] for k in order]


def eval_summary(ws_root: Path, now: Optional[datetime] = None, days: int = 15, tz_minutes: int = 0) -> dict:
    """The Eval tab, from the registered works' files: the first-pass revision rate for the
    last ``days`` days against the ``days`` before, daily bars over both windows, four
    numbers, fail findings per check and the guard-blocked versions.

    Days are the person's local days: ``tz_minutes`` is their offset from UTC (UTC+8 → 480),
    so a cut at 07:00 local on the 26th counts on the 26th, as the "today" bar says."""
    ws_root = ws_root.resolve()
    now = now or datetime.now(timezone.utc)
    # Windows are whole local days, the same days the bars show: "cur" is today and the
    # days-1 days before it, "prev" the days days before those.
    local = timezone(timedelta(minutes=tz_minutes))
    today = now.astimezone(local).date()
    cur_first = today - timedelta(days=days - 1)
    prev_first = today - timedelta(days=2 * days - 1)

    def window(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        d = dt.astimezone(local).date()
        if d > today or d < prev_first:
            return None
        return "cur" if d >= cur_first else "prev"

    first_pass = {"cur": [0, 0], "prev": [0, 0]}   # [first cut passed, revisions]
    changed: dict[str, set[Path]] = {"cur": set(), "prev": set()}
    per_work: dict[str, dict[Path, int]] = {"cur": {}, "prev": {}}
    person = {"cur": 0, "prev": 0}
    blocks = {"cur": 0, "prev": 0}
    daily: dict[str, list[int]] = {}
    hits: dict[str, dict[Path, int]] = {}   # check → work folder → fail findings
    titles: dict[Path, str] = {}
    guard_list: list[dict] = []
    dirs, _ = registered_dirs(ws_root)
    for d in dirs:
        meta = _meta(d)
        if meta is None or meta.get("kind") == VIDEO:
            continue
        titles[d] = _s(meta.get("title")) or d.name
        versions = _versions(meta)
        for v in versions:
            w = window(_when(v["at"]))
            if w is None:
                continue
            if v["by"] == "user" and v["found_on_disk"]:
                person[w] += 1
            if v["by"] == "agent" and v["status"] is not None:
                changed[w].add(d)
                per_work[w][d] = per_work[w].get(d, 0) + 1
                guard = _guard(_version_file(d, v["id"], "guard.json"))
                if guard is not None and not guard["ok"]:
                    blocks[w] += 1
                    if w == "cur":
                        try:
                            shown_path = d.relative_to(ws_root).as_posix()
                        except ValueError:
                            shown_path = str(d)
                        guard_list.append({"path": shown_path, "title": titles[d], "version": v["id"],
                                           "at": v["at"],
                                           "violations": [x["detail"] or x["kind"] or "" for x in guard["violations"]]})
                if w == "cur":
                    for check, n in _fail_counts(_findings(_version_file(d, v["id"], "checks.json"))).items():
                        hits.setdefault(check, {})
                        hits[check][d] = hits[check].get(d, 0) + n
        for rev in _revisions(versions):
            first = rev[0]
            dt = _when(first["at"])
            w = window(dt)
            if w is None:
                continue
            ok_first = first["status"] == "ok"
            first_pass[w][1] += 1
            first_pass[w][0] += 1 if ok_first else 0
            day = dt.astimezone(local).date().isoformat()
            bucket = daily.setdefault(day, [0, 0, 0])
            if ok_first:
                bucket[0] += 1
            elif any(x["status"] == "ok" for x in rev):
                bucket[1] += 1
            else:
                bucket[2] += 1

    def rate(w: str) -> Optional[dict]:
        k, n = first_pass[w]
        return {"passed": k, "revisions": n, "pct": round(100 * k / n)} if n else None

    def median(w: str) -> Optional[float]:
        vals = list(per_work[w].values())
        return statistics.median(vals) if vals else None

    days_out = []
    for i in range(2 * days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        b = daily.get(day, [0, 0, 0])
        days_out.append({"date": day, "first_pass": b[0], "later": b[1], "not_passed": b[2]})
    check_hits = []
    for check, by_work in hits.items():
        top_dir, top_n = max(by_work.items(), key=lambda kv: (kv[1], str(kv[0])))
        check_hits.append({"check": check, "count": sum(by_work.values()), "top_title": titles[top_dir], "top_count": top_n})
    check_hits.sort(key=lambda c: (-c["count"], c["check"]))
    guard_list.sort(key=lambda g: g["at"] or "", reverse=True)
    return {
        "days": days,
        "now": now.isoformat(),
        "rate": {"cur": rate("cur"), "prev": rate("prev")},
        "daily": days_out,
        "works_changed": {"cur": len(changed["cur"]), "prev": len(changed["prev"])},
        "versions_median": {"cur": median("cur"), "prev": median("prev")},
        "person_versions": person,
        "guard_blocks": blocks,
        "check_hits": check_hits,
        "guard_list": guard_list,
    }
