# Poster Design System — Long-Form Social Cards

> Source: Research from heti (6.7K★), open-props (5.4K★), XHS-BLOG, palxiao/poster-design (4.7K★), typo.css (4.5K★), tailwindcss-typography (6.4K★), html-anything (2.2K★), huashu-design (~2.7K★), catppuccin (19.2K★), Radix Colors (1.6K★)
> Last updated: 2026-05-16
> Version: 2.0 — Multi-direction + Anti-Slop + Semantic Tokens

## Design Directions (Choose ONE Per Poster)

Every poster MUST map to exactly ONE named direction. Never mix directions.

| ID | Name | Mood | Use When |
|----|------|------|----------|
| **D1** | Obsidian (黑曜石) | 专业冷峻 | Technical, architecture, code, data |
| **D2** | Paper (纸质) | Apple 极简 | Business insight, methodology, few high-impact points |
| **D3** | Ink (水墨) | 东方意境 | Philosophy, personal values, quotes, ceremony |
| **D4** | Neon (霓虹) | 赛博朋克 | AI/frontier, provocative opinions, tech manifestos |
| **D5** | Morandi (莫兰迪) | 柔和叙事 | Narrative, stories, human interest, warmth |

**Token files:** Each direction has a complete token set in `brand/directions/d{N}-{name}.yaml`

**How to select:**
1. User override wins ("用 Neon 风格" → D4)
2. Content-type heuristic (see each file's `content_triggers`)
3. Thesis fallback: T1→D1, T2→D4, T3→D3, T4→D4, T5→D5, T6→D2
4. Default: D1 (Obsidian) if unclear

**How to apply:** Load the direction's `css_snippet` into the poster's `:root` block. ALL colors/fonts reference semantic tokens (`var(--accent)`, `var(--text-primary)`, etc.). Never hardcode hex values in poster HTML.

**Quick LLM injection (paste into poster prompt):**
```
DIRECTION: D{N} {name}
TOKENS: [paste css_snippet from the direction file]
MOOD: {mood}
VISUAL RULES: [paste visual_elements from the direction file]
```

---

## Platform Dimensions

| Platform | Dimensions | Aspect | Notes |
|----------|-----------|--------|-------|
| 小红书 (standard) | 1080×1440 | 3:4 | Official recommendation |
| 小红书 (tall) | 1080×1920 | 9:16 | Maximum without cropping |
| 朋友圈 (square) | 1080×1080 | 1:1 | Thumbnail in feed |
| 朋友圈 (long) | 1080×variable | — | Long-press to view full |
| OG / Twitter | 1200×630 | ~2:1 | Preview cards |

For long-form story posters: **1080px wide, variable height**. Render at 2x (2160px) for Retina if needed.

## Typography Scale (Chinese + English Mixed)

Based on heti CLReq + tailwindcss-typography. **All sizes computed for 1080px canvas width.**

| Role | Size | Line-height | Weight | Font | Letter-spacing |
|------|------|-------------|--------|------|---------------|
| Hero headline | 52-56px | 1.3 | 600-700 | PingFang SC | 0 |
| Section headline | 40-44px | 1.35 | 600 | PingFang SC | 0 |
| Body (Chinese) | 24px | 2.0-2.2 | 300 | PingFang SC | 0.02em |
| Body (English) | 24px | 1.8 | 300 | Inter | 0 |
| Highlight text | 24px | 2.0 | 400 | PingFang SC | 0 |
| Eyebrow / label | 12-13px | 1.4 | 500-600 | SF Mono / Inter | 3-4px |
| Caption / URL | 13-14px | 1.4 | 400 | SF Mono | 0.3px |
| Tagline (EN) | 20-24px | 1.4 | 500-600 | Inter | 0.5px |

**Rules:**
- Chinese body line-height: **2.0 minimum** (heti recommendation for readability)
- Max reading width: 42em (~700px at 24px base) — never stretch text full-width
- Paragraph indent: 0 (modern style, use spacing instead)
- Auto-spacing between CJK/Latin (pangu.js principle): add 0.25em conceptual gap

### CJK-First Font Stack (Mandatory)

```css
/* Chinese body (default for all directions) */
--font-body-cjk: "Noto Sans SC", "Source Han Sans SC", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;

/* Chinese display (headlines — override per direction) */
--font-display-cjk: "PingFang SC", "Noto Sans SC", "Source Han Sans SC", sans-serif;

/* Chinese serif (D3 Ink direction only) */
--font-serif-cjk: "STSongti-SC", "Noto Serif SC", "Source Han Serif SC",
                  "SimSun", serif;

/* English display */
--font-display-en: "Inter", "Manrope", "SF Pro Display", system-ui, sans-serif;

/* English serif (D3 Ink direction only) */
--font-serif-en: "Playfair Display", "Source Serif 4", "Georgia", serif;

/* Code (all directions) */
--font-code: "SF Mono", "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
```

### CJK Typography Rules (from Heti 赫蹏 CLReq)

1. **CJK-Latin spacing:** Add ~0.25em between Chinese and English/numbers (CSS `word-spacing` or post-processing)
2. **Punctuation compression:** `font-feature-settings: "halt" 1` — CJK punctuation occupies half-width
3. **Chinese paragraph justify:** `text-align: justify; text-justify: inter-ideograph` for body blocks
4. **Reading width limit:** `max-width: 42em` (~700px at 24px) — mandatory, no exceptions
5. **Line-height rules:** CJK body ≥ 2.0, Latin body ≥ 1.75, Headlines = 1.3

## Vertical Rhythm

**Base unit: 24px** (derived from 16px × 1.5, per heti standard block unit)

| Spacing | Multiplier | px | Use |
|---------|-----------|-----|-----|
| xs | 0.5× | 12px | Tight internal (label → heading) |
| sm | 1× | 24px | Paragraph bottom, tight card padding |
| md | 2× | 48px | Between heading and body, card internal |
| lg | 3× | 72px | Section padding (internal) |
| xl | 4× | 96px | Section padding (generous) |
| 2xl | 5× | 120px | Section breaks / hero spacing |

**Critical rule: ALL vertical spacing must be multiples of 24px.** This creates consistent rhythm. Mixed spacing (48px here, 56px there) = visual chaos.

## Section Spacing (Consistent)

| Element | Top padding | Bottom padding | Derived from |
|---------|-------------|----------------|-------------|
| Hero header | 120px | 120px | 5× base |
| Card section | 96px | 96px | 4× base |
| Section divider | 48px | 48px | 2× base |
| Tech grid section | 96px | 96px | 4× base |
| Footer | 72px | 72px | 3× base |
| Card internal (heading → body) | 48px | 0 | 2× base |
| Eyebrow → heading | 24px | 0 | 1× base |

## Alignment System

**One rule: CENTER everything for poster/social format.**

Long-form social posters are not editorial layouts. They're viewed on phones at 375px viewport width where the image is centered. Any left-bias creates dead space on the right.

| Element | Alignment | Max-width |
|---------|-----------|-----------|
| Card container | Center (margin: 0 auto) | 100% of poster width |
| Eyebrow | Center | — |
| Headline | Center | 800px |
| Body text | Center | 700px (42em at 24px) |
| Highlight / pullquote | Center | 700px |
| Tech grid | Center | 920px (full - 80px×2) |
| Footer | Center | 920px |

**Exception: body text WITHIN its max-width block is LEFT-aligned** (reading direction). The block itself is centered in the poster.

## Semantic Token System (Direction-Driven)

All posters use **semantic token names** (not hardcoded colors). The active direction populates the values.

| Token | Purpose | Example (D1 Obsidian) |
|-------|---------|----------------------|
| `--bg-deep` | Canvas/page background | #0A0A0B |
| `--bg-elevated` | Card/container surface | #111113 |
| `--bg-surface` | Subtle surface tint | rgba(255,255,255,0.03) |
| `--border` | Dividers, card borders | rgba(255,255,255,0.06) |
| `--accent` | Primary highlight, tags, CTAs | #D4A853 |
| `--accent-dim` | Accent at low opacity (backgrounds) | rgba(212,168,83,0.15) |
| `--accent-glow` | Glow/shadow using accent color | rgba(212,168,83,0.06) |
| `--text-primary` | Headlines, emphasis | #FFFFFF |
| `--text-secondary` | Body text | rgba(255,255,255,0.7) |
| `--text-muted` | Captions, metadata | rgba(255,255,255,0.4) |

**Rule:** NEVER use raw hex values in poster HTML. Always reference `var(--token-name)`. This ensures direction switching is zero-cost (change the `:root` block, everything follows).

**Legacy compatibility:** The D1 (Obsidian) token set produces identical output to the previous `--gold` / `--bg-deep` system. Existing templates continue to work unchanged.

## Visual Rhythm (Card Variety)

For a 6-section poster, alternate card treatments to prevent monotony:

| Position | Card Style | Visual Element | Purpose |
|----------|-----------|----------------|---------|
| 1 | Full dramatic | Gold left-border on body | Strong opening |
| 2 | Visual accent | Abstract geometric (rings/orbs) | Visual break |
| 3 | Contained card | Border + radius + watermark | Tactile variety |
| 4 | Centered manifesto | Minimal, text-only | Breathing room |
| 5 | Pullquote | Gold-tinted quote block | Emphasis shift |
| 6 | Finale | Elevated bg + geometric accent | Strong close |

**Never use the same card style twice consecutively.** Alternation creates page-turn energy.

## Dividers Between Sections

Alternate between two styles:
- **Vertical line:** 1px × 60px, gold gradient (fade to transparent at both ends)
- **Dot:** 6px circle, gold at 30% opacity

Total divider height including surrounding space: **120px** (5× base).

## Rendering Pipeline

1. Author: HTML + CSS (inline styles or `<style>` block)
2. Render: Playwright headless Chrome → full_page screenshot
3. Output: PNG, verify < 2MB for platform upload
4. QR: Generate via `qrcode` library, match brand colors

**Retina rule:** If final poster > 4000px tall, render at 1x (1080px wide). If < 4000px, can render at 2x for sharpness.

## Anti-Slop Quality Gate

> "What you ban matters more than what you allow." — huashu-design philosophy
> "Hard constraints stop the model from freestyling." — html-anything

### Visual Ban List (BAN — regenerate if detected)

**Colors:**
- ❌ Purple gradient backgrounds (AI's #1 default aesthetic — always ban)
- ❌ Rainbow gradient text
- ❌ Pure `#000000` background (use direction's `--bg-deep` instead)
- ❌ Pure `#FFFFFF` text on pure black (use direction's `--text-primary`)
- ❌ Saturated neon colors on white background (eye-straining)
- ❌ More than 2 hues in the same poster (direction controls the palette)

**Typography:**
- ❌ Inter used as display/headline font (Inter = body only; headlines use direction font)
- ❌ All-caps Chinese text (CJK has no uppercase concept — looks broken)
- ❌ Comic Sans, Papyrus, or any novelty fonts
- ❌ Font size < 18px anywhere on poster (unreadable on phone)
- ❌ More than 3 font families in one poster
- ❌ Letter-spacing > 0.5px on Chinese text (destroys character rhythm)

**Layout:**
- ❌ Left-border accent cards as primary repeating pattern (overused in AI output)
- ❌ Centered emoji used as section icons (emoji is not iconography)
- ❌ Generic SVG human illustrations (Undraw/Storyset style)
- ❌ Drop shadow blur > 20px (dated "2018 card UI" aesthetic)
- ❌ Mixed border-radius (some rounded, some square in same poster)
- ❌ Cards touching poster edges without padding (minimum 48px margin)

**Content:**
- ❌ Stock photo backgrounds (never use photos as poster base)
- ❌ QR code larger than 120px (QR should not dominate visual hierarchy)
- ❌ More than 2 decorative elements per section
- ❌ Decorative elements competing with text for attention
- ❌ Watermark/logo larger than 48px height
- ❌ More than 6 sections per poster (information overload)

### Structural Ban List (BAN — always enforce)

- ❌ Spacing that isn't a multiple of 24px (base unit rule)
- ❌ Text running full canvas width without max-width constraint
- ❌ Same card style used consecutively (must alternate)
- ❌ Body line-height < 2.0 for Chinese (CJK readability minimum)
- ❌ Section with > 3 key points (each screen = one core idea)
- ❌ Headline longer than 12 Chinese characters (poster titles are SHORT)
- ❌ English and Chinese mixed in same line without visual spacing
- ❌ Gradient direction inconsistent across cards in same poster (pick one angle)
- ❌ Text directly on gradient without surface/card container
- ❌ Mixed alignment (some left, some center) in same poster
- ❌ Headline > 56px (overwhelming on phone)
- ❌ More than 3 opacity levels for text (100% / 70% / 40% only)
- ❌ Direction mixing — using tokens from two different directions in one poster

### Quality Gate Execution

```
PRE-RENDER: Parse HTML/CSS → check against both ban lists → regenerate if violation found (max 2 retries)
POST-RENDER: Verify dimensions match platform, file < 2MB, text readable (contrast ≥ 4.5:1), no text cutoff (min 48px edge padding)
```
