#!/usr/bin/env python3
"""Reliable Latin-font extractor + CDN verifier + design.md <link> backfiller.

Extracts families ONLY from `fontFamily: "..."` / `font-family: '...'` declarations
(a complete quoted stack), NOT prose. Drops CJK / generic / intentional-system fonts.
Maps each Latin family to a Google-Fonts family+weights spec and rewrites each
design.md's googleapis css2 <link> to include the missing Latin families.

Usage: font_backfill.py [--apply]   (default: dry-run)
"""
import re, glob, sys, os, urllib.request

import os
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "html-deck", "systems")
APPLY = "--apply" in sys.argv

CJK = {"noto sans sc", "noto serif sc", "lxgw wenkai tc", "noto sans mono cjk sc",
       "noto sans jp", "smiley sans oblique"}  # CJK/JP handled by existing CJK link or alias
GENERIC = {"sans-serif", "serif", "monospace", "system-ui", "ui-monospace", "cursive",
           "inherit", "initial", "ui-serif", "ui-sans-serif"}
SYSTEM = {"ms sans serif", "geneva", "helvetica neue", "-apple-system", "arial",
          "helvetica", "times", "times new roman", "courier", "courier new",
          "georgia", "tahoma", "verdana", "blinkmacsystemfont", "segoe ui",
          "menlo", "monaco", "consolas", "sf mono", "sf pro"}

# Curated Google-Fonts weight specs (the weights each system actually uses; superset-safe).
GF_SPEC = {
    "Barlow": "Barlow:wght@400;500;700;900",
    "IBM Plex Mono": "IBM+Plex+Mono:wght@400;500",
    "Chakra Petch": "Chakra+Petch:wght@400;500;700",
    "Space Mono": "Space+Mono:wght@400;700",
    "Tektur": "Tektur:wght@400;700;900",
    "Archivo": "Archivo:wght@400;500;700;900",
    "Instrument Serif": "Instrument+Serif:ital@0;1",
    "JetBrains Mono": "JetBrains+Mono:wght@400;500;700",
    "Inter": "Inter:wght@300;400;500;600;700",
    "Space Grotesk": "Space+Grotesk:wght@300;400;500;600;700",
    "Libre Baskerville": "Libre+Baskerville:ital,wght@0,400;0,700;1,400",
    "Shrikhand": "Shrikhand",
    "Bricolage Grotesque": "Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700;12..96,800",
    "Bodoni Moda": "Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,700;1,6..96,400",
    "Manrope": "Manrope:wght@400;500;700;800",
    "Source Serif 4": "Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400",
    "Source Serif Pro": "Source+Serif+Pro:ital,wght@0,400;0,600;0,700;1,400",
    "Playfair Display": "Playfair+Display:ital,wght@0,400;0,700;0,900;1,400",
    "Jost": "Jost:wght@300;400;500;600",
    "Fraunces": "Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,400",
    "DM Mono": "DM+Mono:wght@400;500",
    "DM Sans": "DM+Sans:wght@400;500;700",
    "Bebas Neue": "Bebas+Neue",
    "Caveat": "Caveat:wght@400;500;700",
    "Albert Sans": "Albert+Sans:wght@400;500;700;900",
    "Big Shoulders Display": "Big+Shoulders+Display:wght@400;700;900",
    "Zilla Slab": "Zilla+Slab:wght@400;500;700",
    "Press Start 2P": "Press+Start+2P",
    "VT323": "VT323",
    "Cormorant Garamond": "Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500",
    "Garamond": None,  # 'Garamond' is a system/generic serif alias — no reliable GF; treat as system
}

def extract_families(txt):
    """Latin families named ONLY inside real fontFamily/font-family quoted stacks.

    Handles nested quotes ("'Tektur', cursive") by stripping inner quotes per token,
    and skips unresolved template placeholders ({typography.*.fontFamily})."""
    fams = []
    for m in re.finditer(r'(?:font-family|fontFamily)\s*:\s*"([^"\n]+)"', txt):
        stack = m.group(1)
        for part in stack.split(','):
            f = part.strip().strip("'\"").strip()   # strip nested single/double quotes
            fl = f.lower()
            if not f or fl in GENERIC or fl in CJK or fl in SYSTEM:
                continue
            if "{" in f or "}" in f:                  # unresolved template token — skip
                continue
            if f not in fams:
                fams.append(f)
    return fams

def linked_families(txt):
    linked = set()
    for lk in re.findall(r'family=([^&"]+)', txt):
        linked.add(lk.split(':')[0].replace('+', ' ').strip().lower())
    return linked

def verify_cdn(spec):
    url = f"https://fonts.googleapis.com/css2?family={spec}&display=swap"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False

def main():
    os.chdir(ROOT)
    all_needed = {}          # sys -> [families to add]
    unknown = set()          # families with no GF_SPEC mapping
    system_skipped = set()
    for path in sorted(glob.glob("*/design.md")):
        sysname = path.split('/')[0]
        txt = open(path, encoding='utf-8').read()
        fams = extract_families(txt)
        linked = linked_families(txt)
        missing = [f for f in fams if f.lower() not in linked]
        real = []
        for f in missing:
            if f not in GF_SPEC:
                unknown.add(f); continue
            if GF_SPEC[f] is None:
                system_skipped.add(f); continue
            real.append(f)
        if real:
            all_needed[sysname] = real

    print(f"=== systems needing Latin backfill: {len(all_needed)}/34 ===")
    for s, fams in all_needed.items():
        print(f"  {s}: {', '.join(fams)}")
    if unknown:
        print(f"\n⚠️ UNMAPPED families (need GF_SPEC entry or classify as system): {sorted(unknown)}")
    if system_skipped:
        print(f"\nℹ️ treated as system/generic (no CDN): {sorted(system_skipped)}")

    # verify every spec we'd add
    specs_used = {f for fams in all_needed.values() for f in fams}
    print(f"\n=== CDN verify {len(specs_used)} distinct families (fail-loud) ===")
    bad = []
    for f in sorted(specs_used):
        ok = verify_cdn(GF_SPEC[f])
        print(f"  {'OK ' if ok else 'FAIL'} {f}")
        if not ok:
            bad.append(f)
    if bad:
        print(f"\n❌ ABORT: {len(bad)} families not 200 on CDN: {bad}")
        sys.exit(1)
    if unknown:
        print(f"\n❌ ABORT: unmapped families present — resolve before apply.")
        sys.exit(1)

    if not APPLY:
        print("\n(dry-run — re-run with --apply to rewrite links)")
        return

    # APPLY: rewrite each design.md's css2 link to include missing Latin families
    changed = 0
    for sysname, fams in all_needed.items():
        path = f"{sysname}/design.md"
        txt = open(path, encoding='utf-8').read()
        add = "&".join(f"family={GF_SPEC[f]}" for f in fams)
        m = re.search(r'(https://fonts\.googleapis\.com/css2\?family=)([^"\s]+)', txt)
        if m:
            # append Latin families to the existing googleapis css2 link
            core = m.group(2).replace("&display=swap", "")
            new = f"{core}&{add}&display=swap"
            txt2 = txt.replace(m.group(0), m.group(1) + new)
        else:
            # no googleapis link (CJK served via a different CDN) — inject a NEW
            # googleapis css2 <link> for the Latin families next to the first
            # existing <link> in the file.
            new_link = (f'<link href="https://fonts.googleapis.com/css2?{add}'
                        f'&display=swap" rel="stylesheet">')
            lm = re.search(r'(<link href="https?://[^"]+" rel="stylesheet">)', txt)
            if not lm:
                print(f"  ⚠️ {sysname}: no <link> anchor found — skip (manual)"); continue
            txt2 = txt.replace(lm.group(1), lm.group(1) + "\n" + new_link, 1)
        open(path, 'w', encoding='utf-8').write(txt2)
        changed += 1
    print(f"\n✅ applied: {changed} design.md links backfilled")

if __name__ == "__main__":
    main()
