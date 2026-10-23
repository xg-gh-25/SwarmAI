"""Office previews for the Canvas (docs/design/canvas-file-types.md P1).

What is tested (fixtures generated here — no user files):
- xlsx: sheets, rows as text, the formula shown when there is no cached value (a
  workbook written by openpyxl, like the agent's own), the row cap + truncated flag;
- pptx: per slide title, body lines, table, speaker notes;
- docx: ``format=html`` returns a document as text/html with a CSP that allows no
  script; the JSON form says ``kind: html``;
- the zip-bomb guard refuses before parsing; a broken file → 422 generic; a non-Office
  file → 415; a 10 000-row sheet stays under 1 s.

Methodology: the real endpoint through TestClient on a tmp workspace (workspace path
lookup monkeypatched), plus the converter module directly for the limits.
"""
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import office_preview as op


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()

    async def _fake_ws_path():
        return str(ws)
    monkeypatch.setattr("routers.workspace_api._get_workspace_path", _fake_ws_path)
    return ws


def _xlsx(path: Path, rows: int = 3):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["name", "qty"])
    for i in range(rows):
        ws.append([f"item{i}", i + 1])
    ws.append(["total", f"=SUM(B2:B{rows + 1})"])
    wb.create_sheet("Empty")
    wb.save(path)


def _pptx(path: Path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Roadmap"
    s.placeholders[1].text_frame.text = "Ship the Canvas"
    t = s.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(4), Inches(1)).table
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Q", "Goal", "Q4", "GA"
    s.notes_slide.notes_text_frame.text = "say hi"
    prs.save(path)


DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Plan</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>Hello docx</w:t></w:r></w:p></w:body></w:document>'
)


def _docx(path: Path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", DOCX_XML)


def test_xlsx_sheets_and_formula_fallback(client: TestClient, tmp_ws: Path):
    _xlsx(tmp_ws / "a.xlsx")
    data = client.get("/api/workspace/file/preview", params={"path": "a.xlsx"}).json()
    assert data["kind"] == "sheets"
    assert [s["name"] for s in data["sheets"]] == ["Data", "Empty"]
    rows = data["sheets"][0]["rows"]
    assert rows[0] == ["name", "qty"] and rows[1] == ["item0", "1"]
    assert rows[-1] == ["total", "=SUM(B2:B4)"]  # never calculated → the formula, not ""
    assert data["sheets"][0]["truncated"] is False


def test_xlsx_row_cap(tmp_ws: Path):
    _xlsx(tmp_ws / "big.xlsx", rows=10_000)
    t0 = time.perf_counter()
    data = op.preview_xlsx(tmp_ws / "big.xlsx")
    elapsed = time.perf_counter() - t0
    sheet = data["sheets"][0]
    assert len(sheet["rows"]) == op.MAX_ROWS and sheet["truncated"] is True
    assert sheet["totalRows"] == 10_002
    assert elapsed < 1.0, elapsed


def test_pptx_outline(client: TestClient, tmp_ws: Path):
    _pptx(tmp_ws / "d.pptx")
    data = client.get("/api/workspace/file/preview", params={"path": "d.pptx"}).json()
    s = data["slides"][0]
    assert s["title"] == "Roadmap"
    assert "Ship the Canvas" in s["body"]
    assert s["tables"] == [[["Q", "Goal"], ["Q4", "GA"]]]
    assert s["notes"] == "say hi"


def test_docx_html_is_a_scriptless_document(client: TestClient, tmp_ws: Path):
    _docx(tmp_ws / "p.docx")
    assert client.get("/api/workspace/file/preview", params={"path": "p.docx"}).json() == {"kind": "html"}
    resp = client.get("/api/workspace/file/preview", params={"path": "p.docx", "format": "html"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "<h1>Plan</h1>" in resp.text and "Hello docx" in resp.text


def test_zip_bomb_refused_before_parsing(tmp_ws: Path, monkeypatch):
    _xlsx(tmp_ws / "a.xlsx")
    monkeypatch.setattr(op, "MAX_UNCOMPRESSED", 100)
    with pytest.raises(op.OfficePreviewError, match="too large"):
        op.build_preview(tmp_ws / "a.xlsx")


def test_broken_file_is_a_generic_422(client: TestClient, tmp_ws: Path):
    (tmp_ws / "bad.xlsx").write_bytes(b"not a zip")
    resp = client.get("/api/workspace/file/preview", params={"path": "bad.xlsx"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "not a valid Office file"


def test_non_office_is_415(client: TestClient, tmp_ws: Path):
    (tmp_ws / "a.md").write_text("# x", encoding="utf-8")
    assert client.get("/api/workspace/file/preview", params={"path": "a.md"}).status_code == 415
