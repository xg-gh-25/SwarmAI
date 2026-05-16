"""DDD Entity Index extraction.

Scans all projects' DDD markdown files (PRODUCT.md, TECH.md,
IMPROVEMENT.md, PROJECT.md) and extracts ## headings as entities.
Produces a flat routing table for cross-project knowledge discovery.

Key public symbols:
    EntityRef       — dataclass representing one entity reference
    extract_entities_from_ddd — scan projects dir → list of EntityRef
    format_entity_index — EntityRef list → markdown lines for PROJECTS.md
    prune_entity_index — enforce char budget on formatted lines
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# DDD files to scan (order matters for display priority)
_DDD_FILES = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")

# Budget constants
_MAX_REFS_PER_ENTITY = 3
_DEFAULT_MAX_CHARS = 8000


@dataclass(frozen=True)
class EntityRef:
    """A single entity reference: a ## heading found in a DDD document.

    Attributes:
        name: Heading text (stripped)
        project: Project directory name
        doc: DDD doc name without .md extension (e.g., "TECH")
        section: Original heading text (same as name for ## headings)
    """

    name: str
    project: str
    doc: str
    section: str


def extract_entities_from_ddd(projects_dir: Path) -> list[EntityRef]:
    """Extract ## headings from all DDD docs across all projects.

    Args:
        projects_dir: Path to the Projects/ directory containing project folders.

    Returns:
        List of EntityRef, one per heading found. May contain duplicates
        (same heading name from different projects) — this is intentional
        for cross-project routing.
    """
    if not projects_dir.exists():
        return []

    entities: list[EntityRef] = []

    for candidate in sorted(projects_dir.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue

        project_name = candidate.name

        for ddd_file in _DDD_FILES:
            doc_path = candidate / ddd_file
            if not doc_path.exists():
                continue

            try:
                content = doc_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning(
                    "Could not read %s/%s — skipping",
                    project_name,
                    ddd_file,
                )
                continue

            doc_name = ddd_file.replace(".md", "")

            for line in content.splitlines():
                # Only ## headings (not # or ###)
                if line.startswith("## ") and not line.startswith("### "):
                    heading = line[3:].strip()
                    if heading:
                        entities.append(
                            EntityRef(
                                name=heading,
                                project=project_name,
                                doc=doc_name,
                                section=heading,
                            )
                        )

    return entities


def format_entity_index(entities: list[EntityRef]) -> list[str]:
    """Format extracted entities into markdown lines for PROJECTS.md.

    Groups entities by name, deduplicates, and formats as a markdown table.
    Caps references per entity at _MAX_REFS_PER_ENTITY.

    Args:
        entities: List of EntityRef from extract_entities_from_ddd.

    Returns:
        List of markdown lines (without trailing newlines).
        Empty list if no entities.
    """
    if not entities:
        return []

    # Group by entity name
    grouped: dict[str, list[EntityRef]] = {}
    for e in entities:
        grouped.setdefault(e.name, []).append(e)

    # Sort by number of references (most cross-project first), then alphabetically
    sorted_names = sorted(
        grouped.keys(),
        key=lambda n: (-len(grouped[n]), n),
    )

    lines = [
        "## Cross-Project Knowledge Index",
        "",
        "<!-- Auto-maintained by refresh_projects_index(). Do not edit manually. -->",
        "",
        "| Entity | References |",
        "|--------|-----------|",
    ]

    for name in sorted_names:
        refs = grouped[name]
        # Cap at max refs
        capped = refs[:_MAX_REFS_PER_ENTITY]
        # Format references as Project/DOC#Section
        ref_strs = [f"{r.project}/{r.doc}#{r.section}" for r in capped]
        refs_display = ", ".join(ref_strs)
        lines.append(f"| {name} | {refs_display} |")

    return lines


def prune_entity_index(
    lines: list[str], max_chars: int = _DEFAULT_MAX_CHARS
) -> list[str]:
    """Prune entity index lines to fit within character budget.

    Preserves header lines (## heading, table header, separator).
    Removes data rows from the bottom until within budget.

    Args:
        lines: Formatted markdown lines from format_entity_index.
        max_chars: Maximum total characters allowed.

    Returns:
        Pruned list of lines fitting within budget.
    """
    total = sum(len(l) for l in lines)
    if total <= max_chars:
        return lines

    # Separate header (first 6 lines: ## heading, blank, comment, blank, table header, separator)
    # from data rows
    header_end = 0
    for i, line in enumerate(lines):
        if line.startswith("|---"):
            header_end = i + 1
            break

    header = lines[:header_end]
    data_rows = lines[header_end:]

    # Remove rows from the end (least cross-project entities, since sorted desc)
    header_chars = sum(len(l) for l in header)
    remaining_budget = max_chars - header_chars

    kept_rows: list[str] = []
    current_chars = 0
    for row in data_rows:
        if current_chars + len(row) > remaining_budget:
            break
        kept_rows.append(row)
        current_chars += len(row)

    return header + kept_rows
