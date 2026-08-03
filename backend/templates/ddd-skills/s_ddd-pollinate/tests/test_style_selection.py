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
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import recommend_systems as rec  # noqa: E402
import render_style_thumbnails as rt  # noqa: E402
import format_recommend as fr  # noqa: E402


# ---------- Defect 1: PPT→web intent detection (run_a620a6ca) ----------

def test_ppt_to_web_conversion_emits_html_deck_only():
    """The natural PPT→web-deck phrasings must return html_deck ONLY — never the
    PPTX 'deck' track (user asked for a web deck, not a PowerPoint file)."""
    for msg in [
        "把这个ppt转成网页版",
        "convert this pptx to html deck",
        "我有个ppt想变成网页演示",
        "turn my slides into a web deck",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["html_deck"], f"{msg!r} → {got}, expected ['html_deck'] only"


def test_plain_ppt_still_pptx_no_html_deck_regression():
    """REGRESSION guard: a plain deck/ppt request with NO web qualifier must stay
    the PPTX 'deck' track and must NOT gain html_deck."""
    for msg in ["make a ppt", "做个演示", "build a deck", "需要一个pptx", "幻灯片"]:
        got = fr.detect_fast_path(msg)
        assert got == ["deck"], f"{msg!r} → {got}, expected ['deck'] only (no html_deck)"


def test_ppt_that_MENTIONS_web_as_topic_stays_pptx():
    """Gate-2 HIGH regression: a PPTX that merely MENTIONS web/html as its TOPIC
    (no conversion verb) must stay ['deck'] — NOT be suppressed to None/html_deck.
    The over-broad whole-message qualifier scan dropped these entirely."""
    for msg in [
        "做个ppt介绍我们的网页",          # a PPT introducing our webpage
        "做个关于网页设计的ppt",          # a PPT about web design
        "做个ppt讲讲html的历史",          # a PPT on the history of html
        "我要做个pptx，主题是网页开发",   # explicit pptx, topic = web dev
        # English "web page" as a TOPIC noun (Gate-2 CRITICAL, run_32392299):
        # bare "web page"/"web pages" nouns must NOT fire html_deck — only a
        # verb-anchored conversion phrase does.
        "a deck about our web page redesign",
        "make a deck comparing our web pages to competitors",
        "build a ppt on how to design a web page",
        # "as a web page" as a NON-conversion prepositional phrase (Gate-2 MEDIUM,
        # run_32392299): a preposition alone is not a conversion verb governing the
        # target — a deck-noun object between verb and target = topic mention.
        "a deck describing our product as a web page",
        "make a ppt that presents our app as a web page",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["deck"], (
            f"{msg!r} → {got}; a web/html TOPIC mention (no conversion verb) must "
            f"stay ['deck'] — dropping it to {got} silently loses the user's format")


def test_deck_to_web_TEAM_destination_is_not_html_deck():
    """Over-broad MEDIUM regression (run_32392299): the bare 'to web' / 'into a web'
    substrings matched a DESTINATION phrase ('send this deck to web team') and
    misfired html_deck. These are deck requests being SENT somewhere web-adjacent —
    NOT conversion-to-web-format intent. They must stay ['deck']."""
    for msg in [
        "send this deck to web team",
        "push the ppt to web team review",
        "email deck to web ops",
        "hand this deck into a web-facing group",
        # Gate-2 CRITICAL (run_32392299): a LEADING conversion verb + a SEPARATE
        # destination clause must NOT be read as conversion. The verb ('make')
        # governs the deck; 'send/email/post ... to/as a web page' is the
        # destination. _WEB_CONVERT_RE forbids a clause break in its object gap.
        "make a deck and send it to a web page",
        "make a deck and email it to a web page owner",
        "make slides and post them as a web page",
        "make a deck for the demo to a web page team",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["deck"], (
            f"{msg!r} → {got}; a destination clause after a deck request must stay "
            f"['deck'] — the leading verb does not govern the web target")


def test_clause_break_ending_in_source_noun_is_not_html_deck():
    """Gate-2 iter-4→6 CRITICAL (run_32392299): a SECOND clause that ends in a source
    noun ('...and send SLIDES to a web page') must NOT be read as one conversion
    object. The object is validated by a POSITIVE noun-phrase grammar
    (_object_is_clean_noun_phrase / _OBJECT_NP_RE): a genuine object is a single NP
    headed by a source noun (optionally with adjective + prepositional modifiers);
    a clause break has a non-source head ("a POSTER fix slides"), a punctuation
    boundary, or a connector — none match the grammar → html_deck must NOT fire, the
    real format (poster/video/deck) is preserved. A denylist of connectors/verbs was
    tried first and PROVED leaky (iter-2→5: 'while'/'with'/'fix'/'tweak'/punctuation
    kept slipping through); the positive grammar is the structural fix."""
    checks = [
        # conjunction + verb clauses (iter-4)
        ("make a poster and send slides to a web page", "poster"),
        ("make a poster and share slides as a web page", "poster"),
        ("make a video and edit slides to a web page", "video"),
        ("make a report and print slides as a web page", "deck"),
        ("convert the essay and post slides to a web page", "deck"),
        ("convert the memo and the deck to a web page", "deck"),
        # preposition + subordinator + gerund connectors (iter-5)
        ("make a poster while editing slides to a web page", "poster"),
        ("make a poster with slides to a web page", "poster"),
        ("make a poster of slides to a web page", "poster"),
        ("make a poster from slides to a web page", "poster"),
        ("make a poster showing slides to a web page", "poster"),
        ("make a report summarizing slides to a web page", "deck"),
        ("convert the memo by attaching slides to a web page", "deck"),
        ("convert the memo before showing slides to a web page", "deck"),
        # UNENUMERATED verbs (iter-6 Q1a): a denylist can't list every English verb;
        # the positive grammar rejects these because the object head ("poster") is a
        # format noun, not a source noun — "slides" can't be the head behind it.
        ("make a poster fix slides to a web page", "poster"),
        ("make a poster tweak slides to a web page", "poster"),
        ("make a poster rework slides to a web page", "poster"),
        ("make a poster port slides to a web page", "poster"),
        ("make a poster migrate slides to a web page", "poster"),
        # PUNCTUATION clause boundaries (iter-6 Q1b): a ,/;/: in the object span is a
        # clause/list boundary the grammar rejects.
        ("make a poster; reuse slides to a web page", "poster"),
        ("make a poster, revamp slides to a web page", "poster"),
        ("make a poster, slides to a web page", "poster"),
        # destination noun after target (iter-6): "to a web page TEAM" = destination.
        ("make a deck for the demo to a web page team", "deck"),
    ]
    for msg, expected in checks:
        got = fr.detect_fast_path(msg)
        assert got is not None and "html_deck" not in got, (
            f"{msg!r} → {got}; a connector-led clause ending in a source noun is a "
            f"SEPARATE clause (destination), not a conversion object — html_deck must "
            f"NOT fire")
        assert expected in got, (
            f"{msg!r} → {got}; the real requested format {expected!r} must be preserved")


def test_prepositional_object_conversion_is_html_deck():
    """Gate-2 iter-6 Q2 (run_32392299): a genuine conversion whose object is a
    source-noun head + prepositional modifier ('convert the deck OF q3 results to a
    web page', 'turn the slides FROM last week into a web page') must fire html_deck.
    Banning all prepositions (the iter-5 denylist attempt) wrongly rejected these;
    the positive NP grammar attaches the PP to the source-noun head and accepts it,
    while still rejecting a destination ('to a web page team')."""
    for msg in [
        "convert the deck of q3 results to a web page",
        "convert the deck about our roadmap to a web page",
        "convert the deck with the q3 numbers to a web page",
        "turn the slides from last week into a web page",
        "turn the slides in the appendix into a web page",
        "convert the deck on our strategy to a web page",
        "turn the deck on pricing into a web page",
        "convert the deck for marketing into a web page",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["html_deck"], (
            f"{msg!r} → {got}; a source-noun-headed object with a prepositional "
            f"modifier is a genuine conversion — must be ['html_deck']")


def test_possessive_and_powerpoint_conversion_is_html_deck():
    """Gate-2 iter-7 (run_32392299): two ordinary phrasings the grammar/source-noun
    set initially missed. (1) Possessive modifiers ("last week's deck", "the team's
    deck") — the object token must allow an apostrophe (_NP_MOD → [...']* ). (2) The
    spelled-out word "powerpoint" as a source noun — was absent from _WEB_SRC_NOUN/
    _NP_SRC, so "convert my powerpoint to a web page" returned None (total miss).
    Both are the most natural way an English speaker states this exact intent."""
    for msg in [
        "convert last week's deck to a web page",
        "turn the team's deck into a web page",
        "convert our client's slides to a web page",
        "turn john's slides into a web page",
        "convert my powerpoint to a web page",
        "turn the powerpoint into a webpage",
        "convert powerpoint to html",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["html_deck"], (
            f"{msg!r} → {got}; a possessive-modifier or 'powerpoint'-headed conversion "
            f"is genuine — must be ['html_deck']")


def test_genuine_web_conversion_still_html_deck():
    """Paired guard: genuine PPT→web conversion intent must fire html_deck. These
    are verb-anchored (a conversion verb governs a web target), matched by
    _WEB_CONVERT_RE — NOT by bare noun/prep substrings (which mis-fire, see the
    topic + destination tests). Covers both regex shapes: (A) prep-anchored
    ('as a web page') and (B) prep-less ('make this a webpage')."""
    for msg in [
        "convert this ppt to a web deck",
        "make it into a web page",
        "export these slides as a web page",
        "render the deck as web pages",
        "web version of this deck",
        "convert to web",
        # bare "html" as the conversion target (Gate-2 HIGH, run_32392299): the
        # target was previously "html <noun>" only, so "convert this ppt to html"
        # wrongly returned ['deck']. _WEB_TARGET now accepts bare "html".
        "convert this ppt to html",
        "turn the pptx into html",
        "turn my slides into html",
        # rich object (modifiers + year + hyphenated) between verb and prep (Gate-2
        # iter-3 under-match): the tight object gap must still allow adjective/noun/
        # year modifiers ending in a source noun — not just a bare determiner. These
        # are the ORDINARY ways users name a deck; missing them silently delivered a
        # PPTX. Paired with the clause-break destination test (must NOT match those).
        "convert the quarterly review deck into a web page",
        "turn the 2026 board presentation into a webpage",
        "export the final revised slides as a web page",
        "convert this sales deck to a web page",
        "render the pitch deck as a web page",
        "convert the 30-slide deck to a webpage",
        "export the q3 slides as a web page",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["html_deck"], (
            f"{msg!r} → {got}; genuine PPT→web conversion must still be ['html_deck']")


def test_webpage_one_word_conversion_is_html_deck():
    """Gate-2 gap (run_32392299): the single-word 'webpage' spelling was absent
    from the old substring list — 'make this a webpage' → None, 'host the deck as
    a webpage' → ['deck'] (actively WRONG format). The verb-anchored _WEB_CONVERT_RE
    covers 'webpage' as a _WEB_TARGET, including the prep-less 'make this a X' shape."""
    for msg in [
        "turn it into a webpage",
        "make this a webpage",
        "host the deck as a webpage",
        "convert my slides to a webpage",
    ]:
        got = fr.detect_fast_path(msg)
        assert got == ["html_deck"], (
            f"{msg!r} → {got}; single-word 'webpage' conversion must be ['html_deck'] "
            f"— not None (missed) nor ['deck'] (wrong format delivered)")


# ---------- Defect 2: tone optional, derived from audience+outcome ----------

def test_recommender_works_without_tone():
    """DISCOVER yields audience/outcome/context but NOT tone. The recommender must
    still produce a sane derived tone + ranking (not crash, not degrade to noise)."""
    assert rec.derive_tone("leadership", "alignment") == "serious"
    assert rec.derive_tone("social_followers", "awareness") == "bold"
    assert rec.derive_tone("developer_community", None) == "technical"
    # audience-only fallback + unknown → graceful
    assert rec.derive_tone("team", None) == "warm"
    assert rec.derive_tone(None, None) is None  # no signal → tone simply omitted


def test_derived_tone_ranks_like_explicit_tone():
    """A leadership+alignment profile with NO tone should rank professional systems
    top (via derived 'serious'), matching what an explicit tone would do."""
    cards = rec.load_cards()
    tone = rec.derive_tone("leadership", "alignment")
    ranked = sorted(
        ((rec.score(c, "leadership", "alignment", "meeting", tone)[0], c["slug"], c["formality"])
         for c in cards),
        key=lambda x: (-x[0], x[1]),
    )
    top3_formality = [f for _, _, f in ranked[:3]]
    assert all(f in ("high", "medium-high") for f in top3_formality), \
        f"derived-tone ranking surfaced low-formality for leadership: {ranked[:3]}"


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


def test_thumbnail_url_portable_default_and_prefix():
    """Portable render contract (DDD-native): thumbnail_url is WORKSPACE-RELATIVE by
    default (no SwarmAI daemon assumed — resolves against the workspace on any runtime),
    and becomes ABSOLUTE only when $POLLINATE_THUMB_URL_PREFIX is set (e.g. a host that
    serves workspace files over HTTP). Was previously hardcoded to SwarmAI's
    localhost:18321/api/workspace/file/raw — decoupled."""
    import json as _json, subprocess, os as _os

    def _recs(env_extra=None):
        env = dict(_os.environ)
        if env_extra:
            env.update(env_extra)
        out = subprocess.run(
            ["python3", str(SCRIPTS / "recommend_systems.py"),
             "--audience", "leadership", "--tone", "serious", "--top", "3", "--json"],
            capture_output=True, text=True, env=env,
        ).stdout
        return _json.loads(out)["recommendations"]

    # Default (no prefix env): relative, portable, ends in .png
    for r in _recs({"POLLINATE_THUMB_URL_PREFIX": ""}):
        u = r["thumbnail_url"]
        assert not u.startswith(("http://", "https://")), \
            f"default thumbnail_url must be workspace-RELATIVE (portable): {u}"
        assert u.endswith(".png")
    # With prefix set: absolutized by the recommender
    prefix = "http://localhost:18321/api/workspace/file/raw?path="
    for r in _recs({"POLLINATE_THUMB_URL_PREFIX": prefix}):
        u = r["thumbnail_url"]
        assert u.startswith(prefix), f"prefix must be applied when set: {u}"
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
