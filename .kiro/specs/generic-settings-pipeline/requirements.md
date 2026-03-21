# Requirements Document

## Introduction

SwarmAI's settings pipeline currently requires touching 8 files across the full vertical stack to add or remove a single config field. The Pydantic request/response models (`AppConfigRequest`, `AppConfigResponse`), the router's `_build_response` helper, the PUT handler's field-by-field extraction, and the frontend TypeScript interface all enumerate every config field individually. This creates high coupling and unnecessary boilerplate.

This feature replaces the per-field Pydantic models with a generic pass-through architecture where `config.json` (via `AppConfigManager`) is the single source of truth. The settings API becomes a thin pass-through — GET returns the config dict directly, PUT merges partial updates — without needing to enumerate every field in schema models. The frontend transforms the entire dict generically instead of mapping fields one-by-one.

## Glossary

- **Settings_API**: The FastAPI router at `/api/settings` providing GET and PUT endpoints for application configuration.
- **AppConfigManager**: The in-memory cached configuration manager backed by `SwarmWS/config.json`. Single source of truth for non-secret settings.
- **DEFAULT_CONFIG**: The dictionary of default configuration values defined in `app_config_manager.py`.
- **SECRET_KEYS**: The frozenset of credential key names (`aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_bearer_token`, `anthropic_api_key`) that must never appear in API responses or be persisted to disk.
- **Credential_Status_Fields**: Read-only computed fields (`aws_credentials_configured`, `anthropic_api_key_configured`) derived at request time by probing the AWS credential chain and `ANTHROPIC_API_KEY` env var.
- **Config_Dict**: The plain Python dictionary representing the full non-secret configuration state from AppConfigManager.
- **Settings_Client**: The frontend TypeScript service (`desktop/src/services/settings.ts`) that communicates with the Settings_API and transforms between snake_case and camelCase.
- **Claude_Environment**: The module (`claude_environment.py`) that reads config values from AppConfigManager to set process-level environment variables for the Claude Agent SDK.

## Requirements

### Requirement 1: Generic GET Endpoint

**User Story:** As a developer, I want the GET `/api/settings` endpoint to return the full config dict from AppConfigManager without enumerating fields in a Pydantic response model, so that adding new config fields requires zero changes to the API layer.

#### Acceptance Criteria

1. WHEN a GET request is received, THE Settings_API SHALL return the full Config_Dict from AppConfigManager merged with DEFAULT_CONFIG as a JSON object.
2. WHEN a GET request is received, THE Settings_API SHALL exclude all keys in SECRET_KEYS from the response.
3. WHEN a GET request is received, THE Settings_API SHALL include the Credential_Status_Fields (`aws_credentials_configured`, `anthropic_api_key_configured`) computed at request time.
4. THE Settings_API SHALL return the response with HTTP status 200 and content type `application/json`.
5. WHEN a new key is added to DEFAULT_CONFIG, THE Settings_API SHALL include that key in GET responses without any code changes to the router or schema modules.

### Requirement 2: Generic PUT Endpoint

**User Story:** As a developer, I want the PUT `/api/settings` endpoint to accept an arbitrary JSON object and merge it into the config, so that adding new config fields requires zero changes to the API layer.

#### Acceptance Criteria

1. WHEN a PUT request is received with a JSON body, THE Settings_API SHALL merge all provided key-value pairs into the existing Config_Dict via AppConfigManager.update().
2. WHEN a PUT request is received, THE Settings_API SHALL silently discard any keys NOT present in DEFAULT_CONFIG (whitelist approach) and any keys present in SECRET_KEYS from the request body before merging.
3. WHEN a PUT request is received with an empty JSON body `{}`, THE Settings_API SHALL treat the request as a no-op and return the current Config_Dict.
4. WHEN a PUT request is received, THE Settings_API SHALL return the updated Config_Dict (with Credential_Status_Fields) as the response.
5. WHEN a new key is added to DEFAULT_CONFIG, THE Settings_API SHALL accept and persist that key via PUT without any code changes to the router or schema modules.

### Requirement 3: Validation Rules Preserved

**User Story:** As a user, I want the existing validation rules for `default_model` and `available_models` to continue working, so that invalid model configurations are rejected.

#### Acceptance Criteria

1. WHEN a PUT request provides `default_model` that is not in the effective `available_models` list, THE Settings_API SHALL return HTTP 400 with a detail message containing "default_model".
2. WHEN a PUT request provides `available_models` without `default_model`, and the current `default_model` is not in the new list, THE Settings_API SHALL auto-reset `default_model` to the first model in the new `available_models` list.
3. WHEN a PUT request provides `available_models` that still contains the current `default_model`, THE Settings_API SHALL preserve the current `default_model` value.
4. WHEN a PUT request provides `anthropic_base_url` as an empty string, THE Settings_API SHALL clear the value to `null` before persisting.

### Requirement 4: Secret Filtering

**User Story:** As a security-conscious developer, I want credential fields to be excluded from all API interactions, so that secrets are never exposed or accepted through the settings pipeline.

#### Acceptance Criteria

1. THE Settings_API SHALL exclude all keys in SECRET_KEYS from GET responses.
2. THE Settings_API SHALL silently discard all keys in SECRET_KEYS from PUT request bodies.
3. FOR ALL valid Config_Dict values, filtering secrets then serializing to JSON then deserializing SHALL produce a dict with no keys from SECRET_KEYS (round-trip property).

### Requirement 5: Generic Frontend Client

**User Story:** As a frontend developer, I want the settings service to generically transform the entire config dict between snake_case and camelCase, so that adding new config fields requires zero changes to the frontend service layer.

#### Acceptance Criteria

1. WHEN the Settings_Client receives a GET response, THE Settings_Client SHALL transform all keys from snake_case to camelCase using a generic conversion function.
2. WHEN the Settings_Client sends a PUT request, THE Settings_Client SHALL accept camelCase keys and transform them to snake_case using a generic conversion function.
3. THE Settings_Client SHALL preserve the TypeScript type for Credential_Status_Fields as read-only properties.
4. WHEN a new key is added to DEFAULT_CONFIG, THE Settings_Client SHALL transform that key without any code changes to the service module.
5. FOR ALL snake_case keys, converting to camelCase then back to snake_case SHALL produce the original key (round-trip property).

### Requirement 6: Claude Environment Compatibility

**User Story:** As a backend developer, I want `claude_environment.py` to continue reading config values from AppConfigManager without changes, so that the refactor does not affect CLI subprocess environment setup.

#### Acceptance Criteria

1. THE Claude_Environment SHALL continue to read config values via `AppConfigManager.get()` calls with no dependency on Pydantic settings models.
2. WHEN `use_bedrock` is true in the Config_Dict, THE Claude_Environment SHALL set `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, and `AWS_DEFAULT_REGION` environment variables.
3. WHEN `claude_code_disable_experimental_betas` is true in the Config_Dict, THE Claude_Environment SHALL set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` to "true".
4. THE Claude_Environment SHALL have zero import dependencies on `schemas.settings`.

### Requirement 7: Pydantic Model Removal

**User Story:** As a developer, I want the per-field `AppConfigRequest` and `AppConfigResponse` Pydantic models removed, so that the settings pipeline has a single source of truth (DEFAULT_CONFIG) and no redundant field definitions.

#### Acceptance Criteria

1. WHEN the refactor is complete, THE Settings_API SHALL have zero imports from `schemas.settings` for `AppConfigRequest` or `AppConfigResponse`.
2. WHEN the refactor is complete, THE Settings_API GET endpoint SHALL not use a Pydantic `response_model` that enumerates individual config fields.
3. WHEN the refactor is complete, THE Settings_API PUT endpoint SHALL accept the request body as a plain `dict` (or equivalent generic type) instead of a typed Pydantic model.
4. THE `_build_response` helper function SHALL be replaced by a generic function that reads all keys from AppConfigManager, filters SECRET_KEYS, and injects Credential_Status_Fields.

### Requirement 8: Backward Compatibility

**User Story:** As a user, I want the API response shape to remain the same after the refactor, so that existing frontend code and tests continue to work without breaking changes.

#### Acceptance Criteria

1. THE Settings_API GET response SHALL contain the same keys and value types as the current `AppConfigResponse` model for all fields present in DEFAULT_CONFIG.
2. THE Settings_API PUT response SHALL contain the same keys and value types as the current `AppConfigResponse` model for all fields present in DEFAULT_CONFIG.
3. WHEN the frontend sends a PUT request with the current `APIConfigurationRequest` shape, THE Settings_API SHALL accept and process the request correctly.
4. THE Settings_API SHALL continue to serve the `/api/settings/open-tabs` GET and PUT endpoints without changes.

### Requirement 9: Legacy Code, Dead Code, and Test Cleanup

**User Story:** As a developer, I want all dead code, stale references, and redundant tests removed as part of this refactor, so that the codebase stays lean and there are no orphaned artifacts that confuse future contributors.

#### Acceptance Criteria

1. WHEN the refactor is complete, THE file `backend/schemas/settings.py` SHALL be deleted entirely (both `AppConfigRequest` and `AppConfigResponse` classes removed).
2. WHEN the refactor is complete, ALL imports of `AppConfigRequest` or `AppConfigResponse` across the codebase SHALL be removed (zero references).
3. WHEN the refactor is complete, THE frontend `APIConfigurationResponse` and `APIConfigurationRequest` interfaces in `services/settings.ts` SHALL be replaced by a single generic type derived from the API response (no per-field enumeration).
4. WHEN the refactor is complete, THE test file `backend/tests/test_settings_router.py` SHALL be updated to test the generic dict-based API instead of asserting against Pydantic model fields.
5. WHEN the refactor is complete, ANY test that asserts specific per-field Pydantic serialization behavior (e.g. `data["claude_code_disable_experimental_betas"] is True`) SHALL be replaced by tests that verify the generic pass-through contract (response keys match DEFAULT_CONFIG keys + Credential_Status_Fields).
6. WHEN the refactor is complete, THE `_build_response` helper in `routers/settings.py` SHALL be removed and replaced by the generic builder.
7. WHEN the refactor is complete, THE per-field `toCamelCase` mapping in `services/settings.ts` SHALL be replaced by a generic `snakeToCamel` / `camelToSnake` utility.
8. WHEN the refactor is complete, THERE SHALL be zero `# TODO`, `# FIXME`, or `# HACK` comments referencing the old per-field settings pipeline in any modified file.
