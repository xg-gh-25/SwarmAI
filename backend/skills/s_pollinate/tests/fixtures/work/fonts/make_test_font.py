#!/usr/bin/env python3
"""Make test-blocks.ttf: a tiny original font for the work.py tests.

Every printable ASCII letter is a plain filled box of its own width and
height, so a page drawn with this font looks clearly different from one drawn
with a fallback font. Needs fontTools (not a SwarmAI dependency; install it
somewhere temporary to run this once): make_test_font.py OUT.ttf
"""
import argparse

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def box(w, h):
    pen = TTGlyphPen(None)
    if w and h:
        pen.moveTo((50, 0))
        pen.lineTo((50, h))
        pen.lineTo((50 + w, h))
        pen.lineTo((50 + w, 0))
        pen.closePath()
    return pen.glyph()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    out = ap.parse_args().out
    chars = [chr(c) for c in range(0x20, 0x7F)]
    names = [".notdef"] + ["g%02x" % ord(c) for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c): "g%02x" % ord(c) for c in chars})
    glyphs, metrics = {".notdef": box(400, 600)}, {".notdef": (500, 50)}
    for c in chars:
        n = "g%02x" % ord(c)
        if c == " ":
            glyphs[n], metrics[n] = box(0, 0), (300, 0)
        else:
            w, h = 300 + (ord(c) * 37) % 300, 450 + (ord(c) * 53) % 300
            glyphs[n], metrics[n] = box(w, h), (w + 100, 50)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Pollinate Test Blocks", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(out)


if __name__ == "__main__":
    main()
