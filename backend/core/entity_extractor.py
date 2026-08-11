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

# DDD files to scan (order matters for display priority).
# Run 0 (run_393e3dc1): single source of truth — see project_registry.DDD_CANONICAL_DOCS.
from core.project_registry import DDD_CANONICAL_DOCS as _DDD_FILES
from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)

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


def extract_clean_description(product_md_path: Path) -> str:
    """Extract a clean one-line description from PRODUCT.md.

    Reads the file and finds the first non-heading, non-blank content line.
    Strips common markdown formatting artifacts:
    - Leading _ (italic wrappers)
    - Leading > (blockquote markers)
    - Leading - (list item prefix)
    - Leading **Name:** patterns
    - Trailing _ (closing italic)
    Truncates at 80 chars on word boundary.

    Args:
        product_md_path: Path to a project's PRODUCT.md file.

    Returns:
        Clean description string, or empty string if none found.
    """
    if not product_md_path.exists():
        return ""

    try:
        content = product_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    for line in content.splitlines():
        stripped = line.strip()
        # Skip blank lines, headings, HR/frontmatter, HTML comments, images
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---" or stripped.startswith("<!--") or stripped.startswith("!["):
            continue

        # Strip leading/trailing markdown artifacts
        desc = stripped

        # Remove italic wrappers: _text_ or _> text_
        if desc.startswith("_") and desc.endswith("_"):
            desc = desc[1:-1].strip()
        elif desc.startswith("_"):
            desc = desc[1:].strip()

        # Remove blockquote markers
        if desc.startswith(">"):
            desc = desc[1:].strip()

        # Remove list prefix: - or *
        if desc.startswith("- ") or desc.startswith("* "):
            desc = desc[2:].strip()

        # Remove **Name:** prefix pattern (e.g., **Name:** BMS 2.0)
        if desc.startswith("**") and ":**" in desc:
            # Extract everything after the first :** pattern
            idx = desc.index(":**") + 3
            desc = desc[idx:].strip()

        # Remove any remaining leading/trailing bold markers
        while desc.startswith("**"):
            desc = desc[2:]
        while desc.endswith("**"):
            desc = desc[:-2]
        desc = desc.strip()

        if not desc:
            continue

        # Truncate at 80 chars on word boundary
        if len(desc) > 80:
            desc = desc[:80].rsplit(" ", 1)[0]

        return desc

    return ""


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
            doc_path = ddd_path(candidate, ddd_file)  # 2-understanding/ post-ad7f6623
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
                        # M4 fix: escape pipe chars that would break markdown table
                        safe_name = heading.replace("|", "\\|")
                        entities.append(
                            EntityRef(
                                name=safe_name,
                                project=project_name,
                                doc=doc_name,
                                section=heading,  # Keep raw section for ref lookup
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

    # PE-3 fix: sort by UNIQUE project count (post-dedup), not raw ref count
    def _unique_project_count(name: str) -> int:
        return len(set(r.project for r in grouped[name]))

    # Filter: only keep entities referenced by 2+ different projects
    multi_project_names = [n for n in grouped if _unique_project_count(n) >= 2]

    sorted_names = sorted(
        multi_project_names,
        key=lambda n: (-_unique_project_count(n), n),
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
        # L1 fix: deduplicate by (project, doc) before capping
        seen: set[tuple[str, str]] = set()
        deduped: list[EntityRef] = []
        for r in refs:
            key = (r.project, r.doc)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        # Cap at max refs
        capped = deduped[:_MAX_REFS_PER_ENTITY]
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
    Budget accounts for newline join separators.

    Args:
        lines: Formatted markdown lines from format_entity_index.
        max_chars: Maximum total characters allowed.

    Returns:
        Pruned list of lines fitting within budget.
        Empty list if even the header exceeds the budget.
    """
    # H2 fix: account for \n join separators in budget calculation
    total = sum(len(l) + 1 for l in lines) - 1 if lines else 0
    if total <= max_chars:
        return lines

    # Separate header (lines up to and including |---|)
    # from data rows
    header_end = 0
    for i, line in enumerate(lines):
        if line.startswith("|---"):
            header_end = i + 1
            break

    header = lines[:header_end]
    data_rows = lines[header_end:]

    # M5 fix: if header alone exceeds budget, return empty (skip entirely)
    header_chars = sum(len(l) + 1 for l in header) - 1 if header else 0
    if header_chars >= max_chars:
        return []

    remaining_budget = max_chars - header_chars - 1  # -1 for join between header and first row

    kept_rows: list[str] = []
    current_chars = 0
    for row in data_rows:
        row_cost = len(row) + 1  # +1 for the \n join separator
        if current_chars + row_cost > remaining_budget:
            break
        kept_rows.append(row)
        current_chars += row_cost

    # M5 fix: don't return header-only (empty table wastes tokens)
    if not kept_rows:
        return []

    return header + kept_rows
