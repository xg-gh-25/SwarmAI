#!/usr/bin/env python3
"""Review evidence drawn from THIS draft's picture (port of Slate's film.py extract/evidence).

  review_evidence.py --project P [--video build/picture.mp4]

Writes into review/:
  sheet-S01.jpg        4x3 contact sheet per scene, 12 evenly spread frames, labelled "S01 t=1.2s (f36)"
  full-S01.jpg         one full-resolution middle frame per scene (for judging whether text is readable)
  seam-S01-S02.jpg     3 frames before and 3 after each scene boundary
  seams.jpg            all seam strips stacked into one image
  strip.jpg            film strip, the middle frame of every scene (shot variety, consistency)
  evidence.json        index of the files above
Scene boundaries come from the storyboard durations. Exit codes: 0 ok, 1 extraction failed, 2 usage/env error.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("error: Pillow is required (python3 -m pip install pillow)", file=sys.stderr)
    sys.exit(2)

SHEET_COLS, SHEET_ROWS = 4, 3
CELL_W = 480
SEAM_W, SEAM_HALF = 320, 3
STRIP_W, STRIP_COLS = 240, 8
FULL_Q, SHEET_Q = 88, 85
EXTRACT_BATCH = 60     # ffmpeg's select expression parser overflows above ~100 terms
FONTS = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
         "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Helvetica.ttc",
         "/System/Library/Fonts/HelveticaNeue.ttc", "/Library/Fonts/Arial Unicode.ttf"]


def ffmpeg():
    p = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(p):
        raise SystemExit("error: ffmpeg not found")
    return p


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def extract(video, frame_numbers, out_dir):
    """extract these frame numbers as PNG; returns {frame: file}. -fps_mode passthrough keeps only selected frames"""
    want = sorted(set(int(x) for x in frame_numbers if x >= 0))
    out = {}
    for c in range(0, len(want), EXTRACT_BATCH):
        part = want[c:c + EXTRACT_BATCH]
        expr = "+".join(f"eq(n\\,{k})" for k in part)
        pre = f"f{c // EXTRACT_BATCH:03d}_"
        r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", video, "-vf", f"select='{expr}'",
                            "-fps_mode", "passthrough", os.path.join(out_dir, pre + "%05d.png")], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"frame extraction failed: {r.stderr[-300:]}")
        files = sorted(f for f in os.listdir(out_dir) if f.startswith(pre) and f.endswith(".png"))
        out.update({k: os.path.join(out_dir, f) for k, f in zip(part, files)})
    return out


def grid(cells, cols, cell_w, label_h=24):
    """cells = [(image path, label)] -> grid image with a label above each cell"""
    ims = [Image.open(p).convert("RGB") for p, _ in cells]
    th = [im.resize((cell_w, max(1, int(im.height * cell_w / im.width))), Image.LANCZOS) for im in ims]
    h = max(t.height for t in th)
    rows = -(-len(th) // cols)
    cols = min(cols, len(th))
    out = Image.new("RGB", (cols * cell_w + 6 * (cols - 1), rows * (h + label_h) + 6 * (rows - 1)), (250, 250, 250))
    dr, fnt = ImageDraw.Draw(out), font(15)
    for i, (t, (_, label)) in enumerate(zip(th, cells)):
        x, y = (i % cols) * (cell_w + 6), (i // cols) * (h + label_h + 6)
        dr.text((x + 4, y + 4), label, fill=(20, 20, 20), font=fnt)
        out.paste(t, (x, y + label_h))
    return out


def evidence(video, scenes, fps, out_dir):
    """scenes = [(id, start_frame, n_frames)] -> dict of written files (paths relative to out_dir's parent)"""
    plan, want = {}, set()
    k = SHEET_COLS * SHEET_ROWS
    for sid, start, cnt in scenes:
        idx = sorted({start + min(cnt - 1, int((i + 0.5) * cnt / k)) for i in range(k)}) if cnt else []
        plan[sid] = {"sheet": idx, "full": start + cnt // 2}
        want.update(idx)
        want.add(start + cnt // 2)
    bounds = [(scenes[i - 1][0], scenes[i][0], scenes[i][1]) for i in range(1, len(scenes))]
    for _, _, b in bounds:
        want.update(range(max(0, b - SEAM_HALF), b + SEAM_HALF))
    os.makedirs(out_dir, exist_ok=True)
    out = {"sheets": {}, "fulls": {}, "seams": {}, "seams_all": None, "strip": None, "missing_frames": []}
    with tempfile.TemporaryDirectory(prefix="mv-frames-") as tmp:
        got = extract(video, want, tmp)
        out["missing_frames"] = sorted(set(want) - set(got))
        for sid, start, cnt in scenes:
            cells = [(got[f], f"{sid}  t={(f - start) / fps:.1f}s  (f{f - start})") for f in plan[sid]["sheet"] if f in got]
            if cells:
                p = os.path.join(out_dir, f"sheet-{sid}.jpg")
                grid(cells, SHEET_COLS, CELL_W).save(p, quality=SHEET_Q)
                out["sheets"][sid] = p
            fk = plan[sid]["full"]
            if fk in got:
                p = os.path.join(out_dir, f"full-{sid}.jpg")
                Image.open(got[fk]).convert("RGB").save(p, quality=FULL_Q)
                out["fulls"][sid] = p
        for prev, cur, b in bounds:
            cells = [(got[f], f"{prev if f < b else cur}  f{f - b:+d}") for f in range(max(0, b - SEAM_HALF), b + SEAM_HALF) if f in got]
            if cells:
                p = os.path.join(out_dir, f"seam-{prev}-{cur}.jpg")
                grid(cells, len(cells), SEAM_W).save(p, quality=SHEET_Q)
                out["seams"][f"{prev}-{cur}"] = p
        if out["seams"]:
            ims = [Image.open(p).convert("RGB") for _, p in sorted(out["seams"].items())]
            big = Image.new("RGB", (max(i.width for i in ims), sum(i.height for i in ims) + 6 * (len(ims) - 1)), (250, 250, 250))
            y = 0
            for im in ims:
                big.paste(im, (0, y))
                y += im.height + 6
            p = os.path.join(out_dir, "seams.jpg")
            big.save(p, quality=SHEET_Q)
            out["seams_all"] = p
        strip = [(got[plan[sid]["full"]], sid) for sid, _, _ in scenes if plan[sid]["full"] in got]
        if strip:
            p = os.path.join(out_dir, "strip.jpg")
            grid(strip, STRIP_COLS, STRIP_W).save(p, quality=SHEET_Q)
            out["strip"] = p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Contact sheets, full frames, seam strips and a film strip from the draft picture.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--video", help="video to sample (default build/picture.mp4)")
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
    review = os.path.join(proj, "review")
    try:
        ev = evidence(video, scenes, fps, review)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rel = lambda p: os.path.relpath(p, proj) if isinstance(p, str) else p  # noqa: E731
    index = {"video": rel(video), "fps": fps, "scenes": {sid: {"start": st, "frames": n} for sid, st, n in scenes},
             "sheets": {k: rel(v) for k, v in ev["sheets"].items()}, "fulls": {k: rel(v) for k, v in ev["fulls"].items()},
             "seams": {k: rel(v) for k, v in ev["seams"].items()}, "seams_all": rel(ev["seams_all"]), "strip": rel(ev["strip"]),
             "missing_frames": ev["missing_frames"]}
    json.dump(index, open(os.path.join(review, "evidence.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_files = len(ev["sheets"]) + len(ev["fulls"]) + len(ev["seams"]) + bool(ev["seams_all"]) + bool(ev["strip"])
    print(f"wrote {n_files} images to review/ (sheet-*, full-*, seam-*, seams.jpg, strip.jpg) and review/evidence.json")
    if ev["missing_frames"]:
        print(f"warning: {len(ev['missing_frames'])} frame(s) beyond the end of the video: the picture is shorter than the storyboard")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
