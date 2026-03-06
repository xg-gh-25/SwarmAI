/**
 * Property-Based Tests for ExplorerContext state management.
 *
 * Tests the core state logic of the Workspace Explorer context using
 * fast-check for property-based testing with vitest.
 *
 * Key properties verified:
 * - **Property 4: Toggle Expand/Collapse** — toggling a path adds it if absent
 *   or removes it if present, changing the set size by exactly one.
 * - **Property 6: Focus Mode State Transformation** — enabling focus mode
 *   auto-expands the active project and ancestors, collapses non-active
 *   projects, and keeps Knowledge visible but collapsed.
 * - **Property 8: Search Match, Expand, and Highlight** — for any tree and
 *   non-empty query, matchedPaths contains every node whose name contains the
 *   query, ancestors includes all ancestor paths of matched nodes, and no
 *   non-matching node appears in matchedPaths.
 * - **Property 10: Session State Round-Trip** — for any ExplorerSessionState,
 *   serializing to sessionStorage and deserializing produces identical state.
 *
 * Testing methodology: Property-based testing with fast-check.
 * Each property runs with `{ numRuns: 100 }`.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import * as fc from 'fast-check';
import { computeFocusModeExpandedPaths, findMatches, substringMatch, saveSessionState, loadSessionState } from './ExplorerContext';
import type { ExplorerSessionState } from './ExplorerContext';
import type { TreeNode } from '../types';

// ── Pure toggle logic (mirrors ExplorerProvider.toggleExpand) ─────────────
// The actual toggleExpand in ExplorerContext uses setExpandedPaths with a
// callback that clones the Set, then adds or deletes the path. We replicate
// that pure logic here so we can test the invariant without React rendering.

function applyToggle(expandedPaths: Set<string>, path: string): Set<string> {
  const next = new Set(expandedPaths);
  if (next.has(path)) {
    next.delete(path);
  } else {
    next.add(path);
  }
  return next;
}

// ── Generators ───────────────────────────────────────────────────────────

/** Generates realistic-ish path strings (e.g. "a/b/c"). */
const arbPathSegment = fc
  .array(fc.constantFrom('a', 'b', 'c', 'd', 'e', 'f'), { minLength: 1, maxLength: 4 })
  .map((chars) => chars.join(''));

const arbPath = fc
  .array(arbPathSegment, { minLength: 1, maxLength: 4 })
  .map((segments) => segments.join('/'));

/** Generates a Set<string> of expanded paths. */
const arbExpandedPaths = fc.uniqueArray(arbPath, { maxLength: 20 }).map(
  (paths) => new Set(paths),
);

// ── Property Tests ───────────────────────────────────────────────────────

describe('ExplorerContext - Property-Based Tests', () => {
  // Feature: swarmws-explorer-ux, Property 4: Toggle Expand/Collapse
  // **Validates: Requirements 11.2**
  describe('Property 4: Toggle Expand/Collapse', () => {
    it('adds the path when absent — result size increases by 1', () => {
      fc.assert(
        fc.property(arbExpandedPaths, arbPath, (expandedPaths, path) => {
          // Pre-condition: path is NOT in the set
          fc.pre(!expandedPaths.has(path));

          const result = applyToggle(expandedPaths, path);

          expect(result.size).toBe(expandedPaths.size + 1);
          expect(result.has(path)).toBe(true);
        }),
        { numRuns: 100 },
      );
    });

    it('removes the path when present — result size decreases by 1', () => {
      fc.assert(
        fc.property(arbExpandedPaths, arbPath, (expandedPaths, path) => {
          // Ensure path IS in the set
          const withPath = new Set(expandedPaths);
          withPath.add(path);

          const result = applyToggle(withPath, path);

          expect(result.size).toBe(withPath.size - 1);
          expect(result.has(path)).toBe(false);
        }),
        { numRuns: 100 },
      );
    });

    it('result differs from original by exactly one element', () => {
      fc.assert(
        fc.property(arbExpandedPaths, arbPath, (expandedPaths, path) => {
          const result = applyToggle(expandedPaths, path);

          // Symmetric difference should contain exactly one element
          const added = [...result].filter((p) => !expandedPaths.has(p));
          const removed = [...expandedPaths].filter((p) => !result.has(p));
          const symmetricDiffSize = added.length + removed.length;

          expect(symmetricDiffSize).toBe(1);
        }),
        { numRuns: 100 },
      );
    });

    it('double toggle is an identity (round-trip)', () => {
      fc.assert(
        fc.property(arbExpandedPaths, arbPath, (expandedPaths, path) => {
          const afterFirst = applyToggle(expandedPaths, path);
          const afterSecond = applyToggle(afterFirst, path);

          // Should be back to the original set
          expect(afterSecond.size).toBe(expandedPaths.size);
          for (const p of expandedPaths) {
            expect(afterSecond.has(p)).toBe(true);
          }
          for (const p of afterSecond) {
            expect(expandedPaths.has(p)).toBe(true);
          }
        }),
        { numRuns: 100 },
      );
    });
  });

  // Feature: swarmws-explorer-ux, Property 6: Focus Mode State Transformation
  // **Validates: Requirements 12.1, 12.2, 12.3**
  describe('Property 6: Focus Mode State Transformation', () => {
    /** Generates a project subfolder TreeNode under Projects/. */
    const arbProjectName = fc
      .array(fc.constantFrom('a', 'b', 'c', 'd', 'e', '1', '2', '3'), {
        minLength: 2,
        maxLength: 8,
      })
      .map((chars) => chars.join(''))
      .filter((s) => /^[a-e]/.test(s));

    /** Generates child nodes (files/subdirectories) for a project folder. */
    const arbProjectChild = (parentPath: string): fc.Arbitrary<TreeNode> =>
      fc.oneof(
        fc.constant<TreeNode>({
          name: 'README.md',
          path: `${parentPath}/README.md`,
          type: 'file',
        }),
        fc.constant<TreeNode>({
          name: 'context-files',
          path: `${parentPath}/context-files`,
          type: 'directory',
          children: [
            {
              name: 'notes.md',
              path: `${parentPath}/context-files/notes.md`,
              type: 'file',
            },
          ],
        }),
        fc.constant<TreeNode>({
          name: 'research',
          path: `${parentPath}/research`,
          type: 'directory',
          children: [],
        }),
      );

    /** Generates a single project directory node. */
    const arbProjectNode = (name: string): fc.Arbitrary<TreeNode> => {
      const projectPath = `Projects/${name}`;
      return fc.array(arbProjectChild(projectPath), { minLength: 0, maxLength: 3 }).map(
        (children): TreeNode => ({
          name,
          path: projectPath,
          type: 'directory',
          children,
        }),
      );
    };

    /**
     * Generates a workspace tree with Knowledge/ and Projects/ containing
     * 2–6 project subfolders. Returns { tree, projectNames }.
     */
    const arbWorkspaceWithProjects = fc
      .uniqueArray(arbProjectName, { minLength: 2, maxLength: 6 })
      .chain((projectNames) => {
        const projectNodeArbs = projectNames.map((name) => arbProjectNode(name));
        return fc.tuple(fc.constant(projectNames), ...projectNodeArbs);
      })
      .map(([projectNames, ...projectNodes]) => {
        const names = projectNames as string[];
        const knowledgeNode: TreeNode = {
          name: 'Knowledge',
          path: 'Knowledge',
          type: 'directory',
          children: [
            {
              name: 'Library',
              path: 'Knowledge/Library',
              type: 'directory',
              children: [],
            },
            {
              name: 'Notes',
              path: 'Knowledge/Notes',
              type: 'directory',
              children: [],
            },
          ],
        };

        const projectsNode: TreeNode = {
          name: 'Projects',
          path: 'Projects',
          type: 'directory',
          children: projectNodes as TreeNode[],
        };

        const tree: TreeNode[] = [knowledgeNode, projectsNode];
        return { tree, projectNames: names };
      });

    it('(a) active project path and "Projects" ancestor are in expandedPaths', () => {
      fc.assert(
        fc.property(
          arbWorkspaceWithProjects.chain(({ tree, projectNames }) => {
            // Pick a random active project from the generated names
            return fc.tuple(
              fc.constant(tree),
              fc.constant(projectNames),
              fc.constantFrom(...projectNames),
            );
          }),
          ([tree, _projectNames, activeProjectId]) => {
            const result = computeFocusModeExpandedPaths(tree, activeProjectId);

            // "Projects" ancestor must be expanded
            expect(result.has('Projects')).toBe(true);
            // Active project path must be expanded
            expect(result.has(`Projects/${activeProjectId}`)).toBe(true);
          },
        ),
        { numRuns: 100 },
      );
    });

    it('(b) no non-active project paths under Projects/ are in expandedPaths', () => {
      fc.assert(
        fc.property(
          arbWorkspaceWithProjects.chain(({ tree, projectNames }) => {
            return fc.tuple(
              fc.constant(tree),
              fc.constant(projectNames),
              fc.constantFrom(...projectNames),
            );
          }),
          ([tree, projectNames, activeProjectId]) => {
            const result = computeFocusModeExpandedPaths(tree, activeProjectId);

            const otherProjects = projectNames.filter((n) => n !== activeProjectId);
            for (const other of otherProjects) {
              const otherPath = `Projects/${other}`;
              // Non-active project root must NOT be expanded
              expect(result.has(otherPath)).toBe(false);
              // No descendant paths of non-active projects should be expanded
              for (const p of result) {
                expect(p.startsWith(`${otherPath}/`)).toBe(false);
              }
            }
          },
        ),
        { numRuns: 100 },
      );
    });

    it('(c) "Knowledge" is NOT in expandedPaths', () => {
      fc.assert(
        fc.property(
          arbWorkspaceWithProjects.chain(({ tree, projectNames }) => {
            return fc.tuple(
              fc.constant(tree),
              fc.constant(projectNames),
              fc.constantFrom(...projectNames),
            );
          }),
          ([tree, _projectNames, activeProjectId]) => {
            const result = computeFocusModeExpandedPaths(tree, activeProjectId);

            // Knowledge must NOT be in expandedPaths (visible but collapsed)
            expect(result.has('Knowledge')).toBe(false);
          },
        ),
        { numRuns: 100 },
      );
    });
  });

  // Feature: swarmws-explorer-ux, Property 8: Search Match, Expand, and Highlight
  // **Validates: Requirements 13.2, 13.3, 13.4**
  describe('Property 8: Search Match, Expand, and Highlight', () => {
    // ── Generators ─────────────────────────────────────────────────────

    /** Generates a short alphabetic name suitable for tree nodes. */
    const arbNodeName = fc
      .array(fc.constantFrom('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'), {
        minLength: 1,
        maxLength: 6,
      })
      .map((chars) => chars.join(''));

    /** Fixes child paths to be correctly nested under the parent path. */
    function fixPaths(node: TreeNode, parentPath: string, index: number): TreeNode {
      const uniqueName = `${node.name}${index}`;
      const fixedPath = parentPath ? `${parentPath}/${uniqueName}` : uniqueName;
      return {
        ...node,
        name: uniqueName,
        path: fixedPath,
        children: node.children?.map((child, i) => fixPaths(child, fixedPath, i)),
      };
    }

    /** Generates a leaf file TreeNode. */
    const arbFileNode = (parentPath: string): fc.Arbitrary<TreeNode> =>
      arbNodeName.map((name): TreeNode => ({
        name,
        path: parentPath ? `${parentPath}/${name}` : name,
        type: 'file',
      }));

    /** Generates a directory TreeNode with 0–3 file children (depth 1). */
    const arbShallowDir = (parentPath: string): fc.Arbitrary<TreeNode> =>
      fc.tuple(
        arbNodeName,
        fc.array(arbFileNode('__placeholder__'), { minLength: 0, maxLength: 3 }),
      ).map(([name, children]): TreeNode => {
        const dirPath = parentPath ? `${parentPath}/${name}` : name;
        const fixedChildren = children.map((child, i) => fixPaths(child, dirPath, i));
        return {
          name,
          path: dirPath,
          type: 'directory',
          children: fixedChildren,
        };
      });

    /** Generates a directory with nested subdirectories (depth 2). */
    const arbDeepDir = (parentPath: string): fc.Arbitrary<TreeNode> =>
      fc.tuple(
        arbNodeName,
        fc.array(
          fc.oneof(arbFileNode('__placeholder__'), arbShallowDir('__placeholder__')),
          { minLength: 0, maxLength: 3 },
        ),
      ).map(([name, children]): TreeNode => {
        const dirPath = parentPath ? `${parentPath}/${name}` : name;
        const fixedChildren = children.map((child, i) => fixPaths(child, dirPath, i));
        return {
          name,
          path: dirPath,
          type: 'directory',
          children: fixedChildren,
        };
      });

    /** Generates a random tree with 1–5 root nodes, depth up to 2. */
    const arbTree: fc.Arbitrary<TreeNode[]> = fc
      .array(
        fc.oneof(arbFileNode(''), arbShallowDir(''), arbDeepDir('')),
        { minLength: 1, maxLength: 5 },
      )
      .map((nodes) => nodes.map((node, i) => fixPaths(node, '', i)));

    /** Collects all nodes from a tree into a flat array. */
    function collectAllNodes(nodes: TreeNode[]): TreeNode[] {
      const result: TreeNode[] = [];
      function walk(node: TreeNode) {
        result.push(node);
        if (node.children) node.children.forEach(walk);
      }
      nodes.forEach(walk);
      return result;
    }

    /** Extracts all ancestor paths for a given node path. */
    function getAncestorPaths(nodePath: string): string[] {
      const segments = nodePath.split('/');
      const ancestors: string[] = [];
      for (let i = 1; i < segments.length; i++) {
        ancestors.push(segments.slice(0, i).join('/'));
      }
      return ancestors;
    }

    /**
     * Generates a tree and a query that is guaranteed to match at least one
     * node name (picks a substring from a random node's name).
     */
    const arbTreeAndMatchingQuery: fc.Arbitrary<{ tree: TreeNode[]; query: string }> =
      arbTree.chain((tree) => {
        const allNodes = collectAllNodes(tree);
        if (allNodes.length === 0) return fc.constant({ tree, query: 'x' });

        return fc.constantFrom(...allNodes).chain((node: TreeNode) => {
          const name = node.name;
          if (name.length === 0) return fc.constant({ tree, query: 'a' });

          // Pick a random substring of the node's name
          return fc.integer({ min: 0, max: name.length - 1 }).chain((start: number) => {
            return fc.integer({ min: start + 1, max: name.length }).map((end: number) => ({
              tree,
              query: name.slice(start, end),
            }));
          });
        });
      });

    it('(a) matchedPaths contains every node whose name contains the query', () => {
      fc.assert(
        fc.property(arbTreeAndMatchingQuery, ({ tree, query }) => {
          const { matched } = findMatches(tree, query);
          const allNodes = collectAllNodes(tree);

          // Every node whose name contains the query must be in matched
          for (const node of allNodes) {
            if (substringMatch(node.name, query)) {
              expect(matched.has(node.path)).toBe(true);
            }
          }
        }),
        { numRuns: 100 },
      );
    });

    it('(b) ancestors includes all ancestor paths of every matched node', () => {
      fc.assert(
        fc.property(arbTreeAndMatchingQuery, ({ tree, query }) => {
          const { matched, ancestors } = findMatches(tree, query);

          // For every matched node, all its ancestor paths must be in ancestors
          for (const matchedPath of matched) {
            const ancestorPaths = getAncestorPaths(matchedPath);
            for (const ap of ancestorPaths) {
              expect(ancestors.has(ap)).toBe(true);
            }
          }
        }),
        { numRuns: 100 },
      );
    });

    it('(c) no node whose name does NOT contain the query is in matchedPaths', () => {
      fc.assert(
        fc.property(arbTreeAndMatchingQuery, ({ tree, query }) => {
          const { matched } = findMatches(tree, query);
          const allNodes = collectAllNodes(tree);

          // No non-matching node should be in matched
          for (const node of allNodes) {
            if (!substringMatch(node.name, query)) {
              expect(matched.has(node.path)).toBe(false);
            }
          }
        }),
        { numRuns: 100 },
      );
    });
  });

  // Feature: swarmws-explorer-ux, Property 10: Session State Round-Trip
  // **Validates: Requirements 10.5**
  describe('Property 10: Session State Round-Trip', () => {
    beforeEach(() => {
      sessionStorage.clear();
    });

    /** Generates a random ExplorerSessionState. */
    const arbSessionState: fc.Arbitrary<ExplorerSessionState> = fc.record({
      expandedPaths: fc.uniqueArray(arbPath, { maxLength: 20 }),
      focusMode: fc.boolean(),
      activeProjectId: fc.oneof(fc.constant(null), arbPath),
    });

    it('serialize → deserialize produces identical state', () => {
      fc.assert(
        fc.property(arbSessionState, (state) => {
          saveSessionState(state);
          const restored = loadSessionState();

          expect(restored).not.toBeNull();
          expect(restored!.expandedPaths).toEqual(state.expandedPaths);
          expect(restored!.focusMode).toBe(state.focusMode);
          expect(restored!.activeProjectId).toBe(state.activeProjectId);
        }),
        { numRuns: 100 },
      );
    });
  });
});
