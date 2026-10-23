"""Read-only previews of Office files for the Canvas (docs/design/canvas-file-types.md P1).

The agent writes docx / xlsx / pptx (the s_docx / s_xlsx / s_pptx skills); this turns
one into something the viewer can show without the native app. Every file is
UNTRUSTED input: it is size- and zip-checked before any parser touches it, parsers
never execute macros, and callers get a generic error.

- ``OfficePreviewError``  — a file that cannot be previewed (message is user-safe)
- ``preview_kind``        — 'sheets' | 'slides' | 'html' | None for a file name
- ``check_zip``           — the zip-bomb guard (total uncompressed size, entry count)
- ``preview_xlsx`` / ``preview_pptx`` / ``preview_docx_html`` — the converters
- ``build_preview``       — guard + dispatch, returns the JSON-ready dict
"""
from __future__ import annotations

import zipfile
from pathlib import Path

MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_ROWS = 500
MAX_COLS = 50
MAX_SHEETS = 20

_KIND = {".xlsx": "sheets", ".xlsm": "sheets", ".pptx": "slides", ".docx": "html"}


class OfficePreviewError(Exception):
    """The file cannot be previewed. ``str(exc)`` is safe to show the user."""


def preview_kind(name: str) -> str | None:
    return _KIND.get(Path(name).suffix.lower())


def check_zip(path: Path) -> None:
    """Refuse a zip bomb before a parser inflates it."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise OfficePreviewError("not a valid Office file") from exc
    if len(infos) > MAX_ENTRIES:
        raise OfficePreviewError("too many parts to preview")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise OfficePreviewError("too large to preview once unpacked")


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def preview_xlsx(path: Path) -> dict:
    """First MAX_ROWS x MAX_COLS of up to MAX_SHEETS sheets, as text.

    Values are the cached results; a formula with no cached result (a file written by
    openpyxl — the agent's own — was never calculated by Excel) shows its formula, so
    a computed cell is never shown empty.
    """
    import openpyxl

    values = openpyxl.load_workbook(path, read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        sheets = []
        for ws_v, ws_f in list(zip(values.worksheets, formulas.worksheets))[:MAX_SHEETS]:
            rows: list[list[str]] = []
            total_rows = ws_v.max_row or 0
            total_cols = ws_v.max_column or 0
            vi = ws_v.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True)
            fi = ws_f.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True)
            for vr, fr in zip(vi, fi):
                rows.append([
                    _cell_text(fv if (v is None and isinstance(fv, str) and fv.startswith("=")) else v)
                    for v, fv in zip(vr, fr)
                ])
            while rows and not any(rows[-1]):
                rows.pop()
            # read-only sheets pad rows to max_col: drop the empty columns at the end
            width = max((max((i + 1 for i, c in enumerate(r) if c), default=0) for r in rows), default=0)
            rows = [r[:width] for r in rows]
            sheets.append({
                "name": ws_v.title,
                "rows": rows,
                "totalRows": total_rows,
                "totalCols": total_cols,
                "truncated": total_rows > MAX_ROWS or total_cols > MAX_COLS,
            })
        return {"kind": "sheets", "sheets": sheets, "totalSheets": len(values.worksheets)}
    finally:
        values.close()
        formulas.close()


def preview_pptx(path: Path) -> dict:
    """Per slide: title, text lines, tables, speaker notes, picture count."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    slides = []
    for idx, slide in enumerate(prs.slides, start=1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        body: list[str] = []
        tables: list[list[list[str]]] = []
        images = 0
        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                images += 1
            if getattr(shape, "has_table", False) and shape.has_table:
                tables.append([[c.text for c in r.cells] for r in shape.table.rows])
            elif shape.has_text_frame and shape is not slide.shapes.title:
                for p in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        body.append(("  " * p.level) + t)
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        slides.append({"index": idx, "title": title, "body": body, "tables": tables,
                       "notes": notes, "images": images})
    return {"kind": "slides", "slides": slides}


def preview_docx_html(path: Path) -> str:
    """Semantic HTML of a docx (mammoth). NOT sanitized: shown only in a script-less
    sandboxed iframe (sandbox=""), never put into the app's DOM."""
    import mammoth

    with open(path, "rb") as fh:
        result = mammoth.convert_to_html(fh)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>body{font:14px/1.6 -apple-system,system-ui,sans-serif;margin:24px;max-width:860px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}img{max-width:100%}</style>"
        f"</head><body>{result.value}</body></html>"
    )


def build_preview(path: Path) -> dict:
    """Guard, then convert. Raises OfficePreviewError for anything not previewable."""
    kind = preview_kind(path.name)
    if kind is None:
        raise OfficePreviewError("no preview for this file type")
    check_zip(path)
    try:
        if kind == "sheets":
            return preview_xlsx(path)
        if kind == "slides":
            return preview_pptx(path)
        return {"kind": "html"}  # the document itself is served with format=html
    except OfficePreviewError:
        raise
    except Exception as exc:  # noqa: BLE001 — any parser failure is "cannot preview"
        raise OfficePreviewError("could not read this file") from exc
