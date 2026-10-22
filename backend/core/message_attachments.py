"""Attachment bytes live in files, message rows hold a reference.

An image/document block arrives as ``{"type": "image"|"document", "source":
{"type": "base64", "media_type": ..., "data": <base64>}}``. Storing that
verbatim put every attachment's base64 into ``messages.content`` (and, before
``core/message_search_text.py``, into the search index too). Nothing reads
those bytes back from a sent row: the chat view renders text blocks only and
resume writes ``[image attachment]``. The one real reader is the pending-
message drain, which must re-send the original block to the model.

So on write the bytes go to ``<data dir>/attachments/<sha256>`` (decoded,
content-addressed, so a repeated attachment is stored once) and the row keeps

    {"type": "image", "source": {"type": "attachment_ref",
     "media_type": "image/png", "sha256": "...", "bytes": 12345}}

and the drain turns the reference back into the base64 block. A reference whose
file is gone becomes a text block saying so, never a block the model rejects.

Public:
- ``attachments_dir(db_path)`` -- where the files go for a database file.
- ``externalize_attachments(content, store_dir)`` -- write side.
- ``rehydrate_attachments(content, store_dir)`` -- drain side.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REF_TYPE = "attachment_ref"
_KINDS = ("image", "document")


def attachments_dir(db_path: str | os.PathLike) -> Path:
    return Path(db_path).parent / "attachments"


def _as_blocks(content: Any) -> list | None:
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _write_blob(store_dir: Path, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    path = store_dir / digest
    if path.exists():
        return digest
    store_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=store_dir, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return digest


def externalize_attachments(content: Any, store_dir: Path) -> Any:
    """Return ``content`` with base64 image/document data moved to files.

    Never mutates its input. Content that is not a block list, or has no
    base64 attachment, is returned unchanged (same object).
    """
    blocks = _as_blocks(content)
    if not blocks:
        return content
    out: list = []
    changed = False
    for block in blocks:
        source = block.get("source") if isinstance(block, dict) else None
        if (isinstance(source, dict) and block.get("type") in _KINDS
                and source.get("type") == "base64"
                and isinstance(source.get("data"), str) and source["data"]):
            try:
                raw = base64.b64decode(source["data"], validate=True)
            except (binascii.Error, ValueError):
                out.append(block)  # not real base64: leave it alone
                continue
            digest = _write_blob(store_dir, raw)
            out.append({**block, "source": {
                "type": REF_TYPE,
                "media_type": source.get("media_type", ""),
                "sha256": digest,
                "bytes": len(raw),
            }})
            changed = True
        else:
            out.append(block)
    if not changed:
        return content
    return out if isinstance(content, list) else json.dumps(out, ensure_ascii=False)


def rehydrate_attachments(content: list | None, store_dir: Path) -> list | None:
    """Turn ``attachment_ref`` blocks back into base64 blocks for re-sending."""
    if not content:
        return content
    out: list = []
    for block in content:
        source = block.get("source") if isinstance(block, dict) else None
        if not (isinstance(source, dict) and source.get("type") == REF_TYPE):
            out.append(block)
            continue
        digest = str(source.get("sha256") or "")
        path = store_dir / digest
        try:
            if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
                raise FileNotFoundError(digest)
            raw = path.read_bytes()
        except OSError:
            logger.warning("attachment %s missing from %s", digest[:12], store_dir)
            out.append({"type": "text",
                        "text": f"[{block.get('type', 'attachment')} attachment unavailable]"})
            continue
        out.append({**block, "source": {
            "type": "base64",
            "media_type": source.get("media_type", ""),
            "data": base64.b64encode(raw).decode("ascii"),
        }})
    return out
