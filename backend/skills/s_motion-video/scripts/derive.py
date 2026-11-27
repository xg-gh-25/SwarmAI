#!/usr/bin/env python3
"""derive.py: start a new film from an old one (a longer cut, a shorter cut, a new version).

  derive.py --from OLD --to NEW --plan plan.json    make the new project NEW from the old project OLD
  derive.py --carry --to NEW                         carry finished renders again (after tts.py and film.py)

The plan lists the new film's scenes in play order. Each entry either reuses an old scene or is new:

  {"storyboard": {"title": "...", "target_seconds": 540},          optional: film-level storyboard fields
   "scenes": [
     {"reuse": "S01"},                                            the old S01 as it was
     {"reuse": "S04", "narration": "the changed line", "title": "..."},   old page, changed fields
     {"title": "...", "narration": "...", "visual": "...", "shot_type": "statement"}   a new scene
   ],
   "copy": ["assets/logo.png"]}                                   optional: more project files to copy

New ids are S01, S02, ... in plan order. A reused scene's page (scenes/<old>.html) is copied byte for byte
to its new id, with the project files it references (assets, shared styles and scripts, at the same
paths). When its narration is identical, its voice (audio/voice/<old>.*) is copied too and its duration
kept; when the narration changed, the scene is marked for re-voicing (no voice, no duration). The old
project's shared.css, film.json, design.md and pronounce.json come along. Old scenes the plan does not
use are listed as dropped: name them to the person before producing the cut.

Renders: a reused scene keeps its finished build/<old>.mp4 when the old render is current (its
fingerprint matches) and everything the picture depends on is the same in the new film apart from its
id: the page and every file it loads, the voice timing and duration, the fade tail, and its own entry in
the film data (scripts/film.py). Then the mp4 is copied and the new fingerprint written with render.py's
own function, so render.py skips it and renders only new and changed scenes. New and re-voiced scenes
have no duration until tts.py runs, and the film data depends on durations, so carrying is repeated by
`derive.py --carry --to NEW` after tts.py and film.py. NEW/derive.json records the old project, every
scene's origin and status, and the dropped scenes.

Exit codes: 0 ok, 2 usage or environment error (missing old project, invalid plan, unknown old scene id,
a target folder that exists and is not empty, a new project without derive.json for --carry).
Nothing is written into an existing folder: the project is built next to it and moved into place.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys

sys.dont_write_bytecode = True     # do not leave __pycache__ in the skill
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as FILM  # noqa: E402
import render as R  # noqa: E402

RECORD = "derive.json"
FILM_DATA_URL = "/project/scenes/film-data.js"
SCENE_KEYS = ("title", "narration", "visual", "shot_type", "duration", "sfx", "transition", "hero")
NEW_REQUIRED = ("title", "narration", "visual", "shot_type")
PROJECT_FILES = ("scenes/shared.css", "film.json", "design.md", "pronounce.json")
VOICE_EXT = (".mp3", ".words.json", ".captions.json", ".tts.json")
SCENE_FILE_RX = re.compile(r"/project/scenes/S\d{2,}\.html\Z")


class UsageError(Exception):
    """bad arguments, plan or projects -> exit 2"""


def quiet(*_a, **_k):
    pass


# ---------------------------------------------------------------- plan

def load_json(path, what):
    try:
        return json.load(open(path, encoding="utf-8"))
    except OSError as e:
        raise UsageError(f"cannot read {what} {path}: {e}")
    except json.JSONDecodeError as e:
        raise UsageError(f"{what} {path} is not valid JSON: {e}")


def safe_rel(rel, what):
    """a project-relative path with no absolute part, '..' or dot segment"""
    if not isinstance(rel, str) or not rel.strip():
        raise UsageError(f"{what}: expected a path inside the project")
    segs = rel.replace("\\", "/").split("/")
    if os.path.isabs(rel) or rel.startswith(("/", "~")) or any(s in ("..", ".") or s.startswith(".") for s in segs if s):
        raise UsageError(f"{what}: '{rel}' must be a relative path inside the project")
    return "/".join(s for s in segs if s)


def check_plan(plan, old_ids):
    if not isinstance(plan, dict):
        raise UsageError("plan: must be a JSON object with a 'scenes' list")
    unknown = sorted(set(plan) - {"storyboard", "scenes", "copy"} - {k for k in plan if k.startswith("_")})
    if unknown:
        raise UsageError(f"plan: unknown key(s) {', '.join(unknown)} (known: storyboard, scenes, copy)")
    sb = plan.get("storyboard", {})
    if not isinstance(sb, dict) or "scenes" in sb:
        raise UsageError("plan: 'storyboard' is an object of film-level fields (not 'scenes')")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise UsageError("plan: 'scenes' must be a non-empty list")
    for k, e in enumerate(scenes, 1):
        where = f"plan scene {k}"
        if not isinstance(e, dict):
            raise UsageError(f"{where}: must be an object")
        bad = sorted(set(e) - set(SCENE_KEYS) - {"reuse"} - {x for x in e if x.startswith("x-")})
        if bad:
            raise UsageError(f"{where}: unknown key(s) {', '.join(bad)} (ids are assigned in plan order; "
                             f"known: reuse, {', '.join(SCENE_KEYS)}, x-*)")
        if "reuse" in e:
            if e["reuse"] not in old_ids:
                raise UsageError(f"{where}: reuses {e['reuse']!r}, which is not a scene of the old project "
                                 f"(have: {', '.join(old_ids)})")
        else:
            miss = [x for x in NEW_REQUIRED if not isinstance(e.get(x), str)]
            if miss:
                raise UsageError(f"{where}: a new scene needs {', '.join(miss)} (or 'reuse' an old scene id)")
        if "narration" in e and not isinstance(e["narration"], str):
            raise UsageError(f"{where}: narration must be text")
    extra = plan.get("copy", [])
    if not isinstance(extra, list):
        raise UsageError("plan: 'copy' is a list of project paths")
    return [safe_rel(x, "plan copy") for x in extra]


def new_storyboard(old_sb, plan):
    """(storyboard, [{id, from, status}])"""
    sb = {k: copy.deepcopy(v) for k, v in old_sb.items() if k != "scenes"}
    sb.update(copy.deepcopy(plan.get("storyboard") or {}))
    old = {s["id"]: s for s in old_sb["scenes"]}
    scenes, rows = [], []
    for k, e in enumerate(plan["scenes"], 1):
        sid = f"S{k:02d}"
        if "reuse" in e:
            src = old[e["reuse"]]
            sc = copy.deepcopy(src)
            for key, v in e.items():
                if key != "reuse":
                    sc[key] = copy.deepcopy(v)
            sc["id"] = sid
            same = (sc.get("narration") or "") == (src.get("narration") or "")
            if same:
                status = "reused"
            else:
                status = "revoice"
                if "duration" not in e:
                    sc["duration"] = None
            rows.append({"id": sid, "from": e["reuse"], "status": status})
        else:
            sc = {"id": sid}
            for key in SCENE_KEYS:
                if key in e:
                    sc[key] = copy.deepcopy(e[key])
            sc.update({k2: copy.deepcopy(v) for k2, v in e.items() if k2.startswith("x-")})
            sc.setdefault("duration", None)
            sc.setdefault("transition", "cut")
            rows.append({"id": sid, "from": None, "status": "new"})
        scenes.append({"id": sc.pop("id"), **sc})
    sb["scenes"] = scenes
    return sb, rows


# ---------------------------------------------------------------- files

def roots_of(project):
    """the same roots render.Server serves (without starting a server)"""
    return {"skill": (R.SKILL_DIR, R.SKILL_SERVED), "project": (os.path.abspath(project), None)}


def project_refs(project, rel):
    """project files (relative paths) a page, style sheet or script references, followed through css and js"""
    roots = roots_of(project)
    out, todo, seen = [], [("/project/" + rel, os.path.join(project, *rel.split("/")))], set()
    while todo:
        url, full = todo.pop()
        if url in seen or not full or not os.path.isfile(full):
            continue
        seen.add(url)
        if url != "/project/" + rel:
            out.append(url[len("/project/"):])
        if full.endswith((".html", ".css", ".js")) and len(seen) < 500:
            text = open(full, "rb").read().decode("utf-8", "replace")
            for u, f in R.referenced_files(roots, text, url):
                if u.startswith("/project/") and u != FILM_DATA_URL and not SCENE_FILE_RX.match(u):
                    todo.append((u, f))
    return out


def inside(root, path):
    """the file a path resolves to (symlinks followed) lies inside root"""
    real, top = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([real, top]) == top


def copy_file(src_root, dst_root, rel):
    """copy a project file to the same relative path; never a file that resolves outside the old project"""
    src = os.path.join(src_root, *rel.split("/"))
    if not os.path.isfile(src) or not inside(src_root, src):
        return False
    dst = os.path.join(dst_root, *rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def copy_voice(old, new, old_id, new_id):
    src_dir, dst_dir = os.path.join(old, "audio", "voice"), os.path.join(new, "audio", "voice")
    copied = []
    for ext in VOICE_EXT:
        src = os.path.join(src_dir, old_id + ext)
        if not os.path.isfile(src) or not inside(old, src):
            continue
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, new_id + ext)
        if ext == ".tts.json":   # tts.py's cache: its key is the text and voice, the id is only a label
            try:
                meta = json.load(open(src, encoding="utf-8"))
                if isinstance(meta, dict) and "scene" in meta:
                    meta["scene"] = new_id
                with open(dst, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                copied.append(ext)
                continue
            except (OSError, ValueError):
                pass
        shutil.copyfile(src, dst)
        copied.append(ext)
    return copied


def remap_film_json(path, id_map):
    """film.json plate images are keyed by scene id: move them to the new ids, drop the ones not reused"""
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plate = cfg.get("plate") if isinstance(cfg, dict) else None
    imgs = plate.get("images") if isinstance(plate, dict) else None
    if not isinstance(imgs, dict) or not imgs:
        return []
    new = {}
    for new_id, old_id in id_map.items():
        if old_id in imgs:
            new[new_id] = imgs[old_id]
    plate["images"] = dict(sorted(new.items()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return sorted(set(imgs.values()) | ({plate["image"]} if isinstance(plate.get("image"), str) else set()))


# ---------------------------------------------------------------- render carry

FL_CALLS = re.compile(r"\bFL\s*\.\s*(?:setup|at)\s*\(")


def reads_film_entry(roots, text, html_url):
    """True unless the page provably uses the film layer only through FL.setup(...) and FL.at(...): any other
    mention of FL or MV_FILM (FL.scene, destructuring, window.MV_FILM), or a project script the page loads (it may
    read them), means the scene's picture may depend on its whole film-data entry (titles, next section)."""
    if re.search(r"\bMV_FILM\b", text) or re.search(r"\bFL\b", FL_CALLS.sub("", text)):
        return True
    return any(u.startswith("/project/") and u.endswith(".js") and u != FILM_DATA_URL
               for u, _f in R.referenced_files(roots, text, html_url))


def inputs_digest(project, roots, data, tail, film_data):
    """Everything render.fingerprint() hashes for a scene, except its id, with scenes/film-data.js replaced by the
    scene's own slice of it (scripts/film.py scene_slice). Two scenes with equal digests render the same picture.
    Mirrors render.fingerprint's walk (page, referenced files through css, runtime, shared.css, data, tail)."""
    h = hashlib.sha256()
    h.update(R.RENDER_VERSION.encode())
    html_url = f"/project/scenes/{data['id']}.html"
    html = open(os.path.join(project, "scenes", f"{data['id']}.html"), "rb").read()
    h.update(html)
    text = html.decode("utf-8", "replace")
    files = list(R.referenced_files(roots, text, html_url))
    files += [("/skill/runtime/" + os.path.basename(f), f) for f in R.RUNTIME_FILES]
    files.append(("/project/scenes/shared.css", os.path.join(project, "scenes", "shared.css")))
    seen, i = set(), 0
    while i < len(files):
        url, f = files[i]
        i += 1
        if url in seen:
            continue
        seen.add(url)
        h.update(url.encode())
        if url == FILM_DATA_URL:
            sl = FILM.scene_slice(film_data, data["id"], reads_entry=reads_film_entry(roots, text, html_url))
            h.update(json.dumps(sl, sort_keys=True, ensure_ascii=False).encode())
        elif f and os.path.isfile(f):
            b = open(f, "rb").read()
            h.update(hashlib.sha256(b).digest())
            if f.endswith(".css") and len(seen) < 200:
                files += R.referenced_files(roots, b.decode("utf-8", "replace"), url)
        else:
            h.update(b"<missing>")
    h.update(json.dumps({k: v for k, v in data.items() if k not in ("id", "index", "count")},
                        sort_keys=True, ensure_ascii=False).encode())
    h.update(json.dumps(tail, ensure_ascii=False).encode())
    return h.hexdigest()


def render_state(project, sb, sid):
    """(data, tail, fingerprint, roots) of a scene as render.py would compute them; raises R.UsageError when the
    scene is not ready (no duration, no page)"""
    ids = [s["id"] for s in sb["scenes"]]
    i = ids.index(sid)
    if not os.path.isfile(os.path.join(project, "scenes", f"{sid}.html")):
        raise R.UsageError(f"missing scenes/{sid}.html")
    data = R.scene_data(project, sb, i, warn=quiet)
    nxt = None
    if sb["scenes"][i].get("transition") == "fade" and i + 1 < len(ids):
        nxt = R.scene_data(project, sb, i + 1, warn=quiet)
    tail = R.tail_spec(sb, i, nxt)
    roots = roots_of(project)
    return data, tail, R.fingerprint(project, roots, data, tail), roots


def outputs(build, sid, tail):
    return [os.path.join(build, f"{sid}.mp4")] + ([os.path.join(build, f"{sid}.tail.mp4")] if tail else [])


def carry(old, new, rows):
    """copy current renders of reused scenes whose picture is unchanged; returns {new id: reason}"""
    result = {}
    try:
        old_sb, new_sb = R.load_storyboard(old), R.load_storyboard(new)
    except R.UsageError as e:
        return {r["id"]: f"not carried: {e}" for r in rows if r.get("from")}
    old_film, new_film = FILM.read_data(old), FILM.read_data(new)
    old_build, new_build = os.path.join(old, "build"), os.path.join(new, "build")
    for r in rows:
        oid, nid = r.get("from"), r["id"]
        if not oid:
            continue
        try:
            nd, nt, nfp, nroots = render_state(new, new_sb, nid)
        except (R.UsageError, ValueError) as e:
            result[nid] = f"not carried yet: {e}"
            continue
        nfp_path = os.path.join(new_build, f"{nid}.fp")
        if all(os.path.isfile(o) for o in outputs(new_build, nid, nt)) and os.path.isfile(nfp_path) \
                and open(nfp_path).read().strip() == nfp:
            result[nid] = "render current"
            continue
        try:
            od, ot, ofp, oroots = render_state(old, old_sb, oid)
        except (R.UsageError, ValueError) as e:
            result[nid] = f"not carried: old {oid} cannot be fingerprinted ({e})"
            continue
        ofp_path = os.path.join(old_build, f"{oid}.fp")
        if not (all(os.path.isfile(o) for o in outputs(old_build, oid, ot)) and os.path.isfile(ofp_path)
                and open(ofp_path).read().strip() == ofp):
            result[nid] = f"not carried: the old render of {oid} is missing or not current"
            continue
        page = open(os.path.join(new, "scenes", f"{nid}.html"), encoding="utf-8", errors="replace").read()
        if new_film is None and "scenes/film-data.js" in page:
            result[nid] = "not carried yet: no scenes/film-data.js (run tts.py and film.py, then derive.py --carry)"
            continue
        if inputs_digest(old, oroots, od, ot, old_film) != inputs_digest(new, nroots, nd, nt, new_film):
            result[nid] = "re-render: its picture changes (page, files, voice, duration, tail or film data)"
            continue
        os.makedirs(new_build, exist_ok=True)
        if os.path.exists(nfp_path):
            os.remove(nfp_path)
        for src, dst in zip(outputs(old_build, oid, ot), outputs(new_build, nid, nt)):
            shutil.copyfile(src, dst)
        if not nt and os.path.exists(os.path.join(new_build, f"{nid}.tail.mp4")):
            os.remove(os.path.join(new_build, f"{nid}.tail.mp4"))
        with open(nfp_path, "w") as f:
            f.write(nfp + "\n")
        result[nid] = f"carried build/{oid}.mp4"
    return result


# ---------------------------------------------------------------- commands

def needs_film_data(project, sb):
    if os.path.isfile(os.path.join(project, "film.json")):
        return True
    for s in sb["scenes"]:
        p = os.path.join(project, "scenes", f"{s['id']}.html")
        if os.path.isfile(p) and "scenes/film-data.js" in open(p, encoding="utf-8", errors="replace").read():
            return True
    return False


def refresh_film_data(project, sb):
    """write scenes/film-data.js when the film uses the layer and every duration is known; returns a note"""
    if not needs_film_data(project, sb):
        return None
    try:
        FILM.write(project)
        return "wrote scenes/film-data.js"
    except FILM.UsageError as e:
        return f"scenes/film-data.js not written yet ({e})"


def write_record(project, rec):
    with open(os.path.join(project, RECORD), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
        f.write("\n")


def derive(old, new, plan_path):
    old, new = os.path.abspath(old), os.path.abspath(new)
    if not os.path.isdir(old):
        raise UsageError(f"old project not found: {old}")
    try:
        old_sb = R.load_storyboard(old)
    except R.UsageError as e:
        raise UsageError(f"old project: {e}")
    if os.path.exists(new) and (not os.path.isdir(new) or os.listdir(new)):
        raise UsageError(f"{new} already exists and is not empty; derive makes a new project folder")
    if os.path.commonpath([old, new]) in (old, new):
        raise UsageError("the new project must not be inside the old one (or the other way round)")
    plan = load_json(plan_path, "plan")
    old_ids = [s["id"] for s in old_sb["scenes"]]
    extra = check_plan(plan, old_ids)
    sb, rows = new_storyboard(old_sb, plan)

    parent = os.path.dirname(new)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(new)}.derive-{os.getpid()}")
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(os.path.join(tmp, "scenes"))
    try:
        copied = set()
        for rel in PROJECT_FILES:
            if copy_file(old, tmp, rel):
                copied.add(rel)
                copied.update(r for r in project_refs(old, rel) if copy_file(old, tmp, r))
        id_map = {r["id"]: r["from"] for r in rows if r["from"]}
        if "film.json" in copied:
            for rel in remap_film_json(os.path.join(tmp, "film.json"), id_map):
                try:
                    rel = safe_rel(rel, "film.json plate image")
                except UsageError:
                    continue
                if copy_file(old, tmp, rel):
                    copied.add(rel)
        for r in rows:
            if not r["from"]:
                continue
            page = f"scenes/{r['from']}.html"
            src = os.path.join(old, "scenes", f"{r['from']}.html")
            if not os.path.isfile(src) or not inside(old, src):    # a page linked from outside is not copied
                r["status"] = "new" if r["status"] == "reused" else r["status"]
                r["note"] = f"the old project has no {page} of its own: write the scene"
                continue
            shutil.copyfile(src, os.path.join(tmp, "scenes", f"{r['id']}.html"))
            for rel in project_refs(old, page):
                if rel not in copied and copy_file(old, tmp, rel):
                    copied.add(rel)
            if r["status"] == "reused":
                r["voice"] = copy_voice(old, tmp, r["from"], r["id"])
        for rel in extra:
            if not copy_file(old, tmp, rel):
                raise UsageError(f"plan copy: {rel} is not a file of the old project")
            copied.add(rel)
        with open(os.path.join(tmp, "storyboard.json"), "w", encoding="utf-8") as f:
            json.dump(sb, f, ensure_ascii=False, indent=2)
            f.write("\n")
        with open(os.path.join(tmp, "derive-plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if os.path.isdir(new):
            os.rmdir(new)          # empty (checked above)
        os.replace(tmp, new)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    used = {r["from"] for r in rows if r["from"]}
    dropped = [{"id": s["id"], "title": s.get("title") or "", "narration": s.get("narration") or ""}
               for s in old_sb["scenes"] if s["id"] not in used]
    film_note = refresh_film_data(new, sb)
    rec = {"from": old, "plan": "derive-plan.json", "scenes": rows, "dropped": dropped, "copied": sorted(copied)}
    for nid, why in carry(old, new, rows).items():
        next(r for r in rows if r["id"] == nid)["render"] = why
    write_record(new, rec)
    return rec, film_note


def carry_again(new):
    new = os.path.abspath(new)
    p = os.path.join(new, RECORD)
    if not os.path.isfile(p):
        raise UsageError(f"{new} has no {RECORD}: it was not made by derive.py")
    rec = load_json(p, RECORD)
    if not isinstance(rec, dict) or not isinstance(rec.get("from"), str) or not isinstance(rec.get("scenes"), list):
        raise UsageError(f"{p} is not a derive record")
    if not os.path.isdir(rec["from"]):
        raise UsageError(f"the old project {rec['from']} is gone; nothing to carry")
    try:
        sb = R.load_storyboard(new)
    except R.UsageError as e:
        raise UsageError(str(e))
    film_note = refresh_film_data(new, sb)
    rows = rec["scenes"]
    for nid, why in carry(rec["from"], new, rows).items():
        next(r for r in rows if r["id"] == nid)["render"] = why
    write_record(new, rec)
    return rec, film_note


def report(rec, new, film_note, first):
    rows = rec["scenes"]
    for r in rows:
        src = f"from {r['from']}" if r.get("from") else "new"
        extra = "; ".join(x for x in (r.get("note"), r.get("render")) if x)
        print(f"{r['id']}: {r['status']:8} {src:9}" + (f"  {extra}" if extra else ""))
    revoice = [r["id"] for r in rows if r["status"] == "revoice"]
    new_ids = [r["id"] for r in rows if r["status"] == "new"]
    if revoice:
        print("re-voice (narration changed): " + ", ".join(revoice))
    if new_ids:
        print("write (new scenes): " + ", ".join(new_ids))
    if first and rec.get("dropped"):
        print("dropped from the old film (name these to the person before producing):")
        for d in rec["dropped"]:
            print(f"  {d['id']} {d['title']}")
    if film_note:
        print(film_note)
    if first:
        q = '"' + new + '"'
        print("next: register it (work.py adopt --kind video --dir " + q + " --derived-from \"" + rec["from"] + "\"), "
              "run tts.py, film.py, then derive.py --carry --to " + q + ", write the new scenes, check, render")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Start a new film from an old one by a plan; carry unchanged renders.")
    ap.add_argument("--from", dest="old", help="the old project directory")
    ap.add_argument("--to", dest="new", required=True, help="the new project directory (must not exist or be empty)")
    ap.add_argument("--plan", help="plan.json: the new scenes in order, each new or reusing an old scene id")
    ap.add_argument("--carry", action="store_true", help="only carry renders again (after tts.py and film.py)")
    a = ap.parse_args(argv)
    try:
        if a.carry:
            if a.old or a.plan:
                raise UsageError("--carry takes only --to")
            rec, note = carry_again(a.new)
            report(rec, os.path.abspath(a.new), note, False)
        else:
            if not a.old or not a.plan:
                raise UsageError("give --from <old project> --to <new project> --plan <plan.json> (or --carry --to)")
            rec, note = derive(a.old, a.new, a.plan)
            report(rec, os.path.abspath(a.new), note, True)
        return 0
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
