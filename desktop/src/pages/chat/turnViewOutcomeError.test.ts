/**
 * A turn that ended in an error shows the error once (deriveTurnView, the persisted
 * `turn_outcome` block and the stream handler's `_liveError` line).
 *
 * Questions: after a reload (no live line) is the last turn's error the error card
 * (view.failure) and not a line? Live (the line and the outcome both present) is it shown
 * once? An earlier failed turn: a line where it ended, no card? A turn persisted with the
 * CLI's own "API Error" line as text: once? A completed turn: nothing added?
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
  it('after a reload the last turn\'s persisted outcome becomes the error card, not a line', () => {
    const block = { ...(outcome('error') as object), code: 'MODEL_ERROR', suggested_action: 'Send it again.' } as ContentBlock;
    const view = deriveTurnView([block], false);
    expect(shown([block])).toHaveLength(0);
    expect(view.failure).toEqual({ code: 'MODEL_ERROR', message: MSG, suggestedAction: 'Send it again.' });
  });

  it('an outcome saved before the suggested action was kept still gives the card', () => {
    expect(deriveTurnView([outcome('error')], false).failure).toEqual({ message: MSG });
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
    expect(deriveTurnView([outcome('error', null)], false).failure).toBeUndefined();
  });

  it('an earlier failed turn shows its error where it ended, before a later turn', () => {
    const units = deriveTurnView([
      outcome('error'),
      { type: 'text', text: 'Later answer.' } as ContentBlock,
      outcome('completed'),
    ], false).units.map((u) => ('text' in u ? u.text : u.kind));
    expect(units).toEqual([`⚠️ ${MSG}`, 'Later answer.']);
    expect(deriveTurnView([outcome('error'), { type: 'text', text: 'Later answer.' } as ContentBlock,
      outcome('completed')], false).failure).toBeUndefined();   // its card would act on a turn that is over
  });

  it('a later turn with no text of its own (stopped at once) still makes the error an earlier turn\'s', () => {
    const view = deriveTurnView([outcome('error'), { type: 'turn_stop', at: 'T' } as ContentBlock,
      outcome('none')], false);
    expect(view.failure).toBeUndefined();
    expect(view.units.map((u) => ('text' in u ? u.text : u.kind))).toEqual([`⚠️ ${MSG}`]);
  });

  it('a later turn that quotes the message does not hide the earlier error', () => {
    const units = shown([outcome('error'), { type: 'text', text: `It said: ${MSG}` } as ContentBlock, outcome('completed')]);
    expect(units).toHaveLength(2);
  });
});
