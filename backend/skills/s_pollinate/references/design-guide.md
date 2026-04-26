# Pollinate Visual Design Guide

## Hard Minimums (1080p design space, scale(2) to 4K)

| Element | Minimum Size |
|---------|-------------|
| Any text | 24px |
| Hero title | 84px |
| Section title | 72px |
| Card title | 40px |
| Body text | 32px |
| Icons | 56px |
| Section padding | 40px |
| Card padding | 40px 48px |
| Card border-radius | 24px |
| Grid gap | 28px |

## Space Utilization

- Content width >= 85% of available width (maxWidth 1500-1700px)
- Vertical centering required
- Prefer grid over flex wrap
- Bottom 100px reserved for subtitles

## Visual Richness

- Colored borders >= 3px
- Shadows on cards
- Color coding across parallel elements
- Gradients preferred over solid backgrounds
- Every card needs an icon >= 56px

## Theme Rules

| Theme | Background | Text | Accent |
|-------|-----------|------|--------|
| Light | White / Off-white | Dark (#2D3436) | Swarm Orange (#FF6B35) |
| Dark | Charcoal (#1A1A2E) | Light (#FAFAFA) | Domain-specific |

## Animated Backgrounds

5 types, 1-2 per section max, opacity 0.03-0.08:
- **MovingGradient** — rotating linear gradient
- **FloatingShapes** — 3-5 shapes max (circle/hexagon/ring)
- **GridPattern** — dots, lines, or crosses
- **GlowOrb** — large radial gradient with pulse
- **AccentLine** — spring-animated horizontal line

## Section Layout Presets

No repeats in consecutive sections:
1. **SplitLayout** — text left, visual right (or reversed)
2. **StatHighlight** — centered big number
3. **ZigzagCards** — alternating left/right cards
4. **CenteredShowcase** — centered title + body
5. **MetricsRow** — CSS Grid of metric cards (up to 4 columns)
6. **StepProgress** — numbered step cards with active highlight

## Transitions

Use `@remotion/transitions` TransitionSeries:
- Types: fade / slide / wipe / none
- Default: 15 frames
- Remember: transition frame compensation on first section

## Component Selection by Content Type

| Content Type | Primary | Supporting |
|-------------|---------|-----------|
| Architecture / flow | FlowChart | DiagramReveal |
| Code example | CodeBlock | — |
| A vs B comparison | ComparisonCard | DataBar |
| Chronological story | Timeline | IconCard |
| Data / metrics | StatCounter, DataBar | DataTable |
| Expert opinion | QuoteBlock | — |
| Feature list | FeatureGrid | IconCard |
| Concept introduction | IconCard | SectionLayouts |

## 4K Scaling Strategy

- Design at 1920x1080 (1080p)
- Wrap in Scale4K: `transform: scale(2)` → 3840x2160
- ChapterProgressBar: OUTSIDE Scale4K (native 4K)
- Subtitles: OUTSIDE Scale4K (native 4K, fontSize=80 = 40px design space)
