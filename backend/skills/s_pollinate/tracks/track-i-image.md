# Track I: AI Image — Hero Visuals

> Structured prompt generation for hero images, deck illustrations, social thumbnails,
> and article headers. Tool-agnostic — works with any image API or exports prompts
> for manual generation.

## When This Track Runs

This track executes during BUILD stage when `"ai_image"` is in
`confirmed_tracks` (from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — thesis, key points, visual layer hints
- `content/{name}/discovery.json` — audience, outcome, intended usage context
- Active design direction YAML — color palette for prompt grounding

## Primary Use Case

AI Image is a **support track** — it produces hero visuals consumed by other tracks:
- Deck illustrations (Track E slide backgrounds)
- Article hero images (Track C header)
- Social thumbnails (Track B/D companion)
- One-pager visuals (Track F accent)

It can also run standalone when the user explicitly requests "generate an image."

---

## Production Flow

```
Step 1: Determine image purpose from discovery.json (hero/illustration/thumbnail/standalone)

Step 2: Extract visual concepts from content_package.md Visual Layer
        - Diagram descriptions → composition structure
        - Key metaphors → subject matter
        - Audience profile → style calibration

Step 3: Build structured prompt JSON
        - Subject, style, mood, composition, technical specs
        - Negative prompt (what to exclude)
        - Direction tokens → color grounding

Step 4: Detect available generation tool and generate (or export prompt)

Step 5: Quality verification (RP-I)
```

---

## Step 1: Purpose Detection

Read `discovery.json` and determine image role:

| Usage | Aspect Ratio | Resolution | Style Bias |
|-------|-------------|-----------|-----------|
| Deck illustration | 16:9 | 1920x1080 | Clean, minimal, conceptual |
| Social thumbnail | 1:1 | 1080x1080 | Bold, high-contrast, eye-catching |
| Article hero | 16:9 or 3:2 | 1200x630 | Editorial, atmospheric |
| One-pager accent | 4:3 | 800x600 | Subtle, professional |
| Standalone | User-specified | Max available | Match request |

If multiple tracks are in `confirmed_tracks`, generate images sized for the
highest-priority visual consumer (deck > article > social > one-pager).

---

## Step 2: Visual Concept Extraction

From `content_package.md` Visual Layer:

1. **Primary metaphor** — the conceptual image (e.g., "swarm intelligence" → bees/network)
2. **Key elements** — concrete objects that represent the thesis
3. **Mood** — derived from audience + outcome (leadership = professional, social = bold)
4. **Negative space** — what should NOT appear (avoid cliches, competitor imagery)

From direction YAML:
- `tokens.accent` → dominant color hint in prompt
- `tokens.bg-deep` → background tone guidance
- Design style keywords → style modifier in prompt

---

## Step 3: Build Structured Prompt

Generate a prompt JSON file at `content/{name}/tracks/image/prompt.json`:

```json
{
  "version": "1.0",
  "purpose": "deck_illustration",
  "prompt": {
    "subject": "Abstract network of glowing nodes forming a beehive pattern",
    "style": "3D render, clean minimal aesthetic, soft volumetric lighting",
    "mood": "Professional, innovative, forward-looking",
    "composition": "Central focal point with depth, rule of thirds, negative space on left for text overlay",
    "color_grounding": "Primary: #D97757 warm accent, Background: deep navy #1a1a2e, Highlights: gold #f0c040",
    "technical": {
      "aspect_ratio": "16:9",
      "resolution": "1920x1080",
      "format": "PNG",
      "quality": "high"
    }
  },
  "negative_prompt": "text, watermark, logo, blurry, low quality, stock photo cliches, generic business imagery, hands",
  "tool_preference": "dall-e-3",
  "fallback": "structured_prompt_export"
}
```

**Prompt engineering rules:**

| Rule | Why |
|------|-----|
| No text in image | AI text is unreliable — overlay text via HTML/PPTX instead |
| Specific over vague | "glowing hexagonal nodes" > "technology concept" |
| Include composition | "rule of thirds, space on left" prevents unusable crops |
| Color grounding from direction | Visual consistency across all tracks |
| Negative prompt mandatory | Prevents common AI artifacts |
| Purpose-aware style | Deck = clean/minimal, Social = bold/saturated |

---

## Step 4: Tool Detection & Generation

At runtime, detect available tools in priority order:

| Priority | Tool | Detection Method | Notes |
|----------|------|-----------------|-------|
| 1 | MCP image server | Check for `mcp__*__generate_image` in available tools | Full integration |
| 2 | DALL-E 3 via API | `OPENAI_API_KEY` env var present | Highest quality |
| 3 | Local Stable Diffusion | `which sd` or ComfyUI check | Privacy, no cost |
| 4 | **Prompt export** | Always available | User generates elsewhere |

**If no generation tool is available:**
- Save `prompt.json` to `content/{name}/tracks/image/`
- Output instructions: "Paste this prompt into DALL-E / Midjourney / Stable Diffusion"
- Track is still COMPLETE (prompt IS the deliverable for this track)

**If generation tool available:**
- Generate image → save to `content/{name}/tracks/image/{topic}-hero.png`
- Save prompt.json alongside (for regeneration/iteration)

---

## Step 5: Quality Verification (RP-I)

### RP-I1: Prompt Specificity

The prompt must contain:
- [ ] Concrete subject description (not abstract/vague)
- [ ] Style specification (artistic style + lighting)
- [ ] Composition guidance (aspect ratio + focal point)
- [ ] Color grounding (from direction tokens)
- [ ] Negative prompt (at least 3 exclusions)

FAIL if prompt is <50 words or contains only abstract concepts.

### RP-I2: Purpose Alignment

- [ ] Aspect ratio matches declared purpose
- [ ] Style matches audience (professional for leadership, bold for social)
- [ ] If deck illustration: has space for text overlay (composition mentions it)
- [ ] If thumbnail: high contrast, clear focal point

### RP-I3: Brand Consistency

- [ ] Color grounding uses direction tokens (not random colors)
- [ ] Style doesn't conflict with direction's aesthetic (brutalist direction ≠ soft watercolor)
- [ ] No competitor visual references in prompt

### RP-I4: Technical Specs

- [ ] Resolution appropriate for purpose (≥1080px on shortest dimension)
- [ ] Format specified (PNG for transparency-capable, JPG for photos)
- [ ] Quality set to "high" or equivalent

### RP-I5: Content Safety

- [ ] No real people or identifiable faces in prompt
- [ ] No trademarked imagery or logos
- [ ] No violent, explicit, or controversial imagery
- [ ] Prompt would pass mainstream API content filters

### RP-I6: Cross-Track Consistency (if multiple tracks active)

- [ ] Image style matches other track visuals (same direction applied)
- [ ] Color palette coherent with deck/poster/PDF from same run
- [ ] If generating multiple images: consistent style across set

---

## Output Files

```
content/{name}/tracks/image/
├── prompt.json           — structured prompt (ALWAYS produced)
├── {topic}-hero.png      — generated image (if tool available)
└── generation_log.json   — tool used, parameters, timestamp
```

## Integration with Other Tracks

When Track I runs alongside other tracks, it acts as a **supplier**:

| Consumer Track | How it uses Track I output |
|---------------|--------------------------|
| Track E (Deck) | Slide background or section illustration |
| Track F (PDF) | Header image or accent visual |
| Track B (Poster) | Hero image element |
| Track C (Narrative) | Article header/OG image |

The consumer track references `content/{name}/tracks/image/{topic}-hero.png`
directly — no copying needed.
