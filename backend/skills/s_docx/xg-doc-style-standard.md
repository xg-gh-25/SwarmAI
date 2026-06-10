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

## Amazon Narrative Format (LT Review / 6-Pager Style)

When the user requests an Amazon-style narrative, LT review doc, or 6-pager, apply this format instead of the general style above. This is the canonical format for internal decision documents presented to leadership.

### Core Principles

1. **Dense prose over bullets/tables.** The body is continuous paragraphs, not PowerPoint-style bullet lists. Tables are reserved for priority matrices and appendix data only.
2. **Bold lead assertion per paragraph.** Each paragraph begins with a bold sentence that states the claim. The rest of the paragraph provides evidence and reasoning.
3. **Flat section structure.** Use `List Paragraph` style (bold, slightly larger) for top-level section markers (e.g., "Summary & Background", "Observations and Opportunities"). Within sections, use bold inline lead sentences — not nested heading levels.
4. **No markdown headers in the Word doc.** Section markers are bold paragraphs, not Word Heading styles. This keeps the narrative flowing without visual hierarchy breaks.
5. **Tenets at start of Appendix.** Include a "Tenets (To Be Refined as Needed)" section as the first appendix item — bulleted, each tenet starting with a bold label followed by a colon and explanation.

### Structure Template

```
[Document Title — one descriptive line]
[Subtitle — context/audience line]

Summary & Background
  [1-2 dense paragraphs: context, core finding, key numbers]

Observations and Opportunities
  [3-5 paragraphs, each with bold lead assertion]
  [Pattern: assertion → on-site/field evidence → implication/opportunity]
  [Inline bold for company names, key metrics, or strategic terms]

[Strategy / Proposal Section — named contextually]
  [Dense paragraphs describing approach, levers, mechanisms]

Collaboration Model (if applicable)
  [Paragraph form describing RACI or division of labor]

Risks and Open Questions
  [2-4 paragraphs, each bold-lead, stating risk + mitigation]

Ask
  [1-2 paragraphs: numbered dimensions of what LT support is needed]

Appendix
  Tenets (To Be Refined as Needed)
    * [Bold Label]: [Explanation]
  Appendix A: [Data/Cases]
  Appendix B: [Verbatim/Quotes]
  Appendix C: [Priority Matrix — TABLE allowed here]
  ...
```

### Formatting Specifics for Amazon Narrative

#### Page Setup

| Property | Value | docx-js |
|----------|-------|---------|
| Page size | Letter (8.5" × 11") | `width: 12240, height: 15840` (twips) |
| Margins | 0.7" all sides | `top/right/bottom/left: 1008` (twips) |
| Header distance | 0.5" | `720` twips |
| Footer distance | 0.3" | `432` twips |
| **Line numbers** | Every line, continuous | `lnNumType: { countBy: 1, restart: "continuous" }` |
| **Footer** | "Amazon Confidential" left + "Page X of Y" right | Footer paragraph, 8pt (16 hp) |

#### Typography & Spacing

| Element | Font | Size | Alignment | Spacing | docx-js |
|---------|------|------|-----------|---------|---------|
| **Title** | Calibri | 10.5pt (21 hp), bold | **Center** | after=120, line=240 | `alignment: AlignmentType.CENTER` |
| **Body text** | Calibri | 10.5pt (21 hp) | **Justify** | Single (line=240), after=0 | `alignment: AlignmentType.JUSTIFIED` |
| **Section marker** | Calibri, bold | 10.5pt (inherited) | Left (inherited) | after=120, line=240 | Style `ListParagraph`, indent=0 |
| **Bold lead assertion** | Calibri, bold run | 10.5pt | **Justify** (same as body) | Same as body | Bold `Run` inline |
| **Table header** | Calibri | 9pt (18 hp) | — | — | `SIZE_TABLE_HDR: 18` |
| **Table body** | Calibri | 8pt (16 hp) | — | — | `SIZE_TABLE: 16` |
| **Footer** | Calibri | 8pt (16 hp) | — | — | pPr rPr sz=16 |

#### Key Layout Rules

1. **Justify alignment on all body text** — `jc: "both"` (OOXML) / `AlignmentType.JUSTIFIED` (docx-js). This is the professional Amazon look. Only section markers and title are NOT justified.
2. **Single spacing throughout** — `line: 240, lineRule: auto` (NOT the 1.15x/259 used in general docs). Dense narrative.
3. **Minimal paragraph spacing** — Body paragraphs have NO space after (only line spacing separates). Section-opening paragraphs use `after: 120` (6pt) for breathing room.
4. **Line numbers enabled** — `countBy: 1, restart: continuous`. Allows reviewers to reference specific lines in feedback. Applied via section properties.
5. **Title centered** — Only the document title uses center alignment. Everything else is left or justified.
6. **Footer: "Amazon Confidential" + page numbers** — Left-aligned "Amazon Confidential" text + right-aligned "Page X of Y" using `PAGE` and `NUMPAGES` fields. Font size 8pt.
7. **No indentation** — Everything left-aligned flush. List Paragraph's default indent overridden to 0.
8. **Bold runs, not bold paragraphs** — The assertion lead is a bold `Run` within a Normal paragraph, followed by non-bold runs.
9. **No heading styles** — Section markers use `List Paragraph` (bold, indent=0) for visual break without TOC implications.

#### docx-js Constants (Amazon Narrative)

```javascript
// Font
const FONT = "Calibri";
const FONT_EA = "KaiTi";  // East Asian fallback

// Sizes (half-points)
const SIZE_BODY = 21;      // 10.5pt
const SIZE_TABLE_HDR = 18; // 9pt
const SIZE_TABLE = 16;     // 8pt
const SIZE_FOOTER = 16;    // 8pt

// Page (twips)
const MARGINS = { top: 1008, right: 1008, bottom: 1008, left: 1008 };

// Alignment
const ALIGN_BODY = AlignmentType.JUSTIFIED;  // "both" in OOXML
const ALIGN_TITLE = AlignmentType.CENTER;
const ALIGN_SECTION = AlignmentType.LEFT;    // inherited, section markers

// Paragraph spacing — SINGLE line, minimal after
const SPACING_BODY = { line: 240, lineRule: LineRuleType.AUTO };  // No after, justified
const SPACING_SECTION_START = { after: 120, line: 240, lineRule: LineRuleType.AUTO };  // 6pt after

// Section marker (List Paragraph style, bold, no indent)
const SECTION_MARKER = {
  style: "ListParagraph",
  indent: { left: 0 },  // Override default 457200 EMU indent
  spacing: { after: 120, line: 240, lineRule: LineRuleType.AUTO },
  bold: true,
};

// Line numbers (section property)
const LINE_NUMBERS = { countBy: 1, restart: "continuous" };

// Footer
const FOOTER_TEXT = "Amazon Confidential";
const FOOTER_PAGE_FORMAT = "Page {PAGE} of {NUMPAGES}";

// Colors
const CLR_TEXT = "000000";
const CLR_TABLE_HDR_BG = "37475A";
const CLR_TABLE_HDR_TEXT = "FFFFFF";
```

#### Visual Summary

| Element | Format |
|---------|--------|
| Body text | Calibri 10.5pt, single spacing, no space after, black |
| Bold lead assertion | Same font, bold run inline (not separate style) |
| Section marker | List Paragraph, bold, indent=0, 6pt after |
| Tenet label | Bold text before colon: `**Scale Through Leverage:**` |
| Tables (appendix only) | 8-9pt, header row shaded 37475A white text |
| Page target | Main body ~2 pages (excl appendix) |
| Appendix | No page limit, detail as needed |

### Writing Style Rules

- **Never start with "This document..."** — jump into the finding or context directly.
- **Quantify everything.** "60+ requests" not "many requests." "$3.5M pipeline" not "significant pipeline."
- **Name names.** Customer verbatim, people involved, specific products. Concreteness = credibility.
- **One assertion per paragraph.** Don't bury multiple claims in one block.
- **"So what" explicit.** Every observation paragraph ends with an implication or opportunity statement.
- **Risks are genuine.** Not perfunctory. Each risk names a specific failure mode and a specific mitigation.
- **Ask is concrete.** Not "please support us" — rather "formally endorse X so that Y can happen."

### When to Apply

- User says "Amazon narrative", "LT review", "6-pager", "narrative doc", "executive summary for leadership"
- User references presenting to Rob, WangBo, or any L7+ Amazon leader
- User sends a reference doc in this dense-prose format and says "write like this"

---

_Extracted 2026-04-15 from XG's reference documents. Amazon Narrative section added 2026-06-10 from Physical AI report reference + AIDLC LT review._
