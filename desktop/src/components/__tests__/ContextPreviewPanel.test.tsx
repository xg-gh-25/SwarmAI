/**
 * Unit tests for the ContextPreviewPanel component.
 *
 * Tests the collapsible context preview panel that displays the 8-layer
 * context assembly with token counts, source paths, truncation indicators,
 * and expandable content previews.
 *
 * Testing methodology: unit tests with mocked ``getContextPreview`` service.
 *
 * Key behaviors verified:
 * - Panel renders with mock context data and shows total token count
 * - Collapsible behavior (expand/collapse toggle)
 * - Layer list rendering with correct token counts and names
 * - Truncation indicator display with stage info
 * - Truncation summary banner display
 *
 * Validates: Requirements 33.5, 33.6
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import type { ContextPreview } from '../../types';

// ---------------------------------------------------------------------------
// Mock the context service — prevent real network calls
// ---------------------------------------------------------------------------
const mockGetContextPreview = vi.fn<
  [string, string | undefined],
  Promise<ContextPreview | null>
>();

vi.mock('../../services/context', () => ({
  getContextPreview: (...args: unknown[]) =>
    mockGetContextPreview(args[0] as string, args[1] as string | undefined),
}));

// Import after mock setup
import { ContextPreviewPanel } from '../workspace/ContextPreviewPanel';

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const baseLayers = [
  {
    layerNumber: 1,
    name: 'System Prompt',
    sourcePath: 'system-prompts.md',
    tokenCount: 300,
    contentPreview: 'You are a helpful assistant.',
    truncated: false,
    truncationStage: 0,
  },
  {
    layerNumber: 2,
    name: 'Live Work',
    sourcePath: 'db://thread-abc',
    tokenCount: 750,
    contentPreview: 'Thread: Design review\nLast message: looks good',
    truncated: false,
    truncationStage: 0,
  },
  {
    layerNumber: 6,
    name: 'Memory',
    sourcePath: 'Knowledge/Memory/prefs.md',
    tokenCount: 800,
    contentPreview: 'User prefers concise answers.',
    truncated: true,
    truncationStage: 2,
  },
];

const mockPreview: ContextPreview = {
  projectId: 'proj-uuid-1',
  threadId: 'thread-abc',
  layers: baseLayers,
  totalTokenCount: 1850,
  budgetExceeded: true,
  tokenBudget: 10000,
  truncationSummary: '',
  etag: 'etag-v1',
};

const mockPreviewWithTruncation: ContextPreview = {
  ...mockPreview,
  truncationSummary:
    '[Context truncated: Memory layer reduced via snippet-removal. Layers affected: 6 (Memory).]',
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ContextPreviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetContextPreview.mockResolvedValue(mockPreview);
  });

  afterEach(() => {
    cleanup();
  });

  // -----------------------------------------------------------------
  // Rendering with mock data
  // -----------------------------------------------------------------
  describe('renders with mock context data', () => {
    it('should show the panel header with "Context Preview" text', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('Context Preview')).toBeInTheDocument();
      });
    });

    it('should display total token count badge in the header', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      // 1850 → "1.9k tokens" (formatTokenCount rounds)
      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });
    });

    it('should call getContextPreview with the provided projectId and threadId', async () => {
      render(
        <ContextPreviewPanel projectId="proj-uuid-1" threadId="thread-abc" />,
      );

      await waitFor(() => {
        expect(mockGetContextPreview).toHaveBeenCalledWith(
          'proj-uuid-1',
          'thread-abc',
        );
      });
    });
  });

  // -----------------------------------------------------------------
  // Collapsible behavior
  // -----------------------------------------------------------------
  describe('collapsible behavior', () => {
    it('should start collapsed — layer list not visible', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      // Layers should NOT be visible when collapsed
      expect(screen.queryByText('System Prompt')).not.toBeInTheDocument();
    });

    it('should expand when header is clicked, showing layers', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      // Click header to expand
      fireEvent.click(screen.getByText('Context Preview'));

      expect(screen.getByText('System Prompt')).toBeInTheDocument();
      expect(screen.getByText('Live Work')).toBeInTheDocument();
      expect(screen.getByText('Memory')).toBeInTheDocument();
    });

    it('should collapse again when header is clicked a second time', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      // Expand
      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.getByText('System Prompt')).toBeInTheDocument();

      // Collapse
      fireEvent.click(screen.getByText('Context Preview'));
      expect(screen.queryByText('System Prompt')).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------
  // Layer list rendering with token counts
  // -----------------------------------------------------------------
  describe('layer list rendering with correct token counts', () => {
    it('should render each layer with its name', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      expect(screen.getByText('System Prompt')).toBeInTheDocument();
      expect(screen.getByText('Live Work')).toBeInTheDocument();
      expect(screen.getByText('Memory')).toBeInTheDocument();
    });

    it('should display formatted token counts for each layer', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      // 300 → "300 tokens", 750 → "750 tokens", 800 → "800 tokens"
      expect(screen.getByText('300 tokens')).toBeInTheDocument();
      expect(screen.getByText('750 tokens')).toBeInTheDocument();
      expect(screen.getByText('800 tokens')).toBeInTheDocument();
    });

    it('should display workspace-relative source paths', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      expect(screen.getByText('system-prompts.md')).toBeInTheDocument();
      expect(screen.getByText('Knowledge/Memory/prefs.md')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------
  // Truncation indicator with stage info
  // -----------------------------------------------------------------
  describe('truncation indicator display with stage info', () => {
    it('should show truncation badge for truncated layers', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      // Memory layer (stage 2 = snippet-removal)
      expect(
        screen.getByText('truncated · snippet-removal'),
      ).toBeInTheDocument();
    });

    it('should NOT show truncation badge for non-truncated layers', async () => {
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      // Only one truncation badge should exist (for Memory layer)
      const truncationBadges = screen.getAllByText(/truncated ·/);
      expect(truncationBadges).toHaveLength(1);
    });

    it('should show correct stage label for stage 1 (within-layer)', async () => {
      const previewWithStage1: ContextPreview = {
        ...mockPreview,
        layers: [
          {
            layerNumber: 7,
            name: 'Workspace Semantic',
            sourcePath: 'context-L1.md',
            tokenCount: 500,
            contentPreview: 'Workspace overview...',
            truncated: true,
            truncationStage: 1,
          },
        ],
        totalTokenCount: 500,
      };
      mockGetContextPreview.mockResolvedValue(previewWithStage1);

      render(<ContextPreviewPanel projectId="proj-stage1" />);

      await waitFor(() => {
        expect(screen.getByText('500 tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      expect(
        screen.getByText('truncated · within-layer'),
      ).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------
  // Truncation summary banner
  // -----------------------------------------------------------------
  describe('truncation summary banner display', () => {
    it('should display truncation summary banner when truncationSummary is non-empty', async () => {
      mockGetContextPreview.mockResolvedValue(mockPreviewWithTruncation);

      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      expect(
        screen.getByText(
          /Context truncated: Memory layer reduced via snippet-removal/,
        ),
      ).toBeInTheDocument();
    });

    it('should NOT display truncation summary banner when truncationSummary is empty', async () => {
      // mockPreview has empty truncationSummary
      render(<ContextPreviewPanel projectId="proj-uuid-1" />);

      await waitFor(() => {
        expect(screen.getByText('1.9k tokens')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Context Preview'));

      // Layers are visible but no truncation banner
      expect(screen.getByText('System Prompt')).toBeInTheDocument();
      expect(
        screen.queryByText(/Context truncated/),
      ).not.toBeInTheDocument();
    });
  });
});
