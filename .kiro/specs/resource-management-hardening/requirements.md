# Requirements Document

## Introduction

This document specifies the requirements for a systematic hardening pass on the SwarmAI resource management subsystem. The changes address 6 concrete issues discovered during an end-to-end review: CompactionGuard read-only tool exemption with higher thresholds, CompactionGuard escalation grace periods, comment accuracy fixes in ResourceMonitor, flaky test determinism, ResourceExhaustedException SSE handling, and dead code removal in the macOS memory fallback. All changes are backward-compatible and localized to the backend.

## Glossary

- **CompactionGuard**: Per-session anti-loop protection component that detects and escalates when an agent enters a compaction amnesia loop (repeating the same tool calls after context compaction).
- **EscalationLevel**: Enum representing the guard's current severity: MONITORING, SOFT_WARN, HARD_WARN, KILL.
- **Read_Only_Tool**: A tool that only reads data without modifying state (e.g., Read, Grep, ListDir, Glob, ReadFile, GrepSearch, ListDirectory, FileSearch, ReadCode, ReadMultipleFiles).
- **Write_Execute_Tool**: A tool that modifies state or executes commands (e.g., Bash, Write, Edit).
- **Grace_Window**: A configurable number of calls after an escalation event during which no further escalation occurs.
- **Consecutive_Repeat_Detector**: Layer 0 of CompactionGuard's detection strategy that fires when the same (tool_name, input_hash) pair is repeated consecutively.
- **ResourceMonitor**: Singleton module providing cached system and process resource metrics with spawn budget gating.
- **ResourceExhaustedException**: Exception raised when system resources are insufficient to spawn a new subprocess.
- **SSE_Generator**: The `message_generator()` async function in `chat.py` that yields Server-Sent Events to the frontend.
- **_MEMORY_THRESHOLD_PCT**: The 85.0% memory usage threshold used by ResourceMonitor for spawn budget and tab limit decisions.
- **macOS_Fallback**: The `_read_memory_macos_fallback()` method that parses `vm_stat` output when psutil is unavailable.

## Requirements

### Requirement 1: Read-Only Tool Classification

**User Story:** As a developer using SwarmAI, I want the CompactionGuard to tolerate longer sequences of read-only tool calls before warning, so that normal code research (reading files, grepping patterns) is not interrupted by false-positive loop detection.

#### Acceptance Criteria

1. THE CompactionGuard SHALL classify tools as read-only or write-execute based on membership in the `_READ_ONLY_TOOLS` frozenset
2. WHEN a tool name is in the `_READ_ONLY_TOOLS` set, THE CompactionGuard SHALL return `"read_only"` from `_classify_tool()`
3. WHEN a tool name is not in the `_READ_ONLY_TOOLS` set, THE CompactionGuard SHALL return `"write_execute"` from `_classify_tool()`
4. THE `_READ_ONLY_TOOLS` frozenset SHALL contain exactly: Read, Grep, ListDir, Glob, ReadFile, GrepSearch, ListDirectory, FileSearch, ReadCode, ReadMultipleFiles
5. WHEN a read-only tool is used in consecutive repeat detection, THE Consecutive_Repeat_Detector SHALL use thresholds (5, 8, 10) for SOFT_WARN, HARD_WARN, KILL respectively
6. WHEN a write-execute tool is used in consecutive repeat detection, THE Consecutive_Repeat_Detector SHALL use thresholds (3, 5, 7) for SOFT_WARN, HARD_WARN, KILL respectively
7. WHEN a new read-only tool needs to be supported, THE CompactionGuard SHALL require only adding the tool name to the `_READ_ONLY_TOOLS` frozenset with no other code changes

### Requirement 2: Escalation Grace Period

**User Story:** As a developer using SwarmAI, I want the CompactionGuard to pause between escalation levels, so that the agent has a chance to self-correct before being escalated further.

#### Acceptance Criteria

1. WHEN an escalation event occurs in `check()`, THE CompactionGuard SHALL set `_grace_calls_remaining` to `_GRACE_WINDOW` (3 calls)
2. WHILE `_grace_calls_remaining` is greater than zero, THE CompactionGuard SHALL decrement the counter on each `check()` call and return the current escalation level without further escalation
3. WHEN the grace window expires (counter reaches zero), THE CompactionGuard SHALL allow the next detector to escalate normally
4. WHEN `reset()` is called, THE CompactionGuard SHALL clear `_grace_calls_remaining` to zero
5. WHEN `reset_all()` is called, THE CompactionGuard SHALL clear all grace state including `_grace_calls_remaining` and `_grace_level`
6. WHILE the escalation level is KILL, THE CompactionGuard SHALL return KILL regardless of grace state
7. THE CompactionGuard SHALL maintain monotonically non-decreasing escalation (escalation level never decreases except via explicit reset)

### Requirement 3: Comment Accuracy in ResourceMonitor

**User Story:** As a developer maintaining SwarmAI, I want all comments and docstrings in ResourceMonitor to accurately reflect the 85% memory threshold, so that the code documentation does not mislead future contributors.

#### Acceptance Criteria

1. THE ResourceMonitor `spawn_budget()` docstring SHALL reference "85%" as the memory threshold, not "80%"
2. THE ResourceMonitor `compute_max_tabs()` docstring SHALL reference "85%" as the memory threshold, not "80%"
3. THE ResourceMonitor SHALL use `_MEMORY_THRESHOLD_PCT = 85.0` as the single source of truth for the memory threshold constant

### Requirement 4: Flaky Test Fix for test_reap_orphans_has_timeout

**User Story:** As a developer running the test suite, I want `test_reap_orphans_has_timeout` to complete reliably within the conftest timeout, so that CI runs are not flaky.

#### Acceptance Criteria

1. WHEN `test_reap_orphans_has_timeout` executes, THE test SHALL mock the `_reap_orphans` timeout to 5 seconds instead of relying on the production 30-second timeout
2. THE test SHALL complete within 10 seconds of wall-clock time
3. THE test SHALL verify that `_reap_orphans` completes even when `_reap_by_pattern` hangs indefinitely

### Requirement 5: ResourceExhaustedException SSE Handling

**User Story:** As a user of SwarmAI, I want to see a clear "resource exhausted" message when the system cannot spawn a new session, so that I know to close idle tabs instead of seeing a generic error.

#### Acceptance Criteria

1. WHEN a `ResourceExhaustedException` is raised during `run_conversation()`, THE SSE_Generator SHALL catch it before the generic `except Exception` handler
2. WHEN a `ResourceExhaustedException` is caught, THE SSE_Generator SHALL yield an SSE event with `code` set to `"RESOURCE_EXHAUSTED"`
3. WHEN a `ResourceExhaustedException` is caught, THE SSE_Generator SHALL include the exception's `message` field in the SSE event
4. WHEN a `ResourceExhaustedException` is caught, THE SSE_Generator SHALL include the exception's `suggested_action` field in the SSE event
5. WHEN a non-ResourceExhaustedException is raised, THE SSE_Generator SHALL continue to use the existing error classification chain (SESSION_BUSY, AGENT_TIMEOUT, SERVICE_UNAVAILABLE, AGENT_EXECUTION_ERROR)

### Requirement 6: Dead Code Removal in macOS Fallback

**User Story:** As a developer maintaining SwarmAI, I want dead code removed from `_read_memory_macos_fallback()`, so that the code is clean and does not contain unreachable logic that could confuse future contributors.

#### Acceptance Criteria

1. THE `_read_memory_macos_fallback()` method SHALL not contain the dead code line `available = total - used if 'total' in dir() else free + speculative`
2. THE `_read_memory_macos_fallback()` method SHALL define `total` via the `sysctl` call before any computation that references it
3. THE `_read_memory_macos_fallback()` method SHALL compute `used` and `available` only after `total` is defined
4. IF the `sysctl` call fails, THEN THE `_read_memory_macos_fallback()` method SHALL propagate the exception (existing behavior preserved)
