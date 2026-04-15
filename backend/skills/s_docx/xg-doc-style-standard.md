# XG Document Style Standard

Extracted from two reference documents:
- **Doc A**: CustomerLink ML Launch Decision (Quip-origin, data-heavy analysis)
- **Doc B**: Physical AI Customer Engagement Guidance (strategic framework, bilingual CN/EN)

Use this as the default style when creating documents for XG unless the user specifies otherwise.

---

## Page Setup

| Property | Doc A (Letter, Analysis) | Doc B (Letter, Guidance) | **Default** |
|----------|--------------------------|--------------------------|-------------|
| Page size | Letter (12240×15840 twips = 8.5"×11") | Letter (12240×15840) | **Letter** |
| Margins (top/right/bottom/left) | 1440/1440/1440/1440 (1" all) | 1008/1008/1008/1008 (0.7" all) | **1008 twips (0.7") all** — tighter margins for dense content |
| Header/Footer | 720/720 | 720/432 | 720/432 |

## Typography

### Font Stack

| Element | Doc A | Doc B | **Default** |
|---------|-------|-------|-------------|
| Body text | Theme minorHAnsi (Calibri) | Calibri (explicit) | **Calibri** |
| East Asian | Theme minorEastAsia (宋体) | KaiTi (楷体) | **KaiTi** for formal, 宋体 for casual |
| Headings | Helvetica (bold) | Theme majorHAnsi (Calibri Light) | **Calibri** (matches body, clean) |
| Code blocks | Courier New | — | **Courier New** |
| CS/Bidi | Theme minorBidi | Calibri | **Calibri** |

### Font Sizes (half-points → actual pt)

| Element | Doc A | Doc B | **Default** |
|---------|-------|-------|-------------|
| Title/Doc Header | 28 hp = **14pt**, bold | — (uses bold paragraph) | **14pt bold** |
| Heading 1 | 48 hp = **24pt** (style def) | 40 hp = **20pt** | **20pt** |
| Heading 2 | 26 hp = **13pt** (style def) | 32 hp = **16pt** | **13-14pt bold** |
| Body text | 20 hp = **10pt** | 21 hp = **10.5pt** (Normal style) | **10.5pt** |
| Table header | 15 hp = **7.5pt** | 18 hp = **9pt** | **9pt** |
| Table body | 15 hp = **7.5pt** | 16 hp = **8pt** | **8pt** |
| Metadata/caption | 15 hp = **7.5pt** | 16 hp = **8pt** | **8pt italic** |

### Line Spacing

| Element | Doc A | Doc B | **Default** |
|---------|-------|-------|-------------|
| Body paragraphs | Default (single) | 259 twips lineRule=auto (~1.15) | **1.15 (259 auto)** |
| After paragraph | 0 (default) | 160 twips (~8pt) | **160 twips after** |
| Heading 1 before | 480 twips (~24pt) | 360 twips (~18pt) | **360 before, 80 after** |
| Heading 2 before | 200 twips (~10pt) | 160 twips (~8pt) | **200 before, 80 after** |

## Colors

| Purpose | Doc A | Doc B | **Default** |
|---------|-------|-------|-------------|
| Primary text | 06081F (very dark navy) | 000000 (black, themeColor=text1) | **000000** (black) |
| Body text alternate | 06081F | — | 06081F for emphasis sections |
| Accent/heading color | — (no color, just bold) | 0F4761 (themeColor accent1 shade BF) | **0F4761** for headings |
| Hyperlink | 0563C1 (themeColor hyperlink) | 0563C1 | **0563C1** |
| Table header bg | — (uses table style shading) | — | **37475A** (dark blue-gray) |
| Code background | EEEEEE | — | **EEEEEE** |
| Quote border | BFBFBF (themeColor bg1 shade BF) | — | **BFBFBF** |
| Secondary text | — | 999999 | **999999** for footnotes/captions |

## Document Structure Patterns

### Title Block (Both docs)
- **Bold paragraph** (not Word Title style) with full document name + date
- *Italic* line for doc owners (with Quip hyperlinks)
- *Italic* line for reviewers
- Followed by **Purpose** section as bold label in body text

### Heading Hierarchy
- **Heading 1** (`##` in pandoc): Major sections — "Background", "ML Performance Overview", "Customer Segmentation"
- **Heading 2** (not used in Doc A; used sparingly in Doc B): Sub-sections
- **Bold inline labels** preferred over heading styles for sub-topics: `**[Purpose.]**`, `**[Market Opportunity.]**`
- **Numbered list headings**: `1. Section Title` as ListParagraph with bold first line

### Inline Emphasis Patterns (XG's style)
- `**[Label.]{.underline}**` — bold + underline for term definitions and key labels
- `**bold text**` — for data callouts: `**5.5% vs. 4.0% (+150bps)**`
- `*italic*` — for metadata, caveats, test group descriptions
- `***bold italic***` — for test group labels: `***[Test Group A:]***`

### Lists
- **Bullet lists** (`-`): for criteria, requirements, unordered items
- **Numbered lists** (`1/`, `2/`, `3/`): for objectives, ordered items — uses slash notation not period
- **Nested indentation**: used sparingly, prefer flat lists with bold sub-labels

### Tables
- **Doc A**: Heavy use of data tables. Small font (7.5-8pt). Uses built-in table styles (LightShading, LightList-Accent1, LightShading-Accent2). Grid borders. Color-coded cells for comparison data.
- **Doc B**: Sparse tables for structured data (segmentation matrices). Clean grid borders, 8-9pt font.
- **Common pattern**: Header row with shading, body rows with alternating light shading, compact font sizes.

### Callout / Decision Boxes
- `**[Launch Decision]**` — bold bracketed label inline, followed by data summary
- Quote style: left border (18pt, BFBFBF gray), italic, indented 144 left + 864 right
- Pull Quote style: centered, italic, light gray (BFBFBF), 16pt, 288 indent both sides

### Code Style (Doc A only)
- Paragraph style "code": Courier New, background EEEEEE, spacing 240 before/after
- Character style "InlineCode": Courier New, background EEEEEE

## Key Patterns for docx-js Generation

When creating documents in XG's style with docx-js:

```javascript
// Font constants
const FONT = "Calibri";
const FONT_EA = "KaiTi";  // East Asian
const FONT_CODE = "Courier New";

// Size constants (docx-js uses half-points)
const SIZE_TITLE = 28;     // 14pt
const SIZE_H1 = 40;        // 20pt
const SIZE_H2 = 28;        // 14pt
const SIZE_BODY = 21;      // 10.5pt
const SIZE_TABLE_HDR = 18; // 9pt
const SIZE_TABLE = 16;     // 8pt
const SIZE_CAPTION = 16;   // 8pt

// Colors (no # prefix for docx-js)
const CLR_TEXT = "000000";
const CLR_HEADING = "0F4761";
const CLR_ACCENT = "06081F";
const CLR_LINK = "0563C1";
const CLR_TABLE_HDR = "37475A";
const CLR_CODE_BG = "EEEEEE";
const CLR_QUOTE_BORDER = "BFBFBF";
const CLR_SECONDARY = "999999";

// Page margins (twips) — 0.7" all sides
const MARGINS = { top: 1008, right: 1008, bottom: 1008, left: 1008 };

// Paragraph spacing defaults
const SPACING_BODY = { after: 160, line: 259, lineRule: "auto" };  // ~1.15 spacing
const SPACING_H1 = { before: 360, after: 80 };
const SPACING_H2 = { before: 200, after: 80 };
```

## When to Apply

- **Always**: Calibri font, 10.5pt body, 0.7" margins, 1.15 line spacing
- **For data-heavy docs**: Smaller table fonts (8pt), compact layout, colored decision boxes
- **For guidance/framework docs**: Numbered section headings, bold+underline labels, bilingual considerations (KaiTi for CN text)
- **For any doc**: Bold inline labels over heading styles for sub-topics, slash-notation numbered lists (`1/`, `2/`)

---

_Extracted 2026-04-15 from XG's reference documents. Use as default style standard for s_docx skill._
