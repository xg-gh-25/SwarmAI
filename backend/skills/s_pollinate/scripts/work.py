#!/usr/bin/env python3
"""Pollinate works: a folder with work.json, versions, a revision guard and a renderer.

A work is any folder with a ``work.json`` (design section 13.1). It is
registered in place in ``<workspace>/.pollinate/works.json`` (workspace =
the SwarmWS root, as backend config resolves it). A deck's source is one HTML
file; each slide is a top-level ``<section data-slide-id="s-xxxx">``. A poster
or page is one HTML file (the whole page has id ``main``); copy is a Markdown
file.

Commands (design section 13.2):

  init    make a new work: folder, work.json, empty source, .gitignore, registry entry
  adopt   register a folder that already has a source; add missing slide ids
          (nothing else in the file changes) and store v001 (by the person).
          ``--kind video`` registers a video project with no versions as soon as
          it has a brief.md or storyboard.json (or a film in out/*.mp4): it is
          listed with the works, in progress until the film exists; open, cut
          and render refuse it. ``--derived-from <folder>`` records the earlier
          film a new cut was made from (``derived_from`` in work.json and the
          registry entry)
  open    start a revision: when the source on disk differs from the current
          version (the person edited it), store it first as ``<current>-user``;
          then record the declared scope (``--scope``, ``--add``, ``--remove``)
          and the chat session running it (SWARM_APP_SESSION_ID, when set; cut
          records it on its version too).
          ``--mine`` says those edits are the agent's own: they stay in this
          revision instead, and only if the guard finds them inside the scope
  cut     render, measure, run the craft checks, run the guard, export PPTX and
          store a new version; ``current`` moves only when everything passes
  render  render the source on disk into out/ (no version); refused when the source
          differs from the current version and no revision is open (open first).
          ``--version V --out D`` renders an old version into D
  list    print the registry

The guard (design section 13.4) compares the new source with the base version
of the open revision. A slide outside the scope that was changed, deleted or
moved is a violation, and so is a change to a shared file (or to the deck
outside its slides) unless ``open --shared`` was given. (13.4 first said a
note containing ``shared`` allows it; a note is free text, so only an open
record written before the flag existed is still read that way.)

Every version is a read-only snapshot in ``.versions/<id>/`` (the source,
checks.json, guard.json, measure.json). After a cut, out/ and review/ hold the
current version's outputs (a failed cut's go to review/failed-<id>/); a plain
``render`` writes a preview of the source on disk there. Both are ignored by git.

Exit codes: 0 ok, 1 a check or the guard failed, 2 usage or environment error.
"""
from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import work_render  # noqa: E402

SCHEMA = 1
KINDS = ("deck", "poster", "page", "copy")
# A video is registered as a work (adopt --kind video) so it is listed with the others, but
# Pollinate does not make it: the video skill renders and checks its own film, so open, cut
# and render refuse it.
VIDEO = "video"
ALL_KINDS = KINDS + (VIDEO,)
# Kinds whose rendered output carries the person's brand signature (kinds/*.md): a deck on
# its closing slide, a poster and a page in the footer. Copy is text for a platform post.
SIGNED_KINDS = ("poster",)   # the old brand gate judged posters only; a deck or page is often made for work
DEFAULT_SRC = {"deck": "src/deck.html", "poster": "src/index.html",
               "page": "src/index.html", "copy": "src/copy.md"}
DEFAULT_SIZE = {"deck": "1920x1080", "poster": "1080x1350", "page": "1200x800", "copy": "1080x1080"}
DEFAULT_FORMATS = {"deck": ["html", "pdf", "png", "pptx"], "poster": ["png"],
                   "page": ["png", "html"], "copy": ["md"]}
KNOWN_FORMATS = {"html", "pdf", "png", "pptx", "md"}
SHARED = "_shared"
WHOLE = "main"
ID_ATTR = "data-slide-id"
EXISTING_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
GITIGNORE_LINES = ("out/", "review/", ".render-tmp-*/")
SKELETON = {
    "deck": ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             '<title>{title}</title>\n<link rel="stylesheet" href="shared.css">\n</head>\n'
             '<body>\n</body>\n</html>\n'),
    "poster": ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
               '<title>{title}</title>\n</head>\n<body>\n</body>\n</html>\n'),
    "copy": "",
}
SKELETON["page"] = SKELETON["poster"]


class UsageError(Exception):
    """Wrong arguments or a work in a state the command cannot use (exit 2)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm_hash(text: str) -> str:
    """Hash of a text unit: line ends made \\n, outer white space removed (13.1)."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return sha256(t.encode("utf-8", "surrogateescape"))


def dec(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")


def enc(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def write_json(path: Path, data) -> None:
    """Write JSON through a temporary file and a rename, so a crash leaves the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ── slides in the source text ──────────────────────────────────────────────

class _SectionFinder(html.parser.HTMLParser):
    """Find top-level <section> elements with their offsets in the source text."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.depth = 0
        self.found: list = []
        self._cur = None

    def _pos(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag != "section":
            return
        if self.depth == 0:
            start = self._pos()
            raw = self.get_starttag_text() or ""
            self._cur = {"start": start, "tag_end": start + len(raw), "attrs": attrs,
                         "line": self.getpos()[0]}
        self.depth += 1

    def handle_endtag(self, tag):
        if tag != "section" or self.depth == 0:
            return
        self.depth -= 1
        if self.depth == 0:
            pos = self._pos()
            close = self.text.find(">", pos)
            self._cur["end"] = close + 1 if close >= 0 else len(self.text)
            self.found.append(self._cur)
            self._cur = None


def parse_slides(text: str) -> list:
    """Top-level sections in source order: [{id, start, tag_end, end, line, raw}].

    ``id`` is None when the section has no data-slide-id. Raises UsageError for
    an unclosed section, an empty or badly formed id, or a repeated id.
    """
    finder = _SectionFinder(text)
    finder.feed(text)
    finder.close()
    if finder._cur is not None:
        raise UsageError(f"the <section> on line {finder._cur['line']} is never closed")
    slides, seen = [], set()
    for f in finder.found:
        values = [v for k, v in f["attrs"] if k == ID_ATTR]
        sid = values[0] if values else None
        if values and (len(values) > 1 or sid is None or not EXISTING_ID_RE.match(sid) or sid == SHARED):
            raise UsageError(
                f"the <section> on line {f['line']} has a bad {ID_ATTR} ({sid!r}); use letters, "
                "digits, '-' or '_' (at most 64), or remove it so an id is added")
        if sid in seen:
            raise UsageError(f"{ID_ATTR}={sid!r} is used by more than one section (line {f['line']})")
        if sid:
            seen.add(sid)
        slides.append({"id": sid, "start": f["start"], "tag_end": f["tag_end"], "end": f["end"],
                       "line": f["line"], "raw": text[f["start"]:f["end"]]})
    return slides


def new_slide_id(used: set) -> str:
    """A fresh id ``s-<hex>`` not in ``used`` (4 hex digits, more if crowded)."""
    for digits in (4, 4, 4, 4, 6, 6, 8, 8, 8, 8):
        sid = "s-" + secrets.token_hex(4)[:digits]
        if sid not in used:
            return sid
    raise UsageError("could not find a free slide id")


def add_missing_ids(text: str, used: set):
    """Insert ``data-slide-id`` into every top-level section without one.

    Only the attribute text is inserted, right after ``<section``; every other
    byte stays as it was. Returns (new_text, [added ids]).
    """
    slides = parse_slides(text)
    used = set(used) | {s["id"] for s in slides if s["id"]}
    added = []
    plan = []
    for s in slides:
        if s["id"] is None:
            sid = new_slide_id(used)
            used.add(sid)
            added.append(sid)
            plan.append((s["start"] + len("<section"), f' {ID_ATTR}="{sid}"'))
    for pos, ins in reversed(plan):
        text = text[:pos] + ins + text[pos:]
    return text, added


def frame_text(text: str, slides: list) -> str:
    """The deck without its slides: each slide and the space around it is one marker."""
    parts, last = [], 0
    for s in slides:
        parts.append(text[last:s["start"]])
        parts.append("\0")
        last = s["end"]
    parts.append(text[last:])
    joined = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    joined = re.sub(r"\s*\0\s*", "\0", joined)
    return re.sub("\0+", "\0", joined)


# ── the work ───────────────────────────────────────────────────────────────

def work_dir_of(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_meta(wdir: Path) -> dict:
    path = wdir / "work.json"
    if not path.is_file():
        raise UsageError(f"{wdir} is not a work (no work.json); run init or adopt first")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path} is not valid JSON: {exc}") from None
    if meta.get("schema") != SCHEMA or meta.get("kind") not in ALL_KINDS:
        raise UsageError(f"{path} is not a schema {SCHEMA} work.json")
    return meta


def save_meta(wdir: Path, meta: dict) -> None:
    write_json(wdir / "work.json", meta)


def src_layout(wdir: Path, meta: dict):
    """(src_root, source path relative to src_root, standard layout?)."""
    src = Path(meta["src"])
    if src.is_absolute() or ".." in src.parts:
        raise UsageError(f"work.json src must be a path inside the work, not {meta['src']}")
    if src.parts and src.parts[0] == "src" and len(src.parts) > 1:
        return wdir / "src", Path(*src.parts[1:]).as_posix(), True
    return wdir, src.as_posix(), False


def _skip_name(name: str) -> bool:
    return name.startswith(".")


def collect_files(wdir: Path, meta: dict, keep=()) -> dict:
    """The files of the current source: {key: bytes}; the source itself is one of them.

    Standard layout (source under src/): every file under src/ (dot files are
    skipped). A source elsewhere (an adopted folder): the source and the local
    files it uses, read through its CSS too, plus every ``keep`` key (the base
    version's shared files) that still exists, so a file a slide stops using is
    still guarded and only a real deletion counts as one.
    """
    root, rel, standard = src_layout(wdir, meta)
    source = root / rel
    if not source.is_file():
        raise UsageError(f"source file not found: {source}")
    files = {rel: source.read_bytes()}
    if standard:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not _skip_name(d))
            for name in sorted(filenames):
                if _skip_name(name):
                    continue
                p = Path(dirpath) / name
                if p.is_symlink() and not _inside(p.resolve(), root.resolve()):
                    continue
                key = p.relative_to(root).as_posix()
                if key != rel:
                    files[key] = p.read_bytes()
    else:
        skip = {(wdir / d).resolve() for d in (".versions", "out", "review")}
        for p in sorted(work_render.local_files_of(root, source)):
            if any(_inside(p, s) for s in skip) or not p.is_file():
                continue
            key = p.relative_to(root.resolve()).as_posix()
            if key != rel:
                files[key] = p.read_bytes()
        for key in keep:
            p = _safe_join(root, key)
            if key not in files and key != rel and p is not None and p.is_file():
                files[key] = p.read_bytes()
    return files


def _safe_join(root: Path, rel_path: str):
    """root/rel_path when it is a relative path that stays inside root, else None."""
    rp = Path(rel_path or "")
    if not rel_path or rp.is_absolute() or ".." in rp.parts:
        return None
    target = (root / rp).resolve()
    return target if _inside(target, root.resolve()) else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def compute_state(meta: dict, files: dict, rel: str) -> dict:
    """Slide order and hashes (13.1) for a set of source files."""
    text = dec(files[rel])
    shared = {k: sha256(v) for k, v in files.items() if k != rel}
    if meta["kind"] == "deck":
        slides = parse_slides(text)
        missing = [s["line"] for s in slides if not s["id"]]
        if missing:
            raise UsageError(f"sections on lines {missing} have no {ID_ATTR}")
        order = [s["id"] for s in slides]
        hashes = {s["id"]: norm_hash(s["raw"]) for s in slides}
        shared[rel] = norm_hash(frame_text(text, slides))
        raws = {s["id"]: s["raw"] for s in slides}
    else:
        order = [WHOLE]
        hashes = {WHOLE: norm_hash(text)}
        raws = {WHOLE: text}
    hashes[SHARED] = dict(sorted(shared.items()))
    return {"slides": order, "hashes": hashes, "raws": raws}


def used_ids(meta: dict) -> set:
    ids = set(meta.get("slides") or [])
    for v in meta.get("versions", []):
        ids.update(k for k in v.get("hashes", {}) if k != SHARED)
    return ids


def version_entry(meta: dict, vid):
    for v in meta.get("versions", []):
        if v["id"] == vid:
            return v
    return None


def _taken_ids(meta: dict, wdir) -> set:
    """Version ids in work.json and snapshot folders on disk (a crash may leave one)."""
    taken = {v["id"] for v in meta.get("versions", [])}
    vroot = Path(wdir) / ".versions" if wdir else None
    if vroot and vroot.is_dir():
        taken.update(p.name for p in vroot.iterdir() if not p.name.startswith("."))
    return taken


def next_version_id(meta: dict, wdir=None) -> str:
    nums = [int(m.group(1)) for vid in _taken_ids(meta, wdir)
            for m in [re.match(r"v(\d+)", vid)] if m]
    return f"v{max(nums, default=0) + 1:03d}"


def user_version_id(meta: dict, wdir=None) -> str:
    cur = meta.get("current")
    root = re.sub(r"-user\d*$", "", cur) if cur else next_version_id(meta, wdir)
    taken = _taken_ids(meta, wdir)
    vid, n = f"{root}-user", 1
    while vid in taken:
        n += 1
        vid = f"{root}-user{n}"
    return vid


def _readonly_tree(path: Path) -> None:
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            p = Path(dirpath) / name
            p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def stage_snapshot(wdir: Path, vid: str, files: dict) -> Path:
    """Write the source files of a new version into a staging folder under .versions/."""
    vroot = wdir / ".versions"
    if (vroot / vid).exists():
        raise UsageError(f"version {vid} already exists; versions are never written twice")
    vroot.mkdir(parents=True, exist_ok=True)
    for old in vroot.glob(".staging-*"):
        shutil.rmtree(old, ignore_errors=True)
    stage = Path(tempfile.mkdtemp(prefix=f".staging-{vid}-", dir=vroot))
    for key, data in files.items():
        dest = stage / "src" / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return stage


def seal_snapshot(wdir: Path, stage: Path, vid: str, extra: dict) -> None:
    """Add the json files, make the snapshot read-only and move it to .versions/<vid>."""
    for name, data in extra.items():
        write_json(stage / name, data)
    _readonly_tree(stage)
    os.replace(stage, wdir / ".versions" / vid)


def ensure_gitignore(wdir: Path) -> None:
    path = wdir / ".gitignore"
    have = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [line for line in GITIGNORE_LINES if line not in have]
    if missing:
        prefix = "" if not have or path.read_bytes().endswith(b"\n") else "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(prefix + "\n".join(missing) + "\n")


# ── registry ───────────────────────────────────────────────────────────────

def _swarmws() -> Path:
    """The SwarmWS root, resolved exactly as backend config.get_swarmws (a skill script
    cannot import backend config): SWARM_WORKSPACE, then SWARMWS, then
    $SWARM_DATA_DIR/SwarmWS, then ~/.swarm-ai/SwarmWS."""
    for var in ("SWARM_WORKSPACE", "SWARMWS"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(os.path.expanduser(value))
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai") / "SwarmWS"


def workspace_dir() -> Path:
    return _swarmws()


def registry_path() -> Path:
    return workspace_dir() / ".pollinate" / "works.json"


def _lock(path: Path):
    try:
        import fcntl
    except ImportError:  # Windows: no lock; writes are still atomic renames
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path.with_suffix(".lock"), "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def load_registry() -> dict:
    path = registry_path()
    if not path.is_file():
        return {"schema": 1, "works": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("works"), list):
            raise ValueError("no works list")
        return data
    except (json.JSONDecodeError, ValueError, AttributeError):
        keep = path.with_name(f"works.json.corrupt-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        os.replace(path, keep)
        print(f"warning: the registry was not valid JSON; kept it as {keep} and started a new one",
              file=sys.stderr)
        return {"schema": 1, "works": []}


def register(wdir: Path, meta: dict) -> None:
    lock = _lock(registry_path())
    try:
        reg = load_registry()
        works = [w for w in reg["works"] if w.get("path") != str(wdir)]
        entry = {"path": str(wdir), "kind": meta["kind"], "title": meta.get("title") or wdir.name,
                 "updated": now_iso()}
        if meta.get("derived_from"):
            entry["derived_from"] = meta["derived_from"]
        works.append(entry)
        reg["works"] = works
        reg["schema"] = 1
        write_json(registry_path(), reg)
    finally:
        if lock:
            lock.close()


# ── guard (13.4) ───────────────────────────────────────────────────────────

class _TextRuns(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.images = [], []

    def handle_data(self, data):
        t = " ".join(data.split())
        if t:
            self.texts.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in ("img", "image", "source", "video"):
            for k, v in attrs:
                if k in ("src", "href", "xlink:href") and v:
                    self.images.append(v if not v.startswith("data:") else v[:40] + "…")


def _runs(raw: str):
    p = _TextRuns()
    p.feed(raw)
    p.close()
    return p.texts, p.images


def _short(t: str, n: int = 60) -> str:
    return t if len(t) <= n else t[: n - 1] + "…"


def change_detail(old_raw: str, new_raw: str) -> str:
    """Say in a few words what changed in a slide: a text block, an image, or markup."""
    if old_raw is None:
        return "content changed"
    ot, oi = _runs(old_raw)
    nt, ni = _runs(new_raw)
    for img in oi:
        if img not in ni:
            return f"image removed: {_short(img)}"
    for img in ni:
        if img not in oi:
            return f"image added: {_short(img)}"
    for a, b in zip(ot, nt):
        if a != b:
            return f"text changed: '{_short(a)}' -> '{_short(b)}'"
    if len(ot) > len(nt):
        return f"text removed: '{_short(ot[len(nt)])}'"
    if len(nt) > len(ot):
        return f"text added: '{_short(nt[len(ot)])}'"
    return "markup or style changed (same text and images)"


def _kept_in_order(a: list, b: list, weight) -> set:
    """Longest common subsequence of a and b with the most weight; returns the kept ids."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = weight(a[i]) + dp[i + 1][j + 1]
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    kept, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j] and dp[i][j] == weight(a[i]) + dp[i + 1][j + 1]:
            kept.add(a[i])
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return kept


def shared_declared(note: str) -> bool:
    """The first 13.4 form, read only for an open record that has no ``shared`` key.

    A note is free text ("leave shared.css alone" contains the word), so a new
    open never takes permission from it; ``open --shared`` is the only way.
    """
    return bool(re.search(r"\bshared\b", note or "", re.I))


def guard(base, base_raws: dict, state: dict, open_: dict) -> dict:
    """Compare the new state with the base version (13.4) and return guard.json."""
    open_ = open_ or {}
    scope = list(open_.get("scope") or [])
    allowed_delete = set(scope) | set(open_.get("remove") or [])
    result = {"base": base["id"] if base else None, "scope": scope, "added": [],
              "removed_ok": [], "violations": [], "notes": [], "ok": True}
    new_order = state["slides"]
    if base is None:
        result["added"] = list(new_order)
        return result
    base_order = base.get("slides") or [k for k in base["hashes"] if k != SHARED]
    base_hashes = base["hashes"]
    new_set, base_set = set(new_order), set(base_order)
    viol = result["violations"]
    result["added"] = [s for s in new_order if s not in base_set]
    declared_add = int(open_.get("add") or 0)
    if len(result["added"]) > declared_add:
        result["notes"].append(
            f"{len(result['added'])} slides were added, the open revision declared {declared_add}")
    for pos, sid in enumerate(base_order, 1):
        if sid in new_set:
            continue
        if sid in allowed_delete:
            result["removed_ok"].append(sid)
        else:
            viol.append({"slide": sid, "kind": "deleted",
                         "detail": f"page {pos} ({sid}) of {base['id']} is gone and was not in the scope"})
    for sid in new_order:
        if sid in base_set and sid not in scope and state["hashes"][sid] != base_hashes.get(sid):
            viol.append({"slide": sid, "kind": "modified",
                         "detail": change_detail(base_raws.get(sid), state["raws"][sid])})
    common_base = [s for s in base_order if s in new_set]
    common_new = [s for s in new_order if s in base_set]
    kept = _kept_in_order(common_base, common_new, lambda s: 1 if s in scope else 1000)
    for sid in common_base:
        if sid not in kept and sid not in scope:
            viol.append({"slide": sid, "kind": "moved",
                         "detail": f"was page {base_order.index(sid) + 1} of {base['id']}, "
                                   f"is now page {new_order.index(sid) + 1}"})
    allowed = open_["shared"] if "shared" in open_ else shared_declared(open_.get("note", ""))
    if not allowed:
        old_shared = base_hashes.get(SHARED, {})
        new_shared = state["hashes"][SHARED]
        for key, h in old_shared.items():
            if key not in new_shared:
                viol.append({"slide": SHARED, "kind": "deleted", "detail": f"shared file deleted: {key}"})
            elif new_shared[key] != h:
                what = ("the deck outside its slides (head, styles, scripts) changed"
                        if key == base.get("source") else f"shared file changed: {key}")
                viol.append({"slide": SHARED, "kind": "modified", "detail": what})
    result["ok"] = not viol
    return result


# ── commands ───────────────────────────────────────────────────────────────

def _parse_ids(value) -> list:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _parse_formats(value, kind: str) -> list:
    if not value:
        return list(DEFAULT_FORMATS[kind])
    fmts = _parse_ids(value)
    bad = [f for f in fmts if f not in KNOWN_FORMATS]
    if bad:
        raise UsageError(f"unknown formats {bad}; use {sorted(KNOWN_FORMATS)}")
    return fmts


def _size(value: str) -> dict:
    try:
        return work_render.parse_size(value)
    except ValueError as exc:
        raise UsageError(str(exc)) from None


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def _session() -> str | None:
    """The chat session running this command (SWARM_APP_SESSION_ID), or None.

    Recorded on the open revision and on each cut, so the overlay can take the person back
    to the chat that is changing the work."""
    sid = os.environ.get("SWARM_APP_SESSION_ID", "").strip()
    return sid or None


def _refuse_video(meta: dict) -> None:
    if meta.get("kind") == VIDEO:
        raise UsageError("a video is listed as a work but not made by work.py: the video skill "
                         "renders and checks it; open, cut and render do not apply")


def cmd_init(args) -> int:
    wdir = work_dir_of(args.dir)
    if (wdir / "work.json").exists():
        raise UsageError(f"{wdir} is already a work")
    src_rel = DEFAULT_SRC[args.kind]
    if (wdir / src_rel).exists():
        raise UsageError(f"{wdir / src_rel} already exists; use adopt for an existing work")
    size = _size(args.size or DEFAULT_SIZE[args.kind])
    (wdir / "src").mkdir(parents=True, exist_ok=True)
    (wdir / src_rel).write_text(SKELETON[args.kind].replace("{title}", _html_escape(args.title)),
                                encoding="utf-8")
    if args.kind == "deck" and not (wdir / "src" / "shared.css").exists():
        (wdir / "src" / "shared.css").write_text("", encoding="utf-8")
    meta = {"schema": SCHEMA, "kind": args.kind, "title": args.title, "style": args.style,
            "size": size, "src": src_rel, "slides": [], "current": None, "open": None, "versions": []}
    save_meta(wdir, meta)
    ensure_gitignore(wdir)
    register(wdir, meta)
    _print({"work": str(wdir), "kind": args.kind, "src": src_rel})
    return 0


def _html_escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title_of(text: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return " ".join(m.group(1).split()) if m and m.group(1).strip() else fallback


def _adopt_video(wdir: Path, args) -> int:
    """Register a video project in place: work.json with kind video and no versions.

    It may be registered as soon as it is started: the folder needs a brief.md, a
    storyboard.json or a film in out/*.mp4 (an empty folder is refused). Until the film
    exists the overlay shows it in progress, with its stage read from those files. The
    chat session running adopt (SWARM_APP_SESSION_ID) is recorded as ``session``, so the
    overlay can open the chat that is making it.

    Its title, size and style come from the arguments, else from the video's
    storyboard.json (title, width, height, style) when it already has one; otherwise they
    stay null and the overlay reads them from the storyboard once it is written (a video
    registered at its brief has none yet), then the folder name.

    ``--derived-from`` (a new cut made from an earlier film, e.g. by the video skill's
    derive.py) is stored as ``derived_from``: the earlier project's absolute path."""
    has_film = any(p.is_file() for p in (wdir / "out").glob("*.mp4"))
    if not (has_film or (wdir / "brief.md").is_file() or (wdir / "storyboard.json").is_file()):
        raise UsageError(f"{wdir} has no brief.md, storyboard.json or out/*.mp4; "
                         "write the brief first, then register the video")
    board = {}
    try:
        loaded = json.loads((wdir / "storyboard.json").read_text(encoding="utf-8"))
        board = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        board = {}
    if args.size:
        size = _size(args.size)
    elif isinstance(board.get("width"), int) and isinstance(board.get("height"), int):
        size = {"w": board["width"], "h": board["height"]}
    else:
        size = None
    title = args.title or (board.get("title") if isinstance(board.get("title"), str) else None)
    style = args.style or (board.get("style") if isinstance(board.get("style"), str) else None)
    derived = _derived_from(wdir, getattr(args, "derived_from", None))
    meta = {"schema": SCHEMA, "kind": VIDEO, "title": title, "style": style, "size": size,
            "src": None, "slides": [], "current": None, "open": None, "versions": [],
            "session": _session()}
    if derived:
        meta["derived_from"] = derived
    save_meta(wdir, meta)
    register(wdir, meta)
    _print({"work": str(wdir), "kind": VIDEO, "title": title or wdir.name, "film": has_film})
    return 0


def _derived_from(wdir: Path, value):
    """The folder a video was derived from (its earlier film), as an absolute path, or None."""
    if not value:
        return None
    src = Path(value).expanduser().resolve()
    if not src.is_dir():
        raise UsageError(f"--derived-from {value}: not a folder")
    if src == wdir.resolve():
        raise UsageError("--derived-from names the work itself")
    return str(src)


def cmd_adopt(args) -> int:
    wdir = work_dir_of(args.dir)
    if not wdir.is_dir():
        raise UsageError(f"{wdir} is not a folder")
    if (wdir / "work.json").exists():
        raise UsageError(f"{wdir} is already a work")
    if getattr(args, "derived_from", None) and args.kind != VIDEO:
        raise UsageError("--derived-from is for --kind video (a new cut made from an earlier film)")
    if args.kind == VIDEO:
        return _adopt_video(wdir, args)
    if args.src:
        src_path = Path(args.src).expanduser()
        src_path = (src_path if src_path.is_absolute() else wdir / src_path).resolve()
        if not _inside(src_path, wdir):
            raise UsageError("--src must be a file inside the work folder")
        src_rel = src_path.relative_to(wdir).as_posix()
    else:
        src_rel = DEFAULT_SRC[args.kind]
    if not (wdir / src_rel).is_file():
        raise UsageError(f"source file not found: {wdir / src_rel}; give it with --src")
    meta = {"schema": SCHEMA, "kind": args.kind, "title": "", "style": args.style,
            "size": _size(args.size or DEFAULT_SIZE[args.kind]), "src": src_rel,
            "slides": [], "current": None, "open": None, "versions": []}
    root, rel, _std = src_layout(wdir, meta)
    source = root / rel
    original = source.read_bytes()
    added = []
    if args.kind == "deck":
        new_text, added = add_missing_ids(dec(original), set())
        if added:
            source.write_bytes(enc(new_text))
    meta["title"] = args.title or _title_of(dec(original), wdir.name)
    files = collect_files(wdir, meta)
    state = compute_state(meta, files, rel)
    vid = "v001"
    stage = stage_snapshot(wdir, vid, files)
    seal_snapshot(wdir, stage, vid, {})
    meta["versions"].append(_entry(vid, "user", [], "ok", state, rel, {}, note="adopted"))
    meta["current"] = vid
    meta["slides"] = state["slides"]
    save_meta(wdir, meta)
    ensure_gitignore(wdir)
    register(wdir, meta)
    _print({"work": str(wdir), "version": vid, "slides": state["slides"], "ids_added": added})
    return 0


def _entry(vid, by, scope, status, state, rel, outputs, **extra) -> dict:
    e = {"id": vid, "at": now_iso(), "by": by, "scope": list(scope), "status": status,
         "slides": list(state["slides"]), "source": rel, "hashes": state["hashes"], "outputs": outputs}
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


def _differs(state: dict, base: dict) -> list:
    """Ids whose content or place differs from the base (``_shared`` included)."""
    if base is None:
        return list(state["slides"])
    changed = []
    bh = base["hashes"]
    for sid in state["slides"]:
        if state["hashes"][sid] != bh.get(sid):
            changed.append(sid)
    for sid in base.get("slides", []):
        if sid not in state["hashes"]:
            changed.append(sid)
    if state["hashes"][SHARED] != bh.get(SHARED, {}):
        changed.append(SHARED)
    if not changed and state["slides"] != base.get("slides"):
        changed.append("order")
    return changed


def _assign_ids(wdir: Path, meta: dict) -> list:
    if meta["kind"] != "deck":
        return []
    root, rel, _ = src_layout(wdir, meta)
    source = root / rel
    if not source.is_file():
        raise UsageError(f"source file not found: {source}")
    text = dec(source.read_bytes())
    new_text, added = add_missing_ids(text, used_ids(meta))
    if added:
        source.write_bytes(enc(new_text))
    return added


def cmd_open(args) -> int:
    wdir = work_dir_of(args.dir)
    meta = load_meta(wdir)
    _refuse_video(meta)
    if args.add is not None and args.add < 0:
        raise UsageError("--add must be 0 or more")
    scope, remove = _parse_ids(args.scope), _parse_ids(args.remove)
    if meta.get("open"):
        op = meta["open"]
        op.update({"scope": scope, "add": args.add if args.add is not None else op.get("add", 0),
                   "remove": remove, "note": args.note if args.note is not None else op.get("note", ""),
                   "shared": _shared_allowed(args), "at": now_iso()})
        if _session():
            op["session"] = _session()
        _check_ids(meta, scope + remove, meta.get("slides") or [])
        save_meta(wdir, meta)
        _print({"open": op, "user_version": None, "changed_by_user": [], "changed_by_agent": [],
                "note": "a revision was already open; scope updated, base kept"})
        return 0
    _assign_ids(wdir, meta)
    root, rel, _ = src_layout(wdir, meta)
    current = version_entry(meta, meta.get("current"))
    files = collect_files(wdir, meta, keep=(current or {}).get("hashes", {}).get(SHARED, {}))
    state = compute_state(meta, files, rel)
    mine = bool(getattr(args, "mine", False))
    # With --mine the edits on disk belong to this revision, so a slide the agent already
    # deleted is still a current slide that --remove (or --scope) may name.
    known = state["slides"] + ([s for s in current.get("slides", []) if s not in state["slides"]]
                               if mine and current else [])
    _check_ids(meta, scope + remove, known)
    user_vid, changed = None, []
    if current is not None:
        changed = _differs(state, current)
        if changed and mine:
            # The agent says the edits on disk are its own (made before it opened). They are
            # not stored as the person's version: they stay on disk on top of current and
            # must already fit the declared scope, by the same guard cut runs. Refusing here,
            # not at cut, matters: a failed guard tells the agent to undo the change, and a
            # wrong --mine would then undo an edit the person made.
            proposed = {"scope": scope, "add": args.add or 0, "remove": remove,
                        "note": args.note or "", "shared": _shared_allowed(args)}
            g = guard(current, _base_raws(wdir, current), state, proposed)
            if not g["ok"]:
                details = "; ".join(f"{v['slide']} {v['kind']}: {v['detail']}" for v in g["violations"])
                raise UsageError(
                    f"--mine: changes on disk since {current['id']} fall outside the declared scope "
                    f"({details}). If you made them, put those slides in --scope (or --remove, or "
                    f"--shared for shared files). If the person made them, open without --mine: they "
                    f"are stored as the person's version.")
            print(f"note: {', '.join(changed)} changed on disk since {current['id']} and are kept as "
                  f"this revision's own edits (--mine); cut checks them against the scope.",
                  file=sys.stderr)
        elif changed:
            user_vid = user_version_id(meta, wdir)
            stage = stage_snapshot(wdir, user_vid, files)
            seal_snapshot(wdir, stage, user_vid, {})
            # Who wrote the edit is not proven, only that it was on disk and not in a
            # version: the entry says so, and the agent is told to confirm it with the person.
            meta["versions"].append(_entry(user_vid, "user", [c for c in changed if c != "order"],
                                           "ok", state, rel, {}, note="edits found on disk at open",
                                           found_on_disk=True))
            meta["current"] = user_vid
            meta["slides"] = state["slides"]
            print(f"note: {', '.join(changed)} changed on disk since {current['id']} and were stored as "
                  f"the person's version {user_vid}. If you made these edits yourself, tell the person "
                  f"(open --mine keeps your own edits as yours and checks them against the scope).",
                  file=sys.stderr)
    meta["open"] = {"scope": scope, "add": args.add or 0, "remove": remove, "note": args.note or "",
                    "shared": _shared_allowed(args), "at": now_iso(), "base": meta.get("current")}
    if _session():
        meta["open"]["session"] = _session()
    save_meta(wdir, meta)
    register(wdir, meta)
    _print({"open": meta["open"], "user_version": user_vid,
            "changed_by_user": [] if mine else changed, "changed_by_agent": changed if mine else [],
            "slides": state["slides"]})
    return 0


def _unversioned_changes(wdir: Path, meta: dict):
    """(changed, deleted): what differs on disk from the current version while no revision
    is open, and which of those slides are gone from disk.

    Empty when a revision is open (its edits are the revision's), when there is no current
    version yet (the first source is being written), or when the disk matches current.
    """
    if meta.get("open") or not meta.get("current"):
        return [], []
    current = version_entry(meta, meta["current"])
    if current is None:
        return [], []
    root, rel, _ = src_layout(wdir, meta)
    files = collect_files(wdir, meta, keep=current.get("hashes", {}).get(SHARED, {}))
    try:
        state = compute_state(meta, files, rel)
    except UsageError:
        return [NO_ID], []
    changed = _differs(state, current)
    return changed, [s for s in changed if s in current.get("slides", []) and s not in state["slides"]]


NO_ID = "sections without an id"


def _open_hint(d: str, changed: list, deleted: list) -> str:
    """The open --mine command that fits these changes: edited slides in --scope, deleted
    ones in --remove, --shared for shared files."""
    edited = [c for c in changed if c not in (SHARED, "order", NO_ID) and c not in deleted]
    parts = [f"work.py open --dir {d} --scope {','.join(edited) or '<ids>'}"]
    if deleted:
        parts.append(f"--remove {','.join(deleted)}")
    if SHARED in changed:
        parts.append("--shared")
    return " ".join(parts + ["--mine"])


def _shared_allowed(args) -> bool:
    """Shared changes are allowed only by --shared on this open call (never guessed from the note)."""
    return bool(args.shared)


def _check_ids(meta: dict, ids: list, slides: list) -> None:
    known = set(slides) | {WHOLE} if meta["kind"] != "deck" else set(slides)
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise UsageError(f"unknown slide ids {unknown}; the slides are {slides}")


def _load_craft():
    """craft_checks.py (design 13.3) when it is installed next to this script."""
    try:
        import craft_checks
    except ModuleNotFoundError as exc:
        if exc.name == "craft_checks":
            return None
        raise
    if not (hasattr(craft_checks, "PROBE_JS") and hasattr(craft_checks, "evaluate")):
        return None
    return craft_checks


def _base_raws(wdir: Path, base) -> dict:
    """Slide texts of the base version, read from its snapshot (for violation details)."""
    if not base:
        return {}
    path = _safe_join(wdir / ".versions" / base["id"] / "src", base.get("source", ""))
    if path is None or not path.is_file():
        return {}
    text = dec(path.read_bytes())
    if base.get("slides") == [WHOLE]:
        return {WHOLE: text}
    try:
        return {s["id"]: s["raw"] for s in parse_slides(text) if s["id"]}
    except UsageError:
        return {}


NEVER_CARRIED = {"craft"}    # the checks could not judge the slide: not something it already had


def _fail_counts(findings: list) -> dict:
    """(slide, check) -> how many fail-level findings (carried ones count as fails)."""
    counts: dict = {}
    for f in findings or []:
        if f.get("slide") and (f.get("level") == "fail" or f.get("carried")):
            key = (f["slide"], f.get("check"))
            counts[key] = counts.get(key, 0) + 1
    return counts


def _base_counts(wdir: Path, base):
    """The base version's fail counts from its checks.json; None when it has none (a
    version the person made: the adopted one, or one that absorbed their hand edits)."""
    try:
        found = json.loads((Path(wdir) / ".versions" / base["id"] / "checks.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return _fail_counts(found.get("findings"))


def _same_slides(base, state: dict) -> set:
    """Slides whose hash equals the base version's, when no shared file changed."""
    old = base.get("hashes", {})
    if old.get(SHARED) != state["hashes"].get(SHARED):
        return set()
    return {sid for sid in state["slides"] if sid in old and old[sid] == state["hashes"].get(sid)}


def _needs_base_counts(findings: list, base, state: dict) -> bool:
    same = _same_slides(base, state)
    return any(f.get("level") == "fail" and f.get("slide") and f["slide"] not in same
               and f.get("check") not in NEVER_CARRIED for f in findings)


def _carry_over_unchanged(findings: list, base, state: dict, before: dict) -> None:
    """A finding the base version already had is kept as a warning, not a new failure.

    A version fails only for what it made worse. The person's own deck (an adopted v10,
    say) has issues they accepted; revising slide 3 must not fail because slide 20 always
    had 14px labels, nor because slide 3 itself already had a small eyebrow before the
    edit. Two cases carry a fail over:
    - the slide is unchanged: its hash equals the base version's and no shared file (CSS,
      the deck frame, assets) changed;
    - the slide changed but did not get worse: ``before`` (the base version's fail counts)
      has at least as many findings of the same check on that slide (counted, because a
      finding has no id that survives an edit of its text).
    A check that gained findings on a slide keeps all of them as failures. With no base
    (the first cut after init) every slide is new. Deck-level findings (no slide id) and
    checks that could not judge a slide keep their level.
    """
    if not base:
        return
    same = _same_slides(base, state)
    now = _fail_counts([f for f in findings if f.get("level") == "fail"])
    for f in findings:
        if f.get("level") != "fail" or not f.get("slide") or f.get("check") in NEVER_CARRIED:
            continue
        key = (f["slide"], f.get("check"))
        if f["slide"] in same:
            note = "(unchanged slide, already so before this version)"
        elif now[key] <= before.get(key, 0):
            note = "(already so before this version)"
        else:
            continue
        f["level"] = "warn"
        f["carried"] = True
        f["msg"] = f"{note} {f.get('msg', '')}"


def _python_argv() -> list[str]:
    """How to run another Python script: the packaged SwarmAI program runs scripts through
    its ``py`` command (``sys.executable`` is the program itself, not a Python); a plain
    interpreter runs them directly."""
    return [sys.executable, "py"] if getattr(sys, "frozen", False) else [sys.executable]


def _export_pptx(meta: dict, slides_dir: Path, dest: Path, findings: list, outputs: dict) -> None:
    script = Path(__file__).resolve().parent / "export_pptx.py"
    if not script.is_file():
        findings.append({"slide": "", "check": "pptx", "level": "warn",
                         "msg": "export_pptx.py is not installed; deck.pptx was not made", "where": ""})
        return
    size = meta["size"]
    cmd = [*_python_argv(), str(script), "--slides", str(slides_dir), "--out", str(dest),
           "--size", f"{size['w']}x{size['h']}"]
    opts = meta.get("pptx") or {}
    if opts.get("template"):
        cmd += ["--template", str(opts["template"])]
        for key, flag in (("keep_head", "--keep-head"), ("base_index", "--base-index"),
                          ("keep_tail", "--keep-tail")):
            if opts.get(key) is not None:
                cmd += [flag, str(int(opts[key]))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        findings.append({"slide": "", "check": "pptx", "level": "fail",
                         "msg": "export_pptx.py did not finish in 10 minutes", "where": ""})
        return
    if proc.returncode != 0 or not dest.is_file():
        findings.append({"slide": "", "check": "pptx", "level": "fail",
                         "msg": f"export_pptx.py failed (exit {proc.returncode}): "
                                f"{(proc.stderr or proc.stdout).strip()[:300]}", "where": ""})
        return
    outputs["pptx"] = work_render.sha256_file(dest)


def _replace_dir(dest: Path, src: Path) -> None:
    """Move every file of ``src`` into ``dest`` after removing what the renderer owns."""
    dest.mkdir(parents=True, exist_ok=True)
    work_render._clear_outputs(dest)
    for item in src.iterdir():
        target = dest / item.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        os.replace(item, target)


def _judge(res: dict, meta: dict, craft, reference) -> list:
    """The render's own findings plus the craft checks on every measured slide, and the
    brand signature check on the work as a whole."""
    findings = list(res["findings"])
    for m in res["measure"]:
        if craft:
            try:
                found = craft.evaluate(m, meta["size"], meta.get("craft"), reference)
            except ValueError as exc:
                found = [{"slide": m.get("slide", "") if isinstance(m, dict) else "", "check": "craft",
                          "level": "fail", "msg": f"craft_checks could not judge this slide: {exc}",
                          "where": ""}]
        else:
            found = work_render.builtin_evaluate(m, meta["size"])
        findings.extend(found or [])
    if craft and hasattr(craft, "check_signature") and meta["kind"] in SIGNED_KINDS and res["measure"]:
        findings.extend(_brand_findings(res["measure"], meta, craft))
    return findings


def _brand_findings(measures: list, meta: dict, craft) -> list:
    """craft_checks.check_signature with the person's brand (brand_config.py). No brand
    file, or one that sets no signature, gives nothing; a broken one is a failed check."""
    import brand_config
    try:
        sig = brand_config.signature()
    except brand_config.BrandConfigError as exc:
        return [{"slide": "", "check": "brand", "level": "fail",
                 "msg": f"the brand file cannot be read: {exc}", "where": str(brand_config.user_brand_path())}]
    try:
        return craft.check_signature(measures, sig, meta.get("craft"))
    except ValueError as exc:
        return [{"slide": "", "check": "craft", "level": "fail",
                 "msg": f"craft_checks could not judge the brand signature: {exc}", "where": ""}]


def _judge_base(wdir: Path, base: dict, meta: dict, craft, reference, formats: list, out: Path) -> dict:
    """Fail counts of a base version that has no checks.json, by rendering its snapshot.

    Only a version the person made lacks checks (the adopted one, or one that absorbed
    their hand edits); the first edit of a slide on it is judged against what that slide
    already had. A base that cannot be rendered counts as clean, so nothing is carried.
    """
    src = Path(wdir) / ".versions" / base["id"] / "src"
    try:
        res = work_render.render(src, base.get("source", ""), meta["kind"], meta["size"], out, None,
                                 ["png"] + (["html"] if "html" in formats else []),
                                 probe_js=craft.PROBE_JS if craft else None,
                                 extra_sizes=None)
    except work_render.RenderError:
        return {}
    return _fail_counts(_judge(res, meta, craft, reference))


def cmd_cut(args) -> int:
    wdir = work_dir_of(args.dir)
    meta = load_meta(wdir)
    _refuse_video(meta)
    formats = _parse_formats(args.formats, meta["kind"])
    if meta.get("versions") and not meta.get("open"):
        raise UsageError("no revision is open; run: work.py open --dir D --scope <ids> first")
    _apply_cut_options(wdir, meta, args)
    reference = _reference(wdir, meta)
    added_ids = _assign_ids(wdir, meta)
    root, rel, _ = src_layout(wdir, meta)
    open_ = meta.get("open") or {}
    base = version_entry(meta, open_.get("base")) if open_.get("base") else None
    files = collect_files(wdir, meta, keep=(base or {}).get("hashes", {}).get(SHARED, {}))
    state = compute_state(meta, files, rel)
    vid = next_version_id(meta, wdir)
    stage = stage_snapshot(wdir, vid, files)
    tmp = Path(tempfile.mkdtemp(prefix=".render-tmp-", dir=wdir))
    try:
        craft = _load_craft()
        try:
            res = work_render.render(stage / "src", rel, meta["kind"], meta["size"], tmp / "out",
                                     tmp / "review", formats,
                                     probe_js=craft.PROBE_JS if craft else None,
                                     extra_sizes=meta.get("sizes"))
        except work_render.RenderEnvError:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        findings = _judge(res, meta, craft, reference)
        if base:
            before = _base_counts(wdir, base)
            if before is None and _needs_base_counts(findings, base, state):
                before = _judge_base(wdir, base, meta, craft, reference, formats, tmp / "base")
            _carry_over_unchanged(findings, base, state, before or {})
        outputs = res["outputs"]
        if meta["kind"] == "deck" and "pptx" in formats:
            _export_pptx(meta, tmp / "out" / "slides", tmp / "out" / "deck.pptx", findings, outputs)
        g = guard(base, _base_raws(wdir, base), state, open_)
        ok = g["ok"] and not any(f.get("level") == "fail" for f in findings)
        checks = {"ok": not any(f.get("level") == "fail" for f in findings), "probe": res["probe"],
                  "formats": formats, "findings": findings}
        measure = {"schema": 1, "canvas": meta["size"], "probe": res["probe"], "slides": res["measure"]}
        seal_snapshot(wdir, stage, vid, {"checks.json": checks, "guard.json": g, "measure.json": measure})
        status = "ok" if ok else "failed"
        meta["versions"].append(_entry(vid, "agent", open_.get("scope", []), status, state, rel, outputs,
                                       base=g["base"], note=open_.get("note") or None,
                                       session=_session()))
        if ok:
            meta["current"] = vid
            meta["open"] = None
            meta["slides"] = state["slides"]
            _replace_dir(wdir / "out", tmp / "out")
            _replace_dir(wdir / "review", tmp / "review")
            for old in (wdir / "review").glob("failed-*"):
                shutil.rmtree(old, ignore_errors=True)
        else:
            review = wdir / "review"
            review.mkdir(exist_ok=True)
            for old in review.glob("failed-*"):
                shutil.rmtree(old, ignore_errors=True)
            failed = review / f"failed-{vid}"
            os.replace(tmp / "out", failed)
            if (tmp / "review" / "sheet.jpg").is_file():
                os.replace(tmp / "review" / "sheet.jpg", failed / "sheet.jpg")
        save_meta(wdir, meta)
        register(wdir, meta)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fails = [f for f in findings if f.get("level") == "fail"]
    _print({"version": vid, "status": status, "current": meta["current"], "ids_added": added_ids,
            "guard": {"ok": g["ok"], "violations": g["violations"], "notes": g["notes"]},
            "failed_checks": fails,
            "outputs": str(wdir / "out") if ok else str(wdir / "review" / f"failed-{vid}")})
    return 0 if ok else 1


REFERENCE_COPY = ".reference/measure.json"


def _apply_cut_options(wdir: Path, meta: dict, args) -> None:
    """Store cut's --template / --reference choices in work.json so later cuts reuse them.

    --template T.pptx with --keep-head / --base-index / --keep-tail: the PPTX export puts the
    slides into the person's template (design 13.5). --reference R: R is another work's
    folder (its current version's measure.json is used) or a measure.json file; it is copied
    into this work, because a reference must live inside the work it judges.
    """
    template = getattr(args, "template", None)
    if template:
        tpath = Path(template).expanduser().resolve()
        if not tpath.is_file() or tpath.suffix.lower() != ".pptx":
            raise UsageError(f"--template must be an existing .pptx file: {template}")
        meta["pptx"] = {"template": str(tpath)}
        for key in ("keep_head", "base_index", "keep_tail"):
            val = getattr(args, key, None)
            if val is not None:
                if val < 0 or (key == "base_index" and val < 1):
                    raise UsageError(f"--{key.replace('_', '-')} must be {'>= 1' if key == 'base_index' else '>= 0'}")
                meta["pptx"][key] = val
    elif any(getattr(args, k, None) is not None for k in ("keep_head", "base_index", "keep_tail")):
        raise UsageError("--keep-head / --base-index / --keep-tail need --template")
    ref = getattr(args, "reference", None)
    if ref:
        rpath = Path(ref).expanduser().resolve()
        if rpath.is_dir():
            rmeta = load_meta(rpath)
            cur = rmeta.get("current")
            if not cur:
                raise UsageError(f"reference work {ref} has no current version; cut it once first")
            rpath = rpath / ".versions" / cur / "measure.json"
        if not rpath.is_file():
            raise UsageError(f"--reference must be a work folder or a measure.json file: {ref}")
        dest = wdir / REFERENCE_COPY
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rpath, dest)
        meta["reference"] = REFERENCE_COPY


def _reference(wdir: Path, meta: dict):
    ref = meta.get("reference")
    if not ref:
        return None
    path = _safe_join(wdir, ref)
    if path is None or not path.is_file():
        raise UsageError(f"reference measure must be a file inside the work: {ref}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"reference measure is not readable JSON: {ref} ({exc})") from None


def cmd_render(args) -> int:
    wdir = work_dir_of(args.dir)
    meta = load_meta(wdir)
    _refuse_video(meta)
    formats = _parse_formats(args.formats, meta["kind"])
    root, rel, _ = src_layout(wdir, meta)
    if args.version:
        entry = version_entry(meta, args.version)
        if entry is None:
            raise UsageError(f"no version {args.version}")
        if not args.out:
            raise UsageError("--version needs --out DIR (out/ holds only the current version)")
        out = Path(args.out).expanduser().resolve()
        if _inside(out, wdir) and not _inside(out, (wdir / "review").resolve()):
            raise UsageError("--out must be outside the work folder, or under its review/ folder")
        src_root = wdir / ".versions" / args.version / "src"
        rel = entry.get("source", rel)
        if _safe_join(src_root, rel) is None:
            raise UsageError(f"version {args.version} names a source outside its snapshot")
        review = out / "review"
    else:
        if args.out:
            raise UsageError("--out is only for --version")
        # Edits made with no revision open never reach out/: shown or delivered from there, they
        # would skip the guard, and the next open would store them as the person's version.
        changed, deleted = _unversioned_changes(wdir, meta)
        if changed:
            raise UsageError(
                f"{', '.join(changed)} changed on disk since {meta['current']} and no revision is open. "
                f"If you made these edits: {_open_hint(args.dir, changed, deleted)} "
                f"(cut then checks them against the scope). If the person made them: work.py open "
                f"--dir {args.dir} --scope <ids you will change> (stores theirs as their version). "
                f"Then render again.")
        src_root, out, review = root, wdir / "out", wdir / "review"
    craft = _load_craft()
    res = work_render.render(src_root, rel, meta["kind"], meta["size"], out, review, formats,
                             probe_js=craft.PROBE_JS if craft else None, extra_sizes=meta.get("sizes"))
    if meta["kind"] == "deck" and "pptx" in formats:
        _export_pptx(meta, out / "slides", out / "deck.pptx", res["findings"], res["outputs"])
    fails = [f for f in res["findings"] if f.get("level") == "fail"]
    _print({"out": str(out), "outputs": res["outputs"], "failed_checks": fails})
    return 1 if fails else 0


def cmd_list(args) -> int:
    reg = load_registry()
    for w in reg["works"]:
        w["exists"] = (Path(w["path"]) / "work.json").is_file()
    _print(reg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="work.py", description="Pollinate works: versions, guard, renderer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="make a new work")
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--title", required=True)
    p.add_argument("--style")
    p.add_argument("--size", help="canvas size, e.g. 1920x1080")
    p = sub.add_parser("adopt", help="register an existing work in place")
    p.add_argument("--dir", required=True)
    p.add_argument("--kind", required=True, choices=ALL_KINDS)
    p.add_argument("--src", help="the existing source file (default src/deck.html or src/index.html)")
    p.add_argument("--title")
    p.add_argument("--style")
    p.add_argument("--size")
    p.add_argument("--derived-from", dest="derived_from",
                   help="video only: the earlier film's project folder this one was made from (recorded as derived_from)")
    p = sub.add_parser("open", help="start a revision with a declared scope")
    p.add_argument("--dir", required=True)
    p.add_argument("--scope", required=True, help="slide ids that may change, comma separated (may be empty)")
    p.add_argument("--add", type=int, help="how many slides will be added")
    p.add_argument("--remove", help="slide ids that may be deleted, comma separated")
    p.add_argument("--note", help="what this revision does")
    p.add_argument("--shared", action="store_true",
                   help="allow changes to shared files and to the deck outside its slides")
    p.add_argument("--mine", action="store_true",
                   help="the changes already on disk are the agent's own: keep them in this revision "
                        "(they must fit the scope) instead of storing them as the person's version")
    p = sub.add_parser("cut", help="render, check, guard and store a new version")
    p.add_argument("--dir", required=True)
    p.add_argument("--formats", help="comma separated: html,pdf,png,pptx")
    p.add_argument("--template", help="the person's .pptx template for the PPTX export (kept for later cuts)")
    p.add_argument("--keep-head", dest="keep_head", type=int, help="template slides kept at the start")
    p.add_argument("--base-index", dest="base_index", type=int, help="template slide (1-based) used as the base")
    p.add_argument("--keep-tail", dest="keep_tail", type=int, help="template slides kept at the end")
    p.add_argument("--reference", help="a reference work folder or measure.json to match (kept for later cuts)")
    p = sub.add_parser("render", help="render without making a version")
    p.add_argument("--dir", required=True)
    p.add_argument("--formats")
    p.add_argument("--version", help="render this old version instead of the source on disk")
    p.add_argument("--out", help="output folder for --version")
    sub.add_parser("list", help="print the registry")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handler = {"init": cmd_init, "adopt": cmd_adopt, "open": cmd_open, "cut": cmd_cut,
               "render": cmd_render, "list": cmd_list}[args.cmd]
    try:
        return handler(args)
    except (UsageError, work_render.RenderEnvError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - exit 1 means "a check failed", never a crash
        print(f"error: work.py {args.cmd} stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
