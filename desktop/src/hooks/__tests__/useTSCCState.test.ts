/**
 * Unit tests for the useTSCCState React hook.
 *
 * Tests the TSCC state management hook that fetches system prompt
 * metadata from the endpoint.
 *
 * Tests include:
 * - Initial state fetch on mount with valid sessionId
 * - System prompt metadata fetch on mount
 * - State reset when sessionId changes
 * - Expand/collapse preference preserved per session
 * - Pin preference preserved per session
 * - Default state returned on fetch failure
 *
 * Testing methodology: unit tests with mocked TSCC service layer.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

// Mock the tscc service before importing the hook
vi.mock('../../services/tscc', () => ({
  getTSCCState: vi.fn(),
  getSystemPromptMetadata: vi.fn(),
}));

import { useTSCCState } from '../useTSCCState';
import { getTSCCState, getSystemPromptMetadata } from '../../services/tscc';
import type { TSCCState } from '../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const sampleState: TSCCState = {
  threadId: 'session-1',
  projectId: null,
  scopeType: 'workspace',
  lastUpdatedAt: '2026-02-26T10:00:00Z',
  lifecycleState: 'active',
  liveState: {
    context: {
      scopeLabel: 'Workspace: SwarmWS (General)',
      threadTitle: 'Test Thread',
      mode: undefined,
    },
    activeAgents: [],
    activeCapabilities: { skills: [], mcps: [], tools: [] },
    whatAiDoing: [],
    activeSources: [],
    keySummary: [],
  },
};

const sampleMetadata = {
  files: [
    { filename: 'SWARMAI.md', tokens: 500, truncated: false },
    { filename: 'IDENTITY.md', tokens: 200, truncated: false },
  ],
  totalTokens: 700,
  fullText: '# System Prompt',
};

describe('useTSCCState Hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTSCCState).mockResolvedValue(sampleState);
    vi.mocked(getSystemPromptMetadata).mockResolvedValue(sampleMetadata);
  });

  describe('initial fetch', () => {
    it('should fetch state on mount with valid sessionId', async () => {
      const { result } = renderHook(() => useTSCCState('session-1'));
      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });
      expect(getTSCCState).toHaveBeenCalledWith('session-1');
      expect(result.current.tsccState?.threadId).toBe('session-1');
    });

    it('should fetch system prompt metadata on mount', async () => {
      const { result } = renderHook(() => useTSCCState('session-1'));
      await waitFor(() => {
        expect(result.current.promptMetadata).not.toBeNull();
      });
      expect(getSystemPromptMetadata).toHaveBeenCalledWith('session-1');
      expect(result.current.promptMetadata?.totalTokens).toBe(700);
      expect(result.current.promptMetadata?.files).toHaveLength(2);
    });

    it('should fall back to default state on fetch failure', async () => {
      vi.mocked(getTSCCState).mockRejectedValue(new Error('fail'));
      vi.mocked(getSystemPromptMetadata).mockRejectedValue(new Error('fail'));
      const { result } = renderHook(() => useTSCCState('session-1'));
      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });
      expect(result.current.tsccState?.lifecycleState).toBe('new');
      expect(result.current.promptMetadata).toBeNull();
    });

    it('should return null state when sessionId is null', () => {
      const { result } = renderHook(() => useTSCCState(null));
      expect(result.current.tsccState).toBeNull();
      expect(result.current.promptMetadata).toBeNull();
    });
  });

  describe('sessionId change', () => {
    it('should reset and re-fetch when sessionId changes', async () => {
      const { result, rerender } = renderHook(
        ({ id }) => useTSCCState(id),
        { initialProps: { id: 'session-1' as string | null } },
      );
      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });
      rerender({ id: 'session-2' });
      await waitFor(() => {
        expect(getTSCCState).toHaveBeenCalledWith('session-2');
      });
    });
  });

  describe('per-session preferences', () => {
    it('should preserve expand preference per session', async () => {
      const { result, rerender } = renderHook(
        ({ id }) => useTSCCState(id),
        { initialProps: { id: 'session-1' as string | null } },
      );
      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });
      act(() => { result.current.toggleExpand(); });
      expect(result.current.isExpanded).toBe(true);

      // Switch away and back
      rerender({ id: 'session-2' });
      await waitFor(() => {
        expect(result.current.isExpanded).toBe(false);
      });
      rerender({ id: 'session-1' });
      await waitFor(() => {
        expect(result.current.isExpanded).toBe(true);
      });
    });

    it('should preserve pin preference per session', async () => {
      const { result } = renderHook(() => useTSCCState('session-1'));
      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });
      expect(result.current.isPinned).toBe(false);
      act(() => { result.current.togglePin(); });
      expect(result.current.isPinned).toBe(true);
    });

  });
});
