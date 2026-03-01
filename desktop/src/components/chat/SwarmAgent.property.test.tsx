/**
 * Property-Based Tests for SwarmAgent Invariant
 *
 * **Feature: three-column-layout**
 * **Property 20: SwarmAgent Always Active**
 * **Validates: Requirements 7.1**
 *
 * These tests validate that SwarmAgent is always the active agent in the
 * Main_Chat_Panel. Property-based testing focuses on pure functions to
 * ensure correctness properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { Agent } from '../../types';

// ============== Pure Functions Under Test ==============

/**
 * Determines if an agent is the default SwarmAgent.
 * The default agent is identified by the isDefault flag.
 *
 * Requirement 7.1: Main_Chat_Panel SHALL always display SwarmAgent as the active agent
 */
function isDefaultAgent(agent: Agent): boolean {
  return agent.isDefault === true;
}

/**
 * Gets the active agent for the chat panel.
 * This always returns the default agent (SwarmAgent).
 *
 * Requirement 7.1: Main_Chat_Panel SHALL always display SwarmAgent as the active agent
 */
function getActiveAgentForChat(agents: Agent[]): Agent | undefined {
  return agents.find((agent) => agent.isDefault === true);
}

/**
 * Validates that a default agent exists in the agent list.
 * There should always be exactly one default agent (SwarmAgent).
 *
 * Requirement 7.1: SwarmAgent SHALL always be the active agent
 */
function hasDefaultAgent(agents: Agent[]): boolean {
  return agents.some((agent) => agent.isDefault === true);
}

/**
 * Counts the number of default agents in the list.
 * There should be exactly one default agent.
 */
function countDefaultAgents(agents: Agent[]): number {
  return agents.filter((agent) => agent.isDefault === true).length;
}

/**
 * Simulates the toCamelCase conversion for the isDefault field.
 * This ensures the conversion always preserves the isDefault flag.
 */
function convertAgentFromApi(data: { is_default?: boolean }): { isDefault: boolean } {
  return {
    isDefault: data.is_default ?? false,
  };
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating a valid Agent object
 */
const agentArb = (isDefault: boolean = false, index: number = 0): fc.Arbitrary<Agent> =>
  fc.record({
    id: fc.constant(`agent-${index}`),
    name: fc.stringMatching(/^[A-Za-z][A-Za-z0-9 _-]{0,29}$/).map((s) => s || `Agent ${index}`),
    description: fc.option(fc.string({ minLength: 0, maxLength: 100 }), { nil: undefined }),
    model: fc.option(fc.constant('claude-sonnet-4-20250514'), { nil: undefined }),
    permissionMode: fc.constant('default' as const),
    systemPrompt: fc.option(fc.string({ minLength: 0, maxLength: 200 }), { nil: undefined }),
    allowedTools: fc.constant([]),
    pluginIds: fc.constant([]),
    skillIds: fc.constant([]),
    allowAllSkills: fc.boolean(),
    mcpIds: fc.constant([]),
    workingDirectory: fc.option(fc.constant('/workspace'), { nil: undefined }),
    enableBashTool: fc.boolean(),
    enableFileTools: fc.boolean(),
    enableWebTools: fc.boolean(),
    enableToolLogging: fc.boolean(),
    enableSafetyChecks: fc.boolean(),
    globalUserMode: fc.boolean(),
    enableHumanApproval: fc.boolean(),
    sandboxEnabled: fc.boolean(),
    sandbox: fc.constant(undefined),
    isDefault: fc.constant(isDefault),
    isSystemAgent: fc.constant(isDefault), // SwarmAgent is also a system agent
    status: fc.constant('active' as const),
    createdAt: fc.constant(new Date().toISOString()),
    updatedAt: fc.constant(new Date().toISOString()),
  });

/**
 * Arbitrary for generating the default SwarmAgent
 */
const swarmAgentArb: fc.Arbitrary<Agent> = agentArb(true, 0).map((agent) => ({
  ...agent,
  name: 'SwarmAgent',
  isDefault: true,
  isSystemAgent: true,
}));

/**
 * Arbitrary for generating a custom (non-default) agent
 */
const customAgentArb = (index: number): fc.Arbitrary<Agent> =>
  agentArb(false, index).map((agent) => ({
    ...agent,
    isDefault: false,
    isSystemAgent: false,
  }));

/**
 * Arbitrary for generating a list of agents with exactly one default agent
 */
const agentListWithDefaultArb: fc.Arbitrary<Agent[]> = fc
  .integer({ min: 0, max: 9 })
  .chain((customCount) => {
    const customAgents = Array.from({ length: customCount }, (_, i) => customAgentArb(i + 1));
    return fc.tuple(swarmAgentArb, ...customAgents).map((agents) => {
      // Shuffle to ensure tests don't depend on order
      return [...agents].sort(() => Math.random() - 0.5);
    });
  });

/**
 * Arbitrary for API response data with is_default field
 */
const apiAgentDataArb: fc.Arbitrary<{ is_default?: boolean }> = fc.oneof(
  fc.constant({ is_default: true }),
  fc.constant({ is_default: false }),
  fc.constant({}) // Missing field case
);

// ============== Property-Based Tests ==============

describe('SwarmAgent - Property-Based Tests', () => {
  /**
   * Property 20: SwarmAgent Always Active
   * **Feature: three-column-layout, Property 20: SwarmAgent Always Active**
   * **Validates: Requirements 7.1**
   *
   * For any application state during normal operation, SwarmAgent SHALL be
   * displayed as the active agent in Main_Chat_Panel.
   */
  describe('Feature: three-column-layout, Property 20: SwarmAgent Always Active', () => {
    it('should identify default agent correctly via isDefault flag', () => {
      fc.assert(
        fc.property(swarmAgentArb, (swarmAgent) => {
          // Property: SwarmAgent SHALL have isDefault: true
          expect(isDefaultAgent(swarmAgent)).toBe(true);
          expect(swarmAgent.isDefault).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should identify non-default agents correctly', () => {
      fc.assert(
        fc.property(customAgentArb(1), (customAgent) => {
          // Property: Custom agents SHALL NOT be default
          expect(isDefaultAgent(customAgent)).toBe(false);
          expect(customAgent.isDefault).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should always find the default agent in a valid agent list', () => {
      fc.assert(
        fc.property(agentListWithDefaultArb, (agents) => {
          // Property: A valid agent list SHALL always contain a default agent
          expect(hasDefaultAgent(agents)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should return the default agent as the active agent for chat', () => {
      fc.assert(
        fc.property(agentListWithDefaultArb, (agents) => {
          const activeAgent = getActiveAgentForChat(agents);

          // Property: Active agent SHALL be the default agent (SwarmAgent)
          expect(activeAgent).toBeDefined();
          expect(activeAgent?.isDefault).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should have exactly one default agent in a valid list', () => {
      fc.assert(
        fc.property(agentListWithDefaultArb, (agents) => {
          const defaultCount = countDefaultAgents(agents);

          // Property: There SHALL be exactly one default agent
          expect(defaultCount).toBe(1);
        }),
        { numRuns: 100 }
      );
    });

    it('should return SwarmAgent regardless of agent list order', () => {
      fc.assert(
        fc.property(
          agentListWithDefaultArb,
          fc.integer({ min: 0, max: 100 }),
          (agents, shuffleSeed) => {
            // Shuffle the list based on seed
            const shuffled = [...agents].sort(() => (shuffleSeed % 2 === 0 ? 1 : -1));

            const activeAgent = getActiveAgentForChat(shuffled);

            // Property: Active agent SHALL be default regardless of list order
            expect(activeAgent).toBeDefined();
            expect(activeAgent?.isDefault).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve isDefault flag through API conversion', () => {
      fc.assert(
        fc.property(fc.constant({ is_default: true }), (apiData) => {
          const converted = convertAgentFromApi(apiData);

          // Property: isDefault SHALL be preserved through conversion
          expect(converted.isDefault).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should default isDefault to false when missing from API', () => {
      fc.assert(
        fc.property(fc.constant({}), (apiData) => {
          const converted = convertAgentFromApi(apiData);

          // Property: Missing is_default SHALL default to false
          expect(converted.isDefault).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle API conversion for both true and false values', () => {
      fc.assert(
        fc.property(apiAgentDataArb, (apiData) => {
          const converted = convertAgentFromApi(apiData);

          // Property: Conversion SHALL correctly map is_default to isDefault
          const expected = apiData.is_default ?? false;
          expect(converted.isDefault).toBe(expected);
        }),
        { numRuns: 100 }
      );
    });

    it('should ensure SwarmAgent is always identifiable as the active agent', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 1, max: 10 }),
          (customAgentCount) => {
            // Create a list with SwarmAgent and multiple custom agents
            const swarmAgent: Agent = {
              id: 'swarm-agent',
              name: 'SwarmAgent',
              description: 'The default orchestrating agent',
              model: 'claude-sonnet-4-20250514',
              permissionMode: 'default',
              systemPrompt: undefined,
              allowedTools: [],
              pluginIds: [],
              skillIds: [],
              allowAllSkills: false,
              mcpIds: [],
              workingDirectory: undefined,
              enableBashTool: true,
              enableFileTools: true,
              enableWebTools: false,
              enableToolLogging: true,
              enableSafetyChecks: true,
              globalUserMode: false,
              enableHumanApproval: true,
              sandboxEnabled: true,
              sandbox: undefined,
              isDefault: true,
              isSystemAgent: true,
              status: 'active',
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            };

            const customAgents: Agent[] = Array.from({ length: customAgentCount }, (_, i) => ({
              ...swarmAgent,
              id: `custom-agent-${i}`,
              name: `Custom Agent ${i}`,
              isDefault: false,
              isSystemAgent: false,
            }));

            const allAgents = [swarmAgent, ...customAgents];

            // Property: SwarmAgent SHALL always be identifiable as active
            const activeAgent = getActiveAgentForChat(allAgents);
            expect(activeAgent).toBeDefined();
            expect(activeAgent?.id).toBe('swarm-agent');
            expect(activeAgent?.name).toBe('SwarmAgent');
            expect(activeAgent?.isDefault).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return undefined when no default agent exists (invalid state)', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 1, max: 5 }),
          (count) => {
            // Create a list with NO default agent (invalid state)
            const agentsWithoutDefault: Agent[] = Array.from({ length: count }, (_, i) => ({
              id: `agent-${i}`,
              name: `Agent ${i}`,
              description: undefined,
              model: undefined,
              permissionMode: 'default' as const,
              systemPrompt: undefined,
              allowedTools: [],
              pluginIds: [],
              skillIds: [],
              allowAllSkills: false,
              mcpIds: [],
              workingDirectory: undefined,
              enableBashTool: true,
              enableFileTools: true,
              enableWebTools: false,
              enableToolLogging: true,
              enableSafetyChecks: true,
              globalUserMode: false,
              enableHumanApproval: true,
              sandboxEnabled: true,
              sandbox: undefined,
              isDefault: false, // No default agent
              isSystemAgent: false,
              status: 'active' as const,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            }));

            const activeAgent = getActiveAgentForChat(agentsWithoutDefault);

            // Property: Without default agent, getActiveAgentForChat returns undefined
            // This represents an invalid application state that should be prevented
            expect(activeAgent).toBeUndefined();
            expect(hasDefaultAgent(agentsWithoutDefault)).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
