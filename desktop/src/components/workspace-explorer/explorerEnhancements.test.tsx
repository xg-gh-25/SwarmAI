/**
 * Tests for the SwarmWS explorer UX enhancements (run_36f2823c):
 *  1. sortSiblings — pure sibling-ordering by SortMode (name-asc/name-desc/git-first/default)
 *  2. flattenTree sortMode threading — explicit sort replaces the built-in date-desc
 *     for Knowledge/Attachments when a non-default sort is chosen; default is unchanged.
 *  3. computeChangedAncestors — ancestor paths of every git-changed file (default-expand-changed)
 *
 * These are pure functions (no DOM, no mocks) — the deterministic core of the feature.
 * Header wiring is covered by ExplorerHeader.test.tsx.
 */
import { describe, it, expect } from 'vitest';
import { flattenTree, sortSiblings } from './VirtualizedTree';
import type { SortMode } from '../../contexts/ExplorerContext';
import { computeChangedAncestors } from '../../contexts/ExplorerContext';
import type { TreeNode } from '../../types';

const dir = (name: string, path: string, children: TreeNode[] | null = []): TreeNode => ({
  name, path, type: 'directory', children,
});
const file = (name: string, path: string, gitStatus?: TreeNode['gitStatus']): TreeNode => ({
  name, path, type: 'file', ...(gitStatus ? { gitStatus } : {}),
});

describe('sortSiblings — pure sibling ordering', () => {
  const nodes: TreeNode[] = [
    file('banana.md', 'X/banana.md'),
    dir('alpha', 'X/alpha'),
    file('apple.md', 'X/apple.md', 'modified'),
    dir('zeta', 'X/zeta'),
    file('cherry.md', 'X/cherry.md'),
  ];

  it('default mode returns the input order unchanged (referential-safe copy ok)', () => {
    const out = sortSiblings(nodes, 'default');
    expect(out.map((n) => n.name)).toEqual(['banana.md', 'alpha', 'apple.md', 'zeta', 'cherry.md']);
  });

  it('name-asc: dirs before files, each group A→Z', () => {
    const out = sortSiblings(nodes, 'name-asc');
    expect(out.map((n) => n.name)).toEqual(['alpha', 'zeta', 'apple.md', 'banana.md', 'cherry.md']);
  });

  it('name-desc: dirs before files, each group Z→A', () => {
    const out = sortSiblings(nodes, 'name-desc');
    expect(out.map((n) => n.name)).toEqual(['zeta', 'alpha', 'cherry.md', 'banana.md', 'apple.md']);
  });

  it('git-first: changed files float above unchanged, dirs still first', () => {
    const out = sortSiblings(nodes, 'git-first');
    // dirs first (A→Z), then changed file(s), then unchanged files (A→Z)
    expect(out.map((n) => n.name)).toEqual(['alpha', 'zeta', 'apple.md', 'banana.md', 'cherry.md']);
  });

  it('never mutates the input array', () => {
    const before = nodes.map((n) => n.name);
    sortSiblings(nodes, 'name-desc');
    expect(nodes.map((n) => n.name)).toEqual(before);
  });
});

describe('flattenTree sortMode threading', () => {
  // A Knowledge tree with date-prefixed items — the built-in date-desc case.
  const tree: TreeNode[] = [
    dir('Knowledge', 'Knowledge', [
      file('2026-08-01-a.md', 'Knowledge/2026-08-01-a.md'),
      file('2026-08-07-b.md', 'Knowledge/2026-08-07-b.md'),
      dir('sub', 'Knowledge/sub', []),
    ]),
    dir('Projects', 'Projects', [
      file('zeta.md', 'Projects/zeta.md'),
      file('alpha.md', 'Projects/alpha.md'),
    ]),
  ];
  const expanded = new Set<string>(); // sections render children at depth 1 regardless

  const names = (mode: SortMode) =>
    flattenTree(tree, expanded, new Set(), {}, mode)
      .filter((r) => r.kind === 'node')
      .map((r) => (r.kind === 'node' ? r.node.name : ''));

  it("default mode preserves the existing date-desc sort for Knowledge (newest first)", () => {
    const out = names('default');
    // date-desc: newest (08-07) before older (08-01); dir 'sub' before files
    expect(out.indexOf('sub')).toBeLessThan(out.indexOf('2026-08-07-b.md'));
    expect(out.indexOf('2026-08-07-b.md')).toBeLessThan(out.indexOf('2026-08-01-a.md'));
  });

  it("name-asc replaces the date-desc sort (explicit user choice wins)", () => {
    const out = names('name-asc');
    // ascending: 08-01 before 08-07 (opposite of the date-desc default)
    expect(out.indexOf('2026-08-01-a.md')).toBeLessThan(out.indexOf('2026-08-07-b.md'));
    // Projects sorted ascending too
    expect(out.indexOf('alpha.md')).toBeLessThan(out.indexOf('zeta.md'));
  });

  it("dirs stay before files under every mode", () => {
    for (const mode of ['default', 'name-asc', 'name-desc', 'git-first'] as SortMode[]) {
      const out = names(mode);
      expect(out.indexOf('sub')).toBeLessThan(out.indexOf('2026-08-01-a.md'));
    }
  });
});

describe('computeChangedAncestors', () => {
  it('returns ancestor dir paths of every git-changed file', () => {
    const tree: TreeNode[] = [
      dir('Knowledge', 'Knowledge', [
        dir('DailyActivity', 'Knowledge/DailyActivity', [
          file('2026-08-07.md', 'Knowledge/DailyActivity/2026-08-07.md', 'modified'),
        ]),
        file('clean.md', 'Knowledge/clean.md'),
      ]),
      dir('Projects', 'Projects', [
        dir('SwarmAI', 'Projects/SwarmAI', [
          file('new.md', 'Projects/SwarmAI/new.md', 'untracked'),
        ]),
      ]),
    ];
    const anc = computeChangedAncestors(tree);
    // ancestors of the two changed files
    expect(anc.has('Knowledge')).toBe(true);
    expect(anc.has('Knowledge/DailyActivity')).toBe(true);
    expect(anc.has('Projects')).toBe(true);
    expect(anc.has('Projects/SwarmAI')).toBe(true);
    // the changed file paths themselves are NOT dir-expand targets
    expect(anc.has('Knowledge/DailyActivity/2026-08-07.md')).toBe(false);
  });

  it('returns empty set when nothing is changed', () => {
    const tree: TreeNode[] = [dir('Knowledge', 'Knowledge', [file('a.md', 'Knowledge/a.md')])];
    expect(computeChangedAncestors(tree).size).toBe(0);
  });

  it('ignores deleted/ignored status (only surfaces live edits: modified/added/untracked)', () => {
    const tree: TreeNode[] = [
      dir('Knowledge', 'Knowledge', [
        file('gone.md', 'Knowledge/gone.md', 'deleted'),
        file('ign.md', 'Knowledge/ign.md', 'ignored'),
      ]),
    ];
    expect(computeChangedAncestors(tree).size).toBe(0);
  });

  it('handles null (lazy-truncated) children without crashing', () => {
    const tree: TreeNode[] = [dir('Projects', 'Projects', null)];
    expect(computeChangedAncestors(tree).size).toBe(0);
  });
});
