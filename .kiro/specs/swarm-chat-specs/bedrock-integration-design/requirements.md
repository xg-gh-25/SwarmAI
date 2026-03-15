# Requirements Document

## Introduction

This document defines the requirements for the Bedrock Integration (E2E Architecture) feature of SwarmAI, a Tauri 2.0 desktop application with a React frontend and Python FastAPI backend sidecar. The system communicates with Claude models via AWS Bedrock using the Claude Agent SDK. These requirements are derived from the approved design document and cover file-based app configuration, AWS credential delegation, pre-flight credential validation, command permission management, model ID mapping, SSE streaming, session management, security, and migration from the current DB-based design.

## Glossary

- **SwarmAI**: The Tauri 2.0 desktop application comprising a React frontend and Python FastAPI backend sidecar
- **AppConfigManager**: In-memory cached configuration manager backed by `~/.swarm-ai/config.json`
- **CmdPermissionManager**: Filesystem-based command approval system for dangerous bash commands, stored in `~/.swarm-ai/cmd_permissions/`
- **CredentialValidator**: Pre-flight AWS credential validation component using STS GetCallerIdentity with 5-minute cache
- **AgentManager**: Core orchestration engine managing agent lifecycle, session reuse, option building, and response streaming
- **ClaudeSDKClient**: The Claude Agent SDK client that communicates with AWS Bedrock
- **SSE**: Server-Sent Events protocol used for streaming responses from backend to frontend
- **ADA_CLI**: Amazon internal credential management tool that writes temporary AWS credentials to `~/.ada/credentials`
- **AWS_Credential_Chain**: Standard AWS credential resolution order (env vars, `~/.aws/credentials`, `~/.ada/credentials`, config profiles, instance metadata)
- **Bedrock_ARN**: AWS Bedrock model identifier in the format `global.anthropic.<model>-v1:0`
- **ANTHROPIC_TO_BEDROCK_MODEL_MAP**: Static dictionary mapping Anthropic model IDs to Bedrock cross-region inference ARNs
- **Config_File**: The file at `~/.swarm-ai/config.json` storing non-secret application settings
- **Cmd_Permissions_Directory**: The directory at `~/.swarm-ai/cmd_permissions/` containing `approved_commands.json` and `dangerous_patterns.json`
- **Session_Pool**: In-memory dictionary of active ClaudeSDKClient sessions keyed by session ID with 2-hour TTL
- **TSCC_Events**: Optional telemetry events (`agent_activity`, `tool_invocation`, `sources_updated`) emitted for the TSCC dashboard

## Requirements

### Requirement 1: File-Based App Configuration

**User Story:** As a SwarmAI user, I want application settings stored in a local JSON file and cached in memory, so that configuration reads are fast and the config is human-editable.

#### Acceptance Criteria

1. THE AppConfigManager SHALL store non-secret application settings (Bedrock toggle, AWS region, model selection, available models, base URL, experimental betas flag) in `~/.swarm-ai/config.json`
2. WHEN the AppConfigManager starts, THE AppConfigManager SHALL load the Config_File into an in-memory cache
3. WHEN a configuration value is read during a chat request, THE AppConfigManager SHALL return the value from the in-memory cache without performing file or database IO
4. WHEN a user updates settings via the Settings API, THE AppConfigManager SHALL merge the updates into the in-memory cache and write the updated configuration to the Config_File
5. IF the Config_File is missing, empty, or contains invalid JSON, THEN THE AppConfigManager SHALL fall back to default configuration values (Bedrock enabled, us-east-1 region, default model `claude-sonnet-4-5-20250929`)
6. THE AppConfigManager SHALL create the Config_File with `0600` file permissions (owner read/write only)

### Requirement 2: AWS Credential Delegation

**User Story:** As a SwarmAI user, I want the application to use my existing AWS credentials from the standard credential chain, so that I never have to enter or store AWS secrets in the application.

#### Acceptance Criteria

1. THE SwarmAI backend SHALL delegate AWS credential resolution to the standard AWS_Credential_Chain (environment variables, `~/.aws/credentials`, `~/.ada/credentials`, AWS config profiles, instance metadata)
2. THE SwarmAI backend SHALL NOT store, read, or transmit AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) in the Config_File, SQLite database, or any application-managed file
3. WHEN configuring the Claude environment, THE `configure_claude_environment()` function SHALL set only `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, and `AWS_DEFAULT_REGION` environment variables from the cached configuration
4. WHEN AWS credentials expire during a Bedrock session, THE SwarmAI backend SHALL return an SSE error event with code `CREDENTIALS_EXPIRED` and include ADA_CLI refresh instructions in the `suggested_action` field
5. WHERE a user requires Bearer Token authentication for non-IAM auth providers, THE SwarmAI backend SHALL support `AWS_BEARER_TOKEN_BEDROCK` as an externally-set environment variable without storing the value in the Config_File or database

### Requirement 3: Pre-flight Credential Validation

**User Story:** As a SwarmAI user, I want the system to check my AWS credentials before making Bedrock API calls, so that I get immediate clear error messages instead of cryptic SDK failures.

#### Acceptance Criteria

1. WHEN Bedrock is enabled and a chat request is received, THE CredentialValidator SHALL call STS GetCallerIdentity to verify AWS credentials before creating the SDK client
2. WHEN the CredentialValidator determines credentials are invalid, THE SwarmAI backend SHALL yield an SSE error event with code `CREDENTIALS_EXPIRED` and a suggested ADA_CLI refresh command, without invoking the ClaudeSDKClient
3. WHEN the CredentialValidator successfully validates credentials, THE CredentialValidator SHALL cache the result for 5 minutes to avoid adding STS latency to subsequent chat requests
4. WHEN a credential validation check fails, THE CredentialValidator SHALL invalidate the cache so the next request re-checks immediately
5. WHEN credentials expire mid-conversation after the cache period, THE SwarmAI backend SHALL detect authentication errors via expanded `_AUTH_PATTERNS` matching on SDK error responses as a fallback mechanism
6. IF an `is_error=True` ResultMessage from the SDK does not match any known auth pattern while Bedrock is enabled, THEN THE SwarmAI backend SHALL include a credential expiration hint in the error message as a defensive fallback

### Requirement 4: Command Permission Management

**User Story:** As a SwarmAI user, I want dangerous bash commands to require my explicit approval before execution, with approvals persisted across sessions and server restarts, so that I maintain control over potentially destructive operations.

#### Acceptance Criteria

1. THE CmdPermissionManager SHALL store command approval data in `~/.swarm-ai/cmd_permissions/approved_commands.json` and dangerous patterns in `~/.swarm-ai/cmd_permissions/dangerous_patterns.json`
2. WHEN a bash command is intercepted by the PreToolUse hook, THE CmdPermissionManager SHALL check the command against `dangerous_patterns.json` using glob matching
3. WHEN a command matches a dangerous pattern and is not in the approved list, THE SwarmAI backend SHALL emit a `cmd_permission_request` SSE event to the frontend and await user approval
4. WHEN a user approves a command, THE CmdPermissionManager SHALL append the command pattern to `approved_commands.json` and update the in-memory cache
5. WHEN a command matches a dangerous pattern and a matching pattern exists in the approved list, THE CmdPermissionManager SHALL allow execution without prompting the user
6. THE CmdPermissionManager SHALL share the approved command list across all active sessions so that a pattern approved in one session is immediately available to all other sessions
7. WHEN the CmdPermissionManager starts and the Cmd_Permissions_Directory or its files do not exist, THE CmdPermissionManager SHALL create the directory and default files with built-in dangerous patterns
8. THE CmdPermissionManager SHALL load both permission files into memory at startup and perform all checks against the in-memory cache with zero file IO

### Requirement 5: Model ID Mapping

**User Story:** As a SwarmAI user, I want Anthropic model IDs to be automatically translated to Bedrock ARN format, so that I can select models by their familiar names without knowing Bedrock-specific identifiers.

#### Acceptance Criteria

1. WHEN Bedrock is enabled and a model ID exists in the ANTHROPIC_TO_BEDROCK_MODEL_MAP, THE `get_bedrock_model_id()` function SHALL return the corresponding Bedrock cross-region inference ARN
2. WHEN a model ID does not exist in the ANTHROPIC_TO_BEDROCK_MODEL_MAP, THE `get_bedrock_model_id()` function SHALL return the model ID unchanged as a passthrough for custom Bedrock ARNs
3. WHEN `get_bedrock_model_id()` is applied to an already-mapped Bedrock ARN, THE function SHALL return the ARN unchanged (idempotent behavior, no double-mapping)
4. THE `get_bedrock_model_id()` function SHALL be a pure function with no side effects

### Requirement 6: SSE Streaming Protocol

**User Story:** As a SwarmAI frontend developer, I want a well-defined SSE event protocol with consistent event types and ordering guarantees, so that I can reliably render streaming chat responses.

#### Acceptance Criteria

1. WHEN a new chat session is created, THE SwarmAI backend SHALL emit exactly one `session_start` SSE event as the first event in the stream
2. WHEN the ClaudeSDKClient streams response messages, THE SwarmAI backend SHALL emit `assistant` SSE events containing content blocks and model information
3. WHEN a conversation turn completes successfully, THE SwarmAI backend SHALL emit exactly one `result` SSE event as the terminal event, containing `session_id`, `duration_ms`, `total_cost_usd`, and `num_turns`
4. WHEN an error occurs during processing, THE SwarmAI backend SHALL emit an `error` SSE event containing `code`, `message`, and optionally `suggested_action` fields
5. WHILE an SSE connection is open, THE SwarmAI backend SHALL emit `heartbeat` events every 15 seconds to prevent proxy and load-balancer timeouts
6. WHEN the Claude agent requests user input, THE SwarmAI backend SHALL emit an `ask_user_question` SSE event containing `toolUseId` and `questions`
7. WHEN a dangerous command requires approval, THE SwarmAI backend SHALL emit a `cmd_permission_request` SSE event containing `requestId`, `sessionId`, `command`, and `toolInput`
8. WHEN TSCC_Events (`agent_activity`, `tool_invocation`, `sources_updated`) are available, THE SwarmAI backend SHALL emit them as best-effort informational events interleaved with `assistant` events
9. THE SwarmAI frontend SHALL ignore unknown SSE event types gracefully to support forward compatibility with new event types
10. FOR successful conversation streams, THE SSE event ordering SHALL follow: exactly one `session_start`, zero or more mid-stream events (`assistant`, `cmd_permission_request`, `ask_user_question`), exactly one `result`

### Requirement 7: SSE Field Casing Policy

**User Story:** As a SwarmAI developer, I want a clear casing policy for SSE event fields, so that frontend and backend teams have consistent expectations for field naming.

#### Acceptance Criteria

1. THE SwarmAI backend SHALL retain the current casing of all existing SSE event fields for backward compatibility (including the known `sessionId` vs `session_id` inconsistency between `session_start` and `result` events)
2. WHEN new SSE event types or fields are added, THE SwarmAI backend SHALL use camelCase for all new field names to match frontend conventions
3. THE SwarmAI frontend SHALL handle both camelCase and snake_case defensively for fields that have known casing inconsistencies

### Requirement 8: Session Management

**User Story:** As a SwarmAI user, I want my chat sessions to persist across multiple messages and resume efficiently, so that I can have continuous multi-turn conversations without losing context.

#### Acceptance Criteria

1. WHEN a new conversation starts without a session ID, THE AgentManager SHALL create a new ClaudeSDKClient, store it in the Session_Pool with `created_at` and `last_used` timestamps, and emit a `session_start` event with the assigned session ID
2. WHEN a conversation message includes an existing session ID, THE AgentManager SHALL retrieve the existing ClaudeSDKClient from the Session_Pool and reuse it for the query
3. WHEN a session in the Session_Pool has been idle for more than 12 hours, THE AgentManager SHALL disconnect the client wrapper and remove the session entry during background cleanup
4. THE AgentManager SHALL run a background cleanup loop every 60 seconds to remove stale sessions from the Session_Pool
5. IF a frontend sends a session ID that does not exist in the Session_Pool (due to server restart or TTL expiry), THEN THE AgentManager SHALL fall back to creating a fresh session transparently without returning an error
6. WHEN a session is resumed, THE AgentManager SHALL update the `last_used` timestamp in the Session_Pool entry
7. WHEN an SDK execution error with subtype `error_during_execution` occurs, THE AgentManager SHALL remove the session from the Session_Pool

### Requirement 9: Security and Error Sanitization

**User Story:** As a SwarmAI user, I want the application to protect my data and not leak internal implementation details in error messages, so that the application is secure by default.

#### Acceptance Criteria

1. THE SwarmAI backend SHALL NOT include Python tracebacks, file paths, line numbers, or library versions in the `detail` field of SSE error events when `settings.debug` is `False`
2. WHILE `settings.debug` is `True`, THE SwarmAI backend SHALL include full traceback information in the `detail` field of SSE error events for debugging purposes
3. THE SwarmAI backend SHALL restrict CORS to configured origins (localhost ports and Tauri origins) only
4. THE Config_File SHALL NOT contain AWS credentials, API keys, bearer tokens, or any secret values
5. WHEN validating glob patterns in `approved_commands.json`, THE CmdPermissionManager SHALL reject overly broad patterns (such as a bare `*`) that would approve all commands

### Requirement 10: Settings API

**User Story:** As a SwarmAI user, I want to configure Bedrock settings through the Settings UI, so that I can toggle Bedrock, change regions, and select models without editing files manually.

#### Acceptance Criteria

1. WHEN a GET request is made to the Settings API, THE Settings Router SHALL return an AppConfigResponse containing all non-secret configuration fields plus read-only credential status fields (`aws_credentials_configured`, `anthropic_api_key_configured`)
2. WHEN a PUT request is made to the Settings API with partial fields, THE Settings Router SHALL merge only the provided fields into the existing configuration (partial update semantics)
3. WHEN `available_models` is updated and the current `default_model` is not in the new list, THE Settings Router SHALL auto-reset `default_model` to the first model in the new `available_models` list
4. WHEN both `default_model` and `available_models` are provided in the same PUT request, THE Settings Router SHALL validate that `default_model` is contained in `available_models`
5. WHEN an empty string is provided for `anthropic_base_url`, THE Settings Router SHALL clear the value by setting it to `None`
6. THE Settings API request and response models SHALL NOT include credential fields (`aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token`)
7. WHEN computing the `aws_credentials_configured` status field, THE Settings Router SHALL probe the AWS credential chain (e.g., `boto3.Session().get_credentials()`) without exposing actual credential values

### Requirement 11: Chat API Endpoints

**User Story:** As a SwarmAI frontend developer, I want well-defined chat API endpoints with consistent naming, so that I can integrate the frontend chat service with the backend reliably.

#### Acceptance Criteria

1. THE Chat Router SHALL expose `POST /api/chat/stream` as the main SSE streaming endpoint for chat messages
2. THE Chat Router SHALL expose `POST /api/chat/answer-question` for continuing after an `ask_user_question` event
3. THE Chat Router SHALL expose `POST /api/chat/cmd-permission-continue` for continuing after a command permission decision (SSE streaming)
4. THE Chat Router SHALL expose `POST /api/chat/cmd-permission-response` for submitting a non-streaming command permission decision
5. THE Chat Router SHALL expose session management endpoints: `GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/messages`, `POST /stop/{session_id}`, `DELETE /sessions/{id}`
6. ALL command permission endpoint names SHALL use the `cmd-permission-` prefix (not `permission-`)

### Requirement 12: Frontend Chat Service

**User Story:** As a SwarmAI frontend developer, I want a TypeScript chat service that matches the backend API contract, so that the frontend can stream chat responses and manage sessions.

#### Acceptance Criteria

1. THE chatService SHALL expose `streamChat()`, `streamAnswerQuestion()`, `streamCmdPermissionContinue()`, and `submitCmdPermissionDecision()` methods matching the backend endpoint contract
2. THE chatService SHALL expose session management methods: `listSessions()`, `getSession()`, `getSessionMessages()`, `deleteSession()`, `stopSession()`
3. WHEN an SSE connection drops due to a network interruption, THE chatService SHALL invoke the `onError` callback with the error details
4. THE chatService SHALL use the `cmd_permission` naming convention (renamed from `permission`) for all command permission methods

### Requirement 13: Claude Environment Configuration

**User Story:** As a SwarmAI backend developer, I want a clear environment configuration function that sets only the minimal required env vars for the Claude SDK, so that credential management remains delegated to the AWS credential chain.

#### Acceptance Criteria

1. WHEN Bedrock is enabled in the configuration, THE `configure_claude_environment()` function SHALL set `CLAUDE_CODE_USE_BEDROCK` to `"true"`, and set `AWS_REGION` and `AWS_DEFAULT_REGION` from the cached config
2. WHEN Bedrock is disabled in the configuration, THE `configure_claude_environment()` function SHALL remove `CLAUDE_CODE_USE_BEDROCK` from the environment
3. WHEN `anthropic_base_url` is set in the configuration, THE `configure_claude_environment()` function SHALL set `ANTHROPIC_BASE_URL` in the environment
4. WHEN `claude_code_disable_experimental_betas` is true in the configuration, THE `configure_claude_environment()` function SHALL set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` to `"true"` in the environment
5. THE `configure_claude_environment()` function SHALL NOT set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, or `AWS_BEARER_TOKEN_BEDROCK` environment variables
6. IF neither `ANTHROPIC_API_KEY` is set in the environment nor Bedrock is enabled, THEN THE `configure_claude_environment()` function SHALL raise an `AuthenticationNotConfiguredError`
7. THE `configure_claude_environment()` function SHALL read all configuration values from the in-memory cache with zero file or database IO

### Requirement 14: Migration from DB-Based Design

**User Story:** As a SwarmAI developer, I want a phased migration strategy from the current SQLite-based settings to file-based configuration, so that existing user data is preserved during the transition.

#### Acceptance Criteria

1. WHEN the application starts and `config.json` does not exist but the `app_settings` database table contains data, THE migration process SHALL copy settings from the database to `config.json` (Phase 1)
2. WHEN migrating from the database, THE migration process SHALL exclude all credential fields from the new Config_File
3. WHEN Phase 2 migration runs, THE migration process SHALL move hardcoded `DANGEROUS_PATTERNS` to `dangerous_patterns.json` and replace the in-memory per-session `_approved_commands` dictionary with the shared filesystem-based CmdPermissionManager
4. WHEN Phase 2 migration runs, THE migration process SHALL rename all `permission_*` API references to `cmd_permission_*` and remove the `permission_requests` database table
5. WHEN Phase 3 migration runs, THE migration process SHALL remove credential fields (`aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token`) from the Settings API models
6. WHEN Phase 3 migration runs, THE migration process SHALL add the `aws_credentials_configured` read-only field that probes the AWS credential chain
