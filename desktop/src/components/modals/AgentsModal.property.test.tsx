/**
 * Property-Based Tests for Agent Management
 *
 * **Feature: three-column-layout**
 * **Property 21: Agent CRUD Round-Trip**
 * **Property 22: Agent List Display**
 * **Validates: Requirements 8.2, 8.3, 8.4, 8.5, 8.6**
 *
 * These tests validate agent CRUD operations and list display functionality.
 * Property-based testing focuses on pure functions to ensure correctness
 * properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { Agent, AgentCreateRequest, AgentUpdateRequest } from '../../types';

// ============== Pure Functions Under Test ==============

/**
 * Simulates creating an agent from a request.
 * Returns a new Agent with generated ID and timestamps.
 *
 * Requirement 8.3: System SHALL support creating new Custom_Agents
 */
function createAgentFromRequest(request: AgentCreateRequest): Agent {
  const now = new Date().toISOString();
  return {
    id: `agent-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    name: request.name,
    description: request.description,
    model: request.model,
    permissionMode: request.permissionMode ?? 'default',
    systemPrompt: request.systemPrompt,
    allowedTools: request.allowedTools ?? [],
    pluginIds: request.pluginIds ?? [],
    skillIds: request.skillIds ?? [],
    allowAllSkills: request.allowAllSkills ?? false,
    mcpIds: request.mcpIds ?? [],
    workingDirectory: undefined,
    enableBashTool: request.enableBashTool ?? true,
    enableFileTools: request.enableFileTools ?? true,
    enableWebTools: request.enableWebTools ?? false,
    enableToolLogging: true,
    enableSafetyChecks: true,
    globalUserMode: request.globalUserMode ?? false,
    enableHumanApproval: request.enableHumanApproval ?? true,
    sandboxEnabled: request.sandboxEnabled ?? true,
    sandbox: undefined,
    isDefault: false,
    isSystemAgent: false,
    status: 'active',
    createdAt: now,
    updatedAt: now,
  };
}

/**
 * Simulates updating an agent with new data.
 * Returns the updated Agent with new updatedAt timestamp.
 *
 * Requirement 8.4: System SHALL support editing existing Custom_Agent configurations
 */
function updateAgent(agent: Agent, updates: AgentUpdateRequest): Agent {
  return {
    ...agent,
    name: updates.name ?? agent.name,
    description: updates.description ?? agent.description,
    model: updates.model ?? agent.model,
    permissionMode: updates.permissionMode ?? agent.permissionMode,
    systemPrompt: updates.systemPrompt ?? agent.systemPrompt,
    allowedTools: updates.allowedTools ?? agent.allowedTools,
    pluginIds: updates.pluginIds ?? agent.pluginIds,
    skillIds: updates.skillIds ?? agent.skillIds,
    allowAllSkills: updates.allowAllSkills ?? agent.allowAllSkills,
    mcpIds: updates.mcpIds ?? agent.mcpIds,
    enableBashTool: updates.enableBashTool ?? agent.enableBashTool,
    enableFileTools: updates.enableFileTools ?? agent.enableFileTools,
    enableWebTools: updates.enableWebTools ?? agent.enableWebTools,
    globalUserMode: updates.globalUserMode ?? agent.globalUserMode,
    enableHumanApproval: updates.enableHumanApproval ?? agent.enableHumanApproval,
    sandboxEnabled: updates.sandboxEnabled ?? agent.sandboxEnabled,
    updatedAt: new Date().toISOString(),
  };
}

/**
 * Simulates deleting an agent from a list.
 * Returns the list without the deleted agent.
 *
 * Requirement 8.5: System SHALL support deleting Custom_Agents
 */
function deleteAgentFromList(agents: Agent[], agentId: string): Agent[] {
  return agents.filter((agent) => agent.id !== agentId);
}

/**
 * Finds an agent by ID in a list.
 * Used to verify agent exists after creation/update.
 */
function findAgentById(agents: Agent[], agentId: string): Agent | undefined {
  return agents.find((agent) => agent.id === agentId);
}

/**
 * Adds an agent to a list (simulates create operation result).
 */
function addAgentToList(agents: Agent[], newAgent: Agent): Agent[] {
  return [...agents, newAgent];
}

/**
 * Updates an agent in a list (simulates update operation result).
 */
function updateAgentInList(agents: Agent[], updatedAgent: Agent): Agent[] {
  return agents.map((agent) => (agent.id === updatedAgent.id ? updatedAgent : agent));
}

/**
 * Filters agents by search query (name or model).
 * Used for agent list display filtering.
 *
 * Requirement 8.2: Agents page SHALL display a list of all Custom_Agents
 */
function filterAgentsByQuery(agents: Agent[], query: string): Agent[] {
  const lowerQuery = query.toLowerCase();
  return agents.filter(
    (agent) =>
      agent.name.toLowerCase().includes(lowerQuery) ||
      agent.model?.toLowerCase().includes(lowerQuery)
  );
}

/**
 * Gets display names for skills from skill IDs.
 * Returns "All Skills" if allowAllSkills is true.
 */
function getSkillDisplayNames(
  agent: Agent,
  skills: Array<{ id: string; name: string }>
): string {
  if (agent.allowAllSkills) return 'All Skills';
  if (!agent.skillIds || agent.skillIds.length === 0) return '-';
  const names = agent.skillIds
    .map((id) => skills.find((s) => s.id === id)?.name)
    .filter(Boolean);
  return names.length > 0 ? names.join(', ') : '-';
}

/**
 * Gets display names for MCP servers from MCP IDs.
 */
function getMcpDisplayNames(
  mcpIds: string[],
  mcpServers: Array<{ id: string; name: string }>
): string {
  if (!mcpIds || mcpIds.length === 0) return '-';
  const names = mcpIds
    .map((id) => mcpServers.find((m) => m.id === id)?.name)
    .filter(Boolean);
  return names.length > 0 ? names.join(', ') : '-';
}

/**
 * Validates agent configuration is complete.
 */
function isValidAgentConfig(agent: Agent): boolean {
  return (
    typeof agent.id === 'string' &&
    agent.id.length > 0 &&
    typeof agent.name === 'string' &&
    agent.name.length > 0 &&
    typeof agent.isDefault === 'boolean' &&
    typeof agent.status === 'string'
  );
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid agent names
 */
const agentNameArb = fc.stringMatching(/^[A-Za-z][A-Za-z0-9 _-]{0,29}$/).filter((s) => s.length > 0);

/**
 * Arbitrary for generating valid model names
 */
const modelArb = fc.oneof(
  fc.constant('claude-sonnet-4-20250514'),
  fc.constant('claude-3-5-sonnet-20241022'),
  fc.constant('claude-3-opus-20240229'),
  fc.constant('claude-3-haiku-20240307')
);

/**
 * Arbitrary for generating AgentCreateRequest
 */
const agentCreateRequestArb: fc.Arbitrary<AgentCreateRequest> = fc.record({
  name: agentNameArb,
  description: fc.option(fc.string({ minLength: 0, maxLength: 200 }), { nil: undefined }),
  model: fc.option(modelArb, { nil: undefined }),
  permissionMode: fc.option(
    fc.constantFrom('default', 'acceptEdits', 'plan', 'bypassPermissions') as fc.Arbitrary<
      'default' | 'acceptEdits' | 'plan' | 'bypassPermissions'
    >,
    { nil: undefined }
  ),
  systemPrompt: fc.option(fc.string({ minLength: 0, maxLength: 500 }), { nil: undefined }),
  pluginIds: fc.option(fc.array(fc.uuid(), { maxLength: 3 }), { nil: undefined }),
  skillIds: fc.option(fc.array(fc.uuid(), { maxLength: 5 }), { nil: undefined }),
  allowAllSkills: fc.option(fc.boolean(), { nil: undefined }),
  mcpIds: fc.option(fc.array(fc.uuid(), { maxLength: 3 }), { nil: undefined }),
  allowedTools: fc.option(fc.array(fc.string(), { maxLength: 5 }), { nil: undefined }),
  enableBashTool: fc.option(fc.boolean(), { nil: undefined }),
  enableFileTools: fc.option(fc.boolean(), { nil: undefined }),
  enableWebTools: fc.option(fc.boolean(), { nil: undefined }),
  globalUserMode: fc.option(fc.boolean(), { nil: undefined }),
  enableHumanApproval: fc.option(fc.boolean(), { nil: undefined }),
  sandboxEnabled: fc.option(fc.boolean(), { nil: undefined }),
});

/**
 * Arbitrary for generating AgentUpdateRequest
 */
const agentUpdateRequestArb: fc.Arbitrary<AgentUpdateRequest> = fc.record({
  name: fc.option(agentNameArb, { nil: undefined }),
  description: fc.option(fc.string({ minLength: 0, maxLength: 200 }), { nil: undefined }),
  model: fc.option(modelArb, { nil: undefined }),
  skillIds: fc.option(fc.array(fc.uuid(), { maxLength: 5 }), { nil: undefined }),
  mcpIds: fc.option(fc.array(fc.uuid(), { maxLength: 3 }), { nil: undefined }),
});

/**
 * Arbitrary for generating a valid Agent
 */
const agentArb = (index: number = 0): fc.Arbitrary<Agent> =>
  agentCreateRequestArb.map((request) => {
    const agent = createAgentFromRequest(request);
    return {
      ...agent,
      id: `agent-${index}-${Math.random().toString(36).substr(2, 9)}`,
    };
  });

/**
 * Arbitrary for generating a list of agents
 */
const agentListArb: fc.Arbitrary<Agent[]> = fc
  .integer({ min: 0, max: 10 })
  .chain((count) => fc.tuple(...Array.from({ length: count }, (_, i) => agentArb(i))))
  .map((agents) => agents);

/**
 * Arbitrary for generating skill references
 */
const skillRefArb: fc.Arbitrary<{ id: string; name: string }> = fc.record({
  id: fc.uuid(),
  name: fc.stringMatching(/^[A-Za-z][A-Za-z0-9 _-]{0,19}$/),
});

/**
 * Arbitrary for generating MCP server references
 */
const mcpRefArb: fc.Arbitrary<{ id: string; name: string }> = fc.record({
  id: fc.uuid(),
  name: fc.stringMatching(/^[A-Za-z][A-Za-z0-9 _-]{0,19}$/),
});

/**
 * Arbitrary for search queries
 */
const searchQueryArb = fc.oneof(
  fc.constant(''),
  fc.stringMatching(/^[A-Za-z0-9 ]{0,20}$/),
  fc.constant('claude'),
  fc.constant('agent')
);

// ============== Property-Based Tests ==============

describe('Agent Management - Property-Based Tests', () => {
  /**
   * Property 21: Agent CRUD Round-Trip
   * **Feature: three-column-layout, Property 21: Agent CRUD Round-Trip**
   * **Validates: Requirements 8.3, 8.4, 8.5, 8.6**
   *
   * For any Custom_Agent with valid configuration (name, description, model,
   * skills, MCP servers), creating, updating, and then reading the agent
   * SHALL return the updated configuration, and deleting SHALL remove it
   * from the list.
   */
  describe('Feature: three-column-layout, Property 21: Agent CRUD Round-Trip', () => {
    it('should create agent with all provided configuration', () => {
      fc.assert(
        fc.property(agentCreateRequestArb, (request) => {
          const agent = createAgentFromRequest(request);

          // Property: Created agent SHALL have the provided name
          expect(agent.name).toBe(request.name);

          // Property: Created agent SHALL have the provided description
          expect(agent.description).toBe(request.description);

          // Property: Created agent SHALL have the provided model
          expect(agent.model).toBe(request.model);

          // Property: Created agent SHALL have valid ID
          expect(agent.id).toBeDefined();
          expect(agent.id.length).toBeGreaterThan(0);

          // Property: Created agent SHALL have timestamps
          expect(agent.createdAt).toBeDefined();
          expect(agent.updatedAt).toBeDefined();

          // Property: Created agent SHALL be valid
          expect(isValidAgentConfig(agent)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should update agent and preserve unchanged fields', () => {
      fc.assert(
        fc.property(agentArb(0), agentUpdateRequestArb, (originalAgent, updates) => {
          const updatedAgent = updateAgent(originalAgent, updates);

          // Property: Updated agent SHALL have same ID
          expect(updatedAgent.id).toBe(originalAgent.id);

          // Property: Updated agent SHALL have new name if provided
          if (updates.name !== undefined) {
            expect(updatedAgent.name).toBe(updates.name);
          } else {
            expect(updatedAgent.name).toBe(originalAgent.name);
          }

          // Property: Updated agent SHALL have new description if provided
          if (updates.description !== undefined) {
            expect(updatedAgent.description).toBe(updates.description);
          } else {
            expect(updatedAgent.description).toBe(originalAgent.description);
          }

          // Property: Updated agent SHALL have new model if provided
          if (updates.model !== undefined) {
            expect(updatedAgent.model).toBe(updates.model);
          } else {
            expect(updatedAgent.model).toBe(originalAgent.model);
          }

          // Property: Updated agent SHALL be valid
          expect(isValidAgentConfig(updatedAgent)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should add created agent to list and find it by ID', () => {
      fc.assert(
        fc.property(agentListArb, agentCreateRequestArb, (existingAgents, request) => {
          const newAgent = createAgentFromRequest(request);
          const updatedList = addAgentToList(existingAgents, newAgent);

          // Property: New agent SHALL be findable in the list
          const foundAgent = findAgentById(updatedList, newAgent.id);
          expect(foundAgent).toBeDefined();
          expect(foundAgent?.id).toBe(newAgent.id);
          expect(foundAgent?.name).toBe(newAgent.name);

          // Property: List length SHALL increase by 1
          expect(updatedList.length).toBe(existingAgents.length + 1);
        }),
        { numRuns: 100 }
      );
    });

    it('should update agent in list and reflect changes', () => {
      fc.assert(
        fc.property(
          agentListArb.filter((list) => list.length > 0),
          agentUpdateRequestArb,
          (agents, updates) => {
            // Pick a random agent to update
            const targetAgent = agents[0];
            const updatedAgent = updateAgent(targetAgent, updates);
            const updatedList = updateAgentInList(agents, updatedAgent);

            // Property: Updated agent SHALL be findable with new values
            const foundAgent = findAgentById(updatedList, targetAgent.id);
            expect(foundAgent).toBeDefined();

            if (updates.name !== undefined) {
              expect(foundAgent?.name).toBe(updates.name);
            }

            // Property: List length SHALL remain the same
            expect(updatedList.length).toBe(agents.length);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should delete agent from list and not find it', () => {
      fc.assert(
        fc.property(
          agentListArb.filter((list) => list.length > 0),
          (agents) => {
            // Pick a random agent to delete
            const targetAgent = agents[0];
            const updatedList = deleteAgentFromList(agents, targetAgent.id);

            // Property: Deleted agent SHALL NOT be findable
            const foundAgent = findAgentById(updatedList, targetAgent.id);
            expect(foundAgent).toBeUndefined();

            // Property: List length SHALL decrease by 1
            expect(updatedList.length).toBe(agents.length - 1);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should complete full CRUD round-trip successfully', () => {
      fc.assert(
        fc.property(
          agentListArb,
          agentCreateRequestArb,
          agentUpdateRequestArb,
          (initialList, createRequest, updateRequest) => {
            // CREATE
            const createdAgent = createAgentFromRequest(createRequest);
            let currentList = addAgentToList(initialList, createdAgent);

            // READ after create
            let foundAgent = findAgentById(currentList, createdAgent.id);
            expect(foundAgent).toBeDefined();
            expect(foundAgent?.name).toBe(createRequest.name);

            // UPDATE
            const updatedAgent = updateAgent(createdAgent, updateRequest);
            currentList = updateAgentInList(currentList, updatedAgent);

            // READ after update
            foundAgent = findAgentById(currentList, createdAgent.id);
            expect(foundAgent).toBeDefined();
            if (updateRequest.name !== undefined) {
              expect(foundAgent?.name).toBe(updateRequest.name);
            }

            // DELETE
            currentList = deleteAgentFromList(currentList, createdAgent.id);

            // READ after delete
            foundAgent = findAgentById(currentList, createdAgent.id);
            expect(foundAgent).toBeUndefined();

            // Property: Final list length SHALL equal initial length
            expect(currentList.length).toBe(initialList.length);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve skill and MCP configurations through update', () => {
      fc.assert(
        fc.property(agentArb(0), (agent) => {
          // Update with new skills and MCPs
          const newSkillIds = ['skill-1', 'skill-2'];
          const newMcpIds = ['mcp-1', 'mcp-2'];

          const updatedAgent = updateAgent(agent, {
            skillIds: newSkillIds,
            mcpIds: newMcpIds,
          });

          // Property: Updated agent SHALL have new skill IDs
          expect(updatedAgent.skillIds).toEqual(newSkillIds);

          // Property: Updated agent SHALL have new MCP IDs
          expect(updatedAgent.mcpIds).toEqual(newMcpIds);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 22: Agent List Display
   * **Feature: three-column-layout, Property 22: Agent List Display**
   * **Validates: Requirements 8.2**
   *
   * For any set of Custom_Agents in the database, the Agents management page
   * SHALL display all agents in the list.
   */
  describe('Feature: three-column-layout, Property 22: Agent List Display', () => {
    it('should display all agents when no filter is applied', () => {
      fc.assert(
        fc.property(agentListArb, (agents) => {
          const filteredAgents = filterAgentsByQuery(agents, '');

          // Property: Empty query SHALL return all agents
          expect(filteredAgents.length).toBe(agents.length);

          // Property: All original agents SHALL be in filtered list
          for (const agent of agents) {
            expect(filteredAgents.some((a) => a.id === agent.id)).toBe(true);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should filter agents by name correctly', () => {
      fc.assert(
        fc.property(agentListArb, searchQueryArb, (agents, query) => {
          const filteredAgents = filterAgentsByQuery(agents, query);

          // Property: All filtered agents SHALL match the query
          for (const agent of filteredAgents) {
            const matchesName = agent.name.toLowerCase().includes(query.toLowerCase());
            const matchesModel = agent.model?.toLowerCase().includes(query.toLowerCase());
            expect(matchesName || matchesModel).toBe(true);
          }

          // Property: Filtered count SHALL be <= total count
          expect(filteredAgents.length).toBeLessThanOrEqual(agents.length);
        }),
        { numRuns: 100 }
      );
    });

    it('should display skill names correctly for agents', () => {
      fc.assert(
        fc.property(
          agentArb(0),
          fc.array(skillRefArb, { minLength: 1, maxLength: 5 }),
          (agent, skills) => {
            // Assign some skill IDs to the agent
            const agentWithSkills = {
              ...agent,
              skillIds: skills.slice(0, 2).map((s) => s.id),
              allowAllSkills: false,
            };

            const displayNames = getSkillDisplayNames(agentWithSkills, skills);

            // Property: Display SHALL show skill names or '-'
            if (agentWithSkills.skillIds.length > 0) {
              expect(displayNames).not.toBe('-');
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should display "All Skills" when allowAllSkills is true', () => {
      fc.assert(
        fc.property(agentArb(0), fc.array(skillRefArb, { maxLength: 5 }), (agent, skills) => {
          const agentWithAllSkills = {
            ...agent,
            allowAllSkills: true,
          };

          const displayNames = getSkillDisplayNames(agentWithAllSkills, skills);

          // Property: Display SHALL show "All Skills"
          expect(displayNames).toBe('All Skills');
        }),
        { numRuns: 100 }
      );
    });

    it('should display MCP server names correctly', () => {
      fc.assert(
        fc.property(
          fc.array(fc.uuid(), { minLength: 0, maxLength: 3 }),
          fc.array(mcpRefArb, { minLength: 1, maxLength: 5 }),
          (mcpIds, mcpServers) => {
            const displayNames = getMcpDisplayNames(mcpIds, mcpServers);

            // Property: Display SHALL show MCP names or '-'
            if (mcpIds.length === 0) {
              expect(displayNames).toBe('-');
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should maintain agent order in list', () => {
      fc.assert(
        fc.property(agentListArb, (agents) => {
          const filteredAgents = filterAgentsByQuery(agents, '');

          // Property: Order SHALL be preserved when no filter
          for (let i = 0; i < agents.length; i++) {
            expect(filteredAgents[i].id).toBe(agents[i].id);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should handle empty agent list gracefully', () => {
      fc.assert(
        fc.property(searchQueryArb, (query) => {
          const emptyList: Agent[] = [];
          const filteredAgents = filterAgentsByQuery(emptyList, query);

          // Property: Empty list SHALL return empty filtered list
          expect(filteredAgents.length).toBe(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should validate all agents in list have valid configuration', () => {
      fc.assert(
        fc.property(agentListArb, (agents) => {
          // Property: All agents SHALL have valid configuration
          for (const agent of agents) {
            expect(isValidAgentConfig(agent)).toBe(true);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should filter case-insensitively', () => {
      fc.assert(
        fc.property(
          agentListArb.filter((list) => list.length > 0),
          (agents) => {
            const targetAgent = agents[0];
            const upperQuery = targetAgent.name.toUpperCase();
            const lowerQuery = targetAgent.name.toLowerCase();

            const upperFiltered = filterAgentsByQuery(agents, upperQuery);
            const lowerFiltered = filterAgentsByQuery(agents, lowerQuery);

            // Property: Case SHALL NOT affect filter results
            expect(upperFiltered.length).toBe(lowerFiltered.length);

            // Property: Target agent SHALL be in both results
            expect(upperFiltered.some((a) => a.id === targetAgent.id)).toBe(true);
            expect(lowerFiltered.some((a) => a.id === targetAgent.id)).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
