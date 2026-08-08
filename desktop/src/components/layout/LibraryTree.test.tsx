/**
 * LibraryTree — rootPath parameterization + noise filter (run_a75197d9).
 *
 * Pins THREE contracts the Brain-Hub DDD-tree reuse depends on:
 *  1. DEFAULT (no rootPath) = the Library bookshelf: getTree(2) + find the top-level
 *     'Knowledge' node. Byte-identical behavior — LibraryOverlay passes no prop.
 *  2. NESTED rootPath ('Projects/<name>') = expandDirectory(rootPath, 2) for the
 *     initial children (the backend tree endpoint has no per-subtree root).
 *  3. NOISE FILTER: infra junk (.artifacts, *.db, *.lock, *-archive.md, __pycache__,
 *     dotfiles, code-intel.json) never renders — a project root shows only real
 *     browsable content, not a 74MB code_intel.db.
 *
 * react-window is real; workspaceService is mocked at the boundary.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import type { TreeNode } from '../../types';

vi.mock('../../services/workspace', () => ({
  workspaceService: { getTree: vi.fn(), expandDirectory: vi.fn() },
}));
import { workspaceService } from '../../services/workspace';
import { LibraryTree } from './LibraryTree';

const getTree = workspaceService.getTree as ReturnType<typeof vi.fn>;
const expandDirectory = workspaceService.expandDirectory as ReturnType<typeof vi.fn>;

const KNOWLEDGE_TREE: TreeNode[] = [
  { name: 'Knowledge', path: 'Knowledge', type: 'directory', children: [
    { name: 'Notes', path: 'Knowledge/Notes', type: 'directory', children: null },
    { name: 'readme.md', path: 'Knowledge/readme.md', type: 'file' },
  ]},
];

// A Projects/<name> subtree WITH noise mixed in (the real-world shape).
const PROJECT_CHILDREN: TreeNode[] = [
  { name: '2-understanding', path: 'Projects/SwarmAI/2-understanding', type: 'directory', children: null },
  { name: 'spec-details', path: 'Projects/SwarmAI/spec-details', type: 'directory', children: null },
  { name: 'AGENTS.md', path: 'Projects/SwarmAI/AGENTS.md', type: 'file' },
  // ── noise that MUST be filtered ──
  { name: '.artifacts', path: 'Projects/SwarmAI/.artifacts', type: 'directory', children: null },
  { name: 'code_intel.db', path: 'Projects/SwarmAI/code_intel.db', type: 'file' },
  { name: 'code_intel.db-wal', path: 'Projects/SwarmAI/code_intel.db-wal', type: 'file' },
  { name: 'code-intel.json', path: 'Projects/SwarmAI/code-intel.json', type: 'file' },
  { name: 'IMPROVEMENT-archive.md', path: 'Projects/SwarmAI/IMPROVEMENT-archive.md', type: 'file' },
  { name: '.project.json', path: 'Projects/SwarmAI/.project.json', type: 'file' },
  { name: 'TECH.md.lock', path: 'Projects/SwarmAI/TECH.md.lock', type: 'file' },
];

beforeEach(() => {
  getTree.mockResolvedValue(KNOWLEDGE_TREE);
  expandDirectory.mockResolvedValue(PROJECT_CHILDREN);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('LibraryTree — rootPath + noise filter', () => {
  it('DEFAULT (no rootPath) = Library bookshelf: getTree + Knowledge node (regression)', async () => {
    render(<LibraryTree />);
    await waitFor(() => expect(screen.getByText('Notes')).toBeInTheDocument());
    expect(getTree).toHaveBeenCalled();
    expect(expandDirectory).not.toHaveBeenCalled();   // single-segment root never fetches directly
    expect(screen.getByText('readme.md')).toBeInTheDocument();
  });

  it('NESTED rootPath (Projects/SwarmAI) loads children via expandDirectory, not getTree-node', async () => {
    render(<LibraryTree rootPath="Projects/SwarmAI" />);
    await waitFor(() => expect(screen.getByText('2-understanding')).toBeInTheDocument());
    expect(expandDirectory).toHaveBeenCalledWith('Projects/SwarmAI', expect.any(Number));
    expect(screen.getByText('spec-details')).toBeInTheDocument();
    expect(screen.getByText('AGENTS.md')).toBeInTheDocument();
  });

  it('NOISE FILTER: .artifacts / *.db / *.db-wal / code-intel.json / *-archive.md / dotfiles / *.lock never render', async () => {
    render(<LibraryTree rootPath="Projects/SwarmAI" />);
    await waitFor(() => expect(screen.getByText('AGENTS.md')).toBeInTheDocument());
    expect(screen.queryByText('.artifacts')).toBeNull();
    expect(screen.queryByText('code_intel.db')).toBeNull();
    expect(screen.queryByText('code_intel.db-wal')).toBeNull();
    expect(screen.queryByText('code-intel.json')).toBeNull();
    expect(screen.queryByText('IMPROVEMENT-archive.md')).toBeNull();
    expect(screen.queryByText('.project.json')).toBeNull();
    expect(screen.queryByText('TECH.md.lock')).toBeNull();
  });

  it('AC2: DEFAULT (no showAllFiles) still filters noise — Library bookshelf unchanged (no regression)', async () => {
    render(<LibraryTree rootPath="Projects/SwarmAI" />);
    await waitFor(() => expect(screen.getByText('AGENTS.md')).toBeInTheDocument());
    // The infra files stay HIDDEN by default (Library must not regress).
    expect(screen.queryByText('code_intel.db')).toBeNull();
    expect(screen.queryByText('.artifacts')).toBeNull();
    expect(screen.queryByText('TECH.md.lock')).toBeNull();
  });

  it('AC1: showAllFiles=true SHOWS infra files (real complete tree) rendered DIMMED, not hidden', async () => {
    render(<LibraryTree rootPath="Projects/SwarmAI" showAllFiles />);
    await waitFor(() => expect(screen.getByText('AGENTS.md')).toBeInTheDocument());
    // Every previously-hidden infra file is now VISIBLE (nothing hidden — user's ask).
    expect(screen.getByText('code_intel.db')).toBeInTheDocument();
    expect(screen.getByText('code_intel.db-wal')).toBeInTheDocument();
    expect(screen.getByText('code-intel.json')).toBeInTheDocument();
    expect(screen.getByText('IMPROVEMENT-archive.md')).toBeInTheDocument();
    expect(screen.getByText('.project.json')).toBeInTheDocument();
    expect(screen.getByText('TECH.md.lock')).toBeInTheDocument();
    // …and dimmed: TreeNodeRow applies the hidden text color to a forceDim'd row.
    // The infra file's row text uses --color-hidden-text (dim), the normal file does not.
    const infraRow = screen.getByText('code_intel.db').closest('.tree-node-row') as HTMLElement;
    const normalRow = screen.getByText('AGENTS.md').closest('.tree-node-row') as HTMLElement;
    // dim rows carry reduced opacity (0.7) vs 1 for a normal row (TreeNodeRow rowOpacity).
    expect(infraRow.style.opacity).toBe('0.7');
    expect(normalRow.style.opacity).toBe('1');
  });

  it('AC3: maxWidth bounds the tree column (not full-width)', async () => {
    render(<LibraryTree rootPath="Projects/SwarmAI" maxWidth="420px" />);
    await waitFor(() => expect(screen.getByText('AGENTS.md')).toBeInTheDocument());
    expect((screen.getByTestId('library-tree') as HTMLElement).style.maxWidth).toBe('420px');
  });

  it('empty/aria copy derives from the root leaf (not hardcoded "Knowledge")', async () => {
    expandDirectory.mockResolvedValue([]);   // genuinely empty project root
    render(<LibraryTree rootPath="Projects/SwarmAI" />);
    await waitFor(() => expect(screen.getByTestId('library-tree-empty')).toBeInTheDocument());
    expect(screen.getByTestId('library-tree-empty').textContent).toContain('SwarmAI');
    expect(screen.getByTestId('library-tree-empty').textContent).toContain('is empty');
    expect(screen.getByTestId('library-tree-empty').textContent).not.toContain('Knowledge');
  });

  it('an ALL-NOISE dir (has entries but all filtered) says "nothing to browse", NOT "is empty"', async () => {
    // meta-review LOW: don't read as a data-loss bug when the dir is all infra.
    expandDirectory.mockResolvedValue([
      { name: '.artifacts', path: 'Projects/X/.artifacts', type: 'directory', children: null },
      { name: 'code_intel.db', path: 'Projects/X/code_intel.db', type: 'file' },
    ]);
    render(<LibraryTree rootPath="Projects/X" />);
    await waitFor(() => expect(screen.getByTestId('library-tree-empty')).toBeInTheDocument());
    const txt = screen.getByTestId('library-tree-empty').textContent ?? '';
    expect(txt).toContain('nothing to browse');
    expect(txt).not.toContain('is empty');
  });
});
