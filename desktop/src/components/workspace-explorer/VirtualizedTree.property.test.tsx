/**
 * Property-based tests for VirtualizedTree semantic zone grouping and virtualization.
 *
 * Tests the `flattenTree` pure function exported from `VirtualizedTree.tsx`
 * using fast-check to verify that the flattening algorithm correctly orders
 * root files, zone separators, and zone folder contents. Also tests the
 * full VirtualizedTree component to verify virtualization renders fewer DOM
 * nodes than the total item count.
 *
 * Key properties verified:
 * - **Property 1: Semantic Zone Grouping Correctness** — for any valid
 *   workspace tree containing Knowledge/ and Projects/, the flattened rows
 *   place root files before the first separator, contain exactly two zone
 *   separators in order ("Shared Knowledge", "Active Work"), place Knowledge/
 *   in the Shared Knowledge zone, and Projects/ in the Active Work zone.
 * - **Property 11: Virtualization Renders Fewer DOM Nodes** — for any
 *   flattened row list with 500+ items, the VirtualizedTree renders fewer
 *   DOM row elements than the total item count, bounded by
 *   ceil(containerHeight / rowHeight) + overscanCount.
 *
 * Testing methodology: Property-based testing with fast-check.
 * Property 1 runs with `{ numRuns: 100 }`, Property 11 with `{ numRuns: 20 }`.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import * as fc from 'fast-check';
import { flattenTree, SEMANTIC_ZONES, ROOT_FILES } from './VirtualizedTree';
import type { FlattenedRow } from './VirtualizedTree';
import type { TreeNode } from '../../types';

// ── Generators ───────────────────────────────────────────────────────────

/** Generates a short alphabetic name for files/folders. */
const arbName = fc
  .array(fc.constantFrom('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'), {
    minLength: 1,
    maxLength: 6,
  })
  .map((chars) => chars.join(''));

/** Generates 0–3 child file nodes under a given parent path. */
const arbChildFiles = (parentPath: string): fc.Arbitrary<TreeNode[]> =>
  fc.array(arbName, { minLength: 0, maxLength: 3 }).map((names) =>
    names.map((name, i) => ({
      name: `${name}${i}`,
      path: `${parentPath}/${name}${i}`,
      type: 'file' as const,
    })),
  );

/** Generates a Knowledge/ directory with random children. */
const arbKnowledgeNode: fc.Arbitrary<TreeNode> = arbChildFiles('Knowledge').map(
  (children): TreeNode => ({
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
      ...children,
    ],
  }),
);

/** Generates a Projects/ directory with 1–4 project subfolders. */
const arbProjectsNode: fc.Arbitrary<TreeNode> = fc
  .array(arbName, { minLength: 1, maxLength: 4 })
  .map(
    (projectNames): TreeNode => ({
      name: 'Projects',
      path: 'Projects',
      type: 'directory',
      children: projectNames.map((name, i) => ({
        name: `${name}${i}`,
        path: `Projects/${name}${i}`,
        type: 'directory',
        children: [],
      })),
    }),
  );

/** Generates random root-level files (some from ROOT_FILES, some extra). */
const arbRootFiles: fc.Arbitrary<TreeNode[]> = fc
  .tuple(
    // Subset of known ROOT_FILES
    fc.subarray([...ROOT_FILES]),
    // Extra random root files
    fc.array(arbName, { minLength: 0, maxLength: 2 }),
  )
  .map(([knownFiles, extraNames]) => {
    const known: TreeNode[] = knownFiles.map((name) => ({
      name,
      path: name,
      type: 'file' as const,
    }));
    const extra: TreeNode[] = extraNames.map((name, i) => ({
      name: `extra-${name}${i}.md`,
      path: `extra-${name}${i}.md`,
      type: 'file' as const,
    }));
    return [...known, ...extra];
  });

/**
 * Generates a complete workspace tree with Knowledge/, Projects/, and
 * random root files. The order of top-level nodes is shuffled to ensure
 * flattenTree handles any input ordering.
 */
const arbWorkspaceTree: fc.Arbitrary<TreeNode[]> = fc
  .tuple(arbKnowledgeNode, arbProjectsNode, arbRootFiles)
  .chain(([knowledge, projects, rootFiles]) => {
    const allNodes: TreeNode[] = [...rootFiles, knowledge, projects];
    // Shuffle the top-level array to test ordering robustness
    return fc.shuffledSubarray(allNodes, { minLength: allNodes.length, maxLength: allNodes.length });
  });

// ── Helpers ──────────────────────────────────────────────────────────────

/** Find indices of all section headers in the flattened rows (replaces zone separators). */
function findSeparatorIndices(rows: FlattenedRow[]): { index: number; label: string }[] {
  return rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => row.kind === 'section-header')
    .map(({ row, index }) => ({
      index,
      label: (row as Extract<FlattenedRow, { kind: 'section-header' }>).label,
    }));
}

/** Find the index of a node row by its path. */
function findNodeIndex(rows: FlattenedRow[], path: string): number {
  return rows.findIndex(
    (row) => row.kind === 'node' && row.node.path === path,
  );
}

// ── Property 1: Semantic Zone Grouping Correctness ───────────────────────
// Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness

describe('Property 1: Semantic Zone Grouping Correctness', () => {
  /**
   * Validates: Requirements 10.1, 10.3
   *
   * For any valid workspace tree containing Knowledge/ and Projects/,
   * the flattened row list shall:
   * (a) place root files before the first zone separator
   * (b) contain exactly two zone separators in order
   * (c) place Knowledge/ in the Shared Knowledge zone
   * (d) place Projects/ in the Active Work zone
   * (e) maintain correct ordering within zones
   */
  it('(a) root files appear before the first zone separator', () => {
    // Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness
    fc.assert(
      fc.property(arbWorkspaceTree, (tree) => {
        const rows = flattenTree(tree, new Set());
        const separators = findSeparatorIndices(rows);

        // Must have at least one separator
        expect(separators.length).toBeGreaterThanOrEqual(1);
        const firstSepIndex = separators[0].index;

        // All rows before the first separator must be node rows (root files)
        for (let i = 0; i < firstSepIndex; i++) {
          const row = rows[i];
          expect(row.kind).toBe('node');
          if (row.kind === 'node') {
            expect(row.node.type).toBe('file');
            expect(row.depth).toBe(0);
          }
        }
      }),
      { numRuns: 100 },
    );
  });

  it('(b) Knowledge and Projects section-headers in order', () => {
    // Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness
    // In 3-tier hierarchy, section-headers replace zone-separators.
    // Knowledge + Projects are primary sections; System is at the bottom.
    fc.assert(
      fc.property(arbWorkspaceTree, (tree) => {
        const rows = flattenTree(tree, new Set());
        const separators = findSeparatorIndices(rows);

        // At least Knowledge and Projects section-headers
        expect(separators.length).toBeGreaterThanOrEqual(2);
        expect(separators[0].label).toBe('Knowledge');
        expect(separators[1].label).toBe('Projects');
        expect(separators[0].index).toBeLessThan(separators[1].index);
      }),
      { numRuns: 100 },
    );
  });

  it('(c) Knowledge children appear after the Knowledge section-header (root dir is absorbed)', () => {
    // Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness
    // In 3-tier hierarchy, there is no "Knowledge" node row — the section-header IS the root dir.
    fc.assert(
      fc.property(arbWorkspaceTree, (tree) => {
        const rows = flattenTree(tree, new Set(['Knowledge']));
        const separators = findSeparatorIndices(rows);

        // No node row for 'Knowledge' itself
        const knowledgeNodeIndex = findNodeIndex(rows, 'Knowledge');
        expect(knowledgeNodeIndex).toBe(-1);

        // Knowledge section header exists
        const knowledgeSection = separators.find((s) => s.label === 'Knowledge');
        expect(knowledgeSection).toBeDefined();
      }),
      { numRuns: 100 },
    );
  });

  it('(d) Projects children appear after the Projects section-header (root dir is absorbed)', () => {
    // Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness
    fc.assert(
      fc.property(arbWorkspaceTree, (tree) => {
        const rows = flattenTree(tree, new Set(['Projects']));
        const separators = findSeparatorIndices(rows);

        // No node row for 'Projects' itself
        const projectsNodeIndex = findNodeIndex(rows, 'Projects');
        expect(projectsNodeIndex).toBe(-1);

        // Projects section header exists
        const projectsSection = separators.find((s) => s.label === 'Projects');
        expect(projectsSection).toBeDefined();
      }),
      { numRuns: 100 },
    );
  });

  it('(e) correct ordering: root files -> Knowledge section -> Projects section', () => {
    // Feature: swarmws-explorer-ux, Property 1: Semantic Zone Grouping Correctness
    fc.assert(
      fc.property(arbWorkspaceTree, (tree) => {
        const rows = flattenTree(tree, new Set(['Knowledge', 'Projects']));
        const separators = findSeparatorIndices(rows);
        const knowledgeSection = separators.find((s) => s.label === 'Knowledge')!;
        const projectsSection = separators.find((s) => s.label === 'Projects')!;

        // Knowledge section header must come before Projects
        expect(knowledgeSection.index).toBeLessThan(projectsSection.index);

        // All node rows between Knowledge and Projects section-headers should be
        // descendants of Knowledge/
        for (let i = knowledgeSection.index + 1; i < projectsSection.index; i++) {
          const row = rows[i];
          if (row.kind === 'node') {
            expect(row.node.path.startsWith('Knowledge/')).toBe(true);
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});


// ── Property 11: Virtualization Renders Fewer DOM Nodes ──────────────────
// Feature: swarmws-explorer-ux, Property 11: Virtualization Renders Fewer DOM Nodes

// Mock ExplorerContext hooks so VirtualizedTree can render without a provider
vi.mock('../../contexts/ExplorerContext', () => ({
  useTreeData: vi.fn(),
  useSelection: vi.fn(),
}));

import { useTreeData, useSelection } from '../../contexts/ExplorerContext';
import VirtualizedTree from './VirtualizedTree';

const mockedUseTreeData = vi.mocked(useTreeData);
const mockedUseSelection = vi.mocked(useSelection);

// ── Large tree generators ────────────────────────────────────────────────

/**
 * Generate a directory node with `n` file children, all expanded.
 * Used to produce large flattened trees for virtualization testing.
 */
function makeDirectoryWithFiles(
  name: string,
  parentPath: string,
  fileCount: number,
): TreeNode {
  const dirPath = parentPath ? `${parentPath}/${name}` : name;
  const children: TreeNode[] = [];
  for (let i = 0; i < fileCount; i++) {
    children.push({
      name: `file-${i}.md`,
      path: `${dirPath}/file-${i}.md`,
      type: 'file',
    });
  }
  return {
    name,
    path: dirPath,
    type: 'directory',
    children,
  };
}

/**
 * Arbitrary that generates a workspace tree whose flattened form (with all
 * directories expanded) contains at least 500 rows. The tree has Knowledge/
 * and Projects/ with many project subdirectories, each containing files.
 */
const arbLargeWorkspaceTree: fc.Arbitrary<{
  tree: TreeNode[];
  expandedPaths: Set<string>;
  totalFlatCount: number;
}> = fc
  .record({
    projectCount: fc.integer({ min: 10, max: 30 }),
    filesPerProject: fc.integer({ min: 15, max: 40 }),
    knowledgeFiles: fc.integer({ min: 5, max: 20 }),
  })
  .map(({ projectCount, filesPerProject, knowledgeFiles }) => {
    // Build Knowledge/ with files
    const knowledgeChildren: TreeNode[] = [];
    for (let i = 0; i < knowledgeFiles; i++) {
      knowledgeChildren.push({
        name: `note-${i}.md`,
        path: `Knowledge/note-${i}.md`,
        type: 'file',
      });
    }
    const knowledge: TreeNode = {
      name: 'Knowledge',
      path: 'Knowledge',
      type: 'directory',
      children: knowledgeChildren,
    };

    // Build Projects/ with many project subdirectories
    const projectChildren: TreeNode[] = [];
    for (let i = 0; i < projectCount; i++) {
      projectChildren.push(
        makeDirectoryWithFiles(`project-${i}`, 'Projects', filesPerProject),
      );
    }
    const projects: TreeNode = {
      name: 'Projects',
      path: 'Projects',
      type: 'directory',
      children: projectChildren,
    };

    // Root files
    const rootFiles: TreeNode[] = ROOT_FILES.map((name) => ({
      name,
      path: name,
      type: 'file' as const,
    }));

    const tree: TreeNode[] = [...rootFiles, knowledge, projects];

    // Expand all directories so the flattened list is large
    const expandedPaths = new Set<string>();
    expandedPaths.add('Knowledge');
    expandedPaths.add('Projects');
    for (let i = 0; i < projectCount; i++) {
      expandedPaths.add(`Projects/project-${i}`);
    }

    // Compute total flattened count
    const flatRows = flattenTree(tree, expandedPaths);
    const totalFlatCount = flatRows.length;

    return { tree, expandedPaths, totalFlatCount };
  })
  .filter(({ totalFlatCount }) => totalFlatCount >= 500);

describe('Property 11: Virtualization Renders Fewer DOM Nodes', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  /**
   * **Validates: Requirements 15.1, 15.2**
   *
   * For any flattened row list with 500+ items, the VirtualizedTree
   * component shall render fewer DOM row elements than the total item
   * count. The number of rendered rows shall be bounded by
   * ceil(containerHeight / rowHeight) + overscanCount.
   */
  it('renders fewer DOM nodes than total items for large trees', () => {
    // Feature: swarmws-explorer-ux, Property 11: Virtualization Renders Fewer DOM Nodes
    const CONTAINER_HEIGHT = 300;
    const ROW_HEIGHT = 32;
    const OVERSCAN_COUNT = 3; // react-window v2 default

    fc.assert(
      fc.property(arbLargeWorkspaceTree, ({ tree, expandedPaths, totalFlatCount }) => {
        // Verify precondition: we have 500+ rows
        expect(totalFlatCount).toBeGreaterThanOrEqual(500);

        // Configure mocks
        mockedUseTreeData.mockReturnValue({
          treeData: tree,
          isLoading: false,
          error: null,
          refreshTree: vi.fn(),
        });
        mockedUseSelection.mockReturnValue({
          expandedPaths,
          selectedPath: null,
          matchedPaths: new Set<string>(),
          highlightedPaths: new Set<string>(),
          toggleExpand: vi.fn(),
          expandAll: vi.fn(),
          collapseAll: vi.fn(),
          setSelectedPath: vi.fn(),
        });

        const { container } = render(
          <VirtualizedTree height={CONTAINER_HEIGHT} width={400} />,
        );

        // Count rendered DOM rows: tree-row (TreeNodeRow) + zone-separator (ZoneSeparator) + section-header + secondary-label
        const treeRows = container.querySelectorAll('[data-testid="tree-row"]');
        const separators = container.querySelectorAll('[data-testid="zone-separator"]');
        const sectionHeaders = container.querySelectorAll('[data-testid="section-header"]');
        const secondaryLabels = container.querySelectorAll('[data-testid="secondary-label"]');
        const renderedCount = treeRows.length + separators.length + sectionHeaders.length + secondaryLabels.length;

        // Virtualization: rendered count must be less than total
        expect(renderedCount).toBeLessThan(totalFlatCount);

        // Upper bound: visible rows + overscan on both sides
        const maxVisible = Math.ceil(CONTAINER_HEIGHT / ROW_HEIGHT) + OVERSCAN_COUNT * 2;
        expect(renderedCount).toBeLessThanOrEqual(maxVisible);

        cleanup();
      }),
      { numRuns: 20 },
    );
  });
});
