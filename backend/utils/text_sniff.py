"""Text-or-binary sniffing for the file viewer's read and write paths.

The Canvas routes any unlisted extension to its text editor, so the backend must be
the place that knows a file is not text (docs/design/canvas-file-types.md, P0): a
binary opened as text and saved would be overwritten with its base64.

- ``SNIFF_BYTES``      — how much of a file is read to decide (8 KB)
- ``looks_like_text``  — decide from a byte prefix: no NUL, valid UTF-8 (a multi-byte
                         character cut at the end of the prefix is fine)
- ``is_text_on_disk``  — the same for a path (reads at most SNIFF_BYTES)
"""
from __future__ import annotations

import codecs
from pathlib import Path

SNIFF_BYTES = 8192


def looks_like_text(prefix: bytes) -> bool:
    """True when ``prefix`` (the start of a file) reads as UTF-8 text."""
    if b"\x00" in prefix:
        return False
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(prefix, final=False)  # an incomplete trailing char is not an error
    except UnicodeDecodeError:
        return False
    return True


def is_text_on_disk(path: Path) -> bool:
    """True when the file at ``path`` reads as text. Reads at most SNIFF_BYTES."""
    with open(path, "rb") as fh:
        return looks_like_text(fh.read(SNIFF_BYTES))
