# Track E: Deck (PPTX)

> Leadership-ready presentations with speaker notes and progressive reveal.
> "I would present this to Rob as-is."

## When This Track Runs

This track executes during BUILD stage when `"deck"` is in `confirmed_tracks` (from
discovery.json). Read this file at BUILD time. You do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — key points, evidence bank, narrative arc
- `content/{name}/PRFAQ.md` — headline, problem, solution, quote (if available)
- `content/{name}/discovery.json` — confirmed scope and audience context
- Active design direction YAML (from Direction Gate) — for color tokens

## Production Flow (Combined Model — revised 2026-05-26)

> Combines Anthropic's PptxGenJS native approach (searchable, editable elements) with
> our Playwright visual rendering (unlimited CSS capability) and OOXML post-processing
> (speaker notes). Plus mandatory Visual QA subagent loop.

```
Step 1: Outline → classify each slide as NATIVE or RENDER

Step 2: Build NATIVE slides via PptxGenJS
        ├── Text elements: addText() with positioning in inches
        ├── Shapes: addShape() for accent bars, backgrounds, frames
        ├── Icons: react-icons → SVG → sharp rasterize → PNG → addImage()
        └── All elements are editable/searchable in PowerPoint

Step 3: Build RENDER slides (complex diagrams only) via Playwright → PNG
        ├── HTML/CSS with full design freedom (flex, grid, gradients, etc.)
        ├── Playwright screenshot at 1920×1080
        └── Insert as full-bleed image via addImage()

Step 4: Assemble final PPTX via PptxGenJS writeFile()

Step 5: OOXML post-processing (speaker notes)
        ├── unpack.py → inject notesSlide XML → pack.py
        └── deck_notes_injector.py handles the XML surgery

Step 6: Visual QA (MANDATORY — subagent inspection loop)
        ├── soffice --convert-to pdf → pdftoppm → slide JPEG images
        ├── Spawn subagent with fresh context to visually inspect
        ├── Fix issues found → re-render affected slides → re-verify
        └── Loop until clean pass (max 3 iterations)

Fallback:
  Tier 1 (full): native + render + notes + QA pass → deliver
  Tier 2 (notes only): if notes injection fails → deliver with speaker_notes.md companion
  Tier 3 (flat): PPTX AS-IS + speaker_notes.md → deliver
  3 attempts → CHECKPOINT (never deliver corrupt file)
```

### Slide Classification: NATIVE vs RENDER

| Slide Content | Classification | Tool | Why |
|---------------|---------------|------|-----|
| Title, headline text | NATIVE | PptxGenJS `addText()` | Editable, searchable |
| Bullet points, quotes, citations | NATIVE | PptxGenJS `addText()` | Editable content |
| Simple shapes (accent bars, backgrounds) | NATIVE | PptxGenJS `addShape()` | Native elements |
| Icons next to text | NATIVE | SVG→PNG + `addImage()` | Text stays editable |
| Diagrams (flowcharts, pyramids) | RENDER | HTML → Playwright → PNG → `addImage()` | CSS flex/grid needed |
| Charts (line, bar, trend) | RENDER | HTML → Playwright → PNG → `addImage()` | Chart.js/CSS |
| Multi-column grids with decorations | RENDER | HTML → Playwright → PNG → `addImage()` | Complex layout |
| Step flows with connectors | RENDER | HTML → Playwright → PNG → `addImage()` | Flexbox needed |

**CRITICAL CLARIFICATION (from adversarial review 2026-05-26):**

There are exactly TWO paths, not three:

1. **NATIVE path:** Write a `.js` build script that directly calls PptxGenJS API (`pres.addSlide()`,
   `slide.addText()`, `slide.addShape()`, `slide.addImage()`). Coordinates in inches. Output: `.pptx`.
   Files: `build_deck.js` in the content directory.

2. **RENDER path:** Write `.html` files (1920×1080 viewport, full CSS). Playwright screenshots → PNG.
   PNGs inserted into the NATIVE-built PPTX via `slide.addImage({path, x:0, y:0, w:10, h:5.625})`.
   Files: `visuals/*.html` → `visuals/*.png`, rendered by `render_visuals.js`.

**python-pptx is NOT used for deck creation.** (It's weaker than PptxGenJS.) If you see
python-pptx references elsewhere in this file, they are outdated — use PptxGenJS.

**html2pptx.js is NOT the primary path.** It's a utility in s_pptx for converting individual
HTML slides to PPTX elements. For Pollinate decks, write PptxGenJS directly — it's simpler
and more reliable (no flex/overflow issues).

A good deck is ~60-70% NATIVE (PptxGenJS direct), ~30-40% RENDER (Playwright PNG).
NATIVE slides = editable. RENDER slides = visually guaranteed but locked as images.

### PptxGenJS Usage (NATIVE slides)

The primary creation library. Coordinates are in inches. 16:9 = 10" × 5.625".

```javascript
const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';

let slide = pres.addSlide();

// Text with positioning
slide.addText("Headline", {
  x: 0.8, y: 0.6, w: 8.4, h: 1,
  fontSize: 36, fontFace: "Inter", color: "FFFFFF", bold: true
});

// Shape (accent bar, background block)
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 10, h: 5.625,
  fill: { color: "0A0A0B" }
});

// Icon (rasterized SVG → PNG)
slide.addImage({ path: "icons/compass.png", x: 0.8, y: 2, w: 0.5, h: 0.5 });

// Bullet list
slide.addText([
  { text: "First point", options: { bullet: true, breakLine: true } },
  { text: "Second point", options: { bullet: true, breakLine: true } },
  { text: "Third point", options: { bullet: true } }
], { x: 0.8, y: 2.5, w: 8, h: 2.5, fontSize: 16, color: "B3B3B3" });

await pres.writeFile({ fileName: "output.pptx" });
```

**Key rules for PptxGenJS:**
- Coordinates in inches (not px, not pt)
- Colors as 6-char hex WITHOUT # prefix: `"D4A853"` not `"#D4A853"`
- `breakLine: true` for multi-line text arrays (last item doesn't need it)
- `margin: 0` when aligning text precisely with shapes
- `charSpacing` for letter-spacing (NOT `letterSpacing` which is silently ignored)
- Gradient fills not supported natively → use gradient image as background
- Shadow `offset` must be non-negative (negative corrupts file)

### Playwright Rendering (RENDER slides)

For slides that need complex visual layout (diagrams, flowcharts, grids):

```bash
# Each visual slide is an HTML file (1920×1080 viewport)
# Playwright screenshots it to PNG, which is then inserted as full-bleed image

node render_visuals.js  # renders all visuals/*.html → visuals/*.png
```

HTML rules for RENDER slides:
- Body dimensions: `width: 1920px; height: 1080px`
- Use full CSS capability: flex, grid, gradients, border-radius, etc.
- Font loading: use system fonts (Inter, PingFang SC) — they're on the build machine
- Dark theme: match direction tokens (#0A0A0B bg, #D4A853 accent for D1)
- Output PNG is inserted at `{ x: 0, y: 0, w: 10, h: 5.625 }` (full slide)

### Visual QA Loop (MANDATORY — from Anthropic best practice)

> "Assume there are problems. Your job is to find them."
> Your first render is almost never correct.

**After building the deck, BEFORE delivering:**

```bash
# Convert PPTX to individual slide JPEGs
soffice --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide
# Creates slide-01.jpg, slide-02.jpg, etc.
```

**Then spawn a subagent for visual inspection:**

```
Visually inspect these slides. Assume there are issues — find them.

Look for:
- Overlapping elements (text through shapes, stacked elements)
- Text overflow or cut off at edges
- Elements too close (< 0.3" gaps) or nearly touching
- Uneven gaps (large empty area vs cramped)
- Insufficient margin from slide edges (< 0.5")
- Low-contrast text or icons
- Leftover placeholder content
- Misaligned columns or elements

For each slide, list issues found. Even minor ones.

[Read slide-01.jpg, slide-02.jpg, ...]
```

**Fix-and-verify loop:**
1. Subagent reports issues
2. Fix each issue in the source (PptxGenJS code or HTML)
3. Rebuild affected slides
4. Re-convert to JPEG, re-inspect fixed slides only
5. Repeat until clean pass (max 3 iterations)

**Do NOT deliver until Visual QA passes with zero blocking issues.**

### Speaker Notes Injection (OOXML Post-Processing)

After QA pass, inject speaker notes:

```bash
OOXML_SCRIPTS="$HOME/.swarm-ai/SwarmWS/.claude/skills/s_pptx/ooxml/scripts"
INJECT_SCRIPT="$HOME/.swarm-ai/SwarmWS/.claude/skills/s_pollinate/scripts/deck_notes_injector.py"

# Unpack
python3 "$OOXML_SCRIPTS/unpack.py" output.pptx _unpacked/

# Inject notes (from notes.json — slide number → notes text)
python3 "$INJECT_SCRIPT" _unpacked/ --notes notes.json --validate --json

# Repack
python3 "$OOXML_SCRIPTS/pack.py" _unpacked/ output.pptx

# Clean up
rm -rf _unpacked/
```

**notes.json format:**
```json
[
  {"slide": 1, "notes": "Full speaker notes for slide 1. Multiple sentences. Talk about X."},
  {"slide": 2, "notes": "Notes for slide 2. Emphasize Y. Anticipate question about Z."}
]
```

### Brand Compliance (Final Slide) — opt-in, from identity.yaml

If THIS DDD configures branding (identity.yaml + env), the final/CTA slide MAY include:
- your brand mark (`{{EMOJI}} {{PROJECT_NAME}}` from identity.yaml)
- `{{PROJECT_LINK}}` (visible text — omit if none)
- QR code — only if you supply your own asset under `brand/assets/logo/` + `POLLINATE_REQUIRE_QR=1`
- Watermark: `{{WATERMARK_TEXT}}` (small, bottom — only if `$POLLINATE_WATERMARK` set)

Unbranded is valid: with nothing configured, ship a clean CTA slide (no SwarmAI mark).

### Why This Combined Approach

| Approach | Strength | Weakness |
|----------|----------|----------|
| PptxGenJS native only | Editable, searchable, native elements | Can't do complex diagrams/gradients |
| Playwright PNG only | Unlimited visual capability | Images not editable, text not searchable |
| python-pptx only | Simple API, Python ecosystem | Even weaker than PptxGenJS for visual work |
| **Combined (this track)** | Native where possible + visual where needed + QA guarantees quality | Slightly more complex build process |

The combined approach matches what Anthropic's official pptx skill does ("use gradient
image as background instead" for complex visuals) plus adds what they don't have: speaker
notes injection and the Pollinate content pipeline upstream.

---

## Step 1: Determine Slide Structure

### From Content Package to Slide Outline

Read `content_package.md` and extract:
1. **Core thesis** → Title slide headline
2. **Key points** (numbered list) → Each becomes 1 slide (or more if complex)
3. **Evidence bank** → Data slides (chart + insight sentence each)
4. **Narrative arc** → Determines ordering and transitions

### Structure Template Selection

Based on `discovery.json` audience + outcome, select the closest reference pattern:

| Audience + Outcome | Template | Typical Flow |
|-------------------|----------|-------------|
| Leadership + alignment | `pitch` | Title → Problem → Solution → Evidence × N → Ask → Backup |
| Community + education | `conference` | Title → Hook → Context → Insight × N → Implications → CTA |
| Leadership + data_decision | `mbr` | Title → Exec Summary → Metrics → Deep dives × N → Actions |
| Customer + action | `proposal` | Title → Pain → Vision → How → Proof × N → Next Steps |

**Content drives slide count.** If content_package has 8 key points, produce ~12-15 slides
(key points + framing slides). If it has 3, produce ~6-8. NEVER compress to fit a number.

### Slide-by-Slide Outline (write before any production)

Create the outline as a numbered list in `content/{name}/tracks/deck/outline.md`:

```markdown
# Deck Outline: {topic}

Template: pitch
Direction: D1 Obsidian
Total slides: {N}

1. **Title** — "{headline}" | Type: TEXT | Speaker note: introduce self + context
2. **Hook** — "{attention grabber}" | Type: VISUAL (escalation/flow diagram) | Speaker note: walk through it
3. **Problem** — "{problem statement}" | Type: TEXT | Speaker note: why this matters now
4. **Model** — "{core framework}" | Type: VISUAL (diagram/pyramid) | Speaker note: explain architecture
5. **Detail 1** — "{deep dive}" | Type: TEXT | Speaker note: expand with example
...
N-1. **Steps** — "{action items}" | Type: VISUAL (step flow) | Speaker note: make it concrete
N. **CTA** — "{what to do next}" | Type: TEXT + QR | Speaker note: give clear next step
```

**Every line MUST include:**
- Speaker note seed (becomes notesSlide content)
- Type classification: `TEXT` (python-pptx native) or `VISUAL` (HTML → Playwright → PNG)

**Classification guide:**
- TEXT: titles, bullet lists, quotes, citations, simple data → python-pptx
- VISUAL: diagrams, flowcharts, pyramids, step flows, comparison grids, charts → HTML render

**Rule: ≥30% of body slides should be VISUAL.** A deck with 100% text slides is a document,
not a presentation. Visual slides are what make social sharing work (self-explanatory without
a presenter).

---

## Step 2: Create Slide HTML Files

### HTML Per Slide

For each slide in the outline, create an HTML file following the html2pptx format:

```html
<!-- slide_{N}.html — {slide_title} -->
<body style="width: 960px; height: 540px; margin: 0; padding: 0; background: {bg_color};">
  <!-- Content using <p>, <h1>-<h6>, <ul>, <ol> only -->
  <!-- Images via <img> with absolute positioning -->
  <!-- Shapes via positioned <div> with background-color -->
</body>
```

**Dimensions:** 960×540px (maps to 10"×5.63" at 96dpi = standard 16:9)

### Brand Compliance (from direction YAML)

Load the active direction's tokens. Map to slide colors:

| Direction Token | PPTX Usage |
|----------------|-----------|
| `bg-deep` | Title slide background, section divider backgrounds |
| `bg-elevated` | Content slide background (if dark direction) |
| `accent` | Headlines, key data highlights, accent bars |
| `text-primary` | Headline text color |
| `text-secondary` | Body text, bullet points |
| `text-muted` | Captions, metadata, slide numbers |

For light directions (D2 Paper, D10+): invert — `bg-deep` for text, white/cream for backgrounds.

### html2pptx.js Execution

```bash
PPTX_SCRIPTS="$HOME/.swarm-ai/SwarmWS/.claude/skills/s_pptx/scripts"

# Create a build script that:
# 1. Imports pptxgenjs
# 2. Sets layout to LAYOUT_16x9
# 3. For each slide HTML file, calls html2pptx() to add the slide
# 4. Writes output to base.pptx

node build_deck.js \
  --slides "content/{name}/tracks/deck/slides/" \
  --output "content/{name}/tracks/deck/base.pptx"
```

The build_deck.js script follows the pattern in `html2pptx.md`:
```javascript
const pptxgen = require('pptxgenjs');
const { html2pptx } = require('{PPTX_SCRIPTS}/html2pptx.js');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_16x9';

// For each slide HTML file (ordered by slide number):
const slideFiles = fs.readdirSync(slidesDir)
  .filter(f => f.startsWith('slide_') && f.endsWith('.html'))
  .sort((a, b) => parseInt(a.match(/\d+/)) - parseInt(b.match(/\d+/)));

for (const file of slideFiles) {
  await html2pptx(path.join(slidesDir, file), pptx);
}

await pptx.writeFile(outputPath);
```

---

## Step 3: OOXML Post-Processing (Speaker Notes + Animations)

### Unpack

```bash
OOXML_SCRIPTS="$HOME/.swarm-ai/SwarmWS/.claude/skills/s_pptx/ooxml/scripts"

python3 "$OOXML_SCRIPTS/unpack.py" \
  "content/{name}/tracks/deck/base.pptx" \
  "content/{name}/tracks/deck/_unpacked"
```

### Inject Speaker Notes

For each slide N, create `ppt/notesSlides/notesSlide{N}.xml`:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
         xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
         xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr/>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Notes Placeholder"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
        </p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p>
            <a:r>
              <a:rPr lang="zh-CN" dirty="0"/>
              <a:t>{SPEAKER_NOTES_TEXT}</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:notes>
```

### Register Notes in Relationships

For each slide, add to `ppt/slides/_rels/slide{N}.xml.rels`:
```xml
<Relationship Id="rId_notes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{N}.xml"/>
```

Add to `[Content_Types].xml`:
```xml
<Override PartName="/ppt/notesSlides/notesSlide{N}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>
```

### Inject Progressive Reveal (Animations)

For slides with bullet lists (multi-point content), add timing to make bullets appear
on click. Add to `ppt/slides/slide{N}.xml` inside the `<p:sld>` element:

```xml
<p:timing>
  <p:tnLst>
    <p:par>
      <p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">
        <p:childTnLst>
          <p:seq concurrent="1" nextAc="seek">
            <p:cTn id="2" dur="indefinite" nodeType="mainSeq">
              <p:childTnLst>
                <!-- One <p:par> per bullet/element to reveal -->
                <p:par>
                  <p:cTn id="3" fill="hold">
                    <p:stCondLst>
                      <p:cond evt="onClick" delay="0">
                        <p:tgtEl>
                          <p:sndTgt r:embed=""/>
                        </p:tgtEl>
                      </p:cond>
                    </p:stCondLst>
                    <p:childTnLst>
                      <p:par>
                        <p:cTn id="4" fill="hold">
                          <p:childTnLst>
                            <p:par>
                              <p:cTn id="5" presetID="1" presetClass="entr" presetSubtype="0" fill="hold" nodeType="clickEffect">
                                <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                                <p:childTnLst>
                                  <p:set>
                                    <p:cBhvr>
                                      <p:cTn id="6" dur="1" fill="hold">
                                        <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                                      </p:cTn>
                                      <p:tgtEl>
                                        <p:spTgt spid="{SHAPE_ID}">
                                          <p:txEl>
                                            <p:pRg st="{PARA_START}" end="{PARA_END}"/>
                                          </p:txEl>
                                        </p:spTgt>
                                      </p:tgtEl>
                                      <p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>
                                    </p:cBhvr>
                                    <p:to><p:strVal val="visible"/></p:to>
                                  </p:set>
                                </p:childTnLst>
                              </p:cTn>
                            </p:par>
                          </p:childTnLst>
                        </p:cTn>
                      </p:par>
                    </p:childTnLst>
                  </p:cTn>
                </p:par>
              </p:childTnLst>
            </p:cTn>
          </p:seq>
        </p:childTnLst>
      </p:cTn>
    </p:par>
  </p:tnLst>
</p:timing>
```

**Important:** `{SHAPE_ID}` must match the `spid` of the text shape in that slide's XML.
`{PARA_START}` and `{PARA_END}` index the paragraph range (0-based) for each bullet.

### Which Slides Get Animations

- Title slide: NO animation (static)
- Single-point slides (hero statement, full-bleed image): NO animation
- Multi-bullet slides (2+ points): YES — each bullet appears on click
- Chart + insight slides: YES — chart first, then insight text

**Target: ≥50% of body slides have progressive reveal (RP-E2).**

### Repack and Validate

```bash
# Repack
python3 "$OOXML_SCRIPTS/pack.py" \
  "content/{name}/tracks/deck/_unpacked" \
  "content/{name}/tracks/deck/{topic}.pptx"

# Validate
python3 "$PPTX_SCRIPTS/inventory.py" \
  "content/{name}/tracks/deck/{topic}.pptx" \
  --json > "content/{name}/tracks/deck/inventory.json"

# Check inventory for completeness
# Every slide from outline.md must appear in inventory
# All text must be intact (no truncation from XML editing)
```

---

## Step 4: Generate Standalone Speaker Notes

Always produce `content/{name}/tracks/deck/speaker_notes.md` regardless of OOXML success:

```markdown
# Speaker Notes: {topic}

## Slide 1: {title}
{Full talking points — 3-5 sentences. Not bullet repeats.
Include: what to say, what to emphasize, potential questions to anticipate.}

## Slide 2: {title}
{...}

...
```

**This file serves dual purpose:**
1. Tier 3 fallback companion (if OOXML fails)
2. Standalone reference for the presenter to review before the meeting

---

## Step 5: Quality Verification (RP-E)

Run ALL 8 patterns before declaring Track E done:

| # | Pattern | How to Verify | Gate |
|---|---------|--------------|------|
| RP-E1 | Speaker notes present | Every slide has notesSlide in OOXML OR speaker_notes.md covers all slides | BLOCKING |
| RP-E2 | Visual QA pass | Subagent inspected slide JPEGs and found zero blocking issues | BLOCKING |
| RP-E3 | Content completeness | Compare content_package key points vs outline.md slides. Every key point has ≥1 slide | BLOCKING |
| RP-E4 | 1-idea rule | No slide has >3 bullet items or >3 distinct text blocks simultaneously | BLOCKING |
| RP-E5 | Data + insight | Chart slides: one chart + one insight sentence (not 5 bullets explaining data) | BLOCKING |
| RP-E6 | Brand consistency | All colors match direction tokens. No random hex values. | BLOCKING |
| RP-E7 | Font compliance | Only brand fonts: Inter, PingFang SC, SF Mono | BLOCKING |
| RP-E8 | No AI anti-patterns | No accent lines under titles. Layout variety across slides. Visual elements on every slide. | BLOCKING |

**All 8 must pass before delivery. Any failure → fix and re-verify.**

### Visual QA is NON-NEGOTIABLE

RP-E2 (Visual QA) is the most important gate. It catches what code review cannot:
- Overlapping elements that look fine in code but break visually
- Color contrast that passes hex matching but fails visual readability
- Layout that seems correct by coordinates but feels cramped/unbalanced
- Text that wrapped unexpectedly due to font metrics

**The subagent has fresh eyes. You do not.** You wrote the code and will see what you
expect, not what's there. This is the same pattern as poster adversarial review — proven
to catch issues that self-review structurally misses.

---

## Fallback Tiers (when OOXML post-processing fails)

### Tier 1 Failure (notes + animations → validate FAIL)

```
1. Preserve _unpacked/ directory as evidence
2. Re-unpack from base.pptx (fresh start)
3. Inject ONLY notesSlide XML (no <p:timing> blocks)
4. Repack → validate
5. If passes → deliver with note: "Progressive reveal not applied (Tier 2)"
```

### Tier 2 Failure (notes only → validate FAIL)

```
1. Deliver base.pptx AS-IS (the flat deck from html2pptx.js)
2. Deliver speaker_notes.md as companion document
3. Note to user: "Speaker notes in separate file (Tier 3). Open both during presentation."
```

### Tier 3 + All Exhausted → CHECKPOINT

```
Pipeline PAUSED at BUILD/Track E (run_p_{id})
Reason: OOXML post-processing failed 3 times. base.pptx may be invalid.
Evidence: _unpacked/ directory preserved for debugging.

Do NOT deliver any .pptx file to user. Wait for manual investigation.
```

---

## Output Files

| File | Always? | Content |
|------|---------|---------|
| `tracks/deck/outline.md` | ✅ | Slide-by-slide plan with speaker note seeds |
| `tracks/deck/slides/slide_{N}.html` | ✅ | Source HTML for each slide |
| `tracks/deck/base.pptx` | ✅ | Flat deck (Step 1 output) |
| `tracks/deck/{topic}.pptx` | Tier 1/2 | Final deck with notes + animations |
| `tracks/deck/speaker_notes.md` | ✅ | Standalone notes document |
| `tracks/deck/inventory.json` | ✅ | Validation output |
| `tracks/deck/_unpacked/` | On failure | XML evidence for debugging |

---

## Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Skip speaker notes, they can add later" | Notes are MANDATORY (RP-E1). No deck delivers without notes. |
| "3 bullets is close enough to the 1-idea rule" | 3 is the MAX. 4+ = split into 2 slides. |
| "Animations are cosmetic, skip them" | Progressive reveal is a quality signal (RP-E2). Try Tier 1 first. |
| "Content_package only has 3 points, deck seems short" | 3 points = 6-8 slides (framing + evidence). Short is fine if complete. |
| "OOXML is too complex, just deliver base.pptx" | Try Tier 1 and Tier 2 first. Tier 3 is last resort, not first choice. |
| "Fonts look fine in the browser" | Verify font-family strings match exactly. "PingFang" ≠ "PingFang SC". |
| "validate.py passed so it's fine" | validate.py checks structure. inventory.py checks content. Run BOTH. |
