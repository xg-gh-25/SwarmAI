"""YAML frontmatter parser and printer for DailyActivity Markdown files.

This module provides utilities for reading and writing YAML frontmatter
blocks in Markdown files.  Frontmatter is used to track processing state
(e.g. ``distilled: true``) on DailyActivity files without moving them
between directories.

Public symbols:

- ``parse_frontmatter``  — Extract (metadata, body) from a Markdown string
- ``write_frontmatter``  — Produce a Markdown string with YAML frontmatter
"""

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DELIMITER = "---"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a Markdown string.

    Frontmatter is a YAML block delimited by ``---`` on its own line at
    the very start of the file.  Example::

        ---
        distilled: true
        distilled_date: "2025-07-15"
        ---

        Body text here ...

    Returns:
        A tuple of ``(metadata_dict, body_str)``.

        * If no frontmatter block is found, returns ``({}, content)``.
        * If the YAML between the delimiters is malformed, logs a
          warning and returns ``({}, content)``.
    """
    if not content:
        return ({}, content)

    # Frontmatter must start on the very first line with "---"
    if not content.startswith(_DELIMITER + "\n"):
        return ({}, content)

    # Find the closing delimiter
    closing_idx = content.find("\n" + _DELIMITER + "\n", len(_DELIMITER))
    if closing_idx == -1:
        # Also check for closing delimiter at end of string (no trailing newline)
        closing_idx = content.find("\n" + _DELIMITER, len(_DELIMITER))
        if closing_idx == -1 or closing_idx + 1 + len(_DELIMITER) != len(content):
            # No closing delimiter found — treat as no frontmatter
            return ({}, content)

    # Extract the YAML text between the delimiters
    yaml_text = content[len(_DELIMITER) + 1 : closing_idx]

    # Determine where the body starts (after closing --- and optional newlines).
    # The standard format has a blank line between closing --- and body:
    #   ---\n\nbody
    # We skip the closing delimiter line and any leading blank line.
    body_start = closing_idx + 1 + len(_DELIMITER)
    # Skip up to two newlines (the delimiter's own \n and the blank separator)
    for _ in range(2):
        if body_start < len(content) and content[body_start] == "\n":
            body_start += 1
    body = content[body_start:]

    # Parse the YAML
    try:
        metadata = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        logger.warning("Malformed YAML frontmatter: %s", exc)
        return ({}, content)

    # yaml.safe_load returns None for empty YAML
    if metadata is None:
        metadata = {}

    if not isinstance(metadata, dict):
        logger.warning(
            "Frontmatter YAML is not a mapping (got %s), treating as no frontmatter",
            type(metadata).__name__,
        )
        return ({}, content)

    return (metadata, body)


def write_frontmatter(metadata: dict[str, Any], body: str) -> str:
    """Produce a Markdown string with YAML frontmatter.

    Output format::

        ---
        key: value
        ---

        body text here

    If *metadata* is empty, returns just the body with no frontmatter
    block (avoids writing an empty ``---\\n---`` block).

    Args:
        metadata: Dictionary of frontmatter key-value pairs.
        body: The Markdown body content.

    Returns:
        The assembled Markdown string.
    """
    if not metadata:
        return body

    yaml_text = yaml.dump(metadata, default_flow_style=False, allow_unicode=True)
    # yaml.dump always adds a trailing newline; strip it for clean output
    yaml_text = yaml_text.rstrip("\n")

    return f"{_DELIMITER}\n{yaml_text}\n{_DELIMITER}\n\n{body}"
