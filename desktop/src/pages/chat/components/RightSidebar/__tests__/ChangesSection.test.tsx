/**
 * Tests for ChangesSection (✍ 改动) — Run 2 redesign.
 * Focus: gitStatusForPath tree lookup + badge mapping (AC2), written-only (AC1),
 * click dispatches swarm:open-file with autoDiff:true (AC4).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChangesSection, gitStatusForPath } from '../ChangesSection';
import type { TreeNode } from '../../../../types';
import type { ReferencedFile } from '../../../../hooks/useReferencedFiles';

// useTreeData is a context hook — mock it so the component can render standalone.
// Path is relative to THIS test file (one level deeper than ChangesSection.tsx).
vi.mock('../../../../../contexts/ExplorerContext', () => ({
  useTreeData: () => ({ treeData: mockTree, isLoading: false, error: null, refreshTree: vi.fn() }),
}));

let mockTree: TreeNode[] = [];

function file(over: Partial<ReferencedFile>): ReferencedFile {
  return { path: 'a.ts', absolutePath: '/x/a.ts', fileName: 'a.ts', operation: 'written', firstSeen: 0, count: 1, ...over };
}

describe('gitStatusForPath', () => {
  const tree: TreeNode[] = [
    { name: 'src', path: 'src', type: 'directory', children: [
      { name: 'new.ts', path: 'src/new.ts', type: 'file', gitStatus: 'untracked' },
      { name: 'mod.ts', path: 'src/mod.ts', type: 'file', gitStatus: 'modified' },
      { name: 'clean.ts', path: 'src/clean.ts', type: 'file' },
    ] },
  ];

  it('AC2: finds untracked + modified by exact + suffix match', () => {
    expect(gitStatusForPath(tree, 'src/new.ts')).toBe('untracked');
    expect(gitStatusForPath(tree, 'src/mod.ts')).toBe('modified');
    // suffix match: reference path is a shorter tail than tree path
    expect(gitStatusForPath(tree, 'new.ts')).toBe('untracked');
    // ./ prefix normalized
    expect(gitStatusForPath(tree, './src/mod.ts')).toBe('modified');
  });

  it('AC2: a file not in the tree (deep/source-repo) → undefined (no badge, not hidden)', () => {
    expect(gitStatusForPath(tree, 'backend/deep/unknown.py')).toBeUndefined();
  });

  it('AC2: a tracked-but-unmodified file → status undefined (no badge)', () => {
    expect(gitStatusForPath(tree, 'src/clean.ts')).toBeUndefined();
  });
});

describe('ChangesSection', () => {
  beforeEach(() => {
    mockTree = [
      { name: 'src', path: 'src', type: 'directory', children: [
        { name: 'new.ts', path: 'src/new.ts', type: 'file', gitStatus: 'untracked' },
        { name: 'mod.ts', path: 'src/mod.ts', type: 'file', gitStatus: 'modified' },
      ] },
    ];
  });

  const grouped = (written: ReferencedFile[]) => ({
    written,
    read: [file({ path: 'r.ts', fileName: 'r.ts', operation: 'read' })],
    searched: [file({ path: 's.ts', fileName: 's.ts', operation: 'searched' })],
  });

  it('AC1: renders only written files (read/searched never shown)', () => {
    render(<ChangesSection grouped={grouped([file({ path: 'src/new.ts', fileName: 'new.ts' })])} totalCount={3} />);
    expect(screen.getByText('new.ts')).toBeInTheDocument();
    expect(screen.queryByText('r.ts')).not.toBeInTheDocument();
    expect(screen.queryByText('s.ts')).not.toBeInTheDocument();
  });

  it('AC2: shows NEW badge for untracked, UPD for modified', () => {
    render(<ChangesSection grouped={grouped([
      file({ path: 'src/new.ts', fileName: 'new.ts' }),
      file({ path: 'src/mod.ts', fileName: 'mod.ts' }),
    ])} totalCount={2} />);
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(screen.getByText('UPD')).toBeInTheDocument();
  });

  it('AC3: no repeat-count shown even when count>1', () => {
    render(<ChangesSection grouped={grouped([file({ path: 'src/mod.ts', fileName: 'mod.ts', count: 5 })])} totalCount={1} />);
    expect(screen.queryByText('(5)')).not.toBeInTheDocument();
  });

  it('AC4: click dispatches swarm:open-file with autoDiff:true', () => {
    const spy = vi.spyOn(document, 'dispatchEvent');
    render(<ChangesSection grouped={grouped([file({ path: 'src/mod.ts', fileName: 'mod.ts' })])} totalCount={1} />);
    fireEvent.click(screen.getByText('mod.ts'));
    const evt = spy.mock.calls.map((c) => c[0]).find((e) => (e as CustomEvent).type === 'swarm:open-file') as CustomEvent;
    expect(evt).toBeTruthy();
    expect(evt.detail).toEqual({ path: 'src/mod.ts', autoDiff: true });
    spy.mockRestore();
  });

  it('empty written → renders null', () => {
    const { container } = render(<ChangesSection grouped={grouped([])} totalCount={0} />);
    expect(container.firstChild).toBeNull();
  });
});
