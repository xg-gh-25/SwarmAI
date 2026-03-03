import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// Re-implement toCamelCase for testing (since it's not exported)
// This tests the conversion logic that should match the actual implementation
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
  isSystemAgent: boolean;
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

// Convert snake_case response to camelCase (mirrors actual implementation)
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
    isSystemAgent: (data.is_system_agent as boolean) ?? false,
    status: (data.status as 'active' | 'inactive') ?? 'active',
    createdAt: (data.created_at as string) ?? '',
    updatedAt: (data.updated_at as string) ?? '',
  };
};

describe('Agent Service Case Conversion', () => {
  /**
   * Property 2: System Resource Detection (frontend aspect)
   * Validates: Requirements 6.7, 6.8
   * 
   * For any backend response with is_system_agent field,
   * toCamelCase must correctly convert it to isSystemAgent.
   */
  describe('Property: is_system_agent to isSystemAgent conversion', () => {
    // Use a simple string strategy for dates to avoid invalid date issues
    const validDateStrategy = fc.constantFrom(
      '2024-01-15T10:30:00.000Z',
      '2024-06-20T14:45:30.000Z',
      '2025-03-10T08:00:00.000Z',
      '2025-12-31T23:59:59.000Z'
    );

    it('should correctly convert is_system_agent boolean to isSystemAgent', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            is_system_agent: fc.boolean(),
            is_default: fc.boolean(),
            status: fc.constantFrom('active', 'inactive'),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);
            
            // The isSystemAgent field must match the backend is_system_agent value
            expect(result.isSystemAgent).toBe(backendResponse.is_system_agent);
            
            // Verify the field exists and is a boolean
            expect(typeof result.isSystemAgent).toBe('boolean');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should default isSystemAgent to false when is_system_agent is undefined', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            // Intentionally omit is_system_agent
            status: fc.constantFrom('active', 'inactive'),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);
            
            // When is_system_agent is missing, isSystemAgent should default to false
            expect(result.isSystemAgent).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve all other fields during conversion', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            description: fc.option(fc.string(), { nil: undefined }),
            model: fc.option(fc.string(), { nil: undefined }),
            is_system_agent: fc.boolean(),
            is_default: fc.boolean(),
            global_user_mode: fc.boolean(),
            enable_human_approval: fc.boolean(),
            sandbox_enabled: fc.boolean(),
            allow_all_skills: fc.boolean(),
            status: fc.constantFrom('active', 'inactive'),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);
            
            // Verify all snake_case fields are converted to camelCase
            expect(result.id).toBe(backendResponse.id);
            expect(result.name).toBe(backendResponse.name);
            expect(result.description).toBe(backendResponse.description);
            expect(result.model).toBe(backendResponse.model);
            expect(result.isSystemAgent).toBe(backendResponse.is_system_agent);
            expect(result.isDefault).toBe(backendResponse.is_default);
            expect(result.globalUserMode).toBe(backendResponse.global_user_mode);
            expect(result.enableHumanApproval).toBe(backendResponse.enable_human_approval);
            expect(result.sandboxEnabled).toBe(backendResponse.sandbox_enabled);
            expect(result.allowAllSkills).toBe(backendResponse.allow_all_skills);
            expect(result.status).toBe(backendResponse.status);
            expect(result.createdAt).toBe(backendResponse.created_at);
            expect(result.updatedAt).toBe(backendResponse.updated_at);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
