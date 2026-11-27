#!/usr/bin/env python3
"""film.py: write the film data the shared film layer (runtime/film-layer.js) reads.

  film.py --project P            write P/scenes/film-data.js from storyboard.json and film.json
  film.py --project P --check    exit 1 when scenes/film-data.js is missing or no longer matches them

The film layer draws the parts every scene of a film shares: a background plate, a mask, a vignette,
the chapter kicker, the film-wide progress bar with chapter marks and the shot-long camera push.
A scene page cannot read storyboard.json (no network), so this script writes what each scene needs:
the film length, every scene's start (the sum of the earlier scenes' frame-rounded durations, which is
where the scene starts in the joined film; fades keep the length), the chapter marks, each scene's
chapter kicker and the title of the section that follows, plus the project's layer settings from
film.json (template: templates/film.json) and the style's classes and tokens (runtime/film-styles.json).

Run it after every tts.py (tts.py rewrites the durations) and after changing film.json, a plate image,
or the storyboard's order, titles or shot types. A scene whose duration no longer matches the data stops
with a page error that names this script, so check.py and render.py cannot render stale progress data.

Each scene's entry holds everything the layer draws in that scene, and the layer reads nothing else;
derive.py relies on this to tell whether a reused scene's picture is unchanged (scene_slice()).

Exit codes: 0 ok, 1 --check found stale or missing data, 2 usage or environment error
(no storyboard, a scene without a duration, an invalid film.json, a missing plate image).
"""
import argparse
import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True     # do not leave __pycache__ in the skill

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
STYLES_FILE = os.path.join(SKILL_DIR, "runtime", "film-styles.json")
DATA_REL = os.path.join("scenes", "film-data.js")
DATA_PREFIX = "window.MV_FILM = "
VERSION = 1

MASKS = ("left", "right", "center", "warm")
DEFAULTS = {
    "plate": False,        # false, or {"image": "assets/x.jpg", "images": {"S03": "assets/y.jpg"}, "class": "", "drift": 0.09}
    "mask": False,         # false or left | right | center | warm (a scene may pick another side)
    "vignette": False,     # false, true, or extra class names (e.g. "center")
    "kicker": False,       # chapter kicker: chapter number, chapter name, section title
    "progress": False,     # film-wide progress bar with chapter marks
    "push": 1.03,          # camera scale at the scene end (shot-long, log space); false = no push
    "origin": "50% 45%",   # camera transform origin
    "chapters": ["chapter-card"],   # shot types that open a chapter
    "skip": {"kicker": ["title-card", "chapter-card", "logo-endcard"],
             "progress": ["title-card", "logo-endcard"]},
    "classes": {},         # per part: the style's class (overrides runtime/film-styles.json)
    "tokens": {},          # accent | ink | muted | rule | font -> a CSS variable of the style, e.g. "--eb-accent"
}
PARTS = ("plate", "mask", "vignette", "kicker", "progress", "progress_fill", "progress_mark")
TOKENS = ("accent", "ink", "muted", "rule", "font")
PLATE_KEYS = ("image", "images", "class", "drift")
VAR_RX = re.compile(r"--[A-Za-z0-9_-]+\Z")
CLASS_RX = re.compile(r"[A-Za-z0-9_{} -]*\Z")
# A chapter title carries its number only when it is clearly marked, so the kicker never shows a number the title
# does not have, or cuts a name: zero-padded digits then a space or a separator ("01 拐点", "01 - Intro", and
# "01 -Intro": after a space a separator may touch the name, unless the name starts with a digit or sign such as
# "01 -5 degrees"), digits then a separator ("2. Growth", "2 - Growth", "3、拐点"), or the chapter's own roman
# numeral then a separator ("IV: Results" for the fourth chapter). Otherwise a Latin separator needs a space after
# it; CJK punctuation does not. "3 lessons", "CI pipelines", "3.5 billion users", "10:30 standup", "CI-pipelines",
# "C. elegans" are names.
_SEP = r"\s*[.:\-–—]\s+|\s*[、：·]\s*"
CHAPTER_RX = re.compile(r"\s*(?:(0\d{1,2})(?:" + _SEP + r"|\s+[.:\-–—](?![\d.:\-–—])\s*|\s+)|(\d{1,3}|[IVXLC]{1,7})(?:" + _SEP + r"))(\S.*)\Z")


def roman(n):
    """the roman numeral of a chapter ordinal (a title's numeral counts only when it is the chapter's own)"""
    out = ""
    for v, sym in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + sym, n - v
    return out


class UsageError(Exception):
    """missing storyboard, scene without duration, invalid film.json -> exit 2"""


# ---------------------------------------------------------------- inputs

def load_storyboard(project):
    p = os.path.join(project, "storyboard.json")
    if not os.path.isfile(p):
        raise UsageError(f"no storyboard.json in {project}")
    try:
        sb = json.load(open(p, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"storyboard.json is not valid JSON: {e}")
    fps = sb.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise UsageError("storyboard.json: 'fps' must be a positive number")
    scenes = sb.get("scenes")
    if not isinstance(scenes, list) or not scenes or not all(isinstance(s, dict) for s in scenes):
        raise UsageError("storyboard.json: 'scenes' must be a non-empty list of objects")
    missing = [str(s.get("id")) for s in scenes
               if not isinstance(s.get("duration"), (int, float)) or isinstance(s.get("duration"), bool) or s["duration"] <= 0]
    if missing:
        raise UsageError("no duration yet for " + ", ".join(missing) +
                         ": run tts.py (scenes without narration get a hand-set duration), then film.py")
    ids = [s.get("id") for s in scenes]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and re.fullmatch(r"S\d{2,}", i) for i in ids):
        raise UsageError("storyboard.json: scene ids must be unique S01, S02, ...")
    return sb


def project_file(project, rel, what):
    """a file inside the project (no absolute path, no '..'); returns its absolute path"""
    if not isinstance(rel, str) or not rel.strip():
        raise UsageError(f"film.json: {what} must be a path inside the project, e.g. assets/plate.jpg")
    segs = rel.replace("\\", "/").split("/")
    if os.path.isabs(rel) or rel.startswith(("/", "~")) or any(s in ("..", ".") or s.startswith(".") for s in segs if s):
        raise UsageError(f"film.json: {what} '{rel}' must be a relative path inside the project")
    full = os.path.realpath(os.path.join(project, *segs))
    root = os.path.realpath(project)
    if os.path.commonpath([full, root]) != root or not os.path.isfile(full):
        raise UsageError(f"film.json: {what} '{rel}' does not exist in the project")
    return full


def file_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def load_options(project):
    """film.json merged onto DEFAULTS; validated so a typo fails here, not silently in the picture"""
    opts = json.loads(json.dumps(DEFAULTS))
    p = os.path.join(project, "film.json")
    if not os.path.isfile(p):
        return opts
    try:
        user = json.load(open(p, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"film.json is not valid JSON: {e}")
    if not isinstance(user, dict):
        raise UsageError("film.json must be a JSON object")
    user = {k: v for k, v in user.items() if not k.startswith("_")}     # "_about" and other notes
    unknown = sorted(set(user) - set(DEFAULTS))
    if unknown:
        raise UsageError(f"film.json: unknown key(s) {', '.join(unknown)} (known: {', '.join(DEFAULTS)})")
    for k, v in user.items():
        if k == "skip":
            if not isinstance(v, dict) or set(v) - {"kicker", "progress"} or \
                    not all(isinstance(x, list) and all(isinstance(t, str) for t in x) for x in v.values()):
                raise UsageError('film.json: "skip" is {"kicker": [shot types], "progress": [shot types]}')
            opts["skip"].update(v)
        else:
            opts[k] = v
    for k in ("vignette",):
        if not isinstance(opts[k], (bool, str)) or (isinstance(opts[k], str) and not CLASS_RX.match(opts[k])):
            raise UsageError(f'film.json: "{k}" is true, false or class names')
    for k in ("kicker", "progress"):
        if not isinstance(opts[k], bool):
            raise UsageError(f'film.json: "{k}" is true or false')
    if opts["mask"] is not False and opts["mask"] not in MASKS:
        raise UsageError(f'film.json: "mask" is false or one of {", ".join(MASKS)}')
    push = opts["push"]
    if push is not False and (not isinstance(push, (int, float)) or isinstance(push, bool) or not 0.5 <= push <= 2):
        raise UsageError('film.json: "push" is false or the camera scale at the scene end (e.g. 1.03)')
    if not isinstance(opts["origin"], str) or not re.fullmatch(r"[\w%. -]+", opts["origin"]):
        raise UsageError('film.json: "origin" is a CSS transform origin such as "50% 45%"')
    if not isinstance(opts["chapters"], list) or not all(isinstance(x, str) for x in opts["chapters"]):
        raise UsageError('film.json: "chapters" is a list of shot types')
    if not isinstance(opts["classes"], dict) or set(opts["classes"]) - set(PARTS) or \
            not all(isinstance(v, str) and CLASS_RX.match(v) for v in opts["classes"].values()):
        raise UsageError(f'film.json: "classes" maps parts ({", ".join(PARTS)}) to class names')
    if not isinstance(opts["tokens"], dict) or set(opts["tokens"]) - set(TOKENS) or \
            not all(isinstance(v, str) and VAR_RX.match(v) for v in opts["tokens"].values()):
        raise UsageError(f'film.json: "tokens" maps {", ".join(TOKENS)} to a CSS variable of the style, e.g. "--eb-accent" '
                         "(the layer never brings its own colours or fonts)")
    plate = opts["plate"]
    if plate is True:
        plate = opts["plate"] = {}
    if plate is not False:
        if not isinstance(plate, dict) or set(plate) - set(PLATE_KEYS):
            raise UsageError(f'film.json: "plate" is false or an object with {", ".join(PLATE_KEYS)}')
        drift = plate.get("drift", 0.09)
        if not isinstance(drift, (int, float)) or isinstance(drift, bool) or not 0 <= drift <= 0.5:
            raise UsageError('film.json: plate "drift" is the extra scale at the scene start, 0 to 0.5 (e.g. 0.09)')
        plate["drift"] = drift
        if "class" in plate and (not isinstance(plate["class"], str) or not CLASS_RX.match(plate["class"])):
            raise UsageError('film.json: plate "class" is class names')
        if "images" in plate and (not isinstance(plate["images"], dict)):
            raise UsageError('film.json: plate "images" maps scene ids to images, e.g. {"S03": "assets/b.jpg"}')
    return opts


def style_entry(style):
    try:
        table = json.load(open(STYLES_FILE, encoding="utf-8"))["styles"]
    except (OSError, ValueError, KeyError) as e:
        raise UsageError(f"cannot read {os.path.relpath(STYLES_FILE, SKILL_DIR)}: {e}")
    e = table.get(style) or {}
    return dict(e.get("classes") or {}), dict(e.get("tokens") or {})


# ---------------------------------------------------------------- the data

def chapter_of(title, ordinal):
    m = CHAPTER_RX.match(str(title or ""))
    if m and (m.group(1) or m.group(2).isdigit() or m.group(2) == roman(ordinal)):
        return {"no": m.group(1) or m.group(2), "name": m.group(3).strip()}
    return {"no": f"{ordinal:02d}", "name": str(title or "").strip()}


def build_data(project):
    """the film data as a dict (what scenes/film-data.js holds)"""
    project = os.path.abspath(project)
    sb = load_storyboard(project)
    opts = load_options(project)
    fps = sb["fps"]
    scenes = sb["scenes"]
    classes, tokens = style_entry(sb.get("style") or "")
    classes.update(opts.pop("classes"))
    tokens.update(opts.pop("tokens"))

    frames = [max(1, int(round(float(s["duration"]) * fps))) for s in scenes]
    total_f = sum(frames)
    total = total_f / fps
    starts, marks_s, at = {}, [], 0
    chap_types = set(opts["chapters"])
    for s, n in zip(scenes, frames):
        starts[s["id"]] = round(at / fps, 6)
        if s.get("shot_type") in chap_types:
            marks_s.append(round(at / fps, 6))
        at += n

    plate = opts["plate"]
    plate_files = {}
    if plate is not False:
        for sid, rel in sorted((plate.get("images") or {}).items()):
            if sid not in starts:
                raise UsageError(f"film.json: plate images names {sid}, which is not in storyboard.json")
            plate_files[sid] = rel
        default_img = plate.get("image")
        layer_plate = {"class": plate.get("class", ""), "drift": plate["drift"]}
        opts["plate"] = layer_plate
    else:
        default_img = None

    out_scenes, chapter, ordinal = {}, None, 0
    for i, (s, n) in enumerate(zip(scenes, frames)):
        sid, st = s["id"], s.get("shot_type")
        nxt = scenes[i + 1] if i + 1 < len(scenes) else None
        is_chap = st in chap_types
        if is_chap:
            ordinal += 1
            chapter = chapter_of(s.get("title"), ordinal)
        section = s.get("title") or ""
        if is_chap:   # a chapter card names the section that follows it
            section = (nxt.get("title") or "") if nxt and nxt.get("shot_type") not in chap_types else ""
        e = {"title": s.get("title") or "", "shot_type": st, "duration": round(n / fps, 6), "parity": i % 2,
             "chapter": dict(chapter) if chapter else None, "section": section,
             "next": (nxt.get("title") or "") if nxt else None, "kicker": None, "progress": None, "plate_image": None}
        if opts["kicker"] and chapter and st not in opts["skip"]["kicker"]:
            e["kicker"] = {"no": chapter["no"], "name": chapter["name"], "section": section}
        if opts["progress"] and st not in opts["skip"]["progress"]:
            e["progress"] = {"from": round(starts[sid] / total, 6), "to": round((starts[sid] + n / fps) / total, 6),
                             "marks": [round(m / total, 6) for m in marks_s]}
        img = plate_files.get(sid, default_img)
        if plate is not False and img:
            full = project_file(project, img, f"plate image for {sid}")
            e["plate_image"] = {"src": "/project/" + img.replace("\\", "/"), "sha": file_sha(full)}
        out_scenes[sid] = e
    return {
        "version": VERSION,
        "film": {"fps": fps, "total": round(total, 6), "starts": starts, "marks": marks_s},
        "layer": {"style": sb.get("style") or "", "options": opts, "classes": classes, "tokens": tokens},
        "scenes": out_scenes,
    }


DRAWN = ("duration", "kicker", "progress", "plate_image")   # what the layer itself draws from a scene's entry


def scene_slice(data, sid, reads_entry=True):
    """What decides one scene's picture in the film data: the layer settings and the scene's own entry, never the
    film totals (a scene's progress range already holds them). reads_entry=False (the scene's own code never reads
    FL.scene) keeps only what the layer draws, so a reused scene whose neighbours changed still matches. The
    drift direction (parity) counts only when the plate is on."""
    if not isinstance(data, dict):
        return None
    layer = data.get("layer") or {}
    entry = (data.get("scenes") or {}).get(sid)
    if isinstance(entry, dict) and not reads_entry:
        keep = DRAWN + (("parity",) if (layer.get("options") or {}).get("plate") else ())
        entry = {k: entry.get(k) for k in keep}
    return {"version": data.get("version"), "layer": layer, "scene": entry}


def render_text(data):
    head = ("/* scenes/film-data.js: written by scripts/film.py from storyboard.json and film.json; do not edit.\n"
            " * Re-run film.py after every tts.py run. Read by the film layer (runtime/film-layer.js). */\n")
    return head + DATA_PREFIX + json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + ";\n"


def read_data(project):
    """the data in scenes/film-data.js, or None when the file is missing or unreadable"""
    p = os.path.join(project, DATA_REL)
    try:
        text = open(p, encoding="utf-8").read()
    except OSError:
        return None
    at = text.find(DATA_PREFIX)
    if at < 0:
        return None
    body = text[at + len(DATA_PREFIX):].rstrip().rstrip(";")
    try:
        return json.loads(body)
    except ValueError:
        return None


def write(project):
    data = build_data(project)
    p = os.path.join(project, DATA_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_text(data))
    os.replace(tmp, p)
    return data


def is_current(project):
    p = os.path.join(project, DATA_REL)
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8", newline="") as f:
        return f.read() == render_text(build_data(project))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write scenes/film-data.js (film length, scene starts, chapters, layer "
                                             "settings) for the shared film layer. Re-run after every tts.py.")
    ap.add_argument("--project", required=True, help="project directory (contains storyboard.json)")
    ap.add_argument("--check", action="store_true", help="only check: exit 1 when film-data.js is missing or stale")
    a = ap.parse_args(argv)
    proj = os.path.abspath(a.project)
    try:
        if not os.path.isdir(proj):
            raise UsageError(f"project directory not found: {proj}")
        if a.check:
            if is_current(proj):
                print("film-data.js is current")
                return 0
            print(f'film-data.js is missing or stale: run "$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/film.py" --project {a.project}', file=sys.stderr)
            return 1
        data = write(proj)
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    f = data["film"]
    on = [k for k in ("plate", "mask", "vignette", "kicker", "progress", "push") if data["layer"]["options"].get(k)]
    print(f"wrote {DATA_REL}: {len(data['scenes'])} scenes, {f['total']:.3f} s, chapter marks at "
          f"{', '.join(f'{m:.2f}' for m in f['marks']) or 'none'}; layer parts on: {', '.join(on) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
