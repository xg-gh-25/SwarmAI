"""Style font stacks resolve on every OS: macOS families first, then a bundled open-licence face of the same role.

Static checks of references/styles/*.css against references/styles/fonts/ (FONTS.md manifest, licences, sha256).
Run: python3 -m pytest tests/test_fonts.py -q   (from the skill directory)"""
import hashlib
import os
import re

import pytest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLES = os.path.join(SKILL, "references", "styles")
FONTS = os.path.join(STYLES, "fonts")
CSS = sorted(f for f in os.listdir(STYLES) if f.endswith(".css"))

GENERIC = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"}
# macOS CJK families by role, and the bundled face that stands in for each role
MAC_CJK = {
    "sans": {"PingFang SC", "Hiragino Sans GB", "Hiragino Sans", "Hiragino Kaku Gothic ProN"},
    "serif": {"Songti SC", "STSong"},
    "kai": {"Kaiti SC", "STKaiti", "Kai", "BiauKai", "Xingkai SC", "Hannotate SC", "HanziPen SC"},
}
BUNDLED_CJK = {"MV Noto Sans SC": "sans", "MV Noto Serif SC": "serif", "MV LXGW WenKai": "kai"}


def stacks():
    """(file, variable, [families]) for every font-stack custom property in the style CSS."""
    out = []
    for f in CSS:
        text = open(os.path.join(STYLES, f), encoding="utf-8").read()
        for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);", text):
            fams = [x.strip().strip('"') for x in m.group(2).split(",")]
            if fams and fams[-1] in GENERIC:
                out.append((f, m.group(1), fams))
    return out


def manifest():
    rows = {}
    for line in open(os.path.join(FONTS, "FONTS.md"), encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[1].endswith(".woff2"):
            rows[cells[0]] = {"file": cells[1], "weights": cells[2], "sha256": cells[5], "licence": cells[6]}
    return rows


def test_every_stack_reaches_a_bundled_family():
    found = stacks()
    assert len(found) >= 30   # every style declares its stacks as custom properties
    bad = []
    for f, var, fams in found:
        bundled = [x for x in fams if x.startswith("MV ")]
        if not bundled:
            bad.append(f"{f} {var}: no bundled family, Windows/Linux would fall back to whatever is installed")
            continue
        mac_cjk = [x for x in fams if any(x in s for s in MAC_CJK.values())]
        cjk = [x for x in fams if x in BUNDLED_CJK]
        if mac_cjk:
            role = next(r for r, s in MAC_CJK.items() if mac_cjk[0] in s)
            if not cjk:
                bad.append(f"{f} {var}: CJK stack without a bundled CJK face")
            elif BUNDLED_CJK[cjk[0]] != role:
                bad.append(f"{f} {var}: first bundled CJK face {cjk[0]} does not match the {role} role of {mac_cjk[0]}")
            elif fams.index(cjk[0]) < max(fams.index(x) for x in mac_cjk if x in MAC_CJK[role]):
                bad.append(f"{f} {var}: {cjk[0]} comes before a macOS {role} family, so it would replace it on macOS")
        assert fams[0] not in BUNDLED_CJK and fams[0].startswith("MV ") is False, f"{f} {var}: macOS families come first"
    assert not bad, "\n".join(bad)


def test_font_faces_point_at_shipped_files():
    rows = manifest()
    for f in CSS:
        text = open(os.path.join(STYLES, f), encoding="utf-8").read()
        faces = dict(re.findall(r'@font-face\s*\{\s*font-family:\s*"([^"]+)";\s*src:\s*url\("([^"]+)"\)', text))
        used = {x for _, var, fams in stacks() if _ == f for x in fams if x.startswith("MV ")}
        assert used <= set(faces), f"{f}: stacks use {sorted(used - set(faces))} without an @font-face"
        for fam, url in faces.items():
            assert fam in rows, f"{f}: {fam} is not in fonts/FONTS.md"
            assert url == "fonts/" + rows[fam]["file"], f"{f}: {fam} -> {url}"
        for url in re.findall(r"url\(([^)]*)\)", re.sub(r"(?s)/\*.*?\*/", "", text)):
            u = url.strip("\"' ")
            if u.startswith("data:"):
                continue   # inline procedural textures
            assert "://" not in u and not u.startswith("//"), f"{f}: remote url({u})"
            if re.search(r"\.(woff2?|ttf|otf)$", u, re.I):
                assert u.startswith("fonts/"), f"{f}: font url({u}) outside the bundled fonts/"


def test_bundled_fonts_match_manifest_and_have_licences():
    rows = manifest()
    assert set(rows) == {"MV Noto Sans SC", "MV Noto Serif SC", "MV LXGW WenKai", "MV Noto Sans Mono", "MV Patrick Hand",
                         "MV Roboto Condensed", "MV Jost", "MV Nunito", "MV Great Vibes"}
    shipped = {f for f in os.listdir(FONTS) if f.endswith((".woff2", ".woff", ".ttf", ".otf"))}
    assert shipped == {r["file"] for r in rows.values()}, "every font file is listed in FONTS.md, and only those"
    for fam, r in rows.items():
        data = open(os.path.join(FONTS, r["file"]), "rb").read()
        assert data[:4] == b"wOF2", r["file"]
        assert hashlib.sha256(data).hexdigest() == r["sha256"], f"{r['file']} differs from FONTS.md"
        lic = open(os.path.join(FONTS, r["licence"]), encoding="utf-8").read()
        assert "SIL OPEN FONT LICENSE VERSION 1.1" in " ".join(lic.upper().split()) and "Copyright" in lic, r["licence"]


@pytest.mark.parametrize("md", [f for f in os.listdir(STYLES) if f.endswith(".md")])
def test_style_docs_quote_no_old_macos_only_stack(md):
    text = open(os.path.join(STYLES, md), encoding="utf-8").read()
    assert "macOS system fonts only" not in text and "(system fonts only" not in text
    for line in text.splitlines():
        if "PingFang SC" in line and "sans-serif`" in line and '"MV ' not in line:
            pytest.fail(f"{md}: a quoted CJK stack without its bundled face: {line[:120]}")
