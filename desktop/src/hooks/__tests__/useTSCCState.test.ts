/**
 * Unit tests for the useTSCCState React hook.
 *
 * Tests the TSCC state management hook including:
 * - Initial state fetch on mount with valid threadId
 * - State reset when threadId changes
 * - ``applyTelemetryEvent`` for all five telemetry event types
 * - ``whatAiDoing`` max 4 enforcement (FIFO)
 * - ``keySummary`` max 5 enforcement
 * - Expand/collapse preference preserved per thread
 * - Pin preference preserved per thread
 * - Default state returned on fetch failure
 *
 * Testing methodology: unit tests with mocked TSCC service layer.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

// Mock the tscc service before importing the hook
vi.mock('../../services/tscc', () => ({
  getTSCCState: vi.fn(),
}));

import { useTSCCState } from '../useTSCCState';
import { getTSCCState } from '../../services/tscc';
import type { TSCCState, StreamEvent } from '../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const makeSampleState = (threadId = 'thread-1'): TSCCState => ({
  threadId,
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
    activeAgents: ['SwarmAgent'],
    activeCapabilities: { skills: ['code-review'], mcps: [], tools: [] },
    whatAiDoing: ['Analyzing code'],
    activeSources: [{ path: 'src/main.py', origin: 'Project' }],
    keySummary: ['Initial analysis'],
  },
});

describe('useTSCCState Hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ---------------------------------------------------------------
  // Initial fetch
  // ---------------------------------------------------------------
  describe('initial fetch', () => {
    it('should fetch state on mount with valid threadId', async () => {
      const sample = makeSampleState('thread-1');
      vi.mocked(getTSCCState).mockResolvedValue(sample);

      const { result } = renderHook(() => useTSCCState('thread-1'));

      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });

      expect(getTSCCState).toHaveBeenCalledWith('thread-1');
      expect(result.current.tsccState?.threadId).toBe('thread-1');
      expect(result.current.lifecycleState).toBe('active');
    });

    it('should return null state when threadId is null', () => {
      const { result } = renderHook(() => useTSCCState(null));

      expect(result.current.tsccState).toBeNull();
      expect(result.current.lifecycleState).toBeNull();
      expect(getTSCCState).not.toHaveBeenCalled();
    });

    it('should fall back to default state on fetch failure', async () => {
      vi.mocked(getTSCCState).mockRejectedValue(new Error('404'));

      const { result } = renderHook(() => useTSCCState('thread-fail'));

      await waitFor(() => {
        expect(result.current.tsccState).not.toBeNull();
      });

      expect(result.current.tsccState?.threadId).toBe('thread-fail');
      expect(result.current.tsccState?.lifecycleState).toBe('new');
      expect(result.current.tsccState?.liveState.activeAgents).toEqual([]);
    });
  });

  // ---------------------------------------------------------------
  // State reset on threadId change
  // ---------------------------------------------------------------
  describe('threadId change', () => {
    it('should reset and re-fetch when threadId changes', async () => {
      const state1 = makeSampleState('thread-1');
      const state2 = makeSampleState('thread-2');
      state2.liveState.context.threadTitle = 'Second Thread';

      vi.mocked(getTSCCState)
        .mockResolvedValueOnce(state1)
        .mockResolvedValueOnce(state2);

      const { result, rerender } = renderHook(
        ({ id }) => useTSCCState(id),
        { initialProps: { id: 'thread-1' as string | null } },
      );

      await waitFor(() => {
        expect(result.current.tsccState?.threadId).toBe('thread-1');
      });

      rerender({ id: 'thread-2' });

      await waitFor(() => {
        expect(result.current.tsccState?.threadId).toBe('thread-2');
      });

      expect(result.current.tsccState?.liveState.context.threadTitle).toBe(
        'Second Thread',
      );
    });
  });

  // ---------------------------------------------------------------
  // applyTelemetryEvent — all five event types
  // ---------------------------------------------------------------
  describe('applyTelemetryEvent', () => {
    it('should handle agent_activity: add agent and update whatAiDoing', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'agent_activity',
          agentName: 'ResearchAgent',
          description: 'Searching docs',
        } as StreamEvent);
      });

      expect(result.current.tsccState?.liveState.activeAgents).toContain(
        'ResearchAgent',
      );
      expect(result.current.tsccState?.liveState.whatAiDoing).toContain(
        'Searching docs',
      );
    });

    it('should deduplicate agents in agent_activity', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'agent_activity',
          agentName: 'SwarmAgent',
          description: 'Still analyzing',
        } as StreamEvent);
      });

      const agents = result.current.tsccState?.liveState.activeAgents ?? [];
      expect(agents.filter((a) => a === 'SwarmAgent')).toHaveLength(1);
    });

    it('should handle tool_invocation: update whatAiDoing', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'tool_invocation',
          toolName: 'read_file',
          description: 'Reading main.py',
        } as StreamEvent);
      });

      expect(result.current.tsccState?.liveState.whatAiDoing).toContain(
        'Reading main.py',
      );
    });

    it('should handle capability_activated: add to correct category', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'capability_activated',
          capabilityType: 'mcp',
          capabilityName: 'github',
        } as StreamEvent);
      });

      expect(
        result.current.tsccState?.liveState.activeCapabilities.mcps,
      ).toContain('github');
    });

    it('should deduplicate capabilities', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'capability_activated',
          capabilityType: 'skill',
          capabilityName: 'code-review',
        } as StreamEvent);
      });

      const skills =
        result.current.tsccState?.liveState.activeCapabilities.skills ?? [];
      expect(skills.filter((s) => s === 'code-review')).toHaveLength(1);
    });

    it('should handle sources_updated: add new source', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'sources_updated',
          sourcePath: 'src/utils.py',
          origin: 'Project',
        } as StreamEvent);
      });

      const sources = result.current.tsccState?.liveState.activeSources ?? [];
      expect(sources).toContainEqual({
        path: 'src/utils.py',
        origin: 'Project',
      });
    });

    it('should deduplicate sources by path', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'sources_updated',
          sourcePath: 'src/main.py',
          origin: 'Project',
        } as StreamEvent);
      });

      const sources = result.current.tsccState?.liveState.activeSources ?? [];
      expect(sources.filter((s) => s.path === 'src/main.py')).toHaveLength(1);
    });

    it('should handle summary_updated: replace keySummary', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'summary_updated',
          keySummary: ['New finding 1', 'New finding 2'],
        } as StreamEvent);
      });

      expect(result.current.tsccState?.liveState.keySummary).toEqual([
        'New finding 1',
        'New finding 2',
      ]);
    });
  });

  // ---------------------------------------------------------------
  // List length enforcement
  // ---------------------------------------------------------------
  describe('list length enforcement', () => {
    it('should enforce whatAiDoing max 4 (FIFO)', async () => {
      const state = makeSampleState();
      state.liveState.whatAiDoing = ['a', 'b', 'c', 'd'];
      vi.mocked(getTSCCState).mockResolvedValue(state);

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'tool_invocation',
          description: 'fifth item',
        } as StreamEvent);
      });

      const doing = result.current.tsccState?.liveState.whatAiDoing ?? [];
      expect(doing).toHaveLength(4);
      expect(doing[0]).toBe('b'); // 'a' evicted
      expect(doing[3]).toBe('fifth item');
    });

    it('should enforce keySummary max 5', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState());

      const { result } = renderHook(() => useTSCCState('thread-1'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'summary_updated',
          keySummary: ['1', '2', '3', '4', '5', '6', '7'],
        } as StreamEvent);
      });

      const summary = result.current.tsccState?.liveState.keySummary ?? [];
      expect(summary).toHaveLength(5);
    });
  });

  // ---------------------------------------------------------------
  // Per-thread expand/collapse and pin preferences
  // ---------------------------------------------------------------
  describe('per-thread preferences', () => {
    it('should preserve expand preference per thread across switches', async () => {
      const state1 = makeSampleState('thread-1');
      const state2 = makeSampleState('thread-2');
      vi.mocked(getTSCCState)
        .mockResolvedValueOnce(state1)
        .mockResolvedValueOnce(state2)
        .mockResolvedValueOnce(state1);

      const { result, rerender } = renderHook(
        ({ id }) => useTSCCState(id),
        { initialProps: { id: 'thread-1' as string | null } },
      );

      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      // Expand thread-1
      act(() => result.current.toggleExpand());
      expect(result.current.isExpanded).toBe(true);

      // Switch to thread-2 (should be collapsed by default)
      rerender({ id: 'thread-2' });
      await waitFor(() =>
        expect(result.current.tsccState?.threadId).toBe('thread-2'),
      );
      expect(result.current.isExpanded).toBe(false);

      // Switch back to thread-1 (should still be expanded)
      rerender({ id: 'thread-1' });
      await waitFor(() =>
        expect(result.current.tsccState?.threadId).toBe('thread-1'),
      );
      expect(result.current.isExpanded).toBe(true);
    });

    it('should preserve pin preference per thread', async () => {
      const state1 = makeSampleState('thread-1');
      const state2 = makeSampleState('thread-2');
      vi.mocked(getTSCCState)
        .mockResolvedValueOnce(state1)
        .mockResolvedValueOnce(state2)
        .mockResolvedValueOnce(state1);

      const { result, rerender } = renderHook(
        ({ id }) => useTSCCState(id),
        { initialProps: { id: 'thread-1' as string | null } },
      );

      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      // Pin thread-1
      act(() => result.current.togglePin());
      expect(result.current.isPinned).toBe(true);

      // Switch to thread-2
      rerender({ id: 'thread-2' });
      await waitFor(() =>
        expect(result.current.tsccState?.threadId).toBe('thread-2'),
      );
      expect(result.current.isPinned).toBe(false);

      // Switch back to thread-1
      rerender({ id: 'thread-1' });
      await waitFor(() =>
        expect(result.current.tsccState?.threadId).toBe('thread-1'),
      );
      expect(result.current.isPinned).toBe(true);
    });

    it('should support setAutoExpand for programmatic control', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-auto'));

      const { result } = renderHook(() => useTSCCState('thread-auto'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      expect(result.current.isExpanded).toBe(false);

      act(() => result.current.setAutoExpand(true));
      expect(result.current.isExpanded).toBe(true);

      act(() => result.current.setAutoExpand(false));
      expect(result.current.isExpanded).toBe(false);
    });
  });

  // ---------------------------------------------------------------
  // Auto-expand logic (Req 16.1, 16.2, 16.3, 16.4)
  // ---------------------------------------------------------------
  describe('triggerAutoExpand', () => {
    it('should auto-expand on first_plan and only once per thread', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-plan'));

      const { result } = renderHook(() => useTSCCState('thread-plan'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      expect(result.current.isExpanded).toBe(false);

      // First plan triggers expand
      act(() => result.current.triggerAutoExpand('first_plan'));
      expect(result.current.isExpanded).toBe(true);

      // Collapse manually
      act(() => result.current.toggleExpand());
      expect(result.current.isExpanded).toBe(false);

      // Second first_plan trigger should NOT re-expand (already seen)
      act(() => result.current.triggerAutoExpand('first_plan'));
      expect(result.current.isExpanded).toBe(false);
    });

    it('should auto-expand on blocking_issue', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-block'));

      const { result } = renderHook(() => useTSCCState('thread-block'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      expect(result.current.isExpanded).toBe(false);

      act(() => result.current.triggerAutoExpand('blocking_issue'));
      expect(result.current.isExpanded).toBe(true);
    });

    it('should auto-expand on explicit_request', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-explicit'));

      const { result } = renderHook(() => useTSCCState('thread-explicit'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      expect(result.current.isExpanded).toBe(false);

      act(() => result.current.triggerAutoExpand('explicit_request'));
      expect(result.current.isExpanded).toBe(true);
    });

    it('should NOT auto-expand during normal telemetry events', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-normal'));

      const { result } = renderHook(() => useTSCCState('thread-normal'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      expect(result.current.isExpanded).toBe(false);

      // Normal telemetry events should NOT trigger expand
      act(() => {
        result.current.applyTelemetryEvent({
          type: 'agent_activity',
          agentName: 'TestAgent',
          description: 'Doing work',
        } as StreamEvent);
      });
      expect(result.current.isExpanded).toBe(false);

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'tool_invocation',
          toolName: 'read_file',
          description: 'Reading file',
        } as StreamEvent);
      });
      expect(result.current.isExpanded).toBe(false);

      act(() => {
        result.current.applyTelemetryEvent({
          type: 'summary_updated',
          keySummary: ['Updated summary'],
        } as StreamEvent);
      });
      expect(result.current.isExpanded).toBe(false);
    });

    it('should do nothing when threadId is null', () => {
      const { result } = renderHook(() => useTSCCState(null));

      // Should not throw
      act(() => result.current.triggerAutoExpand('first_plan'));
      expect(result.current.isExpanded).toBe(false);
    });

    it('blocking_issue should expand even after manual collapse', async () => {
      vi.mocked(getTSCCState).mockResolvedValue(makeSampleState('thread-reblock'));

      const { result } = renderHook(() => useTSCCState('thread-reblock'));
      await waitFor(() => expect(result.current.tsccState).not.toBeNull());

      // Expand via blocking issue
      act(() => result.current.triggerAutoExpand('blocking_issue'));
      expect(result.current.isExpanded).toBe(true);

      // Collapse manually
      act(() => result.current.toggleExpand());
      expect(result.current.isExpanded).toBe(false);

      // Another blocking issue should re-expand
      act(() => result.current.triggerAutoExpand('blocking_issue'));
      expect(result.current.isExpanded).toBe(true);
    });
  });
});
