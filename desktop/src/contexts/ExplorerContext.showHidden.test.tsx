/**
 * ExplorerContext — show-hidden and first-fetch depth (design
 * workspace-working-location.md v5 §2.1 / §4).
 *
 * Methodology: the real provider over a mocked `workspaceService`; a probe component
 * reads `useTreeData()`.
 * Invariants: root dot folders other than `.context` / `.claude` are left out until
 * `setShowHidden(true)`; deeper dot entries are never filtered; the first fetch is
 * `SWARMWS_TREE_DEPTH` deep; refresh asks for a fresh scan.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';

const getTree = vi.fn();
const refreshTree = vi.fn();
vi.mock('../services/workspace', () => ({
  workspaceService: {
    getTree: (...a: unknown[]) => getTree(...a),
    refreshTree: (...a: unknown[]) => refreshTree(...a),
    setCachedTree: () => {},
    expandDirectory: async () => [],
  },
}));

import { ExplorerProvider, useTreeData, SWARMWS_TREE_DEPTH, isHiddenRootEntry } from './ExplorerContext';

const TREE = [
  { name: '.artifacts', path: '.artifacts', type: 'directory', children: null },
  { name: '.context', path: '.context', type: 'directory', children: null },
  { name: 'Knowledge', path: 'Knowledge', type: 'directory', children: [
    { name: '.notes', path: 'Knowledge/.notes', type: 'file' }] },
];

let api: ReturnType<typeof useTreeData> | null = null;
function Probe() {
  api = useTreeData();
  return <div data-testid="names">{api.treeData.map((n) => n.name).join(',')}</div>;
}

describe('ExplorerContext show-hidden', () => {
  it('hides root dot folders except the System ones until shown', async () => {
    getTree.mockResolvedValue(TREE);
    refreshTree.mockResolvedValue(TREE);
    render(<ExplorerProvider><Probe /></ExplorerProvider>);
    await waitFor(() => expect(screen.getByTestId('names').textContent).toBe('.context,Knowledge'));
    expect(getTree).toHaveBeenCalledWith(SWARMWS_TREE_DEPTH);
    act(() => api!.setShowHidden(true));
    expect(screen.getByTestId('names').textContent).toBe('.artifacts,.context,Knowledge');
    await act(async () => { await api!.refreshTree(); });
    expect(refreshTree).toHaveBeenCalledWith(SWARMWS_TREE_DEPTH);
  });

  it('isHiddenRootEntry', () => {
    expect(isHiddenRootEntry({ name: '.git-recovery-backup', path: '.git-recovery-backup', type: 'directory' })).toBe(true);
    expect(isHiddenRootEntry({ name: '.claude', path: '.claude', type: 'directory' })).toBe(false);
    expect(isHiddenRootEntry({ name: 'Notes', path: 'Notes', type: 'directory' })).toBe(false);
  });
});
