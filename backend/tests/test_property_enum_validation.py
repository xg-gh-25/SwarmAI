"""Property-based tests for entity enum field validation.

**Feature: workspace-refactor, Property 6: Entity enum field validation**

Uses Hypothesis to verify that all enum values are valid for status, source_type,
priority, and other enum fields across preserved entity schemas (ToDo, Task,
ChatThread). Operating Loop entity tests (PlanItem, Communication, Artifact,
Reflection) were removed as part of the operating-loop-cleanup spec.

**Validates: Requirements 4.2, 4.3, 4.4, 5.2, 5.3**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pydantic import ValidationError

# --- ToDo enums ---
from schemas.todo import ToDoStatus, ToDoSourceType, Priority, ToDoCreate, ToDoUpdate, ToDoResponse

# --- Task enums ---
from schemas.task import TaskStatus

# --- ChatThread enums ---
from schemas.chat_thread import ChatMode, MessageRole, SummaryType, ChatThreadCreate, ChatMessageCreate


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy for generating strings that are NOT valid enum values
invalid_enum_strategy = st.text(min_size=1, max_size=50).filter(
    lambda x: x not in {
        # All known valid enum values across all entities
        "pending", "overdue", "in_discussion", "handled", "cancelled", "deleted",
        "manual", "email", "slack", "meeting", "integration",
        "high", "medium", "low", "none",
        "running", "completed", "failed", "draft", "wip", "blocked",
        "active", "deferred",
        "today", "upcoming",
        "pending_reply", "ai_draft", "follow_up", "sent",
        "other",
        "plan", "report", "doc", "decision",
        "daily_recap", "weekly_summary", "lessons_learned",
        "user", "agent", "system",
        "explore", "execute",
        "assistant", "tool",
        "rolling", "final",
    }
)


class TestToDoEnumValidation:
    """Property 6: ToDo enum field validation.

    *For any* ToDo, the `status` field SHALL be one of: pending, overdue,
    in_discussion, handled, cancelled, deleted.
    *For any* ToDo, the `source_type` field SHALL be one of: manual, email,
    slack, meeting, integration.
    *For any* ToDo, the `priority` field SHALL be one of: high, medium, low, none.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_STATUSES = {"pending", "overdue", "in_discussion", "handled", "cancelled", "deleted"}
    EXPECTED_SOURCE_TYPES = {"manual", "email", "slack", "meeting", "integration"}
    EXPECTED_PRIORITIES = {"high", "medium", "low", "none"}

    @given(status=st.sampled_from(list(ToDoStatus)))
    @PROPERTY_SETTINGS
    def test_all_todo_status_values_are_valid(self, status: ToDoStatus):
        """Every ToDoStatus enum member has a value in the expected set.

        **Validates: Requirements 4.2**
        """
        assert status.value in self.EXPECTED_STATUSES

    @given(source_type=st.sampled_from(list(ToDoSourceType)))
    @PROPERTY_SETTINGS
    def test_all_todo_source_type_values_are_valid(self, source_type: ToDoSourceType):
        """Every ToDoSourceType enum member has a value in the expected set.

        **Validates: Requirements 4.3**
        """
        assert source_type.value in self.EXPECTED_SOURCE_TYPES

    @given(priority=st.sampled_from(list(Priority)))
    @PROPERTY_SETTINGS
    def test_all_priority_values_are_valid(self, priority: Priority):
        """Every Priority enum member has a value in the expected set.

        **Validates: Requirements 4.4**
        """
        assert priority.value in self.EXPECTED_PRIORITIES

    @given(invalid_status=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_todo_status_rejected(self, invalid_status: str):
        """Invalid status values are rejected by Pydantic validation.

        **Validates: Requirements 4.2**
        """
        with pytest.raises(ValidationError):
            ToDoUpdate(status=invalid_status)

    @given(invalid_source_type=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_todo_source_type_rejected(self, invalid_source_type: str):
        """Invalid source_type values are rejected by Pydantic validation.

        **Validates: Requirements 4.3**
        """
        with pytest.raises(ValidationError):
            ToDoCreate(
                workspace_id="ws-1",
                title="Test",
                source_type=invalid_source_type,
            )

    @given(invalid_priority=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_todo_priority_rejected(self, invalid_priority: str):
        """Invalid priority values are rejected by Pydantic validation.

        **Validates: Requirements 4.4**
        """
        with pytest.raises(ValidationError):
            ToDoCreate(
                workspace_id="ws-1",
                title="Test",
                priority=invalid_priority,
            )

    def test_todo_status_enum_completeness(self):
        """ToDoStatus enum has exactly the expected members.

        **Validates: Requirements 4.2**
        """
        actual = {s.value for s in ToDoStatus}
        assert actual == self.EXPECTED_STATUSES

    def test_todo_source_type_enum_completeness(self):
        """ToDoSourceType enum has exactly the expected members.

        **Validates: Requirements 4.3**
        """
        actual = {s.value for s in ToDoSourceType}
        assert actual == self.EXPECTED_SOURCE_TYPES

    def test_priority_enum_completeness(self):
        """Priority enum has exactly the expected members.

        **Validates: Requirements 4.4**
        """
        actual = {p.value for p in Priority}
        assert actual == self.EXPECTED_PRIORITIES


class TestTaskEnumValidation:
    """Property 6: Task enum field validation.

    *For any* Task, the `status` field SHALL be one of: draft, wip, blocked,
    completed, cancelled.

    Note: The current TaskStatus enum still uses legacy values (pending, running,
    failed). This test validates the current enum values. Once the migration in
    task 4.10 is complete, the expected values should be updated.

    **Validates: Requirements 5.2, 5.3**
    """

    # Current legacy values - the task schema hasn't been migrated yet
    EXPECTED_TASK_STATUSES = {"pending", "running", "completed", "failed", "cancelled"}

    @given(status=st.sampled_from(list(TaskStatus)))
    @PROPERTY_SETTINGS
    def test_all_task_status_values_are_valid(self, status: TaskStatus):
        """Every TaskStatus enum member has a value in the expected set.

        **Validates: Requirements 5.2**
        """
        assert status.value in self.EXPECTED_TASK_STATUSES

    def test_task_status_enum_completeness(self):
        """TaskStatus enum has exactly the expected members.

        **Validates: Requirements 5.2**
        """
        actual = {s.value for s in TaskStatus}
        assert actual == self.EXPECTED_TASK_STATUSES


class TestChatThreadEnumValidation:
    """Property 6: ChatThread enum field validation.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_MODES = {"explore", "execute"}
    EXPECTED_ROLES = {"user", "assistant", "tool", "system"}
    EXPECTED_SUMMARY_TYPES = {"rolling", "final"}

    @given(mode=st.sampled_from(list(ChatMode)))
    @PROPERTY_SETTINGS
    def test_all_chat_mode_values_are_valid(self, mode: ChatMode):
        """Every ChatMode enum member has a value in the expected set."""
        assert mode.value in self.EXPECTED_MODES

    @given(role=st.sampled_from(list(MessageRole)))
    @PROPERTY_SETTINGS
    def test_all_message_role_values_are_valid(self, role: MessageRole):
        """Every MessageRole enum member has a value in the expected set."""
        assert role.value in self.EXPECTED_ROLES

    @given(summary_type=st.sampled_from(list(SummaryType)))
    @PROPERTY_SETTINGS
    def test_all_summary_type_values_are_valid(self, summary_type: SummaryType):
        """Every SummaryType enum member has a value in the expected set."""
        assert summary_type.value in self.EXPECTED_SUMMARY_TYPES

    @given(invalid_mode=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_chat_mode_rejected(self, invalid_mode: str):
        """Invalid ChatMode values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            ChatThreadCreate(
                workspace_id="ws-1",
                agent_id="agent-1",
                title="Test",
                mode=invalid_mode,
            )

    @given(invalid_role=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_message_role_rejected(self, invalid_role: str):
        """Invalid MessageRole values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            ChatMessageCreate(
                thread_id="thread-1",
                role=invalid_role,
                content="Test",
            )

    def test_chat_mode_enum_completeness(self):
        """ChatMode enum has exactly the expected members."""
        actual = {m.value for m in ChatMode}
        assert actual == self.EXPECTED_MODES

    def test_message_role_enum_completeness(self):
        """MessageRole enum has exactly the expected members."""
        actual = {r.value for r in MessageRole}
        assert actual == self.EXPECTED_ROLES

    def test_summary_type_enum_completeness(self):
        """SummaryType enum has exactly the expected members."""
        actual = {s.value for s in SummaryType}
        assert actual == self.EXPECTED_SUMMARY_TYPES


class TestCrossEntityPriorityConsistency:
    """Property 6: Cross-entity Priority enum consistency.

    Verifies that the Priority enum is accepted by ToDo entities.
    Operating Loop entities (PlanItem, Communication) were removed as part
    of the operating-loop-cleanup spec.

    **Validates: Requirements 4.4**
    """

    @given(priority=st.sampled_from(list(Priority)))
    @PROPERTY_SETTINGS
    def test_priority_accepted_in_todo_create(self, priority: Priority):
        """All Priority values are accepted in ToDoCreate.

        **Validates: Requirements 4.4**
        """
        todo = ToDoCreate(workspace_id="ws-1", title="Test", priority=priority)
        assert todo.priority == priority
