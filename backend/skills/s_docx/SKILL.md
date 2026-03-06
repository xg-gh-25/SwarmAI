---
name: DOCX Editor
description: "Comprehensive document creation, editing, and analysis with support for tracked changes, comments, formatting preservation, and text extraction. When the agent needs to work with professional documents (.docx files) for: (1) Creating new documents, (2) Modifying or editing content, (3) Working with tracked changes, (4) Adding comments, or any other document tasks"
---

# DOCX creation, editing, and analysis

## Overview

A user may ask you to create, edit, or analyze the contents of a .docx file. A .docx file is essentially a ZIP archive containing XML files and other resources that you can read or edit. You have different tools and workflows available for different tasks.

## Workflow Decision Tree

### Reading/Analyzing Content
Use "Text extraction" or "Raw XML access" sections below

### Creating New Document
Use "Creating a new Word document" workflow

### Editing Existing Document
- **Your own document + simple changes**
  Use "Basic OOXML editing" workflow

- **Someone else's document**
  Use **"Redlining workflow"** (recommended default)

- **Legal, academic, business, or government docs**
  Use **"Redlining workflow"** (required)

## Reading and analyzing content

### Text extraction
If you just need to read the text contents of a document, you should convert the document to markdown using pandoc. Pandoc provides excellent support for preserving document structure and can show tracked changes:

```bash
# Convert document to markdown with tracked changes
pandoc --track-changes=all path-to-file.docx -o output.md
# Options: --track-changes=accept/reject/all
```

### Raw XML access
You need raw XML access for: comments, complex formatting, document structure, embedded media, and metadata. For any of these features, you'll need to unpack a document and read its raw XML contents.

#### Unpacking a file
`python ooxml/scripts/unpack.py <office_file> <output_directory>`

#### Key file structures
* `word/document.xml` - Main document contents
* `word/comments.xml` - Comments referenced in document.xml
* `word/media/` - Embedded images and media files
* Tracked changes use `<w:ins>` (insertions) and `<w:del>` (deletions) tags

## Creating a new Word document

When creating a new Word document from scratch, use **docx-js**, which allows you to create Word documents using JavaScript/TypeScript.

### Design Principles

**CRITICAL**: Before creating any document, analyze the content and choose appropriate design elements:

1. **Consider the subject matter**: What is this document about? What tone does it suggest?
2. **Check for branding**: If the user mentions a company/organization, consider their brand colors
3. **Match palette to content**: Select colors that reflect the subject
4. **State your approach**: Explain your design choices before writing code

**Requirements**:
- ✅ State your content-informed design approach BEFORE writing code
- ✅ Use universally supported fonts: Arial (recommended default), Times New Roman, Georgia, Verdana, Courier New
- ✅ Create clear visual hierarchy through size, weight, and color
- ✅ Ensure readability: strong contrast, appropriate text sizes, clean alignment
- ✅ Be consistent: repeat patterns, spacing, and visual language throughout

### Color Palette Selection

Choose colors creatively based on the document's topic, audience, and tone. Build a palette of 3-5 colors (hex values WITHOUT `#` prefix for docx-js):

| Palette | Primary | Header BG | Accent | Light BG | Border |
|---------|---------|-----------|--------|----------|--------|
| Corporate Blue | 1C2833 | 2E4053 | 3498DB | F4F6F6 | D5DBDB |
| AWS Orange | 232F3E | 37475A | FF9900 | F8F9FA | D5DBDB |
| Warm Professional | 5D1D2E | 951233 | C15937 | FAF7F2 | E8D5D0 |
| Modern Teal | 277884 | 5EA8A7 | FE4447 | F0FAFA | B8D8D8 |
| Forest Green | 1E5128 | 4E9F3D | D8E9A8 | F5FAF0 | C5DEB5 |
| Charcoal & Red | 292929 | 555555 | E33737 | F2F2F2 | CCCCCC |

### Workflow
1. **MANDATORY - READ ENTIRE FILE**: Read [`docx-js.md`](docx-js.md) (~500 lines) completely from start to finish. **NEVER set any range limits when reading this file.** Read the full file content for detailed syntax, critical formatting rules, and best practices before proceeding with document creation.
2. **Analyze content** — Determine document type (report, guide, proposal, letter, etc.)
3. **Choose design** — Select color palette, fonts, and layout style
4. **Define custom styles** — Override built-in Heading1/Heading2/Title styles with your palette
5. **Build reusable helpers** — Create functions for tables, tip boxes, step cards (see patterns below)
6. **Generate the document** — Create a JavaScript file using Document, Paragraph, TextRun components
7. **Export** — Save as .docx using `Packer.toBuffer()`
8. **Validate visually** — Convert to images and inspect for layout issues (see Visual Validation below)

### Reusable Component Patterns

Use these helper patterns to avoid repetitive boilerplate. Adapt colors to your chosen palette.

#### Styled Table with Header and Alternating Rows

```javascript
const { Table, TableRow, TableCell, Paragraph, TextRun,
  BorderStyle, WidthType, ShadingType, VerticalAlign, AlignmentType } = require('docx');

const TB = { style: BorderStyle.SINGLE, size: 1, color: "D5DBDB" };
const CB = { top: TB, bottom: TB, left: TB, right: TB };

function headerCell(text, width) {
  return new TableCell({
    borders: CB, width: { size: width, type: WidthType.DXA },
    shading: { fill: "37475A", type: ShadingType.CLEAR },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({ children: [
      new TextRun({ text, bold: true, size: 18, font: "Arial", color: "FFFFFF" })
    ] })]
  });
}

function dataCell(text, width, opts = {}) {
  return new TableCell({
    borders: CB, width: { size: width, type: WidthType.DXA },
    shading: opts.shaded ? { fill: "F8F9FA", type: ShadingType.CLEAR } : undefined,
    children: [new Paragraph({ children: [
      new TextRun({ text, size: 18, font: "Arial", bold: opts.bold,
        color: opts.bold ? "232F3E" : "000000" })
    ] })]
  });
}

// Usage: alternating rows
rows.map((row, i) => new TableRow({ children: [
  dataCell(row[0], 3200, { bold: true, shaded: i % 2 === 1 }),
  dataCell(row[1], 6160, { shaded: i % 2 === 1 }),
] }));
```

#### Tip / Callout Box (Orange Left Border)

```javascript
function tipBox(text, totalWidth) {
  return new Table({
    columnWidths: [totalWidth],
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    rows: [new TableRow({ children: [new TableCell({
      borders: { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE },
        right: { style: BorderStyle.NONE },
        left: { style: BorderStyle.SINGLE, size: 6, color: "FF9900" } },
      width: { size: totalWidth, type: WidthType.DXA },
      shading: { fill: "FFF8E7", type: ShadingType.CLEAR },
      children: [new Paragraph({ children: [
        new TextRun({ text: "Tip: ", bold: true, italics: true, size: 18, font: "Arial", color: "6B4C00" }),
        new TextRun({ text, italics: true, size: 18, font: "Arial", color: "6B4C00" }),
      ] })]
    })] })]
  });
}
```

#### Step Card (Numbered Steps with Accent)

```javascript
function stepParagraphs(num, title, desc, cmd) {
  const parts = [];
  parts.push(new Paragraph({ spacing: { before: 160, after: 40 }, children: [
    new TextRun({ text: `Step ${num}: `, bold: true, size: 22, font: "Arial", color: "FF9900" }),
    new TextRun({ text: title, bold: true, size: 22, font: "Arial", color: "232F3E" }),
  ] }));
  if (desc) parts.push(new Paragraph({ spacing: { after: 40 },
    children: [new TextRun({ text: desc, size: 18, font: "Arial", color: "555555" })] }));
  if (cmd) parts.push(new Paragraph({ spacing: { after: 80 },
    shading: { fill: "EAECEE", type: ShadingType.CLEAR },
    children: [new TextRun({ text: cmd, size: 18, font: "Courier New", color: "232F3E" })] }));
  return parts;
}
```

### Converting Markdown to DOCX

When converting markdown content to a styled Word document, map markdown elements to docx-js components:

| Markdown Element | docx-js Component |
|------------------|--------------------|
| `# Heading 1` | `new Paragraph({ heading: HeadingLevel.HEADING_1, children: [...] })` |
| `## Heading 2` | `new Paragraph({ heading: HeadingLevel.HEADING_2, children: [...] })` |
| Body text | `new Paragraph({ children: [new TextRun("text")] })` |
| `**bold**` | `new TextRun({ text: "bold", bold: true })` |
| `*italic*` | `new TextRun({ text: "italic", italics: true })` |
| `` `code` `` | `new TextRun({ text: "code", font: "Courier New", size: 18 })` |
| Code block | `new Paragraph({ shading: { fill: "EAECEE", type: ShadingType.CLEAR }, children: [new TextRun({ text, font: "Courier New" })] })` |
| `- list item` | `new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [...] })` |
| Table | `new Table({ ... })` with headerCell/dataCell helpers |
| `---` | `new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 1 } } })` |
| Page break | `new Paragraph({ children: [new PageBreak()] })` |

**Workflow for markdown conversion:**
1. Read the markdown source
2. Parse structure (headings, paragraphs, lists, tables, code blocks)
3. Map each element to the corresponding docx-js component
4. Apply consistent styling from your chosen palette
5. Build and validate

## Editing an existing Word document

When editing an existing Word document, use the **Document library** (a Python library for OOXML manipulation). The library automatically handles infrastructure setup and provides methods for document manipulation. For complex scenarios, you can access the underlying DOM directly through the library.

### Workflow
1. **MANDATORY - READ ENTIRE FILE**: Read [`ooxml.md`](ooxml.md) (~600 lines) completely from start to finish. **NEVER set any range limits when reading this file.** Read the full file content for the Document library API and XML patterns for directly editing document files.
2. Unpack the document: `python ooxml/scripts/unpack.py <office_file> <output_directory>`
3. Create and run a Python script using the Document library (see "Document Library" section in ooxml.md)
4. Pack the final document: `python ooxml/scripts/pack.py <input_directory> <office_file>`

The Document library provides both high-level methods for common operations and direct DOM access for complex scenarios.

## Redlining workflow for document review

This workflow allows you to plan comprehensive tracked changes using markdown before implementing them in OOXML. **CRITICAL**: For complete tracked changes, you must implement ALL changes systematically.

**Batching Strategy**: Group related changes into batches of 3-10 changes. This makes debugging manageable while maintaining efficiency. Test each batch before moving to the next.

**Principle: Minimal, Precise Edits**
When implementing tracked changes, only mark text that actually changes. Repeating unchanged text makes edits harder to review and appears unprofessional. Break replacements into: [unchanged text] + [deletion] + [insertion] + [unchanged text]. Preserve the original run's RSID for unchanged text by extracting the `<w:r>` element from the original and reusing it.

Example - Changing "30 days" to "60 days" in a sentence:
```python
# BAD - Replaces entire sentence
'<w:del><w:r><w:delText>The term is 30 days.</w:delText></w:r></w:del><w:ins><w:r><w:t>The term is 60 days.</w:t></w:r></w:ins>'

# GOOD - Only marks what changed, preserves original <w:r> for unchanged text
'<w:r w:rsidR="00AB12CD"><w:t>The term is </w:t></w:r><w:del><w:r><w:delText>30</w:delText></w:r></w:del><w:ins><w:r><w:t>60</w:t></w:r></w:ins><w:r w:rsidR="00AB12CD"><w:t> days.</w:t></w:r>'
```

### Tracked changes workflow

1. **Get markdown representation**: Convert document to markdown with tracked changes preserved:
   ```bash
   pandoc --track-changes=all path-to-file.docx -o current.md
   ```

2. **Identify and group changes**: Review the document and identify ALL changes needed, organizing them into logical batches:

   **Location methods** (for finding changes in XML):
   - Section/heading numbers (e.g., "Section 3.2", "Article IV")
   - Paragraph identifiers if numbered
   - Grep patterns with unique surrounding text
   - Document structure (e.g., "first paragraph", "signature block")
   - **DO NOT use markdown line numbers** - they don't map to XML structure

   **Batch organization** (group 3-10 related changes per batch):
   - By section: "Batch 1: Section 2 amendments", "Batch 2: Section 5 updates"
   - By type: "Batch 1: Date corrections", "Batch 2: Party name changes"
   - By complexity: Start with simple text replacements, then tackle complex structural changes
   - Sequential: "Batch 1: Pages 1-3", "Batch 2: Pages 4-6"

3. **Read documentation and unpack**:
   - **MANDATORY - READ ENTIRE FILE**: Read [`ooxml.md`](ooxml.md) (~600 lines) completely from start to finish. **NEVER set any range limits when reading this file.** Pay special attention to the "Document Library" and "Tracked Change Patterns" sections.
   - **Unpack the document**: `python ooxml/scripts/unpack.py <file.docx> <dir>`
   - **Note the suggested RSID**: The unpack script will suggest an RSID to use for your tracked changes. Copy this RSID for use in step 4b.

4. **Implement changes in batches**: Group changes logically (by section, by type, or by proximity) and implement them together in a single script. This approach:
   - Makes debugging easier (smaller batch = easier to isolate errors)
   - Allows incremental progress
   - Maintains efficiency (batch size of 3-10 changes works well)

   **Suggested batch groupings:**
   - By document section (e.g., "Section 3 changes", "Definitions", "Termination clause")
   - By change type (e.g., "Date changes", "Party name updates", "Legal term replacements")
   - By proximity (e.g., "Changes on pages 1-3", "Changes in first half of document")

   For each batch of related changes:

   **a. Map text to XML**: Grep for text in `word/document.xml` to verify how text is split across `<w:r>` elements.

   **b. Create and run script**: Use `get_node` to find nodes, implement changes, then `doc.save()`. See **"Document Library"** section in ooxml.md for patterns.

   **Note**: Always grep `word/document.xml` immediately before writing a script to get current line numbers and verify text content. Line numbers change after each script run.

5. **Pack the document**: After all batches are complete, convert the unpacked directory back to .docx:
   ```bash
   python ooxml/scripts/pack.py unpacked reviewed-document.docx
   ```

6. **Final verification**: Do a comprehensive check of the complete document:
   - Convert final document to markdown:
     ```bash
     pandoc --track-changes=all reviewed-document.docx -o verification.md
     ```
   - Verify ALL changes were applied correctly:
     ```bash
     grep "original phrase" verification.md  # Should NOT find it
     grep "replacement phrase" verification.md  # Should find it
     ```
   - Check that no unintended changes were introduced


## Visual Validation

After generating a DOCX, validate the output visually to catch layout issues before delivery.

### Workflow

1. **Convert DOCX to PDF**:
   ```bash
   soffice --headless --convert-to pdf document.docx
   ```

2. **Convert PDF pages to JPEG images**:
   ```bash
   pdftoppm -jpeg -r 150 document.pdf preview
   ```
   Creates `preview-1.jpg`, `preview-2.jpg`, etc.

3. **Inspect the images** for:
   - Text cutoff or overflow at page edges
   - Table columns too narrow or text wrapping poorly
   - Heading hierarchy is visually clear
   - Colors have sufficient contrast for readability
   - Consistent spacing between sections
   - Page breaks in logical places
   - Headers/footers rendering correctly

4. **Iterate** — Fix any issues in the generation script and regenerate

Options for pdftoppm:
- `-r 150`: Resolution in DPI (adjust for quality/size balance)
- `-jpeg` or `-png`: Output format
- `-f N -l N`: Specific page range (e.g., `-f 2 -l 5` for pages 2-5)

### Alternative: Text Verification

For quick content checks without visual rendering:
```bash
# Using pandoc
pandoc document.docx -o check.md

# Using markitdown (pip install "markitdown[docx]")
python3 -m markitdown document.docx
```

## Code Style Guidelines
**IMPORTANT**: When generating code for DOCX operations:
- Write concise code
- Avoid verbose variable names and redundant operations
- Avoid unnecessary print statements

## Dependencies

Required dependencies (install if not available):

- **pandoc**: `sudo apt-get install pandoc` or `brew install pandoc` (for text extraction)
- **docx**: `npm install -g docx` (for creating new documents)
- **LibreOffice**: `sudo apt-get install libreoffice` or `brew install --cask libreoffice` (for PDF conversion)
- **Poppler**: `sudo apt-get install poppler-utils` or `brew install poppler` (for pdftoppm)
- **markitdown**: `pip install "markitdown[docx]"` (for quick text verification)
- **defusedxml**: `pip install defusedxml` (for secure XML parsing)

**IMPORTANT: NODE_PATH for globally installed packages**

When `docx` is installed globally (`npm install -g docx`), Node.js may not resolve it from project directories. Use:
```bash
NODE_PATH=$(npm root -g) node your-script.js
```