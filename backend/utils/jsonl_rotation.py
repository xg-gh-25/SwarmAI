"""JSONL file rotation utility.

Provides a single ``rotate_jsonl_if_oversized`` function that any module
can call after appending a line to a JSONL file.  One ``stat()`` per call;
actual rotation (read → keep newest → atomic rewrite) happens rarely.

Defaults: trigger at 512 KB, keep newest 500 entries.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE_BYTES = 512 * 1024   # 512 KB trigger threshold
_DEFAULT_MAX_ENTRIES = 500              # Keep newest N lines after rotation


def rotate_jsonl_if_oversized(
    path: Path,
    max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
) -> bool:
    """Rotate a JSONL file if it exceeds ``max_size_bytes``.

    Rotation keeps the newest ``max_entries`` lines.  Uses atomic
    write-to-tmp + rename (same filesystem = POSIX atomic).

    Returns True if rotation was performed, False otherwise.
    Best-effort — never raises.
    """
    try:
        if not path.exists():
            return False

        if path.stat().st_size <= max_size_bytes:
            return False

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) <= max_entries:
            return False  # Size exceeded but entry count is OK — likely large entries

        kept = lines[-max_entries:]
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.rename(path)
        logger.info(
            "Rotated %s: %d → %d entries",
            path.name, len(lines), len(kept),
        )
        return True

    except Exception:
        logger.exception("Failed to rotate %s", path)
        return False
