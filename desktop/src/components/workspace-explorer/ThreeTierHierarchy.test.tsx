/**
 * TDD acceptance tests for the 3-tier visual hierarchy redesign.
 *
 * Acceptance Criteria from EVALUATE:
 * 1. Knowledge section as primary section header (13.5px bold, book SVG icon, yellow accent bg, left border, count pill) — no duplicate "Knowledge" folder row
 * 2. Projects section as primary section header (folder SVG icon, blue accent bg, left border, count pill) — no duplicate "Projects" folder row
 * 3. System section dimmed (opacity 0.5, hover 0.7), collapsed by default
 * 4. Attachments and Services as secondary plain tree dirs under "Other" label
 * 5. Section dividers 2px solid #222036 (mapped to CSS var) between all tiers
 * 6. Left nav emoji icons replaced with SVG stroke icons
 * 7. Working Files styled as card (rounded bg + border)
 * 8. All existing interactions preserved
 * 9. react-window virtualization performance preserved
 */

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, cleanup, screen, fireEvent } from '@testing-library/react';
import type { TreeNode } from '../../types';

// ─────────────────────────────────────────────────────────────────────────────
// Test 1-4: flattenTree with section headers
// ─────────────────────────────────────────────────────────────────────────────

import { flattenTree, EXPLORER_SECTIONS, SYSTEM_NAMES } from './VirtualizedTree';
import type { FlattenedRow } from './VirtualizedTree';

/** Standard workspace tree fixture. */
function makeWorkspaceTree(): TreeNode[] {
  return [
    { name: 'readme.md', path: 'readme.md', type: 'file' },
    {
      name: 'Knowledge',
      path: 'Knowledge',
      type: 'directory',
      children: [
        { name: 'Notes', path: 'Knowledge/Notes', type: 'directory', children: [] },
        { name: 'Library', path: 'Knowledge/Library', type: 'directory', children: [] },
        { name: 'intro.md', path: 'Knowledge/intro.md', type: 'file' },
      ],
    },
    {
      name: 'Projects',
      path: 'Projects',
      type: 'directory',
      children: [
        { name: 'alpha', path: 'Projects/alpha', type: 'directory', children: [] },
        { name: 'beta', path: 'Projects/beta', type: 'directory', children: [] },
      ],
    },
    {
      name: 'Attachments',
      path: 'Attachments',
      type: 'directory',
      children: [{ name: 'file.pdf', path: 'Attachments/file.pdf', type: 'file' }],
    },
    {
      name: 'Services',
      path: 'Services',
      type: 'directory',
      children: [],
    },
    { name: '.context', path: '.context', type: 'directory', children: [] },
    { name: '.claude', path: '.claude', type: 'directory', children: [] },
    { name: 'config.json', path: 'config.json', type: 'file' },
    { name: 'proactive_state.json', path: 'proactive_state.json', type: 'file' },
  ];
}

describe('AC1: Knowledge section-header', () => {
  it('emits a section-header row with label "Knowledge" and no duplicate Knowledge folder row', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Knowledge']));

    // Find the Knowledge section-header
    const sectionHeaders = rows.filter(
      (r) => r.kind === 'section-header' && r.label === 'Knowledge',
    );
    expect(sectionHeaders.length).toBe(1);

    // There should be NO node row with path === 'Knowledge'
    const knowledgeFolderRows = rows.filter(
      (r) => r.kind === 'node' && r.node.path === 'Knowledge',
    );
    expect(knowledgeFolderRows.length).toBe(0);
  });

  it('Knowledge children appear at depth 0 directly after the section-header', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Knowledge']));

    const headerIdx = rows.findIndex(
      (r) => r.kind === 'section-header' && r.label === 'Knowledge',
    );
    expect(headerIdx).toBeGreaterThanOrEqual(0);

    // Next row should be a Knowledge child at depth 0
    const nextRow = rows[headerIdx + 1];
    expect(nextRow.kind).toBe('node');
    if (nextRow.kind === 'node') {
      expect(nextRow.node.path.startsWith('Knowledge/')).toBe(true);
      expect(nextRow.depth).toBe(0);
    }
  });

  it('section-header includes childCount matching number of Knowledge root children', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Knowledge']));

    const header = rows.find(
      (r) => r.kind === 'section-header' && r.label === 'Knowledge',
    );
    expect(header).toBeDefined();
    if (header && header.kind === 'section-header') {
      // Knowledge has 3 children: Notes, Library, intro.md
      expect(header.childCount).toBe(3);
    }
  });

  it('Knowledge section-header has yellow accent config', () => {
    const knowledgeSection = EXPLORER_SECTIONS.find((s) => s.label === 'Knowledge');
    expect(knowledgeSection).toBeDefined();
    expect(knowledgeSection!.accentBg).toContain('234,179,8');
    expect(knowledgeSection!.accentBorder).toContain('234,179,8');
  });
});

describe('AC2: Projects section-header', () => {
  it('emits a section-header row with label "Projects" and no duplicate Projects folder row', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Projects']));

    const sectionHeaders = rows.filter(
      (r) => r.kind === 'section-header' && r.label === 'Projects',
    );
    expect(sectionHeaders.length).toBe(1);

    const projectsFolderRows = rows.filter(
      (r) => r.kind === 'node' && r.node.path === 'Projects',
    );
    expect(projectsFolderRows.length).toBe(0);
  });

  it('Projects children appear at depth 0 directly after the section-header', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Projects']));

    const headerIdx = rows.findIndex(
      (r) => r.kind === 'section-header' && r.label === 'Projects',
    );
    const nextRow = rows[headerIdx + 1];
    expect(nextRow.kind).toBe('node');
    if (nextRow.kind === 'node') {
      expect(nextRow.node.path.startsWith('Projects/')).toBe(true);
      expect(nextRow.depth).toBe(0);
    }
  });

  it('Projects section-header has blue accent config', () => {
    const projectsSection = EXPLORER_SECTIONS.find((s) => s.label === 'Projects');
    expect(projectsSection).toBeDefined();
    expect(projectsSection!.accentBg).toContain('59,130,246');
    expect(projectsSection!.accentBorder).toContain('59,130,246');
  });
});

describe('AC3: System section dimmed and collapsed by default', () => {
  it('emits a section-header for System with dimmed:true and defaultCollapsed:true', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set());

    const systemHeader = rows.find(
      (r) => r.kind === 'section-header' && r.label === 'System',
    );
    expect(systemHeader).toBeDefined();
    if (systemHeader && systemHeader.kind === 'section-header') {
      expect(systemHeader.dimmed).toBe(true);
      expect(systemHeader.defaultCollapsed).toBe(true);
    }
  });

  it('System items are hidden when section is collapsed (default)', () => {
    const tree = makeWorkspaceTree();
    // Default: System is collapsed, so no section collapse override
    const rows = flattenTree(tree, new Set(), new Set(), { System: true });

    const systemNodes = rows.filter(
      (r) => r.kind === 'node' && SYSTEM_NAMES.has(r.node.name),
    );
    expect(systemNodes.length).toBe(0);
  });

  it('System items appear when section is explicitly expanded', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(), new Set(), { System: false });

    const systemNodes = rows.filter(
      (r) => r.kind === 'node' && SYSTEM_NAMES.has(r.node.name),
    );
    expect(systemNodes.length).toBeGreaterThan(0);
  });
});

describe('AC4: Attachments and Services under "Other" label', () => {
  it('emits a secondary-label "Other" row before Attachments and Services', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set());

    const otherLabel = rows.find(
      (r) => r.kind === 'secondary-label' && r.label === 'Other',
    );
    expect(otherLabel).toBeDefined();

    const otherIdx = rows.indexOf(otherLabel!);
    // Attachments and Services should follow the Other label
    const attachmentsIdx = rows.findIndex(
      (r) => r.kind === 'node' && r.node.name === 'Attachments',
    );
    const servicesIdx = rows.findIndex(
      (r) => r.kind === 'node' && r.node.name === 'Services',
    );

    expect(attachmentsIdx).toBeGreaterThan(otherIdx);
    expect(servicesIdx).toBeGreaterThan(otherIdx);
  });

  it('no secondary-label "Other" when there are no remaining dirs', () => {
    // Tree with only Knowledge, Projects, and system items
    const tree: TreeNode[] = [
      { name: 'Knowledge', path: 'Knowledge', type: 'directory', children: [] },
      { name: 'Projects', path: 'Projects', type: 'directory', children: [] },
      { name: '.context', path: '.context', type: 'directory', children: [] },
    ];
    const rows = flattenTree(tree, new Set());

    const otherLabel = rows.find(
      (r) => r.kind === 'secondary-label',
    );
    expect(otherLabel).toBeUndefined();
  });
});

describe('AC5: Section ordering and dividers', () => {
  it('sections appear in order: root files, Knowledge, Projects, Other, System', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(), new Set(), { System: false });

    const knowledgeIdx = rows.findIndex(
      (r) => r.kind === 'section-header' && r.label === 'Knowledge',
    );
    const projectsIdx = rows.findIndex(
      (r) => r.kind === 'section-header' && r.label === 'Projects',
    );
    const otherIdx = rows.findIndex(
      (r) => r.kind === 'secondary-label' && r.label === 'Other',
    );
    const systemIdx = rows.findIndex(
      (r) => r.kind === 'section-header' && r.label === 'System',
    );

    expect(knowledgeIdx).toBeLessThan(projectsIdx);
    expect(projectsIdx).toBeLessThan(otherIdx);
    expect(otherIdx).toBeLessThan(systemIdx);
  });
});

describe('AC8: Existing interactions preserved', () => {
  it('node rows retain kind "node" with depth, isExpanded, and isMatched', () => {
    const tree = makeWorkspaceTree();
    const expandedPaths = new Set(['Knowledge', 'Knowledge/Notes']);
    const matchedPaths = new Set(['Knowledge/Notes']);
    const rows = flattenTree(tree, expandedPaths, matchedPaths);

    const notesRow = rows.find(
      (r) => r.kind === 'node' && r.node.path === 'Knowledge/Notes',
    );
    expect(notesRow).toBeDefined();
    if (notesRow && notesRow.kind === 'node') {
      expect(notesRow.isExpanded).toBe(true);
      expect(notesRow.isMatched).toBe(true);
      // Under the 3-tier hierarchy, Knowledge children are at depth 0
      // (the section-header replaces the Knowledge folder row)
      expect(notesRow.depth).toBe(0);
    }
  });

  it('creating rows still work with section-header based layout', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Knowledge']));

    // Verify we have node rows that could be targets for creating phantom rows
    const nodeRows = rows.filter((r) => r.kind === 'node');
    expect(nodeRows.length).toBeGreaterThan(0);
  });
});

describe('AC9: FlattenedRow type includes section-header and secondary-label', () => {
  it('section-header rows have required fields: kind, label, childCount, isCollapsed, dimmed, defaultCollapsed, config', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set());

    const header = rows.find((r) => r.kind === 'section-header') as Extract<
      FlattenedRow,
      { kind: 'section-header' }
    >;
    expect(header).toBeDefined();
    expect(header.label).toBeDefined();
    expect(typeof header.childCount).toBe('number');
    expect(typeof header.isCollapsed).toBe('boolean');
    expect(header.config).toBeDefined();
  });

  it('secondary-label rows have required fields: kind, label', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set());

    const label = rows.find((r) => r.kind === 'secondary-label') as Extract<
      FlattenedRow,
      { kind: 'secondary-label' }
    >;
    expect(label).toBeDefined();
    expect(label.label).toBe('Other');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: EXPLORER_SECTIONS configuration
// ─────────────────────────────────────────────────────────────────────────────

describe('EXPLORER_SECTIONS configuration', () => {
  it('has Knowledge and Projects sections defined', () => {
    expect(EXPLORER_SECTIONS.find((s) => s.label === 'Knowledge')).toBeDefined();
    expect(EXPLORER_SECTIONS.find((s) => s.label === 'Projects')).toBeDefined();
  });

  it('Knowledge maps to Knowledge/ path', () => {
    const k = EXPLORER_SECTIONS.find((s) => s.label === 'Knowledge')!;
    expect(k.paths).toContain('Knowledge');
  });

  it('Projects maps to Projects/ path', () => {
    const p = EXPLORER_SECTIONS.find((s) => s.label === 'Projects')!;
    expect(p.paths).toContain('Projects');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: Section collapse with Knowledge expanded children at depth
// ─────────────────────────────────────────────────────────────────────────────

describe('Section collapse hides children', () => {
  it('Knowledge section collapsed hides its children', () => {
    const tree = makeWorkspaceTree();
    // Even if Knowledge folder is "expanded" in expandedPaths, section collapse hides children
    const rows = flattenTree(tree, new Set(['Knowledge']), new Set(), { Knowledge: true });

    const header = rows.find(
      (r) => r.kind === 'section-header' && r.label === 'Knowledge',
    );
    expect(header).toBeDefined();
    if (header && header.kind === 'section-header') {
      expect(header.isCollapsed).toBe(true);
    }

    // No Knowledge children should appear
    const knowledgeChildren = rows.filter(
      (r) => r.kind === 'node' && r.node.path.startsWith('Knowledge/'),
    );
    expect(knowledgeChildren.length).toBe(0);
  });

  it('Projects section expanded shows its children', () => {
    const tree = makeWorkspaceTree();
    const rows = flattenTree(tree, new Set(['Projects']), new Set(), { Projects: false });

    const projectChildren = rows.filter(
      (r) => r.kind === 'node' && r.node.path.startsWith('Projects/'),
    );
    expect(projectChildren.length).toBeGreaterThan(0);
  });
});
