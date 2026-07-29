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


def entry_dedup_keys(line: str) -> tuple[str | None, str | None]:
    """Compute the two dedup match keys for a single MEMORY.md entry line.

    SINGLE SOURCE of the dedup match logic shared by distillation_hook and
    memory_extractor (R3-C, run_55c6ab8f). Mirrors the keys distillation has
    used inline since its dedup was added:

    - prefix key: ``line.strip()[:120].lower()`` — catches exact/near-exact dups
    - title key:  the lowercased bold title (``**...**``) — catches reworded dups
      with the same headline (e.g. two lessons titled "CJK 没有词边界..." that
      differ only in detail/ID).

    Returns ``(prefix_key, title_key)``; either may be ``None`` (blank line →
    both None; no bold title → title None).
    """
    stripped = line.strip()
    if not stripped:
        return (None, None)
    prefix_key = stripped[:120].lower()
    m = re.search(r"\*\*(.+?)\*\*", line)
    title_key = m.group(1).strip().lower() if m else None
    return (prefix_key, title_key)


def filter_duplicate_entries(existing_content: str, candidate_text: str) -> str:
    """Drop candidate lines already present in ``existing_content``.

    SINGLE SOURCE of the dedup behavior shared by both MEMORY.md writers
    (R3-C). Behavior is byte-identical to distillation_hook's historical inline
    dedup, including its deliberate asymmetry (verified against the original at
    distillation_hook.py:1360-1399):

    - The **prefix set** is built ONCE from ``existing_content`` and is STATIC —
      intra-batch prefix collisions are NOT deduped (only collisions against
      pre-existing content are).
    - The **title set** is seeded from ``existing_content`` and is MUTATED as we
      walk the candidate lines, so a second candidate line sharing a bold title
      with an earlier candidate line IS dropped (intra-batch title dedup).

    A fully-filtered batch returns ``""`` — callers MUST guard against writing
    an empty string (an empty write corrupts the target section).
    """
    if not existing_content:
        return candidate_text

    existing_lines_lower = {
        ln.strip()[:120].lower()
        for ln in existing_content.splitlines()
        if ln.strip()
    }
    existing_titles: set[str] = set()
    for ln in existing_content.splitlines():
        m = re.search(r"\*\*(.+?)\*\*", ln)
        if m:
            existing_titles.add(m.group(1).strip().lower())

    new_lines: list[str] = []
    for line in candidate_text.splitlines():
        prefix_key, title_key = entry_dedup_keys(line)
        # Strategy 1: exact prefix match against STATIC existing set
        if prefix_key and prefix_key in existing_lines_lower:
            continue
        # Strategy 2: bold-title match against MUTATED title set (intra-batch)
        if title_key is not None:
            if title_key and title_key in existing_titles:
                continue
            existing_titles.add(title_key)
        new_lines.append(line)

    return "\n".join(new_lines)


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

    Upsert semantics: if the field line does not exist yet (legacy/seed
    entries written before the field convention), it is inserted right
    after the entry header. This mirrors the read path, which treats an
    absent field as a default value — so a field-less entry can still be
    deprecated (the original bug: set-field raised on entries lacking a
    Status line, silently failing every maintenance run).

    Returns:
        Modified content string with the field updated, or inserted if
        the field line was absent.

    Raises:
        ValueError: If the entry is not found, or the entry block has no
            ``###`` header line.
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
        # Upsert: insert the field line immediately after the header so the
        # write path tolerates an absent field exactly as the read path does.
        header_match = re.match(r"^###[^\n]*", entry_block)
        if header_match is None:
            raise ValueError(
                f"Entry '{entry_id}' in section '{section}' has no header line"
            )
        insert_at = header_match.end()
        new_entry_block = (
            entry_block[:insert_at]
            + f"\n- **{field_name}**: {value}"
            + entry_block[insert_at:]
        )
        return content[:entry_start] + new_entry_block + content[entry_end:]

    # Replace the value portion of the existing field line
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
    # ── MemoryGuard: sanitize value before any file I/O ─────────────
    if value is not None:
        try:
            from core.memory_guard import MemoryGuard, MemoryGuardError
            _guard = MemoryGuard()
            try:
                value = _guard.sanitize(value)
            except MemoryGuardError as e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "MemoryGuard rejected field modify on %s: %s", file_path, e,
                )
                raise LockedWriteError(
                    f"Memory injection blocked — {e}"
                ) from e
        except ImportError:
            pass  # memory_guard not available — proceed without guard

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

        # Read current content (with UTF-8 corruption resilience).
        # surrogateescape preserves original bytes on round-trip (PEP 383)
        # — unlike errors="replace" which permanently destroys them.
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "UTF-8 decode error in %s — reading with surrogateescape",
                    file_path,
                )
                content = file_path.read_text(encoding="utf-8", errors="surrogateescape")
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

        # Write back (surrogateescape preserves non-UTF-8 bytes on round-trip)
        file_path.write_text(new_content, encoding="utf-8", errors="surrogateescape")
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
    file_path: Path, section: str, text: str, mode: str = "append",
    *, dedup: bool = False, reindex_memory: bool = False,
):
    """Acquire flock, read file, modify section, write back, release.

    Args:
        file_path: Path to the target Markdown file.
        section: Section header to find (e.g. "Recent Context").
        text: Content to append, prepend, or replace.
        mode: "append" (default), "prepend", or "replace".
        dedup: When True, filter out candidate lines already present in the
            file (via ``filter_duplicate_entries``) BEFORE modifying — applied
            INSIDE the lock against the just-read content (no TOCTOU). If the
            filter removes everything, the write is a NO-OP (returns without
            touching the file) — never injects a bare blank line. Default
            False keeps all existing callers byte-identical. (R3-C: gives the
            memory_extractor write path the same mechanical dedup distillation
            already has, single-sourced.)
        reindex_memory: When True, rebuild the MEMORY_INDEX block IN THE SAME
            LOCK after the section write, so the compact index stays consistent
            with the entries regardless of which writer wrote them. Closes the
            pre-existing bug where distillation_hook and memory_extractor wrote
            MEMORY.md but never reindexed → new entries were invisible in the
            index (run_b356b552). Uses the PURE ``inject_index_into_memory``
            (extract_body→generate→inject); it does NOT call
            ContextHealthHook._refresh_memory_index, which would re-acquire this
            same MEMORY.md.lock and deadlock. No-op guard skips it for non-MEMORY
            files and for a no-op dedup return. Default False keeps all existing
            callers byte-identical.

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

        # Read current content (or empty if file doesn't exist).
        # surrogateescape preserves original bytes on round-trip (PEP 383)
        # — unlike errors="replace" which permanently destroys them.
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "UTF-8 decode error in %s — reading with surrogateescape",
                    file_path,
                )
                content = file_path.read_text(encoding="utf-8", errors="surrogateescape")
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

        # Dedup (R3-C): filter candidate lines already present, against the
        # content just read UNDER THE LOCK (no TOCTOU). Mandatory empty-guard —
        # if everything is filtered, return a NO-OP rather than letting
        # _modify_content inject a bare blank line into the section.
        if dedup:
            text = filter_duplicate_entries(content, text)
            if not text.strip():
                return

        # Modify the content
        new_content = _modify_content(content, section, text, mode)

        # Reindex IN-LOCK (single source of truth for the MEMORY index across
        # every writer). PURE inject_index_into_memory only — NEVER
        # _refresh_memory_index, which re-acquires this same MEMORY.md.lock and
        # would deadlock. Best-effort: an index-rebuild failure must not lose the
        # entry write we already computed. (run_b356b552)
        if reindex_memory and file_path.name == "MEMORY.md":
            try:
                from core.memory_index import inject_index_into_memory
                new_content = inject_index_into_memory(new_content)
            except Exception as e:  # noqa: BLE001 — reindex is non-fatal to the write
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "reindex_memory skipped (write still applied) for %s: %s",
                    file_path, e,
                )

        # Write back (surrogateescape preserves non-UTF-8 bytes on round-trip)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_content, encoding="utf-8", errors="surrogateescape")
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
