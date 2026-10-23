/**
 * Tests for terminalAttachment — terminal text as a chat-input attachment.
 *
 * Properties: one chip per tab, appended to (a second folder's piece is headed
 * with its cwd); the total is cut to TERMINAL_CONTEXT_MAX keeping the NEWEST output
 * and says so; the chip is never a content block (terminal_context strategy);
 * terminalContextOf joins every chip of a message within the limit; the limit
 * equals the backend schema's buffer_tail max_length (a longer value is a 422 that
 * fails the whole message).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import type { UnifiedAttachment } from '../types';
import { TERMINAL_CONTEXT_MAX, addTerminalText, mergeTerminalAttachments, terminalContextOf, terminalLabel } from './terminalAttachment';

let n = 0;
const id = () => `t${++n}`;
const file: UnifiedAttachment = {
  id: 'f1', name: 'a.txt', type: 'text', deliveryStrategy: 'inline_text', size: 3,
  mediaType: 'text/plain', textContent: 'abc', isLoading: false,
};

describe('terminalAttachment', () => {
  it('adds one terminal chip beside the other attachments, trailing blanks trimmed', () => {
    const out = addTerminalText([file], 'npm test\nFAIL x\n\n  ', '/r/swarmai', id);
    expect(out).toHaveLength(2);
    const chip = out[1];
    expect(chip).toMatchObject({
      type: 'terminal', deliveryStrategy: 'terminal_context', name: 'Terminal · swarmai',
      textContent: 'npm test\nFAIL x', terminal: { cwd: '/r/swarmai', lines: 2, truncated: false },
    });
    expect(addTerminalText(out, '   \n', '/r/swarmai', id)).toBe(out);   // nothing to add
  });

  it('a second piece appends to the same chip; a piece from another folder is headed with its cwd', () => {
    let atts = addTerminalText([], 'one', '/r/a', id);
    atts = addTerminalText(atts, 'two', '/r/a', id);
    atts = addTerminalText(atts, 'three', '', id);
    atts = addTerminalText(atts, 'four', '/r/a', id);   // back to A: headed again, under A
    expect(atts).toHaveLength(1);
    expect(atts[0].textContent).toBe('one\n\ntwo\n\n# cwd: SwarmWS\nthree\n\n# cwd: /r/a\nfour');
    expect(atts[0].terminal?.cwd).toBe('/r/a');
  });

  it('over the limit, whole oldest pieces go first: no heading is left without its text', () => {
    let atts = addTerminalText([], 'A'.repeat(9000), '/r/a', id);
    atts = addTerminalText(atts, 'B'.repeat(9000), '/r/b', id);
    const chip = atts[0];
    expect(chip.textContent).toBe('B'.repeat(9000));        // A dropped whole; B is first now
    expect(chip.terminal?.cwd).toBe('/r/b');                 // ... so the chip's cwd is B's
    expect(chip.name).toBe('Terminal · b');
    expect(chip.terminal?.truncated).toBe(true);
  });

  it('merging a queued message keeps ONE terminal chip, inside the limit, before files past it', () => {
    const files = (k: number) => Array.from({ length: k }, (_, i) => ({ ...file, id: `f${i}`, name: `f${i}` }));
    const queuedChip = addTerminalText([], 'queued', '/r/a', id)[0];
    const newChip = addTerminalText([], 'new', '/r/a', id)[0];
    const merged = mergeTerminalAttachments([...files(10)], [newChip], 10);   // queue already full
    expect(merged).toHaveLength(10);
    expect(merged.filter((a) => a.type === 'terminal')).toHaveLength(1);
    expect(merged[9].textContent).toBe('new');
    const two = mergeTerminalAttachments([files(1)[0], queuedChip], [newChip, files(2)[1]], 10);
    expect(two.map((a) => a.id)).toEqual(['f0', queuedChip.id, 'f1']);
    expect(two[1].textContent).toBe('queued\n\nnew');
    expect(mergeTerminalAttachments(files(3), files(2), 4)).toHaveLength(4);   // no chip: plain limit
  });

  it('cuts to the limit keeping the newest output, from a line boundary, and says so', () => {
    const big = Array.from({ length: 4000 }, (_, i) => `line ${i}`).join('\n');
    const [chip] = addTerminalText([], big, '', id);
    expect(chip.textContent!.length).toBeLessThanOrEqual(TERMINAL_CONTEXT_MAX);
    expect(chip.textContent!.endsWith('line 3999')).toBe(true);
    expect(chip.textContent!.startsWith('line ')).toBe(true);
    expect(chip.terminal?.truncated).toBe(true);
    // once cut, it stays marked cut after a small append
    expect(addTerminalText([chip], 'more', '', id)[0].terminal?.truncated).toBe(true);
  });

  it('terminalContextOf: none without chips; every chip of a message joined within the limit', () => {
    expect(terminalContextOf([file])).toBeUndefined();
    expect(terminalContextOf(undefined)).toBeUndefined();
    const a = addTerminalText([], 'x'.repeat(TERMINAL_CONTEXT_MAX - 10), '/r/a', id)[0];
    const b = addTerminalText([], 'tail-of-b', '/r/b', id)[0];
    const ctx = terminalContextOf([file, a, b])!;
    expect(ctx.bufferTail.length).toBeLessThanOrEqual(TERMINAL_CONTEXT_MAX);
    expect(ctx.bufferTail).toBe('tail-of-b');       // a's piece no longer fits: dropped whole
    expect(ctx.cwd).toBe('/r/b');                   // ... so the cwd is the surviving piece's
    const small = addTerminalText([], 'from a', '/r/a', id)[0];
    expect(terminalContextOf([small, b])).toEqual({ bufferTail: 'from a\n\n# cwd: /r/b\ntail-of-b', cwd: '/r/a' });
  });

  it('labels by folder name, SwarmWS without a cwd', () => {
    expect(terminalLabel('/x/y/proj/')).toBe('Terminal · proj');
    expect(terminalLabel('')).toBe('Terminal · SwarmWS');
  });

  it('the limit equals the backend schema TerminalContext.buffer_tail max_length', () => {
    const schema = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../../backend/schemas/message.py'), 'utf-8');
    const m = /buffer_tail:\s*str\s*=\s*Field\([^)]*max_length=(\d+)/.exec(schema);
    expect(m, 'buffer_tail max_length not found in backend/schemas/message.py').not.toBeNull();
    expect(Number(m![1])).toBe(TERMINAL_CONTEXT_MAX);
  });
});
