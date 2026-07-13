# Track F: PDF Document / One-Pager

> Branded PDFs — from scannable one-pagers to comprehensive white papers.
> Content completeness drives length. User drives constraints.

## When This Track Runs

This track executes during BUILD stage when `"one_pager"` or `"full_pdf"` is in
`confirmed_tracks` (from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — key points, evidence bank, narrative
- `content/{name}/PRFAQ.md` — headline, problem, solution (one-pager skeleton)
- `content/{name}/discovery.json` — audience, outcome, confirmed scope
- Active design direction YAML — for color tokens

## Two Modes

| Mode | Track name in discovery.json | Directory | Constraint |
|------|------------------------------|-----------|-----------|
| **One-pager** | `one_pager` | `tracks/pdf/` | Single A4/Letter page |
| **Full PDF** | `full_pdf` | `tracks/pdf/` | Content-driven, no limit |

Both modes share the same directory (`tracks/pdf/`) and the same Track F file.
The mode is determined by which track name appears in `confirmed_tracks`.

**If BOTH are in confirmed_tracks:** produce two files with distinct names:
- `{topic}-onepager.pdf` (single page, scannable)
- `{topic}-full.pdf` (multi-page, comprehensive)

Never overwrite one with the other.

---

## Production Flow

```
Step 1: Determine mode (one-pager vs full PDF) from discovery.json

Step 2: Author HTML with branded CSS
        - Direction tokens → CSS custom properties
        - One-pager: fixed layout template (header/hero/proofs/mechanism/CTA)
        - Full PDF: flowing sections with branded dividers + TOC

Step 3: Render via Playwright
        - playwright.chromium → page.pdf() with A4/Letter format
        - One-pager: verify single page output
        - Full PDF: let content flow naturally

Step 4: Generate preview PNG (for chat inline display)
        - Reuse s_pdf/scripts/convert_pdf_to_images.py

Step 5: Quality verification (RP-F)
```

---

## Step 1: Mode Detection

Read `discovery.json`:
- If `confirmed_tracks` contains `"one_pager"` → one-pager mode
- If `confirmed_tracks` contains `"full_pdf"` → full PDF mode
- If ambiguous, check context: customer email = one-pager, publication = full PDF

---

## Step 2: Author HTML

### Direction Token Mapping (CSS)

Load the active direction YAML and inject tokens:

```css
:root {
  --bg-deep: {tokens.bg-deep};
  --bg-elevated: {tokens.bg-elevated};
  --accent: {tokens.accent};
  --text-primary: {tokens.text-primary};
  --text-secondary: {tokens.text-secondary};
  --text-muted: {tokens.text-muted};
  --border: {tokens.border};
}
```

### One-Pager Layout Template

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @page { size: A4; margin: 0; }
  body {
    width: 210mm; height: 297mm;
    margin: 0; padding: 0;
    font-family: 'Inter', 'PingFang SC', sans-serif;
    background: var(--bg-deep);
    color: var(--text-primary);
    display: flex; flex-direction: column;
  }
  .header { height: 60px; padding: 16px 40px; display: flex; align-items: center; }
  .hero { padding: 40px; text-align: center; }
  .hero h1 { font-size: 28pt; font-weight: 700; margin: 0 0 12px; }
  .hero .subhead { font-size: 14pt; color: var(--text-secondary); }
  .proofs { display: flex; gap: 24px; padding: 0 40px; flex: 1; }
  .proof-card {
    flex: 1; background: var(--bg-elevated);
    border-radius: 8px; padding: 24px;
    border-top: 3px solid var(--accent);
  }
  .proof-card .metric { font-size: 24pt; font-weight: 700; color: var(--accent); }
  .proof-card .label { font-size: 11pt; color: var(--text-muted); margin-top: 8px; }
  .mechanism { padding: 32px 40px; }
  .footer {
    height: 80px; padding: 16px 40px;
    display: flex; align-items: center; justify-content: space-between;
    border-top: 1px solid var(--border);
  }
  .cta { font-size: 14pt; font-weight: 600; color: var(--accent); }
</style>
</head>
<body>
  <div class="header">
    <span style="font-size: 14pt;">🐝 SwarmAI</span>
    <span style="margin-left: auto; font-size: 10pt; color: var(--text-muted);">{date}</span>
  </div>
  <div class="hero">
    <h1>{headline from PRFAQ}</h1>
    <p class="subhead">{problem in ≤15 words}</p>
  </div>
  <div class="proofs">
    <div class="proof-card">
      <div class="metric">{number/quote}</div>
      <div class="label">{what it means}</div>
    </div>
    <div class="proof-card">
      <div class="metric">{number/quote}</div>
      <div class="label">{what it means}</div>
    </div>
    <div class="proof-card">
      <div class="metric">{number/quote}</div>
      <div class="label">{what it means}</div>
    </div>
  </div>
  <div class="mechanism">
    <h3>How It Works</h3>
    <!-- 3-step visual or brief explanation -->
  </div>
  <div class="footer">
    <span class="cta">{Call to action — specific next step}</span>
    <span>github.com/xg-gh-25/SwarmAI</span>
    <!-- QR code image -->
  </div>
</body>
</html>
```

### Full PDF Layout

For full PDF, use flowing HTML (no fixed height constraint):

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @page { size: A4; margin: 30mm 25mm; }
  body { font-family: 'Inter', 'PingFang SC', sans-serif; color: #2D3436; }
  .page-header {
    position: fixed; top: 0; left: 0; right: 0;
    padding: 12px 25mm; font-size: 9pt; color: var(--text-muted);
    border-bottom: 1px solid var(--border);
  }
  h1 { font-size: 24pt; color: var(--accent); margin-top: 0; }
  h2 { font-size: 18pt; color: var(--text-primary); border-bottom: 2px solid var(--accent); padding-bottom: 8px; }
  h3 { font-size: 14pt; }
  .toc { page-break-after: always; }
  .section-divider { border-top: 3px solid var(--accent); margin: 32px 0; }
  .callout {
    background: var(--bg-elevated); border-left: 4px solid var(--accent);
    padding: 16px 20px; margin: 16px 0; border-radius: 0 6px 6px 0;
  }
  .appendix { page-break-before: always; }
</style>
</head>
<body>
  <div class="page-header">🐝 SwarmAI | {title} | {date}</div>

  <!-- TOC (if >3 pages estimated) -->
  <div class="toc">
    <h2>Table of Contents</h2>
    <!-- Auto-generated from h2 headings -->
  </div>

  <!-- Executive Summary (always first) -->
  <section>
    <h2>Executive Summary</h2>
    <!-- 3-5 sentences: thesis + top finding + implication + CTA -->
  </section>

  <!-- Body sections — as many as content requires -->
  <section>
    <h2>{Section from content_package}</h2>
    <!-- Full content, no artificial truncation -->
  </section>

  <!-- Appendix (encouraged for supporting data) -->
  <section class="appendix">
    <h2>Appendix</h2>
    <!-- Raw data, extended evidence, code samples -->
  </section>

  <!-- Footer on last page -->
  <div style="margin-top: 48px; text-align: center;">
    <p>github.com/xg-gh-25/SwarmAI</p>
    <!-- QR code -->
  </div>
</body>
</html>
```

---

## Step 3: Playwright PDF Render

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(f'file://{html_path}')
    page.wait_for_timeout(800)  # Let fonts + layout settle

    # IMPORTANT: Always set Playwright margins to 0.
    # All margins are handled in CSS (@page rule or body padding).
    # Setting margins in BOTH CSS and Playwright would double them.
    page.pdf(
        path=output_pdf_path,
        format='A4',                    # or 'Letter' for US audience
        print_background=True,          # CRITICAL: renders background colors
        margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'}
    )
    browser.close()
```

**One-pager verification:** After render, check page count:
```python
from pypdf import PdfReader
reader = PdfReader(output_pdf_path)
assert len(reader.pages) == 1, f"One-pager rendered {len(reader.pages)} pages — content too long"
```

**PDF Metadata injection (always — professional polish):**
```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader(output_pdf_path)
writer = PdfWriter()
for page in reader.pages:
    writer.add_page(page)

writer.add_metadata({
    '/Title': '{document_title}',
    '/Author': 'XG / SwarmAI',
    '/Subject': '{topic}',
    '/Creator': 'SwarmAI Pollinate',
    '/Producer': 'Playwright + pypdf',
})
with open(output_pdf_path, 'wb') as f:
    writer.write(f)
```
This ensures the PDF shows correct metadata in file browsers, preview apps, and search indexes.

---

## Step 4: Preview for Chat

```bash
PDF_SCRIPTS="$HOME/.swarm-ai/SwarmWS/.claude/skills/s_pdf/scripts"
python3 "$PDF_SCRIPTS/convert_pdf_to_images.py" \
  "content/{name}/tracks/pdf/{topic}.pdf" \
  --output-dir "content/{name}/tracks/pdf/" \
  --dpi 150
```

Show the preview inline in chat with an absolute-path markdown image `![preview](/abs/path/preview.png)` (rendered by the chat's raw-file endpoint, zero context cost) — NOT the Read tool, which injects the full image payload into model context for no display benefit.

---

## Step 5: Quality Verification (RP-F)

| # | Pattern | How to Verify | Gate |
|---|---------|--------------|------|
| RP-F1 | Page constraint | One-pager: `PdfReader(path).pages == 1`. Full PDF: no blank pages. | BLOCKING |
| RP-F2 | Content completeness | One-pager: top 3 key points present. Full PDF: ALL key points from content_package. | BLOCKING |
| RP-F3 | Proof concrete | Each proof card has a specific number, quote, or data point (not generic "significant improvement") | BLOCKING |
| RP-F4 | CTA present | Clear next-step action visible (not buried in body text) | BLOCKING |
| RP-F5 | Brand present | Logo/emoji + QR + github.com URL on every page (full PDF) or footer (one-pager) | BLOCKING |
| RP-F6 | Print-friendly | `print_background=True` verified. No pure-white text on white paper. Dark themes use inverted print styles. | BLOCKING |
| RP-F7 | TOC present | Full PDF >3 pages: Table of Contents on page 1. One-pager: N/A. | BLOCKING (full PDF only) |

---

## Output Files

| File | Always? | Content |
|------|---------|---------|
| `tracks/pdf/{topic}.html` | ✅ | Source HTML |
| `tracks/pdf/{topic}.pdf` | ✅ | Rendered A4 PDF |
| `tracks/pdf/{topic}-letter.pdf` | If US audience | Rendered Letter size |
| `tracks/pdf/{topic}-preview.png` | ✅ | First page preview for chat |

---

## Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "One-pager has too much content, just make it 2 pages" | Distill harder. One-pager = ONE page. If you can't fit, the message isn't focused enough. |
| "Full PDF doesn't need a TOC, it's only 4 pages" | Threshold is 3 pages. 4 > 3. Add TOC. |
| "Print-friendly doesn't matter, everyone reads on screen" | You don't know that. Dark theme PDFs need print override CSS. |
| "QR code doesn't fit the one-pager layout" | Make it fit. Brand compliance is non-negotiable. Shrink to 80×80px if needed. |
| "Content is complete enough with 5 of 8 key points" | Full PDF mode = ALL key points. No cherry-picking unless user explicitly says "summarize." |
