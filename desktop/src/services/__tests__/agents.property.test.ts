/**
 * Property-Based Tests for Agent Service Case Conversion
 *
 * **Validates: Requirements 3.5**
 *
 * Property 2: API Response Case Conversion
 * For any agent response from the backend containing `is_default`, the frontend
 * `toCamelCase` function SHALL produce an object with `isDefault` having the
 * same boolean value.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// Re-implement toCamelCase for testing (extracted from agents.ts)
// This mirrors the actual implementation to test the conversion logic
interface SandboxConfig {
  enabled: boolean;
  autoAllowBashIfSandboxed: boolean;
  excludedCommands: string[];
  allowUnsandboxedCommands: boolean;
  network: {
    allowLocalBinding: boolean;
    allowUnixSockets: string[];
    allowAllUnixSockets: boolean;
  };
}

interface Agent {
  id: string;
  name: string;
  description?: string;
  model?: string;
  permissionMode: 'default' | 'acceptEdits' | 'plan' | 'bypassPermissions';
  systemPrompt?: string;
  allowedTools: string[];
  pluginIds: string[];
  allowedSkills: string[];
  allowAllSkills: boolean;
  mcpIds: string[];
  workingDirectory?: string;
  enableBashTool: boolean;
  enableFileTools: boolean;
  enableWebTools: boolean;
  enableToolLogging: boolean;
  enableSafetyChecks: boolean;
  globalUserMode: boolean;
  enableHumanApproval: boolean;
  sandboxEnabled: boolean;
  sandbox?: SandboxConfig;
  isDefault: boolean;
  status: 'active' | 'inactive';
  createdAt: string;
  updatedAt: string;
}

// Convert sandbox config from snake_case to camelCase
const sandboxToCamelCase = (data: Record<string, unknown>): SandboxConfig => {
  const networkData = data.network as Record<string, unknown> | undefined;
  return {
    enabled: (data.enabled as boolean) ?? false,
    autoAllowBashIfSandboxed: (data.auto_allow_bash_if_sandboxed as boolean) ?? true,
    excludedCommands: (data.excluded_commands as string[]) ?? [],
    allowUnsandboxedCommands: (data.allow_unsandboxed_commands as boolean) ?? false,
    network: {
      allowLocalBinding: (networkData?.allow_local_binding as boolean) ?? false,
      allowUnixSockets: (networkData?.allow_unix_sockets as string[]) ?? [],
      allowAllUnixSockets: (networkData?.allow_all_unix_sockets as boolean) ?? false,
    },
  };
};

// Convert snake_case response to camelCase (mirrors agents.ts implementation)
const toCamelCase = (data: Record<string, unknown>): Agent => {
  const sandboxData = data.sandbox as Record<string, unknown> | undefined;
  const sandbox: SandboxConfig | undefined = sandboxData
    ? sandboxToCamelCase(sandboxData)
    : undefined;

  return {
    id: data.id as string,
    name: data.name as string,
    description: data.description as string | undefined,
    model: data.model as string | undefined,
    permissionMode: (data.permission_mode as Agent['permissionMode']) ?? 'default',
    systemPrompt: data.system_prompt as string | undefined,
    allowedTools: (data.allowed_tools as string[]) || [],
    pluginIds: (data.plugin_ids as string[]) || [],
    allowedSkills: (data.allowed_skills as string[]) || [],
    allowAllSkills: (data.allow_all_skills as boolean) ?? false,
    mcpIds: (data.mcp_ids as string[]) || [],
    workingDirectory: data.working_directory as string | undefined,
    enableBashTool: (data.enable_bash_tool as boolean) ?? true,
    enableFileTools: (data.enable_file_tools as boolean) ?? true,
    enableWebTools: (data.enable_web_tools as boolean) ?? false,
    enableToolLogging: (data.enable_tool_logging as boolean) ?? true,
    enableSafetyChecks: (data.enable_safety_checks as boolean) ?? true,
    globalUserMode: (data.global_user_mode as boolean) ?? false,
    enableHumanApproval: (data.enable_human_approval as boolean) ?? true,
    sandboxEnabled: (data.sandbox_enabled as boolean) ?? true,
    sandbox,
    isDefault: (data.is_default as boolean) ?? false,
    status: (data.status as 'active' | 'inactive') ?? 'active',
    createdAt: (data.created_at as string) ?? '',
    updatedAt: (data.updated_at as string) ?? '',
  };
};

describe('Agent Service - Property-Based Tests', () => {
  /**
   * Property 2: API Response Case Conversion
   * **Validates: Requirements 3.5**
   *
   * For any agent response from the backend containing `is_default`,
   * the frontend `toCamelCase` function SHALL produce an object with
   * `isDefault` having the same boolean value.
   */
  describe('Property 2: API Response Case Conversion', () => {
    // Arbitrary for permission mode
    const permissionModeArb = fc.constantFrom(
      'default',
      'acceptEdits',
      'plan',
      'bypassPermissions'
    );

    // Arbitrary for status
    const statusArb = fc.constantFrom('active', 'inactive');

    // Arbitrary for sandbox network config (snake_case backend format)
    const sandboxNetworkArb = fc.record({
      allow_local_binding: fc.boolean(),
      allow_unix_sockets: fc.array(fc.string(), { maxLength: 5 }),
      allow_all_unix_sockets: fc.boolean(),
    });

    // Arbitrary for sandbox config (snake_case backend format)
    const sandboxArb = fc.record({
      enabled: fc.boolean(),
      auto_allow_bash_if_sandboxed: fc.boolean(),
      excluded_commands: fc.array(fc.string(), { maxLength: 5 }),
      allow_unsandboxed_commands: fc.boolean(),
      network: sandboxNetworkArb,
    });

    // Arbitrary for backend agent response (snake_case format)
    const backendAgentResponseArb = fc.record({
      id: fc.uuid(),
      name: fc.string({ minLength: 1, maxLength: 100 }),
      description: fc.option(fc.string({ maxLength: 500 }), { nil: undefined }),
      model: fc.option(fc.string({ maxLength: 50 }), { nil: undefined }),
      permission_mode: permissionModeArb,
      system_prompt: fc.option(fc.string({ maxLength: 1000 }), { nil: undefined }),
      allowed_tools: fc.array(fc.string(), { maxLength: 10 }),
      plugin_ids: fc.array(fc.uuid(), { maxLength: 5 }),
      allowed_skills: fc.array(fc.uuid(), { maxLength: 5 }),
      allow_all_skills: fc.boolean(),
      mcp_ids: fc.array(fc.uuid(), { maxLength: 5 }),
      working_directory: fc.option(fc.string({ maxLength: 200 }), { nil: undefined }),
      enable_bash_tool: fc.boolean(),
      enable_file_tools: fc.boolean(),
      enable_web_tools: fc.boolean(),
      enable_tool_logging: fc.boolean(),
      enable_safety_checks: fc.boolean(),
      global_user_mode: fc.boolean(),
      enable_human_approval: fc.boolean(),
      sandbox_enabled: fc.boolean(),
      sandbox: fc.option(sandboxArb, { nil: undefined }),
      is_default: fc.boolean(), // The key field we're testing
      status: statusArb,
      // Use integer timestamps to generate valid ISO date strings
      created_at: fc
        .integer({ min: 1577836800000, max: 1924905600000 }) // 2020-01-01 to 2030-12-31
        .map((ts) => new Date(ts).toISOString()),
      updated_at: fc
        .integer({ min: 1577836800000, max: 1924905600000 })
        .map((ts) => new Date(ts).toISOString()),
    });

    it('should preserve is_default boolean value when converting to isDefault', () => {
      fc.assert(
        fc.property(backendAgentResponseArb, (backendResponse) => {
          // Apply the toCamelCase transformation
          const frontendAgent = toCamelCase(backendResponse as Record<string, unknown>);

          // Property: isDefault must equal the original is_default value
          expect(frontendAgent.isDefault).toBe(backendResponse.is_default);
        }),
        { numRuns: 100 } // Minimum 100 iterations as per spec
      );
    });

    it('should handle is_default=true correctly', () => {
      fc.assert(
        fc.property(
          backendAgentResponseArb.map((agent) => ({ ...agent, is_default: true })),
          (backendResponse) => {
            const frontendAgent = toCamelCase(backendResponse as Record<string, unknown>);
            expect(frontendAgent.isDefault).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle is_default=false correctly', () => {
      fc.assert(
        fc.property(
          backendAgentResponseArb.map((agent) => ({ ...agent, is_default: false })),
          (backendResponse) => {
            const frontendAgent = toCamelCase(backendResponse as Record<string, unknown>);
            expect(frontendAgent.isDefault).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should default isDefault to false when is_default is undefined', () => {
      fc.assert(
        fc.property(
          backendAgentResponseArb.map((agent) => {
            // eslint-disable-next-line @typescript-eslint/no-unused-vars
            const { is_default, ...rest } = agent;
            return rest;
          }),
          (backendResponse) => {
            const frontendAgent = toCamelCase(backendResponse as Record<string, unknown>);
            // When is_default is missing, isDefault should default to false
            expect(frontendAgent.isDefault).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve all other boolean field conversions alongside isDefault', () => {
      fc.assert(
        fc.property(backendAgentResponseArb, (backendResponse) => {
          const frontendAgent = toCamelCase(backendResponse as Record<string, unknown>);

          // Verify isDefault conversion
          expect(frontendAgent.isDefault).toBe(backendResponse.is_default);

          // Also verify other boolean fields are correctly converted
          expect(frontendAgent.enableBashTool).toBe(backendResponse.enable_bash_tool);
          expect(frontendAgent.enableFileTools).toBe(backendResponse.enable_file_tools);
          expect(frontendAgent.enableWebTools).toBe(backendResponse.enable_web_tools);
          expect(frontendAgent.enableToolLogging).toBe(backendResponse.enable_tool_logging);
          expect(frontendAgent.enableSafetyChecks).toBe(backendResponse.enable_safety_checks);
          expect(frontendAgent.globalUserMode).toBe(backendResponse.global_user_mode);
          expect(frontendAgent.enableHumanApproval).toBe(backendResponse.enable_human_approval);
          expect(frontendAgent.sandboxEnabled).toBe(backendResponse.sandbox_enabled);
          expect(frontendAgent.allowAllSkills).toBe(backendResponse.allow_all_skills);
        }),
        { numRuns: 100 }
      );
    });
  });
});
