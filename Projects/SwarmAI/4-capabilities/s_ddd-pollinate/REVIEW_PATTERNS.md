# Pollinate Review Patterns

Video-specific quality checks. Run ALL patterns during REVIEW stage.
Write explicit ✅ / ⚠️ / ❌ for each. Silence = unchecked.

## Video Quality Patterns

| # | Pattern | Trigger | What to Verify |
|---|---------|---------|----------------|
| RP-V1 | **Audio-video sync** | Every run | timing.json: each section start/end within ±0.5s of audio |
| RP-V2 | **Subtitle safe zone** | Every run | No visual content in bottom 100px (reserved for subtitles) |
| RP-V3 | **Information density** | Every run | Each screen shows <= 3 key points simultaneously |
| RP-V4 | **Subtitle accuracy** | Every run | SRT text vs podcast.txt: diff <= 2% (character-level) |
| RP-V5 | **Thumbnail specs** | Every run | 16:9 AND 4:3 files exist, correct dimensions. 3:4 for 小红书 |
| RP-V6 | **Polyphone coverage** | zh-CN runs | All domain-specific terms in phonemes.json |
| RP-V7 | **Resolution & codec** | Every run | ffprobe: 3840x2160 (or 2160x3840), H.264, >= 8Mbps video, AAC >= 192kbps |
| RP-V8 | **Duration target** | Every run | B站: 3-12min, shorts: 30-120s per section |
| RP-V9 | **Brand consistency** | Every run | Swarm color palette (identity.yaml), font family, intro/outro present |
| RP-V10 | **Component variety** | Every run | No same component type in consecutive sections |
| RP-V11 | **Text readability** | Every run | All text >= 24px, hero >= 84px, section title >= 72px |
| RP-V12 | **Content width** | Every run | >= 85% of screen width utilized |

## Output Format

```
RP-V1:  ✅ All 6 sections within ±0.3s
RP-V2:  ✅ Bottom 100px clear
RP-V3:  ⚠️ Section 3 has 4 points -- consider splitting
RP-V4:  ✅ SRT diff 0.8%
RP-V5:  ✅ 16:9 (1920x1080), 4:3 (1200x900), 3:4 (1080x1440)
RP-V6:  ✅ 12 terms in phonemes.json
RP-V7:  ✅ 3840x2160, H.264, 16.2Mbps, AAC 192kbps
RP-V8:  ✅ 6:42 (within 3-12min)
RP-V9:  ✅ Swarm Orange #FF6B35, PingFang SC, outro present
RP-V10: ✅ FlowChart -> QuoteBlock -> Timeline -> CodeBlock -> StatCounter
RP-V11: ✅ Min text 32px, hero 96px
RP-V12: ✅ Content width 88%
```

## Poster Quality Patterns

| # | Pattern | Trigger | What to Verify |
|---|---------|---------|----------------|
| RP-P1 | **Direction declared** | Every poster | Poster HTML has a comment or metadata declaring which direction (D1-D5) is active |
| RP-P2 | **Token consistency** | Every poster | ALL colors reference `var(--token)` — zero hardcoded hex values in the body CSS |
| RP-P3 | **Anti-Slop clean** | Every poster | Zero violations against Visual Ban List AND Structural Ban List (see `poster_design_system.md`) |
| RP-P4 | **Platform fit** | Every poster | Dimensions match target platform (per `references/platform-adaptive.md`), density rules followed |
| RP-P5 | **CJK typography** | CJK content | Line-height ≥ 2.0, max-width ≤ 42em, font stack includes Noto Sans SC or Source Han |
| RP-P6 | **Direction cohesion** | Every poster | All visual elements belong to the declared direction — no accidental mixing (e.g., gold accent in Paper direction) |
| RP-P7 | **Contrast & readability** | Every poster | Text:background contrast ≥ 4.5:1 (WCAG AA), no text cut off at edges, minimum 48px edge padding |

### Poster Output Format (8-Layer Publish-Ready Gate)

The 8-layer gate runs as a convergence loop (max 3 iterations) inside BUILD
stage Step B.5. After convergence passes, Step B.5b spawns an adversarial
sub-agent for independent brand review (fresh context, no builder bias).
REVIEW verifies BOTH the gate AND the adversarial review passed cleanly —
checking `convergence-log.txt` output AND re-scanning the final rendered output.

```
── 8-LAYER PUBLISH-READY GATE ──────────

L1 Direction:   ✅ A=D5 (Morandi), B=D4 (Neon) — both declared in HTML
L2 Tokens:      ✅ 0 hardcoded hex in body CSS (both variants)
L3 Spacing:     ✅ Max section gap: 48px (A), 48px (B) — within ≤72px limit
L4 Alignment:   ✅ 100% center-aligned (both variants, 0 mixed elements)
L5 Anti-Slop:   ✅ 0/45 violations — A: clean, B: clean
L6 Platform:    ✅ A: 1080×3200 (XHS long), B: 1080×2800 (XHS long)
L7 Branding:    ✅ Watermark ✓ QR ({{PROJECT_LINK}}) ✓ GitHub URL text ✓ (opt-in — only if the DDD configures branding)
L8 Variants:    ✅ 2 directions rendered (A + B)

Convergence: passed in {N} iteration(s)
Status: PUBLISH-READY
```

**If any layer failed during convergence:**

```
L3 Spacing:     ⚠️ FIXED (iter 1: 96px gap → iter 2: 48px — auto-reduced padding)
L5 Anti-Slop:   ❌ UNFIXED — "left-border accent card" in section 3 (direction D4 doesn't use borders)
                 → Requires regeneration of section 3
```

**REVIEW responsibility:** Verify the convergence log is truthful — spot-check at
least 2 layers by re-measuring the final output (not trusting the log alone).
Specifically: re-check L3 (actual rendered gap) and L4 (actual text-align values).

---

## Anti-Rationalization Gate

| Shortcut | Required Response |
|----------|-------------------|
| "Script is short, skip polyphone check" | Short scripts have higher per-word impact. Check every term. |
| "Brand colors are close enough" | Brand consistency is binary. Match or fix. |
| "Duration is 12:30, close enough to 12min" | 12:00 is the max. Trim the script. |
| "It looked fine in Studio, skip review" | Studio preview != quality audit. Check every RP. |
| "Only targeting B站, skip other platform specs" | Generate metadata for all platforms. Distribution is free. |
| "Thumbnails can wait" | Thumbnails drive click-through. Generate all 3 sizes now. |
