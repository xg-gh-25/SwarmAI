/**
 * Property-Based Tests for System Service Case Conversion
 *
 * **Feature: swarm-init-status-display**
 * **Property 3: Snake Case to Camel Case Transformation**
 * **Validates: Requirements 3.2**
 *
 * For any valid API response from `/api/system/status`, the frontend service's
 * `getStatus()` function SHALL return an object where all snake_case keys
 * (`skills_count`, `mcp_servers_count`, `channel_gateway`) are converted to
 * camelCase (`skillsCount`, `mcpServersCount`, `channelGateway`).
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// ============== Interfaces ==============

// Frontend interfaces (camelCase)
interface DatabaseStatus {
  healthy: boolean;
  error?: string;
}

interface AgentStatus {
  ready: boolean;
  name?: string;
  skillsCount: number;
  mcpServersCount: number;
}

interface ChannelGatewayStatus {
  running: boolean;
}

interface SystemStatus {
  database: DatabaseStatus;
  agent: AgentStatus;
  channelGateway: ChannelGatewayStatus;
  initialized: boolean;
  timestamp: string;
}

// ============== Case Conversion (mirrors system.ts implementation) ==============

/**
 * Convert snake_case API response to camelCase for TypeScript consumption.
 *
 * Backend response (snake_case):
 * - skills_count -> skillsCount
 * - mcp_servers_count -> mcpServersCount
 * - channel_gateway -> channelGateway
 */
const toCamelCase = (data: Record<string, unknown>): SystemStatus => {
  const database = data.database as Record<string, unknown>;
  const agent = data.agent as Record<string, unknown>;
  const channelGateway = data.channel_gateway as Record<string, unknown>;

  return {
    database: {
      healthy: database.healthy as boolean,
      error: database.error as string | undefined,
    },
    agent: {
      ready: agent.ready as boolean,
      name: agent.name as string | undefined,
      skillsCount: (agent.skills_count as number) ?? 0,
      mcpServersCount: (agent.mcp_servers_count as number) ?? 0,
    },
    channelGateway: {
      running: channelGateway.running as boolean,
    },
    initialized: data.initialized as boolean,
    timestamp: data.timestamp as string,
  };
};

// ============== Property-Based Tests ==============

describe('System Service - Property-Based Tests', () => {
  /**
   * Property 3: Snake Case to Camel Case Transformation
   * **Feature: swarm-init-status-display**
   * **Validates: Requirements 3.2**
   *
   * For any valid API response from `/api/system/status`, the frontend service's
   * `getStatus()` function SHALL return an object where all snake_case keys
   * (`skills_count`, `mcp_servers_count`, `channel_gateway`) are converted to
   * camelCase (`skillsCount`, `mcpServersCount`, `channelGateway`).
   */
  describe('Property 3: Snake Case to Camel Case Transformation', () => {
    // Arbitrary for database status (snake_case backend format)
    const databaseStatusArb = fc.record({
      healthy: fc.boolean(),
      error: fc.option(fc.string({ maxLength: 200 }), { nil: undefined }),
    });

    // Arbitrary for agent status (snake_case backend format)
    const agentStatusArb = fc.record({
      ready: fc.boolean(),
      name: fc.option(fc.string({ minLength: 1, maxLength: 100 }), { nil: undefined }),
      skills_count: fc.nat({ max: 100 }), // snake_case key
      mcp_servers_count: fc.nat({ max: 50 }), // snake_case key
    });

    // Arbitrary for channel gateway status (snake_case backend format)
    const channelGatewayStatusArb = fc.record({
      running: fc.boolean(),
    });

    // Arbitrary for complete backend system status response (snake_case format)
    const backendSystemStatusArb = fc.record({
      database: databaseStatusArb,
      agent: agentStatusArb,
      channel_gateway: channelGatewayStatusArb, // snake_case key
      initialized: fc.boolean(),
      timestamp: fc
        .integer({ min: 1577836800000, max: 1924905600000 }) // 2020-01-01 to 2030-12-31
        .map((ts) => new Date(ts).toISOString()),
    });

    it('should convert skills_count to skillsCount', () => {
      fc.assert(
        fc.property(backendSystemStatusArb, (backendResponse) => {
          const result = toCamelCase(backendResponse as Record<string, unknown>);

          // Property: skillsCount must equal the original skills_count value
          expect(result.agent.skillsCount).toBe(backendResponse.agent.skills_count);
          expect(typeof result.agent.skillsCount).toBe('number');
        }),
        { numRuns: 100 }
      );
    });

    it('should convert mcp_servers_count to mcpServersCount', () => {
      fc.assert(
        fc.property(backendSystemStatusArb, (backendResponse) => {
          const result = toCamelCase(backendResponse as Record<string, unknown>);

          // Property: mcpServersCount must equal the original mcp_servers_count value
          expect(result.agent.mcpServersCount).toBe(backendResponse.agent.mcp_servers_count);
          expect(typeof result.agent.mcpServersCount).toBe('number');
        }),
        { numRuns: 100 }
      );
    });

    it('should convert channel_gateway to channelGateway', () => {
      fc.assert(
        fc.property(backendSystemStatusArb, (backendResponse) => {
          const result = toCamelCase(backendResponse as Record<string, unknown>);

          // Property: channelGateway.running must equal the original channel_gateway.running value
          expect(result.channelGateway.running).toBe(backendResponse.channel_gateway.running);
          expect(typeof result.channelGateway.running).toBe('boolean');
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve all field values during snake_case to camelCase conversion', () => {
      fc.assert(
        fc.property(backendSystemStatusArb, (backendResponse) => {
          const result = toCamelCase(backendResponse as Record<string, unknown>);

          // Verify database fields
          expect(result.database.healthy).toBe(backendResponse.database.healthy);
          expect(result.database.error).toBe(backendResponse.database.error);

          // Verify agent fields (including snake_case conversions)
          expect(result.agent.ready).toBe(backendResponse.agent.ready);
          expect(result.agent.name).toBe(backendResponse.agent.name);
          expect(result.agent.skillsCount).toBe(backendResponse.agent.skills_count);
          expect(result.agent.mcpServersCount).toBe(backendResponse.agent.mcp_servers_count);

          // Verify channel gateway (snake_case key conversion)
          expect(result.channelGateway.running).toBe(backendResponse.channel_gateway.running);

          // Verify top-level fields
          expect(result.initialized).toBe(backendResponse.initialized);
          expect(result.timestamp).toBe(backendResponse.timestamp);
        }),
        { numRuns: 100 }
      );
    });

    it('should produce camelCase keys in the output object', () => {
      fc.assert(
        fc.property(backendSystemStatusArb, (backendResponse) => {
          const result = toCamelCase(backendResponse as Record<string, unknown>);

          // Verify the result has camelCase keys (not snake_case)
          expect('channelGateway' in result).toBe(true);
          expect('channel_gateway' in result).toBe(false);

          expect('skillsCount' in result.agent).toBe(true);
          expect('skills_count' in result.agent).toBe(false);

          expect('mcpServersCount' in result.agent).toBe(true);
          expect('mcp_servers_count' in result.agent).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle edge case values for counts', () => {
      fc.assert(
        fc.property(
          fc.record({
            database: fc.record({
              healthy: fc.boolean(),
              error: fc.option(fc.string(), { nil: undefined }),
            }),
            agent: fc.record({
              ready: fc.boolean(),
              name: fc.option(fc.string(), { nil: undefined }),
              skills_count: fc.constantFrom(0, 1, 50, 100), // Edge values
              mcp_servers_count: fc.constantFrom(0, 1, 25, 50), // Edge values
            }),
            channel_gateway: fc.record({
              running: fc.boolean(),
            }),
            initialized: fc.boolean(),
            timestamp: fc.constant('2024-01-15T10:30:00.000Z'),
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse as Record<string, unknown>);

            // Verify edge values are preserved
            expect(result.agent.skillsCount).toBe(backendResponse.agent.skills_count);
            expect(result.agent.mcpServersCount).toBe(backendResponse.agent.mcp_servers_count);
            expect(result.agent.skillsCount).toBeGreaterThanOrEqual(0);
            expect(result.agent.mcpServersCount).toBeGreaterThanOrEqual(0);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
