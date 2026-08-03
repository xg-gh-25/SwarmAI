"""Tests for DDD Entity Index extraction.

Tests the entity_extractor module which scans DDD markdown files
across all projects and produces a flat routing table of entities
(## headings) mapped to their project/doc#section location.
"""

import pytest

from core.entity_extractor import (
    extract_entities_from_ddd,
    format_entity_index,
    prune_entity_index,
)


@pytest.fixture
def projects_dir(tmp_path):
    """Create a realistic Projects/ structure with DDD docs."""
    # Project 1: SwarmAI
    swarm = tmp_path / "SwarmAI"
    swarm.mkdir()
    (swarm / "TECH.md").write_text(
        "# SwarmAI -- Technical Context\n\n"
        "## Architecture\n\nThe system uses a daemon model.\n\n"
        "## Stack\n\nPython + TypeScript + Rust\n\n"
        "## Key Subsystems\n\nSessions, memory, skills.\n"
    )
    (swarm / "IMPROVEMENT.md").write_text(
        "# SwarmAI -- Lessons & Patterns\n\n"
        "## What Worked\n\n- Adversarial review\n\n"
        "## What Failed\n\n- Big-bang refactors\n\n"
        "## Known Issues\n\n- xdist deadlock\n"
    )
    (swarm / "PRODUCT.md").write_text(
        "# SwarmAI -- Product Context\n\n"
        "## Vision\n\nPersonal AI command center.\n\n"
        "## What Makes SwarmAI Different\n\nPersistent context.\n"
    )
    (swarm / "PROJECT.md").write_text(
        "# SwarmAI -- Current Context\n\n"
        "## Current Focus\n\nDDD Cultivation.\n\n"
        "## Open Items\n\n- Entity Index\n"
    )

    # Project 2: SalesIntel
    client = tmp_path / "SalesIntel"
    client.mkdir()
    (client / "TECH.md").write_text(
        "# SalesIntel -- Technical Context\n\n"
        "## Architecture\n\nDataProxy + SDK.\n\n"
        "## Data Sources\n\nAthena, Forecast API.\n"
    )
    (client / "IMPROVEMENT.md").write_text(
        "# SalesIntel -- Lessons\n\n"
        "## What Worked\n\n- Rocky templates.\n\n"
        "## What Failed\n\n- Month_sequence only has value 1.\n"
    )

    # Project 3: PhysicalAI (minimal)
    phys = tmp_path / "PhysicalAI"
    phys.mkdir()
    (phys / "PRODUCT.md").write_text(
        "# PhysicalAI -- Product Context\n\n"
        "## Vision\n\nPhysical AI V-Team.\n"
    )

    return tmp_path


class TestExtractEntitiesFromDDD:
    """Core extraction logic."""

    def test_extracts_h2_headings_as_entities(self, projects_dir):
        """AC1+AC2: Extracts ## headings from DDD docs with correct references."""
        entities = extract_entities_from_ddd(projects_dir)
        # Should find "Architecture" in both SwarmAI and SalesIntel
        arch_refs = [e for e in entities if e.name == "Architecture"]
        assert len(arch_refs) >= 2
        # Each ref should have project + doc + section
        swarm_arch = [e for e in arch_refs if e.project == "SwarmAI"]
        assert len(swarm_arch) == 1
        assert swarm_arch[0].doc == "TECH"
        assert swarm_arch[0].section == "Architecture"

    def test_skips_h1_and_h3_headings(self, projects_dir):
        """Boundary: Never parse ### headings (too granular)."""
        entities = extract_entities_from_ddd(projects_dir)
        names = [e.name for e in entities]
        # H1 titles like "SwarmAI -- Technical Context" should not appear
        assert not any("Technical Context" in n for n in names)
        # If there were ### headings, they should not appear
        assert not any(n.startswith("###") for n in names)

    def test_returns_entities_from_multiple_projects(self, projects_dir):
        """AC1: At least 2 different projects represented."""
        entities = extract_entities_from_ddd(projects_dir)
        projects = set(e.project for e in entities)
        assert len(projects) >= 2

    def test_handles_empty_projects_dir(self, tmp_path):
        """Edge case: no projects at all."""
        empty = tmp_path / "empty"
        empty.mkdir()
        entities = extract_entities_from_ddd(empty)
        assert entities == []

    def test_handles_unreadable_file(self, projects_dir):
        """Edge case: OS error on read — skip, don't crash."""
        # Make a file unreadable by writing invalid encoding marker
        bad_project = projects_dir / "BadProject"
        bad_project.mkdir()
        bad_file = bad_project / "TECH.md"
        bad_file.write_bytes(b"\xff\xfe" + b"\x00" * 100)
        # Should not raise, just skip the bad file
        entities = extract_entities_from_ddd(projects_dir)
        bad_refs = [e for e in entities if e.project == "BadProject"]
        assert bad_refs == []

    def test_strips_heading_whitespace(self, projects_dir):
        """Edge case: headings with trailing spaces."""
        extra = projects_dir / "ExtraProject"
        extra.mkdir()
        (extra / "TECH.md").write_text(
            "# Title\n\n## Messy Heading   \n\nContent.\n"
        )
        entities = extract_entities_from_ddd(projects_dir)
        messy = [e for e in entities if e.project == "ExtraProject"]
        assert any(e.name == "Messy Heading" for e in messy)

    def test_skips_dot_directories(self, projects_dir):
        """Ignore .artifacts, .health etc."""
        hidden = projects_dir / ".hidden_project"
        hidden.mkdir()
        (hidden / "TECH.md").write_text("# Hidden\n\n## Secret\n\nData.\n")
        entities = extract_entities_from_ddd(projects_dir)
        hidden_refs = [e for e in entities if e.project == ".hidden_project"]
        assert hidden_refs == []


class TestFormatEntityIndex:
    """Output formatting for PROJECTS.md."""

    def test_produces_markdown_table(self, projects_dir):
        """AC3 prerequisite: output is structured markdown."""
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        assert any("Cross-Project Knowledge Index" in l for l in lines)
        # Should have table header
        assert any("Entity" in l and "References" in l for l in lines)

    def test_groups_same_name_entities(self, projects_dir):
        """Entities with same name across projects share one row."""
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        # "Architecture" appears in both SwarmAI and SalesIntel
        arch_line = [l for l in lines if "Architecture" in l and "|" in l]
        assert len(arch_line) == 1  # One row, not two
        assert "SwarmAI" in arch_line[0]
        assert "SalesIntel" in arch_line[0]

    def test_caps_references_per_entity(self, projects_dir):
        """Max 3 refs per entity (design doc spec)."""
        # Create 5 projects all with same heading
        for i in range(5):
            p = projects_dir / f"Proj{i}"
            p.mkdir()
            (p / "TECH.md").write_text(f"# P{i}\n\n## CommonHeading\n\nContent.\n")
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        common_line = [l for l in lines if "CommonHeading" in l and "|" in l]
        assert len(common_line) == 1
        # Should have at most 3 references
        refs_part = common_line[0].split("|")[2]  # References column
        assert refs_part.count("/") <= 3


class TestPruneEntityIndex:
    """Budget enforcement."""

    def test_prunes_below_char_limit(self):
        """AC3: Total <=8000 chars after pruning."""
        # Generate many lines that exceed budget
        lines = [f"| Entity{i} | Proj/TECH#Entity{i} |" for i in range(200)]
        pruned = prune_entity_index(lines, max_chars=8000)
        total = sum(len(l) for l in pruned)
        assert total <= 8000

    def test_preserves_header_during_prune(self):
        """Header lines survive pruning."""
        lines = [
            "## Cross-Project Knowledge Index",
            "",
            "| Entity | References |",
            "|--------|-----------|",
        ] + [f"| Entity{i} | Proj/TECH#Entity{i} |" for i in range(200)]
        pruned = prune_entity_index(lines, max_chars=8000)
        assert "## Cross-Project Knowledge Index" in pruned
        assert "| Entity | References |" in pruned


class TestFilterSingleProjectEntities:
    """AC1: Cross-Project Index only shows entities with 2+ project refs."""

    def test_excludes_single_project_entities(self, projects_dir):
        """Entities found in only 1 project are excluded from index."""
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        # "Data Sources" only appears in SalesIntel — should be excluded
        data_sources_lines = [l for l in lines if "Data Sources" in l and "|" in l]
        assert data_sources_lines == []

    def test_keeps_multi_project_entities(self, projects_dir):
        """Entities found in 2+ projects are kept."""
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        # "Architecture" appears in SwarmAI AND SalesIntel — should be kept
        arch_lines = [l for l in lines if "Architecture" in l and "|" in l]
        assert len(arch_lines) == 1
        # "What Worked" appears in SwarmAI AND SalesIntel — kept
        worked_lines = [l for l in lines if "What Worked" in l and "|" in l]
        assert len(worked_lines) == 1

    def test_vision_in_multiple_projects_kept(self, projects_dir):
        """'Vision' appears in SwarmAI and PhysicalAI — kept."""
        entities = extract_entities_from_ddd(projects_dir)
        lines = format_entity_index(entities)
        vision_lines = [l for l in lines if "Vision" in l and "|" in l]
        assert len(vision_lines) == 1


class TestCleanDescriptionExtraction:
    """AC2: Clean one-line descriptions without markdown artifacts."""

    def test_strips_leading_underscore_italic(self, tmp_path):
        """PRODUCT.md lines starting with _ (italic wrapper) are stripped."""
        proj = tmp_path / "TestProj"
        proj.mkdir()
        (proj / "PRODUCT.md").write_text(
            "# TestProj\n\n"
            "_> **ClientOrg** is the current official name for the region_\n\n"
            "## Vision\n\nSome vision.\n"
        )
        # Use the extraction logic from refresh_projects_index
        from core.entity_extractor import extract_clean_description
        desc = extract_clean_description(proj / "PRODUCT.md")
        assert not desc.startswith("_")
        assert not desc.startswith(">")
        assert "ClientOrg" in desc

    def test_strips_bold_name_prefix(self, tmp_path):
        """Lines like '- **Name:** BMS 2.0...' get cleaned."""
        proj = tmp_path / "BMS"
        proj.mkdir()
        (proj / "PRODUCT.md").write_text(
            "# BMS\n\n"
            "- **Name:** BMS 2.0 (Business Management System 2.0)\n\n"
            "## Vision\n\nUnified business ops.\n"
        )
        from core.entity_extractor import extract_clean_description
        desc = extract_clean_description(proj / "PRODUCT.md")
        assert not desc.startswith("-")
        assert not desc.startswith("**")
        assert "BMS 2.0" in desc or "Business Management" in desc

    def test_truncates_at_80_chars_word_boundary(self, tmp_path):
        """Long descriptions are truncated at word boundary."""
        proj = tmp_path / "LongDesc"
        proj.mkdir()
        long_line = "A " * 60  # 120 chars
        (proj / "PRODUCT.md").write_text(
            f"# LongDesc\n\n{long_line}\n\n## Vision\n\nStuff.\n"
        )
        from core.entity_extractor import extract_clean_description
        desc = extract_clean_description(proj / "PRODUCT.md")
        assert len(desc) <= 80

    def test_skips_blank_and_heading_lines(self, tmp_path):
        """Blank lines and # headings are not used as description."""
        proj = tmp_path / "HeadingsOnly"
        proj.mkdir()
        (proj / "PRODUCT.md").write_text(
            "# HeadingsOnly\n\n\n## Section\n\nActual content here.\n"
        )
        from core.entity_extractor import extract_clean_description
        desc = extract_clean_description(proj / "PRODUCT.md")
        assert desc == "Actual content here."
