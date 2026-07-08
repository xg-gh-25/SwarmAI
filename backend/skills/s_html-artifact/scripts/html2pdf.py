#!/usr/bin/env python3
"""html2pdf.py — the ONE sanctioned HTML→PDF converter for SwarmAI.

Why this exists (earned 2026-07-08, run_8debb0fe):
    `chrome --headless --print-to-pdf` is NOT a reliable HTML→PDF path. On complex
    HTML (flex/grid, `min-height:100vh`, inline SVG, `white-space:nowrap` chains) the
    Chrome headless print pipeline fails inside
    `print_render_frame_helper.cc:2268 "Printing failed."` — while the PROCESS STILL
    EXITS 0 AND WRITES NO FILE. That silent "exit 0 = success" lie made a prior
    session retry the same doomed command ~15 times, then only limp to a result by
    splitting the page and stitching with pdfunite.

    Playwright drives Chromium through the DevTools `Page.printToPDF` protocol
    directly (not the CLI print path) and rendered the exact failing file in one
    shot, 5.2s. So: playwright is the engine, chrome CLI is banned, and success is
    ALWAYS verified by output file size — never by an exit code.

Public API:
    html_to_pdf(src_html, out_pdf, *, wait_until="networkidle", print_background=True,
                prefer_css_page_size=True) -> str
        Convert one HTML file to PDF. Returns the absolute output path on success.
        Raises Html2PdfError on ANY failure (missing source, missing playwright /
        browser, or a produced-but-empty PDF).

CLI:
    python html2pdf.py <src.html> [out.pdf]
    (out.pdf defaults to the source path with a .pdf suffix)

Verification contract (the anti-regression invariant):
    Success == os.path.getsize(out) > 0. A zero-byte or absent output is a FAILURE
    and raises, no matter what any underlying call returned. This is the exact
    guard against the chrome exit-0 trap.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class Html2PdfError(RuntimeError):
    """Raised (loudly) whenever HTML→PDF conversion did not produce a real PDF.

    Never swallowed, never downgraded to a return code — the whole point of this
    module is to make the silent-failure mode of chrome --print-to-pdf impossible.
    """


_INSTALL_HINT = (
    "playwright + its Chromium browser are required.\n"
    "  Install:  pip install playwright  &&  python -m playwright install chromium\n"
    "  (On this repo's venv, playwright is already a dependency — you likely just "
    "need the browser: `python -m playwright install chromium`.)"
)


def _render_pdf(uri: str, out_path: str, *, wait_until: str,
                print_background: bool, prefer_css_page_size: bool) -> None:
    """Drive Chromium via Playwright's DevTools printToPDF. Isolated so tests can
    monkeypatch it to simulate an empty-output render."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # ImportError or a broken install
        raise Html2PdfError(
            f"Cannot import playwright ({type(exc).__name__}: {exc}).\n{_INSTALL_HINT}"
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(uri, wait_until=wait_until)
                page.pdf(
                    path=out_path,
                    print_background=print_background,
                    prefer_css_page_size=prefer_css_page_size,
                )
            finally:
                browser.close()
    except Exception as exc:
        # Most common real cause: the browser binary isn't installed.
        raise Html2PdfError(
            f"Playwright render failed ({type(exc).__name__}: {exc}).\n{_INSTALL_HINT}"
        ) from exc


def html_to_pdf(
    src_html: str,
    out_pdf: str | None = None,
    *,
    wait_until: str = "networkidle",
    print_background: bool = True,
    prefer_css_page_size: bool = True,
) -> str:
    """Convert an HTML file to PDF using Playwright (Chromium). Loud on any failure.

    Args:
        src_html: path to the source .html file (must exist).
        out_pdf: destination .pdf path. Defaults to src with a .pdf suffix.
        wait_until: Playwright navigation wait state. "networkidle" waits for
            resources/fonts to settle — the right default for report HTML.
        print_background: render CSS backgrounds/colors (matches on-screen).
        prefer_css_page_size: honor `@page { size: ... }` from the HTML's CSS.

    Returns:
        The absolute output path (success is proven: the file exists and is non-empty).

    Raises:
        Html2PdfError: source missing, playwright/browser unavailable, or the
            produced PDF is missing/empty.
    """
    src_path = Path(src_html).expanduser().resolve()
    if not src_path.is_file():
        raise Html2PdfError(f"Source HTML not found: {src_path}")

    if out_pdf is None:
        out_path = str(src_path.with_suffix(".pdf"))
    else:
        out_path = str(Path(out_pdf).expanduser().resolve())

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Best-effort clean slate so a stale prior PDF can't masquerade as success.
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except OSError:
        pass  # non-fatal; the size check below is the real gate

    uri = src_path.as_uri()
    _render_pdf(
        uri,
        out_path,
        wait_until=wait_until,
        print_background=print_background,
        prefer_css_page_size=prefer_css_page_size,
    )

    # THE contract: verify by file size, never by an exit/return code.
    if not os.path.exists(out_path):
        raise Html2PdfError(
            f"Conversion reported no error but produced no file: {out_path}. "
            "This is the classic silent-failure signature — do NOT trust exit codes."
        )
    size = os.path.getsize(out_path)
    if size <= 0:
        raise Html2PdfError(
            f"Produced an EMPTY (0-byte) PDF: {out_path}. Treated as failure."
        )
    return out_path


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python html2pdf.py <src.html> [out.pdf]", file=sys.stderr)
        return 2
    src = argv[0]
    out = argv[1] if len(argv) > 1 else None
    try:
        result = html_to_pdf(src, out)
    except Html2PdfError as exc:
        print(f"html2pdf: FAILED\n{exc}", file=sys.stderr)
        return 1
    size = os.path.getsize(result)
    print(f"html2pdf: OK  {result}  ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
