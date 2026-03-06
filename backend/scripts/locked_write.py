"""Locked read-modify-write for MEMORY.md.

A CLI script called by the distillation and save-memory skills to safely
modify MEMORY.md under an advisory file lock.  Inlines the locking logic
(fcntl.flock on Unix) — no separate FileLockManager module needed.

Usage:
    python locked_write.py --file PATH --section SECTION --append TEXT
    python locked_write.py --file PATH --section SECTION --replace TEXT

Public symbols:
- ``locked_read_modify_write`` — Core function: acquire flock, read file,
  modify section, write back, release lock.
- ``LOCK_TIMEOUT``             — Maximum seconds to wait for lock (5.0).
- ``FALLBACK_SECTION``         — Default section header when target not found.
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


def locked_read_modify_write(
    file_path: Path, section: str, text: str, mode: str = "append"
):
    """Acquire flock, read file, modify section, write back, release.

    Args:
        file_path: Path to the target Markdown file.
        section: Section header to find (e.g. "Recent Context").
        text: Content to append or replace.
        mode: "append" (default) or "replace".

    Raises:
        SystemExit: With code 1 if the lock cannot be acquired within
            ``LOCK_TIMEOUT`` seconds.
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
                    print(
                        f"ERROR: Lock timeout on {file_path}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                time.sleep(0.1)

        # Read current content (or empty if file doesn't exist)
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
        else:
            content = ""

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

    # Mutually exclusive: --append or --replace
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--append", dest="text_append", help="Text to append")
    group.add_argument(
        "--replace", dest="text_replace", help="Text to replace section with"
    )

    args = parser.parse_args()

    if args.text_append is not None:
        mode = "append"
        text = args.text_append
    else:
        mode = "replace"
        text = args.text_replace

    locked_read_modify_write(args.file, args.section, text, mode)


if __name__ == "__main__":
    main()
