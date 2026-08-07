/**
 * CodeGraph — `inline` variant prop contract (run_a75197d9).
 *
 * The graph is fullscreen by default (`fixed inset-0 z-50`) — BottomBar and the
 * old Brain-Hub "View code graph" both mount it that way and MUST stay unchanged.
 * The Brain-Hub detail refactor needs it embeddable in a content pane, so an
 * ADDITIVE `inline` prop swaps the outer container to `relative h-full w-full`
 * WITHOUT touching the default. These tests pin BOTH branches (default byte-shape
 * + inline) so a future edit can't silently break the shared fullscreen callers.
 *
 * react-force-graph-2d is canvas-based (jsdom has no canvas), so we mock it at the
 * boundary — the test is about the OUTER container class, not the graph render.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';

vi.mock('react-force-graph-2d', () => ({
  default: () => <div data-testid="force-graph" />,
}));
vi.mock('../../services/codeIntel', () => ({
  getCodeIntelGraph: vi.fn(),
}));
import { getCodeIntelGraph } from '../../services/codeIntel';
import { CodeGraph } from './CodeGraph';

const GRAPH_OK = {
  nodes: [{ id: 'a', name: 'a', module: 'core', type: 'function' }],
  edges: [],
};

beforeEach(() => {
  (getCodeIntelGraph as ReturnType<typeof vi.fn>).mockResolvedValue(GRAPH_OK);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

// The outer container is the first child rendered; we read its className.
function outerClass(container: HTMLElement): string {
  return (container.firstElementChild as HTMLElement)?.className ?? '';
}

describe('CodeGraph — inline variant prop', () => {
  it('DEFAULT (no inline prop) keeps the fullscreen container — BottomBar/legacy callers unaffected', async () => {
    const { container } = render(<CodeGraph project="SwarmAI" />);
    await waitFor(() => expect(screen.getByTestId('force-graph')).toBeInTheDocument());
    const cls = outerClass(container);
    expect(cls).toContain('fixed');
    expect(cls).toContain('inset-0');
    expect(cls).not.toContain('relative');
  });

  it('inline=true swaps to a relative, parent-filling container (not fixed) — embeddable in a pane', async () => {
    const { container } = render(<CodeGraph project="SwarmAI" inline />);
    await waitFor(() => expect(screen.getByTestId('force-graph')).toBeInTheDocument());
    const cls = outerClass(container);
    expect(cls).toContain('relative');
    expect(cls).toContain('h-full');
    expect(cls).not.toContain('fixed');
  });

  it('inline loading state is also non-fixed (no fullscreen flash inside a pane)', async () => {
    // Never resolves → stays in loading branch.
    (getCodeIntelGraph as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    const { container } = render(<CodeGraph project="SwarmAI" inline />);
    const cls = outerClass(container);
    expect(cls).toContain('relative');
    expect(cls).not.toContain('fixed');
  });
});
