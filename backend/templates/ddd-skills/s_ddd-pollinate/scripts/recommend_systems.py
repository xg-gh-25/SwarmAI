#!/usr/bin/env python3
"""WS1b: recommend html-deck design systems for the chat style-gallery.

Ranks the 34 systems against a DISCOVER profile (audience / outcome / context /
tone) using ONLY each system's structured preview-card metadata (Mood / Tone /
Formality / Density / Scheme / Best-for / Avoid-for — all 34 cards carry every
field). Deterministic ordering so `--offset` paginates a stable "next batch"
without repeats or gaps.

Usage:
  recommend_systems.py --audience leadership --outcome alignment \\
      --context meeting --tone serious --top 3 [--offset 0] [--json]

Output (JSON): ranked list of {slug, score, formality, scheme, tagline, thumbnail,
why} — `thumbnail` is the workspace-relative PNG path the chat gallery inlines.
"""
import re, glob, os, json, argparse
from pathlib import Path

# Bundled into this skill's own data/ (decoupled from the SwarmAI-sibling s_frontend-design).
CARDS = Path(__file__).resolve().parent.parent / "data" / "slide_bold_previews"
# Thumbnails render to <workspace>/.artifacts/pollinate/assets/deck-styles (render_style_thumbnails.py).
THUMB_REL = ".artifacts/pollinate/assets/deck-styles"
# Portable: emit a WORKSPACE-RELATIVE thumbnail path. On SwarmAI a viewer can prefix the
# daemon file-serve URL via $POLLINATE_THUMB_URL_PREFIX; on Kiro/Claude Code (no daemon)
# the relative path resolves against the workspace directly. No hardcoded localhost daemon.
THUMB_URL_PREFIX = os.environ.get("POLLINATE_THUMB_URL_PREFIX", "")

# ── Audience/outcome/context/tone → desired style traits ─────────────────────
# Each maps to (formality_pref, mood_keywords, scheme_pref-or-None). Scores are
# additive; nothing is hard-excluded (a low match just ranks lower).
AUDIENCE_PREF = {
    "leadership":           (["high", "medium-high"], ["professional", "considered", "authoritative", "editorial", "corporate", "consulting"], None),
    "customer":             (["medium-high", "medium"], ["professional", "bold", "confident", "editorial", "clean"], None),
    "team":                 (["medium", "medium-low"], ["clear", "functional", "friendly", "direct"], None),
    "developer_community":  (["medium", "medium-low", "low"], ["technical", "bold", "playful", "retro", "geeky", "energetic"], None),
    "social_followers":     (["low", "medium-low"], ["playful", "bold", "loud", "energetic", "expressive", "dramatic", "fun"], None),
}
OUTCOME_PREF = {
    "awareness":     ["bold", "loud", "dramatic", "poster", "expressive"],
    "alignment":     ["considered", "professional", "clear", "editorial"],
    "action":        ["bold", "confident", "direct", "punchy"],
    "data_decision": ["clean", "functional", "precise", "editorial", "quiet"],
    "education":     ["clear", "literary", "considered", "patient", "scholarly"],
}
CONTEXT_PREF = {
    "meeting":      (["high", "medium-high"], ["professional", "considered", "clean"]),
    "email":        (["medium", "medium-high"], ["clear", "editorial"]),
    "social_media": (["low", "medium-low"], ["bold", "playful", "loud", "expressive"]),
    "search_learn": (["medium"], ["clear", "literary", "scholarly"]),
    "commute":      (["low", "medium"], ["bold", "punchy"]),
    "analysis":     (["high", "medium-high"], ["clean", "precise", "functional", "quiet"]),
}
TONE_KEYWORDS = {  # free-text tone → mood keywords (user may say anything)
    "serious": ["considered", "professional", "scholarly", "quiet"],
    "professional": ["professional", "considered", "editorial", "corporate"],
    "playful": ["playful", "fun", "childlike", "cheerful", "expressive"],
    "bold": ["bold", "loud", "dramatic", "punchy", "poster"],
    "elegant": ["literary", "editorial", "refined", "considered", "scholarly"],
    "technical": ["technical", "functional", "precise", "retro", "geeky"],
    "warm": ["warm", "friendly", "tactile", "hand-crafted", "cheerful"],
    "dramatic": ["dramatic", "loud", "bold", "expressive", "poster"],
    "quiet": ["quiet", "considered", "scholarly", "patient", "calm"],
    "creative": ["playful", "expressive", "creative", "bold", "hand-crafted"],
}

# Defect 2: the DISCOVER flow (INSTRUCTIONS Q1-Q5) yields message/audience/outcome/
# context/scope but NOT tone. --tone is optional; when absent we DERIVE an implicit
# tone from (audience, outcome) so the recommender still gets its strongest signal.
# Explicit --tone always overrides.
_DERIVED_TONE = {
    # (audience, outcome) → tone. outcome=None = audience-only fallback.
    ("leadership", None):          "professional",
    ("leadership", "alignment"):   "serious",
    ("leadership", "data_decision"): "quiet",
    ("customer", None):            "professional",
    ("customer", "action"):        "bold",
    ("team", None):                "warm",
    ("developer_community", None): "technical",
    ("social_followers", None):    "playful",
    ("social_followers", "awareness"): "bold",
}
_OUTCOME_TONE = {  # last-resort when audience unknown
    "awareness": "bold", "alignment": "professional", "action": "bold",
    "data_decision": "quiet", "education": "elegant",
}

def derive_tone(audience, outcome):
    """Implicit tone from audience+outcome when the user didn't state one."""
    if audience:
        if (audience, outcome) in _DERIVED_TONE:
            return _DERIVED_TONE[(audience, outcome)]
        if (audience, None) in _DERIVED_TONE:
            return _DERIVED_TONE[(audience, None)]
    if outcome in _OUTCOME_TONE:
        return _OUTCOME_TONE[outcome]
    return None  # no signal → tone simply doesn't contribute (still ranks on the rest)


def load_cards():
    cards = []
    for cf in sorted(glob.glob(str(CARDS / "*.md"))):
        txt = open(cf).read()
        def field(name):
            m = re.search(rf'- {re.escape(name)}:\s*(.+)', txt)
            return m.group(1).strip() if m else ""
        slug = Path(cf).stem
        cards.append({
            "slug": slug,
            "mood": [x.strip().lower() for x in field("Mood").split(",") if x.strip()],
            "tone": [x.strip().lower() for x in field("Tone").split(",") if x.strip()],
            "formality": field("Formality").lower(),
            "density": field("Density").lower(),
            "scheme": field("Scheme").lower(),
            "tagline": field("Tagline"),
            "best_for": field("Best for").lower(),
            "avoid_for": field("Avoid for").lower(),
        })
    return cards


def score(card, audience, outcome, context, tone):
    """Additive trait match. Higher = better fit. Deterministic."""
    s = 0.0
    reasons = []
    card_moods = set(card["mood"]) | set(card["tone"])

    def kw_hits(keywords, weight, label):
        nonlocal s
        hits = [k for k in keywords if any(k in cm for cm in card_moods) or k in card["best_for"]]
        if hits:
            s += weight * len(hits)
            reasons.append(f"{label}: {', '.join(sorted(set(hits))[:3])}")

    if audience and audience in AUDIENCE_PREF:
        forms, moods, _ = AUDIENCE_PREF[audience]
        if card["formality"] in forms:
            s += 2.0; reasons.append(f"formality {card['formality']} fits {audience}")
        kw_hits(moods, 1.5, f"audience {audience}")
    if outcome and outcome in OUTCOME_PREF:
        kw_hits(OUTCOME_PREF[outcome], 1.2, f"outcome {outcome}")
    if context and context in CONTEXT_PREF:
        forms, moods = CONTEXT_PREF[context]
        if card["formality"] in forms:
            s += 1.0
        kw_hits(moods, 1.0, f"context {context}")
    if tone:
        tone_kws = TONE_KEYWORDS.get(tone.lower(), [tone.lower()])
        kw_hits(tone_kws, 2.0, f"tone {tone}")
        # penalize explicit avoid-for match
        if any(k in card["avoid_for"] for k in tone_kws):
            s -= 1.5; reasons.append("(down: avoid-for match)")

    return round(s, 3), reasons[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audience"); ap.add_argument("--outcome")
    ap.add_argument("--context");  ap.add_argument("--tone")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    # Defect 2: DISCOVER doesn't yield tone → derive it from audience+outcome when
    # the user didn't pass --tone (explicit --tone always wins).
    effective_tone = a.tone or derive_tone(a.audience, a.outcome)

    cards = load_cards()
    ranked = []
    for c in cards:
        sc, why = score(c, a.audience, a.outcome, a.context, effective_tone)
        ranked.append((sc, c, why))
    # DETERMINISTIC order: score desc, then slug asc (stable tiebreak → paginates
    # cleanly with --offset, no repeats/gaps across "next batch").
    ranked.sort(key=lambda x: (-x[0], x[1]["slug"]))

    page = ranked[a.offset:a.offset + a.top]
    out = []
    for sc, c, why in page:
        out.append({
            "slug": c["slug"], "score": sc,
            "formality": c["formality"], "scheme": c["scheme"],
            "tagline": c["tagline"],
            "thumbnail": f"{THUMB_REL}/{c['slug']}.png",  # workspace-relative
            "thumbnail_url": f"{THUMB_URL_PREFIX}{THUMB_REL}/{c['slug']}.png",  # prefix empty by default → relative; set $POLLINATE_THUMB_URL_PREFIX to absolutize
            "why": why,
        })
    result = {
        "profile": {"audience": a.audience, "outcome": a.outcome,
                    "context": a.context, "tone": a.tone,
                    "effective_tone": effective_tone,
                    "tone_derived": a.tone is None and effective_tone is not None},
        "total": len(ranked), "offset": a.offset, "returned": len(out),
        "has_more": a.offset + a.top < len(ranked),
        "recommendations": out,
    }
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Top {len(out)} of {len(ranked)} (offset {a.offset}):")
        for r in out:
            print(f"  [{r['score']:5}] {r['slug']:20} {r['formality']:12} — {r['tagline'][:60]}")
            for w in r["why"]:
                print(f"           · {w}")


if __name__ == "__main__":
    main()
