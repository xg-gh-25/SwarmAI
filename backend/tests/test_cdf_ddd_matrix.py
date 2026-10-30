"""The DDD routing matrix (contracts.DDD_MATRIX): every section it names exists verbatim
in the DDD skeleton, the sections are generic skeleton headings, and the check fails
when the skeleton drifts away from it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.ingestion.contracts import DDD_MATRIX, validate_ddd_matrix


def test_every_ddd_matrix_heading_exists_verbatim():
    validate_ddd_matrix()
    assert DDD_MATRIX["product"]["model"] == ("PRODUCT.md", "What Makes It Different")
    assert DDD_MATRIX["architecture"]["model"] == ("TECH.md", "Key Subsystems")


def test_ddd_matrix_sections_are_generic_skeleton_headings():
    root = Path(__file__).resolve().parents[1] / "templates" / "ddd-skeleton"
    matrix_sections = {
        name: {section for row in DDD_MATRIX.values() for file_name, section in row.values()
               if file_name == name}
        for name in {cell[0] for row in DDD_MATRIX.values() for cell in row.values()}
    }
    for name, sections in matrix_sections.items():
        headings = set(re.findall(
            r"^##\s+(.+?)\s*$", (root / name).read_text(encoding="utf-8"), re.MULTILINE
        ))
        assert sections <= headings
    assert all("swarmai" not in section.lower()
               for sections in matrix_sections.values() for section in sections)


def test_the_matrix_check_reads_the_skeleton_and_fails_when_it_drifts(tmp_path):
    """C2: the default root is the skeleton; a skeleton heading the matrix names going
    away makes validate_ddd_matrix fail, so the two cannot drift apart silently."""
    skeleton = Path(__file__).resolve().parents[1] / "templates" / "ddd-skeleton"
    for doc in skeleton.glob("*.md"):
        text = doc.read_text(encoding="utf-8")
        if doc.name == "TECH.md":
            text = text.replace("## Key Subsystems", "## Key Parts")
        (tmp_path / doc.name).write_text(text, encoding="utf-8")
    validate_ddd_matrix(skeleton)
    with pytest.raises(ValueError, match="Key Subsystems"):
        validate_ddd_matrix(tmp_path)
