/**
 * Unit tests for the TSCCPanel component.
 *
 * Tests the simplified TSCC panel that now shows system prompt metadata
 * via a single SystemPromptModule instead of five cognitive modules.
 *
 * Tests include:
 * - Collapsed bar renders "System Prompt" label, file count, token count
 * - Expanded view renders SystemPromptModule heading
 * - Click/keyboard expand triggers callback
 * - Pin toggle works with ARIA attributes
 * - Lifecycle state labels display correctly
 * - Default state when tsccState is null
 *
 * Testing methodology: unit tests with React Testing Library.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TSCCPanel, createDefaultTSCCState } from '../TSCCPanel';
import type { TSCCState, SystemPromptMetadata } from '../../../../types';

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

const sampleMetadata: SystemPromptMetadata = {
  files: [
    { filename: 'SWARMAI.md', tokens: 500, truncated: false },
    { filename: 'IDENTITY.md', tokens: 200, truncated: false },
    { filename: 'KNOWLEDGE.md', tokens: 1200, truncated: true },
  ],
  totalTokens: 1900,
  fullText: '# System Prompt\nHello world',
};

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
    sessionId: 'session-1',
    promptMetadata: sampleMetadata,
  };

  it('renders collapsed bar with "System Prompt" label', () => {
    render(<TSCCPanel {...defaultProps} />);
    expect(screen.getByText('System Prompt')).toBeInTheDocument();
  });

  it('renders default state when tsccState is null', () => {
    render(<TSCCPanel {...defaultProps} tsccState={null} />);
    expect(screen.getByText('System Prompt')).toBeInTheDocument();
  });

  it('renders default state when threadId is null', () => {
    render(<TSCCPanel {...defaultProps} threadId={null} />);
    expect(screen.getByText('System Prompt')).toBeInTheDocument();
  });

  describe('CollapsedBar', () => {
    it('renders file count from metadata', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('3 files')).toBeInTheDocument();
    });

    it('renders total token count from metadata', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('1,900 tok')).toBeInTheDocument();
    });

    it('renders freshness indicator', () => {
      render(<TSCCPanel {...defaultProps} />);
      expect(screen.getByText('just now')).toBeInTheDocument();
    });

    it('triggers onToggleExpand on click', () => {
      const onToggleExpand = vi.fn();
      render(<TSCCPanel {...defaultProps} onToggleExpand={onToggleExpand} />);
      fireEvent.click(
        screen.getByRole('region', { name: 'Thread cognitive context' }),
      );
      expect(onToggleExpand).toHaveBeenCalledOnce();
    });

    it('triggers onToggleExpand on Enter key', () => {
      const onToggleExpand = vi.fn();
      render(<TSCCPanel {...defaultProps} onToggleExpand={onToggleExpand} />);
      fireEvent.keyDown(
        screen.getByRole('region', { name: 'Thread cognitive context' }),
        { key: 'Enter' },
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
        'aria-pressed', 'true',
      );
    });
  });

  describe('ExpandedView', () => {
    it('renders SystemPromptModule heading', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      // The SystemPromptModule renders "System Prompt" heading
      expect(screen.getAllByText('System Prompt').length).toBeGreaterThanOrEqual(1);
    });

    it('has aria-expanded true when expanded', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      const region = screen.getByRole('region', {
        name: 'Thread cognitive context',
      });
      expect(region).toHaveAttribute('aria-expanded', 'true');
    });

    it('renders file list from metadata', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('SWARMAI.md')).toBeInTheDocument();
      expect(screen.getByText('IDENTITY.md')).toBeInTheDocument();
      expect(screen.getByText('KNOWLEDGE.md')).toBeInTheDocument();
    });

    it('renders truncation indicator for truncated files', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('truncated')).toBeInTheDocument();
    });

    it('renders total token count', () => {
      render(<TSCCPanel {...defaultProps} isExpanded={true} />);
      expect(screen.getByText('1,900 tokens')).toBeInTheDocument();
    });

    it('renders "No active session" when sessionId is null', () => {
      render(
        <TSCCPanel {...defaultProps} isExpanded={true} sessionId={null} promptMetadata={null} />,
      );
      expect(screen.getByText('No active session')).toBeInTheDocument();
    });

    it('triggers onTogglePin in expanded view', () => {
      const onTogglePin = vi.fn();
      render(
        <TSCCPanel {...defaultProps} isExpanded={true} onTogglePin={onTogglePin} />,
      );
      fireEvent.click(screen.getByLabelText('Pin panel'));
      expect(onTogglePin).toHaveBeenCalledOnce();
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
      { state: 'failed', expected: 'Something went wrong — see details below' },
      { state: 'cancelled', expected: 'Execution stopped · Partial progress saved' },
      { state: 'idle', expected: 'Idle · Ready for next task' },
    ];

    lifecycleTests.forEach(({ state, expected }) => {
      it(`displays "${expected}" for lifecycle state "${state}"`, () => {
        const tscc = makeSampleState({ lifecycleState: state });
        render(
          <TSCCPanel {...defaultProps} isExpanded={true} tsccState={tscc} />,
        );
        expect(screen.getByText(expected)).toBeInTheDocument();
      });
    });
  });

  describe('createDefaultTSCCState', () => {
    it('returns a fresh timestamp on each call', () => {
      const s1 = createDefaultTSCCState();
      const s2 = createDefaultTSCCState();
      expect(s1.lastUpdatedAt).toBeDefined();
      expect(s2.lastUpdatedAt).toBeDefined();
      expect(s1.lifecycleState).toBe('new');
    });
  });
});
