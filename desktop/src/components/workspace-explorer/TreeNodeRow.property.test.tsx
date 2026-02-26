/**
 * Property-based tests for TreeNodeRow visual properties.
 *
 * Tests the depth-based visual rendering of the TreeNodeRow component using
 * fast-check for property-based testing with vitest and @testing-library/react.
 *
 * Key properties verified:
 * - **Property 12: Depth-Based Visual Properties** — for any tree node at
 *   depth d, the rendered row has left padding = d × 16 + 8 px (base padding)
 *   and font-weight 500 at depth 0, 400 at depth 1+.
 *
 * Testing methodology: Property-based testing with fast-check.
 * Each property runs with `{ numRuns: 100 }`.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import TreeNodeRow from './TreeNodeRow';
import type { TreeNode } from '../../types';

// ── Helpers ──────────────────────────────────────────────────────────────

/** Minimal TreeNode fixture for rendering. */
function makeNode(overrides?: Partial<TreeNode>): TreeNode {
  return {
    name: 'test-node',
    path: 'test/path',
    type: 'directory',
    isSystemManaged: false,
    ...overrides,
  };
}

/** No-op handler for event props. */
const noop = () => {};
const noopMouse = (_e: React.MouseEvent) => {};

// ── Property 12: Depth-Based Visual Properties ──────────────────────────
// Feature: swarmws-explorer-ux, Property 12: Depth-Based Visual Properties

describe('Property 12: Depth-Based Visual Properties', () => {
  /**
   * Validates: Requirements 14.1, 14.2
   *
   * For any tree node at depth d (0–5), the rendered row shall have:
   * - paddingLeft = d * 16 + 8 (base padding)
   * - fontWeight = 500 when depth === 0, 400 otherwise
   */
  it('paddingLeft = depth * 16 + 8 and fontWeight varies by depth', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 5 }),
        (depth) => {
          cleanup();
          const node = makeNode();

          const { getByTestId } = render(
            <TreeNodeRow
              node={node}
              depth={depth}
              isExpanded={false}
              isSelected={false}
              isMatched={false}
              isSystemManaged={false}
              onToggle={noop}
              onSelect={noop}
              onContextMenu={noopMouse}
              onDoubleClick={noop}
              style={{ position: 'absolute' as const, top: 0, height: 32, width: '100%' }}
            />,
          );

          const row = getByTestId('tree-row');
          const style = row.style;

          // Verify paddingLeft = depth * 16 + 8
          const expectedPadding = depth * 16 + 8;
          expect(style.paddingLeft).toBe(`${expectedPadding}px`);

          // Verify fontWeight: 500 at depth 0, 400 at depth 1+
          const expectedWeight = depth === 0 ? '500' : '400';
          expect(style.fontWeight).toBe(expectedWeight);

          cleanup();
        },
      ),
      { numRuns: 100 },
    );
  });
});
