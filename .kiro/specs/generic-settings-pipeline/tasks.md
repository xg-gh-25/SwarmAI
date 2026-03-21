# Implementation Plan: Generic Settings Pipeline

## Overview

Replace the per-field Pydantic settings pipeline with a generic dict pass-through architecture. `DEFAULT_CONFIG` becomes the single source of truth — the API layer and frontend transform the dict generically. Implementation proceeds backend-first (router rewrite → schema deletion → test rewrite), then frontend (generic transform → consumer updates), then cleanup.

## Tasks

- [x] 1. Rewrite backend settings router to generic dict endpoints
  - [x] 1.1 Replace `_build_response` with `_build_config_response` returning a plain dict
    - Add `WRITABLE_KEYS` constant: `frozenset(DEFAULT_CONFIG.keys()) - SECRET_KEYS`
    - Implement `_build_config_response(cfg)`: iterate `DEFAULT_CONFIG`, skip `SECRET_KEYS`, inject credential status fields
    - Remove `from schemas.settings import AppConfigRequest, AppConfigResponse`
    - Add `from fastapi import Request` import
    - Update module docstring to reflect generic dict architecture
    - _Requirements: 1.1, 1.2, 1.3, 7.1, 7.4, 9.6_

  - [x] 1.2 Rewrite GET endpoint to return plain dict
    - Remove `response_model=AppConfigResponse` from `@router.get("")`
    - Return `_build_config_response(cfg)` directly
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.2_

  - [x] 1.3 Rewrite PUT endpoint to accept generic dict via `Request`
    - Change signature from `request: AppConfigRequest` to `request: Request`
    - Use `body = await request.json()` to get raw dict
    - Whitelist keys via `WRITABLE_KEYS` (replaces per-field `if request.X is not None` blocks)
    - Preserve `anthropic_base_url` empty-string → `None` clearing
    - Preserve `default_model` / `available_models` cross-validation (validate-before-persist)
    - Move auto-reset logic before `cfg.update()` so it's a single atomic update
    - Remove `response_model=AppConfigResponse` from `@router.put("")`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.2, 7.3_

- [x] 2. Delete Pydantic settings schema file
  - Delete `backend/schemas/settings.py` entirely
  - Verify zero remaining imports of `AppConfigRequest` or `AppConfigResponse` across the codebase
  - _Requirements: 7.1, 9.1, 9.2_

- [x] 3. Checkpoint — Backend compiles and existing open-tabs endpoints still work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Rewrite backend settings tests for generic dict contract
  - [x] 4.1 Rewrite `test_settings_router.py` with isolated fixture (no `from main import app`)
    - Create a standalone `TestClient` using only the settings router (not the full app)
    - Use `tmp_path`-backed `AppConfigManager` fixture with `set_config_manager()`
    - Remove `pytestmark = pytest.mark.skip` — tests must run
    - Mock `_probe_aws_credentials` and `_probe_anthropic_api_key` in all tests
    - _Requirements: 9.4, 9.5_

  - [x] 4.2 Write unit tests for generic GET contract
    - Test: response contains all `DEFAULT_CONFIG` keys minus `SECRET_KEYS` plus credential status fields
    - Test: no secret keys in response
    - Test: credential status fields reflect mocked probe values
    - Test: GET reflects values written by prior PUT
    - _Requirements: 1.1, 1.2, 1.3, 4.1, 8.1_

  - [x] 4.3 Write unit tests for generic PUT contract
    - Test: partial update of a single field preserves other defaults
    - Test: empty body `{}` is a no-op returning current config
    - Test: unknown keys in PUT body are silently discarded (not persisted)
    - Test: secret keys in PUT body are silently discarded
    - Test: `anthropic_base_url` empty string clears to `None`
    - Test: `default_model` not in `available_models` returns 400
    - Test: auto-reset `default_model` when `available_models` changes
    - Test: `default_model` preserved when still in new `available_models`
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 4.2, 8.2, 8.3_

  - [x] 4.4 Write unit test verifying new DEFAULT_CONFIG keys work without code changes
    - Monkey-patch a new key into `DEFAULT_CONFIG` and `WRITABLE_KEYS`
    - Verify GET includes the new key and PUT accepts/persists it
    - _Requirements: 1.5, 2.5_

  - [ ]* 4.5 Write property tests for backend (Properties 1–8)
    - **Property 1: GET response contains all expected keys**
    - **Validates: Requirements 1.1, 1.3, 2.4, 8.1, 8.2**
    - **Property 2: Secret keys never appear in GET responses**
    - **Validates: Requirements 1.2, 4.1**
    - **Property 3: Secret keys in PUT body are silently discarded**
    - **Validates: Requirements 2.2, 4.2**
    - **Property 4: Secret filter round-trip**
    - **Validates: Requirements 4.3**
    - **Property 5: PUT update round-trip**
    - **Validates: Requirements 2.1**
    - **Property 6: New DEFAULT_CONFIG keys appear without code changes**
    - **Validates: Requirements 1.5, 2.5, 5.4**
    - **Property 7: Invalid default_model rejected**
    - **Validates: Requirements 3.1**
    - **Property 8: default_model always in available_models after PUT**
    - **Validates: Requirements 3.2, 3.3**

- [x] 5. Checkpoint — All backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Rewrite frontend settings service with generic snake↔camel transform
  - [x] 6.1 Replace per-field interfaces and mapper with generic utilities
    - Remove `APIConfigurationResponse` interface
    - Remove `APIConfigurationRequest` interface
    - Remove `toSettingsCamelCase()` function
    - Add `snakeToCamel(s: string)` utility
    - Add `camelToSnake(s: string)` utility
    - Add `transformKeys<T>(obj, keyFn)` generic key transformer
    - Add `SettingsConfig` interface extending `Record<string, unknown>` with known typed fields and `readonly` credential status fields
    - Update `settingsService.getAPIConfiguration()` to use `transformKeys<SettingsConfig>(response.data, snakeToCamel)`
    - Update `settingsService.updateAPIConfiguration()` to accept `Record<string, unknown>`, transform keys with `camelToSnake`, return `SettingsConfig`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 9.3, 9.7_

  - [ ]* 6.2 Write property test for snake↔camel round-trip (Property 9)
    - **Property 9: snake_case ↔ camelCase round-trip**
    - **Validates: Requirements 5.5**
    - Use fast-check to generate random snake_case strings matching `[a-z][a-z0-9]*(_[a-z0-9]+)*`
    - Verify `camelToSnake(snakeToCamel(s)) === s`
    - Verify `camelToSnake` is idempotent on already-snake_case strings

- [x] 7. Update frontend consumers
  - [x] 7.1 Update `SettingsPage.tsx` imports
    - Change `import { settingsService, APIConfigurationResponse } from '../services/settings'` to `import { settingsService, SettingsConfig } from '../services/settings'`
    - Update `useState<APIConfigurationResponse | null>` to `useState<SettingsConfig | null>`
    - _Requirements: 8.3, 9.3_

- [x] 8. Verify claude_environment.py has zero dependency on schemas.settings
  - Confirm `claude_environment.py` uses only `AppConfigManager.get()` calls
  - Confirm no import of `AppConfigRequest`, `AppConfigResponse`, or `schemas.settings`
  - _Requirements: 6.1, 6.4_

- [x] 9. Final cleanup — remove dead code, stale references, and TODO/FIXME comments
  - Grep for any remaining references to `AppConfigRequest`, `AppConfigResponse`, `_build_response`, `APIConfigurationResponse`, `APIConfigurationRequest`, `toSettingsCamelCase` across the codebase
  - Remove any stale imports or dead code found
  - Remove any `# TODO`, `# FIXME`, or `# HACK` comments referencing the old per-field settings pipeline in modified files
  - _Requirements: 9.2, 9.6, 9.7, 9.8_

- [x] 10. Final checkpoint — All tests pass, full pipeline verified
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The open-tabs endpoints (`/api/settings/open-tabs`) are unchanged and require no modifications (Req 8.4)
- `claude_environment.py` and `app_config_manager.py` are unchanged by design (Req 6.1–6.4)
