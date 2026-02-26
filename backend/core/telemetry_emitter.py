"""TSCC telemetry event emitter for agent execution.

This module provides the ``TelemetryEmitter`` class, which is responsible for
constructing SSE-compatible telemetry event dicts during agent execution.  Each
emitter instance is bound to a single ``thread_id`` so that every event it
produces is automatically tagged for the correct chat thread.

Key public symbols:

- ``TelemetryEmitter``  — Emits five telemetry event types as plain dicts
- ``VALID_ORIGINS``     — Allowed origin values for ``sources_updated`` events
- ``VALID_CAP_TYPES``   — Allowed capability types for ``capability_activated``

All emitted dicts use snake_case field names and follow the ``TelemetryEvent``
schema defined in ``backend/schemas/tscc.py``.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7
"""

import os
import re
from datetime import datetime, timezone


VALID_ORIGINS = frozenset({
    "Project",
    "Knowledge Base",
    "Notes",
    "Memory",
    "External MCP",
})

VALID_CAP_TYPES = frozenset({"skill", "mcp", "tool"})

# Pattern to detect {app_data_dir} references (case-insensitive)
_APP_DATA_DIR_PATTERN = re.compile(r"\{app_data_dir\}", re.IGNORECASE)


def _normalize_source_path(source_path: str) -> str:
    """Normalize a source path to workspace-relative form.

    Strips absolute path prefixes (``/``), home-directory tildes (``~``),
    and ``{app_data_dir}`` references so that the resulting path is always
    workspace-relative.
    """
    path = source_path

    # Strip {app_data_dir} and any leading separator that follows
    path = _APP_DATA_DIR_PATTERN.sub("", path)

    # Expand ~ then strip to relative
    if path.startswith("~"):
        path = os.path.expanduser(path)

    # Strip leading slashes to make relative
    path = path.lstrip("/")

    # Strip leading ./ for cleanliness
    while path.startswith("./"):
        path = path[2:]

    # Fallback: if empty after stripping, return original basename
    if not path:
        path = os.path.basename(source_path) or source_path

    return path


def _iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class TelemetryEmitter:
    """Emits TSCC telemetry events as SSE-compatible dicts.

    Used within ``AgentManager._run_query_on_client()`` to yield telemetry
    events alongside normal chat SSE events.  Each method returns a plain
    dict matching the ``TelemetryEvent`` schema.

    Parameters
    ----------
    thread_id:
        The chat thread identifier that all emitted events will be tagged with.
    """

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id

    # -- public emitters -----------------------------------------------------

    def agent_activity(self, agent_name: str, description: str) -> dict:
        """Emit when an agent begins or completes a reasoning step.

        Parameters
        ----------
        agent_name:
            Human-readable name of the agent (e.g. ``"ResearchAgent"``).
        description:
            Human-readable description of the activity.
        """
        return self._build_event(
            event_type="agent_activity",
            data={
                "agent_name": agent_name,
                "description": description,
            },
        )

    def tool_invocation(self, tool_name: str, description: str) -> dict:
        """Emit when a tool is invoked during agent execution.

        Parameters
        ----------
        tool_name:
            Name of the tool being invoked.
        description:
            Human-readable description of the invocation purpose.
        """
        return self._build_event(
            event_type="tool_invocation",
            data={
                "tool_name": tool_name,
                "description": description,
            },
        )

    def capability_activated(
        self, cap_type: str, cap_name: str, label: str
    ) -> dict:
        """Emit when a skill, MCP connector, or tool is activated.

        Parameters
        ----------
        cap_type:
            One of ``'skill'``, ``'mcp'``, or ``'tool'``.
        cap_name:
            Name of the capability.
        label:
            Human-readable label for the capability.
        """
        return self._build_event(
            event_type="capability_activated",
            data={
                "cap_type": cap_type,
                "cap_name": cap_name,
                "label": label,
            },
        )

    def sources_updated(self, source_path: str, origin: str) -> dict:
        """Emit when the agent references a new source file or material.

        The *source_path* is normalised to workspace-relative form before
        emission — absolute paths, ``~`` prefixes, and ``{app_data_dir}``
        references are stripped at the emission boundary.

        Parameters
        ----------
        source_path:
            Path to the source (will be normalised).
        origin:
            Provenance tag — one of ``'Project'``, ``'Knowledge Base'``,
            ``'Notes'``, ``'Memory'``, or ``'External MCP'``.
        """
        normalized_path = _normalize_source_path(source_path)
        return self._build_event(
            event_type="sources_updated",
            data={
                "source_path": normalized_path,
                "origin": origin,
            },
        )

    def summary_updated(self, key_summary: list[str]) -> dict:
        """Emit when the agent's working conclusion changes.

        Parameters
        ----------
        key_summary:
            Updated list of summary bullet points.
        """
        return self._build_event(
            event_type="summary_updated",
            data={
                "key_summary": key_summary,
            },
        )

    # -- internals -----------------------------------------------------------

    def _build_event(self, *, event_type: str, data: dict) -> dict:
        """Construct a telemetry event dict with standard envelope fields."""
        return {
            "type": event_type,
            "thread_id": self._thread_id,
            "timestamp": _iso_now(),
            "data": data,
        }
