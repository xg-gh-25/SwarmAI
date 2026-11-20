/**
 * A turn that ended in an error shows the error once (deriveTurnView, the persisted
 * `turn_outcome` block and the stream handler's `_liveError` line).
 *
 * Questions: after a reload (no live line) is the error still shown? Live (the line and
 * the outcome both present) is it shown once? A turn persisted with the CLI's own
 * "API Error" line as text: once? A completed turn: nothing added?
 */
import { describe, it, expect } from 'vitest';
import { deriveTurnView } from './turnView';
import type { ContentBlock } from '../../types';

const MSG = 'API Error: 400 The scripted model refused this request.';
const outcome = (ended: string, message: string | null = MSG) =>
  ({ type: 'turn_outcome', ended, message } as unknown as ContentBlock);
const shown = (content: ContentBlock[]) => deriveTurnView(content, false).units
  .filter((u) => 'text' in u && typeof u.text === 'string' && u.text.includes(MSG));

describe('a turn that ended in an error', () => {
  it('after a reload the persisted outcome shows the error', () => {
    const units = shown([outcome('error')]);
    expect(units).toHaveLength(1);
    expect((units[0] as { text: string }).text).toBe(`⚠️ ${MSG}`);
  });

  it('live, the stream handler line shows it and the outcome adds nothing', () => {
    // the live line may say it in other words than the outcome (a friendlier message)
    const live = { type: 'text', text: '⚠️ The model refused the request.', _liveError: true } as ContentBlock;
    const units = deriveTurnView([live, outcome('error')], false).units;
    expect(units.filter((u) => 'text' in u && String(u.text).startsWith('⚠️'))).toHaveLength(1);
  });

  it('a turn persisted with the CLI line as text shows it once', () => {
    expect(shown([{ type: 'text', text: MSG } as ContentBlock, outcome('error')])).toHaveLength(1);
  });

  it('a completed turn or an error without a message adds nothing', () => {
    expect(deriveTurnView([{ type: 'text', text: 'Done.' } as ContentBlock, outcome('completed')], false).units)
      .toHaveLength(1);
    expect(deriveTurnView([outcome('error', null)], false).units).toHaveLength(0);
  });

  it('an earlier failed turn shows its error where it ended, before a later turn', () => {
    const units = deriveTurnView([
      outcome('error'),
      { type: 'text', text: 'Later answer.' } as ContentBlock,
      outcome('completed'),
    ], false).units.map((u) => ('text' in u ? u.text : u.kind));
    expect(units).toEqual([`⚠️ ${MSG}`, 'Later answer.']);
  });

  it('a later turn that quotes the message does not hide the earlier error', () => {
    const units = shown([outcome('error'), { type: 'text', text: `It said: ${MSG}` } as ContentBlock, outcome('completed')]);
    expect(units).toHaveLength(2);
  });
});
