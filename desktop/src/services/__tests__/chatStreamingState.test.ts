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

import api from '../api';
import { chatService } from '../chat';

const mockGet = vi.spyOn(api, 'get');

describe('chatService.getStreamingState — boundary mapping', () => {
  beforeEach(() => mockGet.mockReset());

  it('maps every live field snake→camel for an AskUserQuestion waiting_input session', async () => {
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
    expect(e.pendingPermission).toBeNull(); // a question is not a permission prompt
    // the mapped entry carries exactly the live fields of the backend's streaming-state
    // contract (backend/tests/test_streaming_state_contract.py) except `pid`, which
    // only the backend's session-health probe reads
    expect(Object.keys(e).sort()).toEqual(
      ['lastDrainedSeqs', 'pendingCount', 'pendingPermission', 'pendingQuestion', 'state', 'streaming', 'waitingInput'],
    );
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
    // …and it is the permission prompt, mapped for its own re-surface path
    // (core/permission_prompt, 2026-10-22 incident)
    expect(out['perm-sess'].pendingPermission).toEqual({
      requestId: 'perm-req-1', toolName: 'Bash', toolInput: out['perm-sess'].pendingPermission!.toolInput,
      reason: 'destructive', options: ['approve', 'deny'],
    });
    expect(Object.keys(out['perm-sess'].pendingPermission!.toolInput)).toEqual(['command']);
  });

  it('a permission prompt from the durable store (tool_input as a JSON string) maps too', async () => {
    mockGet.mockResolvedValue({ data: { sessions: { 's': { state: 'waiting_input', waiting_input: true,
      pending_question: { tool_use_id: 'p2', request_id: 'p2', tool_input: '{"command": "ls"}' } } } } });
    const out = await chatService.getStreamingState();
    expect(out['s'].pendingPermission).toEqual({ requestId: 'p2', toolName: 'Bash', toolInput: { command: 'ls' }, reason: '', options: ['approve', 'deny'] });
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
      pendingPermission: null,
      lastDrainedSeqs: [],
    });
  });

  it('returns empty object when sessions map is absent', async () => {
    mockGet.mockResolvedValue({ data: {} });
    const out = await chatService.getStreamingState();
    expect(out).toEqual({});
  });
});

describe('chatService.getStreamingState — background agents snapshot', () => {
  beforeEach(() => mockGet.mockReset());

  it('every read replaces the background agents store (stores/backgroundAgentsStore)', async () => {
    const { backgroundAgentsStore } = await import('../../stores/backgroundAgentsStore');
    backgroundAgentsStore.reset();
    mockGet.mockResolvedValue({ data: { sessions: {
      s1: { streaming: false, state: 'idle', background_agents: [{ toolUseId: 'ag1', description: 'd', status: 'running' }] },
      s2: { streaming: false, state: 'idle' },
    } } });
    await chatService.getStreamingState();
    expect(backgroundAgentsStore.loaded()).toBe(true);
    expect(backgroundAgentsStore.get('s1').map((a) => a.status)).toEqual(['running']);
    expect(backgroundAgentsStore.get('s2')).toEqual([]);
    backgroundAgentsStore.reset();
  });
});
