"""SSE event helper functions for the self-evolution capability.

Thin helper functions that construct typed SSE event dicts for the
evolution system.  These events are emitted to the frontend via the
existing SSE streaming infrastructure when the agent performs
self-evolution actions.

All ``data`` fields use camelCase (frontend convention).

Public symbols:

- ``evolution_start_event``        — Build event for evolution attempt start
- ``evolution_result_event``       — Build event for evolution attempt result
- ``evolution_stuck_event``        — Build event for stuck state detection
- ``evolution_help_request_event`` — Build event for help request after 3 failures
"""

from typing import Any


def evolution_start_event(
    trigger_type: str,
    description: str,
    strategy: str,
    attempt_number: int,
    principle: str | None = None,
) -> dict[str, Any]:
    """Build an evolution_start SSE event dict."""
    return {
        "event": "evolution_start",
        "data": {
            "triggerType": trigger_type,
            "description": description,
            "strategySelected": strategy,
            "attemptNumber": attempt_number,
            "principleApplied": principle,
        },
    }


def evolution_result_event(
    outcome: str,
    duration_ms: int,
    capability_created: str | None = None,
    evolution_id: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Build an evolution_result SSE event dict."""
    return {
        "event": "evolution_result",
        "data": {
            "outcome": outcome,
            "durationMs": duration_ms,
            "capabilityCreated": capability_created,
            "evolutionId": evolution_id,
            "failureReason": failure_reason,
        },
    }


def evolution_stuck_event(
    signals: list[str],
    summary: str,
    escape_strategy: str,
) -> dict[str, Any]:
    """Build an evolution_stuck_detected SSE event dict."""
    return {
        "event": "evolution_stuck_detected",
        "data": {
            "detectedSignals": signals,
            "triedSummary": summary,
            "escapeStrategy": escape_strategy,
        },
    }


def evolution_help_request_event(
    task_summary: str,
    trigger_type: str,
    attempts: list[dict],
    suggested_next_step: str,
) -> dict[str, Any]:
    """Build an evolution_help_request SSE event dict."""
    return {
        "event": "evolution_help_request",
        "data": {
            "taskSummary": task_summary,
            "triggerType": trigger_type,
            "attempts": attempts,
            "suggestedNextStep": suggested_next_step,
        },
    }
