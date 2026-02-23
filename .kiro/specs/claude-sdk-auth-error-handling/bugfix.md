# Bugfix Requirements Document

## Introduction

When the Claude Agent SDK's bundled CLI has no valid authentication (no API key configured in Settings, no environment variable, and no `/login` session), the SDK returns a `ResultMessage` with `is_error=True` and `result='Not logged in · Please run /login'`. The current code in `_run_query_on_client` does not detect this as an authentication error — it only checks for `subtype == 'error_during_execution'`. The error text is yielded as a normal `assistant` message, misleading the user. The failed session is also stored in the active sessions pool, wasting resources.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the Claude SDK returns a `ResultMessage` with `is_error=True` and the result text contains an authentication error (e.g., "Not logged in · Please run /login") THEN the system yields the error text as a normal `assistant` message type instead of an `error` event

1.2 WHEN the Claude SDK returns a `ResultMessage` with `is_error=True` and `total_cost_usd=0` (indicating no API call was made due to auth failure) THEN the system stores the failed session in the `_active_sessions` pool for potential reuse

1.3 WHEN no API key is configured in the database Settings page and no `ANTHROPIC_API_KEY` environment variable is set THEN the system proceeds to create a `ClaudeSDKClient` and send a query without any pre-flight authentication validation, only discovering the auth failure after the SDK round-trip

### Expected Behavior (Correct)

2.1 WHEN the Claude SDK returns a `ResultMessage` with `is_error=True` and the result text contains an authentication error THEN the system SHALL yield an SSE event with `type: "error"` and a clear message instructing the user to configure their API key in Settings

2.2 WHEN the Claude SDK returns a `ResultMessage` with `is_error=True` indicating an authentication failure THEN the system SHALL NOT store the session in the `_active_sessions` pool and SHALL clean up the failed session

2.3 WHEN no API key is configured (neither in database Settings nor in environment variables) and Bedrock is not enabled THEN the system SHALL detect the missing authentication before creating the `ClaudeSDKClient` and SHALL yield an `error` event with a message directing the user to configure their API key in Settings

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a valid API key is configured and the Claude SDK returns a `ResultMessage` with `is_error=False` and non-empty result text THEN the system SHALL CONTINUE TO yield the result as a normal `assistant` message and persist it to the database

3.2 WHEN the Claude SDK returns a `ResultMessage` with `subtype='error_during_execution'` THEN the system SHALL CONTINUE TO handle it as an error event and clean up the broken session from the reuse pool

3.3 WHEN a valid API key is configured and a conversation completes normally THEN the system SHALL CONTINUE TO store the session in `_active_sessions` for future resume calls

3.4 WHEN Bedrock authentication is configured (with valid credentials or bearer token) instead of an Anthropic API key THEN the system SHALL CONTINUE TO allow the conversation to proceed without requiring an `ANTHROPIC_API_KEY`

3.5 WHEN the Claude SDK returns a `ResultMessage` with `is_error=True` for non-authentication errors (e.g., rate limiting, server errors) THEN the system SHALL CONTINUE TO surface those errors appropriately without misclassifying them as authentication failures
