"""Search text for a chat message row: what ``messages.content_seg`` is built from.

``messages_fts`` indexes ``content_seg``. Its job is session search and recall:
find the conversation in which a person or the agent SAID something. Only
words someone can search for belong in it.

Before this module, ``content_seg`` was ``expand_cjk`` of the whole raw JSON
of ``content``. That indexed base64 image/document payloads (a 6.7 MB message
produced a 6.6 MB ``content_seg``), thinking signatures, JSON keys
(``type``, ``tool_use_id``), and tool-call ids (``toolu_bdrk_...``). None of it
can be searched meaningfully, and it made ``content_seg`` + ``messages_fts``
the largest part of the database.

Kept: ``text`` blocks, each ``tool_use`` block's one-line ``summary`` (falls
back to the tool ``name``), so "the session where I ran X on file Y" stays
findable, and each ``turn_product`` block's path or action + target. Dropped:
thinking, tool results, images, documents, ids, keys.

A ``content`` that is not a JSON block list (a legacy plain string) is indexed
whole, as before.

Public:
- ``message_search_text(content)`` -- plain text to segment.
- ``message_content_seg(content)`` -- ``expand_cjk`` of that text; the value
  every writer stores in ``messages.content_seg``.
"""

from __future__ import annotations

import json
from typing import Any

from core.cjk_index import expand_cjk


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        text = block.get("text")
        return text if isinstance(text, str) else ""
    if kind == "tool_use":
        summary = block.get("summary")
        if isinstance(summary, str) and summary:
            return summary
        name = block.get("name")
        return name if isinstance(name, str) else ""
    if kind == "turn_product":
        # What the turn produced (core/turn_products): the file's path, or the
        # remote action and its target, so "the report I wrote" is findable.
        parts = [block.get(k) for k in ("path", "action", "target")]
        return " ".join(p for p in parts if isinstance(p, str) and p)
    return ""


def message_search_text(content: Any) -> str:
    """Return the searchable words of a message ``content`` value.

    ``content`` may be a block list, a single block dict, a JSON string of
    either, or a plain string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return content
        if not isinstance(parsed, (list, dict)):
            return content
        content = parsed
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return str(content)
    return "\n".join(t for t in (_block_text(b) for b in content) if t)


def message_content_seg(content: Any) -> str:
    """The value stored in ``messages.content_seg`` for ``content``."""
    return expand_cjk(message_search_text(content))
