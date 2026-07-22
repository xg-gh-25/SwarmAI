# Track H: Document (DOCX)

> Professional documents — six-pagers, white papers, proposals, guides.
> Content completeness is the primary rule. No artificial caps.

## When This Track Runs

This track executes during BUILD stage when `"document"` is in `confirmed_tracks`
(from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — full Narrative + Evidence Layers
- `content/{name}/PRFAQ.md` — structure source for six-pagers
- `content/{name}/discovery.json` — audience, outcome context
- Active design direction YAML — for heading colors, accent elements

## Production Flow

```
Step 1: Determine document type from audience + outcome
Step 2: Build document structure (python-docx)
Step 3: Populate content from content_package (no truncation)
Step 4: Apply brand styling (heading colors, callout boxes, header/footer)
Step 5: Generate PDF companion (Playwright render of DOCX → print)
Step 6: Quality verification (RP-H)
```

---

## Step 1: Document Type Selection

Based on `discovery.json` audience + outcome:

| Audience + Outcome | Document Type | Structure |
|-------------------|---------------|-----------|
| Leadership + alignment | Six-pager (Amazon style) | PR/FAQ format → deep appendix |
| Customer + action | Proposal | Exec Summary → Background → Recommendation → Implementation → Budget |
| Community + education | White paper | Abstract → Problem → Approach → Evidence → Conclusion |
| Team + education | Guide / playbook | Overview → Steps × N → Examples × N → FAQ → References |
| Any + data_decision | Technical spec | Overview → Architecture → API → Data Model → Migration → Testing |

**Content drives length.** A white paper can be 5 pages or 30 pages — determined by
content_package depth, not a template cap.

---

## Step 2: Build Document Structure (python-docx)

```python
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
import yaml

doc = Document()

# Load direction tokens
with open(direction_yaml_path) as f:
    direction = yaml.safe_load(f)
tokens = direction['tokens']
accent_rgb = RGBColor.from_string(tokens['accent'].lstrip('#'))

# ─── Custom Styles ────────────────────────────────────────────────────────

# Heading 1 — section titles
style_h1 = doc.styles['Heading 1']
style_h1.font.name = 'Inter'
style_h1.font.size = Pt(22)
style_h1.font.color.rgb = accent_rgb
style_h1.font.bold = True
style_h1.paragraph_format.space_before = Pt(24)
style_h1.paragraph_format.space_after = Pt(12)

# Heading 2 — subsections
style_h2 = doc.styles['Heading 2']
style_h2.font.name = 'Inter'
style_h2.font.size = Pt(16)
style_h2.font.bold = True
style_h2.paragraph_format.space_before = Pt(18)
style_h2.paragraph_format.space_after = Pt(8)

# Heading 3 — sub-subsections
style_h3 = doc.styles['Heading 3']
style_h3.font.name = 'Inter'
style_h3.font.size = Pt(13)
style_h3.font.bold = True

# Body text
style_body = doc.styles['Normal']
style_body.font.name = 'Inter'
style_body.font.size = Pt(11)
style_body.paragraph_format.line_spacing = 1.5
style_body.paragraph_format.space_after = Pt(8)
```

### Document Header/Footer

```python
from docx.oxml.ns import qn

section = doc.sections[0]
section.top_margin = Cm(2.5)
section.bottom_margin = Cm(2.5)
section.left_margin = Cm(2.5)
section.right_margin = Cm(2.5)

# Header: logo + title + date
header = section.header
header_para = header.paragraphs[0]
header_para.text = f"{{EMOJI}} {{PROJECT_NAME}} | {document_title} | {date}"
header_para.style.font.size = Pt(8)
header_para.style.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

# Footer: page number
footer = section.footer
footer_para = footer.paragraphs[0]
footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer_para.text = "Page "
# Add page number field (requires XML manipulation)
```

---

## Step 3: Populate Content

### Executive Summary (ALWAYS first for documents >2 pages)

```python
doc.add_heading('Executive Summary', level=1)
doc.add_paragraph(
    f"{thesis}. {top_finding}. {implication}. {recommended_action}."
)
```

### Body Sections (from content_package Narrative Layer)

For EACH key point in content_package:

```python
doc.add_heading(f'{section_title}', level=2)

# Main content — NO TRUNCATION
for paragraph in section_content:
    doc.add_paragraph(paragraph)

# Evidence callout (if data supports this section)
if evidence:
    # Add shaded callout box
    callout = doc.add_paragraph()
    callout.paragraph_format.left_indent = Cm(1)
    run = callout.add_run(f"→ {evidence}")
    run.font.italic = True
    run.font.color.rgb = accent_rgb
```

### Subheadings for Readability

**Rule: Every ~500 words gets a subheading (H3) for navigation.**
This is NOT a content cap — it's a readability aid. The content stays complete;
subheadings break it into scannable chunks.

```python
# Check if section exceeds ~500 words without a break
if len(section_content.split()) > 500 and no_subheading:
    # Insert logical break point with H3
    doc.add_heading(f'{logical_subtopic}', level=3)
```

### Appendix (encouraged)

```python
doc.add_page_break()
doc.add_heading('Appendix', level=1)

# Supporting data, raw evidence, extended examples
doc.add_heading('A. Data Tables', level=2)
# ... full data that supports body claims

doc.add_heading('B. Code Examples', level=2)
# ... complete code (not truncated snippets)

doc.add_heading('C. References', level=2)
# ... full citation list with URLs
```

---

## Step 4: Brand Styling

### Callout Boxes (for key insights)

```python
from docx.oxml import OxmlElement

def add_callout(doc, text, accent_color):
    """Add a branded callout box (shaded paragraph with left border)."""
    para = doc.add_paragraph()
    para.paragraph_format.left_indent = Cm(0.5)
    para.paragraph_format.space_before = Pt(12)
    para.paragraph_format.space_after = Pt(12)

    # Add shading
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), 'F5F5F5')
    shading.set(qn('w:val'), 'clear')
    para.paragraph_format.element.append(shading)

    run = para.add_run(f"💡 {text}")
    run.font.italic = True
    run.font.size = Pt(10)
```

### Table Styling (for data sections)

```python
from docx.table import Table

table = doc.add_table(rows=len(data)+1, cols=len(headers))
table.style = 'Table Grid'

# Header row with brand color
for i, header in enumerate(headers):
    cell = table.rows[0].cells[i]
    cell.text = header
    cell.paragraphs[0].runs[0].font.bold = True
    # Apply accent background to header row
```

---

## Step 5: PDF Companion

After DOCX is complete, generate a PDF version for email attachment:

```python
# Option 1: LibreOffice headless conversion
import subprocess
subprocess.run([
    'soffice', '--headless', '--convert-to', 'pdf',
    '--outdir', output_dir, docx_path
], timeout=30)

# Option 2: If soffice unavailable, note in delivery
# "PDF version requires LibreOffice. DOCX is the primary deliverable."
```

---

## Step 6: Quality Verification (RP-H)

| # | Pattern | How to Verify | Gate |
|---|---------|--------------|------|
| RP-H1 | Heading hierarchy | No skipped levels. H1→H2→H3 in order. `grep -n "Heading" docx_xml` | BLOCKING |
| RP-H2 | Executive summary | Present if document >2 pages. Contains thesis + finding + action. | BLOCKING |
| RP-H3 | Readability | No section >500 words without a subheading (H3). Count words between headings. | BLOCKING |
| RP-H4 | Evidence density | Every body section has ≥1 data point, quote, example, or callout | BLOCKING |
| RP-H5 | Brand header | Document header contains logo + title + date | BLOCKING |
| RP-H6 | TOC present | `doc.add_table_of_contents()` or manual TOC if >5 pages | BLOCKING (>5 pages only) |
| RP-H7 | Content completeness | Every key point from content_package appears. No silent omission. | BLOCKING |

---

## Output Files

| File | Always? | Content |
|------|---------|---------|
| `tracks/document/{topic}.docx` | ✅ | Branded Word document |
| `tracks/document/{topic}.pdf` | If soffice available | PDF print version |

---

## Tracked Changes (Review Mode)

When the document is a proposal or six-pager that needs review, use OOXML XML editing
to add tracked changes after initial creation.

**Note:** DOCX and PPTX are both OOXML (ZIP) containers — the same unpack/pack scripts
work for both.

```python
# Unpack DOCX for XML editing
import subprocess
OOXML_SCRIPTS = "${SWARM_WORKSPACE:-$PWD}/.claude/skills/s_pptx/ooxml/scripts"
subprocess.run(["python3", f"{OOXML_SCRIPTS}/unpack.py", "{topic}.docx", "_unpacked/"])

# Add comments/tracked changes via document.py (Python library, NOT CLI)
# Import and use the DocxXMLEditor class:
import sys
sys.path.insert(0, "${SWARM_WORKSPACE:-$PWD}/.claude/skills/s_docx/scripts")
from document import DocxXMLEditor

editor = DocxXMLEditor("_unpacked/")
# editor.add_comment(start_node, end_node, text="Review comment here", author="REDACTED")
# editor.add_tracked_deletion(node, author="XG")
# editor.add_tracked_insertion(node, text="suggested text", author="XG")
editor.save()

# Repack
subprocess.run(["python3", f"{OOXML_SCRIPTS}/pack.py", "_unpacked/", "{topic}.docx"])
```

This is OPTIONAL — only when user needs a review-mode document (e.g., "add comments
for Bo to review" or "mark suggested changes"). Most decks deliver without tracked changes.

---

## Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Document is getting long, summarize the rest" | Content completeness. If the content exists in content_package, it goes in. Use appendix for supporting data. |
| "TOC is overkill for 6 pages" | Threshold is 5 pages. 6 > 5. Add TOC. Reader shouldn't hunt for sections. |
| "Heading hierarchy doesn't matter for internal docs" | It matters for accessibility, TOC generation, and reader navigation. Always enforce. |
| "Skip the appendix, main body is enough" | If evidence exists that supports the argument but isn't core narrative → appendix. Better there than omitted. |
| "PDF conversion failed, just deliver DOCX" | That's fine — DOCX is the primary deliverable. Note in delivery that PDF needs soffice. |
| "Executive summary is the same as the abstract" | Executive summary = thesis + top finding + action recommendation. Abstract = scope + methodology. Different. |
