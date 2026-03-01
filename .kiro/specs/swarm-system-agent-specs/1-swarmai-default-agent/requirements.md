# Requirements Document

## Introduction

This document specifies the requirements for the SwarmAI Default Agent feature. The feature provides a built-in "SwarmAI" agent that auto-creates on first application launch and is immediately available on the chat page without requiring manual agent selection. This creates a seamless onboarding experience where users can start chatting immediately.

## Glossary

- **Default_Agent**: The built-in SwarmAI agent that is automatically created on first launch and cannot be deleted
- **System**: The SwarmAI application backend and frontend components
- **Chat_Page**: The main chat interface where users interact with agents
- **Agents_Page**: The agent management interface for viewing and editing agents
- **SKILL_File**: A markdown file with YAML frontmatter that defines agent capabilities
- **MCP_Server**: Model Context Protocol server that provides external tool integrations
- **is_default_Flag**: A boolean field indicating whether an agent is the protected default agent

## Requirements

### Requirement 1: Default Agent Auto-Creation

**User Story:** As a new user, I want a default agent to be automatically available when I first launch the app, so that I can start chatting immediately without configuration.

#### Acceptance Criteria

1. WHEN the backend starts for the first time, THE System SHALL check if a default agent exists in the database
2. IF no default agent exists in the database, THEN THE System SHALL create the default agent with predefined configuration
3. WHEN the default agent is created, THE System SHALL use the configuration from `desktop/resources/default-agent.json`
4. WHEN the default agent is created, THE System SHALL set `is_default` to `true`
5. WHEN the default agent is created, THE System SHALL use the Claude Opus 4.5 model
6. WHEN the default agent is created, THE System SHALL load the system prompt from `backend/templates/SWARMAI.md`

### Requirement 2: Default Agent Protection

**User Story:** As a user, I want the default agent to be protected from deletion, so that I always have a working agent available.

#### Acceptance Criteria

1. WHEN a delete request is made for the default agent, THE System SHALL reject the request with an appropriate error message
2. THE System SHALL allow updates to the default agent's editable properties (name, description, system_prompt, skill_ids, mcp_ids)
3. WHEN displaying the default agent in the UI, THE Agents_Page SHALL show a "Default" badge to indicate its protected status
4. WHEN displaying the default agent in the UI, THE Agents_Page SHALL disable the delete button for the default agent

### Requirement 3: Default Agent Schema Extension

**User Story:** As a developer, I want the agent schema to include an `is_default` field, so that the system can identify and protect the default agent.

#### Acceptance Criteria

1. THE System SHALL add an `is_default` boolean field to the agent database schema with default value `false`
2. THE System SHALL add an `is_default` field to the AgentConfig Pydantic model
3. THE System SHALL add an `is_default` field to the AgentResponse Pydantic model
4. THE System SHALL add an `isDefault` field to the TypeScript Agent interface
5. WHEN converting API responses, THE agents service SHALL map `is_default` to `isDefault` in the `toCamelCase` function

### Requirement 4: Default Agent API Endpoints

**User Story:** As a frontend developer, I want API endpoints to retrieve the default agent, so that the chat page can auto-select it.

#### Acceptance Criteria

1. THE System SHALL provide a `GET /api/agents/default` endpoint that returns the default agent
2. WHEN the default agent does not exist, THE `GET /api/agents/default` endpoint SHALL return a 404 error
3. WHEN listing agents via `GET /api/agents`, THE System SHALL include the default agent in the response
4. THE agents service SHALL provide a `getDefault()` method to fetch the default agent

### Requirement 5: Chat Page Auto-Selection

**User Story:** As a user, I want the chat page to automatically select the default agent when no agent is previously selected, so that I can start chatting immediately.

#### Acceptance Criteria

1. WHEN the Chat_Page loads and no agent is previously selected, THE System SHALL automatically select the default agent
2. WHEN the Chat_Page loads with the default agent selected, THE System SHALL display a welcome message from SwarmAI
3. THE Chat_Page SHALL display a visible agent dropdown/selector for switching between agents
4. WHEN the default agent is auto-selected, THE System SHALL persist the selection to localStorage

### Requirement 6: Pre-configured Default Skills

**User Story:** As a user, I want the default agent to come with useful pre-configured skills, so that I can perform common tasks immediately.

#### Acceptance Criteria

1. THE System SHALL include a Research skill file at `desktop/resources/default-skills/RESEARCH.md`
2. THE Research skill SHALL provide capabilities for deep research with citations and analysis
3. THE System SHALL include a Document skill file at `desktop/resources/default-skills/DOCUMENT.md`
4. THE Document skill SHALL provide capabilities for document creation and editing
5. WHEN the default agent is created, THE System SHALL register the default skills in the database
6. WHEN the default agent is created, THE System SHALL associate the default skill IDs with the agent

### Requirement 7: Pre-configured MCP Server

**User Story:** As a user, I want the default agent to have file system access via MCP, so that I can work with files immediately.

#### Acceptance Criteria

1. THE System SHALL include a filesystem MCP server configuration at `desktop/resources/default-mcp-servers.json`
2. THE filesystem MCP server SHALL provide file read, write, and directory operations
3. WHEN the default agent is created, THE System SHALL register the default MCP server in the database
4. WHEN the default agent is created, THE System SHALL associate the default MCP server ID with the agent

### Requirement 8: SwarmAI System Prompt Template

**User Story:** As a user, I want the default agent to have a professional system prompt that embodies the SwarmAI product vision, so that interactions feel cohesive with the product experience.

#### Acceptance Criteria

1. THE System SHALL create a `backend/templates/SWARMAI.md` template file
2. THE SWARMAI template SHALL embody the "Command Center for Your AI Team" concept
3. THE SWARMAI template SHALL incorporate the four principles: You supervise, Agents execute, Memory persists, Work compounds
4. THE SWARMAI template SHALL use a professional yet approachable communication style
5. THE SWARMAI template SHALL be action-oriented and proactive
6. THE SWARMAI template SHALL be transparent about actions taken
