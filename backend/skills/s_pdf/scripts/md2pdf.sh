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
#   md2pdf.sh --batch [options] <file1.md> <file2.md> ...
#
# Options:
#   --style <name>     Template: professional (default), minimal
#   --preset <name>    Presets: pe-review, memo, default
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
# Dependencies: pandoc (3.x), tectonic
# Optional: rsvg-convert (SVG→PDF), pdftoppm (preview)
# Install: brew install pandoc tectonic librsvg poppler
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

# ── Unicode sanitization (LaTeX-safe replacements) ──
if $SANITIZE; then
    SANITIZED_INPUT="$(mktemp "${TMPDIR:-/tmp}/md2pdf-XXXXXX").md"
    # Chain cleanup: remove sanitized file, then run existing EXIT trap
    PREV_TRAP="$(trap -p EXIT | sed -n "s/^trap -- '\\(.*\\)' EXIT$/\\1/p")"
    trap "rm -f '$SANITIZED_INPUT'; ${PREV_TRAP:-true}" EXIT

    sed \
        -e 's/→/$\\rightarrow$/g' \
        -e 's/←/$\\leftarrow$/g' \
        -e 's/↑/$\\uparrow$/g' \
        -e 's/↓/$\\downarrow$/g' \
        -e 's/≥/$\\geq$/g' \
        -e 's/≤/$\\leq$/g' \
        -e 's/≈/$\\approx$/g' \
        -e 's/≠/$\\neq$/g' \
        -e 's/±/$\\pm$/g' \
        -e 's/×/$\\times$/g' \
        -e 's/÷/$\\div$/g' \
        -e 's/∞/$\\infty$/g' \
        -e 's/✅/\\checkmark{}/g' \
        -e 's/❌/$\\times$/g' \
        -e 's/⏸/\\textbar\\textbar{}/g' \
        -e 's/🔴/(P0)/g' \
        -e 's/🟡/(P1)/g' \
        -e 's/🔵/(P2)/g' \
        -e 's/📌/[PIN]/g' \
        "$ACTUAL_INPUT" > "$SANITIZED_INPUT"

    # Count replacements (approximate)
    SANITIZE_COUNT=$(diff "$ACTUAL_INPUT" "$SANITIZED_INPUT" | grep -c '^[<>]' || true)
    if [[ $SANITIZE_COUNT -gt 0 ]]; then
        echo "→ Sanitized $((SANITIZE_COUNT / 2)) Unicode symbols for LaTeX"
    fi
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
