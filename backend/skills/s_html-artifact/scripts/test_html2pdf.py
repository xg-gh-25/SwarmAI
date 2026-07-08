"""Tests for html2pdf.py — the playwright-only HTML→PDF converter.

Methodology: the module MUST verify success by output file size, NOT by an exit
code (the whole reason this script exists — chrome --print-to-pdf returns 0 while
silently emitting "Printing failed" and producing no file on complex HTML). So the
key invariants are:

  1. A real HTML file → a non-empty PDF on disk (end-to-end, real playwright).
  2. Missing/empty output is detected and raised as Html2PdfError (never a silent
     "success" — the exact failure mode of the chrome CLI path).
  3. A missing playwright browser/module fails LOUD with actionable guidance,
     not a bare traceback or silent no-op.

The end-to-end test is skipped (not failed) when playwright's browser binary is
absent, so the suite stays green on a machine without it — but the error-path
tests (which need no browser) always run.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "html2pdf.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("html2pdf", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _playwright_browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            b.close()
        return True
    except Exception:
        return False


def test_module_exposes_convert_and_error():
    mod = _load_module()
    assert hasattr(mod, "html_to_pdf"), "must expose html_to_pdf(src, out)"
    assert hasattr(mod, "Html2PdfError"), "must expose Html2PdfError for loud failures"


def test_success_returns_path_when_render_produces_file(tmp_path, monkeypatch):
    """BEHAVIORAL (no browser needed): a render that writes a non-empty file →
    html_to_pdf returns the output path. Drives the REAL html_to_pdf, mocking only
    the render boundary."""
    mod = _load_module()
    html = tmp_path / "x.html"
    html.write_text("<html><body><h1>x</h1></body></html>", encoding="utf-8")
    out = tmp_path / "x.pdf"

    def _fake_render(uri, out_path, **kwargs):  # signature-compatible with kwargs
        Path(out_path).write_bytes(b"%PDF-1.4\n...")  # non-empty

    monkeypatch.setattr(mod, "_render_pdf", _fake_render)
    result = mod.html_to_pdf(str(html), str(out))
    assert os.path.getsize(out) > 0
    assert str(out) in str(result)


def test_verifies_by_filesize_not_exit_code():
    """The core invariant: success is verified via os.path.getsize, and the module
    never shells out to a browser CLI. This is a lint-style structural guard that
    COMPLEMENTS the behavioral empty-output test (which proves the guard fires) —
    it is not the primary proof on its own."""
    src = Path(_SCRIPT).read_text(encoding="utf-8")
    assert "getsize" in src, "success MUST be verified via os.path.getsize(out), not exit code"
    # It must NOT actually shell out to chrome (the banned, silently-failing path).
    # Check for real invocation, not docstring mentions: no subprocess module, and
    # no reference to a Chrome executable being launched.
    assert "import subprocess" not in src, "must not shell out to a browser CLI"
    assert "Google Chrome" not in src, "must not invoke the Chrome.app binary"


def test_missing_source_raises(tmp_path):
    mod = _load_module()
    missing = tmp_path / "does-not-exist.html"
    out = tmp_path / "out.pdf"
    with pytest.raises(mod.Html2PdfError):
        mod.html_to_pdf(str(missing), str(out))


@pytest.mark.skipif(
    not _playwright_browser_available(),
    reason="playwright chromium browser not installed on this machine",
)
def test_end_to_end_produces_nonempty_pdf(tmp_path):
    """Render a non-trivial HTML (flex + grid + 100vh + inline SVG — the exact
    shape that made chrome --print-to-pdf fail) and assert a real, non-empty PDF."""
    mod = _load_module()
    html = tmp_path / "complex.html"
    html.write_text(
        """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <style>
          .page { min-height:100vh; display:flex; }
          .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
          .node { white-space:nowrap; min-width:150px; }
        </style></head><body>
        <section class="page"><div class="grid">
          <div class="node">alpha</div><div class="node">beta</div><div class="node">gamma</div>
        </div>
        <svg width="100" height="40"><rect width="100" height="40" fill="#2563EB"/></svg>
        </section></body></html>""",
        encoding="utf-8",
    )
    out = tmp_path / "complex.pdf"
    result = mod.html_to_pdf(str(html), str(out))
    assert os.path.exists(out), "PDF must exist on disk"
    assert os.path.getsize(out) > 0, "PDF must be non-empty (the chrome-CLI failure mode)"
    # Returned path should point at the produced file.
    assert str(out) in str(result)


def test_empty_output_detected_as_failure(tmp_path, monkeypatch):
    """THE load-bearing anti-regression test: if rendering yields a zero-byte file,
    html_to_pdf must RAISE Html2PdfError, never return a fake success. This is what
    stops the chrome exit-0 trap. The render is mocked → NO browser required, so
    this must ALWAYS run (never skipif-gated). The mock signature is kwargs-
    compatible with the real _render_pdf call so the size check — not a TypeError —
    is what fires."""
    mod = _load_module()
    html = tmp_path / "x.html"
    html.write_text("<html><body><h1>x</h1></body></html>", encoding="utf-8")
    out = tmp_path / "x.pdf"

    def _fake_render(uri, out_path, **kwargs):
        Path(out_path).write_bytes(b"")  # zero bytes — the failure signature

    monkeypatch.setattr(mod, "_render_pdf", _fake_render)
    with pytest.raises(mod.Html2PdfError):
        mod.html_to_pdf(str(html), str(out))
