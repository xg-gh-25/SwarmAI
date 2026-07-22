# Platform-Adaptive Rendering Rules

> One content → multiple formats. Same message, different density/size per platform.
> Source: html-anything (75 skills), haberkart (8-target pattern), design doc v2

## Core Principle

Content and visual presentation are SEPARATE concerns:
1. **Content Schema** defines WHAT to show (title, sections, footer)
2. **Platform Renderer** defines HOW to show it (dimensions, density, section limit)

The LLM generates content structure once. Platform rules control the visual output.

---

## Platform Specifications

### XHS Card (小红书 标准)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1080 × 1440 | 3:4 official recommendation |
| Max sections | 4 | Readable at thumb-scroll speed |
| Text hero | 56px | Eye-catching in feed |
| Text body | 26px | +2px from base for feed thumbnail readability |
| Section spacing | 72px (3× base) | Moderate density |
| Edge padding | 60px | Safe from feed crop |
| Content density | Medium | 3-4 key points visible |

**XHS-specific rules:**
- Title ≤ 20 Chinese characters (platform norm)
- Emoji usage encouraged (platform culture)
- Gradient cards work well (thumb-stopping in feed)
- Bottom 120px reserved for branding/QR (not content)

### XHS Long (小红书 长图)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1080 × auto | Variable height, full content |
| Max sections | 6 | Complete story |
| Text hero | 52px | Slightly smaller for scroll |
| Text body | 24px | Standard |
| Section spacing | 96px (4× base) | Generous — long scroll needs breathing room |
| Edge padding | 60px | Consistent with card format |
| Content density | High | Full information |

**Long-form rules:**
- Add section number indicators (1/6, 2/6...) for progress
- Each section should be independently meaningful (screenshot-friendly)
- Total height should not exceed 8000px (platform may truncate)

### OG Image (Twitter / Link Preview)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1200 × 630 | Universal OG standard |
| Max sections | 1 | Title + one statement ONLY |
| Text hero | 64px | Maximum impact in tiny preview |
| Text body | 28px | +4px from base — OG renders at 300px width in feeds, needs extra size |
| Section spacing | 48px (2× base) | Compact |
| Edge padding | 80px | Extra safe — platforms crop unpredictably |
| Content density | Very low | One thought per image |

**OG-specific rules:**
- English-first (international audience)
- High contrast mandatory (renders at 300px wide in feeds)
- No QR code (pointless at this size)
- Logo placement: bottom-right corner, 32px height max

### Story (9:16 竖屏)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1080 × 1920 | Standard story format |
| Max sections | 2 | Thumb-stoppable in 2 seconds |
| Text hero | 72px | Must read in < 1 second |
| Text body | 32px | Large for split-second comprehension |
| Section spacing | 96px (4× base) | Very generous |
| Edge padding | 72px | Safe from system UI overlays |
| Content density | Very low | Single statement per screen |
| Safe zone bottom | 180px | Reserved for swipe-up / CTA area |

**Story-specific rules:**
- Safe zone: top 100px (status bar), bottom 180px (CTA/swipe)
- Center content vertically within safe area
- Maximum 2 sentences visible at once
- Works for: 抖音, 视频号, Instagram Stories

### 朋友圈 Square (WeChat Moments)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1080 × 1080 | Square for Moments grid |
| Max sections | 3 | Moderate density in square |
| Text hero | 48px | Balanced for 1:1 ratio |
| Text body | 24px | Standard |
| Section spacing | 60px (2.5× base) | Compact but readable |
| Edge padding | 60px | Standard |
| Content density | Medium | Concise but complete |

**WeChat-specific rules:**
- CSS must be INLINED (WeChat strips `<style>` blocks)
- No external font loading (WeChat blocks)
- Use system font stack only: PingFang SC, Hiragino Sans GB
- Image optimized for 9-grid display (thumbnail at 360px)

### Twitter/X (16:9 Landscape)

| Property | Value | Reasoning |
|----------|-------|-----------|
| Dimensions | 1280 × 720 | Standard landscape for feed |
| Max sections | 2 | Landscape = less vertical space |
| Text hero | 56px | Readable in timeline |
| Text body | 24px | Standard |
| Section spacing | 48px (2× base) | Tight — landscape is space-constrained |
| Edge padding | 80px | Platform crops aggressively |
| Content density | Low-medium | Bold statement + one supporting point |

**Twitter-specific rules:**
- English text preferred (global audience)
- High contrast (dark mode users are majority)
- Single powerful observation + opinion format
- Must be retweetable (self-contained meaning)

---

## Content Density Control

When the same content targets different platforms, TRIM by priority:

```
Priority (keep first, cut last):
1. Title / hero statement (NEVER cut)
2. First section / key insight (NEVER cut)
3. Supporting evidence / data (trim for low-density)
4. Context / background (trim for medium-density)
5. Footer / branding (trim for extreme low-density)
6. QR code (only include if platform supports scanning)
```

**Density adaptation examples:**
- Full content (6 sections) → XHS Long: keep all 6
- Same content → XHS Card: keep sections 1-4, merge 5-6 into card 4
- Same content → OG Image: keep title + section 1 only
- Same content → Story: keep title + one sentence from section 1

---

## Direction × Platform Matrix

All 5 directions work on all platforms. Platform rules control LAYOUT. Direction controls COLOR/TYPOGRAPHY/MOOD.

| | XHS Card | XHS Long | OG | Story | 朋友圈 | Twitter |
|---|---|---|---|---|---|---|
| D1 Obsidian | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| D2 Paper | ✅ ★ | ✅ | ✅ | ✅ | ✅ ★ | ✅ |
| D3 Ink | ✅ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ |
| D4 Neon | ✅ | ✅ | ✅ ★ | ✅ ★ | ✅ | ✅ |
| D5 Morandi | ✅ ★ | ✅ | ✅ | ✅ | ✅ ★ | ✅ |

★ = Particularly good combination
⚠️ = Works but serif Chinese may render poorly at very small sizes (OG/Twitter thumbnails)
