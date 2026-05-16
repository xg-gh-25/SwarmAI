"""Tests for Pollinate 8-Layer Convergence Gate (convergence_gate.py).

Verifies that the convergence gate correctly identifies poster HTML violations
for each of the 8 quality layers (L1-L8) and passes well-formed HTML.
"""
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts" / "convergence_gate.py"

# ── Fixtures ────────────────────────────────────────────────────────────────


GOOD_HTML = dedent("""\
<!DOCTYPE html>
<html>
<head>
<style>
:root {
  --primary: #1a1a2e;
  --accent: #e94560;
  --bg: #0f3460;
  --text: #ffffff;
}
body { margin: 0; padding: 0; background: var(--bg); color: var(--text); }
.s { text-align: center; padding: 48px 24px; }
.hero { text-align: center; padding: 64px 24px; }
h1 { font-size: 52px; text-align: center; }
p { font-size: 22px; max-width: 700px; margin: 0 auto; text-align: center; }
.watermark { text-align: right; font-size: 12px; opacity: 0.6; }
.footer { text-align: center; padding: 24px; }
</style>
</head>
<body>
<!-- Direction: D4 Neon Pulse -->
<div class="hero">
  <h1>SwarmAI Quality Gates</h1>
  <p>Content as Black Box — zero human fixing before publish</p>
</div>
<div class="s">
  <h2 style="font-size: 36px; text-align: center;">8-Layer Verification</h2>
  <p>Every poster passes all 8 layers before you see it.</p>
</div>
<div class="footer">
  <p>🐝 SwarmAI · Your message, their attention</p>
  <img src="qr-github-light-on-dark.png" width="80" alt="QR">
  <p style="font-size: 12px;">github.com/xg-gh-25/SwarmAI</p>
</div>
<div class="watermark">🐝 Made with SwarmAI Pollinate</div>
</body>
</html>
""")


BAD_HTML_NO_DIRECTION = GOOD_HTML.replace("<!-- Direction: D4 Neon Pulse -->", "")

BAD_HTML_HARDCODED_HEX = GOOD_HTML.replace(
    "background: var(--bg)",
    "background: #0f3460"
)

BAD_HTML_MIXED_ALIGNMENT = GOOD_HTML.replace(
    '<h2 style="font-size: 36px; text-align: center;">',
    '<h2 style="font-size: 36px; text-align: left;">'
)

BAD_HTML_LARGE_GAP = GOOD_HTML.replace(
    ".s { text-align: center; padding: 48px 24px; }",
    ".s { text-align: center; padding: 120px 24px; }"
)

BAD_HTML_NO_WATERMARK = GOOD_HTML.replace(
    '<div class="watermark">🐝 Made with SwarmAI Pollinate</div>',
    ""
)

BAD_HTML_NO_QR = GOOD_HTML.replace(
    '<img src="qr-github-light-on-dark.png" width="80" alt="QR">',
    ""
)


# ── Helper ──────────────────────────────────────────────────────────────────


def run_gate(html_content: str, tmp_path: Path) -> dict:
    """Write HTML to tmp file and run convergence_gate.py, return parsed JSON."""
    html_file = tmp_path / "poster.html"
    html_file.write_text(html_content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Script did not return valid JSON.\nstdout: {result.stdout}\nstderr: {result.stderr}")


# ── Tests: All layers pass on good HTML ─────────────────────────────────────


class TestAllLayersPass:
    """Well-formed HTML should pass all 8 layers."""

    def test_good_html_passes_all(self, tmp_path):
        # Create 2 direction PNGs so L8 passes
        (tmp_path / "poster-d4-neon.png").write_bytes(b"fake")
        (tmp_path / "poster-d5-morandi.png").write_bytes(b"fake")
        # Write HTML with direction-named file
        html_file = tmp_path / "poster-d4-neon.html"
        html_file.write_text(GOOD_HTML, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        assert data["valid"] is True, f"Failures: {data['errors']}"
        assert data["checks_passed"] == 8
        assert data["errors"] == []


# ── Tests: Individual layer violations ──────────────────────────────────────


class TestL1DirectionDeclared:
    """L1: HTML must have <!-- Direction: D{N} --> comment."""

    def test_missing_direction_comment(self, tmp_path):
        result = run_gate(BAD_HTML_NO_DIRECTION, tmp_path)
        assert result["valid"] is False
        assert any("L1" in e for e in result["errors"])


class TestL2TokenPurity:
    """L2: Zero hardcoded hex in body CSS (outside :root)."""

    def test_hardcoded_hex_in_body(self, tmp_path):
        result = run_gate(BAD_HTML_HARDCODED_HEX, tmp_path)
        assert result["valid"] is False
        assert any("L2" in e for e in result["errors"])


class TestL3SpacingCompliance:
    """L3: Section gaps ≤ 72px (CSS padding check fallback)."""

    def test_excessive_padding(self, tmp_path):
        result = run_gate(BAD_HTML_LARGE_GAP, tmp_path)
        assert result["valid"] is False
        assert any("L3" in e for e in result["errors"])


class TestL4AlignmentUnity:
    """L4: All text elements must use text-align: center."""

    def test_mixed_alignment(self, tmp_path):
        result = run_gate(BAD_HTML_MIXED_ALIGNMENT, tmp_path)
        assert result["valid"] is False
        assert any("L4" in e for e in result["errors"])


class TestL5AntiSlop:
    """L5: No banned visual/structural patterns."""

    def test_gradient_text(self, tmp_path):
        bad = GOOD_HTML.replace("</style>", "h1 { background: linear-gradient(to right, red, blue); -webkit-background-clip: text; }\n</style>")
        result = run_gate(bad, tmp_path)
        assert result["valid"] is False
        assert any("L5" in e for e in result["errors"])


class TestL6PlatformFit:
    """L6: Width = 1080px viewport (checked by presence of viewport meta or render width)."""

    def test_passes_standard_poster(self, tmp_path):
        # Good HTML doesn't explicitly set viewport — script checks render width assumption
        result = run_gate(GOOD_HTML, tmp_path)
        # L6 passes because we don't have a rendered PNG to check size
        # Script should pass L6 in CSS-only mode (no Playwright)
        assert not any("L6" in e for e in result.get("errors", []))


class TestL7BrandPresent:
    """L7: Watermark + QR + GitHub link must all be present."""

    def test_missing_watermark(self, tmp_path):
        result = run_gate(BAD_HTML_NO_WATERMARK, tmp_path)
        assert result["valid"] is False
        assert any("L7" in e for e in result["errors"])

    def test_missing_qr(self, tmp_path):
        result = run_gate(BAD_HTML_NO_QR, tmp_path)
        assert result["valid"] is False
        assert any("L7" in e for e in result["errors"])


class TestL8TwoVariants:
    """L8: ≥2 direction PNGs in output dir."""

    def test_single_variant_fails(self, tmp_path):
        # Only one HTML file → L8 checks sibling PNGs with direction pattern
        html_file = tmp_path / "topic-d4-neon.html"
        html_file.write_text(GOOD_HTML, encoding="utf-8")
        # Only create 1 PNG
        (tmp_path / "topic-d4-neon.png").write_bytes(b"fake png")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        assert any("L8" in e for e in data["errors"])

    def test_two_variants_pass(self, tmp_path):
        html_file = tmp_path / "topic-d4-neon.html"
        html_file.write_text(GOOD_HTML, encoding="utf-8")
        # Create 2 PNGs with direction pattern
        (tmp_path / "topic-d4-neon.png").write_bytes(b"fake png")
        (tmp_path / "topic-d5-morandi.png").write_bytes(b"fake png")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        assert not any("L8" in e for e in data.get("errors", []))


# ── Integration test ────────────────────────────────────────────────────────


class TestIntegration:
    """End-to-end: exit code reflects validity."""

    def test_good_html_exit_0(self, tmp_path):
        html_file = tmp_path / "topic-d4-neon.html"
        html_file.write_text(GOOD_HTML, encoding="utf-8")
        (tmp_path / "topic-d4-neon.png").write_bytes(b"fake")
        (tmp_path / "topic-d5-morandi.png").write_bytes(b"fake")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_bad_html_exit_1(self, tmp_path):
        html_file = tmp_path / "poster.html"
        html_file.write_text(BAD_HTML_NO_DIRECTION, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(html_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1
