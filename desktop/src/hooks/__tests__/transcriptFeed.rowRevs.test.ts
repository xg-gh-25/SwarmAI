/**
 * transcriptFeed × rowRevs: a feed change already in a history snapshot is skipped.
 *
 * A history read merges consecutive assistant rows into one bubble whose id is its
 * first row's; the backend lists the rows it holds and their revs (`row_revs`). A
 * transcript change that reaches the tab after that read, for a row the bubble
 * already holds at that rev or later, must not be added again — without rowRevs
 * a Stop receipt (a block with no identity) was appended a second time.
 *
 * Properties:
 * - a change at or below the held rev produces no op;
 * - a newer change to a held row still applies (an identity block's state);
 * - without rowRevs the same change is appended (the bug this closes);
 * - mergeOlderMessages keeps both sides' rowRevs.
 */
import { describe, expect, it } from 'vitest';
import { planTranscriptChange, type TranscriptChange } from '../transcriptFeed';
import { mergeOlderMessages } from '../../pages/chat/utils';
import type { Message } from '../../types';

const bubble = (rowRevs?: Record<string, number>): Message => ({
  id: 'a1', role: 'assistant', timestamp: '2026-10-23T10:00:01',
  content: [
    { type: 'text', text: 'one' },
    { type: 'tool_result', toolUseId: 't1', content: '', subagent: { status: 'running' } },
    { type: 'turn_stop', items: [] },
  ] as never,
  ...(rowRevs ? { rowRevs } : {}),
});

const receipt = (rev: number): TranscriptChange => ({
  type: 'transcript_row', rev, pending: false, op: 'insert',
  row: { id: 'a2', session_id: 'S', role: 'assistant', created_at: '2026-10-23T10:00:02',
    content: [{ type: 'turn_stop', items: [] }] },
} as never);

describe('planTranscriptChange with rowRevs', () => {
  it('skips a change the snapshot already holds', () => {
    const msgs = [bubble({ a1: 3, a2: 7 })];
    expect(planTranscriptChange(msgs, receipt(7), new Map())).toEqual([]);
  });

  it('appends the same change when the bubble does not say it holds the row (the old bug)', () => {
    expect(planTranscriptChange([bubble()], receipt(7), new Map())).toHaveLength(1);
  });

  it('still applies a newer change to a row the snapshot holds', () => {
    const change = {
      type: 'transcript_row', rev: 9, pending: false, op: 'update',
      row: { id: 'a1', session_id: 'S', role: 'assistant', created_at: '2026-10-23T10:00:01',
        content: [{ type: 'tool_result', tool_use_id: 't1', subagent: { status: 'completed' } }] },
    } as never as TranscriptChange;
    const ops = planTranscriptChange([bubble({ a1: 3, a2: 7 })], change, new Map());
    expect(ops).toHaveLength(1);
    const op = ops[0] as { kind: string; update: (m: Message) => Message };
    expect(op.kind).toBe('update');
    const tr = op.update(bubble({ a1: 3 })).content[1] as { subagent?: { status: string } };
    expect(tr.subagent?.status).toBe('completed');
  });
});

describe('mergeOlderMessages', () => {
  it('the merged boundary bubble holds both sides\' rows', () => {
    const older: Message = { ...bubble({ a0: 1 }), id: 'a0' };
    const current: Message = bubble({ a1: 3, a2: 7 });
    const [merged] = mergeOlderMessages([older], [current]);
    expect(merged.rowRevs).toEqual({ a0: 1, a1: 3, a2: 7 });
  });
});
