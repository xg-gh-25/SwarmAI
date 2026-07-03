"""Tests for the html-deck style-selection gallery (run_4e1ed63e).

WS1 foundation:
  - recommend_systems.py: metadata-driven ranking of the 34 design systems against
    a DISCOVER profile — mutation-proven (different tone → different top set),
    deterministic pagination (disjoint 'next batch', reproducible).
  - render_style_thumbnails.py: role-derivation + idempotence guard (input-hash,
    not byte-pixel, since headless font rendering isn't byte-deterministic).

Methodology: drives the REAL recommend_systems.score/load_cards against the REAL
34 preview cards on disk. No mocks.
"""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import recommend_systems as rec  # noqa: E402
import render_style_thumbnails as rt  # noqa: E402


# ---------- recommender: data + ranking ----------

def test_all_34_cards_load_with_metadata():
    cards = rec.load_cards()
    assert len(cards) == 34, f"expected 34 cards, got {len(cards)}"
    for c in cards:
        assert c["slug"], "card missing slug"
        assert c["formality"], f"{c['slug']} missing formality"
        assert c["scheme"] in ("dark", "light", "mixed"), f"{c['slug']} bad scheme {c['scheme']}"
        assert c["mood"] or c["tone"], f"{c['slug']} has no mood/tone"


def test_leadership_serious_ranks_professional_top():
    """A leadership+serious profile must surface high-formality professional systems
    (blue-professional / vellum / cobalt-grid class), NOT playful ones."""
    cards = rec.load_cards()
    ranked = sorted(
        ((rec.score(c, "leadership", "alignment", "meeting", "serious")[0], c["slug"], c["formality"])
         for c in cards),
        key=lambda x: (-x[0], x[1]),
    )
    top3 = [slug for _, slug, _ in ranked[:3]]
    top3_formality = [f for _, _, f in ranked[:3]]
    # every top-3 pick is medium-high or high formality (serious leadership fit)
    assert all(f in ("high", "medium-high") for f in top3_formality), \
        f"leadership+serious surfaced a low-formality system: {list(zip(top3, top3_formality))}"
    # a playful low-formality system must NOT be in the top 3
    assert "scatterbrain" not in top3 and "daisy-days" not in top3, \
        f"playful system wrongly ranked for leadership+serious: {top3}"


def test_MUTATION_tone_changes_top_set():
    """RED-line: the ranking must actually DEPEND on the profile. Same audience,
    only tone flips serious→playful → the top set MUST change. If it doesn't, the
    scorer is vacuous (ignores its inputs)."""
    cards = rec.load_cards()
    def top3(tone):
        r = sorted(((rec.score(c, "team", None, None, tone)[0], c["slug"]) for c in cards),
                   key=lambda x: (-x[0], x[1]))
        return [s for _, s in r[:3]]
    serious, playful = top3("serious"), top3("playful")
    assert serious != playful, \
        f"tone had NO effect on ranking (vacuous scorer): both={serious}"


def test_pagination_deterministic_and_disjoint():
    """'next batch' (offset) must return disjoint, gap-free, reproducible pages."""
    cards = rec.load_cards()
    ranked = sorted(((rec.score(c, "leadership", None, "meeting", "serious")[0], c["slug"]) for c in cards),
                    key=lambda x: (-x[0], x[1]))
    order = [s for _, s in ranked]
    p0, p3, p6 = order[0:3], order[3:6], order[6:9]
    combined = p0 + p3 + p6
    assert len(combined) == len(set(combined)), f"pages overlap: {combined}"
    # reproducible: recompute, same order
    ranked2 = sorted(((rec.score(c, "leadership", None, "meeting", "serious")[0], c["slug"]) for c in cards),
                     key=lambda x: (-x[0], x[1]))
    assert [s for _, s in ranked2] == order, "ranking not reproducible across runs"


def test_thumbnail_url_is_absolute_raw_endpoint():
    """CRITICAL render contract (Gate-2): chat ContentBlockRenderer passes NO basePath,
    so a workspace-RELATIVE markdown image does NOT resolve and shows broken. The
    gallery MUST emit an ABSOLUTE http:// URL to /api/workspace/file/raw, which
    MarkdownRenderer.resolveImageSrc passes through unchanged. Assert the recommender
    emits that absolute form, not the bare relative path."""
    import json as _json, subprocess
    out = subprocess.run(
        ["python3", str(SCRIPTS / "recommend_systems.py"),
         "--audience", "leadership", "--tone", "serious", "--top", "3", "--json"],
        capture_output=True, text=True,
    ).stdout
    recs = _json.loads(out)["recommendations"]
    assert recs, "no recommendations returned"
    for r in recs:
        u = r["thumbnail_url"]
        assert u.startswith("http://") or u.startswith("https://"), \
            f"thumbnail_url must be absolute (chat can't resolve relative): {u}"
        assert "/api/workspace/file/raw?path=" in u, f"must hit the raw endpoint: {u}"
        assert u.endswith(".png")


# ---------- thumbnail role derivation ----------

def test_roles_dark_scheme_picks_dark_bg():
    bg, text, accent = rt.roles(["#0A0E27", "#FFFFFF", "#5EDCF4"], "dark", "some-dark-sys")
    assert rt.lum(bg) < rt.lum(text), "dark scheme bg must be darker than text"


def test_roles_light_scheme_picks_light_bg():
    bg, text, accent = rt.roles(["#F5F0E6", "#2D2D2D", "#7ECDC0"], "light", "some-light-sys")
    assert rt.lum(bg) > rt.lum(text), "light scheme bg must be lighter than text"


def test_roles_text_readable_contrast():
    """The derived text color must have real contrast vs bg (>= 3) — guards the
    low-contrast-pastel bug (scatterbrain mint-on-pink) the override fixes."""
    cards = rec.load_cards()
    import re, glob
    for cf in glob.glob(str(rec.CARDS / "*.md")):
        txt = open(cf).read()
        slug = Path(cf).stem
        scheme = (re.search(r'- Scheme:\s*(\w+)', txt) or [None, "light"])[1]
        pal = re.search(r'- Palette:\s*(.+)', txt)
        colors = re.findall(r'#[0-9A-Fa-f]{6}', pal.group(1)) if pal else []
        bg, text, accent = rt.roles(colors, scheme, slug)
        assert rt.contrast(text, bg) >= 3.0, \
            f"{slug}: text {text} on bg {bg} contrast {rt.contrast(text,bg):.1f} < 3 (unreadable)"


def test_override_systems_have_readable_roles():
    """The 4 hand-overridden systems must be readable (they were the failures)."""
    for slug in ("8-bit-orbit", "scatterbrain", "daisy-days", "monochrome"):
        bg, text, accent = rt.ROLE_OVERRIDES[slug]
        assert rt.contrast(text, bg) >= 4.0, f"{slug} override low contrast"
