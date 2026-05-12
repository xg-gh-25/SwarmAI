#!/usr/bin/env bash
# md2pdf.sh — Production-grade Markdown-to-PDF converter
#
# Two engines:
#   tectonic (default) — pandoc → XeLaTeX → PDF. Best for: clean docs, CJK, print quality.
#   weasyprint         — pandoc → HTML → PDF. Best for: inline SVG diagrams, heavy unicode, architecture docs.
#
# Features:
#   - Full CJK support (PingFang SC) with Unicode symbol fallback
#   - Professional tables (booktabs/CSS), syntax-highlighted code blocks
#   - Styled blockquotes, auto TOC, page numbers (page/total)
#   - SVG handling: tectonic converts via rsvg; weasyprint renders natively
#   - Multiple style templates (professional, minimal)
#   - Code-block-aware Unicode sanitization (won't crash verbatim)
#
# Usage:
#   md2pdf.sh <input.md> [output.pdf] [options]
#   md2pdf.sh --batch [options] <file1.md> <file2.md> ...
#
# Options:
#   --style <name>     Template: professional (default), minimal
#   --preset <name>    Presets: pe-review, memo, default
#   --engine <name>    Engine: tectonic (default), weasyprint (for SVG-heavy docs)
#   --toc              Include table of contents
#   --page <size>      Page size: a4 (default), letter
#   --title <title>    Override document title (use --title "" to suppress)
#   --author <author>  Set document author
#   --date <date>      Set document date
#   --preview          Generate JPEG preview of first page
#   --batch            Process multiple .md files with same settings
#   --no-sanitize      Skip Unicode sanitization (if your template handles it)
#
# Presets:
#   pe-review   TOC + numbered sections + 1in margins + letter + colored links
#   memo        No TOC, minimal, letter, compact margins
#   default     Professional style, a4, no TOC
#
# Engine selection guide:
#   Use tectonic when: clean markdown, CJK text, print-quality typography needed
#   Use weasyprint when: inline SVG diagrams, heavy emoji/unicode, architecture docs
#
# Dependencies: pandoc (3.x), tectonic OR weasyprint
# Optional: rsvg-convert (SVG→PDF for tectonic), pdftoppm (preview)
# Install: brew install pandoc tectonic librsvg poppler
#          pip install weasyprint  # for --engine weasyprint
#
# Examples:
#   md2pdf.sh README.md
#   md2pdf.sh design.md output.pdf --style professional --toc --preview
#   md2pdf.sh report.md --title "Q1 Report" --author "XG"
#   md2pdf.sh doc.md --title ""   # suppress YAML frontmatter title
#   md2pdf.sh --preset pe-review design.md             # PE-quality single file
#   md2pdf.sh --preset pe-review --batch *.md           # PE-quality batch

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/templates"

# ── Defaults ──
INPUT=""
OUTPUT=""
STYLE="professional"
PRESET=""
TOC=""
PAGE_SIZE="a4"
TITLE=""
TITLE_SET=false
AUTHOR=""
DATE=""
PREVIEW=false
BATCH_MODE=false
SANITIZE=true
ENGINE="tectonic"  # tectonic (default) or weasyprint (for SVG-heavy docs)
EXTRA_PANDOC_ARGS=()
BATCH_FILES=()

# ── Parse args ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --style)       STYLE="$2"; shift 2 ;;
        --preset)      PRESET="$2"; shift 2 ;;
        --toc)         TOC="--toc"; shift ;;
        --page)        PAGE_SIZE="$2"; shift 2 ;;
        --title)       TITLE="$2"; TITLE_SET=true; shift 2 ;;
        --author)      AUTHOR="$2"; shift 2 ;;
        --date)        DATE="$2"; shift 2 ;;
        --preview)     PREVIEW=true; shift ;;
        --batch)       BATCH_MODE=true; shift ;;
        --no-sanitize) SANITIZE=false; shift ;;
        --engine)      ENGINE="$2"; shift 2 ;;
        -V)            EXTRA_PANDOC_ARGS+=("-V" "$2"); shift 2 ;;
        --)            shift; break ;;
        --*)           EXTRA_PANDOC_ARGS+=("$1"); shift ;;
        *)
            if $BATCH_MODE; then
                BATCH_FILES+=("$1")
            elif [[ -z "$INPUT" ]]; then INPUT="$1"
            elif [[ -z "$OUTPUT" ]]; then OUTPUT="$1"
            fi
            shift ;;
    esac
done

# ── Apply preset (override individual settings) ──
case "$PRESET" in
    pe-review)
        TOC="--toc"
        PAGE_SIZE="letter"
        EXTRA_PANDOC_ARGS+=("--toc-depth=3" "-N")
        EXTRA_PANDOC_ARGS+=("-V" "colorlinks=true" "-V" "linkcolor=blue")
        EXTRA_PANDOC_ARGS+=("-V" "urlcolor=blue" "-V" "toccolor=blue")
        EXTRA_PANDOC_ARGS+=("-V" "fontsize=11pt")
        ;;
    memo)
        STYLE="minimal"
        PAGE_SIZE="letter"
        ;;
    default|"") ;; # use defaults
    *)
        echo "Error: Unknown preset '$PRESET'. Available: pe-review, memo, default" >&2
        exit 1 ;;
esac

# ── Batch mode ──
if $BATCH_MODE; then
    if [[ ${#BATCH_FILES[@]} -eq 0 ]]; then
        echo "Error: --batch requires at least one .md file" >&2
        exit 1
    fi
    SUCCESS=0
    FAIL=0
    for f in "${BATCH_FILES[@]}"; do
        if [[ ! -f "$f" ]]; then
            echo "⚠ Skip: $f (not found)" >&2
            FAIL=$((FAIL + 1))
            continue
        fi
        OUT="${f%.md}.pdf"
        echo "── $(basename "$f") ──"
        # Re-invoke self for each file (inherits all options except --batch and files)
        SELF_ARGS=()
        [[ -n "$PRESET" ]]          && SELF_ARGS+=(--preset "$PRESET")
        [[ "$STYLE" != "professional" ]] && SELF_ARGS+=(--style "$STYLE")
        [[ -n "$TOC" && -z "$PRESET" ]] && SELF_ARGS+=(--toc)
        [[ "$PAGE_SIZE" != "a4" && -z "$PRESET" ]] && SELF_ARGS+=(--page "$PAGE_SIZE")
        $PREVIEW                     && SELF_ARGS+=(--preview)
        ! $SANITIZE                  && SELF_ARGS+=(--no-sanitize)
        bash "$0" "$f" "$OUT" "${SELF_ARGS[@]}" "${EXTRA_PANDOC_ARGS[@]+"${EXTRA_PANDOC_ARGS[@]}"}" 2>&1 || true
        if [[ -f "$OUT" ]]; then
            SUCCESS=$((SUCCESS + 1))
        else
            echo "✗ Failed: $f" >&2
            FAIL=$((FAIL + 1))
        fi
    done
    echo "── Batch complete: $SUCCESS succeeded, $FAIL failed ──"
    exit $( [[ $FAIL -gt 0 ]] && echo 1 || echo 0 )
fi

# ── Validate ──
if [[ -z "$INPUT" ]]; then
    cat <<'USAGE'
md2pdf — Markdown to professional PDF

Usage: md2pdf.sh <input.md> [output.pdf] [options]
       md2pdf.sh --preset pe-review --batch *.md

Options:
  --style <name>     professional (default), minimal
  --preset <name>    pe-review | memo | default
  --toc              Include table of contents
  --page <size>      a4 (default), letter
  --title <title>    Document title (--title "" to suppress)
  --author <author>  Document author
  --date <date>      Document date
  --preview          Generate JPEG preview of page 1
  --batch            Process multiple .md files
  --no-sanitize      Skip Unicode→LaTeX replacement

Presets:
  pe-review   TOC + numbered sections + letter + 1in margins + colored links
  memo        No TOC, minimal style, letter, compact

Dependencies: pandoc, tectonic (brew install pandoc tectonic)
Optional:     librsvg (SVG images), poppler (preview)
USAGE
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: Input file not found: $INPUT" >&2
    exit 1
fi

for cmd in pandoc tectonic; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd not found. Install: brew install $cmd" >&2
        exit 1
    fi
done

[[ -z "$OUTPUT" ]] && OUTPUT="${INPUT%.md}.pdf"

# ── Template ──
TEMPLATE="$TEMPLATE_DIR/${STYLE}.tex"
if [[ ! -f "$TEMPLATE" ]]; then
    echo "Error: Template '$STYLE' not found" >&2
    echo "Available: $(ls "$TEMPLATE_DIR"/*.tex 2>/dev/null | xargs -I{} basename {} .tex | tr '\n' ' ')" >&2
    exit 1
fi

# ── Geometry ──
case "$PAGE_SIZE" in
    a4)     GEOMETRY="a4paper,margin=2.2cm" ;;
    letter) GEOMETRY="letterpaper,margin=1in" ;;
    *)      GEOMETRY="$PAGE_SIZE" ;;
esac

# ── SVG pre-conversion (LaTeX can't render SVG) ──
INPUT_DIR="$(cd "$(dirname "$INPUT")" && pwd)"
TMPDIR_SVG=""
SVG_COUNT=0

cleanup() { [[ -n "${TMPDIR_SVG:-}" ]] && rm -rf "$TMPDIR_SVG"; true; }
trap cleanup EXIT

# Extract SVG references: ![...](path.svg) — portable grep (no -P flag)
while IFS= read -r line; do
    # Extract path from ![...](path.svg)
    svg_ref="$(echo "$line" | sed -n 's/.*](\([^)]*\.svg\)).*/\1/p')"
    [[ -z "$svg_ref" ]] && continue

    # Resolve relative to input file directory
    if [[ "$svg_ref" == /* ]]; then
        svg_path="$svg_ref"
    else
        svg_path="$INPUT_DIR/$svg_ref"
    fi

    if [[ -f "$svg_path" ]]; then
        if command -v rsvg-convert &>/dev/null; then
            [[ -z "$TMPDIR_SVG" ]] && TMPDIR_SVG="$(mktemp -d)"
            pdf_name="$(basename "${svg_ref%.svg}.pdf")"
            if rsvg-convert -f pdf -o "$TMPDIR_SVG/$pdf_name" "$svg_path" 2>/dev/null; then
                SVG_COUNT=$((SVG_COUNT + 1))
            else
                echo "⚠ Failed to convert: $svg_ref" >&2
            fi
        else
            echo "⚠ rsvg-convert not found — SVG images will show as placeholders (brew install librsvg)" >&2
            break
        fi
    fi
done < <(grep '\.svg)' "$INPUT" 2>/dev/null || true)

# Build pandoc input: if SVGs converted, use temp copy with .svg→.pdf refs
ACTUAL_INPUT="$INPUT"
if [[ $SVG_COUNT -gt 0 && -n "$TMPDIR_SVG" ]]; then
    ACTUAL_INPUT="$TMPDIR_SVG/$(basename "$INPUT")"
    sed 's/\.svg)/.pdf)/g' "$INPUT" > "$ACTUAL_INPUT"
    echo "→ Converted $SVG_COUNT SVG(s) to PDF for embedding"
fi

# ── Unicode sanitization (code-block-aware) ──
# CRITICAL: Inside code blocks (```...```), LaTeX math commands crash (verbatim mode).
# Solution: text → LaTeX math commands, code blocks → ASCII equivalents.
if $SANITIZE; then
    SANITIZED_INPUT="$(mktemp "${TMPDIR:-/tmp}/md2pdf-XXXXXX").md"
    # Chain cleanup: remove sanitized file, then run existing EXIT trap
    PREV_TRAP="$(trap -p EXIT | sed -n "s/^trap -- '\\(.*\\)' EXIT$/\\1/p")"
    trap "rm -f '$SANITIZED_INPUT'; ${PREV_TRAP:-true}" EXIT

    python3 - "$ACTUAL_INPUT" "$SANITIZED_INPUT" << 'PYEOF'
import re, sys

input_path, output_path = sys.argv[1], sys.argv[2]
with open(input_path) as f:
    content = f.read()

# Split on code fences — odd indices are code blocks
parts = re.split(r'(```[^`]*```)', content, flags=re.DOTALL)

# LaTeX math replacements (for prose text)
TEXT_MAP = {
    '→': r'$\rightarrow$', '←': r'$\leftarrow$',
    '↑': r'$\uparrow$', '↓': r'$\downarrow$',
    '≥': r'$\geq$', '≤': r'$\leq$',
    '≈': r'$\approx$', '≠': r'$\neq$',
    '±': r'$\pm$', '×': r'$\times$', '÷': r'$\div$',
    '∞': r'$\infty$',
    '✅': r'\checkmark{}', '✓': r'\checkmark{}',
    '❌': r'$\times$', '✗': r'$\times$',
    '⏸': r'\textbar\textbar{}',
    '🔴': '(P0)', '🟡': '(P1)', '🔵': '(P2)',
    '📌': '[PIN]', '📋': '[LIST]',
    '🌱': '[sparse]', '🌿': '[growing]', '🌳': '[mature]', '⚡': '[fast]',
    '🐝': '[bee]',
}

# ASCII replacements (for inside code blocks — verbatim, no LaTeX allowed)
CODE_MAP = {
    '→': '->', '←': '<-', '↑': '^', '↓': 'v',
    '≥': '>=', '≤': '<=', '≈': '~=', '≠': '!=',
    '±': '+/-', '×': 'x', '÷': '/',
    '∞': 'inf',
    '✅': '[ok]', '✓': '[ok]', '❌': '[no]', '✗': '[x]',
    '🔴': '(P0)', '🟡': '(P1)', '🔵': '(P2)',
    '📌': '[PIN]', '📋': '[LIST]',
    '🌱': '[sparse]', '🌿': '[growing]', '🌳': '[mature]', '⚡': '[fast]',
    '🐝': '[bee]', '▼': 'v', '▶': '>', '◄': '<', '►': '>',
}

count = 0
result = []
for i, part in enumerate(parts):
    if i % 2 == 1:  # Code block (odd index after split on fence pattern)
        for old, new in CODE_MAP.items():
            if old in part:
                part = part.replace(old, new)
                count += 1
    else:  # Prose text
        for old, new in TEXT_MAP.items():
            if old in part:
                part = part.replace(old, new)
                count += 1
    result.append(part)

with open(output_path, 'w') as f:
    f.write(''.join(result))

if count > 0:
    print(f'→ Sanitized {count} Unicode symbols (text: LaTeX math, code: ASCII)', file=sys.stderr)
PYEOF

    ACTUAL_INPUT="$SANITIZED_INPUT"
fi

# ── Table width warning ──
# Detect tables with 5+ columns that may overflow in LaTeX
WIDE_TABLES=$(grep -n '^|.*|.*|.*|.*|.*|' "$ACTUAL_INPUT" | head -1 || true)
if [[ -n "$WIDE_TABLES" ]]; then
    LINE_NUM=$(echo "$WIDE_TABLES" | cut -d: -f1)
    COL_COUNT=$(echo "$WIDE_TABLES" | tr -cd '|' | wc -c | tr -d ' ')
    if [[ $COL_COUNT -ge 6 ]]; then
        echo "⚠ Wide table (~$((COL_COUNT - 1)) cols) near line $LINE_NUM — may overflow. Consider splitting." >&2
    fi
fi

# ── WeasyPrint engine (alternative for SVG-heavy/unicode-heavy docs) ──
if [[ "$ENGINE" == "weasyprint" ]]; then
    echo "→ Using WeasyPrint engine (SVG-friendly, no LaTeX)"

    # Generate HTML with pandoc, then PDF with weasyprint
    HTML_TMP="$(mktemp "${TMPDIR:-/tmp}/md2pdf-XXXXXX").html"
    trap "rm -f '$HTML_TMP'; ${PREV_TRAP:-true}" EXIT

    # pandoc → standalone HTML (no sanitization needed — HTML renders all unicode)
    pandoc "$INPUT" -o "$HTML_TMP" --standalone --from markdown --to html \
        --metadata title="" ${TOC:+--toc --toc-depth=2}

    # Post-process HTML: inject CSS + inline SVG files + fix SVG rendering issues
    python3 - "$HTML_TMP" "$INPUT_DIR" "$PAGE_SIZE" << 'WEASY_POST'
import re, sys
from pathlib import Path

html_path, input_dir, page_size = sys.argv[1], sys.argv[2], sys.argv[3]

with open(html_path) as f:
    html = f.read()

# 1. Inject compact full-width CSS
css = f'''<style>
body{{font-family:-apple-system,"Helvetica Neue",sans-serif;max-width:none;margin:0;padding:0;font-size:10pt;line-height:1.3;color:#1a1a1a}}
h1{{font-size:20pt;border-bottom:2px solid #333;padding-bottom:5px;margin-top:0.6em;margin-bottom:0.3em}}
h2{{font-size:14pt;margin-top:1.2em;margin-bottom:0.2em;border-bottom:1px solid #ddd;padding-bottom:3px;page-break-after:avoid}}
h3{{font-size:11pt;color:#333;margin-top:0.8em;margin-bottom:0.15em;page-break-after:avoid}}
p{{margin:0.3em 0}}
table{{border-collapse:collapse;width:100%;margin:0.5em 0;font-size:9pt}}
th,td{{border:1px solid #ccc;padding:3px 5px;text-align:left}}
th{{background:#f5f5f5;font-weight:600}}
code{{font-family:Menlo,monospace;font-size:8pt;background:#f6f6f6;padding:1px 3px;border-radius:2px}}
pre{{background:#f6f6f6;padding:6px;border-radius:4px;font-size:8pt;line-height:1.25;overflow-x:auto;margin:0.4em 0}}
pre code{{background:none;padding:0}}
hr{{border:none;border-top:1px solid #ddd;margin:0.8em 0}}
blockquote{{border-left:3px solid #1976d2;margin:0.5em 0;padding:0.3em 0.8em;background:#f8f9fa;font-size:9.5pt}}
img,svg{{max-width:100%;width:100%;height:auto}}
.diagram{{width:100%;margin:0.6em 0;page-break-inside:avoid}}
.diagram svg{{width:100%;height:auto}}
.diagram-caption{{text-align:center;font-size:8pt;color:#666;margin-top:2px;font-style:italic}}
a{{color:#1976d2}}
ul,ol{{margin:0.2em 0;padding-left:1.4em}}
li{{margin:0.1em 0}}
@page{{size:{page_size};margin:1cm 1cm 1.3cm 1cm}}
@page{{@bottom-center{{content:counter(page) " / " counter(pages);font-size:8pt;color:#888}}}}
</style>'''
html = html.replace('</head>', css + '</head>', 1)

# 2. Inline SVG files: replace <img src="*.svg"> with actual SVG content
def inline_svg(match):
    src = match.group(1)
    # Resolve relative to input directory
    svg_path = Path(input_dir) / src if not src.startswith('/') else Path(src)
    if svg_path.exists():
        svg_content = svg_path.read_text()
        # Fix SVG for WeasyPrint: add fill="none" to stroke paths, remove markers
        def fix_path(m):
            tag = m.group(0)
            if 'fill=' not in tag and 'stroke' in tag:
                tag = tag.replace('<path ', '<path fill="none" ', 1)
            return tag
        svg_content = re.sub(r'<path [^>]*>', fix_path, svg_content)
        svg_content = re.sub(r'<defs>.*?</defs>', '', svg_content, flags=re.DOTALL)
        svg_content = re.sub(r' marker-end="url\(#[^"]*\)"', '', svg_content)
        # Wrap in diagram div for page-break-inside: avoid
        alt = match.group(2) if match.group(2) else ''
        return f'<div class="diagram">{svg_content}<p class="diagram-caption">{alt}</p></div>'
    return match.group(0)  # Leave as-is if file not found

# Match <figure><img src="*.svg" ...></figure> patterns (pandoc wraps images in <figure>)
# Also handles multiline <img> tags (pandoc puts attributes on separate lines)
def replace_figures(html_text):
    """Replace <figure> blocks containing SVG images with inlined SVG content."""
    def figure_replace(m):
        figure_html = m.group(0)
        # Extract src and alt from within the figure
        src_match = re.search(r'src="([^"]*\.svg)"', figure_html)
        alt_match = re.search(r'alt="([^"]*)"', figure_html)
        if src_match:
            # Create a fake match object for inline_svg
            class FakeMatch:
                def group(self, i):
                    if i == 1: return src_match.group(1)
                    if i == 2: return alt_match.group(1) if alt_match else ''
                    return ''
            result = inline_svg(FakeMatch())
            if '<svg' in result:
                return result
        return figure_html
    return re.sub(r'<figure>.*?</figure>', figure_replace, html_text, flags=re.DOTALL)

html = replace_figures(html)

# Also handle standalone <img> (not in <figure>) — single-line or multiline
html = re.sub(r'<img[^>]*src="([^"]*\.svg)"[^>]*alt="([^"]*)"[^>]*/?>',  inline_svg, html, flags=re.DOTALL)
html = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*src="([^"]*\.svg)"[^>]*/?>',
              lambda m: inline_svg(type('M', (), {'group': lambda s,i: m.group(2) if i==1 else m.group(1)})()),
              html, flags=re.DOTALL)

# Count inlined SVGs
svg_count = html.count('<div class="diagram"><svg')
if svg_count > 0:
    print(f'→ Inlined {svg_count} SVG diagram(s) from files', file=sys.stderr)

with open(html_path, 'w') as f:
    f.write(html)
WEASY_POST

    # weasyprint → PDF (DYLD_LIBRARY_PATH for macOS homebrew libs)
    # Note: weasyprint emits CSS warnings to stderr — suppress. Exit code unreliable, check output file.
    set +e
    DYLD_LIBRARY_PATH=/opt/homebrew/lib weasyprint "$HTML_TMP" "$OUTPUT" 2>/dev/null
    set -e

    if [[ -f "$OUTPUT" ]]; then
        SIZE=$(du -h "$OUTPUT" | cut -f1 | tr -d ' ')
        PAGES=$(pdfinfo "$OUTPUT" 2>/dev/null | awk '/^Pages:/{print $2}' || echo "?")
        echo "✓ $OUTPUT (${SIZE}, ${PAGES} pages) [weasyprint]"
        exit 0
    else
        echo "Error: WeasyPrint failed to produce output" >&2
        exit 1
    fi
fi

# ── Build pandoc command (tectonic engine) ──
PANDOC_ARGS=(
    "$ACTUAL_INPUT"
    -o "$OUTPUT"
    --pdf-engine=tectonic
    --template="$TEMPLATE"
    --resource-path="${TMPDIR_SVG:+$TMPDIR_SVG:}$INPUT_DIR:."
    -V "geometry=$GEOMETRY"
    -V "CJKmainfont=PingFang SC"
    -V "CJKsansfont=PingFang SC"
    -V "CJKmonofont=PingFang SC"
    -V "monofont=Menlo"
    -V "fontsize=10pt"
    --syntax-highlighting=tango
)

[[ -n "$TOC" ]]    && PANDOC_ARGS+=("--toc")
$TITLE_SET         && PANDOC_ARGS+=(-V "title=$TITLE")
[[ -n "$AUTHOR" ]] && PANDOC_ARGS+=(-V "author=$AUTHOR")
[[ -n "$DATE" ]]   && PANDOC_ARGS+=(-V "date=$DATE")
PANDOC_ARGS+=("${EXTRA_PANDOC_ARGS[@]+"${EXTRA_PANDOC_ARGS[@]}"}")

# ── Run pandoc ──
# Note: first run may take ~90s as tectonic downloads LaTeX packages.
# Subsequent runs are fast (~5s) since packages are cached.
pandoc "${PANDOC_ARGS[@]}"

SIZE=$(du -h "$OUTPUT" | cut -f1 | tr -d ' ')
PAGES=$(pdfinfo "$OUTPUT" 2>/dev/null | awk '/^Pages:/{print $2}' || echo "?")
echo "✓ $OUTPUT (${SIZE}, ${PAGES} pages)"

# ── Preview ──
if $PREVIEW; then
    PREVIEW_FILE="${OUTPUT%.pdf}-preview.jpg"
    if command -v pdftoppm &>/dev/null; then
        pdftoppm -jpeg -r 150 -f 1 -l 1 "$OUTPUT" "${PREVIEW_FILE%.jpg}"
        # pdftoppm appends -N suffix (varies: -1, -01, -001)
        for f in "${PREVIEW_FILE%.jpg}"-*.jpg; do
            [[ -f "$f" ]] && mv "$f" "$PREVIEW_FILE" && break
        done
        [[ -f "$PREVIEW_FILE" ]] && echo "✓ Preview: $PREVIEW_FILE"
    else
        echo "⚠ pdftoppm not found — skip preview (brew install poppler)" >&2
    fi
fi
