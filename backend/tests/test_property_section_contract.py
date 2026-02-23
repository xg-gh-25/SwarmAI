"""Property-based tests for section endpoint unified response contract.

**Feature: workspace-refactor, Property 11: Section endpoint unified response contract**

Uses Hypothesis to verify that all section responses conform to the unified
response contract: counts (dict), groups (list of {name, items}),
pagination ({limit, offset, total, has_more}), sort_keys (list),
and last_updated_at (ISO8601 timestamp or None).

**Validates: Requirements 7.1-7.12, 33.1-33.6**
"""
import pytest
from datetime import datetime
from hypothesis import given, strategies as st, settings, HealthCheck
from pydantic import ValidationError

from schemas.section import (
    SectionGroup,
    Pagination,
    SectionResponse,
    SectionCounts,
    SignalsCounts,
    PlanCounts,
    ExecuteCounts,
    CommunicateCounts,
    ArtifactsCounts,
    ReflectionCounts,
)


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# Hypothesis strategies for section response components
# ---------------------------------------------------------------------------

# Strategy for generating valid pagination data
pagination_strategy = st.builds(
    Pagination,
    limit=st.integers(min_value=1, max_value=100),
    offset=st.integers(min_value=0, max_value=10000),
    total=st.integers(min_value=0, max_value=100000),
    has_more=st.booleans(),
)

# Strategy for sort keys (realistic field names)
sort_key_strategy = st.lists(
    st.sampled_from([
        "created_at", "updated_at", "priority", "due_date",
        "title", "status", "sort_order", "scheduled_date",
    ]),
    min_size=1,
    max_size=5,
    unique=True,
)

# Strategy for optional last_updated_at timestamps
last_updated_strategy = st.one_of(
    st.none(),
    st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2030, 12, 31)),
)

# Strategy for counts dicts (must have 'total' key and non-negative int values)
counts_strategy = st.fixed_dictionaries(
    {"total": st.integers(min_value=0, max_value=10000)},
    optional={
        "pending": st.integers(min_value=0, max_value=5000),
        "overdue": st.integers(min_value=0, max_value=5000),
        "in_discussion": st.integers(min_value=0, max_value=5000),
        "today": st.integers(min_value=0, max_value=5000),
        "upcoming": st.integers(min_value=0, max_value=5000),
        "blocked": st.integers(min_value=0, max_value=5000),
        "draft": st.integers(min_value=0, max_value=5000),
        "wip": st.integers(min_value=0, max_value=5000),
        "completed": st.integers(min_value=0, max_value=5000),
    },
)

# Strategy for section group names (realistic sub-category names)
group_name_strategy = st.sampled_from([
    "pending", "overdue", "in_discussion", "handled",
    "today", "upcoming", "blocked",
    "draft", "wip", "completed",
    "pending_reply", "ai_draft", "follow_up", "sent",
    "plan", "report", "doc", "decision", "other",
    "daily_recap", "weekly_summary", "lessons_learned",
])

# Strategy for simple item dicts (representing serialized entity data)
simple_item_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=36, alphabet=st.characters(whitelist_categories=("Ll", "Nd"))),
    "title": st.text(min_size=1, max_size=200),
})

# Strategy for SectionGroup with simple dict items
section_group_strategy = st.builds(
    SectionGroup[dict],
    name=group_name_strategy,
    items=st.lists(simple_item_strategy, min_size=0, max_size=5),
)

# Strategy for a complete SectionResponse
section_response_strategy = st.builds(
    SectionResponse[dict],
    counts=counts_strategy,
    groups=st.lists(section_group_strategy, min_size=0, max_size=6),
    pagination=pagination_strategy,
    sort_keys=sort_key_strategy,
    last_updated_at=last_updated_strategy,
)


# ---------------------------------------------------------------------------
# Section-specific group name sets (for validating correct grouping)
# ---------------------------------------------------------------------------

SIGNALS_GROUP_NAMES = {"pending", "overdue", "in_discussion"}
PLAN_GROUP_NAMES = {"today", "upcoming", "blocked"}
EXECUTE_GROUP_NAMES = {"draft", "wip", "blocked", "completed"}
COMMUNICATE_GROUP_NAMES = {"pending_reply", "ai_draft", "follow_up"}
ARTIFACTS_GROUP_NAMES = {"plan", "report", "doc", "decision", "other"}
REFLECTION_GROUP_NAMES = {"daily_recap", "weekly_summary", "lessons_learned"}

ALL_SECTION_GROUP_NAMES = {
    "signals": SIGNALS_GROUP_NAMES,
    "plan": PLAN_GROUP_NAMES,
    "execute": EXECUTE_GROUP_NAMES,
    "communicate": COMMUNICATE_GROUP_NAMES,
    "artifacts": ARTIFACTS_GROUP_NAMES,
    "reflection": REFLECTION_GROUP_NAMES,
}


class TestSectionResponseContract:
    """Property 11: Section endpoint unified response contract.

    *For any* section endpoint (/sections/signals, /sections/plan,
    /sections/execute, /sections/communicate, /sections/artifacts,
    /sections/reflection), the response SHALL contain: counts (dict),
    groups (list of {name, items}), pagination ({limit, offset, total,
    has_more}), sort_keys (list), and last_updated_at (ISO8601 timestamp).

    **Validates: Requirements 7.1-7.12, 33.1-33.6**
    """

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_has_counts_field(self, response: SectionResponse):
        """Verify all section responses contain a counts dict.

        **Validates: Requirements 7.9, 33.1**
        """
        assert hasattr(response, "counts")
        assert isinstance(response.counts, dict)
        assert "total" in response.counts
        for value in response.counts.values():
            assert isinstance(value, int)
            assert value >= 0

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_has_groups_field(self, response: SectionResponse):
        """Verify all section responses contain a groups list of {name, items}.

        **Validates: Requirements 7.1-7.7, 33.1**
        """
        assert hasattr(response, "groups")
        assert isinstance(response.groups, list)
        for group in response.groups:
            assert hasattr(group, "name")
            assert isinstance(group.name, str)
            assert len(group.name) > 0
            assert hasattr(group, "items")
            assert isinstance(group.items, list)

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_has_pagination_field(self, response: SectionResponse):
        """Verify all section responses contain pagination metadata.

        **Validates: Requirements 7.10, 33.1, 33.3, 33.5**
        """
        assert hasattr(response, "pagination")
        pagination = response.pagination
        assert isinstance(pagination, Pagination)
        assert hasattr(pagination, "limit")
        assert hasattr(pagination, "offset")
        assert hasattr(pagination, "total")
        assert hasattr(pagination, "has_more")
        assert isinstance(pagination.limit, int)
        assert isinstance(pagination.offset, int)
        assert isinstance(pagination.total, int)
        assert isinstance(pagination.has_more, bool)
        assert pagination.limit >= 1
        assert pagination.offset >= 0
        assert pagination.total >= 0

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_has_sort_keys_field(self, response: SectionResponse):
        """Verify all section responses contain sort_keys list.

        **Validates: Requirements 33.1, 33.4**
        """
        assert hasattr(response, "sort_keys")
        assert isinstance(response.sort_keys, list)
        for key in response.sort_keys:
            assert isinstance(key, str)
            assert len(key) > 0

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_has_last_updated_at_field(self, response: SectionResponse):
        """Verify all section responses contain last_updated_at field.

        **Validates: Requirements 33.1**
        """
        assert hasattr(response, "last_updated_at")
        if response.last_updated_at is not None:
            assert isinstance(response.last_updated_at, datetime)

    @given(
        limit=st.integers(min_value=1, max_value=100),
        offset=st.integers(min_value=0, max_value=10000),
        total=st.integers(min_value=0, max_value=100000),
        has_more=st.booleans(),
    )
    @PROPERTY_SETTINGS
    def test_pagination_constraints(self, limit, offset, total, has_more):
        """Verify pagination model enforces constraints.

        **Validates: Requirements 33.3, 33.5**
        """
        pagination = Pagination(
            limit=limit, offset=offset, total=total, has_more=has_more
        )
        assert pagination.limit >= 1
        assert pagination.limit <= 100
        assert pagination.offset >= 0
        assert pagination.total >= 0

    @given(limit=st.integers(min_value=-100, max_value=0))
    @PROPERTY_SETTINGS
    def test_pagination_rejects_invalid_limit(self, limit):
        """Verify pagination rejects limit < 1.

        **Validates: Requirements 33.3**
        """
        with pytest.raises(ValidationError):
            Pagination(limit=limit, offset=0, total=0, has_more=False)

    @given(offset=st.integers(min_value=-100, max_value=-1))
    @PROPERTY_SETTINGS
    def test_pagination_rejects_negative_offset(self, offset):
        """Verify pagination rejects negative offset.

        **Validates: Requirements 33.3**
        """
        with pytest.raises(ValidationError):
            Pagination(limit=50, offset=offset, total=0, has_more=False)


class TestSectionResponseSerialization:
    """Verify SectionResponse serializes correctly for API responses.

    **Validates: Requirements 33.1, 33.6**
    """

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_serializes_to_dict(self, response: SectionResponse):
        """Verify SectionResponse can be serialized to dict with all required keys.

        **Validates: Requirements 33.1**
        """
        data = response.model_dump()
        assert "counts" in data
        assert "groups" in data
        assert "pagination" in data
        assert "sort_keys" in data
        assert "last_updated_at" in data

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_groups_serialize_with_name_and_items(self, response: SectionResponse):
        """Verify serialized groups contain name and items keys.

        **Validates: Requirements 7.1-7.7, 33.1**
        """
        data = response.model_dump()
        for group in data["groups"]:
            assert "name" in group
            assert "items" in group
            assert isinstance(group["name"], str)
            assert isinstance(group["items"], list)

    @given(response=section_response_strategy)
    @PROPERTY_SETTINGS
    def test_section_response_pagination_serializes_correctly(self, response: SectionResponse):
        """Verify serialized pagination has all required fields.

        **Validates: Requirements 33.1, 33.3**
        """
        data = response.model_dump()
        pagination = data["pagination"]
        assert "limit" in pagination
        assert "offset" in pagination
        assert "total" in pagination
        assert "has_more" in pagination


class TestSectionCountsContract:
    """Verify SectionCounts model covers all six sections.

    **Validates: Requirements 7.1, 7.9**
    """

    REQUIRED_SECTIONS = {"signals", "plan", "execute", "communicate", "artifacts", "reflection"}

    def test_section_counts_has_all_six_sections(self):
        """Verify SectionCounts contains all six Daily Work Loop sections.

        **Validates: Requirements 7.1**
        """
        counts = SectionCounts()
        data = counts.model_dump()
        for section in self.REQUIRED_SECTIONS:
            assert section in data, f"Missing section: {section}"

    @given(
        signals_total=st.integers(min_value=0, max_value=1000),
        plan_total=st.integers(min_value=0, max_value=1000),
        execute_total=st.integers(min_value=0, max_value=1000),
        communicate_total=st.integers(min_value=0, max_value=1000),
        artifacts_total=st.integers(min_value=0, max_value=1000),
        reflection_total=st.integers(min_value=0, max_value=1000),
    )
    @PROPERTY_SETTINGS
    def test_section_counts_accepts_valid_totals(
        self, signals_total, plan_total, execute_total,
        communicate_total, artifacts_total, reflection_total,
    ):
        """Verify SectionCounts accepts any non-negative totals for all sections.

        **Validates: Requirements 7.9**
        """
        counts = SectionCounts(
            signals=SignalsCounts(total=signals_total),
            plan=PlanCounts(total=plan_total),
            execute=ExecuteCounts(total=execute_total),
            communicate=CommunicateCounts(total=communicate_total),
            artifacts=ArtifactsCounts(total=artifacts_total),
            reflection=ReflectionCounts(total=reflection_total),
        )
        assert counts.signals.total == signals_total
        assert counts.plan.total == plan_total
        assert counts.execute.total == execute_total
        assert counts.communicate.total == communicate_total
        assert counts.artifacts.total == artifacts_total
        assert counts.reflection.total == reflection_total

    @given(
        pending=st.integers(min_value=0, max_value=500),
        overdue=st.integers(min_value=0, max_value=500),
        in_discussion=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_signals_counts_sub_categories(self, pending, overdue, in_discussion):
        """Verify SignalsCounts tracks pending, overdue, in_discussion sub-categories.

        **Validates: Requirements 7.2**
        """
        total = pending + overdue + in_discussion
        counts = SignalsCounts(
            total=total, pending=pending, overdue=overdue, in_discussion=in_discussion
        )
        assert counts.pending == pending
        assert counts.overdue == overdue
        assert counts.in_discussion == in_discussion

    @given(
        today=st.integers(min_value=0, max_value=500),
        upcoming=st.integers(min_value=0, max_value=500),
        blocked=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_plan_counts_sub_categories(self, today, upcoming, blocked):
        """Verify PlanCounts tracks today, upcoming, blocked sub-categories.

        **Validates: Requirements 7.3**
        """
        total = today + upcoming + blocked
        counts = PlanCounts(total=total, today=today, upcoming=upcoming, blocked=blocked)
        assert counts.today == today
        assert counts.upcoming == upcoming
        assert counts.blocked == blocked

    @given(
        draft=st.integers(min_value=0, max_value=500),
        wip=st.integers(min_value=0, max_value=500),
        blocked=st.integers(min_value=0, max_value=500),
        completed=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_execute_counts_sub_categories(self, draft, wip, blocked, completed):
        """Verify ExecuteCounts tracks draft, wip, blocked, completed sub-categories.

        **Validates: Requirements 7.4**
        """
        total = draft + wip + blocked + completed
        counts = ExecuteCounts(
            total=total, draft=draft, wip=wip, blocked=blocked, completed=completed
        )
        assert counts.draft == draft
        assert counts.wip == wip
        assert counts.blocked == blocked
        assert counts.completed == completed

    @given(
        pending_reply=st.integers(min_value=0, max_value=500),
        ai_draft=st.integers(min_value=0, max_value=500),
        follow_up=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_communicate_counts_sub_categories(self, pending_reply, ai_draft, follow_up):
        """Verify CommunicateCounts tracks pending_reply, ai_draft, follow_up sub-categories.

        **Validates: Requirements 7.5**
        """
        total = pending_reply + ai_draft + follow_up
        counts = CommunicateCounts(
            total=total, pending_reply=pending_reply, ai_draft=ai_draft, follow_up=follow_up
        )
        assert counts.pending_reply == pending_reply
        assert counts.ai_draft == ai_draft
        assert counts.follow_up == follow_up

    @given(
        plan=st.integers(min_value=0, max_value=500),
        report=st.integers(min_value=0, max_value=500),
        doc=st.integers(min_value=0, max_value=500),
        decision=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_artifacts_counts_sub_categories(self, plan, report, doc, decision):
        """Verify ArtifactsCounts tracks plan, report, doc, decision sub-categories.

        **Validates: Requirements 7.6**
        """
        total = plan + report + doc + decision
        counts = ArtifactsCounts(
            total=total, plan=plan, report=report, doc=doc, decision=decision
        )
        assert counts.plan == plan
        assert counts.report == report
        assert counts.doc == doc
        assert counts.decision == decision

    @given(
        daily_recap=st.integers(min_value=0, max_value=500),
        weekly_summary=st.integers(min_value=0, max_value=500),
        lessons_learned=st.integers(min_value=0, max_value=500),
    )
    @PROPERTY_SETTINGS
    def test_reflection_counts_sub_categories(self, daily_recap, weekly_summary, lessons_learned):
        """Verify ReflectionCounts tracks daily_recap, weekly_summary, lessons_learned sub-categories.

        **Validates: Requirements 7.7**
        """
        total = daily_recap + weekly_summary + lessons_learned
        counts = ReflectionCounts(
            total=total, daily_recap=daily_recap,
            weekly_summary=weekly_summary, lessons_learned=lessons_learned,
        )
        assert counts.daily_recap == daily_recap
        assert counts.weekly_summary == weekly_summary
        assert counts.lessons_learned == lessons_learned


class TestSectionResponseAllWorkspacesContract:
    """Verify the unified response contract works for 'all' workspace aggregation.

    **Validates: Requirements 7.8, 7.11, 7.12, 33.6**
    """

    @given(
        counts=counts_strategy,
        groups=st.lists(section_group_strategy, min_size=0, max_size=10),
        pagination=pagination_strategy,
        sort_keys=sort_key_strategy,
        last_updated_at=last_updated_strategy,
    )
    @PROPERTY_SETTINGS
    def test_aggregated_response_uses_same_contract(
        self, counts, groups, pagination, sort_keys, last_updated_at,
    ):
        """Verify 'All Workspaces' aggregation uses the same response contract.

        **Validates: Requirements 7.8, 33.6**
        """
        response = SectionResponse[dict](
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=sort_keys,
            last_updated_at=last_updated_at,
        )
        data = response.model_dump()
        assert "counts" in data
        assert "groups" in data
        assert "pagination" in data
        assert "sort_keys" in data
        assert "last_updated_at" in data
        assert data["counts"]["total"] >= 0
