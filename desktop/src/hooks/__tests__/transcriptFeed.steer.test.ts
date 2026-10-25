/**
 * transcriptFeed × steer (chat-steer frontend §8): a user row the CLI read as a
 * steer (`metadata.steered`) reaches every tab through the feed. A tab that has
 * the bubble gets the label (even if it missed `user_steered`); a provisional
 * bubble (built from a 200-char preview) gets the row's full content. A user row
 * without the label still changes nothing in a tab that shows it.
 */
import { describe, expect, it } from 'vitest';
import { planTranscriptChange, type TranscriptChange } from '../transcriptFeed';
import type { Message } from '../../types';

const row = (meta: Record<string, unknown>, text = 'the full steer text'): TranscriptChange => ({
  type: 'transcript_row', rev: 3, pending: false, op: 'insert',
  row: { id: 'row-s', session_id: 'S', role: 'user', created_at: '2026-10-25T10:00:02',
    content: [{ type: 'text', text }], metadata: { client_id: 'local-s', ...meta } },
} as never);
const bubble = (over: Partial<Message> = {}): Message => ({ id: 'local-s', role: 'user', timestamp: '',
  content: [{ type: 'text', text: 'the full' }], metadata: { client_id: 'local-s' }, ...over });
const apply = (msgs: Message[], ch: TranscriptChange) => {
  let out = [...msgs];
  for (const op of planTranscriptChange(out, ch, new Map())) {
    if (op.kind === 'update') out = out.map((m) => (m.id === op.id ? op.update(m) : m));
    if (op.kind === 'append') out = [...out, op.msg];
  }
  return out;
};

describe('the feed brings a read steer to every tab', () => {
  it('labels a Waiting bubble it already shows', () => {
    const [m] = apply([bubble({ steerWaiting: true })], row({ steered: 'during' }));
    expect(m.metadata?.steered).toBe('during');
    expect(m.steerWaiting).toBeFalsy();
  });

  it('a provisional bubble gets the row\'s full content and stops being provisional', () => {
    const [m] = apply([bubble({ metadata: { client_id: 'local-s', provisional: true }, steerWaiting: true })],
      row({ steered: 'after' }));
    // the row's content as every history row is displayed (toDisplayMessage)
    expect(m.content).toMatchObject([{ type: 'text', text: 'the full steer text' }]);
    expect(m.metadata?.provisional).toBeFalsy();
    expect(m.metadata?.steered).toBe('after');
  });

  it('a user row without the label changes nothing in a tab that shows it', () => {
    expect(planTranscriptChange([bubble()], row({}), new Map())).toEqual([]);
  });
});
