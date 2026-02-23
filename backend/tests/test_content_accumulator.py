"""Property-based tests for ContentBlockAccumulator deduplication.

# Feature: agent-code-refactoring, Property 4: Content block deduplication equivalence

Uses Hypothesis to verify that ContentBlockAccumulator correctly deduplicates
content blocks: duplicate text blocks (same text), duplicate tool_use (same id),
and duplicate tool_result (same tool_use_id) are each added only once, while
blocks with unknown types or missing IDs are always added.

**Validates: Requirements 5.2, 5.3**
"""

import pytest
from hypothesis import given, strategies as st, settings

from core.content_accumulator import ContentBlockAccumulator


# --- Strategies ---

# Non-empty text for block content
_text_content = st.text(min_size=0, max_size=200)

# Non-empty IDs for tool_use and tool_result
_block_id = st.text(min_size=1, max_size=50)

# Strategy for text content blocks
_text_block = st.fixed_dictionaries({
    "type": st.just("text"),
    "text": _text_content,
})

# Strategy for tool_use content blocks
_tool_use_block = st.fixed_dictionaries({
    "type": st.just("tool_use"),
    "id": _block_id,
    "name": st.text(min_size=1, max_size=30),
})

# Strategy for tool_result content blocks
_tool_result_block = st.fixed_dictionaries({
    "type": st.just("tool_result"),
    "tool_use_id": _block_id,
    "content": _text_content,
})

# Strategy for unknown-type blocks (always added, no dedup)
_unknown_block = st.fixed_dictionaries({
    "type": st.sampled_from(["image", "audio", "video", "custom", "other"]),
})

# Strategy for blocks with missing IDs (tool_use without id, tool_result without tool_use_id)
_tool_use_missing_id = st.fixed_dictionaries({
    "type": st.just("tool_use"),
    "name": st.text(min_size=1, max_size=30),
})

_tool_result_missing_id = st.fixed_dictionaries({
    "type": st.just("tool_result"),
    "content": _text_content,
})

# Combined strategy for any content block
_any_block = st.one_of(
    _text_block,
    _tool_use_block,
    _tool_result_block,
    _unknown_block,
    _tool_use_missing_id,
    _tool_result_missing_id,
)

PROPERTY_SETTINGS = settings(max_examples=100)


class TestContentBlockDeduplication:
    """Property 4: Content block deduplication equivalence.

    **Validates: Requirements 5.2, 5.3**

    For any sequence of content block dicts, adding them to a
    ContentBlockAccumulator shall deduplicate text blocks by text content,
    tool_use blocks by id, and tool_result blocks by tool_use_id, while
    always adding blocks with unknown types or missing IDs.
    """

    @given(text=_text_content)
    @PROPERTY_SETTINGS
    def test_duplicate_text_blocks_added_once(self, text: str):
        """Duplicate text blocks (same text) are added only once.

        **Validates: Requirements 5.2, 5.3**
        """
        acc = ContentBlockAccumulator()
        block = {"type": "text", "text": text}

        first_add = acc.add(block)
        second_add = acc.add(dict(block))  # fresh copy, same content

        assert first_add is True
        assert second_add is False
        assert len(acc.blocks) == 1
        assert acc.blocks[0]["text"] == text

    @given(block_id=_block_id)
    @PROPERTY_SETTINGS
    def test_duplicate_tool_use_blocks_added_once(self, block_id: str):
        """Duplicate tool_use blocks (same id) are added only once.

        **Validates: Requirements 5.2, 5.3**
        """
        acc = ContentBlockAccumulator()
        block1 = {"type": "tool_use", "id": block_id, "name": "func_a"}
        block2 = {"type": "tool_use", "id": block_id, "name": "func_b"}

        first_add = acc.add(block1)
        second_add = acc.add(block2)

        assert first_add is True
        assert second_add is False
        assert len(acc.blocks) == 1
        assert acc.blocks[0]["id"] == block_id

    @given(tool_use_id=_block_id)
    @PROPERTY_SETTINGS
    def test_duplicate_tool_result_blocks_added_once(self, tool_use_id: str):
        """Duplicate tool_result blocks (same tool_use_id) are added only once.

        **Validates: Requirements 5.2, 5.3**
        """
        acc = ContentBlockAccumulator()
        block1 = {"type": "tool_result", "tool_use_id": tool_use_id, "content": "result_a"}
        block2 = {"type": "tool_result", "tool_use_id": tool_use_id, "content": "result_b"}

        first_add = acc.add(block1)
        second_add = acc.add(block2)

        assert first_add is True
        assert second_add is False
        assert len(acc.blocks) == 1
        assert acc.blocks[0]["tool_use_id"] == tool_use_id

    @given(blocks=st.lists(_unknown_block, min_size=1, max_size=10))
    @PROPERTY_SETTINGS
    def test_unknown_type_blocks_always_added(self, blocks: list[dict]):
        """Blocks with unknown types are always added (no deduplication).

        **Validates: Requirements 5.2, 5.3**
        """
        acc = ContentBlockAccumulator()
        for block in blocks:
            result = acc.add(block)
            assert result is True

        assert len(acc.blocks) == len(blocks)

    @given(blocks=st.lists(
        st.one_of(_tool_use_missing_id, _tool_result_missing_id),
        min_size=1,
        max_size=10,
    ))
    @PROPERTY_SETTINGS
    def test_blocks_with_missing_ids_always_added(self, blocks: list[dict]):
        """Blocks with missing IDs are always added (no deduplication).

        **Validates: Requirements 5.2, 5.3**
        """
        acc = ContentBlockAccumulator()
        for block in blocks:
            result = acc.add(block)
            assert result is True

        assert len(acc.blocks) == len(blocks)

    @given(blocks=st.lists(_any_block, min_size=1, max_size=20))
    @PROPERTY_SETTINGS
    def test_deduplication_equivalence_via_extend(self, blocks: list[dict]):
        """Adding blocks via extend produces the same result as individual add calls.

        **Validates: Requirements 5.2, 5.3**
        """
        # Add one-by-one
        acc_add = ContentBlockAccumulator()
        for block in blocks:
            acc_add.add(block)

        # Add via extend
        acc_extend = ContentBlockAccumulator()
        acc_extend.extend(blocks)

        assert len(acc_add.blocks) == len(acc_extend.blocks)
        for a, b in zip(acc_add.blocks, acc_extend.blocks):
            assert a == b
