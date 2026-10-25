# Default Document Style

The style to use when the user has not given one. It is deliberately plain: readable,
conventional, easy for the user to restyle in Word.

**Which style wins, in order:**
1. What the user asks for in this request ("use A4", "make it look like the attached doc").
2. A reference document the user sends: unpack it (see `ooxml.md`) and read its
   `word/styles.xml` and `word/document.xml` for fonts, sizes, spacing and margins.
3. A style the user keeps on record: their profile or tool notes (USER.md / TOOLS.md) may
   name a style file; read it before writing.
4. This file.

---

## Page Setup

| Property | Default | docx-js |
|----------|---------|---------|
| Page size | **A4**; **Letter** when the user or their organization is in the US or Canada | A4 `width: 11906, height: 16838`; Letter `12240 × 15840` (twips) |
| Margins | 1" (2.54 cm) all sides | `1440` twips |
| Header / footer distance | 0.5" | `720` twips |
| Page numbers | Footer, centered, "Page X of Y" for documents over 2 pages | `PageNumber.CURRENT` / `PageNumber.TOTAL_PAGES` in a `Footer` |

## Typography

| Element | Font | Size | docx-js (half-points) |
|---------|------|------|-----------------------|
| Body | Calibri (Latin) | 11pt | `22` |
| East Asian text | SimSun (宋体); Microsoft YaHei (微软雅黑) for slides-like or on-screen docs | same as body | `font: { ascii: "Calibri", hAnsi: "Calibri", eastAsia: "SimSun" }` |
| Title | Calibri, bold | 18pt | `36` |
| Heading 1 | Calibri, bold | 16pt | `32` |
| Heading 2 | Calibri, bold | 13pt | `26` |
| Heading 3 | Calibri, bold | 11pt | `22` |
| Table text | Calibri | 10pt (header bold) | `20` |
| Caption / footnote | Calibri, italic | 9pt | `18` |
| Code | Consolas (fallback Courier New) | 10pt | `20` |

Use Word's built-in Heading 1–3 styles for headings so the navigation pane and a table of
contents work. Set `eastAsia` explicitly whenever the document contains CJK text; without
it Word falls back to a Latin font and Chinese renders inconsistently.

## Spacing

| Element | Default | docx-js |
|---------|---------|---------|
| Body line spacing | 1.15 | `line: 276, lineRule: "auto"` |
| After body paragraph | 8pt | `after: 160` |
| Heading 1 | 18pt before, 6pt after | `before: 360, after: 120` |
| Heading 2 | 12pt before, 6pt after | `before: 240, after: 120` |
| Lists | 4pt after each item | `after: 80` |

## Colors

Black body text on white. One accent color for headings and table header shading; pick it
from the subject or the organization's brand when the user mentions one (see Design
Principles in `INSTRUCTIONS.md`), otherwise use a dark blue.

| Purpose | Default |
|---------|---------|
| Body text | `000000` |
| Headings | `1F3864` (dark blue) |
| Table header fill / text | `1F3864` / `FFFFFF` |
| Table banding (optional) | `F2F2F2` |
| Hyperlink | `0563C1` |
| Secondary text (captions) | `595959` |

## Structure

- Title, then a one-line purpose or summary, then the body. Put author and date under the
  title when the user gives them.
- Headings for sections; avoid more than three levels.
- Bullets for unordered items, numbered lists for steps or ranked items.
- Tables: header row repeated across pages, light grid borders, numbers right-aligned.
- Keep emphasis sparse: bold for the key fact in a paragraph, italic for caveats.

## docx-js Constants

```javascript
const FONT = "Calibri";
const FONT_EA = "SimSun";        // East Asian
// Document default: styles.default.document.run = { font: RUN_FONT, size: SIZE_BODY }
const RUN_FONT = { ascii: FONT, hAnsi: FONT, eastAsia: FONT_EA };
const FONT_CODE = "Consolas";

const SIZE_TITLE = 36;   // 18pt
const SIZE_H1 = 32;      // 16pt
const SIZE_H2 = 26;      // 13pt
const SIZE_BODY = 22;    // 11pt
const SIZE_TABLE = 20;   // 10pt
const SIZE_CAPTION = 18; // 9pt

const CLR_TEXT = "000000";
const CLR_HEADING = "1F3864";
const CLR_TABLE_HDR = "1F3864";
const CLR_LINK = "0563C1";
const CLR_SECONDARY = "595959";

const PAGE_A4 = { width: 11906, height: 16838 };
const MARGINS = { top: 1440, right: 1440, bottom: 1440, left: 1440 };

const SPACING_BODY = { after: 160, line: 276, lineRule: "auto" };
const SPACING_H1 = { before: 360, after: 120 };
const SPACING_H2 = { before: 240, after: 120 };
```

---

## Dense Narrative Layout (decision memo / six-pager)

When the user asks for a narrative decision document (a six-pager, a decision memo, a
leadership review doc), write the content with `s_narrative-writing` and lay it out like
this instead of the default above.

**Principles**
1. Continuous prose, not bullet lists. Tables only in the appendix.
2. Each paragraph opens with a bold sentence stating its claim; the rest gives evidence and
   the implication.
3. Flat structure: section markers are bold paragraphs, not Heading styles.
4. Main body about 2 pages (up to 6 for a six-pager); the appendix has no limit.

**Layout**

| Element | Format | docx-js |
|---------|--------|---------|
| Title | Bold, centered, body size | `alignment: AlignmentType.CENTER` |
| Body | 11pt, **justified**, single spacing, no space after | `alignment: AlignmentType.JUSTIFIED`, `spacing: { line: 240, lineRule: "auto" }` |
| Section marker | Bold paragraph, 6pt after, no indent | `spacing: { after: 120 }`, `indent: { left: 0 }` |
| Bold lead sentence | Bold run inside a normal paragraph | bold `TextRun`, then plain runs |
| Line numbers | Every line, continuous, so reviewers can cite lines | section `properties.lineNumbers: { countBy: 1, restart: LineNumberRestartFormat.CONTINUOUS }` |
| Footer | Page X of Y; add the confidentiality label the user's organization uses, if they give one | 9pt footer paragraph |
| Appendix tables | 9–10pt, shaded header row | as in Typography |

**Writing rules**
- Don't open with "This document…"; open with the finding or the context.
- Quantify: "60+ requests", not "many requests".
- Be concrete: name the customers, products and people involved.
- One claim per paragraph, and say the "so what" explicitly.
- Risks name a specific failure mode and a specific mitigation.
- The ask is concrete: what decision or support, so that what can happen.
