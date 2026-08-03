"""Tests for _merge_consecutive_assistant_messages.

Covers: consecutive merge, user-separator no-merge, legacy string content,
empty content, and model attribution.
"""
from routers.chat import _merge_consecutive_assistant_messages


class TestMergeConsecutiveAssistantMessages:
    """Unit tests for the read-path merge function."""

    def test_consecutive_assistant_rows_merge(self):
        """Two consecutive assistant rows merge into one message."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "Hello"}], "model": "opus", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": " World"}], "model": "opus", "created_at": "t2"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert len(result) == 1
        assert result[0]["id"] == "1"  # first row's id
        assert result[0]["created_at"] == "t1"  # first row's timestamp
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["text"] == "Hello"
        assert result[0]["content"][1]["text"] == " World"

    def test_user_row_separates_assistant_rows(self):
        """A user row between assistant rows prevents merge."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "Response 1"}], "model": "opus", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "user",
             "content": "Question", "model": None, "created_at": "t2"},
            {"id": "3", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "Response 2"}], "model": "opus", "created_at": "t3"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert len(result) == 3
        assert result[0]["content"][0]["text"] == "Response 1"
        assert result[2]["content"][0]["text"] == "Response 2"

    def test_legacy_string_content_normalized(self):
        """Legacy string content (not list) is wrapped in text block before merge."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": "Legacy text", "model": "sonnet", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": " new text"}], "model": "opus", "created_at": "t2"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert len(result) == 1
        assert result[0]["content"][0] == {"type": "text", "text": "Legacy text"}
        assert result[0]["content"][1] == {"type": "text", "text": " new text"}

    def test_empty_messages_returns_empty(self):
        """Empty input returns empty output."""
        assert _merge_consecutive_assistant_messages([]) == []

    def test_single_message_no_merge(self):
        """Single message is returned as-is (wrapped in standard dict)."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "Only one"}], "model": "opus", "created_at": "t1"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert len(result) == 1
        assert result[0]["content"][0]["text"] == "Only one"

    def test_last_model_wins(self):
        """Merged message takes the LAST row's model."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "a"}], "model": "sonnet", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "b"}], "model": "opus", "created_at": "t2"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert result[0]["model"] == "opus"

    def test_no_in_place_mutation_of_source(self):
        """Merge must not mutate the input message dicts' content lists."""
        original_content = [{"type": "text", "text": "Hello"}]
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": original_content, "model": "opus", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": " World"}], "model": "opus", "created_at": "t2"},
        ]
        _merge_consecutive_assistant_messages(messages)
        # Original content list must NOT have been mutated
        assert len(original_content) == 1, (
            "Source content list was mutated in-place — "
            "this breaks if a read cache is ever added"
        )

    def test_three_consecutive_merge(self):
        """Three consecutive assistant rows all merge into one."""
        messages = [
            {"id": "1", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "a"}], "model": "m1", "created_at": "t1"},
            {"id": "2", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "b"}], "model": "m2", "created_at": "t2"},
            {"id": "3", "session_id": "s1", "role": "assistant",
             "content": [{"type": "text", "text": "c"}], "model": "m3", "created_at": "t3"},
        ]
        result = _merge_consecutive_assistant_messages(messages)
        assert len(result) == 1
        assert result[0]["id"] == "1"
        assert [b["text"] for b in result[0]["content"]] == ["a", "b", "c"]
        assert result[0]["model"] == "m3"  # last wins
