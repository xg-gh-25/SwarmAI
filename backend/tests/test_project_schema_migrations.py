"""Property-based tests for project schema migrations.

Tests the ``project_schema_migrations`` module using Hypothesis to verify
schema migration correctness and forward-compatibility invariants.

Testing methodology: Property-based testing with Hypothesis.

Key properties verified:

- **Property 10 (forward-compatibility)**: Metadata with ``schema_version``
  newer than ``CURRENT_SCHEMA_VERSION`` is returned unchanged by
  ``migrate_if_needed()`` with ``was_migrated=False``.

# Feature: swarmws-projects, Property 10: Schema migration correctness and forward compatibility
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

from core.project_schema_migrations import (
    CURRENT_SCHEMA_VERSION,
    compare_versions,
    migrate_if_needed,
)

PROPERTY_SETTINGS = settings(
    max_examples=100,
    
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _current_version_tuple() -> tuple[int, int, int]:
    """Parse CURRENT_SCHEMA_VERSION into an (int, int, int) tuple."""
    parts = CURRENT_SCHEMA_VERSION.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _future_semver() -> st.SearchStrategy[str]:
    """Generate semver strings strictly greater than CURRENT_SCHEMA_VERSION.

    Bumps at least one component (major, minor, or patch) above the current
    version while keeping values reasonable.
    """
    major, minor, patch = _current_version_tuple()

    return st.one_of(
        # Bump major
        st.integers(min_value=major + 1, max_value=major + 50).map(
            lambda m: f"{m}.0.0"
        ),
        # Same major, bump minor
        st.integers(min_value=minor + 1, max_value=minor + 50).map(
            lambda mi: f"{major}.{mi}.0"
        ),
        # Same major+minor, bump patch
        st.integers(min_value=patch + 1, max_value=patch + 50).map(
            lambda p: f"{major}.{minor}.{p}"
        ),
    )


def _valid_metadata_with_version(version_strategy: st.SearchStrategy[str]) -> st.SearchStrategy[dict]:
    """Generate a valid .project.json metadata dict using the given version strategy."""
    return st.builds(
        lambda sv, name, desc, status, tags, priority, version: {
            "id": str(uuid4()),
            "name": name,
            "description": desc,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "tags": tags,
            "priority": priority,
            "schema_version": sv,
            "version": version,
            "update_history": [
                {
                    "version": 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "created",
                    "changes": {},
                    "source": "user",
                }
            ],
        },
        sv=version_strategy,
        name=st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,29}", fullmatch=True),
        desc=st.text(max_size=100),
        status=st.sampled_from(["active", "archived", "completed"]),
        tags=st.lists(st.text(min_size=1, max_size=20), max_size=5),
        priority=st.sampled_from(["low", "medium", "high", "critical", None]),
        version=st.integers(min_value=1, max_value=100),
    )


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestSchemaVersionForwardCompatibility:
    """Property 10 (cont.): Schema Version Forward-Compatibility.

    # Feature: swarmws-projects, Property 10: Schema migration correctness and forward compatibility

    For any valid ``.project.json`` with ``schema_version`` greater than
    ``CURRENT_SCHEMA_VERSION``, ``migrate_if_needed()`` must return the data
    unchanged with ``was_migrated=False``.

    **Validates: Requirements 32.4**
    """

    @given(metadata=_valid_metadata_with_version(_future_semver()))
    @PROPERTY_SETTINGS
    def test_future_schema_version_returned_unchanged(self, metadata: dict):
        """Metadata with schema_version > CURRENT_SCHEMA_VERSION is never modified.

        **Validates: Requirements 32.4**

        Verifies that ``migrate_if_needed()`` treats future schema versions as
        forward-compatible: the data is returned as-is and ``was_migrated`` is
        ``False``.
        """
        original = copy.deepcopy(metadata)

        result, was_migrated = migrate_if_needed(metadata)

        assert was_migrated is False, (
            f"Expected was_migrated=False for future schema_version "
            f"{metadata['schema_version']}, got True"
        )
        assert result == original, (
            f"Expected metadata to be unchanged for future schema_version "
            f"{metadata['schema_version']}, but data was modified"
        )

    @given(metadata=_valid_metadata_with_version(_future_semver()))
    @PROPERTY_SETTINGS
    def test_future_schema_version_no_history_entries_added(self, metadata: dict):
        """No migration history entries are appended for future schema versions.

        **Validates: Requirements 32.4**

        Verifies that ``update_history`` is not modified when the schema version
        is newer than what the application understands.
        """
        original_history_len = len(metadata.get("update_history", []))

        result, was_migrated = migrate_if_needed(metadata)

        result_history_len = len(result.get("update_history", []))
        assert result_history_len == original_history_len, (
            f"Expected update_history length to remain {original_history_len}, "
            f"got {result_history_len} for future schema_version "
            f"{metadata['schema_version']}"
        )

    @given(metadata=_valid_metadata_with_version(_future_semver()))
    @PROPERTY_SETTINGS
    def test_future_schema_version_preserved_exactly(self, metadata: dict):
        """The schema_version field itself is never downgraded.

        **Validates: Requirements 32.4**

        Verifies that ``migrate_if_needed()`` does not overwrite
        ``schema_version`` to ``CURRENT_SCHEMA_VERSION`` when the input
        version is newer.
        """
        original_version = metadata["schema_version"]

        result, _ = migrate_if_needed(metadata)

        assert result["schema_version"] == original_version, (
            f"Expected schema_version to remain '{original_version}', "
            f"got '{result['schema_version']}' — forward-compatibility violated"
        )
        assert compare_versions(result["schema_version"], CURRENT_SCHEMA_VERSION) > 0, (
            f"Result schema_version '{result['schema_version']}' should be "
            f"greater than CURRENT_SCHEMA_VERSION '{CURRENT_SCHEMA_VERSION}'"
        )
