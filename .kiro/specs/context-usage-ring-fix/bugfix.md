# Bugfix Requirements Document

## Introduction

The ContextUsageRing component (displayed below the chat input) shows incorrect or stale context usage percentages. The root cause is that `check_context_usage()` in `backend/core/context_monitor.py` reads Claude Code's `.jsonl` transcript files from `~/.claude/projects/` instead of using the SwarmAI app's own conversation data. Since Claude SDK 0.1.34+ no longer persists transcripts to disk, the function either picks up an unrelated Claude Code session's transcript or finds nothing — causing the ring to show 0% or an irrelevant percentage. Additionally, context checks only run every 5 turns (`CHECK_INTERVAL_TURNS = 5`), making the ring appear stuck between checks, even though the `result` SSE event already carries real `usage.input_tokens` from the SDK on every turn.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the backend calls `check_context_usage()` THEN the system scans `.jsonl` transcript files from `~/.claude/projects/` which belong to Claude Code, not to the SwarmAI app's own conversation sessions

1.2 WHEN Claude SDK 0.1.34+ is in use (which no longer persists `.jsonl` transcripts to disk) THEN the system either finds no transcript (returning 0% usage) or finds a stale/unrelated Claude Code transcript (returning an irrelevant percentage)

1.3 WHEN a user sends messages on turns 2, 3, 4 (i.e. turns where `turn % CHECK_INTERVAL_TURNS != 0` and `turn != 1`) THEN the system does not emit a `context_warning` event, so the ring displays the last known value and appears frozen

1.4 WHEN the `result` SSE event is emitted with accurate `usage.input_tokens` from the SDK THEN the system ignores this data for context usage calculation, relying instead on the broken `.jsonl` file-scanning approach

### Expected Behavior (Correct)

2.1 WHEN the backend computes context usage THEN the system SHALL use the `input_tokens` value from the SDK's `result` event (already available in the response pipeline) instead of scanning `.jsonl` transcript files from `~/.claude/projects/`

2.2 WHEN Claude SDK 0.1.34+ is in use THEN the system SHALL correctly report context usage percentage based on `input_tokens / model_context_window * 100`, reflecting the actual SwarmAI session's token consumption

2.3 WHEN a user sends any message (every turn) AND the SDK's `ResultMessage` carries valid `usage.input_tokens` (not None, > 0) THEN the system SHALL emit a `context_warning` event with the current usage data, so the ring updates after every turn rather than only on turns 1, 5, 10, 15... When `usage.input_tokens` is None or missing, no `context_warning` SHALL be emitted for that turn.

2.4 WHEN the `result` SSE event carries `usage.input_tokens` THEN the system SHALL use this value as the authoritative source for context window usage calculation, eliminating dependency on filesystem-based transcript scanning

2.5 WHEN the user answers a permission prompt (via `continue_with_answer()`) THEN the system SHALL apply the same SDK-based context usage computation as in `run_conversation()`, ensuring both code paths emit accurate `context_warning` events

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the context usage percentage is below 70% THEN the system SHALL CONTINUE TO report level `ok` and display a green ring

3.2 WHEN the context usage percentage is between 70% and 84% THEN the system SHALL CONTINUE TO report level `warn` and display an amber ring

3.3 WHEN the context usage percentage is 85% or above THEN the system SHALL CONTINUE TO report level `critical` and display a red ring

3.4 WHEN no usage data is available yet (e.g. before the first turn completes) THEN the system SHALL CONTINUE TO display a gray ring with `pct: null`

3.5 WHEN the `context_warning` SSE event is received by the frontend THEN the system SHALL CONTINUE TO update the `ContextWarning` state via the existing `useChatStreamingLifecycle` handler and pass `pct` to the `ContextUsageRing` component

3.6 WHEN context monitoring encounters an error THEN the system SHALL CONTINUE TO fail silently (best-effort) without breaking the response stream

3.7 WHEN multiple tabs are streaming in parallel THEN each tab's context_warning event SHALL reflect only that tab's own session usage (from its own ResultMessage.usage.input_tokens), and SHALL NOT be affected by other tabs' sessions or shared global state

---

## Bug Condition (Formal)

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type ContextCheckInput  -- represents a context usage check invocation
  OUTPUT: boolean

  // The bug triggers whenever check_context_usage() is called, because it
  // always reads from the wrong data source (~/.claude/projects/*.jsonl)
  // instead of using SDK-provided input_tokens.
  RETURN X.dataSource = "jsonl_filesystem_scan"
END FUNCTION
```

```pascal
// Property: Fix Checking — Correct data source
FOR ALL X WHERE isBugCondition(X) DO
  result ← check_context_usage'(X)
  ASSERT result.dataSource = "sdk_input_tokens"
    AND result.pct = round(X.input_tokens / X.model_context_window * 100)
END FOR
```

```pascal
// Property: Fix Checking — Every-turn emission
FOR ALL turns T in session DO
  ASSERT context_warning_emitted(T) = true
END FOR
```

```pascal
// Property: Preservation Checking — Threshold levels unchanged
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X).level = F'(X).level
    AND F(X).thresholds = F'(X).thresholds  -- ok < 70%, warn 70-84%, critical >= 85%
END FOR
```
