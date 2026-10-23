---
title: Canvas file types — what renders, what does not, and the fix
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: draft for XG review
---

# Canvas file types

XG 2026-10-23: "这些提议都应该做 … 写个系统化修复的 design". Covers every file type
the Canvas (and the modal viewer, which shares FileViewer) can be asked to open.

## 1. Today (read from the code)

Routing is one function, `classifyFileForViewer` (`file-viewer/utils/fileViewTypes.ts`):
a fixed extension list per renderer, `NON_RENDERABLE_BINARY` → info card, and
**everything else → `text`** (FileEditorCore).

| Type | Today | Verdict |
|---|---|---|
| code / config / md / svg | FileEditorCore (edit, md + svg preview) | OK |
| html | sandboxed iframe on `/workspace/file/raw` | OK |
| csv / tsv | CsvRenderer (papaparse, virtual table) | OK |
| png jpg gif webp avif bmp ico | ImageRenderer (raw URL) | OK |
| pdf | react-pdf | OK |
| mp4 webm mov m4v ogv / mp3 wav ogg m4a flac aac opus | media renderers | OK |
| docx xlsx pptx doc xls ppt odt ods odp | info card (open in system app / attach / copy path) | **gap** — the agent writes these (`s_docx`, `s_xlsx`, `s_pptx`) and the user cannot see them in Canvas |
| heic heif tiff tif | info card | WebKit (macOS desktop) draws them; WebView2 / most browsers do not |
| zip, db, fonts, executables, … | info card | right |
| > 50 MB | backend 413 → "too large" notice | right |
| **any other binary** (parquet pkl npy safetensors onnx psd mkv avi pages numbers key …) | routed to `text` | **bug, see P0** |
| ipynb | raw JSON in the editor | gap |
| mmd / mermaid | plain text (MarkdownRenderer can draw mermaid) | gap |

## 2. P0 — a binary file can be shown as text and overwritten

Chain, each step in code today:

1. An unlisted extension routes to `text`.
2. `GET /workspace/file` fails `read_text(utf-8)` and falls back to the WHOLE file as
   base64 (up to 50 MB → ~67 MB of JSON), not marked readonly
   (`workspace_api.py` binary fallback).
3. FileViewer passes `cached.content` (the base64) to FileEditorCore as the text.
   The user sees a wall of base64; a large file freezes the panel.
4. Save sends that text to `PUT /workspace/file`, which writes it with
   `write_text` — **the binary file is replaced by its base64 text**. Nothing on
   either side checks.

Fix — three layers, each enough on its own to stop the corruption:

- **B1 server write guard (the structural one).** `PUT /workspace/file` refuses
  (409 `binary file, not overwritten`) when the file on disk exists and is not text.
  "Is text" = one shared helper `is_text_file(path)`: the first 8 KB has no NUL and
  decodes as UTF-8 (incremental decoder, so a cut multi-byte char at the boundary is
  fine). New files and text files write as today.
- **B2 server read: text-only mode.** The text route asks
  `GET /workspace/file?text_only=1`; a non-text file then returns 415
  `{detail: 'binary'}` after reading 8 KB, never the base64 body. Other callers
  (StageDocs, pollinate) keep today's behaviour (no flag); the base64 fallback also
  gains `readonly: true`.
- **B3 client routing.** FileViewer, on that 415 for a `text` / `markdown` / `svg` /
  `csv` route, re-routes the tab to the info card ("Binary file — not shown as
  text") with Open in system app / Attach / Copy path. It never mounts the editor.
- Labels: add the common binaries above to `NON_RENDERABLE_BINARY` only for a
  better card label/icon (mkv/avi/wmv → "video format this viewer cannot play";
  pages/numbers/key → "Apple iWork"; parquet/npy/pkl/safetensors/onnx/h5 → "data /
  model file"). Correctness does not depend on the list: an unlisted binary is
  caught by B2/B3.

## 3. P1 — Office preview (docx / xlsx / pptx)

One read-only backend endpoint, one renderer per kind, "Open in system app" kept on
every card.

`GET /workspace/file/preview?path=` → a typed result, computed in a worker thread:

| File | Library | Result |
|---|---|---|
| xlsx / xlsm | `openpyxl` (already a dependency), `read_only=True, data_only=True` | `{kind:'sheets', sheets:[{name, rows: string[][], totalRows, totalCols, truncated}]}` — first 500 rows × 50 columns per sheet, at most 20 sheets. A formula cell with no cached value (files written by openpyxl, i.e. the agent's own, have none) shows its formula `=SUM(A1:A3)`, never an empty cell. |
| pptx | `python-pptx` (already a dependency) | `{kind:'slides', slides:[{index, title, body: string[], tables: string[][][], notes, images}]}` — text outline, tables, speaker notes, image count. No slide drawing (stated on the card). |
| docx | `mammoth` (**new**: pure Python, BSD-2-Clause, pin `mammoth==1.13.0`, checked on PyPI 2026-10-23) | `{kind:'html', html}` — semantic HTML (headings, lists, tables, bold/italic, images as data URIs). |
| doc xls ppt odt ods odp | — | stay on the info card (old binary formats / OpenDocument). |

Frontend (`classifyFileForViewer` → new `office` type → `OfficeRenderer`):

- sheets → a sheet tab strip + the existing **CsvRenderer** (rows passed through
  `Papa.unparse`), so there is one table implementation;
- slides → a slide-by-slide outline (title, bullets, tables, notes);
- html → a sandboxed iframe **without** `allow-scripts` (`sandbox=""`), document
  served by the same endpoint with `format=html` as `text/html` (srcDoc renders
  blank in this webview, per HtmlRenderer).

Safety (security baseline): the files are untrusted input.
- Size: the existing 50 MB cap; plus a zip guard before parsing — total
  uncompressed size ≤ 200 MB and entry count ≤ 10 000 (zip bomb).
- Time: the conversion runs in `asyncio.to_thread` with a 10 s cap; over it → 504
  "preview took too long", card stays usable.
- mammoth's own docs say its HTML is not sanitized (a docx can carry `javascript:` links): it is shown only in the script-less sandbox above, never injected into the app's DOM.
- No macro execution (openpyxl/python-pptx/mammoth never execute VBA); xlsm is read
  as data.
- Errors to the client are generic ("could not read this file"); details in the log.
- Read-only: Office previews never offer Edit or Save.

Measure in the phase (fixtures generated in tests, not user files): a 5 000-row
sheet, a 40-slide deck, a 30-page docx; budget < 1 s each on this Mac.

Not now: pixel-exact pages via LibreOffice → PDF (needs a ~600 MB install).
Possible later as "Render pages" when `soffice` is on PATH.

## 4. P2 — images the platform may draw; video it cannot

- heic / heif / tiff / tif route to ImageRenderer. Capability, not platform
  guessing: ImageRenderer's `onError` switches to the info card ("this format is
  not shown on this system"). macOS WebKit draws them; elsewhere the card appears
  exactly as today.
- mkv / avi / wmv / flv go to the info card (WebKit cannot play them; today they
  fall into P0).

## 5. P3 — notebooks and mermaid, as previews of an editable file

FileEditorCore has two hard-coded previews (`isMarkdown`, `isSvg` + two booleans,
29 references). Replace them with ONE preview table so a new kind is one entry:

```
PREVIEWS: { md|markdown|mdx → MarkdownPreview, svg → SvgPreview,
            ipynb → NotebookPreview, mmd|mermaid → MermaidPreview }
```

One `previewOn` state, one large-file guard, one Preview / Edit / Changes switch —
so review, selection comments and "attach selection" (round 2) work in every
preview the same way.

- **ipynb**: markdown cells via MarkdownRenderer; code cells highlighted with their
  `execution_count`; outputs: `stream`, `text/plain`, `image/png|jpeg` (base64 →
  `<img>`), `error` (traceback as text). `text/html` and `application/javascript`
  outputs are NOT rendered (untrusted HTML) — shown as "HTML output (not rendered)"
  with its `text/plain` fallback when present. Invalid JSON → the Edit view with a
  notice. Edit stays raw JSON.
- **mmd**: the file body drawn through the existing MermaidDiagram.

## 6. Order, one commit each, tests first (each mutation-checked)

1. **P0** B1–B3 + labels. Tests: PUT on a binary → 409, file bytes unchanged; PUT on
   text/new file → writes; `text_only` on binary → 415 after ≤ 8 KB read; FileViewer
   on 415 → info card, no textarea; is_text_file on UTF-8 split at the 8 KB edge →
   text.
2. **P3 refactor + ipynb + mmd** (frontend). Tests: one preview table drives md/svg
   (existing md/svg tests unchanged and green); ipynb cells/outputs; html output not
   rendered; invalid ipynb → Edit; mmd → diagram node.
3. **P2**. Tests: heic routes to image; image error → card; mkv → card.
4. **P1** backend endpoint + OfficeRenderer. Tests: generated xlsx (incl. openpyxl
   formula without cached value), pptx, docx fixtures; caps; zip-bomb fixture →
   refused; timeout path; docx iframe has `sandbox=""`.

## 7. Decisions for XG

1. Add `mammoth` (BSD-2, pure Python) for docx — or skip docx preview?
2. xlsx caps: 500 rows × 50 columns × 20 sheets — OK?
3. ipynb `text/html` outputs not rendered (security) — OK?
