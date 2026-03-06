---
name: PDF Toolkit
description: Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms. When the agent needs to fill in a PDF form or programmatically process, generate, or analyze PDF documents at scale.
---

# PDF Processing Guide

## USER NOTIFICATION (DISPLAY IMMEDIATELY)

**When this skill is triggered, you MUST display this message to the user:**

---

📄 **PDF Operator Activated**

I can help you with PDF documents in four ways:

1. **Create New PDFs** - Build styled documents from scratch using reportlab (Python) or pdf-lib (JavaScript)
2. **Edit Existing PDFs** - Merge, split, rotate, crop, watermark, encrypt/decrypt
3. **Extract Content** - Pull text, tables, images, and metadata from PDFs
4. **Fill PDF Forms** - Complete fillable forms programmatically (see forms.md)

**What I'll do:**
- Choose the right tool for the job (reportlab for creation, pdfplumber for extraction, pypdf for manipulation)
- Follow structured workflows with visual validation
- Apply professional design with proper typography, colors, and layout
- Handle errors gracefully with fallback strategies

**Example requests:**
- "Convert this markdown file to a styled PDF report"
- "Extract all tables from quarterly-report.pdf into a spreadsheet"
- "Merge these 5 PDFs into one document"
- "Fill out this PDF form with the data I provide"
- "Create a professional PDF from this data with charts and tables"

Let me know what you'd like to do with your PDF!

---

## Overview

This guide covers PDF processing operations using Python libraries and command-line tools. For advanced features, JavaScript libraries, and detailed examples, see reference.md. If you need to fill out a PDF form, read forms.md and follow its instructions.

## Workflow Selection

Choose the right workflow based on the task:

| Task | Workflow | Primary Tool |
|------|----------|-------------|
| Create new PDF from scratch | [Create New PDF](#creating-a-new-pdf) | reportlab (Python) |
| Create PDF from markdown/HTML | [Convert to PDF](#converting-markdown-or-html-to-pdf) | reportlab Platypus |
| Extract text | [Extract Content](#extracting-content) | pdfplumber |
| Extract tables | [Extract Content](#extracting-content) | pdfplumber + pandas |
| Merge/split/rotate | [Manipulate PDFs](#manipulating-existing-pdfs) | pypdf |
| Fill PDF forms | [forms.md](forms.md) | pdf-lib or pypdf |
| OCR scanned PDFs | [OCR Workflow](#ocr-scanned-pdfs) | pytesseract + pdf2image |

---

## Creating a New PDF

### Design Principles

**CRITICAL**: Before creating any PDF, analyze the content and choose appropriate design elements:

1. **Consider the subject matter**: What is this document about? What tone does it suggest?
2. **Check for branding**: If the user mentions a company/organization, consider their brand colors
3. **Match palette to content**: Select colors that reflect the subject
4. **State your approach**: Explain your design choices before writing code

**Requirements**:
- ✅ State your content-informed design approach BEFORE writing code
- ✅ Use standard PDF fonts: Helvetica, Times-Roman, Courier (always available in reportlab)
- ✅ Create clear visual hierarchy through size, weight, and color
- ✅ Ensure readability: strong contrast, appropriate text sizes, clean alignment
- ✅ Be consistent: repeat patterns, spacing, and visual language across pages

### Color Palette Selection

Choose colors creatively based on the document's topic, audience, and tone. Build a palette of 3-5 colors:

| Palette | Primary | Secondary | Accent | Background |
|---------|---------|-----------|--------|------------|
| Corporate Blue | #1C2833 | #2E4053 | #3498DB | #F4F6F6 |
| AWS Orange | #232F3E | #37475A | #FF9900 | #FFFFFF |
| Warm Professional | #5D1D2E | #951233 | #C15937 | #FAF7F2 |
| Modern Teal | #277884 | #5EA8A7 | #FE4447 | #FFFFFF |
| Forest Green | #1E5128 | #4E9F3D | #D8E9A8 | #FFFFFF |
| Charcoal & Red | #292929 | #555555 | #E33737 | #F2F2F2 |

Use `HexColor("#RRGGBB")` from `reportlab.lib.colors` for custom colors.

### Workflow: Create from Scratch

1. **Analyze content** — Determine document type (report, guide, invoice, letter, etc.)
2. **Choose design** — Select color palette, fonts, and layout style
3. **Define custom styles** — Create ParagraphStyles for title, headings, body, code, tables
4. **Build the document** — Use Platypus flowables (Paragraph, Table, Spacer, HRFlowable, Image, PageBreak)
5. **Validate visually** — Convert first page to image and inspect for layout issues
6. **Iterate** — Fix any overflow, alignment, or readability issues

```python
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)

# 1. Setup document
doc = SimpleDocTemplate("output.pdf", pagesize=letter,
    topMargin=0.6*inch, bottomMargin=0.6*inch,
    leftMargin=0.75*inch, rightMargin=0.75*inch)

# 2. Define colors
PRIMARY = HexColor("#232F3E")
ACCENT = HexColor("#FF9900")
LIGHT_BG = HexColor("#F8F9FA")

# 3. Create custom styles
styles = getSampleStyleSheet()
styles.add(ParagraphStyle('DocTitle', parent=styles['Title'],
    fontSize=28, leading=34, textColor=PRIMARY,
    fontName='Helvetica-Bold', alignment=TA_CENTER))
styles.add(ParagraphStyle('SectionHead', parent=styles['Heading1'],
    fontSize=18, leading=24, textColor=PRIMARY,
    fontName='Helvetica-Bold', spaceBefore=20, spaceAfter=10))
styles.add(ParagraphStyle('Body', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=6, fontName='Helvetica'))
styles.add(ParagraphStyle('Code', parent=styles['Normal'],
    fontSize=9, leading=13, fontName='Courier',
    backColor=HexColor("#EAECEE"), leftIndent=12, rightIndent=12,
    spaceBefore=4, spaceAfter=8, borderWidth=0.5,
    borderColor=HexColor("#D5DBDB"), borderPadding=8))

# 4. Build content
story = []
story.append(Paragraph("Document Title", styles['DocTitle']))
story.append(HRFlowable(width="100%", thickness=2, color=ACCENT))
story.append(Spacer(1, 12))
story.append(Paragraph("Section Heading", styles['SectionHead']))
story.append(Paragraph("Body text content here.", styles['Body']))

# 5. Build PDF
doc.build(story)
```

### Tables with Professional Styling

```python
from reportlab.platypus import Table, TableStyle
from reportlab.lib.colors import HexColor

HEADER_BG = HexColor("#37475A")
ROW_ALT = HexColor("#F8F9FA")
BORDER = HexColor("#D5DBDB")

def styled_table(headers, rows, col_widths=None):
    """Create a professionally styled table."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), HexColor("#FFFFFF")),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        # Body
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor("#FFFFFF"), ROW_ALT]),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    return t
```

### Tip/Callout Boxes

```python
def tip_box(text, styles, page_width, border_color=HexColor("#FF9900"),
            bg_color=HexColor("#FFF8E7"), text_color=HexColor("#6B4C00")):
    """Create a styled callout/tip box."""
    tip_style = ParagraphStyle('Tip', parent=styles['Normal'],
        fontSize=10, leading=14, textColor=text_color,
        fontName='Helvetica-Oblique', leftIndent=12)
    data = [[Paragraph(text, tip_style)]]
    t = Table(data, colWidths=[page_width - 8])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg_color),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEBEFOREDECOR', (0, 0), (0, -1), 3, border_color),
    ]))
    return t
```

### Title Banner (Full-Width Colored Header)

```python
def title_banner(title, subtitle, page_width,
                 bg_color=HexColor("#232F3E"),
                 title_color=HexColor("#FFFFFF"),
                 subtitle_color=HexColor("#D5DBDB")):
    """Create a full-width colored title banner."""
    title_style = ParagraphStyle('BannerTitle', fontSize=28, leading=34,
        textColor=title_color, alignment=TA_CENTER, fontName='Helvetica-Bold')
    sub_style = ParagraphStyle('BannerSub', fontSize=13, leading=18,
        textColor=subtitle_color, alignment=TA_CENTER, fontName='Helvetica')
    data = [
        [Paragraph(title, title_style)],
        [Paragraph(subtitle, sub_style)],
    ]
    t = Table(data, colWidths=[page_width])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg_color),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (0, 0), 30),
        ('BOTTOMPADDING', (-1, -1), (-1, -1), 24),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
    ]))
    return t
```

---

## Converting Markdown or HTML to PDF

For converting markdown content to a styled PDF, parse the markdown structure and map to reportlab flowables:

| Markdown Element | Reportlab Flowable |
|------------------|--------------------|
| `# Heading 1` | `Paragraph(text, styles['Heading1'])` |
| `## Heading 2` | `Paragraph(text, styles['Heading2'])` |
| Body text | `Paragraph(text, styles['Normal'])` |
| `**bold**` | `<b>bold</b>` inside Paragraph |
| `*italic*` | `<i>italic</i>` inside Paragraph |
| `` `code` `` | `<font face="Courier">code</font>` inside Paragraph |
| Code block | `Paragraph(code, styles['Code'])` with monospace style |
| `- list item` | `Paragraph("• item", styles['Normal'])` with leftIndent |
| Table | `Table(data)` with `TableStyle` |
| `---` | `HRFlowable(width="100%", thickness=1)` |
| Page break | `PageBreak()` |

### Workflow

1. Read the markdown/HTML source
2. Parse structure (headings, paragraphs, lists, tables, code blocks)
3. Map each element to the corresponding reportlab flowable
4. Apply consistent styling from your chosen palette
5. Build and validate

---

## Visual Validation

After generating a PDF, validate the output visually:

### Method 1: Convert to Image (Recommended)

```python
import pypdfium2 as pdfium

def validate_pdf(pdf_path, output_prefix="preview", pages=None, scale=1.5):
    """Render PDF pages to images for visual inspection."""
    pdf = pdfium.PdfDocument(pdf_path)
    total = len(pdf)
    check_pages = pages or range(min(total, 3))  # First 3 pages by default

    for i in check_pages:
        if i >= total:
            break
        bitmap = pdf[i].render(scale=scale)
        img = bitmap.to_pil()
        img.save(f"{output_prefix}_page{i+1}.png", "PNG")
        print(f"Saved preview: {output_prefix}_page{i+1}.png")
```

### Method 2: Command-Line Preview

```bash
# Using pdftoppm (poppler-utils)
pdftoppm -jpeg -r 150 -f 1 -l 1 output.pdf preview

# Using soffice (LibreOffice)
soffice --headless --convert-to png output.pdf
```

### What to Check

- Text cutoff or overflow at page edges
- Table columns too narrow or text wrapping poorly
- Heading hierarchy is visually clear
- Colors have sufficient contrast for readability
- Consistent spacing between sections
- Page breaks in logical places

---

## Extracting Content

### Extract Text

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        print(text)
```

### Extract Tables to DataFrame

```python
import pdfplumber
import pandas as pd

with pdfplumber.open("document.pdf") as pdf:
    all_tables = []
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if table:
                df = pd.DataFrame(table[1:], columns=table[0])
                all_tables.append(df)

    if all_tables:
        combined = pd.concat(all_tables, ignore_index=True)
        combined.to_excel("extracted_tables.xlsx", index=False)
```

### Extract Metadata

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
meta = reader.metadata
print(f"Title: {meta.title}")
print(f"Author: {meta.author}")
print(f"Pages: {len(reader.pages)}")
```

---

## Manipulating Existing PDFs

### Merge PDFs

```python
from pypdf import PdfWriter, PdfReader

writer = PdfWriter()
for pdf_file in ["doc1.pdf", "doc2.pdf", "doc3.pdf"]:
    reader = PdfReader(pdf_file)
    for page in reader.pages:
        writer.add_page(page)

with open("merged.pdf", "wb") as output:
    writer.write(output)
```

### Split PDF

```python
reader = PdfReader("input.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    with open(f"page_{i+1}.pdf", "wb") as output:
        writer.write(output)
```

### Rotate Pages

```python
reader = PdfReader("input.pdf")
writer = PdfWriter()
page = reader.pages[0]
page.rotate(90)
writer.add_page(page)
with open("rotated.pdf", "wb") as output:
    writer.write(output)
```

### Add Watermark

```python
from pypdf import PdfReader, PdfWriter

watermark = PdfReader("watermark.pdf").pages[0]
reader = PdfReader("document.pdf")
writer = PdfWriter()

for page in reader.pages:
    page.merge_page(watermark)
    writer.add_page(page)

with open("watermarked.pdf", "wb") as output:
    writer.write(output)
```

### Password Protection

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
writer = PdfWriter()
for page in reader.pages:
    writer.add_page(page)

writer.encrypt("userpassword", "ownerpassword")
with open("encrypted.pdf", "wb") as output:
    writer.write(output)
```

---

## OCR Scanned PDFs

```python
import pytesseract
from pdf2image import convert_from_path

images = convert_from_path('scanned.pdf')
text = ""
for i, image in enumerate(images):
    text += f"Page {i+1}:\n"
    text += pytesseract.image_to_string(image)
    text += "\n\n"
```

---

## Command-Line Tools

### pdftotext (poppler-utils)
```bash
pdftotext input.pdf output.txt              # Extract text
pdftotext -layout input.pdf output.txt      # Preserve layout
pdftotext -f 1 -l 5 input.pdf output.txt   # Pages 1-5
```

### qpdf
```bash
qpdf --empty --pages f1.pdf f2.pdf -- merged.pdf   # Merge
qpdf input.pdf --pages . 1-5 -- out.pdf            # Extract pages
qpdf input.pdf out.pdf --rotate=+90:1               # Rotate page 1
qpdf --password=pw --decrypt enc.pdf dec.pdf         # Decrypt
```

---

## Code Style Guidelines

**IMPORTANT**: When generating code for PDF operations:
- Write concise code — avoid verbose variable names and redundant operations
- Avoid unnecessary print statements
- Use helper functions for repeated patterns (styled tables, tip boxes, banners)
- Keep the `story` list clean — build components in functions, append results

---

## Dependencies

Required dependencies (install as needed):

- **reportlab**: `pip install reportlab` (PDF creation)
- **pypdf**: `pip install pypdf` (PDF manipulation — merge, split, rotate, encrypt)
- **pdfplumber**: `pip install pdfplumber` (text and table extraction)
- **pypdfium2**: `pip install pypdfium2` (PDF rendering to images for validation)
- **pandas**: `pip install pandas` (table data processing)
- **pytesseract**: `pip install pytesseract pdf2image` (OCR for scanned PDFs)
- **poppler-utils**: `brew install poppler` / `apt install poppler-utils` (CLI tools)
- **qpdf**: `brew install qpdf` / `apt install qpdf` (CLI manipulation)

## Quick Reference

| Task | Best Tool | Key API |
|------|-----------|---------|
| Create styled PDF | reportlab | `SimpleDocTemplate` + Platypus flowables |
| Merge PDFs | pypdf | `PdfWriter.add_page()` |
| Split PDFs | pypdf | One `PdfWriter` per page |
| Extract text | pdfplumber | `page.extract_text()` |
| Extract tables | pdfplumber | `page.extract_tables()` |
| Visual validation | pypdfium2 | `page.render()` → PIL Image |
| Fill PDF forms | pdf-lib or pypdf | See forms.md |
| OCR scanned PDFs | pytesseract | Convert to image first |
| CLI merge | qpdf | `qpdf --empty --pages ...` |

## Next Steps

- For advanced pypdfium2, pdf-lib (JS), pdfjs-dist, and CLI usage, see reference.md
- For filling PDF forms, read forms.md and follow its instructions
- For troubleshooting, see the Troubleshooting section in reference.md
