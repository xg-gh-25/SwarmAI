# Implementation Plan: Pre-seeded Database

## Overview

This implementation creates a build-time database generation system that pre-populates the SQLite database with SwarmAgent, system skills, MCP servers, and workspace records. The seed database is bundled with the application and copied to the user data directory on first launch, eliminating runtime initialization delays.

## Tasks

- [ ] 1. Create seed database generator script
  - [x] 1.1 Create `backend/scripts/generate_seed_db.py` with SeedDatabaseGenerator class
    - Import SQLiteDatabase from database.sqlite
    - Implement generate() method that orchestrates all insertions
    - Implement _insert_default_agent() reading from default-agent.json
    - Implement _insert_system_skills() reading from default-skills/*.md
    - Implement _insert_system_mcps() reading from default-mcp-servers.json
    - Implement _insert_default_workspace() with SwarmWorkspace record using `{app_data_dir}/swarm-workspaces/SwarmWS` path
    - Implement _insert_app_settings() with initialization_complete=true
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 7.1, 7.2, 7.3, 7.4_

  - [x] 1.2 Implement validation in the generator script
    - Add _validate() method that checks all required records exist
    - Verify SwarmAgent with id="default" and is_system_agent=true
    - Verify system skills exist with is_system=true
    - Verify system MCPs exist with is_system=true
    - Verify app_settings has initialization_complete=true
    - Exit with code 1 if validation fails
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 1.3 Add main() entry point and CLI interface
    - Parse output path argument (default: desktop/resources/seed.db)
    - Add logging for each record created
    - Handle errors gracefully with clear messages
    - _Requirements: 6.3, 6.4_

  - [-] 1.4 Write property test for schema consistency
    - **Property 1: Schema Consistency**
    - Generate seed DB and runtime DB, compare schemas
    - **Validates: Requirements 1.2, 7.1, 7.5**

  - [ ] 1.5 Write property test for idempotency
    - **Property 2: Build Script Idempotency**
    - Run generator twice, compare outputs (excluding timestamps)
    - **Validates: Requirements 1.8**

- [x] 2. Checkpoint - Verify seed database generator works
  - Run `python backend/scripts/generate_seed_db.py` manually
  - Verify seed.db is created at desktop/resources/seed.db
  - Verify all records exist using sqlite3 CLI
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Modify application startup to use seed database
  - [x] 3.1 Add seed database copy logic to `backend/main.py`
    - Create _get_seed_database_path() to locate bundled seed.db
    - Create _ensure_database_initialized() to copy seed DB if no user DB exists
    - Call _ensure_database_initialized() before database.initialize() in lifespan
    - Log whether seed DB was copied or existing DB was used
    - _Requirements: 3.1, 3.2, 3.4_

  - [x] 3.2 Add workspace folder initialization to startup
    - Call swarm_workspace_manager.ensure_workspace_folders_exist() after DB init
    - Make folder creation non-blocking (fire and forget with asyncio.create_task)
    - _Requirements: 4.1, 4.2, 4.5_

  - [x] 3.3 Add ensure_workspace_folders_exist() to SwarmWorkspaceManager
    - Check if default workspace exists in DB
    - Expand `{app_data_dir}` placeholder to platform-specific path
    - Check if filesystem folders exist at the expanded path
    - Create folders and context files if missing
    - _Requirements: 4.2, 4.3, 4.4_

  - [ ] 3.4 Write property test for first-launch copy behavior
    - **Property 4: First-Launch Database Copy**
    - Test with no user DB → seed DB should be copied
    - **Validates: Requirements 3.1**

  - [ ] 3.5 Write property test for existing database preservation
    - **Property 9: Existing Database Preservation**
    - Test with existing user DB → should not be overwritten
    - **Validates: Requirements 5.1, 5.2**

- [x] 4. Checkpoint - Verify startup flow works
  - Delete user database, start app, verify seed DB is copied
  - Verify workspace folders are created
  - Verify app starts quickly (< 2 seconds)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Integrate with build process
  - [x] 5.1 Update `desktop/package.json` with seed generation scripts
    - Add "generate-seed-db" script that runs the Python generator
    - Add "prebuild" script that calls generate-seed-db
    - Update "build:all" to include prebuild step
    - _Requirements: 6.1, 6.2_

  - [x] 5.2 Update Tauri configuration to bundle seed.db
    - Verify `desktop/src-tauri/tauri.conf.json` includes resources/*
    - Ensure seed.db is in the resources folder
    - _Requirements: 2.1, 2.2_

  - [ ] 5.3 Write unit tests for build integration
    - Test that generate-seed-db script runs successfully
    - Test that seed.db is created in correct location
    - _Requirements: 6.3_

- [x] 6. Add backward compatibility handling
  - [x] 6.1 Ensure migrations run on copied seed database
    - Verify _run_migrations() is called after copying seed DB
    - Test with seed DB that has older schema version
    - _Requirements: 5.3, 5.4_

  - [ ] 6.2 Write unit tests for backward compatibility
    - Test app startup with pre-existing database (no init_complete flag)
    - Verify migration adds the flag
    - _Requirements: 5.3, 5.4_

- [x] 7. Final checkpoint - End-to-end verification
  - Run full build with `npm run build:all`
  - Verify seed.db is generated and bundled
  - Test fresh install scenario (delete user data, run app)
  - Test upgrade scenario (keep old user data, run app)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The seed database generator reuses existing SQLiteDatabase class to ensure schema consistency
- Workspace path uses `{app_data_dir}/swarm-workspaces/SwarmWS` placeholder, expanded at runtime to platform-specific path
- Fallback to runtime initialization ensures the app works even if seed.db is missing
- Property tests validate universal correctness properties across all inputs
