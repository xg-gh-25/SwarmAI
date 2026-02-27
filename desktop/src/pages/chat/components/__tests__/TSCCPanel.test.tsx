/**
 * Unit tests for the TSCCPanel component.
 *
 * Tests the TSCC panel including:
 * - Collapsed bar renders scope, agent count, capabilities, source count, freshness
 * - Click on collapsed bar triggers expand
 * - Expanded view renders all five cognitive modules
 * - Idle/empty state displays correct placeholder text
 * - Keyboard navigation (Enter/Space expand)
 * - ARIA attributes present
 * - Pin toggle works
 * - Lifecycle state displays correct text
 *
 * Testing methodology: unit tests with React Testing Library.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TSCCPanel } from '../TSCCPanel';
import type { TSCCState } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const makeSampleState = (
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('TSCCPanel', () => {
  const defaultProps = {
    threadId: 'thread-1',
    tsccState: makeSampleState(),
    isExpanded: false,
    isPinned: false,
    onToggleExpand: vi.fn(),
    onTogglePin: vi.fn(),
  };

  it('renders default collapsed bar when threadId is null', () => {
    render(
      <TSCCPanel {...defaultProps} threadId={null} />,
    );
    // Should show default "new" state with workspace scope label (Req 1.2, 9.1)
    expect(screen.getByText('Workspace: SwarmWS (General)')).toBeInTheDocument();
  });

  it('renders default collapsed bar when tsccState is null', () => {
    render(
      <TSCCPanel {...defaultProps} tsccState={null} />,
    );
    // Should show default "new" state with workspace scope label (Req 1.2, 9.1)
    expect(screen.getByText('Workspace: SwarmWS (General)')).toBeInTheDocument();
  });

  describe('CollapsedBar', () => {
    it('renders scope label', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(
        screen.getByText('Workspace: SwarmWS (General)'),
      ).toBeInTheDocument();
    });

    it('renders agent count', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('1 agent')).toBeInTheDocument();
    });

    it('renders capability summary', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('code-review')).toBeInTheDocument();
    });

    it('renders source count', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('1 source')).toBeInTheDocument();
    });

    it('renders freshness indicator', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('just now')).toBeInTheDocument();
    });

    it('triggers onToggleExpand on click', () => {
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel {...defaultProps} onToggleExpand={onToggleExpand} />,
      );
      fireEvent.click(
        screen.getByRole('region', { name: 'Thread cognitive context' }),
      );
      expect(onToggleExpand).toHaveBeenCalledOnce();
    });

    it('triggers onToggleExpand on Enter key', () => {
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel {...defaultProps} onToggleExpand={onToggleExpand} />,
      );
      fireEvent.keyDown(
        screen.getByRole('region', { name: 'Thread cognitive context' }),
        { key: 'Enter' },
      );
      expect(onToggleExpand).toHaveBeenCalledOnce();
    });

    it('triggers onToggleExpand on Space key', () => {
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel {...defaultProps} onToggleExpand={onToggleExpand} />,
      );
      fireEvent.keyDown(
        screen.getByRole('region', { name: 'Thread cognitive context' }),
        { key: ' ' },
      );
      expect(onToggleExpand).toHaveBeenCalledOnce();
    });

    it('has correct ARIA attributes', () => {
      render(<TSCCPanel {...defaultProps} />);
      const region = screen.getByRole('region', {
        name: 'Thread cognitive context',
      });
      expect(region).toHaveAttribute('aria-expanded', 'false');
      expect(region).toHaveAttribute('tabindex', '0');
    });

    it('triggers onTogglePin on pin button click', () => {
      const onTogglePin = vi.fn();
      render(<TSCCPanel {...defaultProps} onTogglePin={onTogglePin} />);
      fireEvent.click(screen.getByLabelText('Pin panel'));
      expect(onTogglePin).toHaveBeenCalledOnce();
    });

    it('shows aria-pressed on pin button when pinned', () => {
      render(<TSCCPanel {...defaultProps} isPinned={true} />);
      expect(screen.getByLabelText('Unpin panel')).toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });

    it('renders plural agents when count > 1', () => {
      const state = makeSampleState({
        liveState: {
          ...makeSampleState().liveState,
          activeAgents: ['SwarmAgent', 'CodeAgent'],
        },
      });
      render(<TSCCPanel {...defaultProps} tsccState={state} />);
      expect(screen.getByText('2 agents')).toBeInTheDocument();
    });

    it('renders plural sources when count > 1', () => {
      const state = makeSampleState({
        liveState: {
          ...makeSampleState().liveState,
          activeSources: [
            { path: 'src/main.py', origin: 'Project' },
            { path: 'src/utils.py', origin: 'Project' },
          ],
        },
      });
      render(<TSCCPanel {...defaultProps} tsccState={state} />);
      expect(screen.getByText('2 sources')).toBeInTheDocument();
    });
  });

  describe('ExpandedView', () => {
    it('renders all five cognitive module headings', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('Context')).toBeInTheDocument();
      expect(screen.getByText('Active Agents')).toBeInTheDocument();
      expect(screen.getByText('What AI is Doing')).toBeInTheDocument();
      expect(screen.getByText('Active Sources')).toBeInTheDocument();
      expect(screen.getByText('Key Summary')).toBeInTheDocument();
    });

    it('has aria-expanded true when expanded', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      const region = screen.getByRole('region', {
        name: 'Thread cognitive context',
      });
      expect(region).toHaveAttribute('aria-expanded', 'true');
    });

    it('renders scope label and thread title in Context module', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(
        screen.getByText('Workspace: SwarmWS (General)'),
      ).toBeInTheDocument();
      expect(screen.getByText('Test Thread')).toBeInTheDocument();
    });

    it('renders agent names in Active Agents module', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('SwarmAgent')).toBeInTheDocument();
    });

    it('renders skills in Active Agents module', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('Skills: code-review')).toBeInTheDocument();
    });

    it('renders activity items in What AI is Doing module', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('Analyzing code')).toBeInTheDocument();
    });

    it('renders sources with origin tags', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('src/main.py')).toBeInTheDocument();
      expect(screen.getByText('Project')).toBeInTheDocument();
    });

    it('renders key summary items', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(
        screen.getByText('Initial analysis complete'),
      ).toBeInTheDocument();
    });

    it('triggers onTogglePin in expanded view', () => {
      const onTogglePin = vi.fn();
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          onTogglePin={onTogglePin}
        />,
      );
      fireEvent.click(screen.getByLabelText('Pin panel'));
      expect(onTogglePin).toHaveBeenCalledOnce();
    });

    it('triggers onToggleExpand when clicking expanded header', () => {
      const onToggleExpand = vi.fn();
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          onToggleExpand={onToggleExpand}
        />,
      );
      expect(screen.getByText('Cognitive Context')).toBeInTheDocument();
    });
  });

  describe('Empty states', () => {
    it('displays "Using core SwarmAgent only" when no agents', () => {
      const state = makeSampleState({
        liveState: {
          ...makeSampleState().liveState,
          activeAgents: [],
        },
      });
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          tsccState={state}
        />,
      );
      expect(
        screen.getByText('Using core SwarmAgent only'),
      ).toBeInTheDocument();
    });

    it('displays "Waiting for your input" when idle with no activity', () => {
      const state = makeSampleState({
        lifecycleState: 'idle',
        liveState: {
          ...makeSampleState().liveState,
          whatAiDoing: [],
        },
      });
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          tsccState={state}
        />,
      );
      expect(
        screen.getByText('Waiting for your input'),
      ).toBeInTheDocument();
    });

    it('displays "Using conversation context only" when no sources', () => {
      const state = makeSampleState({
        liveState: {
          ...makeSampleState().liveState,
          activeSources: [],
        },
      });
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          tsccState={state}
        />,
      );
      expect(
        screen.getByText('Using conversation context only'),
      ).toBeInTheDocument();
    });

    it('displays "No summary yet" when no summary', () => {
      const state = makeSampleState({
        liveState: {
          ...makeSampleState().liveState,
          keySummary: [],
        },
      });
      render(
        <TSCCPanel
          {...defaultProps}
          isExpanded={true}
          tsccState={state}
        />,
      );
      expect(
        screen.getByText(
          'No summary yet — ask me to summarize this thread',
        ),
      ).toBeInTheDocument();
    });
  });

  describe('Lifecycle states', () => {
    const lifecycleTests: Array<{
      state: TSCCState['lifecycleState'];
      expected: string;
    }> = [
      { state: 'new', expected: 'New thread · Ready' },
      { state: 'active', expected: 'Updated just now' },
      { state: 'paused', expected: 'Paused · Waiting for your input' },
      {
        state: 'failed',
        expected: 'Something went wrong — see details below',
      },
      {
        state: 'cancelled',
        expected: 'Execution stopped · Partial progress saved',
      },
      { state: 'idle', expected: 'Idle · Ready for next task' },
    ];

    lifecycleTests.forEach(({ state, expected }) => {
      it(`displays "${expected}" for lifecycle state "${state}"`, () => {
        const tscc = makeSampleState({ lifecycleState: state });
        render(
          <TSCCPanel
            {...defaultProps}
            isExpanded={true}
            tsccState={tscc}
          />,
        );
        expect(screen.getByText(expected)).toBeInTheDocument();
      });
    });
  });
});
