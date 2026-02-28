# Implementation Plan: SwarmAI Default Agent

## Overview

This implementation plan breaks down the SwarmAI Default Agent feature into discrete coding tasks. The approach follows the existing codebase patterns and ensures incremental progress with validation at each step.

## Tasks

- [x] 1. Backend Schema and Database Changes
  - [x] 1.1 Add `is_default` field to agent Pydantic models
    - Add `is_default: bool = Field(default=False)` to `AgentConfig` in `backend/schemas/agent.py`
    - Add `is_default: bool = False` to `AgentResponse` model
    - _Requirements: 3.1, 3.2, 3.3_
  
  - [x] 1.2 Add `is_default` column to database schema
    - Add `is_default INTEGER DEFAULT 0` to agents table in `backend/database/sqlite.py`
    - _Requirements: 3.1_

- [x] 2. Create Resource Files
  - [x] 2.1 Create default agent configuration file
    - Create `desktop/resources/default-agent.json` with agent config
    - Include id, name, description, model, permissions, tool settings
    - _Requirements: 1.3, 1.4, 1.5_
  
  - [x] 2.2 Create SwarmAI system prompt template
    - Create `backend/templates/SWARMAI.md` with YAML frontmatter
    - Include Command Center concept and four principles
    - Include professional communication guidelines
    - _Requirements: 1.6, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  
  - [x] 2.3 Create Research skill file
    - Create `desktop/resources/default-skills/RESEARCH.md`
    - Include YAML frontmatter with name, description, version
    - Include research capabilities and guidelines
    - _Requirements: 6.1, 6.2_
  
  - [x] 2.4 Create Document skill file
    - Create `desktop/resources/default-skills/DOCUMENT.md`
    - Include YAML frontmatter with name, description, version
    - Include document creation and editing capabilities
    - _Requirements: 6.3, 6.4_
  
  - [x] 2.5 Create default MCP servers configuration
    - Create `desktop/resources/default-mcp-servers.json`
    - Include filesystem MCP server with stdio connection
    - _Requirements: 7.1, 7.2_

- [x] 3. Implement Default Agent Initialization
  - [x] 3.1 Create `ensure_default_agent()` function
    - Add function to `backend/core/agent_manager.py`
    - Check if default agent exists in database
    - Load config from `default-agent.json` if not exists
    - Load system prompt from `SWARMAI.md` template
    - Create default agent with `is_default=True`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  
  - [x] 3.2 Implement default skills registration
    - Load skill files from `desktop/resources/default-skills/`
    - Register skills in database with unique IDs
    - Associate skill IDs with default agent
    - _Requirements: 6.5, 6.6_
  
  - [x] 3.3 Implement default MCP server registration
    - Load MCP config from `default-mcp-servers.json`
    - Register MCP server in database
    - Associate MCP server ID with default agent
    - _Requirements: 7.3, 7.4_
  
  - [x] 3.4 Call `ensure_default_agent()` on startup
    - Add call in `backend/main.py` lifespan startup
    - Add logging for default agent initialization
    - _Requirements: 1.1, 1.2_

- [x] 4. Checkpoint - Backend Initialization
  - Ensure backend starts successfully
  - Verify default agent is created in database
  - Verify skills and MCP server are registered
  - Ask the user if questions arise

- [x] 5. Implement API Endpoints
  - [x] 5.1 Add `GET /agents/default` endpoint
    - Add endpoint to `backend/routers/agents.py`
    - Return default agent or 404 if not found
    - _Requirements: 4.1, 4.2_
  
  - [x] 5.2 Add delete protection for default agent
    - Modify delete endpoint to check `is_default` flag
    - Return validation error if attempting to delete default
    - _Requirements: 2.1_
  
  - [x] 5.3 Write unit tests for API endpoints
    - Test GET /agents/default returns correct agent
    - Test DELETE /agents/default returns 400 error
    - Test PUT /agents/default allows updates
    - _Requirements: 2.1, 2.2, 4.1, 4.2_

- [x] 6. Write property test for default agent updates
  - **Property 1: Default Agent Update Preservation**
  - **Validates: Requirements 2.2**
  - Generate random valid updates for editable fields
  - Verify updates are applied and `is_default` remains `true`

- [x] 7. Frontend Type and Service Updates
  - [x] 7.1 Add `isDefault` to TypeScript Agent interface
    - Add `isDefault: boolean` to Agent interface in `desktop/src/types/index.ts`
    - _Requirements: 3.4_
  
  - [x] 7.2 Update agents service case conversion
    - Add `is_default` to `isDefault` mapping in `toCamelCase` function
    - Add `getDefault()` method to agentsService
    - _Requirements: 3.5, 4.4_
  
  - [x] 7.3 Write property test for case conversion
    - **Property 2: API Response Case Conversion**
    - **Validates: Requirements 3.5**
    - Generate random agent responses with `is_default`
    - Verify `toCamelCase` produces correct `isDefault` value

- [x] 8. Checkpoint - API and Service Layer
  - Ensure API endpoints work correctly
  - Verify frontend service can fetch default agent
  - Ask the user if questions arise

- [x] 9. Update Chat Page
  - [x] 9.1 Implement auto-selection of default agent
    - Modify useEffect to fetch default agent when no selection
    - Fall back to default agent if localStorage selection invalid
    - _Requirements: 5.1, 5.4_
  
  - [x] 9.2 Add welcome message for default agent
    - Display SwarmAI welcome message when default agent selected
    - _Requirements: 5.2_
  
  - [x] 9.3 Ensure agent dropdown is visible
    - Verify agent selector dropdown is always visible
    - _Requirements: 5.3_

- [x] 10. Update Agents Page
  - [x] 10.1 Add "Default" badge for default agent
    - Show badge next to agent name when `isDefault` is true
    - Style badge with primary color
    - _Requirements: 2.3_
  
  - [x] 10.2 Disable delete button for default agent
    - Disable delete button when `isDefault` is true
    - Add visual indication (opacity, cursor)
    - _Requirements: 2.4_

- [x] 11. Final Checkpoint
  - Ensure all tests pass
  - Verify end-to-end flow works:
    - Fresh start creates default agent
    - Chat page auto-selects default agent
    - Agents page shows badge and disables delete
    - Default agent can be edited but not deleted
  - Ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Follow existing code patterns in the SwarmAI codebase
- CRITICAL: Always update both `toSnakeCase` AND `toCamelCase` in services when adding fields
