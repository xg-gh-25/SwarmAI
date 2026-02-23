"""Property-based tests for entity enum field validation.

**Feature: workspace-refactor, Property 6: Entity enum field validation**

Uses Hypothesis to verify that all enum values are valid for status, source_type,
priority, and other enum fields across all entity schemas. Also verifies that
invalid values are rejected by Pydantic validation.

**Validates: Requirements 4.2, 4.3, 4.4, 5.2, 5.3**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pydantic import ValidationError

# --- ToDo enums ---
from schemas.todo import ToDoStatus, ToDoSourceType, Priority, ToDoCreate, ToDoUpdate, ToDoResponse

# --- Task enums ---
from schemas.task import TaskStatus

# --- PlanItem enums ---
from schemas.plan_item import PlanItemStatus, FocusType, PlanItemCreate

# --- Communication enums ---
from schemas.communication import CommunicationStatus, ChannelType, CommunicationCreate

# --- Artifact enums ---
from schemas.artifact import ArtifactType, ArtifactCreate

# --- Reflection enums ---
from schemas.reflection import ReflectionType, GeneratedBy, ReflectionCreate

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


class TestPlanItemEnumValidation:
    """Property 6: PlanItem enum field validation.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_STATUSES = {"active", "deferred", "completed", "cancelled"}
    EXPECTED_FOCUS_TYPES = {"today", "upcoming", "blocked"}

    @given(status=st.sampled_from(list(PlanItemStatus)))
    @PROPERTY_SETTINGS
    def test_all_plan_item_status_values_are_valid(self, status: PlanItemStatus):
        """Every PlanItemStatus enum member has a value in the expected set."""
        assert status.value in self.EXPECTED_STATUSES

    @given(focus_type=st.sampled_from(list(FocusType)))
    @PROPERTY_SETTINGS
    def test_all_focus_type_values_are_valid(self, focus_type: FocusType):
        """Every FocusType enum member has a value in the expected set."""
        assert focus_type.value in self.EXPECTED_FOCUS_TYPES

    @given(invalid_status=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_plan_item_status_rejected(self, invalid_status: str):
        """Invalid PlanItemStatus values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            PlanItemCreate(
                workspace_id="ws-1",
                title="Test",
                status=invalid_status,
            )

    @given(invalid_focus=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_focus_type_rejected(self, invalid_focus: str):
        """Invalid FocusType values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            PlanItemCreate(
                workspace_id="ws-1",
                title="Test",
                focus_type=invalid_focus,
            )

    def test_plan_item_status_enum_completeness(self):
        """PlanItemStatus enum has exactly the expected members."""
        actual = {s.value for s in PlanItemStatus}
        assert actual == self.EXPECTED_STATUSES

    def test_focus_type_enum_completeness(self):
        """FocusType enum has exactly the expected members."""
        actual = {f.value for f in FocusType}
        assert actual == self.EXPECTED_FOCUS_TYPES


class TestCommunicationEnumValidation:
    """Property 6: Communication enum field validation.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_STATUSES = {"pending_reply", "ai_draft", "follow_up", "sent", "cancelled"}
    EXPECTED_CHANNEL_TYPES = {"email", "slack", "meeting", "other"}

    @given(status=st.sampled_from(list(CommunicationStatus)))
    @PROPERTY_SETTINGS
    def test_all_communication_status_values_are_valid(self, status: CommunicationStatus):
        """Every CommunicationStatus enum member has a value in the expected set."""
        assert status.value in self.EXPECTED_STATUSES

    @given(channel_type=st.sampled_from(list(ChannelType)))
    @PROPERTY_SETTINGS
    def test_all_channel_type_values_are_valid(self, channel_type: ChannelType):
        """Every ChannelType enum member has a value in the expected set."""
        assert channel_type.value in self.EXPECTED_CHANNEL_TYPES

    @given(invalid_status=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_communication_status_rejected(self, invalid_status: str):
        """Invalid CommunicationStatus values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            CommunicationCreate(
                workspace_id="ws-1",
                title="Test",
                recipient="someone",
                status=invalid_status,
            )

    @given(invalid_channel=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_channel_type_rejected(self, invalid_channel: str):
        """Invalid ChannelType values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            CommunicationCreate(
                workspace_id="ws-1",
                title="Test",
                recipient="someone",
                channel_type=invalid_channel,
            )

    def test_communication_status_enum_completeness(self):
        """CommunicationStatus enum has exactly the expected members."""
        actual = {s.value for s in CommunicationStatus}
        assert actual == self.EXPECTED_STATUSES

    def test_channel_type_enum_completeness(self):
        """ChannelType enum has exactly the expected members."""
        actual = {c.value for c in ChannelType}
        assert actual == self.EXPECTED_CHANNEL_TYPES


class TestArtifactEnumValidation:
    """Property 6: Artifact enum field validation.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_TYPES = {"plan", "report", "doc", "decision", "other"}

    @given(artifact_type=st.sampled_from(list(ArtifactType)))
    @PROPERTY_SETTINGS
    def test_all_artifact_type_values_are_valid(self, artifact_type: ArtifactType):
        """Every ArtifactType enum member has a value in the expected set."""
        assert artifact_type.value in self.EXPECTED_TYPES

    @given(invalid_type=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_artifact_type_rejected(self, invalid_type: str):
        """Invalid ArtifactType values are rejected by Pydantic validation."""
        with pytest.raises(ValidationError):
            ArtifactCreate(
                workspace_id="ws-1",
                title="Test",
                file_path="/test/path.md",
                created_by="user",
                artifact_type=invalid_type,
            )

    def test_artifact_type_enum_completeness(self):
        """ArtifactType enum has exactly the expected members."""
        actual = {t.value for t in ArtifactType}
        assert actual == self.EXPECTED_TYPES


class TestReflectionEnumValidation:
    """Property 6: Reflection enum field validation.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    EXPECTED_TYPES = {"daily_recap", "weekly_summary", "lessons_learned"}
    EXPECTED_GENERATED_BY = {"user", "agent", "system"}

    @given(reflection_type=st.sampled_from(list(ReflectionType)))
    @PROPERTY_SETTINGS
    def test_all_reflection_type_values_are_valid(self, reflection_type: ReflectionType):
        """Every ReflectionType enum member has a value in the expected set."""
        assert reflection_type.value in self.EXPECTED_TYPES

    @given(generated_by=st.sampled_from(list(GeneratedBy)))
    @PROPERTY_SETTINGS
    def test_all_generated_by_values_are_valid(self, generated_by: GeneratedBy):
        """Every GeneratedBy enum member has a value in the expected set."""
        assert generated_by.value in self.EXPECTED_GENERATED_BY

    @given(invalid_type=invalid_enum_strategy)
    @PROPERTY_SETTINGS
    def test_invalid_reflection_type_rejected(self, invalid_type: str):
        """Invalid ReflectionType values are rejected by Pydantic validation."""
        from datetime import datetime
        with pytest.raises(ValidationError):
            ReflectionCreate(
                workspace_id="ws-1",
                title="Test",
                file_path="/test/path.md",
                period_start=datetime.now(),
                period_end=datetime.now(),
                reflection_type=invalid_type,
            )

    def test_reflection_type_enum_completeness(self):
        """ReflectionType enum has exactly the expected members."""
        actual = {t.value for t in ReflectionType}
        assert actual == self.EXPECTED_TYPES

    def test_generated_by_enum_completeness(self):
        """GeneratedBy enum has exactly the expected members."""
        actual = {g.value for g in GeneratedBy}
        assert actual == self.EXPECTED_GENERATED_BY


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

    Verifies that the Priority enum is shared and consistent across all
    entities that use it (ToDo, PlanItem, Communication).

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

    @given(priority=st.sampled_from(list(Priority)))
    @PROPERTY_SETTINGS
    def test_priority_accepted_in_plan_item_create(self, priority: Priority):
        """All Priority values are accepted in PlanItemCreate.

        **Validates: Requirements 4.4**
        """
        item = PlanItemCreate(workspace_id="ws-1", title="Test", priority=priority)
        assert item.priority == priority

    @given(priority=st.sampled_from(list(Priority)))
    @PROPERTY_SETTINGS
    def test_priority_accepted_in_communication_create(self, priority: Priority):
        """All Priority values are accepted in CommunicationCreate.

        **Validates: Requirements 4.4**
        """
        comm = CommunicationCreate(
            workspace_id="ws-1",
            title="Test",
            recipient="someone",
            priority=priority,
        )
        assert comm.priority == priority
