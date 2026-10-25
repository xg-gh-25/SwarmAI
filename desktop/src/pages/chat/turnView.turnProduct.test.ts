/**
 * `turn_product` blocks (backend core/turn_products, design 2026-10-26-session-products.md
 * §2) are a display record: until the products zone draws them they must not change
 * how the rest of the turn is derived. Property: inserting product blocks after their
 * calls leaves every unit, every step status and the unrecovered-failure mark as they
 * were, finished or streaming.
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock } from '../../types';
import { deriveTurnView } from './turnView';

const tu = (id: string, name = 'Write'): ContentBlock => ({ type: 'tool_use', id, name, input: {}, summary: id } as ContentBlock);
const tr = (id: string, isError = false): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: '', isError } as ContentBlock);
const pr = (id: string): ContentBlock => ({ type: 'turn_product', toolUseId: id, product: 'file', path: `${id}.md`,
  absolutePath: `/w/${id}.md`, fileKind: 'content' } as ContentBlock);
const tx = (text: string): ContentBlock => ({ type: 'text', text } as ContentBlock);

// Content positions (`index`, `resultIndex`) shift by the inserted blocks; everything
// else must be identical.
const strip = (v: ReturnType<typeof deriveTurnView>) => JSON.parse(JSON.stringify(v.units,
  (k, val) => (k === 'index' || k === 'resultIndex' ? undefined : val)));

describe('turn_product blocks are inert in the turn view', () => {
  it.each([false, true])('streaming=%s: same units with and without products', (streaming) => {
    const base = [tu('a'), tr('a'), tu('b'), tr('b', true), tu('c'), tr('c')];
    const withP = [tu('a'), tr('a'), pr('a'), tu('b'), tr('b', true), tu('c'), tr('c'), pr('c')];
    expect(strip(deriveTurnView(withP, streaming))).toEqual(strip(deriveTurnView(base, streaming)));
  });

  it('a product after the last call does not hide its unrecovered failure', () => {
    const v = deriveTurnView([tu('a'), tr('a'), pr('a'), tu('b'), tr('b', true), pr('b')], false);
    const proc = v.units.find((u) => u.kind === 'process') as { unrecoveredFailure?: unknown } | undefined;
    expect(proc?.unrecoveredFailure).toBeTruthy();
  });

  it('a product before the answer keeps the answer an outcome', () => {
    const v = deriveTurnView([tu('a'), tr('a'), pr('a'), tx('Done.')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['process', 'outcome']);
  });
});
