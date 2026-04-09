"""Locked read-modify-write for MEMORY.md and EVOLUTION.md.

A CLI script called by the distillation, save-memory, and self-evolution
skills to safely modify Markdown files under an advisory file lock.
Inlines the locking logic (fcntl.flock on Unix) — no separate
FileLockManager module needed.

Usage:
    python locked_write.py --file PATH --section SECTION --append TEXT
    python locked_write.py --file PATH --section SECTION --prepend TEXT
    python locked_write.py --file PATH --section SECTION --replace TEXT
    python locked_write.py --file PATH --section SECTION --increment-field FIELD --entry-id ID
    python locked_write.py --file PATH --section SECTION --set-field FIELD --value VAL --entry-id ID

Public symbols:
- ``locked_read_modify_write``  — Core function: acquire flock, read file,
  modify section, write back, release lock.
- ``locked_field_modify``       — Field-level modify: acquire flock, read file,
  increment or set a field on a specific entry, write back, release lock.
- ``_find_entry_in_section``    — Find a ``### ID`` entry block within a section.
- ``_increment_field``          — Increment a numeric field on an entry by 1.
- ``_set_field``                — Set a field value on an entry.
- ``LOCK_TIMEOUT``              — Maximum seconds to wait for lock (5.0).
- ``FALLBACK_SECTION``          — Default section header when target not found.

The ``--prepend`` mode inserts text at the top of a section (right after
the header), enabling newest-first ordering for date-prefixed entries.

The ``--increment-field`` and ``--set-field`` modes operate on individual
entry fields within EVOLUTION.md, identified by entry ID (E001, O001, F001).
"""

import argparse
import platform
import re
import sys
import time
from pathlib import Path

# Platform-specific locking
_IS_WINDOWS = platform.system() == "Windows"
if not _IS_WINDOWS:
    import fcntl

LOCK_TIMEOUT = 5.0  # seconds
FALLBACK_SECTION = "## Distilled"


class LockedWriteError(Exception):
    """Raised when a locked write operation fails.

    Replaces ``sys.exit(1)`` for library callers.  The CLI ``main()``
    catches this and calls ``sys.exit(1)`` for backward compatibility.
    """
    pass


def _find_section_range(content: str, section: str):
    """Find the start and end positions of a markdown section.

    Looks for a ``## Section Name`` header and returns the range from
    the end of that header line to the start of the next ``##`` header
    (or end of file).

    Returns:
        tuple[int, int] | None: (insert_pos, next_header_pos) or None
            if the section is not found.  ``insert_pos`` is the position
            right after the section header line (including its newline).
            ``next_header_pos`` is the start of the next ``##`` header
            or len(content).
    """
    # Strip leading "## " prefix if present, then match exactly
    clean_section = re.sub(r"^#+\s*", "", section).strip()
    pattern = re.compile(
        r"^(##\s+" + re.escape(clean_section) + r")\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if match is None:
        return None

    # Position right after the header line
    header_end = match.end()
    # Skip the newline after the header if present
    if header_end < len(content) and content[header_end] == "\n":
        header_end += 1

    # Find the next ## header or end of file
    next_header = re.search(r"^##\s+", content[header_end:], re.MULTILINE)
    if next_header:
        next_header_pos = header_end + next_header.start()
    else:
        next_header_pos = len(content)

    return (header_end, next_header_pos)


def _modify_content(content: str, section: str, text: str, mode: str) -> str:
    """Apply the section modification to the file content.

    Args:
        content: Current file content (may be empty).
        section: Target section header (e.g. "Recent Context").
        text: Text to append or replace with.
        mode: "append" or "replace".

    Returns:
        Modified content string.
    """
    section_range = _find_section_range(content, section)

    if section_range is None:
        # Section not found — append under fallback section
        suffix = f"\n\n{FALLBACK_SECTION}\n{text}\n"
        return content.rstrip() + suffix if content.strip() else f"{FALLBACK_SECTION}\n{text}\n"

    header_end, next_header_pos = section_range

    if mode == "replace":
        # Replace everything between header and next header
        return content[:header_end] + text + "\n" + content[next_header_pos:]

    if mode == "prepend":
        # Insert text at the beginning of the section (right after header)
        existing_section = content[header_end:next_header_pos]
        if existing_section.strip():
            return (
                content[:header_end]
                + text
                + "\n"
                + existing_section.lstrip("\n")
                + content[next_header_pos:]
            )
        else:
            return content[:header_end] + text + "\n" + content[next_header_pos:]

    # mode == "append"
    # Insert text at the end of the section (before next header)
    insert_pos = next_header_pos
    # Ensure there's a newline before the appended text
    existing_section = content[header_end:insert_pos]
    if existing_section.rstrip():
        # Section has content — append after it
        return (
            content[:header_end]
            + existing_section.rstrip()
            + "\n"
            + text
            + "\n"
            + content[next_header_pos:]
        )
    else:
        # Section is empty — just add the text
        return content[:header_end] + text + "\n" + content[next_header_pos:]


def _find_entry_in_section(
    content: str, section: str, entry_id: str
) -> tuple[int, int] | None:
    """Find a markdown entry by its ID within a section.

    Searches for a ``### {entry_id} | ...`` header line within the
    specified section.  Returns the start and end positions of the
    entry block (from the ``###`` header to the next ``###`` header
    or section end).

    Args:
        content: Full file content.
        section: Section header (e.g. "Capabilities Built").
        entry_id: Entry ID pattern (e.g. "E001", "O001", "F001").

    Returns:
        tuple[int, int] | None: (entry_start, entry_end) or None if
            the section or entry is not found.
    """
    section_range = _find_section_range(content, section)
    if section_range is None:
        return None

    header_end, next_section_pos = section_range
    section_content = content[header_end:next_section_pos]

    # Match ### {entry_id} at the start of a line (entry_id is first token)
    entry_pattern = re.compile(
        r"^###\s+" + re.escape(entry_id) + r"[\s|]",
        re.MULTILINE,
    )
    entry_match = entry_pattern.search(section_content)
    if entry_match is None:
        return None

    entry_start = header_end + entry_match.start()

    # Find the next ### header within the section, or use section end
    rest_after_entry = section_content[entry_match.end():]
    next_entry = re.search(r"^###\s+", rest_after_entry, re.MULTILINE)
    if next_entry:
        entry_end = header_end + entry_match.end() + next_entry.start()
    else:
        entry_end = next_section_pos

    return (entry_start, entry_end)


def _increment_field(
    content: str, section: str, entry_id: str, field_name: str
) -> str:
    """Increment a numeric field on an entry by 1.

    Finds the entry by ID within the section, locates the field line
    matching ``- **{field_name}**: {value}``, parses the value as an
    integer, increments by 1, and returns the modified content.

    Args:
        content: Full file content.
        section: Section header (e.g. "Capabilities Built").
        entry_id: Entry ID (e.g. "E001").
        field_name: Field name (e.g. "Usage Count").

    Returns:
        Modified content string with the field incremented.

    Raises:
        ValueError: If entry not found, field not found, or field
            value is non-numeric.
    """
    entry_range = _find_entry_in_section(content, section, entry_id)
    if entry_range is None:
        raise ValueError(
            f"Entry '{entry_id}' not found in section '{section}'"
        )

    entry_start, entry_end = entry_range
    entry_block = content[entry_start:entry_end]

    # Match the field line: - **Field Name**: value
    field_pattern = re.compile(
        r"^(- \*\*" + re.escape(field_name) + r"\*\*:\s*)(.+)$",
        re.MULTILINE,
    )
    field_match = field_pattern.search(entry_block)
    if field_match is None:
        raise ValueError(
            f"Field '{field_name}' not found in entry '{entry_id}'"
        )

    old_value = field_match.group(2).strip()
    try:
        new_value = int(old_value) + 1
    except ValueError as exc:
        raise ValueError(
            f"Field '{field_name}' in entry '{entry_id}' has non-numeric "
            f"value: '{old_value}'"
        ) from exc

    # Replace the value in the entry block
    new_entry_block = (
        entry_block[: field_match.start(2)]
        + str(new_value)
        + entry_block[field_match.end(2) :]
    )

    return content[:entry_start] + new_entry_block + content[entry_end:]


def _set_field(
    content: str, section: str, entry_id: str, field_name: str, value: str
) -> str:
    """Set a field value on an entry.

    Finds the entry by ID within the section, locates the field line
    matching ``- **{field_name}**: {value}``, and replaces the value.

    Args:
        content: Full file content.
        section: Section header (e.g. "Capabilities Built").
        entry_id: Entry ID (e.g. "E003").
        field_name: Field name (e.g. "Status").
        value: New value to set.

    Returns:
        Modified content string with the field updated.

    Raises:
        ValueError: If entry not found or field not found.
    """
    entry_range = _find_entry_in_section(content, section, entry_id)
    if entry_range is None:
        raise ValueError(
            f"Entry '{entry_id}' not found in section '{section}'"
        )

    entry_start, entry_end = entry_range
    entry_block = content[entry_start:entry_end]

    # Match the field line: - **Field Name**: value
    field_pattern = re.compile(
        r"^(- \*\*" + re.escape(field_name) + r"\*\*:\s*)(.+)$",
        re.MULTILINE,
    )
    field_match = field_pattern.search(entry_block)
    if field_match is None:
        raise ValueError(
            f"Field '{field_name}' not found in entry '{entry_id}'"
        )

    # Replace the value portion
    new_entry_block = (
        entry_block[: field_match.start(2)]
        + value
        + entry_block[field_match.end(2) :]
    )

    return content[:entry_start] + new_entry_block + content[entry_end:]


def locked_field_modify(
    file_path: Path,
    section: str,
    entry_id: str,
    field_name: str,
    mode: str,
    value: str | None = None,
) -> None:
    """Acquire flock, read file, modify a field on an entry, write back.

    Args:
        file_path: Path to the target Markdown file.
        section: Section header to find (e.g. "Capabilities Built").
        entry_id: Entry ID (e.g. "E001").
        field_name: Field name (e.g. "Usage Count").
        mode: "increment-field" or "set-field".
        value: New value (required for "set-field" mode).

    Raises:
        LockedWriteError: On lock timeout or file-not-found.
        ValueError: On field modification error or invalid mode/args.
    """
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = None
    try:
        fd = open(lock_path, "w")  # noqa: SIM115
        # Acquire exclusive lock with timeout
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                if _IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise LockedWriteError(
                        f"Lock timeout on {file_path} after {LOCK_TIMEOUT}s"
                    )
                time.sleep(0.1)

        # Read current content
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
        else:
            raise LockedWriteError(f"File not found: {file_path}")

        # Modify the field (ValueError propagates naturally)
        if mode == "increment-field":
            new_content = _increment_field(content, section, entry_id, field_name)
        elif mode == "set-field":
            if value is None:
                raise ValueError("value is required for set-field mode")
            new_content = _set_field(
                content, section, entry_id, field_name, value
            )
        else:
            raise ValueError(f"Unknown field mode: {mode}")

        # Write back
        file_path.write_text(new_content, encoding="utf-8")
    finally:
        if fd is not None:
            try:
                if _IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()


def locked_read_modify_write(
    file_path: Path, section: str, text: str, mode: str = "append"
):
    """Acquire flock, read file, modify section, write back, release.

    Args:
        file_path: Path to the target Markdown file.
        section: Section header to find (e.g. "Recent Context").
        text: Content to append, prepend, or replace.
        mode: "append" (default), "prepend", or "replace".

    Raises:
        LockedWriteError: If the lock cannot be acquired within
            ``LOCK_TIMEOUT`` seconds.
    """
    # ── MemoryGuard: sanitize content before any file I/O ────────────
    try:
        from core.memory_guard import MemoryGuard, MemoryGuardError
        _guard = MemoryGuard()
        try:
            text = _guard.sanitize(text)
        except MemoryGuardError as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "MemoryGuard rejected write to %s: %s", file_path, e,
            )
            raise LockedWriteError(
                f"Memory injection blocked — {e}"
            ) from e
    except ImportError:
        pass  # memory_guard not available yet — proceed without guard

    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = None
    try:
        fd = open(lock_path, "w")  # noqa: SIM115
        # Acquire exclusive lock with timeout
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                if _IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise LockedWriteError(
                        f"Lock timeout on {file_path} after {LOCK_TIMEOUT}s"
                    )
                time.sleep(0.1)

        # Read current content (or empty if file doesn't exist)
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
        else:
            content = ""

        # Validate content for MEMORY.md (injection prevention)
        if file_path.name == "MEMORY.md":
            validate_memory_content = None  # type: ignore[assignment]
            try:
                from core.memory_validation import validate_memory_content
            except ImportError:
                # Running standalone (CLI) — try relative import path
                import importlib.util
                _spec = importlib.util.spec_from_file_location(
                    "memory_validation",
                    Path(__file__).parent.parent / "core" / "memory_validation.py",
                )
                if _spec and _spec.loader:
                    _mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    validate_memory_content = _mod.validate_memory_content

            if validate_memory_content is not None:
                safe, pattern = validate_memory_content(text)
                if not safe:
                    raise LockedWriteError(
                        f"Memory injection blocked — pattern '{pattern}' "
                        f"detected in content: {text[:80]!r}"
                    )

        # Modify the content
        new_content = _modify_content(content, section, text, mode)

        # Write back
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_content, encoding="utf-8")
    finally:
        if fd is not None:
            try:
                if _IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()


def main():
    """CLI entry point for locked_write.py."""
    parser = argparse.ArgumentParser(
        description="Locked read-modify-write for Markdown files.",
    )
    parser.add_argument(
        "--file", required=True, type=Path, help="Path to the target file"
    )
    parser.add_argument(
        "--section",
        required=True,
        help="Section header to target (e.g. 'Recent Context')",
    )
    parser.add_argument(
        "--entry-id",
        dest="entry_id",
        help="Entry ID for field operations (e.g. 'E001', 'O001', 'F001')",
    )
    parser.add_argument(
        "--value",
        help="New value for --set-field mode",
    )

    # Mutually exclusive: --append, --prepend, --replace, --increment-field, --set-field
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--append", dest="text_append", help="Text to append to section end")
    group.add_argument("--prepend", dest="text_prepend", help="Text to prepend to section start (newest-first)")
    group.add_argument(
        "--replace", dest="text_replace", help="Text to replace section with"
    )
    group.add_argument(
        "--increment-field",
        dest="increment_field",
        help="Field name to increment by 1 (requires --entry-id)",
    )
    group.add_argument(
        "--set-field",
        dest="set_field",
        help="Field name to set (requires --entry-id and --value)",
    )

    args = parser.parse_args()

    # Handle field modification modes
    if args.increment_field is not None:
        if not args.entry_id:
            parser.error("--entry-id is required when using --increment-field")
        try:
            locked_field_modify(
                args.file,
                args.section,
                args.entry_id,
                args.increment_field,
                "increment-field",
            )
        except (LockedWriteError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if args.set_field is not None:
        if not args.entry_id:
            parser.error("--entry-id is required when using --set-field")
        if not args.value:
            parser.error("--value is required when using --set-field")
        try:
            locked_field_modify(
                args.file,
                args.section,
                args.entry_id,
                args.set_field,
                "set-field",
                args.value,
            )
        except (LockedWriteError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Handle text modification modes
    if args.text_append is not None:
        mode = "append"
        text = args.text_append
    elif args.text_prepend is not None:
        mode = "prepend"
        text = args.text_prepend
    else:
        mode = "replace"
        text = args.text_replace

    try:
        locked_read_modify_write(args.file, args.section, text, mode)
    except (LockedWriteError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()
