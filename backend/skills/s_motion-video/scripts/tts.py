#!/usr/bin/env python3
"""tts.py: narration for every scene (edge-tts or Amazon Polly), word timings, caption lines, and scene durations.

For each scene with narration:
  audio/voice/<id>.mp3            the voice (storyboard voice.name / voice.rate, engine below)
  audio/voice/<id>.words.json     [{"text","start","end"}] seconds from scene start (original spelling, markup stripped)
  audio/voice/<id>.captions.json  [{"text","start","end"}] lines break at punctuation or 16 full-width chars
                                  (12 when height > width); a line lasts until the next one starts
  audio/voice/<id>.tts.json       cache: hash of (engine, text, spoken text, voice, rate), raw boundaries, measured length
and writes scene.duration = max(voice + 0.4, 2.0), rounded UP to a whole frame, back into storyboard.json.

Engines (same outputs, same rules):
  edge   edge-tts, the Microsoft Edge read-aloud endpoint (unofficial; see NOTICE.md); word boundaries from the stream.
  polly  Amazon Polly neural voices through boto3 on the AWS account set in SwarmAI's Settings (its `swarmai`
         profile; a plain python3 outside SwarmAI uses the standard chain); word timings from
         speech marks of type "word" (a word ends where the next starts, the last at the end of speech).
Which one: --engine edge|polly, else storyboard voice.engine, else the engine voice.name belongs to (a hyphenated
name such as zh-CN-YunxiNeural is an edge-tts voice, a single word such as Zhiyu is a Polly voice), else auto:
a Chinese film speaks with edge-tts (Yunxi) when edge-tts is installed; any other language takes Polly when AWS
credentials, a region and one small Polly request succeed, else edge (printed with the reason).
--engine auto forces that choice. Fallback: when edge-tts fails (an HTTP 403 from the endpoint, the network, not
installed) and the engine was not named with --engine (and no --scene), the whole film is voiced again with Polly's
default voice for its language, and storyboard.json records the Polly voice, so the film keeps one voice. When the chosen engine does not own voice.name, the engine's default voice for
storyboard.lang is used and written back to storyboard.json, so later runs stay on the same engine.

Width unit for captions: a CJK / full-width character counts 1, any other character 0.5, so the
16 / 12 limit means the same visual width for Chinese and English lines.

Pronunciation: <project>/pronounce.json {"word": "spoken replacement"} is applied before synthesis;
captions and words keep the original spelling (boundaries are mapped back to the original text).

If a voice returns no word boundaries, the script re-synthesizes with sentence boundaries and splits each
sentence span over its characters proportionally (it says so in the output).

Exit codes: 0 ok, 1 check failed (narration longer than target_seconds by > 20 %), 2 usage / environment
error (missing storyboard, ffmpeg/ffprobe, edge-tts or boto3, credentials, network, synthesis failure); every
environment error is one line that names what is missing and how to get it.
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
DEFAULT_VOICE = {"zh": "zh-CN-YunxiNeural", "en": "en-US-AndrewNeural"}          # edge-tts
ENGINES = ("edge", "polly")
SCENE_ID = re.compile(r"[A-Za-z0-9_-]+")
POLLY_DEFAULT_VOICE = {"zh": "Zhiyu", "en-GB": "Amy", "en": "Matthew"}           # most specific language first
POLLY_MAX_CHARS = 3000    # Polly neural: billed characters per request
POLLY_RATE = (20, 200)    # Polly prosody rate range, percent of normal
FFMPEG_HINT = ("install ffmpeg, which includes ffprobe (macOS: brew install ffmpeg; Windows: winget install Gyan.FFmpeg; "
               "Linux: the distribution's ffmpeg package, e.g. apt install ffmpeg)")

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

# Amazon Polly neural voices (any other Polly neural voice id also works: aws polly describe-voices --engine neural)
POLLY_VOICES = [
    ("zh", "Zhiyu", "female", "Mandarin (cmn-CN), warm and clear; default for zh"),
    ("en", "Matthew", "male", "warm, confident; default for en explainers"),
    ("en", "Ruth", "female", "natural, conversational; product stories"),
    ("en", "Stephen", "male", "calm, steady; tutorials"),
    ("en", "Joanna", "female", "clear, friendly; ads"),
    ("en", "Danielle", "female", "bright, expressive; consumer ads"),
    ("en", "Gregory", "male", "deep, measured; corporate briefs"),
    ("en", "Amy", "female", "British (en-GB), crisp; default for en-GB"),
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


def tts_hash(text, spoken, voice, rate, engine="edge"):
    """Cache key. The engine is part of it; edge keeps the payload it always had, so existing edge caches stay valid."""
    payload = [CACHE_VERSION, text, spoken, voice, rate]
    if engine != "edge":
        payload = [CACHE_VERSION, engine, text, spoken, voice, rate]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def voice_engine(name):
    """The engine a voice name belongs to: edge-tts names carry a locale prefix (zh-CN-YunxiNeural), Polly ids do not."""
    return "edge" if "-" in str(name) else "polly"


def _engine_of(name):
    return "an edge-tts" if voice_engine(name) == "edge" else "a Polly"


def default_voice(engine, lang):
    lang = str(lang or "zh-CN")
    if engine == "edge":
        return DEFAULT_VOICE["zh" if lang.startswith("zh") else "en"]
    for key, name in POLLY_DEFAULT_VOICE.items():
        if lang == key or lang.startswith(key):
            return name
    return None


def resolve_engine(flag, vcfg, probe, lang=None, edge_ok=lambda: edge_available()):
    """-> (engine, reason). flag: None, "auto", "edge" or "polly"; vcfg: storyboard voice; probe() -> (ok, why).
    auto: a Chinese film speaks with edge-tts (its Yunxi voice) whenever edge-tts is installed, and run() moves the
    whole film to Polly if edge-tts then fails; any other language takes Polly when probe() says it works, else edge."""
    if flag in ENGINES:
        return flag, f"--engine {flag}"
    if flag != "auto":
        cfg = vcfg.get("engine")
        if cfg is not None:
            if cfg not in ENGINES:
                raise UsageError(f"voice.engine must be one of {', '.join(ENGINES)}, got {cfg!r}")
            return cfg, "storyboard voice.engine"
        name = vcfg.get("name")
        if name:
            return voice_engine(name), f"voice.name {name} is {_engine_of(name)} voice"
    if str(lang or "").startswith("zh") and edge_ok():
        return "edge", "auto: a Chinese film defaults to edge-tts (Yunxi); Polly takes over if edge-tts fails"
    ok, why = probe()
    return ("polly" if ok else "edge"), f"auto: {why}"


def pick_voice(engine, vcfg, lang, flag):
    """-> (voice name, changed). Keeps voice.name when the engine owns it; otherwise the engine's default for lang.
    A storyboard whose voice.engine contradicts its voice.name (and no --engine) is a usage error."""
    name = vcfg.get("name")
    if name and voice_engine(name) == engine:
        return str(name), False
    if name and flag is None and vcfg.get("engine") is not None:
        raise UsageError(f"voice.engine is {engine} but voice.name {name} is {_engine_of(name)} voice: set voice.name to "
                         f"a {engine} voice (tts.py --list-voices --engine {engine}) or drop voice.engine")
    d = default_voice(engine, lang)
    if not d:
        raise UsageError(f"no default Polly voice for lang {lang}: set voice.name to a Polly neural voice id "
                         f"(aws polly describe-voices --engine neural)")
    return d, True


def xml_escape(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.'\-]*[A-Za-z0-9]")


def polly_ssml(text, rate, lang):
    """Narration -> Polly SSML. Every piece of text is XML-escaped (narration can never inject tags); in Chinese
    narration, Latin words of 2+ characters are wrapped in <lang xml:lang="en-US"> so they are read as English; a
    rate other than +0% becomes <prosody rate="N%"> (N = 100 + offset, kept within Polly's 20..200)."""
    parts, pos = [], 0
    if str(lang).startswith("zh"):
        for m in LATIN_WORD.finditer(text):
            parts.append(xml_escape(text[pos:m.start()]))
            parts.append(f'<lang xml:lang="en-US">{xml_escape(m.group(0))}</lang>')
            pos = m.end()
    parts.append(xml_escape(text[pos:]))
    body = "".join(parts)
    if rate and rate != "+0%":
        pct = min(max(100 + int(rate[:-1]), POLLY_RATE[0]), POLLY_RATE[1])
        body = f'<prosody rate="{pct}%">{body}</prosody>'
    return f"<speak>{body}</speak>"


def polly_boundaries(marks, speech_end):
    """Polly speech marks (type "word": time in ms, value) -> [{"text","start","end"}] like edge word boundaries.
    A word ends where the next word starts; the last ends at speech_end (seconds)."""
    words = []
    for m in marks:
        if m.get("type") != "word":
            continue
        v = re.sub(r"<[^>]+>", "", str(m.get("value") or "")).strip()
        if v:
            words.append((v, float(m.get("time") or 0) / 1000.0))
    out = []
    for i, (v, st) in enumerate(words):
        end = words[i + 1][1] if i + 1 < len(words) else max(float(speech_end), st)
        out.append({"text": v, "start": round(st, 3), "end": round(max(end, st), 3)})
    return out


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
        die(f"{name} not found on PATH: {FFMPEG_HINT}")
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


class UsageError(Exception):
    pass


EDGE_MISSING = ("edge-tts is not installed in this Python (SwarmAI's own Python carries it: run tts.py with "
                "\"$SWARM_BACKEND_BIN\" py), or use --engine polly")
BOTO3_MISSING = ("boto3 is not installed in this Python: run tts.py with SwarmAI's Python (\"$SWARM_BACKEND_BIN\" py), "
                 "which carries it, or python3 -m pip install boto3")


def edge_available():
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


async def synth(text, voice, rate, mp3, boundary):
    try:
        import edge_tts
    except ImportError:
        raise EnvError(EDGE_MISSING)
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


# ---------- Amazon Polly ----------

def swarmai_profile_file():
    """SwarmAI's own AWS profile file when this runs under SwarmAI's Python, else None. Voice is a SwarmAI
    feature, so it uses the one AWS account set in SwarmAI's Settings: the daemon writes a `swarmai` profile
    whose credential_process asks the daemon for that account's pinned credentials (core/aws_account,
    design docs/design/2026-10-31-swarmai-aws-account.md section 5.3). The person's own ~/.aws profiles are
    never used for it."""
    try:
        from core import aws_account
        path = aws_account.profile_file()
    except Exception:   # noqa: BLE001  (not SwarmAI's Python, or no data dir)
        return None
    return path if path.is_file() else None


def aws_session():
    """The boto3 session for Polly: SwarmAI's account (Settings) through its `swarmai` profile when SwarmAI runs
    this; the standard credential chain only for a plain python3 outside SwarmAI."""
    try:
        import boto3
    except ImportError:
        raise EnvError(BOTO3_MISSING)
    cfg = swarmai_profile_file()
    if cfg is None:
        return boto3.Session()
    # this script's own process only (a short-lived child of the agent's shell): read SwarmAI's profile file
    # alone, never the person's ~/.aws config or keys
    os.environ["AWS_CONFIG_FILE"] = str(cfg)
    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    return boto3.Session(profile_name="swarmai")


def polly_client(region=None):
    """A Polly client on SwarmAI's AWS account (aws_session)."""
    sess = aws_session()
    region = region or sess.region_name
    if not region:
        raise EnvError("no AWS region configured for Polly: set AWS_REGION, or region in the AWS config file (aws configure)")
    return sess.client("polly", region_name=region)   # timeouts and retries: botocore's standard configuration


def polly_error(e, voice):
    """A boto3 / botocore exception -> one EnvError line that says what to do."""
    name = type(e).__name__
    resp = getattr(e, "response", None)
    code = (resp.get("Error", {}).get("Code", "") if isinstance(resp, dict) else "") or ""
    msg = (str(e).splitlines() or [name])[0][:300]
    if name in ("NoCredentialsError", "PartialCredentialsError", "SSOTokenLoadError", "UnauthorizedSSOTokenError",
                "TokenRetrievalError", "CredentialRetrievalError"):
        where = ("SwarmAI's AWS account (Settings) gave no credentials: sign in again there, and check that SwarmAI is "
                 "running" if swarmai_profile_file() else "configure the standard AWS chain (environment, AWS config files, "
                 "SSO login or an instance role)")
        return EnvError(f"no usable AWS credentials for Polly ({name}): {where}, or use --engine edge")
    if code in ("AccessDeniedException", "AccessDenied", "UnrecognizedClientException", "ExpiredTokenException"):
        return EnvError(f"Polly refused the request ({code}): the AWS identity needs polly:SynthesizeSpeech "
                        f"with valid credentials, or use --engine edge")
    return EnvError(f"Polly failed for voice {voice}: {code or name}: {msg}")


def polly_probe(lang):
    """auto: is Polly usable here? One tiny speech-marks request with the default voice. -> (ok, why)."""
    voice = default_voice("polly", lang)
    if not voice:
        return False, f"no default Polly voice for lang {lang}"
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False, "boto3 is not available in this Python"
    try:
        sess = aws_session()
        if sess.get_credentials() is None:
            return False, "no AWS credentials found"
        if not sess.region_name:
            return False, "no AWS region configured"
        polly_client(sess.region_name).synthesize_speech(Engine="neural", VoiceId=voice, OutputFormat="json",
                                                          SpeechMarkTypes=["sentence"], Text="ok")
        return True, f"the AWS account works for Polly in {sess.region_name}"
    except EnvError as e:
        return False, f"Polly not usable ({str(e)[:200]})"
    except Exception as e:   # noqa: BLE001  (any failure means: not usable here; the reason is printed)
        return False, f"Polly not usable ({str(polly_error(e, voice))[:200]})"


def polly_synth(client, text, voice, rate, lang, mp3):
    """Audio (mp3, 24 kHz) and word speech marks from the same SSML, so the timings match the audio."""
    if len(text) > POLLY_MAX_CHARS:
        raise EnvError(f"narration is {len(text)} characters; Polly takes at most {POLLY_MAX_CHARS} per request: split the scene")
    common = dict(Engine="neural", VoiceId=voice, TextType="ssml", Text=polly_ssml(text, rate, lang))
    try:
        audio = client.synthesize_speech(OutputFormat="mp3", SampleRate="24000", **common)["AudioStream"].read()
        raw = client.synthesize_speech(OutputFormat="json", SpeechMarkTypes=["word"], **common)["AudioStream"].read()
    except Exception as e:   # noqa: BLE001  (service / network errors are reported, not hidden)
        raise polly_error(e, voice)
    if not audio:
        raise EnvError(f"Polly returned no audio for voice {voice}")
    with open(mp3, "wb") as f:
        f.write(audio)
    return [json.loads(ln) for ln in raw.decode("utf-8").splitlines() if ln.strip()]


async def voice_scene_polly(job, sem, client):
    async with sem:
        marks = await asyncio.to_thread(polly_synth, client, job["spoken"], job["voice"], job["rate"], job["lang"], job["mp3"])
        trimmed = trim_tail(job["mp3"])
        full = probe_seconds(job["mp3"])
        words = polly_boundaries(marks, full - TAIL_KEEP if trimmed else full)
        if words:
            return {"mode": "word", "boundaries": words, "trimmed": trimmed}
        return {"mode": "sentence", "boundaries": [{"text": job["spoken"], "start": 0.0, "end": round(full, 3)}],
                "trimmed": trimmed}


def build_timings(text, spoken, s2o, cache, duration, max_width):
    raw = cache["boundaries"] if cache["mode"] == "word" else split_sentences(cache["boundaries"])
    words = map_words(text, spoken, s2o, raw)
    caps = captions_from_words(text, words, duration, max_width)
    return [{"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in words], caps


def list_voices(lang, engine=None):
    lang = (lang or "all").lower()
    if engine == "auto":
        ok, why = polly_probe({"zh": "zh-CN", "en": "en-US", "all": "en-US"}.get(lang, lang))
        engine = "polly" if ok else "edge"
        print(f"auto picks {engine}: {why}")
    printed = 0
    for eng, table, more in (("edge", VOICES, "edge-tts --list-voices"),
                             ("polly", POLLY_VOICES, "aws polly describe-voices --engine neural")):
        if engine and engine != eng:
            continue
        rows = [v for v in table if lang in ("all", v[0]) or v[1].lower().startswith(lang)]
        if not rows:
            continue
        if not engine:
            print(f"# {eng} (any other voice: {more})")
        for lg, name, sex, desc in rows:
            print(f"{lg:3} {name:24} {sex:7} {desc}")
        printed += len(rows)
    if not printed:
        die(f"no curated voices for '{lang}' (try zh, en or all); any edge-tts or Polly neural voice name also works")


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
    if not isinstance(vcfg, dict):
        die("storyboard voice must be an object {\"name\", \"rate\"}")
    sb["voice"] = vcfg   # a null voice becomes the object tts.py writes the chosen voice into
    engine, why = resolve_engine(a.engine, vcfg, lambda: polly_probe(lang), lang)
    voice, voice_changed = pick_voice(engine, vcfg, lang, a.engine)
    print(f"engine: {engine} ({why}), voice {voice}"
          + (f" (was {vcfg.get('name')}, not a {engine} voice)" if voice_changed and vcfg.get("name") else ""))
    # edge-tts is an unofficial endpoint (NOTICE.md): when it fails and the engine was not named on the command line,
    # the whole film is voiced again with Polly, never only the failed scenes, so the film keeps one voice
    may_fall_back, fell_back = engine == "edge" and a.engine not in ENGINES and not a.scene, False
    rate = str(vcfg.get("rate") or "+0%")
    if not re.fullmatch(r"[+-]\d{1,3}%", rate):
        die(f"voice.rate must look like +0% or -10%, got {rate!r}")
    pron = load_json(os.path.join(proj, "pronounce.json"), {}) or {}
    if not isinstance(pron, dict):
        die("pronounce.json must be an object {\"word\": \"spoken replacement\"}")
    scenes = sb["scenes"]
    bad_ids = [str(s.get("id")) for s in scenes if not SCENE_ID.fullmatch(str(s.get("id")))]
    if bad_ids:   # ids become file names: never a path out of the project
        die(f"scene ids must be letters, digits, - or _ (like S01): {', '.join(bad_ids)}")
    if a.scene and not any(str(s.get("id")) == a.scene for s in scenes):
        die(f"no scene {a.scene} in storyboard (ids: {', '.join(str(s.get('id')) for s in scenes)})")
    vdir = os.path.join(proj, "audio", "voice")
    os.makedirs(vdir, exist_ok=True)

    def plan(engine, voice):
        jobs, plans = [], []
        for s in scenes:
            sid = str(s.get("id"))
            text = strip_markup(str(s.get("narration") or ""))
            if not text:
                continue
            spoken, s2o = apply_pronounce(text, pron)
            h = tts_hash(text, spoken, voice, rate, engine)
            base = os.path.join(vdir, sid)
            p = {"scene": s, "id": sid, "text": text, "spoken": spoken, "s2o": s2o, "hash": h, "mp3": base + ".mp3",
                 "meta": base + ".tts.json", "selected": not a.scene or a.scene == sid}
            plans.append(p)
            old = load_json(p["meta"], {}) or {}
            if not p["selected"]:
                if old and (old.get("voice") != voice or old.get("engine", "edge") != engine):
                    print(f"{sid}: warning: voiced with {old.get('engine', 'edge')} {old.get('voice')}, not {engine} {voice}: "
                          f"run tts.py without --scene so the film keeps one voice", file=sys.stderr)
                continue
            if not a.force and old.get("hash") == h and os.path.isfile(p["mp3"]) and old.get("boundaries"):
                p["cache"], p["reused"] = old, True
            else:
                jobs.append(p)
            p["voice"], p["rate"], p["lang"] = voice, rate, lang
        return jobs, plans

    async def voice_all(jobs, engine):
        """-> [(scene id, error)]; each voiced job gets its cache"""
        sem = asyncio.Semaphore(LANES)
        if jobs and engine == "edge":
            if not edge_available():
                return [(j["id"], EDGE_MISSING) for j in jobs]
            coros = [voice_scene(j, sem) for j in jobs]
        elif jobs:
            client = polly_client()
            coros = [voice_scene_polly(j, sem, client) for j in jobs]
        else:
            coros = []
        results = await asyncio.gather(*coros, return_exceptions=True)
        failed = []
        for j, r in zip(jobs, results):
            if isinstance(r, BaseException):
                failed.append((j["id"], str(r)))
                continue
            j["cache"], j["reused"] = r, False
        return failed

    jobs, plans = plan(engine, voice)
    failed = await voice_all(jobs, engine)
    if failed and may_fall_back:
        ok, pwhy = polly_probe(lang)
        fallback = default_voice("polly", lang) if ok else None
        if fallback:
            print(f"warning: edge-tts failed ({failed[0][1][:200]}): the whole film is voiced again with Polly "
                  f"{fallback} (was {voice}) so it keeps one voice; to go back: tts.py --engine edge --force",
                  file=sys.stderr)
            engine, voice, voice_changed, fell_back = "polly", fallback, True, True
            jobs, plans = plan(engine, voice)
            failed = await voice_all(jobs, engine)
        elif failed and all(err == EDGE_MISSING for _, err in failed):   # nothing voiced: one environment line, exit 2
            raise EnvError(f"{EDGE_MISSING}; Polly could not take over: {pwhy}")
        else:
            print(f"edge-tts failed and Polly cannot take over: {pwhy}", file=sys.stderr)
    if failed and all(err == EDGE_MISSING for _, err in failed):
        raise EnvError(EDGE_MISSING + (f" [{why}]" if why.startswith("auto") else ""))

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
        meta = {"scene": p["id"], "hash": p["hash"], "engine": engine, "voice": voice, "rate": rate, "text": p["text"], "spoken": p["spoken"],
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
    if voice_changed and not failed:   # the film now speaks with this engine's voice: later runs infer the same engine
        old_name = vcfg.get("name")
        vcfg["name"] = voice
        if "engine" in vcfg or fell_back:
            vcfg["engine"] = engine
        print(f"storyboard.json: voice.name {old_name or '(none)'} -> {voice} ({engine})")
    if changed or (voice_changed and not failed):
        dump_json(sb_path, sb)
        if changed:
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
    ap = argparse.ArgumentParser(description="Voice every narrated scene with edge-tts or Amazon Polly; write word timings, "
                                             "captions and scene durations.")
    ap.add_argument("--project", help="project directory (contains storyboard.json)")
    ap.add_argument("--scene", help="only this scene id, e.g. S03")
    ap.add_argument("--engine", choices=ENGINES + ("auto",),
                    help="edge | polly | auto (default: storyboard voice.engine, else the engine of voice.name, else auto: "
                         "Polly when SwarmAI's AWS account works for it, else edge)")
    ap.add_argument("--force", action="store_true", help="re-synthesize even when narration and voice are unchanged")
    ap.add_argument("--list-voices", nargs="?", const="all", metavar="LANG", help="print recommended voices (zh, en or all) and exit")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):   # CJK narration must print on any console (Windows code pages too)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if a.list_voices:
        list_voices(a.list_voices, a.engine)
        return 0
    if not a.project:
        ap.error("--project is required")
    tool("ffprobe")
    tool("ffmpeg")
    try:
        return asyncio.run(run(a))
    except (EnvError, UsageError) as e:
        die(str(e))


if __name__ == "__main__":
    sys.exit(main())
