# Session State Machine Refactor — Design Reference

## Problem

`_execute_on_session_inner` is a ~500-line function with 4 interleaved execution paths:
- PATH A: New client creation
- PATH B: Reused client (long-lived)
- PATH A retry: Auto-retry after PATH B failure
- PATH A self-retry: Auto-retry after PATH A timeout

Each path has its own deferred-save logic, error handling, client storage, and cleanup.
When one path is modified, all four must be mentally verified. This is the root cause of
recurring regressions.

## Proposed Architecture

### Session States

```
IDLE → BUILDING_OPTIONS → CREATING_CLIENT → STREAMING → COMPLETING → STORING
                                                ↓              ↓
                                           RETRYING      ERROR_CLEANUP
                                                ↓
                                         CREATING_CLIENT (retry)
```

### State Handlers

Each state is a method that:
1. Receives the current `SessionContext` (replaces the mutable `session_context` dict)
2. Performs its work
3. Returns the next state + updated context

```python
@dataclass
class SessionContext:
    sdk_session_id: str | None
    app_session_id: str | None
    had_error: bool
    early_active_key: str | None
    client: ClaudeSDKClient | None
    wrapper: _ClaudeClientWrapper | None
    assistant_content: ContentBlockAccumulator
    retry_count: int
    is_resuming: bool
```

### Key Methods

| Method | Current Code | Responsibility |
|--------|-------------|----------------|
| `_build_session_options` | Lines 1890-1910 | Build ClaudeAgentOptions |
| `_create_client` | Lines 2073-2095 | Spawn subprocess, early registration |
| `_reuse_client` | Lines 1940-1960 | PATH B: reuse existing client |
| `_stream_query` | `_run_query_on_client` | Send query, process events |
| `_store_session` | Lines 2148-2260 | Post-stream client storage + cleanup |
| `_handle_stream_error` | Lines 2160-2200 | Error → retry or cleanup |
| `_cleanup_early_key` | Lines 2232-2255 | Early key → final key migration |

### Benefits

1. Each path is independently testable (mock one method, verify another)
2. State transitions are explicit (no flag-driven branching)
3. Retry logic is a state transition, not nested code
4. New paths (e.g., warm-up, pre-spawn) are new states, not new flags

## Migration Strategy

1. Extract `SessionContext` dataclass (replaces mutable dict)
2. Extract `_store_session` and `_cleanup_early_key` first (smallest, most bug-prone)
3. Extract `_create_client` with early registration
4. Extract `_reuse_client` (PATH B)
5. Extract retry logic as state transitions
6. Final: `_execute_on_session_inner` becomes a dispatcher that calls state handlers

Each step is independently shippable and testable. No big-bang refactor.

## Risk

- The SDK's async generator pattern (`async for event in _run_query_on_client`)
  makes pure state machine extraction harder — events must be yielded through
  the chain. Consider using an event queue pattern instead.
- The `session_context` dict is mutated by `_run_query_on_client` (sets
  `sdk_session_id`, `had_error`, etc.). The dataclass approach makes these
  mutations explicit.
