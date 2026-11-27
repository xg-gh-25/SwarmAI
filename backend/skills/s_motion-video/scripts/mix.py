#!/usr/bin/env python3
"""mix.py: voice, sound effects and music mixed in one ffmpeg filtergraph to build/mix.wav, then measured.

  mix.py --project <dir>  -> build/mix.wav (48 kHz stereo 16-bit, exactly the film length) + review/loudness.json

- Timeline: scene i starts at sum(round(duration_k * fps) for k < i) / fps. Voice clips are placed at their scene
  start and SFX at scene start + "at". Clip lengths are never probed for placement (fade transitions keep the total
  length, so the starts do not move).
- Voice bus: every clip adelay'ed to its start, apad, amix normalize=0. SFX bus: the same, at 0.22 of voice level
  (storyboard "volume" overrides, capped at 0.35).
- Music: audio/bgm.wav looped (-stream_loop -1), pre-levelled to -20 LUFS (gain measured on the first 90 s, clamped
  to +-18 dB), then x bgm.volume (default 0.32), fade in 1 s / out 1.5 s, ducked with sidechaincompress keyed on
  the voice bus (threshold 0.03 ratio 3 attack 40 ms release 700 ms).
- Sum: amix normalize=0, trimmed and padded to the film length.
- Two-pass loudness (plus up to two correction passes) (not loudnorm: its meter is 1-3 LU off on material with long quiet stretches and it silently
  switches to dynamic mode when LRA > 11): pass 1 measures with ebur128 (and runs silencedetect -40 dB / 1.5 s on
  the voice bus); pass 2 applies a linear gain to -14 LUFS (capped at +20 dB), then a 4x-oversampled alimiter at
  -1.5 dBFS so the true peak stays under -1 dBTP. When heavy limiting leaves the result more than 0.3 LU short,
  the gain is corrected by the measured error and pass 2 runs again (at most 3 gain passes).
- Target: -14 LUFS integrated, -1 dBTP, everywhere.

review/loudness.json: integrated LUFS, true peak, LRA, narration gaps > 1.5 s (gaps inside scenes without narration
are not counted), duration check. These numbers are how an agent "listens".
Exit codes: 0 ok; 1 check failed (integrated off target by > 1 LU, true peak > -0.5 dBTP, length off by > 1 frame,
silent film); 2 usage / environment error (missing storyboard, durations, voice or music files, ffmpeg).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

SR = 48000
TARGET_LUFS = -14.0
TRUE_PEAK = -1.0
TP_MARGIN = 0.5                 # limiter ceiling sits 0.5 dB under the true-peak target (sample-peak limiter overshoots)
GAIN_CAP_DB = 20.0
BGM_REF = -20.0
BGM_GAIN_CLAMP = 18.0
BGM_VOLUME = 0.32
DUCK = dict(threshold=0.03, ratio=3, attack=40, release=700, level_sc=1.0)
SFX_DEFAULT, SFX_MAX = 0.22, 0.35
SILENCE_DB, SILENCE_S = -40, 1.5
LUFS_TOL, TP_FAIL = 1.0, -0.5
FADE_IN, FADE_OUT = 1.0, 1.5
MAX_GAIN_PASSES, SETTLE_LU = 3, 0.3   # gain passes after the measuring pass; stop when within 0.3 LU


class MixError(Exception):
    pass


# ---------- pure functions (unit-tested) ----------

def timeline(scenes, fps):
    """-> ([(id, start seconds, frames)], total seconds). Every scene needs a duration."""
    out, acc = [], 0
    missing = [str(s.get("id")) for s in scenes if not s.get("duration")]
    if missing:
        raise MixError(f"scenes without duration: {', '.join(missing)} (run tts.py; scenes without narration need a hand-set duration)")
    for s in scenes:
        n = max(1, int(round(float(s["duration"]) * fps)))
        out.append((str(s["id"]), acc / fps, n))
        acc += n
    return out, acc / fps


def sfx_volume(item):
    v = item.get("volume") if isinstance(item, dict) else None
    return min(SFX_MAX, float(v)) if v not in (None, "") else SFX_DEFAULT


def placements(scenes, fps, voice_path, sfx_path):
    """-> (voices [(path, start)], sfx [(path, start, volume)], total). voice_path(id) returns a path or None."""
    tl, total = timeline(scenes, fps)
    voices, sfx = [], []
    for s, (sid, start, frames) in zip(scenes, tl):
        v = voice_path(sid)
        if v:
            voices.append((v, round(start, 6)))
        for it in s.get("sfx") or []:
            name = str((it.get("name") if isinstance(it, dict) else it) or "").strip()
            if not name:
                continue
            at = max(0.0, float((it.get("at") if isinstance(it, dict) else 0) or 0))
            if at >= frames / fps:
                raise MixError(f"{sid}: sfx {name} at {at} s is past the scene end ({frames / fps:.2f} s)")
            sfx.append((sfx_path(name), round(start + at, 6), sfx_volume(it)))
    return voices, sfx, total


def parse_silences(stderr, total):
    out, start = [], None
    for m in re.finditer(r"silence_(start|end): (-?[\d.]+)", stderr):
        if m.group(1) == "start":
            start = max(0.0, float(m.group(2)))
        elif start is not None:
            out.append((start, min(float(m.group(2)), total)))
            start = None
    if start is not None:
        out.append((start, total))
    return out


def narration_gaps(silences, tl, narrated, min_len=SILENCE_S):
    """Voice-bus silences minus the spans of scenes without narration; keep pieces longer than min_len.
    tl: [(id, start, frames)] with frames converted by the caller to end seconds: [(id, start, end)]."""
    quiet = [(st, en) for sid, st, en in tl if sid not in narrated]
    out = []
    for a, b in silences:
        pieces = [(a, b)]
        for qa, qb in quiet:
            nxt = []
            for pa, pb in pieces:
                if qb <= pa or qa >= pb:
                    nxt.append((pa, pb))
                    continue
                if pa < qa:
                    nxt.append((pa, qa))
                if qb < pb:
                    nxt.append((qb, pb))
            pieces = nxt
        for pa, pb in pieces:
            if pb - pa > min_len:
                ids = [sid for sid, st, en in tl if st < pb and en > pa]
                out.append({"from": round(pa, 2), "to": round(pb, 2), "seconds": round(pb - pa, 2), "scenes": ids})
    return out


def parse_ebur128(stderr):
    """last ebur128 summary -> {lufs, true_peak, lra}"""
    def g(k):
        v = re.findall(k + r":\s+(-?[\d.]+|-inf)", stderr)
        return round(float(v[-1]), 2) if v and v[-1] != "-inf" else None
    return {"lufs": g(r"\bI"), "true_peak": g(r"True peak:\s+Peak"), "lra": g("LRA")}


def bus(n0, items, name):
    """[(path, start, volume)] -> (input args, filters, bus label or None)"""
    inputs, filters, labels = [], [], []
    for i, (p, start, vol) in enumerate(items):
        ms = int(round(start * 1000))
        inputs += ["-i", p]
        filters.append(f"[{n0 + i}:a]aresample={SR},aformat=channel_layouts=stereo,volume={vol:g},adelay={ms}|{ms},apad[{name}{i}]")
        labels.append(f"[{name}{i}]")
    if not labels:
        return inputs, filters, None
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{name}]")
    return inputs, filters, f"[{name}]"


def graph(total, voices, sfx, bgm, bgm_gain):
    """-> (inputs, filters, mix label, voice-detect label or None). bgm_gain = linear volume after pre-levelling."""
    ns = n_samples(total)
    vi, vf, vlab = bus(0, [(p, s, 1.0) for p, s in voices], "v")
    si, sf, slab = bus(len(voices), sfx, "x")
    inputs, filters, mix = vi + si, vf + sf, []
    if vlab:
        filters.append(f"{vlab}atrim=end_sample={ns},asplit=3[vmix][vsc][vsd]")
        mix.append("[vmix]")
    if slab:
        mix.append(slab)
    if bgm:
        n = len(voices) + len(sfx)
        inputs += ["-stream_loop", "-1", "-i", bgm]
        filters.append(f"[{n}:a]aresample={SR},aformat=channel_layouts=stereo,volume={bgm_gain:.6g},afade=t=in:d={FADE_IN:g},"
                       f"afade=t=out:st={max(0.0, total - FADE_OUT):.4f}:d={FADE_OUT:g}[bgm0]")
        if vlab:
            d = ":".join(f"{k}={v:g}" for k, v in DUCK.items())
            filters.append(f"[bgm0][vsc]sidechaincompress={d}[bgm]")
        else:
            filters.append("[bgm0]anull[bgm]")
        mix.append("[bgm]")
    elif vlab:
        filters.append("[vsc]anullsink")
    filters.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:dropout_transition=0,"
                   f"atrim=end_sample={ns},apad=whole_len={ns}[mix]")
    return inputs, filters, "[mix]", "[vsd]" if vlab else None


def n_samples(total):
    return int(round(total * SR))


def limiter_chain(gain_db):
    limit = 10 ** ((TRUE_PEAK - TP_MARGIN) / 20)
    return (f"volume={gain_db:.2f}dB,aresample={4 * SR},alimiter=limit={limit:.4f}:attack=5:release=50:level=false,"
            f"aresample={SR}")


def loudness_gain(measured):
    return min(TARGET_LUFS - measured, GAIN_CAP_DB) if measured is not None else 0.0


# ---------- ffmpeg ----------

def tool(name):
    p = shutil.which(name) or (os.path.join("/opt/homebrew/bin", name) if os.path.isfile(os.path.join("/opt/homebrew/bin", name)) else None)
    if not p:
        raise MixError(f"{name} not found on PATH (brew install ffmpeg)")
    return p


def run_ff(args):
    r = subprocess.run([tool("ffmpeg"), "-y", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise MixError(f"ffmpeg failed: {r.stderr[-1200:]}")
    return r.stderr


def probe_seconds(path):
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise MixError(f"ffprobe could not read {path}")


def measure(path, seconds=None):
    pre = ["-t", str(seconds)] if seconds else []
    return parse_ebur128(run_ff([*pre, "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]))


def bgm_level_gain(path):
    m = measure(path, 90)
    if m["lufs"] is None:
        return 1.0, 0.0, None
    db = max(-BGM_GAIN_CLAMP, min(BGM_GAIN_CLAMP, BGM_REF - m["lufs"]))
    return 10 ** (db / 20), db, m["lufs"]


def mix_audio(out_wav, total, voices, sfx, bgm, bgm_volume):
    ns = n_samples(total)
    out_args = ["-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", "-t", f"{ns / SR:.6f}", out_wav]
    rep = {"bgm_measured_lufs": None, "bgm_level_gain_db": None, "silences": [], "pass1_lufs": None, "gain_db": 0.0}
    if not voices and not sfx and not bgm:
        run_ff(["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo", *out_args])
        rep["silences"] = [(0.0, total)]
        return rep
    gain = 1.0
    if bgm:
        lin, db, lufs = bgm_level_gain(bgm)
        gain = bgm_volume * lin
        rep.update(bgm_measured_lufs=lufs, bgm_level_gain_db=round(db, 2))
    inputs, filters, mix, vsd = graph(total, voices, sfx, bgm, gain)
    f1 = filters + [f"{mix}ebur128=video=0:peak=true[aout]"]
    maps = ["-map", "[aout]", "-f", "null", "-"]
    if vsd:
        f1.append(f"{vsd}silencedetect=n={SILENCE_DB}dB:d={SILENCE_S}[sd]")
        maps += ["-map", "[sd]", "-f", "null", "-"]
    err = run_ff([*inputs, "-filter_complex", ";".join(f1), *maps])
    m = parse_ebur128(err)
    rep["pass1_lufs"] = m["lufs"]
    rep["silences"] = parse_silences(err, total) if vsd else [(0.0, total)]
    g = loudness_gain(m["lufs"])
    for n in range(1, MAX_GAIN_PASSES + 1):
        f2 = filters + [f"{mix}{limiter_chain(g)},atrim=end_sample={ns},apad=whole_len={ns}[aout]"]
        if vsd:
            f2.append(f"{vsd}anullsink")
        run_ff([*inputs, "-filter_complex", ";".join(f2), "-map", "[aout]", *out_args])
        got = measure(out_wav)["lufs"]
        rep.update(gain_db=round(g, 2), passes=n + 1)
        # the limiter eats some loudness when the gain is large: correct by the measured error and render again
        if got is None or abs(got - TARGET_LUFS) <= SETTLE_LU or g >= GAIN_CAP_DB:
            break
        g = min(g + (TARGET_LUFS - got), GAIN_CAP_DB)
    return rep


# ---------- main ----------

def load_storyboard(proj):
    p = os.path.join(proj, "storyboard.json")
    if not os.path.isfile(p):
        raise MixError(f"no storyboard.json in {proj}")
    with open(p, encoding="utf-8") as f:
        sb = json.load(f)
    if not sb.get("scenes"):
        raise MixError("storyboard has no scenes")
    return sb


def run(proj):
    sb = load_storyboard(proj)
    fps = float(sb.get("fps") or 30)
    scenes = sb["scenes"]
    warnings, narrated = [], set()
    vdir, sdir = os.path.join(proj, "audio", "voice"), os.path.join(proj, "audio", "sfx")

    def voice_path(sid):
        s = next(x for x in scenes if str(x.get("id")) == sid)
        if not str(s.get("narration") or "").strip():
            return None
        p = os.path.join(vdir, f"{sid}.mp3")
        if not os.path.isfile(p):
            raise MixError(f"{sid} has narration but no audio/voice/{sid}.mp3 (run tts.py)")
        meta_p = os.path.join(vdir, f"{sid}.tts.json")
        if os.path.isfile(meta_p):
            with open(meta_p, encoding="utf-8") as f:
                if json.load(f).get("narration") != str(s.get("narration") or ""):
                    warnings.append(f"{sid}: narration changed since tts.py ran (voice is stale)")
        clip = probe_seconds(p)
        if clip > float(s["duration"]) + 1e-3:
            warnings.append(f"{sid}: voice {clip:.2f} s is longer than the scene ({float(s['duration']):.2f} s) and runs into the next one")
        narrated.add(sid)
        return p

    def sfx_path(name):
        p = os.path.join(sdir, f"{name}.wav")
        if not os.path.isfile(p):
            raise MixError(f"no audio/sfx/{name}.wav (run sfx.py)")
        return p

    voices, sfx, total = placements(scenes, fps, voice_path, sfx_path)
    n_sfx = len(sfx)
    if n_sfx > 8 or any(len(s.get("sfx") or []) > 2 for s in scenes):
        warnings.append("sfx budget exceeded (max 2 per scene, 8 per film)")
    cfg = sb.get("bgm")
    bgm, vol = None, BGM_VOLUME
    if cfg is not None:
        bgm = os.path.join(proj, "audio", "bgm.wav")
        if not os.path.isfile(bgm):
            raise MixError("storyboard has bgm but audio/bgm.wav is missing (run bgm.py, or set \"bgm\": null)")
        vol = float((cfg or {}).get("volume") or BGM_VOLUME)
    os.makedirs(os.path.join(proj, "build"), exist_ok=True)
    out = os.path.join(proj, "build", "mix.wav")
    rep = mix_audio(out, total, voices, sfx, bgm, vol)

    final = measure(out)
    dur = probe_seconds(out)
    tl, _ = timeline(scenes, fps)
    spans = [(sid, st, st + n / fps) for sid, st, n in tl]
    gaps = narration_gaps(rep["silences"], spans, narrated)
    problems = []
    if final["lufs"] is None:
        problems.append("the mix is silent")
    elif abs(final["lufs"] - TARGET_LUFS) > LUFS_TOL:
        problems.append(f"integrated {final['lufs']} LUFS is more than {LUFS_TOL} LU from {TARGET_LUFS}")
    if final["true_peak"] is not None and final["true_peak"] > TP_FAIL:
        problems.append(f"true peak {final['true_peak']} dBTP is above {TP_FAIL}")
    if abs(dur - total) > 1 / fps:
        problems.append(f"mix length {dur:.3f} s differs from the film ({total:.3f} s) by more than one frame")
    report = {
        "file": "build/mix.wav", "film_seconds": round(total, 4), "mix_seconds": round(dur, 4), "fps": fps,
        "integrated_lufs": final["lufs"], "true_peak_dbtp": final["true_peak"], "lra": final["lra"],
        "target_lufs": TARGET_LUFS, "true_peak_target_dbtp": TRUE_PEAK,
        "narration_gaps": gaps,
        "pass1_lufs": rep["pass1_lufs"], "gain_db": rep["gain_db"], "passes": rep.get("passes", 1),
        "bgm": {"file": "audio/bgm.wav", "volume": vol, "measured_lufs": rep["bgm_measured_lufs"],
                "level_gain_db": rep["bgm_level_gain_db"], "ducking": bool(voices)} if bgm else None,
        "inputs": {"voices": len(voices), "sfx": n_sfx, "bgm": bool(bgm)},
        "placements": {"voices": [{"file": os.path.relpath(p, proj), "start": s} for p, s in voices],
                       "sfx": [{"file": os.path.relpath(p, proj), "start": s, "volume": v} for p, s, v in sfx]},
        "warnings": warnings, "problems": problems, "ok": not problems,
    }
    os.makedirs(os.path.join(proj, "review"), exist_ok=True)
    with open(os.path.join(proj, "review", "loudness.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"build/mix.wav: {dur:.3f} s (film {total:.3f} s), integrated {final['lufs']} LUFS, true peak {final['true_peak']} dBTP, "
          f"LRA {final['lra']} LU; {len(voices)} voice, {n_sfx} sfx, music {'on (ducked)' if bgm and voices else 'on' if bgm else 'off'}")
    for g in gaps:
        print(f"narration gap {g['from']:.2f}-{g['to']:.2f} s ({g['seconds']:.2f} s) in {', '.join(g['scenes'])}")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    for p in problems:
        print(f"check failed: {p}", file=sys.stderr)
    print("review/loudness.json written")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description="Mix voice, sfx and music to build/mix.wav at -14 LUFS / -1 dBTP; write review/loudness.json.")
    ap.add_argument("--project", required=True, help="project directory (contains storyboard.json)")
    a = ap.parse_args()
    try:
        return run(os.path.abspath(a.project))
    except MixError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
