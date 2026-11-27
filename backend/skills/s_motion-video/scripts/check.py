#!/usr/bin/env python3
"""Pre-render checks for scene pages and the storyboard.

  check.py --project P                 all scenes + film-level storyboard checks -> review/check.json
  check.py --project P --scene S03     one scene (scene-level checks only)       -> review/check-S03.json
  --no-render                          skip the 3 preview stills (static + storyboard only)

(a) static scan of scenes/<id>.html and scenes/shared.css: network/URLs, Math.random, wall clock, timers,
    CSS animation/transition, gsap.to/from (fromTo/set only), infinite repeat, missing runtime or MV.register;
(b) 3 preview stills per scene (via render.py): page errors, missing files, blocked network, blank frames
    (pixel std < 4), visible author text inside the caption band or outside the safe area;
(c) storyboard: schema (schemas/storyboard.schema.json when jsonschema is installed), ids in order, durations,
    sfx limits, title length, narration estimate vs duration and vs target_seconds (warn +10 %, fail +20 %).
Exit codes: 0 no failure (warnings allowed), 1 at least one failure, 2 usage/env error.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True     # do not leave __pycache__ in the skill
sys.path.insert(0, HERE)
import render as R  # noqa: E402

SKILL_DIR = os.path.dirname(HERE)
BLANK_STD = 4.0
ZH_CHARS_PER_S, EN_WORDS_PER_S = 4.5, 2.6
VOICE_PAD, MIN_SCENE = 0.4, 2.0
SFX_PER_SCENE, SFX_PER_FILM = 2, 8
TITLE_CJK, TITLE_WORDS = 10, 5

# ---------------------------------------------------------------- (a) static scan

RULES = [  # (rule id, regex, message) — applied to comment-stripped text, reported as file:line
    ("external-url", re.compile(r"\b(?:https?|wss?|ftp|file):|(?:src|href)\s*=\s*[\"']//|url\(\s*[\"']?//", re.I),
     "no external URLs: put fonts/images in assets/ and load /project/assets/..."),
    ("network-api", re.compile(r"\bfetch\s*\(|XMLHttpRequest|\bWebSocket\b|\bEventSource\b|sendBeacon|RTCPeerConnection|importScripts|\bimport\s*\("),
     "no network or dynamic import in scenes"),
    ("navigation", re.compile(r"window\s*\.\s*open\b|\blocation\s*\.\s*(?:href|assign|replace|reload)\b|document\s*\.\s*location\b|<\s*iframe\b", re.I),
     "no navigation or iframes"),
    ("math-random", re.compile(r"Math\s*\.\s*random"), "Math.random is not seekable: use const r = MV.rand('seed'); r()"),
    ("wall-clock", re.compile(r"Date\s*\.\s*now|new\s+Date\b|\bDate\s*\(\s*\)|performance\s*\.\s*now"),
     "no wall clock: the picture may depend only on the timeline time"),
    ("timer", re.compile(r"\bset(?:Timeout|Interval)\s*\(|requestAnimationFrame|requestIdleCallback"),
     "no timers or rAF: add every tween to the timeline before MV.register(tl)"),
    ("css-animation", re.compile(r"@keyframes|\banimation(?:-name)?\s*:", re.I), "no CSS animations: use timeline fromTo"),
    ("css-transition", re.compile(r"\btransition\s*:", re.I), "no CSS transitions: they are not seekable"),
    ("gsap-to-from", re.compile(r"\b(?!Array\b|Object\b|Buffer\b|Set\b|Map\b|\w*Array\b)[A-Za-z_$][\w$]*\s*\.\s*(?:to|from|staggerTo|staggerFrom|staggerFromTo)\s*\("),
     "use tl.fromTo (both states) or tl.set at an absolute time; .to/.from fight the initial state under seek"),
    ("infinite-repeat", re.compile(r"\brepeat\s*:\s*-\s*1\b"), "finite repeats only: repeat: Math.max(0, Math.floor(secs / period) - 1)"),
    ("audio-element", re.compile(r"<\s*audio\b", re.I), "no sound in the picture: audio is mixed by mix.py"),
    ("play-call", re.compile(r"\btl\s*\.\s*(?:play|resume|restart)\s*\(|\bgsap\s*\.\s*ticker\b"), "the renderer seeks the paused timeline; never play it"),
]
WARN_RULES = [
    ("relative-position", re.compile(r",\s*[\"'](?:[+-]=[\d.]+|<[\d.+=-]*|>[\d.+=-]*)[\"']\s*\)"),
     "position tweens at absolute seconds (MV.cue(...) or a number), not relative strings"),
    ("layout-tween", re.compile(r"fromTo\s*\([^;]*\{[^}]*\b(?:left|top|width|height|margin\w*)\s*:"),
     "tween x/y/scale/opacity, not left/top/width/height/margin"),
]
ALLOW_URL = re.compile(r"https?://www\.w3\.org/")


def strip_comments(text):
    """blank out <!-- -->, /* */ and // comments (keeps line numbers); '//' after ':' (URLs) is kept"""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"(?<![:\\\"'])//[^\n]*", blank, text)


def scan_file(path, rel, is_scene):
    out = []
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return [{"level": "fail", "file": rel, "line": 0, "rule": "unreadable", "msg": str(e)}]
    text = strip_comments(raw)
    for i, line in enumerate(text.splitlines(), 1):
        for level, rules in (("fail", RULES), ("warn", WARN_RULES)):
            for rid, rx, msg in rules:
                if not rx.search(line):
                    continue
                if rid == "external-url" and ALLOW_URL.search(line) and not re.search(r"(?:https?|wss?|ftp|file):(?!//www\.w3\.org/)", line):
                    continue
                out.append({"level": level, "file": rel, "line": i, "rule": rid, "msg": msg, "text": line.strip()[:140]})
    if is_scene:
        for m in re.finditer(r"<\s*video\b[^>]*>", text, re.I):
            if "muted" not in m.group(0):
                out.append({"level": "fail", "file": rel, "line": text[:m.start()].count("\n") + 1, "rule": "video-not-muted",
                            "msg": "<video> must be muted (audio is mixed separately)", "text": m.group(0)[:140]})
        for need, rid in (("/skill/runtime/gsap.min.js", "missing-gsap"), ("/skill/runtime/scene-runtime.js", "missing-runtime")):
            if need not in text:
                out.append({"level": "fail", "file": rel, "line": 0, "rule": rid, "msg": f'load <script src="{need}"> in <head>'})
        if not re.search(r"\bMV\s*\.\s*register\s*\(", text):
            out.append({"level": "fail", "file": rel, "line": 0, "rule": "missing-register",
                        "msg": "call MV.register(tl) after every tween is added"})
        if text.find("gsap.min.js") > text.find("scene-runtime.js") >= 0:
            out.append({"level": "fail", "file": rel, "line": 0, "rule": "script-order", "msg": "load gsap.min.js before scene-runtime.js"})
    return out


# ---------------------------------------------------------------- (c) storyboard

def is_cjk_lang(lang):
    return str(lang or "").lower().startswith(("zh", "ja", "ko"))


def speech_estimate(text, lang):
    t = text or ""
    if is_cjk_lang(lang) or re.search(r"[一-鿿]", t):
        n = len(re.findall(r"[^\s\W_]", t)) - len(re.findall(r"[A-Za-z0-9]", t))   # CJK chars
        words = len(re.findall(r"[A-Za-z0-9]+", t))                                 # Latin words inside CJK text
        return n / ZH_CHARS_PER_S + words / EN_WORDS_PER_S
    return len(re.findall(r"[\w'’-]+", t)) / EN_WORDS_PER_S


def title_ok(title):
    t = str(title or "").strip()
    if re.search(r"[぀-ヿ一-鿿가-힯]", t):
        return len(re.sub(r"\s", "", t)) <= TITLE_CJK
    return len(t.split()) <= TITLE_WORDS


def storyboard_checks(project, sb, only):
    items = []

    def add(level, where, msg):
        items.append({"level": level, "where": where, "msg": msg})

    schema_p = os.path.join(SKILL_DIR, "schemas", "storyboard.schema.json")
    if os.path.isfile(schema_p):
        try:
            import jsonschema
            schema = json.load(open(schema_p, encoding="utf-8"))
            for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(sb), key=lambda e: list(e.absolute_path)):
                path = list(e.absolute_path)
                where = "storyboard" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)
                if only is not None:
                    if len(path) < 2 or path[0] != "scenes" or path[1] != only:
                        continue
                add("fail", where, f"schema: {e.message[:200]}")
        except ImportError:
            add("warn", "storyboard", "jsonschema not installed; schema validation skipped (field checks below still run)")
    else:
        for k in ("title", "lang", "width", "height", "fps", "style", "captions", "scenes"):
            if k not in sb and only is None:
                add("fail", "storyboard", f"missing field '{k}'")

    scenes = sb.get("scenes") if isinstance(sb.get("scenes"), list) else []
    lang = sb.get("lang")
    total_est, total_speech, total_sfx = 0.0, 0.0, 0
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            continue
        sid = s.get("id", f"#{i + 1}")
        want_id = f"S{i + 1:02d}"
        here = only is None or i == only
        sfx = s.get("sfx") or []
        total_sfx += len(sfx) if isinstance(sfx, list) else 0
        speech = speech_estimate(s.get("narration"), lang)
        total_speech += speech
        d = s.get("duration")
        total_est += float(d) if isinstance(d, (int, float)) else max(speech + VOICE_PAD, MIN_SCENE)
        if not here:
            continue
        if sid != want_id:
            add("fail", sid, f"scene {i + 1} must have id {want_id} (ids are S01, S02, ... in play order)")
        for k in ("title", "narration", "visual", "shot_type"):
            if k not in s:
                add("fail", sid, f"missing field '{k}'")
        if not isinstance(d, (int, float)):
            add("fail", sid, "duration is not set: run tts.py (or set it by hand for a scene without narration)")
        else:
            if d < MIN_SCENE:
                add("fail", sid, f"duration {d}s is below the {MIN_SCENE}s minimum")
            if s.get("narration") and speech + VOICE_PAD > d * 1.2:
                add("warn", sid, f"narration estimate {speech:.1f}s + {VOICE_PAD}s does not fit duration {d}s; re-run tts.py")
        if not title_ok(s.get("title")):
            add("fail", sid, f"title too long: '{s.get('title')}' (at most {TITLE_CJK} chars / {TITLE_WORDS} words)")
        if isinstance(sfx, list):
            if len(sfx) > SFX_PER_SCENE:
                add("fail", sid, f"{len(sfx)} sfx; at most {SFX_PER_SCENE} per scene")
            for x in sfx:
                at = x.get("at") if isinstance(x, dict) else None
                if not isinstance(at, (int, float)) or at < 0 or (isinstance(d, (int, float)) and at >= d):
                    add("fail", sid, f"sfx {x!r}: 'at' must be seconds inside the scene")
        tr = s.get("transition", "cut")
        if tr not in ("cut", "fade"):
            add("fail", sid, f"transition '{tr}' must be cut or fade")
        if tr == "fade" and i == len(scenes) - 1:
            add("warn", sid, "fade on the last scene has nothing to fade into (treated as cut)")
        if not os.path.isfile(os.path.join(project, "scenes", f"{sid}.html")):
            add("fail", sid, f"scenes/{sid}.html does not exist")
        nar = s.get("narration") or ""
        if nar and isinstance(d, (int, float)):
            vdir = os.path.join(project, "audio", "voice")
            if not os.path.isfile(os.path.join(vdir, f"{sid}.words.json")):
                add("warn", sid, f"no audio/voice/{sid}.words.json: MV.cue uses a text estimate until tts.py runs")
    if only is None:
        if total_sfx > SFX_PER_FILM:
            add("fail", "storyboard", f"{total_sfx} sfx in the film; at most {SFX_PER_FILM}")
        tgt = sb.get("target_seconds")
        if isinstance(tgt, (int, float)) and tgt > 0:
            for name, val in (("film length", total_est), ("narration estimate", total_speech)):
                over = val / tgt - 1
                if over > 0.2 and name == "film length":
                    add("fail", "storyboard", f"{name} {val:.1f}s is {over:.0%} over target_seconds {tgt}s (limit +20 %): cut words, don't speed up")
                elif over > 0.1:
                    add("warn", "storyboard", f"{name} {val:.1f}s is {over:.0%} over target_seconds {tgt}s (warn at +10 %)")
    return items


# ---------------------------------------------------------------- (b) stills

def pixel_std(path):
    from PIL import Image, ImageStat
    im = Image.open(path).convert("L").resize((160, 90))
    return float(ImageStat.Stat(im).stddev[0])


def faded_in_scenes(sb):
    """Scenes whose first 0.4 s is covered by the previous scene's crossfade (its transition is "fade")."""
    sc = sb.get("scenes", [])
    return {sc[i]["id"] for i in range(1, len(sc)) if sc[i - 1].get("transition") == "fade"}


MIN_MOVE_S = 0.25   # project-contract: every move lasts at least 0.25 s
SETTLE_S = 0.3      # project-contract: the last motion settles 0.3 s before the scene end


def tween_checks(sid, tweens, duration, fps, add):
    """Move length and settle time read from the registered timeline itself (exact; a pixel diff cannot see the
    long eased tail of a power4.out move). duration 0 = gsap.set, allowed. A tween that starts at 0 and spans the
    whole scene is the shot-long camera push and may run to the end."""
    frame = 1.0 / fps
    for tw in tweens:
        st, d, name = tw["start"], tw["duration"], tw["target"]
        if 0 < d < MIN_MOVE_S - 1e-6:
            add("fail", sid, f"tween on {name} at {st:.2f}s lasts {d:.2f}s: every move lasts at least {MIN_MOVE_S} s")
        whole = st <= frame and d >= duration - 2 * frame
        if duration and not whole and d > 0 and st + d > duration - SETTLE_S + frame:
            add("warn", sid, f"tween on {name} ends at {st + d:.2f}s, after MV.end() = {duration - SETTLE_S:.2f}s: "
                             f"clamp its start with Math.min(MV.cue(..), MV.end() - {d:g})")


def still_checks(project, previews, faded_in=frozenset(), durations=None, fps=30):
    """faded_in: scene ids whose frame 0 is hidden under the incoming crossfade, so an empty frame 0 is by design.
    durations: {scene id: seconds} for the tween settle check."""
    items = []
    durations = durations or {}

    def add(level, sid, msg):
        items.append({"level": level, "where": sid, "msg": msg})

    for sid, r in previews.items():
        tween_checks(sid, r.get("tweens", []), durations.get(sid) or 0, fps, add)
        for e in r["errors"]:
            add("fail", sid, e)
        for m in r["missing"]:
            if not m.endswith(R.OPTIONAL_FILES):
                add("fail", sid, f"missing file: {m}")
        for b in r["blocked"]:
            add("fail", sid, f"blocked network request: {b}")
        for c in r["console"]:
            add("fail" if c.startswith("error") else "warn", sid, f"console {c}")
        for st in r["stills"]:
            std = pixel_std(st["path"])
            st["std"] = round(std, 2)
            rel = os.path.relpath(st["path"], project)
            if std < BLANK_STD and not (st["tag"] == "a" and sid in faded_in):
                add("warn" if st["tag"] == "a" else "fail", sid,
                    f"{rel} (t={st['t']}s) is almost a flat colour (std {std:.1f} < {BLANK_STD}): blank or black frame")
            for x in st.get("band", []):
                add("fail", sid, f"{rel} (t={st['t']}s): text '{x['text']}' reaches into the caption band "
                                 f"(bottom {x['bottom']}px > caption top {st.get('captionTop')}px)")
            if st["tag"] != "a":
                for x in st.get("outside", []):
                    add("warn", sid, f"{rel} (t={st['t']}s): text '{x['text']}' is outside the 5 % safe area "
                                     f"({x['left']},{x['top']})-({x['right']},{x['bottom']})")
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="Static, still-frame and storyboard checks before rendering.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--scene", help="check one scene, e.g. S03")
    ap.add_argument("--no-render", action="store_true", help="skip the preview stills")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"error: project directory not found: {proj}", file=sys.stderr)
        return 2
    sbp = os.path.join(proj, "storyboard.json")
    if not os.path.isfile(sbp):
        print(f"error: no storyboard.json in {proj}", file=sys.stderr)
        return 2
    report = {"project": proj, "scene": a.scene, "static": [], "storyboard": [], "stills": [], "previews": {}}
    try:
        sb = json.load(open(sbp, encoding="utf-8"))
    except json.JSONDecodeError as e:
        report["storyboard"].append({"level": "fail", "where": "storyboard", "msg": f"invalid JSON: {e}"})
        sb = None
    only = None
    if sb is not None:
        ids = [s.get("id") for s in sb.get("scenes", []) if isinstance(s, dict)]
        if a.scene:
            if a.scene not in ids:
                print(f"error: scene {a.scene} is not in storyboard.json (have: {', '.join(map(str, ids))})", file=sys.stderr)
                return 2
            only = ids.index(a.scene)
        report["storyboard"] = storyboard_checks(proj, sb, only)
        targets = [a.scene] if a.scene else [i for i in ids if i]
        # (a) static
        for sid in targets:
            p = os.path.join(proj, "scenes", f"{sid}.html")
            if os.path.isfile(p):
                report["static"] += scan_file(p, f"scenes/{sid}.html", True)
        shared = os.path.join(proj, "scenes", "shared.css")
        if os.path.isfile(shared):
            report["static"] += scan_file(shared, "scenes/shared.css", False)
        # (b) stills
        renderable = [sid for sid in targets if os.path.isfile(os.path.join(proj, "scenes", f"{sid}.html"))]
        if not a.no_render and renderable:
            try:
                previews = R.preview_scenes(proj, renderable, a.jobs, probe=True, log=lambda m: None)
                report["stills"] = still_checks(proj, previews, faded_in_scenes(sb or {}),
                                               {x.get("id"): x.get("duration") for x in (sb or {}).get("scenes", [])},
                                               (sb or {}).get("fps", 30))
                report["previews"] = {sid: [{k: st.get(k) for k in ("tag", "frame", "t", "std", "band", "outside")}
                                            | {"path": os.path.relpath(st["path"], proj)} for st in r["stills"]]
                                      for sid, r in previews.items()}
            except R.UsageError as e:
                report["stills"].append({"level": "fail", "where": "render", "msg": str(e)})
    all_items = [("static", x) for x in report["static"]] + [("storyboard", x) for x in report["storyboard"]] + \
                [("stills", x) for x in report["stills"]]
    fails = [x for _, x in all_items if x["level"] == "fail"]
    warns = [x for _, x in all_items if x["level"] == "warn"]
    report["ok"] = not fails
    report["fails"], report["warnings"] = len(fails), len(warns)
    os.makedirs(os.path.join(proj, "review"), exist_ok=True)
    out = os.path.join(proj, "review", f"check-{a.scene}.json" if a.scene else "check.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for kind, x in all_items:
        loc = f"{x['file']}:{x['line']}" if "file" in x else x["where"]
        extra = f"  [{x['rule']}] {x.get('text', '')}" if "rule" in x else ""
        print(f"{x['level'].upper():4} {kind:10} {loc}: {x['msg']}{extra}")
    print(f"{'OK' if not fails else 'FAILED'}: {len(fails)} failure(s), {len(warns)} warning(s); wrote {os.path.relpath(out, proj)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
