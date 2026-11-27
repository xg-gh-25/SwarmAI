#!/usr/bin/env python3
"""tts.py: narration for every scene with edge-tts, word timings, caption lines, and scene durations.

For each scene with narration:
  audio/voice/<id>.mp3            the voice (edge-tts, storyboard voice.name / voice.rate)
  audio/voice/<id>.words.json     [{"text","start","end"}] seconds from scene start (original spelling, markup stripped)
  audio/voice/<id>.captions.json  [{"text","start","end"}] lines break at punctuation or 16 full-width chars
                                  (12 when height > width); a line lasts until the next one starts
  audio/voice/<id>.tts.json       cache: hash of (text, spoken text, voice, rate), raw boundaries, measured length
and writes scene.duration = max(voice + 0.4, 2.0), rounded UP to a whole frame, back into storyboard.json.

Width unit for captions: a CJK / full-width character counts 1, any other character 0.5, so the
16 / 12 limit means the same visual width for Chinese and English lines.

Pronunciation: <project>/pronounce.json {"word": "spoken replacement"} is applied before synthesis;
captions and words keep the original spelling (boundaries are mapped back to the original text).

If a voice returns no word boundaries, the script re-synthesizes with sentence boundaries and splits each
sentence span over its characters proportionally (it says so in the output).

Exit codes: 0 ok, 1 check failed (narration longer than target_seconds by > 20 %), 2 usage / environment
error (missing storyboard, ffprobe, edge-tts, network, synthesis failure).
"""
import argparse
import asyncio
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata

PAD_AFTER_VOICE = 0.4
MIN_SCENE = 2.0
CAPTION_MAX = 16
CAPTION_MAX_PORTRAIT = 12
LANES = 4
CACHE_VERSION = 2         # bump when synthesis post-processing changes (re-voices everything once)
TAIL_KEEP = 0.12          # silence kept after the last spoken sound
CJK = "⺀-鿿豈-﫿぀-ヿ가-힯"
PUNCT = set("，。！？；：、,.!?;:…—")
EDGE_PUNCT = "，。！？；：、,.!?;:…—\"'“”‘’「」『』（）()《》<>[]【】 \t\n"
DEFAULT_VOICE = {"zh": "zh-CN-YunxiNeural", "en": "en-US-AndrewNeural"}

VOICES = [
    ("zh", "zh-CN-YunxiNeural", "male", "young, lively, clear; default for zh explainers"),
    ("zh", "zh-CN-XiaoxiaoNeural", "female", "warm, natural; product and lifestyle"),
    ("zh", "zh-CN-YunjianNeural", "male", "deep, energetic; ads, launches, sport"),
    ("zh", "zh-CN-YunyangNeural", "male", "news-anchor, formal; corporate briefs"),
    ("zh", "zh-CN-XiaoyiNeural", "female", "bright, lively; consumer ads"),
    ("en", "en-US-AndrewNeural", "male", "warm, confident; default for en explainers"),
    ("en", "en-US-AvaNeural", "female", "friendly, expressive; product stories"),
    ("en", "en-US-BrianNeural", "male", "casual, approachable; tutorials"),
    ("en", "en-US-EmmaNeural", "female", "cheerful, clear; ads"),
    ("en", "en-GB-RyanNeural", "male", "British, calm; documentary tone"),
]

# Markup that must never be spoken or shown: TTS control tags, inline pinyin, html/xml tags, markdown emphasis.
MARKUP = [
    re.compile(r"<#[0-9.]+#>"),
    re.compile(r"\((?:laughs|chuckle|coughs|clear-throat|groans|breath|pant|inhale|exhale|gasps|sniffs|sighs|"
               r"snorts|burps|lip-smacking|humming|hissing|emm|sneezes)\)"),
    re.compile(r"/\([a-z]+[1-5]\)(?:\([a-z]+[1-5]\))*"),
    re.compile(r"</?[A-Za-z][^<>]*>"),
    re.compile(r"\*\*|__|`"),
]


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- pure functions (unit-tested) ----------

def strip_markup(text):
    t = text or ""
    for rx in MARKUP:
        t = rx.sub("", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)   # markdown link -> its text
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(f"(?<=[{CJK}，。！？；：、]) (?=[{CJK}，。！？；：、])", "", t)   # no spaces between CJK characters


def char_width(c):
    return 1.0 if unicodedata.east_asian_width(c) in ("W", "F") else 0.5


def text_width(s):
    return sum(char_width(c) for c in s)


def max_caption_width(width, height):
    return CAPTION_MAX_PORTRAIT if height > width else CAPTION_MAX


def scene_duration(voice_seconds, fps):
    """max(voice + 0.4, 2.0) rounded up to a whole frame; returned in seconds (3 decimals, round(d*fps) == frames)."""
    raw = max(float(voice_seconds) + PAD_AFTER_VOICE, MIN_SCENE)
    frames = math.ceil(raw * fps - 1e-6)
    return round(frames / fps, 3)


def _is_word_char(c):
    return c.isascii() and (c.isalnum() or c == "_")


def apply_pronounce(text, table):
    """Replace words for synthesis. Returns (spoken, s2o) where s2o[k] = (orig_start, orig_end) for spoken char k.
    Longest key first; ASCII keys only match on word boundaries (so 'SQL' does not hit 'SQLite')."""
    keys = sorted((k for k in (table or {}) if k), key=len, reverse=True)
    spoken, s2o, i = [], [], 0
    while i < len(text):
        hit = None
        for k in keys:
            if not text.startswith(k, i):
                continue
            if _is_word_char(k[0]) and i > 0 and _is_word_char(text[i - 1]):
                continue
            j = i + len(k)
            if _is_word_char(k[-1]) and j < len(text) and _is_word_char(text[j]):
                continue
            hit = k
            break
        if hit:
            rep = str(table[hit])
            spoken.append(rep)
            s2o += [(i, i + len(hit))] * len(rep)
            i += len(hit)
        else:
            spoken.append(text[i])
            s2o.append((i, i + 1))
            i += 1
    return "".join(spoken), s2o


def locate_words(spoken, boundaries):
    """Find each boundary word in the spoken text in order. boundaries: [{"text","start","end"}].
    Returns [{"text","start","end","b","e"}] with spoken char offsets [b, e)."""
    out, cur, low = [], 0, spoken.lower()
    for w in boundaries:
        t = html.unescape(str(w["text"])).strip()
        if not t:
            continue
        pos = spoken.find(t, cur)
        if pos < 0 or pos - cur > 40:
            pos2 = low.find(t.lower(), cur)
            pos = pos2 if 0 <= pos2 and pos2 - cur <= 40 else -1
        if pos < 0:   # TTS normalized the text (numbers etc.): take the next non-space chars
            pos = cur
            while pos < len(spoken) and spoken[pos].isspace():
                pos += 1
            b, e = pos, min(len(spoken), pos + len(t))
        else:
            b, e = pos, pos + len(t)
        out.append({"text": t, "start": w["start"], "end": w["end"], "b": b, "e": max(e, b + 1)})
        cur = max(cur, e)
    return out


def map_words(text, spoken, s2o, boundaries):
    """Boundary words in spoken text -> words in the original text. Words that fall in one replaced span merge.
    Returns [{"text","start","end","ob","oe"}]."""
    words = []
    for w in locate_words(spoken, boundaries):
        b, e = min(w["b"], len(s2o) - 1), min(w["e"], len(s2o))
        if b < 0 or e <= b:
            continue
        ob, oe = min(s2o[k][0] for k in range(b, e)), max(s2o[k][1] for k in range(b, e))
        if words and ob < words[-1]["oe"]:   # same replaced word: extend
            words[-1]["oe"] = max(words[-1]["oe"], oe)
            words[-1]["end"] = max(words[-1]["end"], w["end"])
            continue
        words.append({"start": w["start"], "end": w["end"], "ob": ob, "oe": oe})
    for w in words:
        w["text"] = text[w["ob"]:w["oe"]].strip()
    return [w for w in words if w["text"]]


def split_sentences(sentences):
    """Sentence-boundary fallback: [{"text","start","end"}] -> pseudo-words, time split by character count.
    CJK characters are one word each; other runs split on whitespace; punctuation gets no time."""
    out = []
    for s in sentences:
        text = html.unescape(str(s["text"]))
        toks = re.findall(r"[⺀-鿿豈-﫿぀-ヿ가-힯]|[^\s⺀-鿿豈-﫿぀-ヿ가-힯]+", text)
        toks = [t for t in toks if t.strip(EDGE_PUNCT)]
        weights = [max(1, len(t.strip(EDGE_PUNCT))) for t in toks]
        total, t0, span = sum(weights), float(s["start"]), float(s["end"]) - float(s["start"])
        acc = 0
        for tok, wgt in zip(toks, weights):
            st = t0 + span * acc / total
            acc += wgt
            out.append({"text": tok.strip(EDGE_PUNCT) or tok, "start": round(st, 3), "end": round(t0 + span * acc / total, 3)})
    return out


def _punct_after(text, oe):
    i = oe
    while i < len(text) and text[i] == " ":
        i += 1
    return text[i] if i < len(text) and text[i] in PUNCT else ""


def captions_from_words(text, words, duration, max_width):
    """Lines break after a word followed by punctuation, or before a word that would push the line past max_width.
    Each line starts at its first word; ends when the next line starts; the last ends at duration."""
    lines, cur = [], []

    def flush():
        if cur:
            t = text[cur[0]["ob"]:cur[-1]["oe"]].strip().strip(EDGE_PUNCT).strip()
            if t:
                lines.append([cur[0]["start"], t])
            cur.clear()

    for w in words:
        if cur:
            cand = text[cur[0]["ob"]:w["oe"]].strip(EDGE_PUNCT)
            if text_width(cand) > max_width:
                flush()
        cur.append(w)
        p = _punct_after(text, w["oe"])
        # An enumeration comma (、) keeps a list on one line; it breaks only when the line is already half full.
        if p and (p != "、" or text_width(text[cur[0]["ob"]:w["oe"]]) >= max_width / 2):
            flush()
    flush()
    out = []
    for i, (st, tx) in enumerate(lines):
        end = lines[i + 1][0] if i + 1 < len(lines) else max(duration, st)
        out.append({"text": tx, "start": round(st, 3), "end": round(end, 3)})
    return out


def tts_hash(text, spoken, voice, rate):
    return hashlib.sha256(json.dumps([CACHE_VERSION, text, spoken, voice, rate], ensure_ascii=False).encode()).hexdigest()[:16]


def length_check(narration_total, target):
    """-> (level, message); level in ok / warn / fail."""
    if not target:
        return "ok", f"narration {narration_total:.1f} s (no target_seconds in storyboard)"
    over = narration_total / float(target) - 1
    msg = f"narration {narration_total:.1f} s vs target {float(target):.1f} s ({over * 100:+.0f} %)"
    if over > 0.20:
        return "fail", msg + ": more than +20 %, cut the script"
    if over > 0.10:
        return "warn", msg + ": more than +10 %, consider trimming"
    return "ok", msg


# ---------- environment ----------

def tool(name):
    p = shutil.which(name) or (os.path.join("/opt/homebrew/bin", name) if os.path.isfile(os.path.join("/opt/homebrew/bin", name)) else None)
    if not p:
        die(f"{name} not found on PATH (brew install ffmpeg)")
    return p


def probe_seconds(path):
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe could not read {path}: {r.stderr.strip()[-300:]}")


def trim_tail(mp3, keep=TAIL_KEEP):
    """edge-tts ends every clip with ~0.9 s of silence. Cut it to `keep` s (stream copy, no re-encode) so the
    measured length is the speech and the 0.4 s padding means what it says. Returns seconds removed."""
    ff = tool("ffmpeg")
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", mp3, "-af", "silencedetect=n=-45dB:d=0.25", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", r.stderr)]
    full = probe_seconds(mp3)
    # trailing silence: the last silence has no end, or (newer ffmpeg) ends at end of file
    if not starts or (len(ends) >= len(starts) and ends[-1] < full - 0.05):
        return 0.0
    cut = starts[-1] + keep
    if cut <= 0.3 or full - cut < 0.05:
        return 0.0
    tmp = mp3 + ".trim.mp3"
    r = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", mp3, "-t", f"{cut:.3f}", "-c", "copy", tmp],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise EnvError(f"ffmpeg could not trim {mp3}: {r.stderr[-300:]}")
    os.replace(tmp, mp3)
    return round(full - probe_seconds(mp3), 3)


def load_json(p, default=None):
    if not os.path.isfile(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


# ---------- synthesis ----------

class EnvError(Exception):
    pass


async def synth(text, voice, rate, mp3, boundary):
    try:
        import edge_tts
    except ImportError:
        raise EnvError("edge-tts is not installed (python3 -m pip install -U edge-tts)")
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary=boundary)
    except TypeError:   # edge-tts < 7 has no boundary argument (word boundaries only)
        comm = edge_tts.Communicate(text, voice, rate=rate)
    events, chunks = [], []
    try:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                chunks.append(ch["data"])
            elif ch["type"] in ("WordBoundary", "SentenceBoundary"):
                events.append({"type": ch["type"], "text": ch["text"], "start": round(ch["offset"] / 1e7, 3),
                               "end": round((ch["offset"] + ch["duration"]) / 1e7, 3)})
    except Exception as e:   # noqa: BLE001  (network / service errors are reported, not hidden)
        status = getattr(e, "status", None)
        if status == 403:
            ver = getattr(edge_tts, "__version__", "?")
            raise EnvError(f"edge-tts {ver} was rejected by the Microsoft endpoint (HTTP 403). The installed edge-tts is "
                           f"probably outdated: python3 -m pip install -U edge-tts (7.3.1 works as of 2026-11)")
        raise EnvError(f"edge-tts failed for voice {voice}: {type(e).__name__}: {str(e)[:300]}")
    if not chunks:
        raise EnvError(f"edge-tts returned no audio for voice {voice} (wrong voice name? see --list-voices)")
    with open(mp3, "wb") as f:
        f.write(b"".join(chunks))
    return events


async def voice_scene(job, sem):
    async with sem:
        ev = await synth(job["spoken"], job["voice"], job["rate"], job["mp3"], "WordBoundary")
        words = [e for e in ev if e["type"] == "WordBoundary"]
        if words:
            return {"mode": "word", "boundaries": words, "trimmed": trim_tail(job["mp3"])}
        ev = await synth(job["spoken"], job["voice"], job["rate"], job["mp3"], "SentenceBoundary")
        sents = [e for e in ev if e["type"] == "SentenceBoundary"]
        trimmed = trim_tail(job["mp3"])
        if not sents:
            sents = [{"text": job["spoken"], "start": 0.0, "end": probe_seconds(job["mp3"])}]
        return {"mode": "sentence", "boundaries": sents, "trimmed": trimmed}


def build_timings(text, spoken, s2o, cache, duration, max_width):
    raw = cache["boundaries"] if cache["mode"] == "word" else split_sentences(cache["boundaries"])
    words = map_words(text, spoken, s2o, raw)
    caps = captions_from_words(text, words, duration, max_width)
    return [{"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in words], caps


def list_voices(lang):
    lang = (lang or "all").lower()
    rows = [v for v in VOICES if lang in ("all", v[0]) or v[1].lower().startswith(lang)]
    if not rows:
        die(f"no curated voices for '{lang}' (try zh, en or all); any edge-tts voice name also works: edge-tts --list-voices")
    for lg, name, sex, desc in rows:
        print(f"{lg:3} {name:24} {sex:7} {desc}")


async def run(a):
    proj = os.path.abspath(a.project)
    sb_path = os.path.join(proj, "storyboard.json")
    sb = load_json(sb_path)
    if not sb or not isinstance(sb.get("scenes"), list):
        die(f"no storyboard.json with scenes in {proj}")
    fps = float(sb.get("fps") or 30)
    width, height = int(sb.get("width") or 1920), int(sb.get("height") or 1080)
    max_w = max_caption_width(width, height)
    lang = str(sb.get("lang") or "zh-CN")
    vcfg = sb.get("voice") or {}
    voice = vcfg.get("name") or DEFAULT_VOICE["zh" if lang.startswith("zh") else "en"]
    rate = str(vcfg.get("rate") or "+0%")
    if not re.fullmatch(r"[+-]\d{1,3}%", rate):
        die(f"voice.rate must look like +0% or -10%, got {rate!r}")
    pron = load_json(os.path.join(proj, "pronounce.json"), {}) or {}
    if not isinstance(pron, dict):
        die("pronounce.json must be an object {\"word\": \"spoken replacement\"}")
    scenes = sb["scenes"]
    if a.scene and not any(str(s.get("id")) == a.scene for s in scenes):
        die(f"no scene {a.scene} in storyboard (ids: {', '.join(str(s.get('id')) for s in scenes)})")
    vdir = os.path.join(proj, "audio", "voice")
    os.makedirs(vdir, exist_ok=True)

    jobs, plans = [], []
    for s in scenes:
        sid = str(s.get("id"))
        text = strip_markup(str(s.get("narration") or ""))
        if not text:
            continue
        spoken, s2o = apply_pronounce(text, pron)
        h = tts_hash(text, spoken, voice, rate)
        base = os.path.join(vdir, sid)
        p = {"scene": s, "id": sid, "text": text, "spoken": spoken, "s2o": s2o, "hash": h, "mp3": base + ".mp3",
             "meta": base + ".tts.json", "selected": not a.scene or a.scene == sid}
        plans.append(p)
        if not p["selected"]:
            continue
        old = load_json(p["meta"], {}) or {}
        if not a.force and old.get("hash") == h and os.path.isfile(p["mp3"]) and old.get("boundaries"):
            p["cache"], p["reused"] = old, True
        else:
            jobs.append(p)
        p["voice"], p["rate"] = voice, rate

    sem = asyncio.Semaphore(LANES)
    results = await asyncio.gather(*(voice_scene(j, sem) for j in jobs), return_exceptions=True)
    failed = []
    for j, r in zip(jobs, results):
        if isinstance(r, BaseException):
            failed.append((j["id"], str(r)))
            continue
        j["cache"], j["reused"] = r, False

    changed, narration_total = 0, 0.0
    for p in plans:
        if not p["selected"]:
            if os.path.isfile(p["mp3"]):
                narration_total += probe_seconds(p["mp3"])
            continue
        if "cache" not in p:
            continue
        dur = probe_seconds(p["mp3"])
        narration_total += dur
        words, caps = build_timings(p["text"], p["spoken"], p["s2o"], p["cache"], dur, max_w)
        dump_json(os.path.join(vdir, p["id"] + ".words.json"), words)
        dump_json(os.path.join(vdir, p["id"] + ".captions.json"), caps)
        meta = {"scene": p["id"], "hash": p["hash"], "voice": voice, "rate": rate, "text": p["text"], "spoken": p["spoken"],
                "narration": str(p["scene"].get("narration") or ""), "voice_seconds": round(dur, 3),
                "trimmed_tail_seconds": p["cache"].get("trimmed", 0.0),
                "mode": p["cache"]["mode"], "boundaries": p["cache"]["boundaries"]}
        dump_json(p["meta"], meta)
        new_d = scene_duration(dur, fps)
        if p["scene"].get("duration") != new_d:
            p["scene"]["duration"] = new_d
            changed += 1
        how = (f"{len(words)} words from word boundaries" if p["cache"]["mode"] == "word" else
               f"NO word boundaries from {voice}: {len(p['cache']['boundaries'])} sentence spans split by character count into {len(words)} pseudo-words")
        tag = "reused" if p["reused"] else "voiced"
        print(f"{p['id']}: {tag}, voice {dur:.3f} s -> duration {new_d:.3f} s, {len(caps)} caption lines, {how}"
              + (f", spoken as: {p['spoken']}" if p["spoken"] != p["text"] else ""))
    for s in scenes:
        if not strip_markup(str(s.get("narration") or "")) and not s.get("duration"):
            print(f"{s.get('id')}: no narration and no duration: set its duration by hand", file=sys.stderr)
    if changed:
        dump_json(sb_path, sb)
        print(f"storyboard.json: {changed} scene duration(s) written")
    for sid, err in failed:
        print(f"{sid}: FAILED: {err}", file=sys.stderr)
    if failed:
        return 2
    film = sum(float(s.get("duration") or 0) for s in scenes)
    level, msg = length_check(narration_total, sb.get("target_seconds"))
    print(f"length: {msg}; film {film:.2f} s ({len(scenes)} scenes)")
    if level == "warn":
        print("warning: narration over target by more than 10 %", file=sys.stderr)
    if level == "fail":
        print("check failed: narration over target by more than 20 %", file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="Voice every narrated scene with edge-tts; write word timings, captions and scene durations.")
    ap.add_argument("--project", help="project directory (contains storyboard.json)")
    ap.add_argument("--scene", help="only this scene id, e.g. S03")
    ap.add_argument("--force", action="store_true", help="re-synthesize even when narration and voice are unchanged")
    ap.add_argument("--list-voices", nargs="?", const="all", metavar="LANG", help="print recommended voices (zh, en or all) and exit")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if a.list_voices:
        list_voices(a.list_voices)
        return 0
    if not a.project:
        ap.error("--project is required")
    tool("ffprobe")
    try:
        return asyncio.run(run(a))
    except EnvError as e:
        die(str(e))


if __name__ == "__main__":
    sys.exit(main())
