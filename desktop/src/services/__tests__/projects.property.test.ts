/**
 * Property-Based Tests for Project Service Case Conversion (Property 7).
 *
 * Tests the frontend snake_case ↔ camelCase conversion functions used by
 * the projects service layer. Verifies that `projectToCamelCase`,
 * `projectUpdateToSnakeCase`, and `historyEntryToCamelCase` correctly map
 * all fields between backend (snake_case) and frontend (camelCase) formats.
 *
 * Testing methodology: property-based using fast-check with 100+ runs.
 *
 * **Validates: Requirements 21.6, 22.4**
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import {
  projectToCamelCase,
  projectUpdateToSnakeCase,
  historyEntryToCamelCase,
} from '../workspace';
import type {
  Project,
  ProjectHistoryEntry,
  ProjectUpdateRequest,
} from '../../types';

// ─────────────────────────────────────────────────────────────────────────────
// fast-check arbitraries
// ─────────────────────────────────────────────────────────────────────────────

const statusArb = fc.constantFrom('active', 'archived', 'completed');
const priorityArb = fc.constantFrom('low', 'medium', 'high', 'critical');
const sourceArb = fc.constantFrom('user', 'agent', 'system', 'migration');
const isoDateArb = fc
  .integer({ min: 1577836800000, max: 1924905600000 })
  .map((ts) => new Date(ts).toISOString());

/** Arbitrary for a snake_case backend project API response. */
const backendProjectResponseArb = fc.record({
  id: fc.uuid(),
  name: fc.string({ minLength: 1, maxLength: 100 }),
  description: fc.string({ maxLength: 500 }),
  path: fc.string({ maxLength: 200 }),
  created_at: isoDateArb,
  updated_at: isoDateArb,
  status: statusArb,
  priority: fc.option(priorityArb, { nil: null }),
  tags: fc.array(fc.string({ minLength: 1, maxLength: 30 }), { maxLength: 10 }),
  schema_version: fc.constantFrom('1.0.0', '1.1.0', '2.0.0'),
  version: fc.integer({ min: 1, max: 10000 }),
  context_l0: fc.option(fc.string({ maxLength: 1000 }), { nil: undefined }),
  context_l1: fc.option(fc.string({ maxLength: 4000 }), { nil: undefined }),
});

/** Arbitrary for a camelCase ProjectUpdateRequest with at least one field. */
const projectUpdateRequestArb = fc
  .record(
    {
      name: fc.string({ minLength: 1, maxLength: 100 }),
      description: fc.string({ maxLength: 500 }),
      status: statusArb,
      tags: fc.array(fc.string({ minLength: 1, maxLength: 30 }), { maxLength: 10 }),
      priority: fc.oneof(priorityArb, fc.constant(null)),
    },
    { requiredKeys: [] }
  )
  .filter((d) => Object.keys(d).length > 0);

/** Arbitrary for a snake_case backend history entry. */
const backendHistoryEntryArb = fc.record({
  version: fc.integer({ min: 1, max: 10000 }),
  timestamp: isoDateArb,
  action: fc.constantFrom(
    'created',
    'updated',
    'status_changed',
    'renamed',
    'tags_modified',
    'priority_changed',
    'schema_migrated'
  ),
  changes: fc.dictionary(
    fc.string({ minLength: 1, maxLength: 20 }),
    fc.record({ from: fc.string(), to: fc.string() }),
    { maxKeys: 5 }
  ),
  source: sourceArb,
});

// ─────────────────────────────────────────────────────────────────────────────
// Property tests
// ─────────────────────────────────────────────────────────────────────────────

describe('Project Service - Property-Based Tests', () => {
  /**
   * Property 7: Frontend Case Conversion Round-Trip
   * **Validates: Requirements 21.6, 22.4**
   */
  describe('Property 7: Frontend Case Conversion Round-Trip', () => {
    it('projectToCamelCase maps all snake_case fields correctly', () => {
      fc.assert(
        fc.property(backendProjectResponseArb, (backend) => {
          const result = projectToCamelCase(backend as Record<string, unknown>);

          // Direct-mapped fields (no rename needed)
          expect(result.id).toBe(backend.id);
          expect(result.name).toBe(backend.name);
          expect(result.description).toBe(backend.description);
          expect(result.path).toBe(backend.path);
          expect(result.status).toBe(backend.status);
          expect(result.priority).toBe(backend.priority);
          expect(result.tags).toEqual(backend.tags);
          expect(result.version).toBe(backend.version);

          // Renamed fields: snake_case → camelCase
          expect(result.createdAt).toBe(backend.created_at);
          expect(result.updatedAt).toBe(backend.updated_at);
          expect(result.schemaVersion).toBe(backend.schema_version);
          expect(result.contextL0).toBe(backend.context_l0);
          expect(result.contextL1).toBe(backend.context_l1);
        }),
        { numRuns: 100 }
      );
    });

    it('projectUpdateToSnakeCase preserves all present update fields', () => {
      fc.assert(
        fc.property(projectUpdateRequestArb, (update) => {
          const result = projectUpdateToSnakeCase(update as ProjectUpdateRequest);

          // Every field present in the input should appear in the output
          if (update.name !== undefined) expect(result.name).toBe(update.name);
          if (update.description !== undefined)
            expect(result.description).toBe(update.description);
          if (update.status !== undefined) expect(result.status).toBe(update.status);
          if (update.tags !== undefined) expect(result.tags).toEqual(update.tags);
          if (update.priority !== undefined)
            expect(result.priority).toBe(update.priority);

          // No extra keys beyond what was provided
          const inputKeys = Object.keys(update).filter(
            (k) => (update as Record<string, unknown>)[k] !== undefined
          );
          expect(Object.keys(result).sort()).toEqual(inputKeys.sort());
        }),
        { numRuns: 100 }
      );
    });

    it('projectUpdateToSnakeCase omits undefined fields', () => {
      fc.assert(
        fc.property(projectUpdateRequestArb, (update) => {
          const result = projectUpdateToSnakeCase(update as ProjectUpdateRequest);

          // Result should only contain keys that were defined in input
          for (const key of Object.keys(result)) {
            expect((update as Record<string, unknown>)[key]).not.toBeUndefined();
          }
        }),
        { numRuns: 100 }
      );
    });

    it('historyEntryToCamelCase maps all history entry fields correctly', () => {
      fc.assert(
        fc.property(backendHistoryEntryArb, (backend) => {
          const result = historyEntryToCamelCase(backend as Record<string, unknown>);

          expect(result.version).toBe(backend.version);
          expect(result.timestamp).toBe(backend.timestamp);
          expect(result.action).toBe(backend.action);
          expect(result.changes).toEqual(backend.changes);
          expect(result.source).toBe(backend.source);
        }),
        { numRuns: 100 }
      );
    });
  });
});
