#!/usr/bin/env python3
"""Frame-level defects of the rendered picture (port of Slate's flash.py, same thresholds).

  defects.py --project P [--video build/picture.mp4]   -> review/defects.json

Decodes the video once to 192x108 grayscale and reports, per scene (scene boundaries from storyboard durations):
  flashes      a frame that differs from both neighbours while the neighbours match each other   -> FAIL (exit 1)
  jumps        a region that snaps into place in one frame (no in-between frames); whole-frame
               changes (> 50 % of blocks) are hard cuts and do not count; >= 4 in a scene -> note
  still        the whole frame almost unchanged for >= 2.5 s (rule: never fully still > 2 s)      -> note
  motion       mean frame difference; curve = 12-bin motion curve; seam = 3 diffs either side of each scene start
When storyboard captions are on, the caption band (bottom 16 %) is masked for jumps / still, because
caption lines swap in one frame by design. Flashes are measured on the full frame.
Move length (every move >= 0.25 s) is not measured here: an eased move spends most of its time in a tail too small
to see in a pixel diff, and a shot-long camera push keeps every frame moving. check.py reads it exactly from the
registered timeline instead.
Exit codes: 0 no flash, 1 flash found, 2 usage/env error.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

try:
    import numpy as np
except ImportError:
    print("error: numpy is required (python3 -m pip install numpy)", file=sys.stderr)
    sys.exit(2)

W, H = 192, 108
BW, BH = 24, 18
JUMP_DIFF, JUMP_SHARE, CUT_FRACTION, STILL_DIFF, STILL_S = 6.0, 0.55, 0.5, 0.08, 2.5
JUMPS_MAX = 4              # jumps per scene before it becomes a note
MIN_MOVE_S = 0.25          # every move lasts at least this long
CURVE_BINS = 12
CAPTION_BAND = 0.16


def ffmpeg():
    p = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(p):
        raise SystemExit("error: ffmpeg not found")
    return p


def frames(path):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", path, "-vf", f"scale={W}:{H}:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"cannot decode {path}: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    raw = r.stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], np.uint8).reshape(n, H, W).astype(np.int16)


def mean_diff(f):
    return np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)) if len(f) > 1 else np.zeros(0)


def flashes(f, fps):
    d = lambda i, j: float(np.abs(f[i] - f[j]).mean())  # noqa: E731
    out = []
    for i in range(1, len(f) - 1):
        a, b = d(i, i - 1), d(i + 1, i)
        if min(a, b) > 3 and d(i + 1, i - 1) < min(a, b) / 3:
            out.append({"frame": i, "t": round(i / fps, 2), "in": round(a, 1), "out": round(b, 1)})
    return out


def jumps(f, fps):
    if len(f) < 5:
        return []
    diff = np.abs(np.diff(f, axis=0)).astype(np.float32)
    rows, cols = H // BH, W // BW
    db = diff.reshape(len(diff), rows, BH, cols, BW).mean(axis=(2, 4))
    pad = np.pad(db, ((0, 0), (1, 1), (1, 1)))
    nb = sum(pad[:, 1 + dy:1 + dy + rows, 1 + dx:1 + dx + cols] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    out = []
    for k in range(2, len(db) - 2):
        hot = (db[k] > JUMP_DIFF) & (nb[k] > JUMP_SHARE * nb[k - 2:k + 3].sum(axis=0))
        n = int(hot.sum())
        if n and n < rows * cols * CUT_FRACTION:
            out.append({"frame": k + 1, "t": round((k + 1) / fps, 2), "blocks": n})
    return out


def still(f, fps):
    d = mean_diff(f)
    out, s = [], None
    for i, x in enumerate(list(d) + [1.0]):
        if x < STILL_DIFF:
            s = i if s is None else s
        elif s is not None:
            if (i - s + 1) / fps >= STILL_S:
                out.append({"from": round(s / fps, 2), "to": round((i + 1) / fps, 2)})
            s = None
    return out



def curve(f, bins=CURVE_BINS):
    if len(f) < 2:
        return [0.0] * bins
    d = mean_diff(f)
    parts = np.array_split(d, min(bins, len(d)))
    out = [round(float(p.mean()), 3) for p in parts]
    return out + [out[-1]] * (bins - len(out))



def analyze(path, fps, scenes, mask_captions=False):
    """scenes = [(id, start_frame, n_frames)]. Times in the per-scene results are seconds from the scene start."""
    full = frames(path)
    n = len(full)
    f = full.copy()
    if mask_captions:
        f[:, int(round(H * (1 - CAPTION_BAND))):, :] = 0
    d_full = mean_diff(full)
    film = {"frames": int(n), "fps": fps, "flashes": flashes(full, fps), "jumps": jumps(f, fps), "still": still(f, fps),
            "motion": round(float(d_full.mean()), 3) if len(d_full) else 0.0,
            "curve": curve(full)}
    out = {}
    for sid, start, cnt in scenes:
        a, b = max(0, start), min(n, start + cnt)
        if b <= a:
            out[sid] = {"flashes": [], "jumps": [], "still": [], "motion": 0.0,
                        "curve": [0.0] * CURVE_BINS, "seam": None, "notes": ["scene missing from the video"]}
            continue
        sub = f[a:b]
        dt = a / fps
        loc = lambda x: {**x, "frame": x["frame"] - a, "t": round(x["t"] - dt, 2)}  # noqa: E731
        sc = {
            "flashes": [loc(x) for x in film["flashes"] if a <= x["frame"] < b],
            "jumps": [loc(x) for x in film["jumps"] if a < x["frame"] < b],     # a jump AT the scene start is the cut
            "still": still(sub, fps),
            "motion": round(float(mean_diff(full[a:b]).mean()), 3) if b - a > 1 else 0.0,
            "curve": curve(full[a:b]),
            "seam": [round(float(x), 2) for x in d_full[max(0, a - 3):a + 3]] if a > 0 else None,
        }
        notes = []
        if sc["flashes"]:
            notes.append("single-frame flash at t=" + ", ".join(str(x["t"]) for x in sc["flashes"]) +
                         ": check whether an element disappears or jumps at those frames")
        if len(sc["jumps"]) >= JUMPS_MAX:
            notes.append(f"{len(sc['jumps'])} one-frame jumps: every move lasts at least {MIN_MOVE_S} s "
                         f"({int(round(MIN_MOVE_S * fps))} frames at {fps:g} fps), with easing")
        if sc["still"]:
            notes.append("frozen " + ", ".join(f"{x['from']}-{x['to']}s" for x in sc["still"]) +
                         ": keep something moving (slow push, progress line, counter, breathing drift)")
        sc["notes"] = notes
        out[sid] = sc
    return {"film": film, "scenes": out}


def probe_fps(path):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"([\d.]+) fps", r.stderr)
    return float(m.group(1)) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Detect flashes, jumps and frozen stretches in the rendered picture.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--video", help="video to analyse (default build/picture.mp4)")
    a = ap.parse_args(argv)
    proj = os.path.abspath(a.project)
    sbp = os.path.join(proj, "storyboard.json")
    if not os.path.isfile(sbp):
        print(f"error: no storyboard.json in {proj}", file=sys.stderr)
        return 2
    try:
        sb = json.load(open(sbp, encoding="utf-8"))
        fps = float(sb["fps"])
        scenes, acc = [], 0
        for s in sb["scenes"]:
            if not isinstance(s.get("duration"), (int, float)):
                raise ValueError(f"{s.get('id')}: duration not set")
            cnt = max(1, int(round(float(s["duration"]) * fps)))
            scenes.append((s["id"], acc, cnt))
            acc += cnt
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        print(f"error: storyboard.json: {e}", file=sys.stderr)
        return 2
    video = os.path.abspath(a.video) if a.video else os.path.join(proj, "build", "picture.mp4")
    if not os.path.isfile(video):
        print(f"error: {video} not found (run render.py --join)", file=sys.stderr)
        return 2
    vf = probe_fps(video)
    if vf and abs(vf - fps) > 0.01:
        print(f"warning: video is {vf} fps, storyboard says {fps}", file=sys.stderr)
    try:
        res = analyze(video, fps, scenes, mask_captions=sb.get("captions", True) is not False)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    film = res["film"]
    if film["frames"] != acc:
        film["frame_mismatch"] = f"video has {film['frames']} frames, storyboard {acc}"
    res["video"] = os.path.relpath(video, proj) if video.startswith(proj) else video
    res["ok"] = not film["flashes"]
    os.makedirs(os.path.join(proj, "review"), exist_ok=True)
    out = os.path.join(proj, "review", "defects.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{res['video']}: {film['frames']} frames, motion {film['motion']}, flashes {len(film['flashes'])}"
          + (f", {film['frame_mismatch']}" if "frame_mismatch" in film else ""))
    for sid, sc in res["scenes"].items():
        print(f"  {sid}: motion {sc['motion']}, jumps {len(sc['jumps'])}, "
              f"still {len(sc['still'])}, flashes {len(sc['flashes'])}")
        for note in sc["notes"]:
            print(f"    note: {note}")
    print(f"wrote {os.path.relpath(out, proj)}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
