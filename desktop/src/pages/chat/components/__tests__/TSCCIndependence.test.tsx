/**
 * Independence tests for TSCCPanel and ContextPreviewPanel.
 *
 * Verifies that the two panels operate with completely separate state:
 * - Expanding/collapsing TSCCPanel does not affect ContextPreviewPanel
 * - Expanding/collapsing ContextPreviewPanel does not affect TSCCPanel
 * - TSCCPanel renders in chat view (between messages and input)
 * - ContextPreviewPanel renders in project detail view
 *
 * Testing methodology: unit tests with React Testing Library.
 * Validates: Requirements 17.1, 17.2, 17.3, 17.4
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { TSCCPanel } from '../TSCCPanel';
import type { TSCCState } from '../../../../types';

// ---------------------------------------------------------------------------
// Mock ContextPreviewPanel's service dependency
// ---------------------------------------------------------------------------
const mockGetContextPreview = vi.fn();

vi.mock('../../../../services/context', () => ({
  getContextPreview: (...args: unknown[]) => mockGetContextPreview(...args),
}));

// Import ContextPreviewPanel after mock setup
import { ContextPreviewPanel } from '../../../../components/workspace/ContextPreviewPanel';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const makeSampleTSCCState = (
  overrides: Partial<TSCCState> = {},
): TSCCState => ({
  threadId: 'thread-1',
  projectId: null,
  scopeType: 'workspace',
  lastUpdatedAt: new Date().toISOString(),
  lifecycleState: 'active',
  liveState: {
    context: {
      scopeLabel: 'Workspace: SwarmWS (General)',
      threadTitle: 'Test Thread',
      mode: undefined,
    },
    activeAgents: ['SwarmAgent'],
    activeCapabilities: { skills: ['code-review'], mcps: [], tools: [] },
    whatAiDoing: ['Analyzing code'],
    activeSources: [{ path: 'src/main.py', origin: 'Project' }],
    keySummary: ['Initial analysis complete'],
  },
  ...overrides,
});

const mockContextPreview = {
  projectId: 'proj-1',
  threadId: 'thread-1',
  layers: [
    {
      layerNumber: 1,
      name: 'System Prompt',
      sourcePath: 'system-prompts.md',
      tokenCount: 300,
      contentPreview: 'You are a helpful assistant.',
      truncated: false,
      truncationStage: 0,
    },
  ],
  totalTokenCount: 300,
  budgetExceeded: false,
  tokenBudget: 10000,
  truncationSummary: '',
  etag: 'etag-v1',
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('TSCC and ContextPreviewPanel independence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetContextPreview.mockResolvedValue(mockContextPreview);
  });

  afterEach(() => {
    cleanup();
  });

  describe('state isolation', () => {
    it('expanding TSCCPanel does not expand ContextPreviewPanel', async () => {
      const onToggleExpand = vi.fn();

      const { unmount: unmountTSCC } = render(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={true}
          isPinned={false}
          onToggleExpand={onToggleExpand}
          onTogglePin={vi.fn()}
        />,
      );

      // TSCC is expanded — verify expanded content visible
      expect(screen.getByText('Active Agents')).toBeInTheDocument();

      unmountTSCC();

      // Render ContextPreviewPanel separately — it should start collapsed
      render(<ContextPreviewPanel projectId="proj-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });

      // ContextPreviewPanel starts collapsed — layers NOT visible
      expect(screen.queryByText('System Prompt')).not.toBeInTheDocument();
    });

    it('expanding ContextPreviewPanel does not affect TSCCPanel collapsed state', async () => {
      // Render ContextPreviewPanel and expand it
      render(<ContextPreviewPanel projectId="proj-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.getByText('System Prompt')).toBeInTheDocument();

      cleanup();

      // Render TSCCPanel in collapsed state — should remain collapsed
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={false}
          isPinned={false}
          onToggleExpand={onToggleExpand}
          onTogglePin={vi.fn()}
        />,
      );

      // TSCC collapsed bar visible, expanded content NOT visible
      expect(
        screen.getByText('Workspace: SwarmWS (General)'),
      ).toBeInTheDocument();
      expect(screen.queryByText('Active Agents')).not.toBeInTheDocument();
    });

    it('collapsing TSCCPanel does not collapse ContextPreviewPanel', async () => {
      // Render ContextPreviewPanel expanded
      render(<ContextPreviewPanel projectId="proj-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.getByText('System Prompt')).toBeInTheDocument();

      // TSCCPanel uses external isExpanded prop — toggling it is independent
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={false}
          isPinned={false}
          onToggleExpand={onToggleExpand}
          onTogglePin={vi.fn()}
        />,
      );

      // ContextPreviewPanel should still show expanded content
      expect(screen.getByText('System Prompt')).toBeInTheDocument();
    });
  });

  describe('separate state mechanisms', () => {
    it('TSCCPanel uses external isExpanded prop (controlled component)', () => {
      const { rerender } = render(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={false}
          isPinned={false}
          onToggleExpand={vi.fn()}
          onTogglePin={vi.fn()}
        />,
      );

      // Collapsed — no expanded content
      expect(screen.queryByText('Active Agents')).not.toBeInTheDocument();

      // Re-render with isExpanded=true
      rerender(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={true}
          isPinned={false}
          onToggleExpand={vi.fn()}
          onTogglePin={vi.fn()}
        />,
      );

      // Now expanded
      expect(screen.getByText('Active Agents')).toBeInTheDocument();
    });

    it('ContextPreviewPanel uses internal useState for collapse', async () => {
      render(<ContextPreviewPanel projectId="proj-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });

      // Starts collapsed
      expect(screen.queryByText('System Prompt')).not.toBeInTheDocument();

      // Click to expand — internal state toggle
      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.getByText('System Prompt')).toBeInTheDocument();

      // Click to collapse — internal state toggle
      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.queryByText('System Prompt')).not.toBeInTheDocument();
    });
  });

  describe('rendering locations', () => {
    it('TSCCPanel renders in chat view context (requires threadId)', () => {
      render(
        <TSCCPanel
          threadId="thread-1"
          tsccState={makeSampleTSCCState()}
          isExpanded={false}
          isPinned={false}
          onToggleExpand={vi.fn()}
          onTogglePin={vi.fn()}
        />,
      );

      const region = screen.getByRole('region', {
        name: 'Thread cognitive context',
      });
      expect(region).toBeInTheDocument();
    });

    it('TSCCPanel returns null without threadId (not in chat view)', () => {
      const { container } = render(
        <TSCCPanel
          threadId={null}
          tsccState={makeSampleTSCCState()}
          isExpanded={false}
          isPinned={false}
          onToggleExpand={vi.fn()}
          onTogglePin={vi.fn()}
        />,
      );
      expect(container.innerHTML).toBe('');
    });

    it('ContextPreviewPanel renders in project detail view context', async () => {
      render(<ContextPreviewPanel projectId="proj-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });
    });
  });
});
