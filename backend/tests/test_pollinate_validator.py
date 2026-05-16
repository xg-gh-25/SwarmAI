"""Tests for Pollinate structural validator.

Verifies:
- AC8: pollinate_validator.py exists with >= 5 structural invariant checks
"""
import json
from pathlib import Path

import pytest


def _make_valid_content_dir(tmp_path: Path) -> Path:
    """Create a content dir that passes all checks."""
    content_dir = tmp_path / "content" / "test-topic"
    content_dir.mkdir(parents=True)

    # Platform matrix
    (content_dir / "platform_matrix.md").write_text(
        "## Platform Matrix\n| Platform | Format | Dimensions |\n"
        "| 小红书 | PNG | 1080x1440 |\n| LinkedIn | PNG | 1080x1080 |\n"
    )

    # QR code
    (content_dir / "qr-swarmai.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # GitHub link in delivery text
    (content_dir / "delivery.md").write_text(
        "## Delivery\nGitHub: https://github.com/xg-gh-25/SwarmAI\n"
    )

    # 2 variants per format
    tracks_dir = content_dir / "tracks" / "poster"
    tracks_dir.mkdir(parents=True)
    (tracks_dir / "variant-a.html").write_text("<html>A</html>")
    (tracks_dir / "variant-b.html").write_text("<html>B</html>")

    # Output files with correct extensions
    (tracks_dir / "variant-a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    (tracks_dir / "variant-b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    return content_dir


class TestPollinateValidatorExists:
    """AC8: pollinate_validator.py exists with >= 5 checks."""

    def test_import(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery
        assert callable(validate_delivery)

    def test_returns_structured_result(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        result = validate_delivery(str(content_dir))

        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert "checks_passed" in result
        assert "checks_total" in result
        assert result["checks_total"] >= 5


class TestAllChecksPassing:
    """Full valid content dir passes all checks."""

    def test_valid_dir_passes(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        result = validate_delivery(str(content_dir))
        assert result["valid"] is True
        assert result["errors"] == []


class TestIndividualChecks:
    """Each check detects its specific missing element."""

    def test_missing_platform_matrix(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "platform_matrix.md").unlink()
        result = validate_delivery(str(content_dir))
        assert any("platform" in e.lower() for e in result["errors"])

    def test_missing_qr_code(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "qr-swarmai.png").unlink()
        result = validate_delivery(str(content_dir))
        assert any("qr" in e.lower() for e in result["errors"])

    def test_missing_github_link(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "delivery.md").write_text("## Delivery\nNo link here\n")
        result = validate_delivery(str(content_dir))
        assert any("github" in e.lower() for e in result["errors"])

    def test_missing_variants(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        # Remove one variant — should have < 2
        tracks = content_dir / "tracks" / "poster"
        (tracks / "variant-b.html").unlink()
        (tracks / "variant-b.png").unlink()
        result = validate_delivery(str(content_dir))
        assert any("variant" in e.lower() for e in result["errors"])
