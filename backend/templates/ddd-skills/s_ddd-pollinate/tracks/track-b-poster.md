# Track B: Poster

> Branded poster/长图 PNG(s) via Playwright HTML→PNG render. ALWAYS 2 direction
> variants. 8-Layer quality convergence gate + adversarial brand review (blocking).

## When This Track Runs

Executes during BUILD when `"poster"` is in `confirmed_tracks` (discovery.json).
Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

(Legacy note: older strategy.json used `production_tracks`; STRATEGIZE copies
`confirmed_tracks` into it for back-compat — they are always equal. Scope is
authoritatively `confirmed_tracks`.)

---

#### Step B.1: Load Design System

```
Read brand/poster_design_system.md
```

This is MANDATORY before writing any poster HTML. Do not skip. The design system
defines spacing tokens, typography scale, alignment rules, and anti-patterns.

#### Step B.2: Select Directions (ALWAYS 2 variants)

**MANDATORY: Every poster run produces exactly 2 direction variants.**

Select 2 best-fit directions from the content:
1. Read each direction's `content_triggers` in `brand/directions/d{N}-{name}.yaml`
2. Score content against triggers (strong=3, moderate=2, weak=1)
3. Top 2 scores → selected directions
4. If user specified a direction → that's #1, auto-select #2 based on content contrast

**Direction selection decision: Mechanical** (score-based from triggers).

#### Step B.3: Author HTML (per direction)

For EACH selected direction, create `content/{name}/tracks/poster/{topic}-{direction}.html`:

- Load direction's `css_snippet` into `:root {}` block
- Add `<!-- Direction: D{N} {name} -->` as first HTML comment
- ALL colors via `var(--token)` — **ZERO hardcoded hex in body**
- ALL text-align: center (no exceptions, no mixed alignment)
- Section gaps ≤ 72px (no explicit dividers between sections)
- Typography: headline 52px / body 22px / eyebrow 12px
- Card variety: NEVER same style consecutively (per direction's `card_styles`)
- Chinese body line-height: 2.0 minimum
- Max text width: 700px
- Include Branding ONLY if configured (see `poster_design_system.md` — opt-in):
  - Footer section with `{{EMOJI}} {{PROJECT_NAME}}` + `{{TAGLINE}}` + (optional) QR + `{{PROJECT_LINK}}`
  - Watermark: `{{WATERMARK_TEXT}}` at bottom-right (only if `$POLLINATE_WATERMARK` set)
  - With nothing configured → ship an unbranded poster (valid; L7 auto-passes)

**QR code (only if you opt in with your own asset under `brand/assets/logo/`):**
- Light bg directions (D2/D3/D5): your dark-on-light QR
- Dark bg directions (D1/D4): your light-on-dark QR

#### Step B.4: Render to PNG (per direction)

```bash
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1080, 'height': 800})
    page.goto('file://{absolute_path_to_html}')
    page.wait_for_timeout(800)
    page.screenshot(path='{output_png}', type='png', full_page=True)
    browser.close()
"
```

#### Step B.5: 8-Layer Quality Convergence Loop (BLOCKING)

> "Content as Black Box" — same principle as Pipeline's 6-Layer Push-Ready Gate.
> Output must pass ALL 8 layers simultaneously before reaching the user.
> A poster passing 7/8 is NOT publish-ready.

**For EACH rendered variant, run all 8 layers:**

| Layer | Gate | What to Verify | RP-P | Auto-fixable? |
|-------|------|---------------|------|---------------|
| L1 | Direction Declared | HTML has `<!-- Direction: D{N} -->` comment | RP-P1 | ✅ Inject comment |
| L2 | Token Purity | Zero hardcoded hex values in `<style>` body (outside `:root`) | RP-P2 | ✅ Replace with var() |
| L3 | Spacing Compliance | All section gaps ≤ 72px when rendered | RP-P3 | ⚠️ Reduce padding |
| L4 | Alignment Unity | ALL text elements use text-align: center | RP-P4 | ✅ Force center |
| L5 | Anti-Slop Clean | Zero violations against Visual + Structural Ban Lists | RP-P5 | ⚠️ Regenerate section |
| L6 | Platform Fit | Output width = 1080px, file < 2MB | RP-P6 | ✅ Re-render viewport |
| L7 | Brand Present (opt-in — configured) | Watermark `{{WATERMARK_TEXT}}` + QR code linking to `{{PROJECT_LINK}}` + GitHub URL text — ALL must exist in DOM. (only when branding is configured for this DDD) | RP-P7 | ✅ Append template |
| L8 | 2-Variant Output | ≥ 2 direction PNGs rendered | — | ✅ Render second |

**Convergence Loop:**

```
LOOP (max 3 iterations):
  1. Run all 8 layers against rendered PNG + source HTML
  2. IF all 8 PASS → exit loop, proceed to Content Principles Check
  3. IF failures exist:
     a. Auto-fixable (L1,L2,L4,L6,L7,L8): fix HTML in-place
     b. Semi-fixable (L3,L5): adjust CSS values
     c. Re-render via Playwright
     d. Re-verify ALL 8 layers (fix may introduce new failures)
  4. Increment iteration counter

EXIT CONDITIONS:
  - All 8 pass → PUBLISH-READY (proceed)
  - 3 iterations without convergence → show best version + flag remaining issues

CRITICAL: The loop runs BEFORE the user sees anything.
The user receives only publish-ready output.
```

**Automated enforcement script (run BEFORE manual checks):**

```bash
python "$SKILL_DIR/scripts/convergence_gate.py" "content/{name}/tracks/poster/{topic}-{direction}.html" --json
```

Returns JSON: `{"valid": true/false, "checks_passed": N, "checks_total": 8, "errors": [...], "layers": {...}}`
Exit 0 = all 8 pass. Exit 1 = failures listed. Run this FIRST — it catches mechanical
violations instantly. Only proceed to manual verification for issues the script cannot
detect (visual aesthetics, content quality).

**Verification methods (manual fallback for when script is unavailable):**

L1: `grep "<!-- Direction:" {html_file}`
L2: After `</style>`, scan body for raw hex: `#[0-9a-fA-F]{3,8}` → 0 matches (exclude img src paths)
L3: Check section padding values in CSS (all ≤ 72px for between-section gaps)
L4: **TWO checks required:**
    (a) All section/container CSS classes (.s, .hero, .card, section) MUST have explicit `text-align:center` in their class definition — never rely on inheritance or browser default (which is left)
    (b) `grep "text-align" {html}` → only "center" and "right" (watermark only). Any "left" or "justify" → FAIL
    WHY BOTH: A class without text-align declaration inherits browser default (left). Grepping only for declared values misses this. The fix is to require explicit declaration on every container.
L5: Scan against `poster_design_system.md` ban lists (32 visual + 13 structural)
L6: Rendered PNG width = 1080px AND file size < 2MB
L7: opt-in — only if branding configured: `grep "$POLLINATE_WATERMARK" {html}` (+ QR when `POLLINATE_REQUIRE_QR=1`). Unset → L7 auto-passes.
L8: Count `*-d*.png` files in output directory ≥ 2

#### Step B.5b: Adversarial Brand Review (BLOCKING — sub-agent)

> Same pattern as Pipeline's adversarial specialist dispatch (deliver.md).
> Fresh-context sub-agent catches what self-review structurally cannot:
> brand drift, unconscious style mixing, spacing assumptions from builder bias.

**After the 8-Layer Gate passes (all 8 green), spawn an adversarial sub-agent:**

Use the Agent tool with a **fresh context** (the sub-agent has NOT seen the
BUILD process — it reviews cold, like an external brand auditor).

**Sub-agent prompt template:**

```
You are a brand consistency reviewer for THIS DDD's Pollinate output (brand = identity.yaml).
Review this poster HTML against the brand system and quality patterns.
Be specific: element/line, what's wrong, how to fix.

## Brand System (source of truth)
<paste contents of brand/poster_design_system.md>

## Content Principles
<paste contents of brand/content_principles.md>

## Quality Patterns (RP-P1~P7)
<paste REVIEW_PATTERNS.md poster section>

## Poster HTML to Review
<paste the rendered HTML source for EACH variant>

## Output
For each finding, output:
- Severity: HIGH (publish-blocking) / MED (should fix) / LOW (polish)
- Element: CSS selector or text content that violates
- Rule: which RP-P# or brand rule is violated
- Fix: specific CSS/HTML change to resolve

If no findings: output `BRAND CLEAN — no violations detected`.

Focus on:
1. Direction mixing (elements from one direction bleeding into another)
2. Spacing/alignment drift (gaps > 72px, non-centered text)
3. Color token violations (hardcoded values that passed L2 somehow)
4. Content principle violations (P1-P8 in copy text)
5. Typography scale violations (font sizes outside design system range)
6. Visual ban list items that may have been missed by mechanical check
```

**Sub-agent configuration:**
- Use default model (opus for strongest visual reasoning)
- Do NOT run in background — must complete before DELIVER
- If sub-agent returns findings:
  - HIGH severity → fix in HTML, re-render, re-verify 8-Layer Gate
  - MED severity → fix if convergence iterations remain, else note in REPORT
  - LOW severity → note only
- If sub-agent returns `BRAND CLEAN` → proceed to Content Principles Check

**Why this exists:** The builder (you) wrote the HTML and can't see your own
assumptions. You might use the correct token variable but the visual result
clashes. You might declare Direction D4 but unconsciously use D5's spacing
rhythm. The 8-Layer Gate catches MECHANICAL violations (hex values, missing
elements). The sub-agent catches AESTHETIC violations (does this LOOK like the
declared direction? does the spacing FEEL consistent?).

**Record results in convergence-log.txt:**
```
Adversarial Brand Review:
  Spawned: yes
  Findings: N (H:X M:Y L:Z)
  Fixed: N | Noted: N
  Status: CLEAN / FIXED / ESCALATED
```

---

#### Step B.6: Content Principles Check (external content only)

Read `brand/content_principles.md` and run anti-pattern scan on poster text:
- No LOC/commit/天数 as value (P1)
- No first-person hero framing (P2) — **MECHANICAL GATE: run `scripts/p2_scan.py`**
- Thesis-driven, not feature-driven (P3)
- Effects over mechanisms (P4)
- English only where stronger than Chinese (P5)
- Each piece standalone (P6)
- No internal术语 in body text (P7)
- Positioning hierarchy respected (P8)

**P2 Hero Framing Gate (BLOCKING — L2 mechanical enforcement):**
```bash
# Scan poster HTML for hero framing (script auto-strips HTML tags/style/script)
python3 scripts/p2_scan.py {html_file}
```
Exit 0 = clean. Exit 1 = FAIL — fix offending text before proceeding.
Targets: "我造了/我做了/我们是最.../我们的X远超" hero claims.
Does NOT flag: section headers ("## 我们的设计哲学"), technical discussion, thesis statements.

**Legacy term blocklist (mechanical check):**
```
BLOCKED = ["Your AI Team, 24/7", "AI 实践者，不是布道者"]
```
Any match → FAIL → fix before proceeding.

#### Poster Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Direction selection | Mechanical | Score-based from content_triggers |
| Card layout choice | Taste | From direction's card_styles |
| Visual element style | Taste | From direction's visual_elements |
| QR code variant | Mechanical | Light/dark based on direction bg |
| Color token usage | Mechanical | Must be var(--token), zero hardcoded |

#### Poster Output Files (per run)

- `content/{name}/tracks/poster/{topic}-d{N}-{name}.html` -- source (2 files)
- `content/{name}/tracks/poster/{topic}-d{N}-{name}.png` -- rendered (2 files)
- `content/{name}/tracks/poster/convergence-log.txt` -- gate results per iteration

#### Step B.7: Delegation Fidelity Check (Reskin/Restyle Operations)

**BLOCKING — applies whenever a poster is reskinned from one direction to another.**

When restyling existing content (e.g., "reskin D2 → D5"), the output MUST preserve
≥90% of the source content's structural sections. Content truncation during style
changes is the #1 delegation failure mode (C024, 2026-05-16: all 4 posters were
truncated by 40-60% during D5 reskin).

**Verification procedure (after reskin):**
```bash
# Count sections in source HTML (grep -E for alternation on macOS BSD grep)
SOURCE_SECTIONS=$(grep -Ec '<section|<div class="s|<div class="card' {source_html})
# Count sections in output HTML
OUTPUT_SECTIONS=$(grep -Ec '<section|<div class="s|<div class="card' {output_html})
# Calculate ratio
RATIO=$(python3 -c "print(f'{$OUTPUT_SECTIONS / max($SOURCE_SECTIONS, 1) * 100:.0f}%')")
echo "Delegation fidelity: $OUTPUT_SECTIONS / $SOURCE_SECTIONS sections = $RATIO"
```

**Gate:**
- Ratio ≥ 90% → PASS (minor structural simplification is acceptable)
- Ratio < 90% → **FAIL** — content was truncated. Regenerate from source, preserving
  ALL section content. Only change: CSS tokens, colors, typography.
- Ratio > 110% → WARN — content was added (may be intentional for enrichment)

**When this fires:**
- "Reskin to D5 style" / "switch direction" / "restyle"
- Any operation that takes existing HTML as input and produces restyled output
- Sub-agent delegation for reskin tasks

**Why this exists:** Agent delegation (sub-agent or same-agent multi-pass) systematically
truncates content when given a style-change task. The agent optimizes for visual
coherence in the new style and unconsciously drops sections that feel "redundant"
in the new layout. This gate makes truncation mechanically detectable.

