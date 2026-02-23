"""Content block accumulator with O(1) deduplication.

Provides the ContentBlockAccumulator class for efficiently accumulating
content blocks while preventing duplicates. Uses a hash-set for O(1)
duplicate detection instead of O(n) list scanning.

This is a pure utility class with zero external dependencies.
"""

from typing import Any


class ContentBlockAccumulator:
    """Accumulates content blocks with O(1) deduplication.

    Used to prevent duplicate content when SDK sends cumulative messages.
    Uses a set for O(1) duplicate detection instead of O(n) list scanning.
    """

    def __init__(self) -> None:
        self._blocks: list[dict[str, Any]] = []
        self._seen_keys: set[str] = set()

    @staticmethod
    def _get_key(block: dict[str, Any]) -> str | None:
        """Generate unique key for a content block.

        Returns None for unknown types or blocks with missing IDs,
        which causes them to always be added (no deduplication).
        """
        block_type = block.get('type')
        if block_type == 'text':
            # Use hash for text content to handle large strings efficiently
            # Note: hash() collisions are theoretically possible but extremely rare
            # for the SDK cumulative message deduplication use case
            text = block.get('text', '')
            return f"text:{hash(text)}"
        elif block_type == 'tool_use':
            block_id = block.get('id')
            return f"tool_use:{block_id}" if block_id else None
        elif block_type == 'tool_result':
            tool_use_id = block.get('tool_use_id')
            return f"tool_result:{tool_use_id}" if tool_use_id else None
        return None

    def add(self, block: dict[str, Any]) -> bool:
        """Add block if not duplicate. Returns True if added."""
        key = self._get_key(block)
        if key is None:
            # Unknown type - always add
            self._blocks.append(block)
            return True
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self._blocks.append(block)
        return True

    def extend(self, blocks: list[dict[str, Any]]) -> None:
        """Add multiple blocks with deduplication."""
        for block in blocks:
            self.add(block)

    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Get the accumulated blocks as a list."""
        return self._blocks

    def __bool__(self) -> bool:
        return bool(self._blocks)
