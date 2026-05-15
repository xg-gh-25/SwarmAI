# Poster Design System — Long-Form Social Cards

> Source: Research from heti (6.7K★), open-props (5.4K★), XHS-BLOG, palxiao/poster-design (4.7K★), typo.css (4.5K★), tailwindcss-typography (6.4K★)
> Last updated: 2026-05-16

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

## Color System (Dark Mode)

| Token | Value | Use |
|-------|-------|-----|
| --bg-deep | #0A0A0B | Page background (near-OLED black) |
| --bg-elevated | #111113 | Elevated cards, footer |
| --surface | rgba(255,255,255, 0.03) | Card background |
| --border | rgba(255,255,255, 0.06) | Card borders, dividers |
| --gold | #D4A853 | Accent, tagline, labels, highlights |
| --gold-dim | rgba(212,168,83, 0.15) | Subtle gold backgrounds |
| --gold-glow | rgba(212,168,83, 0.06) | Background radial glow |
| --text-primary | #FFFFFF | Headlines |
| --text-secondary | rgba(255,255,255, 0.7) | Body text |
| --text-muted | rgba(255,255,255, 0.4) | Captions, labels |

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

## Anti-Patterns

- ❌ Mixed alignment (some left, some center) in the same poster
- ❌ Spacing that isn't a multiple of 24px
- ❌ Text running full 1080px width (no max-width constraint)
- ❌ Same card style repeated consecutively
- ❌ Body line-height < 2.0 for Chinese
- ❌ Headline > 56px (overwhelming on phone)
- ❌ More than 3 opacity levels for text (stick to 100% / 70% / 40%)
- ❌ Decorative elements that compete with text (text is always hero)
