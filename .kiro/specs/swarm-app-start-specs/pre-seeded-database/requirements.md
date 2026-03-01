# Requirements Document

## Introduction

This feature eliminates first-launch initialization delays by pre-seeding the SQLite database during the build process. Currently, SwarmAI creates the default SwarmAgent, system skills, MCP servers, and SwarmWorkspace records at runtime on first app launch, causing 5-10 seconds of initialization time and potential "Failed to Load Agent" errors. By bundling a pre-populated database with the application, first launch becomes as fast as subsequent launches.

This feature complements the fast-startup-optimization spec by moving initialization work from runtime to build time. The pre-seeded database contains all database records that can be created ahead of time, while filesystem operations (workspace folder creation) still occur at runtime since user paths cannot be pre-created.

## Glossary

- **Seed_Database**: A pre-populated SQLite database file created during the build process containing default SwarmAgent, system skills, and MCP server records
- **Build_Script**: A Python script that generates the seed database during the build process
- **Bundled_Database**: The seed database file included in the application resources folder
- **User_Database**: The SQLite database in the user's data directory that the application uses at runtime
- **SwarmAgent**: The default system agent with ID "default" that is pre-created in the seed database
- **System_Skills**: Default skills (DOCUMENT.md, RESEARCH.md) that are pre-registered in the seed database
- **System_MCPs**: Default MCP servers (Filesystem) that are pre-registered in the seed database
- **SwarmWorkspace**: The default workspace record stored in the app data directory (`{app_data_dir}/swarm-workspaces/SwarmWS`); database entry is pre-seeded with a placeholder path, filesystem folders created at runtime

## Requirements

### Requirement 1: Build-Time Database Generation

**User Story:** As a developer, I want the build process to generate a pre-seeded database, so that the application ships with default resources already configured.

#### Acceptance Criteria

1. THE Build_Script SHALL create a seed database file at `desktop/resources/seed.db`
2. WHEN the build script runs, THE Build_Script SHALL initialize the database schema using the same schema as the runtime database
3. WHEN the build script runs, THE Build_Script SHALL insert the SwarmAgent record with ID "default" and is_system_agent=true
4. WHEN the build script runs, THE Build_Script SHALL insert system skill records for DOCUMENT.md and RESEARCH.md with is_system=true
5. WHEN the build script runs, THE Build_Script SHALL insert the Filesystem MCP server record with is_system=true
6. WHEN the build script runs, THE Build_Script SHALL insert an app_settings record with initialization_complete=true
7. WHEN the build script runs, THE Build_Script SHALL insert a SwarmWorkspace record with is_default=true and file_path using `{app_data_dir}/swarm-workspaces/SwarmWS` placeholder (expanded at runtime)
8. THE Build_Script SHALL be idempotent, producing identical output when run multiple times with the same inputs

### Requirement 2: Database Bundling

**User Story:** As a developer, I want the seed database bundled with the application, so that it is available on first launch.

#### Acceptance Criteria

1. THE Tauri build process SHALL include `desktop/resources/seed.db` in the application bundle
2. WHEN the application is installed, THE seed database SHALL be accessible from the resources folder
3. THE bundled seed database SHALL NOT be modified by the application at runtime

### Requirement 3: First-Launch Database Initialization

**User Story:** As a user, I want the application to use the pre-seeded database on first launch, so that startup is fast.

#### Acceptance Criteria

1. WHEN the application starts and no user database exists, THE Application SHALL copy the bundled seed database to the user data directory
2. WHEN copying the seed database, THE Application SHALL preserve all pre-seeded records (agent, skills, MCPs, workspace, app_settings)
3. WHEN the seed database is copied, THE Application SHALL skip full initialization since initialization_complete is already true
4. IF the bundled seed database is missing, THEN THE Application SHALL fall back to runtime initialization
5. THE first-launch database copy SHALL complete in under 500 milliseconds

### Requirement 4: Workspace Filesystem Initialization

**User Story:** As a user, I want my workspace folders created on first launch, so that I have a working environment.

#### Acceptance Criteria

1. WHEN the application starts with a pre-seeded database, THE Application SHALL check if the SwarmWorkspace filesystem folders exist
2. IF the SwarmWorkspace folders do not exist, THEN THE Application SHALL expand the `{app_data_dir}` placeholder and create the folder structure at the resolved path
3. WHEN creating workspace folders, THE Application SHALL create all standard subdirectories (Context, Docs, Projects, Tasks, ToDos, Plans, Historical-Chats, Reports)
4. WHEN creating workspace folders, THE Application SHALL create context files (overall-context.md, compressed-context.md)
5. THE workspace folder creation SHALL NOT block the application from becoming ready

### Requirement 5: Backward Compatibility

**User Story:** As an existing user, I want my current database preserved when updating the application, so that I don't lose my data.

#### Acceptance Criteria

1. WHEN the application starts and a user database already exists, THE Application SHALL NOT overwrite it with the seed database
2. WHEN the application starts with an existing database, THE Application SHALL use the existing database as-is
3. IF the existing database is missing the initialization_complete flag, THEN THE Application SHALL run the migration to add it
4. THE Application SHALL support databases created before the pre-seeded database feature was introduced

### Requirement 6: Build Integration

**User Story:** As a developer, I want the seed database generated automatically during builds, so that I don't have to remember to run it manually.

#### Acceptance Criteria

1. THE `npm run build:all` command SHALL execute the seed database generation script before building the application
2. WHEN the seed database generation fails, THE build process SHALL fail with a clear error message
3. THE seed database generation script SHALL be runnable independently via `python scripts/generate_seed_db.py`
4. THE seed database generation script SHALL log what records it creates

### Requirement 7: Data Consistency

**User Story:** As a developer, I want the seed database to match the runtime database schema, so that there are no compatibility issues.

#### Acceptance Criteria

1. THE Build_Script SHALL use the same SQLiteDatabase class and schema as the runtime application
2. THE Build_Script SHALL read default-agent.json to populate the SwarmAgent record
3. THE Build_Script SHALL read default-skills/*.md to populate system skill records
4. THE Build_Script SHALL read default-mcp-servers.json to populate MCP server records
5. IF the schema changes, THEN THE Build_Script SHALL automatically generate a compatible seed database

### Requirement 8: Seed Database Validation

**User Story:** As a developer, I want to verify the seed database is valid, so that I catch issues before release.

#### Acceptance Criteria

1. THE Build_Script SHALL validate that the SwarmAgent record exists after generation
2. THE Build_Script SHALL validate that all system skills are registered after generation
3. THE Build_Script SHALL validate that all system MCP servers are registered after generation
4. THE Build_Script SHALL validate that initialization_complete is set to true after generation
5. IF validation fails, THEN THE Build_Script SHALL exit with a non-zero status code
