# Implementation Plan: Bedrock Integration (E2E Architecture)

## Overview

Phased migration from SQLite-based settings to file-based configuration with AWS credential delegation, filesystem-based command permissions, and pre-flight credential validation. Follows the 3-phase strategy: (1) config.json support, (2) CmdPermissionManager, (3) credential cleanup + validation. Backend is Python/FastAPI, frontend is TypeScript/React.

## Tasks

- [x] 1. Phase 1 — File-Based App Configuration
  - [x] 1.1 Create `AppConfigManager` class in `backend/core/app_config_manager.py`
    - Implement `load()`, `get()`, `update()`, `reload()` methods
    - Config path: `~/.swarm-ai/config.json` with `0600` permissions
    - In-memory cache populated at startup, zero IO on reads
    - Handle missing/empty/invalid JSON by falling back to defaults (Bedrock enabled, us-east-1, `claude-sonnet-4-5-20250929`)
    - Filter out secret keys on `update()` — never write `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token`, `anthropic_api_key` to file
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 9.4_

  - [ ]* 1.2 Write property test CP-1: Config file contains no secrets
    - **Property CP-1: Config File Contains No Secrets**
    - Use hypothesis `st.dictionaries` to generate arbitrary update dicts, assert secret keys never appear in persisted file
    - Test file: `backend/tests/test_property_config_no_secrets.py`
    - **Validates: Requirements 1.1, 2.2, 9.4**

  - [ ]* 1.3 Write property test CP-11: Config update round-trip consistency
    - **Property CP-11: Config Update Round-Trip Consistency**
    - For any partial update, assert in-memory cache matches persisted file and non-updated fields are preserved
    - Test file: `backend/tests/test_property_config_roundtrip.py`
    - **Validates: Requirements 1.4, 10.2**

  - [x] 1.4 Add `get_bedrock_model_id()` and `ANTHROPIC_TO_BEDROCK_MODEL_MAP` to `backend/config.py`
    - Static dict mapping Anthropic model IDs to Bedrock cross-region inference ARNs
    - Passthrough behavior for unknown model IDs (return input unchanged)
    - Pure function with no side effects
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 1.5 Write property tests CP-2 and CP-3: Model ID mapping
    - **Property CP-2: Model ID Mapping Idempotency** — applying `get_bedrock_model_id()` to an already-mapped ARN returns it unchanged
    - **Property CP-3: Model ID Passthrough** — unknown model IDs pass through unchanged
    - Test file: `backend/tests/test_property_model_id_mapping.py`
    - **Validates: Requirements 5.1, 5.2, 5.3**

  - [x] 1.6 Add startup migration from `app_settings` DB table to `config.json`
    - In `AppConfigManager.load()`, if `config.json` doesn't exist but `app_settings` has data, copy non-credential settings to `config.json`
    - Exclude all credential fields during migration
    - _Requirements: 14.1, 14.2_

  - [x] 1.7 Update `configure_claude_environment()` in `backend/core/claude_environment.py`
    - Accept `AppConfigManager` instead of reading from DB
    - Read all values from in-memory cache (zero IO)
    - Set only `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`
    - Never set AWS credential env vars
    - Raise `AuthenticationNotConfiguredError` if no API key and Bedrock disabled
    - _Requirements: 2.3, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [ ]* 1.8 Write property test CP-8: Environment only sets non-credential vars
    - **Property CP-8: Environment Only Sets Non-Credential Vars**
    - Use hypothesis to generate config states, assert `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK` are never set by the function
    - Test file: `backend/tests/test_property_env_no_credentials.py`
    - **Validates: Requirements 2.3, 13.5**

  - [x] 1.9 Update Settings API router (`backend/routers/settings.py`) to read/write `config.json`
    - Replace DB-based `get_api_settings()` with `AppConfigManager` reads
    - GET endpoint returns `AppConfigResponse` with all non-secret config fields
    - PUT endpoint merges partial updates via `AppConfigManager.update()`
    - Validate `default_model` is in `available_models` when both provided
    - Auto-reset `default_model` when `available_models` changes and current default is not in new list
    - Clear `anthropic_base_url` to `None` when empty string provided
    - Remove credential fields from request/response Pydantic models
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 1.10 Write property test CP-12: default_model always in available_models
    - **Property CP-12: Default Model Always In Available Models**
    - Use hypothesis to generate `available_models` lists and `current_default` strings, assert invariant holds after settings update logic
    - Test file: `backend/tests/test_property_default_model_invariant.py`
    - **Validates: Requirements 10.3, 10.4**

- [x] 2. Checkpoint — Phase 1 complete
  - Ensure all tests pass (`cd backend && pytest`), ask the user if questions arise.

- [x] 3. Phase 2 — Command Permission Manager
  - [x] 3.1 Create `CmdPermissionManager` class in `backend/core/cmd_permission_manager.py`
    - Filesystem storage in `~/.swarm-ai/cmd_permissions/`
    - `approved_commands.json` and `dangerous_patterns.json` file structure
    - Implement `load()`, `is_dangerous()`, `is_approved()`, `approve()`, `reload()` methods
    - Glob matching for both dangerous patterns and approved commands
    - In-memory cache loaded at startup, zero IO on checks
    - Create directory and default files on first launch if missing
    - Reject overly broad patterns (bare `*`) in `approve()`
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 4.8, 9.5_

  - [ ]* 3.2 Write property test CP-6: Command permission shared state
    - **Property CP-6: Command Permission Shared State**
    - Approve a pattern via one manager instance, verify a separate instance (same filesystem) recognizes it
    - Test file: `backend/tests/test_property_cmd_permission_shared.py`
    - **Validates: Requirements 4.4, 4.6**

  - [ ]* 3.3 Write property test CP-7: Command permission persistence
    - **Property CP-7: Command Permission Persistence**
    - Approve a pattern, create a new `CmdPermissionManager` instance (simulating restart), verify pattern is still approved
    - Test file: `backend/tests/test_property_cmd_permission_persistence.py`
    - **Validates: Requirement 4.4**

  - [ ]* 3.4 Write property test CP-9: Dangerous pattern matching correctness
    - **Property CP-9: Dangerous Pattern Matching Correctness**
    - Use hypothesis to generate command strings, assert: dangerous + unapproved → prompt, dangerous + approved → allow, not dangerous → allow
    - Test file: `backend/tests/test_property_cmd_pattern_matching.py`
    - **Validates: Requirements 4.2, 4.3, 4.5**

  - [x] 3.5 Migrate hardcoded `DANGEROUS_PATTERNS` to `dangerous_patterns.json`
    - Move patterns from `backend/core/permission_manager.py` to `~/.swarm-ai/cmd_permissions/dangerous_patterns.json`
    - `CmdPermissionManager.load()` seeds default patterns if file doesn't exist
    - _Requirements: 14.3_

  - [x] 3.6 Replace per-session `_approved_commands` dict with `CmdPermissionManager`
    - Update `backend/core/agent_manager.py` to use `CmdPermissionManager` instead of in-memory per-session dict
    - Remove `asyncio.Queue` + forwarder pattern for permission handling
    - Wire `CmdPermissionManager` into the PreToolUse hook (`backend/core/security_hooks.py`)
    - _Requirements: 4.3, 4.5, 4.6, 14.3_

  - [x] 3.7 Rename `permission_*` to `cmd_permission_*` throughout backend
    - Rename SSE event: `permission_request` → `cmd_permission_request`
    - Rename endpoints: `/api/chat/permission-response` → `/api/chat/cmd-permission-response`, `/api/chat/permission-continue` → `/api/chat/cmd-permission-continue`
    - Update `backend/routers/chat.py` endpoint definitions and handler functions
    - Update `backend/core/agent_manager.py` SSE event type strings
    - Remove `permission_requests` DB table
    - _Requirements: 4.3, 11.3, 11.4, 11.6, 14.4_

- [x] 4. Checkpoint — Phase 2 complete
  - Ensure all tests pass (`cd backend && pytest`), ask the user if questions arise.

- [x] 5. Phase 3 — Credential Validation and DB Credential Removal
  - [x] 5.1 Create `CredentialValidator` class in `backend/core/credential_validator.py`
    - Implement `is_valid(region)`, `get_identity(region)`, `invalidate()` methods
    - STS `GetCallerIdentity` pre-flight check using `boto3`
    - 5-minute cache TTL; invalidate cache on validation failure
    - Async-compatible (use `asyncio.to_thread` for boto3 call)
    - _Requirements: 3.1, 3.3, 3.4_

  - [ ]* 5.2 Write property test CP-10: Pre-flight credential validation consistency
    - **Property CP-10: Pre-flight Credential Validation Consistency**
    - Assert: when `is_valid()` returns False, system yields `CREDENTIALS_EXPIRED` error without invoking SDK
    - Assert: cache invalidation forces immediate re-check (not stale cached True)
    - Test file: `backend/tests/test_property_credential_validation.py`
    - **Validates: Requirements 3.2, 3.4**

  - [x] 5.3 Integrate `CredentialValidator` into `AgentManager._execute_on_session()`
    - Call `credential_validator.is_valid()` when Bedrock is enabled, before creating SDK client
    - Yield `CREDENTIALS_EXPIRED` SSE error with ADA refresh instructions if invalid
    - Invalidate cache on auth errors detected via `_AUTH_PATTERNS` fallback
    - _Requirements: 3.1, 3.2, 3.5_

  - [x] 5.4 Expand `_AUTH_PATTERNS` in `backend/core/agent_manager.py`
    - Add patterns: `"expired"`, `"credential"`, `"security token"`, `"signaturedoesnotmatch"`, `"invalidclienttokenid"`, `"expiredtokenexception"`
    - Add defensive fallback: if Bedrock enabled and `is_error=True` doesn't match known patterns, include credential hint in error message
    - _Requirements: 3.5, 3.6_

  - [x] 5.5 Remove credential fields from Settings API models
    - Remove `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token` from `AppConfigRequest` and `AppConfigResponse` Pydantic models in `backend/routers/settings.py` or `backend/schemas/`
    - Add `aws_credentials_configured: bool` read-only field to `AppConfigResponse`
    - Add `anthropic_api_key_configured: bool` read-only field to `AppConfigResponse`
    - Compute credential status at GET time by probing AWS credential chain (`boto3.Session().get_credentials()`) and checking `ANTHROPIC_API_KEY` env var
    - _Requirements: 10.1, 10.6, 10.7, 14.5, 14.6_

  - [x] 5.6 Add error event sanitization in `backend/core/agent_manager.py`
    - Wrap all `yield {"type": "error", ...}` sites in `_execute_on_session()` and `_run_query_on_client()`
    - Include `detail` with traceback only when `settings.debug` is `True`
    - In production mode, omit tracebacks, file paths, line numbers, and library versions from `detail`
    - _Requirements: 9.1, 9.2_

  - [ ]* 5.7 Write property test CP-13: Error event sanitization in production mode
    - **Property CP-13: Error Event Sanitization**
    - Use hypothesis to generate error messages, assert `detail` field never contains `Traceback`, `File "`, or `.py", line` when debug is False
    - Test file: `backend/tests/test_property_error_sanitization.py`
    - **Validates: Requirement 9.1**

- [x] 6. Checkpoint — Phase 3 backend complete
  - Ensure all tests pass (`cd backend && pytest`), ask the user if questions arise.

- [x] 7. SSE Streaming and Session Management
  - [x] 7.1 Verify and update SSE event types in `backend/core/agent_manager.py`
    - Ensure `session_start`, `assistant`, `result`, `error`, `heartbeat`, `ask_user_question`, `cmd_permission_request` event types match design spec
    - Ensure `session_start` emits exactly once as first event, `result` emits exactly once as terminal event
    - Ensure heartbeat emits every 15 seconds on open connections
    - Ensure TSCC telemetry events (`agent_activity`, `tool_invocation`, `sources_updated`) are best-effort and interleaved with `assistant` events
    - Retain existing field casing for backward compatibility; use camelCase for any new fields
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 7.1, 7.2_

  - [ ]* 7.2 Write property test CP-5: SSE event ordering
    - **Property CP-5: SSE Event Ordering**
    - For any successful conversation stream, assert: first event is `session_start`, last event is `result`, mid-stream events are only `assistant`/`cmd_permission_request`/`ask_user_question`
    - Test file: `backend/tests/test_property_sse_event_ordering.py`
    - **Validates: Requirements 6.1, 6.3, 6.10**

  - [x] 7.3 Verify session lifecycle in `AgentManager`
    - Ensure new sessions create `ClaudeSDKClient`, store in `_active_sessions` with `created_at`/`last_used` timestamps
    - Ensure session resumption retrieves existing client and updates `last_used`
    - Ensure 2-hour TTL cleanup loop runs every 60 seconds
    - Ensure stale session ID falls back to fresh session transparently
    - Ensure `error_during_execution` removes session from pool
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 7.4 Write property test CP-4: Session lifecycle consistency
    - **Property CP-4: Session Lifecycle Consistency**
    - For any session in `_active_sessions`, assert: `client` and `wrapper` are non-None, `last_used >= created_at`
    - Test file: `backend/tests/test_property_session_lifecycle.py`
    - **Validates: Requirements 8.1, 8.3, 8.6**

- [x] 8. Frontend Updates
  - [x] 8.1 Update `desktop/src/services/chat.ts` with `cmd_permission` naming
    - Rename `streamPermissionContinue` → `streamCmdPermissionContinue`
    - Rename `submitPermissionDecision` → `submitCmdPermissionDecision`
    - Update endpoint URLs: `/api/chat/permission-response` → `/api/chat/cmd-permission-response`, `/api/chat/permission-continue` → `/api/chat/cmd-permission-continue`
    - Update `toCamelCase()` functions if new fields are added
    - Ensure SSE event handler ignores unknown event types gracefully
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [x] 8.2 Update `desktop/src/services/settings.ts` to remove credential fields
    - Remove credential input fields from settings request/response types
    - Add `awsCredentialsConfigured: boolean` and `anthropicApiKeyConfigured: boolean` read-only fields to response type
    - Update `toCamelCase()` mapping for new fields
    - _Requirements: 10.1, 10.6_

  - [x] 8.3 Update Settings UI to show credential status and ADA instructions
    - Replace credential input fields with read-only credential status indicators
    - Show ADA CLI refresh instructions when `awsCredentialsConfigured` is false
    - Show `ANTHROPIC_API_KEY` env var instructions when `anthropicApiKeyConfigured` is false
    - _Requirements: 2.4, 10.1, 10.7_

  - [x] 8.4 Update frontend SSE event handling for `cmd_permission_request`
    - Handle renamed `cmd_permission_request` event type (was `permission_request`)
    - Handle both camelCase and snake_case defensively for fields with known casing inconsistencies
    - _Requirements: 6.7, 7.3, 12.4_

- [x] 9. Integration Wiring and CORS
  - [x] 9.1 Wire `AppConfigManager` and `CmdPermissionManager` into application startup (`backend/main.py`)
    - Initialize `AppConfigManager` and call `load()` at startup
    - Initialize `CmdPermissionManager` and call `load()` at startup
    - Initialize `CredentialValidator`
    - Pass instances to `AgentManager`, Settings router, and Chat router
    - Ensure CORS is restricted to configured origins (localhost ports + Tauri origins)
    - _Requirements: 1.2, 4.7, 4.8, 9.3_

  - [x] 9.2 Update `AgentManager` constructor and `_resolve_model()` to use new components
    - Accept `AppConfigManager`, `CmdPermissionManager`, `CredentialValidator` as constructor params
    - Route model IDs through `get_bedrock_model_id()` when Bedrock is active in `_resolve_model()`
    - Use `AppConfigManager` in `_build_options()` for model and config reads
    - _Requirements: 5.1, 13.1_

  - [ ]* 9.3 Write integration tests for end-to-end config → env → chat flow
    - Settings update → config.json written → next chat reads from cache → verify env vars set correctly
    - Command approval flow: dangerous command → user approves → persisted → next session auto-allows
    - _Requirements: 1.4, 4.6, 13.1_

- [x] 10. Final checkpoint — All phases complete
  - Ensure all backend tests pass (`cd backend && pytest`)
  - Ensure all frontend tests pass (`cd desktop && npm test -- --run`)
  - Ask the user if questions arise.

- [x] 11. Cleanup — Strip `app_settings` DB table to `initialization_complete` only
  - [x] 11.1 Strip `app_settings` DDL in `backend/database/sqlite.py`
    - Remove all credential and config columns: `anthropic_api_key`, `anthropic_base_url`, `use_bedrock`, `bedrock_auth_type`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token`, `aws_region`, `available_models`, `default_model`
    - Keep only: `id`, `initialization_complete`, `created_at`, `updated_at`
    - Remove all `app_settings` ALTER TABLE migration blocks (columns no longer needed)
  - [x] 11.2 Update `backend/scripts/generate_seed_db.py`
    - Strip `_insert_app_settings()` to only insert `id`, `initialization_complete`, `created_at`, `updated_at`
    - Regenerate `desktop/resources/seed.db`
  - [x] 11.3 Remove `app_settings` credential columns from test fixtures
    - Update `backend/tests/test_seed_database_migrations.py` — remove credential columns from test DDL
    - Update `backend/tests/test_seed_startup_preservation.py` — remove credential columns from test DDL
    - Update `backend/tests/test_task_data_migration.py` — remove credential columns from test DDL
  - [x] 11.4 Verify all tests pass and seed.db is consistent with data.db schema
    - Run `cd backend && pytest`
    - Run `python scripts/generate_seed_db.py`
    - Delete `~/.swarm-ai/data.db` and restart to verify clean seed copy

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each phase
- Property tests (CP-1 through CP-13) validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The 3-phase approach allows incremental delivery: Phase 1 (config), Phase 2 (permissions), Phase 3 (credentials)
