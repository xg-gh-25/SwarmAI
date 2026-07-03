# Track C: Narrative / Article

> Markdown article/narrative following content_package + content_principles, with
> an advisory GEO (Generative Engine Optimization) signal-stack score.

## When This Track Runs

Executes during BUILD when `"narrative"` is in `confirmed_tracks` (discovery.json).
Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

(Legacy note: older strategy.json used `production_tracks`; STRATEGIZE copies
`confirmed_tracks` into it for back-compat — they are always equal. Scope is
authoritatively `confirmed_tracks`.)

---

#### Step C.1: Content Generation

Write the article/narrative in markdown format. Follow content_package.md
structure and content_principles.md for tone.

#### Step C.2: L9 GEO Signal Stack (ADVISORY — articles/narratives only)

> GEO (Generative Engine Optimization) ensures content is citable by AI engines
> (ChatGPT, Perplexity, Claude, Gemini). Source: Princeton KDD 2024 + CMU AutoGEO.

**After writing narrative content, run the GEO scorer:**

```bash
python "$SKILL_DIR/scripts/geo_score.py" "content/{name}/tracks/narrative/{file}.md" --json
```

**Output:** JSON with `total_score` (0-100), per-pillar breakdown, warnings.
**Gate:** Score ≥ 60 = PASS. Score < 60 = WARN (advisory, not blocking for now).

**The 4 pillars (weighted):**

| Pillar | Weight | Key Checks |
|--------|--------|------------|
| Evidence Density | 35% | ≥5 numbers with units, ≥2 named experts, ≥1 cite/500w |
| Structure & Position | 25% | Core message in first 150 words (PAWC), TL;DR section, headings |
| Authority Signals | 25% | Author byline, date, methodology section, limitations |
| AI Crawlability | 15% | Clean markdown, semantic headings, no placeholders |

**PAWC Rule (Position-Adjusted Word Count):**
Sentence #1 in AI answers is worth ~5x sentence #20 (exponential decay).
**Put the conclusion + one statistic in the first 150 words. Always.**

**If score < 60 (advisory improvements):**
- Add specific numbers with units (not "many" or "significant")
- Add named expert quotes (not "experts say")
- Move core finding to first paragraph
- Add TL;DR section for articles > 500 words
- Remove generic openers ("In today's rapidly evolving...")

**Anti-patterns (auto-detected, surfaced as warnings):**
- Generic AI openers ("In today's rapidly evolving landscape...")
- Unsupported superlatives ("revolutionary", "game-changing") without evidence
- Zero named entities (content too generic to be cited)
- Keyword density > 1% (actively harmful per Princeton data)

#### Narrative Output Files

- `content/{name}/tracks/narrative/{topic}.md` — the article
- `content/{name}/tracks/narrative/geo_score.json` — GEO scoring result (saved automatically)

---

