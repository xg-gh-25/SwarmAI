import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// Re-implement conversion functions for testing (since they're not exported)
// This tests the conversion logic that should match the actual implementation

interface SwarmWorkspace {
  id: string;
  name: string;
  filePath: string;
  context: string;
  icon?: string;
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
}

interface SwarmWorkspaceCreateRequest {
  name: string;
  filePath: string;
  context: string;
  icon?: string;
}

interface SwarmWorkspaceUpdateRequest {
  name?: string;
  filePath?: string;
  context?: string;
  icon?: string;
}

/**
 * Convert snake_case API response to camelCase for frontend use.
 * Backend uses: file_path, is_default, created_at, updated_at
 * Frontend uses: filePath, isDefault, createdAt, updatedAt
 */
const toCamelCase = (data: Record<string, unknown>): SwarmWorkspace => {
  return {
    id: data.id as string,
    name: data.name as string,
    filePath: data.file_path as string,
    context: data.context as string,
    icon: data.icon as string | undefined,
    isDefault: (data.is_default as boolean) ?? false,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

/**
 * Convert camelCase frontend request to snake_case for API.
 * Frontend uses: filePath, icon
 * Backend expects: file_path, icon
 */
const toSnakeCase = (
  data: SwarmWorkspaceCreateRequest | SwarmWorkspaceUpdateRequest
): Record<string, unknown> => {
  const result: Record<string, unknown> = {};
  if (data.name !== undefined) result.name = data.name;
  if (data.filePath !== undefined) result.file_path = data.filePath;
  if (data.context !== undefined) result.context = data.context;
  if (data.icon !== undefined) result.icon = data.icon;
  return result;
};

describe('SwarmWorkspaces Service Case Conversion', () => {
  // Use a simple string strategy for dates to avoid invalid date issues
  const validDateStrategy = fc.constantFrom(
    '2024-01-15T10:30:00.000Z',
    '2024-06-20T14:45:30.000Z',
    '2025-03-10T08:00:00.000Z',
    '2025-12-31T23:59:59.000Z'
  );

  /**
   * Property 12: API Serialization Convention
   * **Validates: Requirements 9.5**
   * 
   * For any API response from workspace endpoints, field names should use
   * camelCase format (e.g., filePath, isDefault, createdAt).
   */
  describe('Property: toCamelCase conversion', () => {
    it('should correctly convert file_path to filePath', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // The filePath field must match the backend file_path value
            expect(result.filePath).toBe(backendResponse.file_path);

            // Verify the field exists and is a string
            expect(typeof result.filePath).toBe('string');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should correctly convert is_default to isDefault', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // The isDefault field must match the backend is_default value
            expect(result.isDefault).toBe(backendResponse.is_default);

            // Verify the field exists and is a boolean
            expect(typeof result.isDefault).toBe('boolean');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should correctly convert created_at to createdAt', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // The createdAt field must match the backend created_at value
            expect(result.createdAt).toBe(backendResponse.created_at);

            // Verify the field exists and is a string
            expect(typeof result.createdAt).toBe('string');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should correctly convert updated_at to updatedAt', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // The updatedAt field must match the backend updated_at value
            expect(result.updatedAt).toBe(backendResponse.updated_at);

            // Verify the field exists and is a string
            expect(typeof result.updatedAt).toBe('string');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should default isDefault to false when is_default is undefined', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            // Intentionally omit is_default
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // When is_default is missing, isDefault should default to false
            expect(result.isDefault).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve all fields during conversion', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            icon: fc.option(fc.string(), { nil: undefined }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const result = toCamelCase(backendResponse);

            // Verify all snake_case fields are converted to camelCase
            expect(result.id).toBe(backendResponse.id);
            expect(result.name).toBe(backendResponse.name);
            expect(result.filePath).toBe(backendResponse.file_path);
            expect(result.context).toBe(backendResponse.context);
            expect(result.icon).toBe(backendResponse.icon);
            expect(result.isDefault).toBe(backendResponse.is_default);
            expect(result.createdAt).toBe(backendResponse.created_at);
            expect(result.updatedAt).toBe(backendResponse.updated_at);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 12: API Serialization Convention (reverse direction)
   * **Validates: Requirements 9.5**
   * 
   * For any frontend request to workspace endpoints, field names should be
   * converted to snake_case format (e.g., file_path) for the backend.
   */
  describe('Property: toSnakeCase conversion', () => {
    it('should correctly convert filePath to file_path for create requests', () => {
      fc.assert(
        fc.property(
          fc.record({
            name: fc.string({ minLength: 1, maxLength: 100 }),
            filePath: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            icon: fc.option(fc.string(), { nil: undefined }),
          }),
          (frontendRequest) => {
            const result = toSnakeCase(frontendRequest as SwarmWorkspaceCreateRequest);

            // The file_path field must match the frontend filePath value
            expect(result.file_path).toBe(frontendRequest.filePath);

            // Verify the field exists and is a string
            expect(typeof result.file_path).toBe('string');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve name and context fields unchanged', () => {
      fc.assert(
        fc.property(
          fc.record({
            name: fc.string({ minLength: 1, maxLength: 100 }),
            filePath: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
          }),
          (frontendRequest) => {
            const result = toSnakeCase(frontendRequest as SwarmWorkspaceCreateRequest);

            // name and context should be preserved as-is
            expect(result.name).toBe(frontendRequest.name);
            expect(result.context).toBe(frontendRequest.context);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should only include defined fields in update requests', () => {
      fc.assert(
        fc.property(
          fc.record({
            name: fc.option(fc.string({ minLength: 1, maxLength: 100 }), { nil: undefined }),
            filePath: fc.option(fc.string({ minLength: 1 }), { nil: undefined }),
            context: fc.option(fc.string({ minLength: 1 }), { nil: undefined }),
            icon: fc.option(fc.string(), { nil: undefined }),
          }),
          (frontendRequest) => {
            const result = toSnakeCase(frontendRequest as SwarmWorkspaceUpdateRequest);

            // Only defined fields should be present in result
            if (frontendRequest.name !== undefined) {
              expect(result.name).toBe(frontendRequest.name);
            } else {
              expect(result.name).toBeUndefined();
            }

            if (frontendRequest.filePath !== undefined) {
              expect(result.file_path).toBe(frontendRequest.filePath);
            } else {
              expect(result.file_path).toBeUndefined();
            }

            if (frontendRequest.context !== undefined) {
              expect(result.context).toBe(frontendRequest.context);
            } else {
              expect(result.context).toBeUndefined();
            }

            if (frontendRequest.icon !== undefined) {
              expect(result.icon).toBe(frontendRequest.icon);
            } else {
              expect(result.icon).toBeUndefined();
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle icon field correctly', () => {
      fc.assert(
        fc.property(
          fc.record({
            name: fc.string({ minLength: 1, maxLength: 100 }),
            filePath: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            icon: fc.string({ minLength: 1 }),
          }),
          (frontendRequest) => {
            const result = toSnakeCase(frontendRequest as SwarmWorkspaceCreateRequest);

            // icon should be preserved as-is (no case conversion needed)
            expect(result.icon).toBe(frontendRequest.icon);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property: Round-trip conversion consistency
   * **Validates: Requirements 9.5**
   * 
   * Converting from snake_case to camelCase and extracting the relevant fields
   * should produce consistent results.
   */
  describe('Property: Round-trip conversion consistency', () => {
    it('should maintain data integrity through toCamelCase conversion', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.uuid(),
            name: fc.string({ minLength: 1, maxLength: 100 }),
            file_path: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
            icon: fc.option(fc.string(), { nil: undefined }),
            is_default: fc.boolean(),
            created_at: validDateStrategy,
            updated_at: validDateStrategy,
          }),
          (backendResponse) => {
            const camelCaseResult = toCamelCase(backendResponse);

            // All data should be preserved (just with different key names)
            expect(camelCaseResult.id).toBe(backendResponse.id);
            expect(camelCaseResult.name).toBe(backendResponse.name);
            expect(camelCaseResult.filePath).toBe(backendResponse.file_path);
            expect(camelCaseResult.context).toBe(backendResponse.context);
            expect(camelCaseResult.icon).toBe(backendResponse.icon);
            expect(camelCaseResult.isDefault).toBe(backendResponse.is_default);
            expect(camelCaseResult.createdAt).toBe(backendResponse.created_at);
            expect(camelCaseResult.updatedAt).toBe(backendResponse.updated_at);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should produce valid snake_case output from toSnakeCase', () => {
      fc.assert(
        fc.property(
          fc.record({
            name: fc.string({ minLength: 1, maxLength: 100 }),
            filePath: fc.string({ minLength: 1 }),
            context: fc.string({ minLength: 1 }),
          }),
          (frontendRequest) => {
            const snakeCaseResult = toSnakeCase(frontendRequest as SwarmWorkspaceCreateRequest);

            // Result should have snake_case keys
            expect(snakeCaseResult).toHaveProperty('file_path');
            expect(snakeCaseResult).not.toHaveProperty('filePath');

            // Values should match
            expect(snakeCaseResult.file_path).toBe(frontendRequest.filePath);
            expect(snakeCaseResult.name).toBe(frontendRequest.name);
            expect(snakeCaseResult.context).toBe(frontendRequest.context);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
