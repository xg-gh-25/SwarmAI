/**
 * Unit Tests for chatService.getStreamingState() — Root-1 SSOT Phase 3 boundary.
 *
 * Verifies the snake_case → camelCase mapping of the streaming-state read API,
 * and specifically the Gate-2 HIGH fix: a command-permission prompt shares the
 * WAITING_INPUT state and emits a pending_question with NO `questions` key — it
 * MUST map to pendingQuestion=null so the AC5 re-surface never mistakes it for
 * an AskUserQuestion.
 *
 * Feature: session-state-source-of-truth (Phase 3), Gate-2 finding HIGH#1.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: { get: vi.fn() },
}));

import api from '../api';
import { chatService } from '../chat';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe('chatService.getStreamingState — boundary mapping', () => {
  beforeEach(() => vi.clearAllMocks());

  it('maps all 6 fields snake→camel for an AskUserQuestion waiting_input session', async () => {
    mockGet.mockResolvedValue({
      data: {
        sessions: {
          's1': {
            streaming: false,
            state: 'waiting_input',
            waiting_input: true,
            pending_count: 1,
            pending_question: {
              tool_use_id: 'toolu_01ABC',
              questions: [{ question: 'Q?', header: 'H', options: [], multiSelect: false }],
            },
            last_drained_seqs: [4, 5],
          },
        },
      },
    });
    const out = await chatService.getStreamingState();
    const e = out['s1'];
    expect(e.streaming).toBe(false);
    expect(e.state).toBe('waiting_input');
    expect(e.waitingInput).toBe(true);
    expect(e.pendingCount).toBe(1);
    expect(e.lastDrainedSeqs).toEqual([4, 5]);
    expect(e.pendingQuestion).not.toBeNull();
    expect(e.pendingQuestion!.toolUseId).toBe('toolu_01ABC');
    expect(e.pendingQuestion!.questions).toHaveLength(1);
  });

  it('Gate-2 HIGH: a command-permission pending_question (no `questions`) maps to pendingQuestion=null', async () => {
    mockGet.mockResolvedValue({
      data: {
        sessions: {
          'perm-sess': {
            streaming: false,
            state: 'waiting_input',
            waiting_input: true,
            pending_count: 0,
            // The permission-prompt shape: tool_use_id + request_id + options, NO questions.
            pending_question: {
              tool_use_id: 'perm-req-1',
              request_id: 'perm-req-1',
              tool_name: 'Bash',
              tool_input: { command: 'rm -rf /' },
              reason: 'destructive',
              options: ['approve', 'deny'],
            },
            last_drained_seqs: [],
          },
        },
      },
    });
    const out = await chatService.getStreamingState();
    // waitingInput stays true (the permission UI uses cmd_permission_request),
    // but pendingQuestion is null — NOT a phantom empty-questions AskUserQuestion.
    expect(out['perm-sess'].waitingInput).toBe(true);
    expect(out['perm-sess'].pendingQuestion).toBeNull();
  });

  it('an empty `questions` array also maps to null (defense-in-depth)', async () => {
    mockGet.mockResolvedValue({
      data: { sessions: { 's': { state: 'waiting_input', waiting_input: true, pending_question: { tool_use_id: 'x', questions: [] }, last_drained_seqs: [] } } },
    });
    const out = await chatService.getStreamingState();
    expect(out['s'].pendingQuestion).toBeNull();
  });

  it('tolerates missing optional fields with safe defaults', async () => {
    mockGet.mockResolvedValue({
      data: { sessions: { 's': { streaming: true, state: 'streaming' } } },
    });
    const out = await chatService.getStreamingState();
    expect(out['s']).toEqual({
      streaming: true,
      state: 'streaming',
      waitingInput: false,
      pendingCount: 0,
      pendingQuestion: null,
      lastDrainedSeqs: [],
      // OT01 honest-signal fix: boundary mapping always emits this field with a
      // fail-safe default (raw.post_disconnect_flushing ?? false), so even the
      // "missing optional fields" case carries it as false.
      postDisconnectFlushing: false,
    });
  });

  it('returns empty object when sessions map is absent', async () => {
    mockGet.mockResolvedValue({ data: {} });
    const out = await chatService.getStreamingState();
    expect(out).toEqual({});
  });
});
