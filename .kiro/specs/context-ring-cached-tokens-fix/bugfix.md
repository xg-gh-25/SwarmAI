# Bugfix Requirements Document

## Introduction

The context usage ring (below the chat input) shows near-0% even during heavy sessions because the previous bugfix (`context-usage-ring-fix`) only reads `usage.input_tokens` from the SDK's `ResultMessage`. With Anthropic's prompt caching enabled, `input_tokens` reflects only non-cached tokens (often single digits like 3 or 97), while the bulk of context consumption lives in `cache_read_input_tokens` and `cache_creation_input_tokens`. The total context window usage should be the sum of all three fields. A secondary issue is that the `model` field is never populated in the `result` SSE event, so `_get_model_context_window(None)` always falls back to the default 200K window regardless of the actual model in use.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the SDK returns a `ResultMessage` with prompt caching active (e.g., `input_tokens: 3`, `cache_read_input_tokens: 98,599`, `cache_creation_input_tokens: 948`) THEN the system computes context usage using only `input_tokens` (3 tokens), resulting in ~0% displayed in the ring instead of the actual ~50%

1.2 WHEN the SDK returns a `ResultMessage` with large cached token counts (e.g., `cache_read_input_tokens: 661,568`, `cache_creation_input_tokens: 66,889`) but small `input_tokens` (e.g., 11,337) THEN the system reports ~6% usage instead of the actual ~370% (over-window), giving the user no warning that the context window is exhausted

1.3 WHEN `run_conversation()` captures usage data from the `result` event THEN the system only extracts `last_input_tokens = _usage.get("input_tokens")`, ignoring the `cache_read_input_tokens` and `cache_creation_input_tokens` fields that are already present in the same usage dict

1.4 WHEN `continue_with_answer()` captures usage data from the `result` event THEN the system exhibits the same defect as 1.3, only extracting `input_tokens` and ignoring cached token fields

1.5 WHEN the `result` SSE event is built in `_run_query_on_client()` THEN the event does NOT include a `model` field, so `last_model` captured in `run_conversation()` and `continue_with_answer()` is always `None`, causing `_get_model_context_window(None)` to always return the default 200K window even for models with different context windows

### Expected Behavior (Correct)

2.1 WHEN the SDK returns a `ResultMessage` with prompt caching active THEN the system SHALL compute total input tokens as `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` (treating any `None` field as 0) and use this sum for context percentage calculation

2.2 WHEN the SDK returns a `ResultMessage` with large cached token counts THEN the system SHALL report the correct context usage percentage based on the total of all three token fields, providing accurate warnings when the context window is approaching or exceeding capacity

2.3 WHEN `run_conversation()` captures usage data from the `result` event THEN the system SHALL extract all three token fields (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) and sum them to compute total input tokens

2.4 WHEN `continue_with_answer()` captures usage data from the `result` event THEN the system SHALL apply the same three-field summation as in `run_conversation()`

2.5 WHEN computing context usage THEN the system SHALL resolve the model from the agent config (already available as `agent_config.get("model")`) so that `_get_model_context_window()` receives the correct model identifier instead of `None`

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the context usage percentage is below 70% THEN the system SHALL CONTINUE TO report level `ok`

3.2 WHEN the context usage percentage is between 70% and 84% THEN the system SHALL CONTINUE TO report level `warn`

3.3 WHEN the context usage percentage is 85% or above THEN the system SHALL CONTINUE TO report level `critical`

3.4 WHEN no usage data is available (e.g., all three token fields are `None` or 0) THEN the system SHALL CONTINUE TO skip emitting a `context_warning` event (no false 0% readings)

3.5 WHEN the `context_warning` SSE event is emitted THEN the system SHALL CONTINUE TO use the same event shape (`type`, `level`, `pct`, `tokensEst`, `message`). Note: `tokensEst` will now reflect the total of all three token fields (not just `input_tokens`), which is the correct semantic — it represents total estimated context consumption.

3.6 WHEN the `result` SSE event is emitted THEN the system SHALL CONTINUE TO include the individual `input_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens` fields in the `usage` dict (the result event shape must not change)

3.7 WHEN context monitoring encounters an error THEN the system SHALL CONTINUE TO fail silently without breaking the response stream

3.8 WHEN multiple tabs are streaming in parallel THEN each tab's `context_warning` SHALL CONTINUE TO reflect only that tab's own session usage
