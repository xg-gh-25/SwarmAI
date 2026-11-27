#!/usr/bin/env python3
"""bgm.py: compose royalty-free background music for this film, locally, in a few seconds (numpy only).

  bgm.py --project <dir> [--mood calm] [--seed 7] [--seconds 64] [--preview]
    -> <project>/audio/bgm.wav (48 kHz stereo 16-bit, pre-levelled to -20 LUFS; mix.py applies bgm.volume and ducking)
       --preview writes a 20 s sketch to audio/bgm-preview.wav instead (lift at 58 %), so you can audition a mood/seed.

Defaults come from storyboard.json "bgm": {"mood", "seed"}; "bgm": null means no music (nothing is written).
Length = sum of scene durations (or --seconds). The piece ends on a held final chord exactly at that length.
The B section ("lift": brighter, louder, key change) starts on the bar nearest to the start of the scene marked
"hero": true; without a hero, the scene start nearest 58 % of the film (within 40-75 %), for films >= 45 s.

Seven moods, each with its own instrumentation (not one patch with tweaks):
  calm      felt piano + airy pad, no drums, slight rubato
  tense     low pulses, muted-pluck ostinato, woodblock / clock ticks, minor with suspensions
  hopeful   chorused electric-piano arpeggios, swelling strings, brushes
  bright    marimba + nylon guitar, claps / shaker, major
  tech      filtered saw bass, beat-pumped pad, crisp hats and kick, sparse bell melody
  warm      nylon guitar, warm bass, soft Rhodes chords, gentle swing
  cinematic low string / brass pad swells, taiko-style drums, sparse high piano, big reverb
The seed picks progression, key, bpm, accompaniment, bass pattern, feel, counter-melody, intro and lift kind.

Exit codes: 0 ok, 2 usage / environment error.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

SR = 48000
TARGET_LUFS = -20.0
PEAK = 0.90
F32 = np.float32
PREVIEW_SECONDS = 20.0


class BgmError(Exception):
    pass


def die(msg):
    raise BgmError(msg)


# ---------- music theory ----------
PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SCALE = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10]}
CH = {"I": (0, "M"), "ii": (2, "m"), "iii": (4, "m"), "IV": (5, "M"), "V": (7, "M"), "vi": (9, "m"),
      "i": (0, "m"), "III": (3, "M"), "iv": (5, "m"), "v": (7, "m"), "VI": (8, "M"), "VII": (10, "M")}
PROG = {
    "major": [["I", "V", "vi", "IV"], ["I", "vi", "IV", "V"], ["IV", "I", "V", "vi"], ["I", "iii", "IV", "V"],
              ["vi", "IV", "I", "V"], ["I", "IV", "vi", "V"], ["ii", "V", "I", "IV"], ["I", "V", "IV", "IV"], ["I", "iii", "vi", "IV"]],
    "minor": [["i", "VI", "III", "VII"], ["i", "iv", "VI", "V"], ["i", "VII", "VI", "VII"], ["VI", "VII", "i", "i"],
              ["i", "III", "VII", "iv"], ["iv", "i", "VI", "V"], ["i", "v", "VI", "III"], ["i", "VI", "iv", "v"], ["i", "i", "VI", "VII"]],
}
# arpeggio patterns: chord-tone index (0 root 1 third 2 fifth 3 seventh/ninth), negative = rest; one cell per eighth
ARPS = [[0, 1, 2, 3, 2, 1, 0, 2], [0, 2, 1, 3, 0, 2, 1, 3], [0, 1, 2, 1, 3, 1, 2, 1], [0, -1, 2, -1, 1, 3, -1, 2],
        [0, 2, 3, 2, 0, 2, 3, 1], [0, -1, 1, 2, -1, 3, 2, -1], [3, 2, 1, 0, 3, 2, 1, 0], [0, 1, -1, 2, 0, 3, -1, 1]]
# block-chord hit grids (eighths)
BLOCKS = [[1, 0, 0, 0, 1, 0, 0, 0], [1, 0, 0, 1, 0, 0, 1, 0], [1, 0, 1, 0, 0, 0, 1, 0], [1, 0, 0, 0, 0, 0, 1, 0], [0, 0, 1, 0, 0, 0, 1, 0]]
# motif rhythm cells (eight eighths per bar; 1 = onset)
CELLS = [[1, 0, 1, 0, 1, 0, 0, 0], [1, 0, 0, 1, 0, 1, 0, 0], [1, 1, 0, 1, 0, 0, 1, 0], [1, 0, 0, 0, 1, 0, 1, 0],
         [0, 0, 1, 0, 1, 0, 1, 0], [1, 0, 1, 1, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 1, 1], [1, 0, 1, 0, 1, 0, 1, 0]]

# mood = instrumentation + the set of devices the seed chooses from
#  lead/acc timbres: piano ep marimba nylon bell muted; pad: saw air orch; kit: brush electro clap tick taiko or None
MOODS = {
    "calm":      dict(bpm=(60, 76), keys=["C", "F", "G", "D", "Eb", "A", "Bb"], mode="major", lead="piano", acc="piano", pad="air", kit=None,
                      accomp=["arp", "block", "motif"], bass=["whole", "half"], feel=["rubato", "straight"], intro=["pad", "pad_lead", "lead_solo"],
                      lift=["lead"], lead_oct=[0, 12], bright=0.30, verb=0.5, levels=dict(pad=0.9, acc=0.6, lead=0.8, bass=0.45, drums=0.0),
                      sus=0.1, gap=True, density=1, strings_b=True),
    "tense":     dict(bpm=(80, 96), keys=["Dm", "Am", "Em", "Gm", "Cm", "Bm", "F#m"], mode="minor", lead="muted", acc="muted", pad="saw", kit="tick",
                      accomp=["pulse", "arp"], bass=["pulse8", "walk"], feel=["straight", "dotted"], intro=["drone", "pad"],
                      lift=["drums", "lead"], lead_oct=[0, 12], bright=0.35, verb=0.35, levels=dict(pad=0.7, acc=0.7, lead=0.7, bass=0.9, drums=0.6),
                      sus=0.5, gap=True, density=2, strings_b=False),
    "hopeful":   dict(bpm=(84, 100), keys=["G", "C", "D", "A", "F", "E", "Bb"], mode="major", lead="ep", acc="ep", pad="orch", kit="brush",
                      accomp=["arp", "block"], bass=["half", "rootfifth"], feel=["straight", "swing"], intro=["pad", "pad_lead"],
                      lift=["drums", "lead"], lead_oct=[0, 12], bright=0.45, verb=0.4, levels=dict(pad=0.8, acc=0.8, lead=0.9, bass=0.7, drums=0.7),
                      sus=0.0, gap=False, density=2, strings_b=True),
    "bright":    dict(bpm=(100, 120), keys=["D", "G", "C", "A", "E", "F", "Bb"], mode="major", lead="marimba", acc="nylon", pad="air", kit="clap",
                      accomp=["arp", "block"], bass=["rootfifth", "walk"], feel=["straight", "swing"], intro=["lead_solo", "pad_lead"],
                      lift=["drums", "lead"], lead_oct=[0, 12], bright=0.6, verb=0.3, levels=dict(pad=0.5, acc=0.9, lead=1.0, bass=0.8, drums=0.8),
                      sus=0.0, gap=False, density=2, strings_b=False),
    "tech":      dict(bpm=(104, 128), keys=["Am", "Em", "Dm", "Cm", "Gm", "Bm", "F#m"], mode="minor", lead="bell", acc="ep", pad="saw", kit="electro",
                      accomp=["pulse", "arp", "motif"], bass=["wobble", "pulse8"], feel=["straight", "dotted"], intro=["drone", "pad"],
                      lift=["drums", "lead"], lead_oct=[0, 12], bright=0.55, verb=0.25, levels=dict(pad=0.7, acc=0.6, lead=0.6, bass=1.0, drums=0.9),
                      sus=0.2, gap=True, density=4, strings_b=False),
    "warm":      dict(bpm=(72, 88), keys=["F", "Bb", "C", "Eb", "G", "D", "Ab"], mode="major", lead="nylon", acc="ep", pad="air", kit="brush",
                      accomp=["block", "arp"], bass=["half", "whole", "rootfifth"], feel=["swing", "straight"], intro=["pad_lead", "lead_solo", "pad"],
                      lift=["lead", "drums"], lead_oct=[0, 12], bright=0.35, verb=0.4, levels=dict(pad=0.8, acc=0.8, lead=0.9, bass=0.7, drums=0.45),
                      sus=0.1, gap=True, density=1, strings_b=False),
    "cinematic": dict(bpm=(60, 80), keys=["Dm", "Cm", "Gm", "Am", "Em", "Bbm", "Fm"], mode="minor", lead="piano", acc="piano", pad="orch", kit="taiko",
                      accomp=["motif", "block"], bass=["whole", "half"], feel=["straight", "rubato"], intro=["pad", "drone", "pad_lead"],
                      lift=["drums", "lead"], lead_oct=[12, 24], bright=0.4, verb=0.7, levels=dict(pad=1.0, acc=0.5, lead=0.6, bass=0.9, drums=0.9),
                      sus=0.25, gap=True, density=1, strings_b=True),
}
LEAD_BASE = {"piano": 72, "ep": 67, "marimba": 72, "nylon": 60, "bell": 84, "muted": 60}


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def parse_key(s, default_mode):
    """'Am' / 'C' / 'F#m' / 'Bb' / 'Ebmaj' -> (pitch class 0..11, 'major'|'minor')"""
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major)?\s*", s or "")
    if not m:
        die(f"unknown key: {s} (e.g. Am, C, F#m, Bb)")
    pc = (PC[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    q = m.group(3) or ""
    mode = "minor" if q.startswith("m") and not q.startswith("maj") else "major" if q else default_mode
    return pc, mode


def key_name(pc, mode):
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    return names[pc % 12] + ("m" if mode == "minor" else "")


def chord_pcs(key_pc, sym, seventh, sus=None):
    off, q = CH[sym]
    root = (key_pc + off) % 12
    third = root + (3 if q == "m" else 4)
    if sus == "sus4":
        third = root + 5
    elif sus == "sus2":
        third = root + 2
    tones = [root, third, root + 7]
    tones.append(root + (10 if q == "m" else 11) if seventh else root + 14)
    return [t % 12 for t in tones]


def voice_chord(pcs, prev, lo=55, hi=76):
    """root in the low octave (48..59); the upper three voices move to the nearest chord tone (smooth voice leading)"""
    root = pcs[0] + 48
    cands = sorted(p for p in range(lo, hi + 1) if p % 12 in pcs[1:])
    if prev is None:
        prev = [62, 66, 69]
    upper, used = [], set()
    for p0 in prev:
        best = min((abs(c - p0), c) for c in cands if c not in used)[1]
        upper.append(best)
        used.add(best)
    return [root] + sorted(upper)


# ---------- synthesis helpers (vectorized) ----------

def _t(n):
    return np.arange(n, dtype=F32) / SR


def lowpass_fft(x, fc, poles=2):
    """zero-phase soft low-pass |H| = 1/(1+(f/fc)^2)^(poles/2)"""
    n = len(x)
    X = np.fft.rfft(x.astype(F32))
    f = np.fft.rfftfreq(n, 1 / SR)
    H = (1.0 / (1.0 + (f / fc) ** 2)) ** (poles / 2)
    return np.fft.irfft(X * H.astype(F32), n).astype(F32)


def highpass_fft(x, fc):
    n = len(x)
    X = np.fft.rfft(x.astype(F32))
    f = np.fft.rfftfreq(n, 1 / SR)
    r = (f / fc) ** 2
    return np.fft.irfft(X * (r / (1 + r)).astype(F32), n).astype(F32)


def _attack(x, a_s):
    a = min(len(x), int(a_s * SR))
    if a:
        x[:a] *= (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a))).astype(F32)
    tail = min(len(x), int(0.06 * SR))
    x[-tail:] *= np.linspace(1, 0, tail, dtype=F32)
    return x


def _partials(f, t, parts, rng):
    """parts: [(frequency ratio, amplitude, decay per second)] -> summed sines"""
    out = np.zeros(len(t), F32)
    for ratio, amp, dec in parts:
        fh = f * ratio
        if fh > SR * 0.45 or amp <= 0:
            continue
        out += (amp * np.sin(2 * np.pi * fh * t + rng.uniform(0, 2 * np.pi)) * np.exp(-dec * t)).astype(F32)
    return out


_NOTES = {}


def note_wave(timbre, midi):
    """one note (cached per timbre and pitch); each plucked family has its own partials, decay and attack"""
    k = (timbre, midi)
    if k in _NOTES:
        return _NOTES[k]
    f = midi_hz(midi)
    rng = np.random.default_rng(midi * 31 + sum(ord(c) for c in timbre))   # not hash(): it differs per process
    if timbre == "piano":       # felt piano: 7 inharmonic partials, fast high decay, hammer noise, soft low-pass
        dur = 2.4
        t = _t(int(dur * SR))
        B = 0.0003 + 0.0004 * max(0.0, (f - 200) / 1000)
        parts = [(h * math.sqrt(1 + B * h * h), (1 / h) ** 1.35 * math.exp(-(h - 1) * 0.28 * math.sqrt(f / 262)), 1.6 + 0.9 * h + f / 420) for h in range(1, 8)]
        out = _partials(f, t, parts, rng)
        th = int(0.012 * SR)
        out[:th] += 0.06 * rng.standard_normal(th).astype(F32) * np.exp(-np.arange(th) / SR * 400).astype(F32)
        out = lowpass_fft(_attack(out, 0.006), 1800 + f * 1.5)
    elif timbre == "ep":        # Rhodes: fundamental + a little 2nd/3rd + fast "tine" partials; light chorus
        dur = 2.6
        t = _t(int(dur * SR))
        parts = [(1, 1.0, 1.2 + f / 900), (2, 0.28, 2.5), (3, 0.10, 3.5), (6.0, 0.18, 14), (9.0, 0.08, 22)]
        out = _partials(f, t, parts, rng) + 0.5 * _partials(f * 1.003, t, parts[:3], rng)
        out = lowpass_fft(_attack(out, 0.004), 3500)
    elif timbre == "marimba":   # marimba: ratios 1, 4, 10.1, fast decay, mallet noise
        dur = 1.1
        t = _t(int(dur * SR))
        parts = [(1, 1.0, 5.0 + f / 150), (4.0, 0.35, 16), (10.1, 0.12, 40)]
        out = _partials(f, t, parts, rng)
        th = int(0.004 * SR)
        out[:th] += 0.25 * rng.standard_normal(th).astype(F32) * np.exp(-np.arange(th) / SR * 1500).astype(F32)
        out = lowpass_fft(_attack(out, 0.002), 6000, poles=1)
    elif timbre in ("nylon", "muted"):  # nylon guitar: 12 harmonics ~1/k, pick noise; muted = darker, shorter
        dur = 1.6 if timbre == "nylon" else 0.8
        t = _t(int(dur * SR))
        mute = 2.2 if timbre == "muted" else 1.0
        parts = [(h * math.sqrt(1 + 0.00008 * h * h), (1 / h) ** 1.1, (2.4 + 0.7 * h + f / 500) * mute) for h in range(1, 13)]
        out = _partials(f, t, parts, rng)
        th = int(0.006 * SR)
        out[:th] += 0.12 * rng.standard_normal(th).astype(F32) * np.exp(-np.arange(th) / SR * 700).astype(F32)
        out = lowpass_fft(_attack(out, 0.003), (2600 + f) if timbre == "nylon" else (900 + f * 0.5))
    elif timbre == "bell":      # bell / glockenspiel: inharmonic 1, 2.76, 5.4, 8.93, long tail
        dur = 3.0
        t = _t(int(dur * SR))
        parts = [(1, 1.0, 1.0 + f / 1500), (2.76, 0.45, 2.2), (5.40, 0.2, 3.5), (8.93, 0.08, 5.0)]
        out = _partials(f, t, parts, rng)
        out = lowpass_fft(_attack(out, 0.002), 9000, poles=1)
    else:
        die(f"unknown timbre: {timbre}")
    out /= (np.abs(out).max() + 1e-9)
    _NOTES[k] = out
    return out


def saw_stack(f, n, detunes, phase_seed, vib=0.0, shape="saw"):
    """detuned saws (or triangles); vib = vibrato depth in cents"""
    t = _t(n)
    rng = np.random.default_rng(phase_seed)
    out = np.zeros(n, F32)
    if vib:
        d = 2 ** (vib / 1200) - 1
        t = t + (d / (2 * np.pi * 5.2)) * np.sin(2 * np.pi * 5.2 * t + rng.uniform(0, 6.28))
    for c in detunes:
        ph = t * F32(f * 2 ** (c / 1200))
        ph += F32(rng.uniform(0, 1))
        ph -= np.floor(ph)
        if shape == "tri":
            ph = np.abs(ph * 2 - 1)
        out += ph
    out *= F32(2.0 / len(detunes))
    out -= F32(1.0)
    return out


def pad_voice(kind, f, n, ch_seed, side):
    """saw: detuned saws; air: triangle + sub-octave sine; orch: wide detune, vibrato, octave-down thickening"""
    det = ((-7, 5), (-4, 8))[side]
    if kind == "saw":
        return saw_stack(f, n, det, ch_seed)
    if kind == "air":
        t = _t(n)
        tri = saw_stack(f, n, ((-4, 3), (-2, 5))[side], ch_seed, shape="tri")
        return 0.7 * tri + 0.5 * np.sin(2 * np.pi * f * 0.5 * t + side).astype(F32)
    if kind == "orch":
        return 0.8 * saw_stack(f, n, ((-12, 0, 11), (-9, 3, 13))[side], ch_seed, vib=6) + 0.35 * saw_stack(f * 0.5, n, ((-5,), (6,))[side], ch_seed + 1)
    die(f"unknown pad: {kind}")


def env_ar(n, a, r):
    e = np.ones(n, F32)
    ai, ri = min(n, int(a * SR)), min(n, int(r * SR))
    if ai:
        e[:ai] = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ai))).astype(F32)
    if ri:
        e[-ri:] *= np.cos(np.linspace(0, np.pi / 2, ri)).astype(F32) ** 2
    return e


# ---------- drums ----------

def _noise(n, seed):
    return np.random.default_rng(seed).standard_normal(n).astype(F32)


def drum_samples():
    s = {}
    n = int(0.28 * SR)
    t = _t(n)
    f = 42 + 85 * np.exp(-t * 26)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14)
    body[:int(0.004 * SR)] += 0.25 * _noise(int(0.004 * SR), 3) * np.exp(-np.arange(int(0.004 * SR)) / SR * 900)
    s["kick_soft"] = lowpass_fft(body.astype(F32), 2500)
    n = int(0.22 * SR)
    t = _t(n)
    f = 48 + 140 * np.exp(-t * 45)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 20)
    body[:int(0.003 * SR)] += 0.6 * _noise(int(0.003 * SR), 4)
    s["kick_tight"] = body.astype(F32)
    n = int(0.16 * SR)
    t = _t(n)
    x = _noise(n, 5) * np.exp(-t * 22).astype(F32)
    s["brush"] = lowpass_fft(highpass_fft(x, 700), 3200)
    n = int(0.05 * SR)
    t = _t(n)
    s["hat_soft"] = highpass_fft(_noise(n, 9) * np.exp(-t * 90).astype(F32), 6000)
    n = int(0.03 * SR)
    t = _t(n)
    s["hat_crisp"] = highpass_fft(_noise(n, 10) * np.exp(-t * 160).astype(F32), 8500)
    n = int(0.12 * SR)
    t = _t(n)
    s["hat_open"] = highpass_fft(_noise(n, 11) * np.exp(-t * 28).astype(F32), 7000)
    n = int(0.18 * SR)   # clap: three very short bursts then a tail
    t = _t(n)
    x = np.zeros(n, F32)
    for i, g in ((0, 1.0), (int(0.011 * SR), 0.8), (int(0.021 * SR), 0.7)):
        m = int(0.012 * SR)
        x[i:i + m] += g * _noise(m, 12 + i) * np.exp(-np.arange(m) / SR * 500).astype(F32)
    x += 0.5 * _noise(n, 13) * np.exp(-t * 30).astype(F32)
    s["clap"] = lowpass_fft(highpass_fft(x, 900), 5000)
    n = int(0.045 * SR)
    t = _t(n)
    s["shaker"] = highpass_fft(_noise(n, 14) * (np.exp(-t * 70) * (1 - np.exp(-t * 900))).astype(F32), 4500)
    n = int(0.05 * SR)   # woodblock: short 1.1 kHz resonance + strike noise
    t = _t(n)
    s["wood"] = (np.sin(2 * np.pi * 1100 * t) * np.exp(-t * 90) + 0.3 * _noise(n, 15) * np.exp(-t * 400)).astype(F32)
    n = int(0.03 * SR)   # clock tick: higher and shorter
    t = _t(n)
    s["tick"] = (np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 180) + 0.5 * _noise(n, 16) * np.exp(-t * 600)).astype(F32)
    n = int(0.9 * SR)    # taiko: falling 60 Hz drum head + skin noise, long tail
    t = _t(n)
    f = 55 + 60 * np.exp(-t * 12)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
    body += 0.35 * lowpass_fft(_noise(n, 17), 900) * np.exp(-t * 18)
    s["taiko"] = lowpass_fft(body.astype(F32), 1800)
    for k, v in s.items():
        s[k] = v / (np.abs(v).max() + 1e-9)
    return s


_DRUMS = []


def drums():
    if not _DRUMS:
        _DRUMS.append(drum_samples())
    return _DRUMS[0]


def kit_events(kit, sec, beat, var, srng, feel):
    """one bar of drums: [(sample, seconds into bar, gain)]; var 0/1 is the seed's variation; swing delays off-beats"""
    off = beat * (0.62 if feel == "swing" else 0.5)
    ev = []
    lvl = 1.0 if sec == "B" else 0.75
    jit = lambda: srng.normal(0, 0.003)   # noqa: E731
    if kit == "brush":
        for bt in range(4):
            if bt in (0, 2):
                ev.append(("kick_soft", bt * beat, 0.8 * lvl))
            if bt in (1, 3):
                ev.append(("brush", bt * beat + jit(), 0.3 * lvl))
            ev.append(("hat_soft", bt * beat + off + jit(), 0.12 * lvl))
        if var and sec == "B":
            ev.append(("kick_soft", 3 * beat + off, 0.45 * lvl))
    elif kit == "electro":
        for bt in range(4):
            if bt in (0, 2) or (var and bt == 3 and sec == "B"):
                ev.append(("kick_tight", bt * beat + (off if bt == 3 else 0), 0.9 * lvl))
            if bt in (1, 3) and sec == "B":
                ev.append(("clap", bt * beat, 0.35 * lvl))
            ev.append(("hat_crisp", bt * beat, 0.2 * lvl))
            ev.append(("hat_crisp", bt * beat + beat / 2, 0.3 * lvl))
            if var:
                ev.append(("hat_crisp", bt * beat + beat / 4, 0.1 * lvl))
                ev.append(("hat_crisp", bt * beat + 3 * beat / 4, 0.12 * lvl))
        ev.append(("hat_open", 3 * beat + beat / 2, 0.18 * lvl))
    elif kit == "clap":
        for bt in range(4):
            if bt in (0, 2):
                ev.append(("kick_soft", bt * beat, 0.55 * lvl))
            if bt in (1, 3):
                ev.append(("clap", bt * beat + jit(), 0.4 * lvl))
            ev.append(("shaker", bt * beat, 0.25 * lvl))
            ev.append(("shaker", bt * beat + off + jit(), 0.35 * lvl))
        if var:
            ev.append(("kick_soft", 1 * beat + off, 0.4 * lvl))
    elif kit == "tick":
        for bt in range(4):
            ev.append(("wood" if bt == 0 else "tick", bt * beat, (0.5 if bt == 0 else 0.3) * lvl))
            ev.append(("tick", bt * beat + off, 0.22 * lvl))
            if var:
                ev.append(("wood", bt * beat + beat * 0.75, 0.18 * lvl))
        if sec == "B":
            ev.append(("kick_soft", 0, 0.7))
            ev.append(("kick_soft", 2 * beat + (off if var else 0), 0.5))
    elif kit == "taiko":
        ev.append(("taiko", 0, 1.0))
        if sec == "B" or var:
            ev.append(("taiko", 2 * beat, 0.7))
        if sec == "B":
            ev.append(("taiko", 3 * beat + off, 0.5))
            ev.append(("brush", 1 * beat, 0.15))
            ev.append(("brush", 3 * beat, 0.15))
    return ev


def place(buf, sig, at_s, gain=1.0):
    i = int(round(at_s * SR))
    if i < 0 or i >= len(buf):
        return
    end = min(len(buf), i + len(sig))
    buf[i:end] += (gain * sig[:end - i]).astype(F32)


def schroeder(x, seed_off=0, size=1.0):
    """Schroeder reverb: 4 combs + 2 allpasses, block-recursive (no per-sample loop); wet only"""
    n = len(x)
    acc = np.zeros(n, F32)
    for D0, g0 in ((1116, 0.84), (1277, 0.83), (1422, 0.82), (1617, 0.80)):
        D, g = int(D0 * size * SR / 44100) + seed_off, min(0.93, g0 + (size - 1) * 0.12)
        y = np.empty(n, F32)
        for s in range(0, n, D):
            e = min(n, s + D)
            y[s:e] = x[s:e] if s == 0 else x[s:e] + g * y[s - D:e - D]
        acc += y
    acc *= 0.25
    for D, g in ((int(556 * SR / 44100) + seed_off, 0.5), (int(341 * SR / 44100) + seed_off, 0.5)):
        y = np.empty(n, F32)
        for s in range(0, n, D):
            e = min(n, s + D)
            y[s:e] = -g * acc[s:e] if s == 0 else -g * acc[s:e] + acc[s - D:e - D] + g * y[s - D:e - D]
        acc = y
    return lowpass_fft(acc, 5500, poles=1)


def bus_compress(st, thr_db=-14.0, ratio=1.8, win_s=0.02, smooth_s=0.16):
    n, w = st.shape[0], int(win_s * SR)
    m = (n // w) * w
    mono = st[:m].mean(axis=1).reshape(-1, w)
    rms_db = 20 * np.log10(np.sqrt((mono ** 2).mean(axis=1)) + 1e-9)
    gr = -np.maximum(0.0, rms_db - thr_db) * (1 - 1 / ratio)
    k = max(1, int(smooth_s / win_s))
    gr = np.convolve(gr, np.ones(k) / k, mode="same")
    g = 10 ** (gr / 20)
    gs = np.interp(np.arange(n), np.arange(len(g)) * w + w / 2, g).astype(F32)
    return st * gs[:, None]


def soft_clip(x, drive=1.15):
    return (np.tanh(drive * x) / math.tanh(drive)).astype(F32)


# ---------- composition ----------

def plan_structure(seconds, bpm, lift_at):
    """whole bars: intro / A / B (from lift_at, nearest bar) / held final chord.
    -> (bar seconds, bars, intro bars, B start bar or None, final chord start seconds)"""
    bar = 240.0 / bpm
    hold_min = max(3.2, 1.5 * bar)
    n_bars = max(2, int((seconds - hold_min) // bar))
    final_at = n_bars * bar
    intro = 1 if n_bars < 6 else 2 if n_bars < 12 else 4
    lift_bar = None
    if lift_at is None and seconds >= 45:
        lift_at = 0.58 * seconds
    if lift_at is not None and lift_at > 0:
        lb = int(round(lift_at / bar))
        if intro + 2 <= lb <= n_bars - 1:   # B lasts at least one bar before the final chord
            lift_bar = lb
    return bar, n_bars, intro, lift_bar, final_at


def make_motif(rng, density):
    """two-bar call and response; shape asc/desc/arch; the response shifts, inverts or answers the call.
    -> {"notes": [(bar 0/1, eighth slot, scale degree from the tonic)], ...}"""
    shape = ["asc", "desc", "arch"][int(rng.integers(3))]
    call = list(CELLS[int(rng.integers(len(CELLS)))])
    resp = list(CELLS[int(rng.integers(len(CELLS)))])
    if density <= 1 and rng.random() < 0.5:   # slow moods: fewer notes in the call
        for i in (1, 3, 5, 7):
            call[i] = 0
    n_call = sum(call)
    start = int(rng.integers(0, 5))
    steps = []
    for i in range(n_call):
        if shape == "asc":
            steps.append(start + i * int(rng.integers(1, 3)))
        elif shape == "desc":
            steps.append(start + 4 - i * int(rng.integers(1, 3)))
        else:
            half = max(1, n_call // 2)
            steps.append(start + (i if i < half else (2 * half - i)) * int(rng.integers(1, 3)))
    notes = []
    k = 0
    for s, on in enumerate(call):
        if on:
            notes.append((0, s, steps[k]))
            k += 1
    var = ["shift", "invert", "answer"][int(rng.integers(3))]
    k = 0
    for s, on in enumerate(resp):
        if not on:
            continue
        src = steps[k % len(steps)]
        if var == "shift":
            d = src + (-1 if rng.random() < 0.6 else 1)
        elif var == "invert":
            d = 2 * steps[0] - src
        else:
            d = src - 2 if k % 2 else src + 1
        k += 1
        notes.append((1, s, d))
    if notes and notes[-1][0] == 1:   # the response ends on tonic or fifth
        b, s, d = notes[-1]
        notes[-1] = (b, s, 7 if d > 4 else 0 if d > -3 else -7)
    return {"notes": notes, "shape": shape, "var": var}


def degree_pitch(deg, kp, md, base):
    sc = SCALE[md]
    octv, i = divmod(deg, 7)
    p = kp + sc[i] + 12 * octv
    while p < base - 6:
        p += 12
    while p > base + 6 + 12:
        p -= 12
    return p


def snap_to_chord(p, v):
    pcs = {x % 12 for x in v}
    for d in (0, -1, 1, -2, 2):
        if (p + d) % 12 in pcs:
            return p + d
    return p


def compose(seconds, mood, *, bpm=None, key=None, lift_at=None, seed=0):
    """-> (stereo float32 [n, 2], meta). Loudness is normalized in render_file (needs ffmpeg)."""
    if mood not in MOODS:
        die(f"unknown mood: {mood} (choose from {', '.join(MOODS)})")
    seconds = float(seconds)
    if not 6 <= seconds <= 900:
        die("music length must be 6-900 s")
    M = MOODS[mood]
    rng = np.random.default_rng([int(seed), sum(ord(c) for c in mood)])   # the mood is part of the seed
    pick = lambda xs: xs[int(rng.integers(len(xs)))]   # noqa: E731
    lo_b, hi_b = M["bpm"]
    bpm = int(bpm or int(rng.integers(lo_b, hi_b + 1)))
    bpm = max(50, min(160, bpm))
    key_pc, mode = parse_key(key or pick(M["keys"]), M["mode"])
    accomp, bass_pat, feel, intro_kind, lift_kind = pick(M["accomp"]), pick(M["bass"]), pick(M["feel"]), pick(M["intro"]), pick(M["lift"])
    lead_oct = pick(M["lead_oct"])
    counter = bool(rng.random() < 0.5)
    kit_var = int(rng.integers(2))
    kit = M["kit"]
    drums_a = kit is not None and mood in ("bright", "tech")
    drums_b = kit is not None and (lift_kind == "drums" or drums_a or mood in ("hopeful",))
    strings_b = M["strings_b"] or lift_kind == "lead"
    lv = dict(M["levels"])
    bar, n_bars, intro_bars, lift_bar, final_at = plan_structure(seconds, bpm, lift_at)
    beat = bar / 4
    n = int(round(seconds * SR))
    seventh = mood in ("calm", "warm", "cinematic", "hopeful") or bool(rng.random() < 0.4)

    prog_a = pick(PROG[mode])
    if lift_bar is not None:   # B modulates: minor -> relative major, major -> up a minor third
        key_b, mode_b = (key_pc + 3) % 12, "major"
        prog_b = pick(PROG[mode_b])
    else:
        key_b, mode_b, prog_b = key_pc, mode, prog_a
    chords, prev = [], None   # per bar: (start s, end s, voicing, section, key root, mode)
    for b in range(n_bars):
        in_b = lift_bar is not None and b >= lift_bar
        kp, md, pg, off = (key_b, mode_b, prog_b, b - lift_bar) if in_b else (key_pc, mode, prog_a, b)
        sym = "I" if (b == lift_bar and mode == "minor") else pg[off % len(pg)]
        sus = None
        if M["sus"] and rng.random() < M["sus"] and off % 4 in (1, 3):
            sus = "sus4" if rng.random() < 0.6 else "sus2"
        v = voice_chord(chord_pcs(kp, sym, seventh, sus), prev)
        prev = v[1:]
        sec = "intro" if b < intro_bars else "B" if in_b else "A"
        chords.append((b * bar, (b + 1) * bar, v, sec, kp, md))
    final_v = voice_chord(chord_pcs(key_b, "I" if mode_b == "major" else "i", seventh), prev)
    chords.append((final_at, seconds, final_v, "outro", key_b, mode_b))

    motif = make_motif(rng, M["density"])
    arp = list(pick(ARPS))
    block = list(pick(BLOCKS))
    swing = 0.62 if feel == "swing" else 0.5
    rubato_amp = 0.02 if feel == "rubato" else 0.0

    def slot_time(s, slots):
        if feel == "dotted":
            return s * 0.75 * beat if slots == 8 else s * 0.375 * beat
        q, r = divmod(s, slots // 4)
        if slots == 8:
            return q * beat + (swing * beat if r else 0.0)
        return q * beat + r * beat / 4

    def humanize(t_abs, srng):
        return t_abs + srng.normal(0, 0.004) + rubato_amp * math.sin(2 * math.pi * t_abs / (2 * bar))

    pad_L, pad_R = np.zeros(n, F32), np.zeros(n, F32)
    str_L, str_R = np.zeros(n, F32), np.zeros(n, F32)
    acc_L, acc_R = np.zeros(n, F32), np.zeros(n, F32)
    lead_L, lead_R = np.zeros(n, F32), np.zeros(n, F32)
    bass = np.zeros(n, F32)
    drum = np.zeros(n, F32)
    bright = np.zeros(n, F32)
    DS = drums() if kit else None
    lead_t, acc_t = M["lead"], M["acc"]
    lead_base = LEAD_BASE[lead_t] + lead_oct

    def play(bufL, bufR, timbre, p, at, vel, pan=None):
        w = note_wave(timbre, p)
        if pan is None:
            pan = min(1.0, max(0.0, 0.5 + (p - 64) / 40))
        place(bufL, w, at, vel * math.sqrt(1 - pan))
        place(bufR, w, at + 0.004 * (pan - 0.5), vel * math.sqrt(pan))

    for ci, (t0, t1, v, sec, kp, md) in enumerate(chords):
        i0 = int(round(t0 * SR))
        rel = 0.6 if sec != "outro" else 0.0
        i1 = min(n, int(round((t1 + rel) * SR)))
        seg_n = i1 - i0
        if seg_n <= 0:
            continue
        srng = np.random.default_rng(seed * 1000 + ci)
        seg_t = _t(seg_n)
        pad_on = not (sec == "intro" and intro_kind in ("lead_solo", "drone"))
        if pad_on:
            attack = {"saw": 0.3, "air": 0.9, "orch": 1.2}[M["pad"]] if sec != "outro" else 0.1
            e = env_ar(seg_n, min(attack, (t1 - t0) * 0.5), rel if sec != "outro" else min(2.8, (t1 - t0) * 0.6))
            lvl = lv["pad"] * {"intro": 0.55, "A": 0.85, "B": 1.1, "outro": 1.15}[sec]
            for vi, p in enumerate(v):
                g = (0.9 if vi == 0 else 0.6) * lvl / 4
                f = midi_hz(p)
                pad_L[i0:i1] += g * pad_voice(M["pad"], f, seg_n, seed + ci * 7 + vi, 0) * e
                pad_R[i0:i1] += g * pad_voice(M["pad"], f, seg_n, seed + ci * 7 + vi + 100, 1) * e
        base = {"intro": 0.15, "A": 0.45, "B": 0.85, "outro": 0.5}[sec] * (0.6 + M["bright"])
        sweep = 1 - np.exp(-seg_t / 0.9)
        bright[i0:i1] = np.maximum(bright[i0:i1], (base * (0.75 + 0.25 * sweep)).astype(F32))
        if sec in ("B", "outro") and strings_b:   # string layer in B / outro
            es = env_ar(seg_n, min(1.1, (t1 - t0) * 0.45) if sec != "outro" else 0.4, rel if sec != "outro" else min(3.0, (t1 - t0) * 0.6))
            for vi, p in enumerate(v[1:]):
                f = midi_hz(p + 12)
                str_L[i0:i1] += 0.11 * saw_stack(f, seg_n, (-11, 9), seed + ci * 11 + vi + 300, vib=7) * es
                str_R[i0:i1] += 0.11 * saw_stack(f, seg_n, (-8, 12), seed + ci * 11 + vi + 400, vib=7) * es
        fb = midi_hz(v[0] - 12)
        f5 = midi_hz(v[0] - 12 + 7)
        if bass_pat == "wobble":
            tone = saw_stack(fb, seg_n, (-4, 4), seed + ci + 900) * 0.8
        else:
            tone = np.sin(2 * np.pi * fb * seg_t) + 0.25 * np.sin(4 * np.pi * fb * seg_t)
        if sec == "outro":
            tone = tone * env_ar(seg_n, 0.05, min(3.0, (t1 - t0) * 0.7))
        elif sec == "intro" and intro_kind == "lead_solo":
            tone = tone * 0
        elif bass_pat == "whole":
            tone = tone * env_ar(seg_n, 0.04, 0.5)
        elif bass_pat == "half":
            ph = seg_t % (2 * beat)
            tone = tone * np.exp(-ph * 1.2) * (1 - np.exp(-ph * 600)) * env_ar(seg_n, 0.0, rel)
        elif bass_pat == "rootfifth":
            g = np.zeros(seg_n, F32)
            for bt, f_, dur in ((0, fb, 1.9 * beat), (2, f5, 1.4 * beat), (3.5, fb, 0.45 * beat)):
                a_, b_ = int(bt * beat * SR), min(seg_n, int((bt * beat + dur) * SR))
                if a_ < seg_n:
                    tt = _t(b_ - a_)
                    g[a_:b_] += (np.sin(2 * np.pi * f_ * tt) + 0.25 * np.sin(4 * np.pi * f_ * tt)) * np.exp(-tt * 1.5) * (1 - np.exp(-tt * 600))
            tone = g
        elif bass_pat == "walk":
            g = np.zeros(seg_n, F32)
            for bt, f_ in ((0, fb), (1, f5), (2, fb * 2), (3, f5)):
                a_, b_ = int(bt * beat * SR), min(seg_n, int((bt * beat + 0.8 * beat) * SR))
                tt = _t(b_ - a_)
                g[a_:b_] += (np.sin(2 * np.pi * f_ * tt) + 0.3 * np.sin(4 * np.pi * f_ * tt)) * np.exp(-tt * 5) * (1 - np.exp(-tt * 600))
            tone = g
        else:   # pulse8 / wobble: eighth-note pulses
            ph = seg_t % (beat / 2)
            tone = tone * np.exp(-ph * 9) * (1 - np.exp(-ph * 600)) * env_ar(seg_n, 0.0, rel)
        bass[i0:i1] += (lv["bass"] * 0.55 * (0.7 if sec == "intro" else 1.0) * tone).astype(F32)
        if sec == "outro":   # slow arpeggio into the final chord, then a high root
            for j, p in enumerate(v):
                play(lead_L, lead_R, lead_t, p + 12, t0 + j * 0.12, 0.35 * lv["lead"])
            play(lead_L, lead_R, lead_t, v[0] + 24, t0 + 0.5, 0.3 * lv["lead"])
            continue
        chord_up = list(v[1:]) + [v[1] + 12]
        acc_vel = {"intro": 0.22, "A": 0.4, "B": 0.48}[sec] * lv["acc"]
        acc_on = not (sec == "intro" and intro_kind in ("drone", "lead_solo"))
        if acc_on and accomp == "arp":
            slots = 16 if M["density"] >= 4 else 8
            pat = arp if slots == 8 else [arp[k % 8] for k in range(16)]
            dens = {"intro": 0.4, "A": 0.8, "B": 1.0}[sec]
            for s in range(slots):
                idx = pat[s % len(pat)]
                if idx < 0 or srng.random() > dens:
                    continue
                tt = slot_time(s, slots)
                if tt >= bar:
                    continue
                vel = acc_vel * (1 + 0.12 * srng.standard_normal()) * (1.15 if s % 4 == 0 else 1.0)
                play(acc_L, acc_R, acc_t, chord_up[idx % 4], humanize(t0 + tt, srng), vel)
        elif acc_on and accomp == "block":
            for s, on in enumerate(block):
                if not on or (sec == "intro" and s != 0):
                    continue
                tt = slot_time(s, 8)
                for j, p in enumerate(chord_up[:3]):   # light strum: 14 ms apart
                    play(acc_L, acc_R, acc_t, p, humanize(t0 + tt, srng) + j * 0.014, acc_vel * 0.8, pan=0.3 + 0.2 * j)
        elif acc_on and accomp == "pulse":
            slots = 16 if M["density"] >= 4 else 8
            for s in range(slots):
                tt = slot_time(s, slots)
                if tt >= bar:
                    continue
                p = v[0] + (12 if s % 4 == 2 else 0) + (7 if s % 8 == 6 else 0)
                play(acc_L, acc_R, acc_t, p, t0 + tt, acc_vel * (0.9 if s % 4 == 0 else 0.6), pan=0.5 + 0.15 * (-1) ** s)
        elif acc_on:   # motif: just root and fifth on beat one
            play(acc_L, acc_R, acc_t, v[0] + 12, humanize(t0, srng), acc_vel * 0.7, pan=0.4)
            play(acc_L, acc_R, acc_t, v[0] + 19, humanize(t0 + 0.02, srng), acc_vel * 0.5, pan=0.6)
        if sec == "intro":
            lead_here = intro_kind in ("pad_lead", "lead_solo")
            lead_vel = 0.3
        else:
            idx = (ci - intro_bars) if sec == "A" else (ci - lift_bar)
            lead_here = (idx % 4 in (0, 1)) if M["gap"] else True
            lead_vel = 0.5 if sec == "A" else 0.6
        if lead_here:
            which = ci % 2
            oct_up = 12 if (sec == "B" and lift_kind == "lead") else 0
            for b_, s, deg in motif["notes"]:
                if b_ != which:
                    continue
                p = degree_pitch(deg, kp, md, lead_base + oct_up)
                if s % 4 == 0:
                    p = snap_to_chord(p, v)
                at = humanize(t0 + slot_time(s, 8), srng)
                play(lead_L, lead_R, lead_t, p, at, lead_vel * lv["lead"] * (1 + 0.1 * srng.standard_normal()), pan=0.5)
                if counter and sec != "intro" and (sec == "B" or idx % 4 == 1):   # counter line: an eighth later, a third lower
                    pc_ = degree_pitch(deg - 2, kp, md, lead_base - 12 + oct_up)
                    play(acc_L, acc_R, acc_t, pc_, at + beat / 2, 0.4 * lead_vel * lv["acc"], pan=0.3)
        drums_here = (sec == "A" and drums_a) or (sec == "B" and drums_b) or (sec == "intro" and intro_kind == "drone" and kit in ("tick", "electro"))
        if drums_here:
            for name, tt, g in kit_events(kit, "intro" if sec == "intro" else sec, beat, kit_var, srng, feel):
                place(drum, DS[name], t0 + tt, g * lv["drums"] * (0.5 if sec == "intro" else 1.0))

    def filt_pad(x, fcs):   # three low-passed versions blended by the brightness curve (the pad opens in B)
        X = np.fft.rfft(x)
        f = np.fft.rfftfreq(n, 1 / SR)
        lo, mid, hi = (np.fft.irfft(X * (1.0 / (1.0 + (f / fc) ** 2)).astype(F32), n).astype(F32) for fc in fcs)
        b = np.clip(bright, 0, 1)
        w_hi = np.clip((b - 0.5) * 2, 0, 1)
        w_mid = np.clip(1 - np.abs(b - 0.5) * 2, 0, 1)
        w_lo = np.clip((0.5 - b) * 2, 0, 1)
        return lo * w_lo + mid * w_mid + hi * w_hi

    fcs = {"saw": (520, 1300, 3000), "air": (400, 900, 1800), "orch": (600, 1500, 3400)}[M["pad"]]
    pad_L, pad_R = filt_pad(pad_L, fcs), filt_pad(pad_R, fcs)
    tl = np.arange(n, dtype=F32) / SR
    if mood == "tech":   # beat-synced pump on the pad (fake sidechain)
        pump = 1 - 0.55 * np.exp(-((tl % beat) / 0.13)) * (tl >= intro_bars * bar) * (tl < final_at)
        pad_L *= pump
        pad_R *= pump
    haas = int(0.012 * SR)
    pad_R = np.concatenate([np.zeros(haas, F32), pad_R[:-haas]])
    if str_L.any():
        str_L, str_R = lowpass_fft(str_L, 3600), lowpass_fft(str_R, 3600)
        str_R = np.concatenate([np.zeros(haas // 2, F32), str_R[:-(haas // 2)]])
    if bass_pat == "wobble":
        lo, hi = lowpass_fft(bass, 180), lowpass_fft(bass, 1100)
        rate = bpm / 60 / float(pick([2, 4, 1]))
        w = (0.5 + 0.5 * np.sin(2 * np.pi * rate * tl)).astype(F32) * (tl >= intro_bars * bar)
        bass = lo * (1 - w) + hi * w
    else:
        bass = lowpass_fft(bass, 220, poles=1)

    verb, size = M["verb"], (1.6 if mood == "cinematic" else 1.0)
    dry_L = pad_L * 0.55 + acc_L * 0.8 + lead_L * 0.85 + str_L + drum * 0.5
    dry_R = pad_R * 0.55 + acc_R * 0.8 + lead_R * 0.85 + str_R + drum * 0.5
    send = lowpass_fft((pad_L + pad_R) * 0.2 + (acc_L + acc_R) * 0.22 + (lead_L + lead_R) * 0.3 + (str_L + str_R) * 0.3
                       + drum * (0.25 if kit == "taiko" else 0.12), 3800)
    L = dry_L + verb * schroeder(send, size=size)
    R = dry_R + verb * schroeder(send, seed_off=23, size=size)
    L += bass
    R += bass
    st = np.stack([L, R], axis=1)
    st = bus_compress(st)
    st /= (np.abs(st).max() + 1e-9) / 0.7
    st = soft_clip(st)
    fi, fo = int(0.4 * SR), int(2.6 * SR)
    st[:fi] *= np.linspace(0, 1, fi, dtype=F32)[:, None]
    st[-fo:] *= (np.clip(np.cos(np.linspace(0, np.pi / 2, fo, dtype=F32)), 0, 1) ** 1.5)[:, None]

    sections = []

    def add(name, s, e, kp, md):
        if e > s:
            sections.append({"name": name, "start": round(s, 2), "end": round(e, 2), "key": key_name(kp, md)})
    add("intro", 0, intro_bars * bar, key_pc, mode)
    if lift_bar is not None:
        add("A", intro_bars * bar, lift_bar * bar, key_pc, mode)
        add("B", lift_bar * bar, final_at, key_b, mode_b)
    else:
        add("A", intro_bars * bar, final_at, key_pc, mode)
    add("outro", final_at, seconds, key_b, mode_b)
    meta = {"seconds": seconds, "bpm": bpm, "key": key_name(key_pc, mode), "mood": mood, "seed": int(seed), "sections": sections,
            "progression": prog_a, "bars": n_bars, "lift_at": round(lift_bar * bar, 2) if lift_bar is not None else None,
            "arrangement": {"lead": lead_t, "acc": acc_t, "pad": M["pad"], "kit": kit, "accomp": accomp, "bass": bass_pat, "feel": feel,
                            "intro": intro_kind, "lift": lift_kind, "lead_oct": lead_oct, "counter": counter, "motif": motif["shape"] + "/" + motif["var"]}}
    return st, meta


# ---------- output ----------

def ffmpeg():
    p = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.path.isfile("/opt/homebrew/bin/ffmpeg") else None)
    if not p:
        die("ffmpeg not found on PATH (brew install ffmpeg)")
    return p


def write_wav(p, st):
    with wave.open(p, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(st, -1, 1) * 32767).astype("<i2").tobytes())


def measure_lufs(wav):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", wav, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    v = re.findall(r"I:\s+(-?[\d.]+|-inf)", r.stderr)
    return float(v[-1]) if v and v[-1] != "-inf" else None


def render_file(out_path, seconds, mood, **kw):
    """compose -> gain to TARGET_LUFS (measured with ebur128) -> 48 kHz 16-bit WAV. Returns meta."""
    t0 = time.time()
    st, meta = compose(seconds, mood, **kw)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tmp = os.path.splitext(out_path)[0] + ".tmp.wav"
    write_wav(tmp, st)
    lufs = measure_lufs(tmp)
    if lufs is not None:
        st = st * F32(10 ** ((TARGET_LUFS - lufs) / 20))
        pk = float(np.abs(st).max())
        if pk > PEAK:   # the gain hit the ceiling: soft-clip back under PEAK and re-measure
            st = soft_clip(st / pk * 1.4, 1.6) * PEAK
            write_wav(tmp, st)
            lufs = measure_lufs(tmp)
        else:
            write_wav(tmp, st)
            lufs = TARGET_LUFS
    os.replace(tmp, out_path)
    meta.update(lufs=round(lufs, 1) if lufs is not None else None, peak=round(float(np.abs(st).max()), 3),
                render_seconds=round(time.time() - t0, 2))
    return meta


def plan_lift(scenes, total):
    """-> (lift_at seconds or None, scene id or None, reason). Hero scene first; else scene start nearest 58 %."""
    starts, acc = [], 0.0
    for s in scenes:
        starts.append((str(s.get("id")), acc, bool(s.get("hero"))))
        acc += float(s.get("duration") or 0)
    heroes = [(sid, st) for sid, st, h in starts if h and 0 < st < total]
    if heroes:
        return heroes[0][1], heroes[0][0], "hero scene"
    if total >= 45 and len(scenes) >= 3:
        cands = [(sid, st) for sid, st, _ in starts if 0.4 * total <= st <= 0.75 * total]
        if cands:
            sid, st = min(cands, key=lambda x: abs(x[1] - 0.58 * total))
            return st, sid, "scene start nearest 58 %"
        return 0.58 * total, None, "58 % of the film"
    return None, None, "film shorter than 45 s: no lift"


def main():
    ap = argparse.ArgumentParser(description="Compose background music for the film (numpy, seconds, royalty-free).")
    ap.add_argument("--project", required=True, help="project directory (contains storyboard.json)")
    ap.add_argument("--mood", choices=sorted(MOODS), help="default: storyboard bgm.mood, else calm")
    ap.add_argument("--seed", type=int, help="default: storyboard bgm.seed, else 0; same seed = same piece")
    ap.add_argument("--seconds", type=float, help="default: sum of scene durations")
    ap.add_argument("--preview", action="store_true", help=f"write a {PREVIEW_SECONDS:.0f} s sketch to audio/bgm-preview.wav")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    sb_path = os.path.join(proj, "storyboard.json")
    if not os.path.isfile(sb_path):
        print(f"error: no storyboard.json in {proj}", file=sys.stderr)
        return 2
    with open(sb_path, encoding="utf-8") as f:
        sb = json.load(f)
    cfg = sb.get("bgm", {})
    if cfg is None and not a.mood:
        print("storyboard bgm is null: no music for this film (pass --mood to compose anyway)")
        return 0
    cfg = cfg or {}
    mood = a.mood or cfg.get("mood") or "calm"
    if mood not in MOODS:
        print(f"error: unknown mood {mood!r} in storyboard (choose from {', '.join(MOODS)})", file=sys.stderr)
        return 2
    seed = a.seed if a.seed is not None else int(cfg.get("seed") or 0)
    scenes = sb.get("scenes") or []
    try:
        if a.preview:
            out = os.path.join(proj, "audio", "bgm-preview.wav")
            meta = render_file(out, PREVIEW_SECONDS, mood, seed=seed, lift_at=0.58 * PREVIEW_SECONDS)
            why = "preview"
        else:
            if a.seconds:
                total = float(a.seconds)
            else:
                missing = [str(s.get("id")) for s in scenes if not s.get("duration")]
                if missing or not scenes:
                    print(f"error: scenes without duration: {', '.join(missing) or 'no scenes'} (run tts.py first or pass --seconds)",
                          file=sys.stderr)
                    return 2
                fps = float(sb.get("fps") or 30)
                total = sum(max(1, round(float(s["duration"]) * fps)) for s in scenes) / fps
            lift_at, lift_scene, why = plan_lift(scenes, total)
            out = os.path.join(proj, "audio", "bgm.wav")
            meta = render_file(out, total, mood, seed=seed, lift_at=lift_at if lift_at is not None else 0)
            meta["lift_scene"] = lift_scene if meta["lift_at"] is not None else None
    except BgmError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rel = os.path.relpath(out, proj)
    secs = " | ".join(f"{s['name']} {s['start']:.1f}-{s['end']:.1f} {s['key']}" for s in meta["sections"])
    if meta["lift_at"] is not None:
        lift = f"lift at {meta['lift_at']:.2f} s ({why}{', scene ' + meta['lift_scene'] if meta.get('lift_scene') else ''})"
    elif not a.preview and lift_at:
        lift = (f"no lift: {why}{' ' + lift_scene if lift_scene else ''} at {lift_at:.1f} s leaves no room for intro + A + B "
                f"on the {240 / meta['bpm']:.2f} s bar grid")
    else:
        lift = f"no lift ({why})"
    print(f"{rel}: {meta['seconds']:.2f} s, {mood} seed {seed}, {meta['bpm']} bpm, {meta['key']}, {lift}, "
          f"{meta['lufs']} LUFS, peak {meta['peak']}, {meta['render_seconds']} s to render")
    print(f"sections: {secs}")
    print(json.dumps({"path": rel, **meta}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
