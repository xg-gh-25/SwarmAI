#!/usr/bin/env python3
"""Generate PE-ready PDFs from design doc markdown files.

Uses markdown→HTML→PDF via Playwright (headless Chromium).
Matches the styling of the existing Architecture Design Doc PDFs.

Usage:
    python3 Projects/SwarmAI/assets/generate-design-pdfs.py
"""

import sys
import os
import markdown
from pathlib import Path

ASSETS_DIR = Path(__file__).parent
OUTPUT_DIR = ASSETS_DIR.parent  # Projects/SwarmAI/

DOCS = [
    {
        "md": ASSETS_DIR / "Next-Gen-Agent-Intelligence-Design-Doc.md",
        "pdf": OUTPUT_DIR / "2026-04-15-next-gen-agent-intelligence-design.pdf",
        "title": "Next-Gen Agent Intelligence — Design Document",
    },
    {
        "md": ASSETS_DIR / "Memory-Management-Design-Doc.md",
        "pdf": OUTPUT_DIR / "2026-04-15-memory-management-design.pdf",
        "title": "Memory Management — Design Document",
    },
]

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
    --text: #1a1a2e;
    --text-secondary: #4a4a6a;
    --accent: #2563eb;
    --accent-light: #eff6ff;
    --border: #e2e8f0;
    --bg-code: #f8fafc;
    --bg-table-header: #f1f5f9;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 10.5pt;
    line-height: 1.6;
    color: var(--text);
    max-width: 100%;
    padding: 0;
}

h1 {
    font-size: 22pt;
    font-weight: 700;
    color: var(--text);
    margin: 0 0 8pt 0;
    padding-bottom: 8pt;
    border-bottom: 3px solid var(--accent);
    page-break-after: avoid;
}

h2 {
    font-size: 16pt;
    font-weight: 600;
    color: var(--accent);
    margin: 20pt 0 8pt 0;
    padding-bottom: 4pt;
    border-bottom: 1px solid var(--border);
    page-break-after: avoid;
}

h3 {
    font-size: 12pt;
    font-weight: 600;
    color: var(--text);
    margin: 14pt 0 6pt 0;
    page-break-after: avoid;
}

h4 {
    font-size: 10.5pt;
    font-weight: 600;
    color: var(--text-secondary);
    margin: 10pt 0 4pt 0;
    page-break-after: avoid;
}

p { margin: 0 0 8pt 0; }

strong { font-weight: 600; }

a { color: var(--accent); text-decoration: none; }

table {
    width: 100%;
    border-collapse: collapse;
    margin: 8pt 0 12pt 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}

th {
    background: var(--bg-table-header);
    font-weight: 600;
    text-align: left;
    padding: 6pt 8pt;
    border: 1px solid var(--border);
}

td {
    padding: 5pt 8pt;
    border: 1px solid var(--border);
    vertical-align: top;
}

tr:nth-child(even) { background: #fafafa; }

code {
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 9pt;
    background: var(--bg-code);
    padding: 1pt 4pt;
    border-radius: 3pt;
    border: 1px solid var(--border);
}

pre {
    background: var(--bg-code);
    border: 1px solid var(--border);
    border-radius: 4pt;
    padding: 10pt 12pt;
    margin: 8pt 0 12pt 0;
    overflow-x: auto;
    font-size: 8.5pt;
    line-height: 1.5;
    page-break-inside: avoid;
}

pre code {
    background: none;
    border: none;
    padding: 0;
    font-size: 8.5pt;
}

blockquote {
    border-left: 3px solid var(--accent);
    background: var(--accent-light);
    padding: 8pt 12pt;
    margin: 8pt 0 12pt 0;
    color: var(--text-secondary);
    font-style: italic;
}

ul, ol {
    margin: 4pt 0 8pt 0;
    padding-left: 20pt;
}

li { margin: 2pt 0; }

hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 16pt 0;
}

em { color: var(--text-secondary); }

/* Page break hints */
h2 { page-break-before: auto; }
table, pre, blockquote { page-break-inside: avoid; }
"""


def md_to_html(md_path: Path, title: str) -> str:
    """Convert markdown to styled HTML."""
    md_text = md_path.read_text(encoding="utf-8")

    # Convert markdown to HTML
    html_body = markdown.markdown(
        md_text,
        extensions=[
            "tables",
            "fenced_code",
            "codehilite",
            "toc",
            "smarty",
        ],
        extension_configs={
            "codehilite": {"css_class": "highlight", "guess_lang": False},
        },
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""


def html_to_pdf(html: str, pdf_path: Path, title: str):
    """Convert HTML to PDF via Playwright."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")

        page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "1in", "bottom": "1in", "left": "0.75in", "right": "0.75in"},
            display_header_footer=True,
            header_template=(
                '<div style="font-size:8pt; color:#94a3b8; width:100%; '
                'text-align:center; font-family:Inter,sans-serif;">'
                f"SwarmAI — {title}"
                "</div>"
            ),
            footer_template=(
                '<div style="font-size:8pt; color:#94a3b8; width:100%; '
                'text-align:center; font-family:Inter,sans-serif;">'
                'Page <span class="pageNumber"></span> of '
                '<span class="totalPages"></span> — '
                "Confidential: For PE Review Only"
                "</div>"
            ),
            print_background=True,
        )

        browser.close()


def main():
    for doc in DOCS:
        md_path = doc["md"]
        pdf_path = doc["pdf"]
        title = doc["title"]

        if not md_path.exists():
            print(f"SKIP: {md_path} not found")
            continue

        print(f"Generating: {pdf_path.name}")
        print(f"  Source: {md_path.name}")

        html = md_to_html(md_path, title)
        html_to_pdf(html, pdf_path, title)

        size_kb = pdf_path.stat().st_size / 1024
        print(f"  Output: {pdf_path.name} ({size_kb:.0f} KB)")

    print("\nDone.")


if __name__ == "__main__":
    main()
