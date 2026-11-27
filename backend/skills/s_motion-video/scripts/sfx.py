#!/usr/bin/env python3
"""sfx.py: synthesize the short sound effects the storyboard uses (numpy, royalty-free).

  sfx.py --project <dir>          -> audio/sfx/<name>.wav for every name in scenes[].sfx
  sfx.py --project <dir> --all    -> all six: whoosh pop click ding thud rise

Recipes:
  whoosh  band-passed noise swelling in and out (transitions, things sliding in/out)
  pop     fast falling pitch, 900 -> 260 Hz (an element pops in)
  click   very short high-passed noise burst (select, toggle)
  ding    1568 Hz bell with 2.76x / 5.4x partials (key point, a number lands)
  thud    low falling sine + noise (landing, impact, stamp)
  rise    300 -> 1400 Hz triangle sweep (reveal, result unfolds)
Each file: 48 kHz stereo 16-bit, peak 0.8. mix.py places it at scene start + "at", at 0.22 of voice level (max 0.35).
A storyboard name that is not a recipe is accepted if audio/sfx/<name>.wav already exists (your own sound).

Budget (contract): at most 2 per scene and 8 per film; exceeding it is reported and exits 1.
Exit codes: 0 ok, 1 check failed (unknown name, budget exceeded), 2 usage error.
"""
import argparse
import json
import os
import sys
import wave

import numpy as np

SR = 48000
PER_SCENE, PER_FILM = 2, 8


def env(n, a, r):
    e = np.ones(n)
    ai, ri = int(a * SR), int(r * SR)
    e[:ai] = np.linspace(0, 1, ai) if ai else 1
    if ri:
        e[-ri:] *= np.linspace(1, 0, ri)
    return e


def lowpass(x, k=8):
    return np.convolve(x, np.ones(k) / k, mode="same")


def _noise(dur, seed=1):
    return np.random.default_rng(seed).standard_normal(int(dur * SR))


def _sweep(f0, f1, dur, shape="sine"):
    t = np.arange(int(dur * SR)) / SR
    ph = 2 * np.pi * np.cumsum(np.geomspace(f0, f1, len(t))) / SR
    return np.sin(ph) if shape == "sine" else 2 / np.pi * np.arcsin(np.sin(ph))


# moving-average widths were tuned at 44.1 kHz; scale them so the colour is the same at 48 kHz
def _k(k):
    return max(1, int(round(k * SR / 44100)))


def whoosh():
    n = _noise(0.6, 3)
    body = lowpass(n, _k(3)) - lowpass(n, _k(24))
    return body * np.sin(np.linspace(0, np.pi, len(body))) ** 1.6


def pop():
    return _sweep(900, 260, 0.12) * np.exp(-np.arange(int(0.12 * SR)) / SR * 32)


def click():
    n = _noise(0.05, 5)
    return (n - lowpass(n, _k(6))) * np.exp(-np.arange(len(n)) / SR * 220)


def ding():
    t = np.arange(int(0.9 * SR)) / SR
    return (np.sin(2 * np.pi * 1568 * t) + 0.45 * np.sin(2 * np.pi * 1568 * 2.76 * t)
            + 0.25 * np.sin(2 * np.pi * 1568 * 5.4 * t)) * np.exp(-t * 6)


def thud():
    t = np.arange(int(0.35 * SR)) / SR
    return (np.sin(2 * np.pi * (70 * np.exp(-t * 6) + 40) * t) * np.exp(-t * 14)
            + 0.15 * lowpass(_noise(0.35, 9), _k(40)) * np.exp(-t * 40))


def rise():
    dur = 0.8
    return lowpass(_sweep(300, 1400, dur, "tri") * env(int(dur * SR), 0.05, 0.25), _k(4))


RECIPES = {"whoosh": whoosh, "pop": pop, "click": click, "ding": ding, "thud": thud, "rise": rise}


def render(name):
    sig = RECIPES[name]()
    sig = sig / (np.max(np.abs(sig)) + 1e-9) * 0.8
    fade = min(len(sig), int(0.003 * SR))   # 3 ms tail fade: no click at the cut
    sig[-fade:] *= np.linspace(1, 0, fade)
    return sig


def write_wav(path, sig):
    st = np.stack([sig, sig], axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(st, -1, 1) * 32767).astype("<i2").tobytes())


def used_names(scenes):
    """-> (ordered unique names, budget problems)"""
    names, problems, total = [], [], 0
    for s in scenes:
        items = s.get("sfx") or []
        total += len(items)
        if len(items) > PER_SCENE:
            problems.append(f"{s.get('id')}: {len(items)} sfx (max {PER_SCENE} per scene)")
        for it in items:
            n = str((it.get("name") if isinstance(it, dict) else it) or "").strip()
            if n and n not in names:
                names.append(n)
    if total > PER_FILM:
        problems.append(f"film: {total} sfx (max {PER_FILM} per film)")
    return names, problems


def main():
    ap = argparse.ArgumentParser(description="Synthesize the sound effects the storyboard uses into audio/sfx/<name>.wav.")
    ap.add_argument("--project", required=True, help="project directory (contains storyboard.json)")
    ap.add_argument("--all", action="store_true", help="write all six recipes")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    sb_path = os.path.join(proj, "storyboard.json")
    if not os.path.isfile(sb_path):
        print(f"error: no storyboard.json in {proj}", file=sys.stderr)
        return 2
    with open(sb_path, encoding="utf-8") as f:
        sb = json.load(f)
    names, problems = used_names(sb.get("scenes") or [])
    out_dir = os.path.join(proj, "audio", "sfx")
    os.makedirs(out_dir, exist_ok=True)
    todo = list(RECIPES) if a.all else names
    for n in todo:
        p = os.path.join(out_dir, f"{n}.wav")
        if n in RECIPES:
            sig = render(n)
            write_wav(p, sig)
            print(f"audio/sfx/{n}.wav: {len(sig) / SR:.2f} s")
        elif os.path.isfile(p):
            print(f"audio/sfx/{n}.wav: custom file, kept")
        else:
            problems.append(f"unknown sfx {n!r}: not a recipe ({', '.join(RECIPES)}) and no audio/sfx/{n}.wav")
    if not todo:
        print("storyboard uses no sfx (use --all to write every recipe)")
    for p in problems:
        print(f"check failed: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
