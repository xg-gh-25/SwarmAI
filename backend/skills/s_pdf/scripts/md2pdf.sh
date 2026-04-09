#!/usr/bin/env bash
# md2pdf.sh — Production-grade Markdown-to-PDF converter
#
# Pipeline: Markdown → pandoc (parse) → tectonic/XeLaTeX (typeset) → PDF
#
# Features:
#   - Full CJK support (PingFang SC) with Unicode symbol fallback
#   - Professional tables (booktabs), syntax-highlighted code blocks
#   - Styled blockquotes, auto TOC, page numbers
#   - SVG images auto-converted to PDF via rsvg-convert
#   - Multiple style templates (professional, minimal)
#
# Usage:
#   md2pdf.sh <input.md> [output.pdf] [options]
#
# Options:
#   --style <name>     Template: professional (default), minimal
#   --toc              Include table of contents
#   --page <size>      Page size: a4 (default), letter
#   --title <title>    Override document title (use --title "" to suppress)
#   --author <author>  Set document author
#   --date <date>      Set document date
#   --preview          Generate JPEG preview of first page
#
# Dependencies: pandoc (3.x), tectonic
# Optional: rsvg-convert (SVG→PDF), pdftoppm (preview)
# Install: brew install pandoc tectonic librsvg poppler
#
# Examples:
#   md2pdf.sh README.md
#   md2pdf.sh design.md output.pdf --style professional --toc --preview
#   md2pdf.sh report.md --title "Q1 Report" --author "XG"
#   md2pdf.sh doc.md --title ""   # suppress YAML frontmatter title

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/templates"

# ── Defaults ──
INPUT=""
OUTPUT=""
STYLE="professional"
TOC=""
PAGE_SIZE="a4"
TITLE=""
TITLE_SET=false
AUTHOR=""
DATE=""
PREVIEW=false
EXTRA_PANDOC_ARGS=()

# ── Parse args ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --style)   STYLE="$2"; shift 2 ;;
        --toc)     TOC="--toc"; shift ;;
        --page)    PAGE_SIZE="$2"; shift 2 ;;
        --title)   TITLE="$2"; TITLE_SET=true; shift 2 ;;
        --author)  AUTHOR="$2"; shift 2 ;;
        --date)    DATE="$2"; shift 2 ;;
        --preview) PREVIEW=true; shift ;;
        -V)        EXTRA_PANDOC_ARGS+=("-V" "$2"); shift 2 ;;
        --)        shift; break ;;
        --*)       EXTRA_PANDOC_ARGS+=("$1"); shift ;;
        *)
            if [[ -z "$INPUT" ]]; then INPUT="$1"
            elif [[ -z "$OUTPUT" ]]; then OUTPUT="$1"
            fi
            shift ;;
    esac
done

# ── Validate ──
if [[ -z "$INPUT" ]]; then
    cat <<'USAGE'
md2pdf — Markdown to professional PDF

Usage: md2pdf.sh <input.md> [output.pdf] [options]

Options:
  --style <name>     professional (default), minimal
  --toc              Include table of contents
  --page <size>      a4 (default), letter
  --title <title>    Document title (--title "" to suppress)
  --author <author>  Document author
  --date <date>      Document date
  --preview          Generate JPEG preview of page 1

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

cleanup() { [[ -n "${TMPDIR_SVG:-}" ]] && rm -rf "$TMPDIR_SVG"; }
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

# ── Build pandoc command ──
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
