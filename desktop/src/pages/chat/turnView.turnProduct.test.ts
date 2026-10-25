/**
 * `turn_product` blocks in the turn view (backend core/turn_products, design
 * 2026-10-26-session-products.md §3.1).
 *
 * A top-level call's products are a `products` unit after its work; a sub-agent's are
 * not a unit (card only). What must NOT change when products are inserted after their
 * calls: every step and its status, the unrecovered-failure mark, and the answer
 * staying an outcome. Products end the open process unit, so the steps may sit in
 * more units than before; they are compared flattened.
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock } from '../../types';
import { deriveTurnView } from './turnView';

const tu = (id: string, name = 'Write'): ContentBlock => ({ type: 'tool_use', id, name, input: {}, summary: id } as ContentBlock);
const tr = (id: string, isError = false): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: '', isError } as ContentBlock);
const pr = (id: string, parent?: string): ContentBlock => ({ type: 'turn_product', toolUseId: id, product: 'file', path: `${id}.md`,
  absolutePath: `/w/${id}.md`, fileKind: 'content', ...(parent ? { parentToolUseId: parent } : {}) } as ContentBlock);
const tx = (text: string): ContentBlock => ({ type: 'text', text } as ContentBlock);

const steps = (v: ReturnType<typeof deriveTurnView>) => v.units.flatMap((u) => (u.kind === 'process'
  ? u.steps.map((s) => (s.kind === 'tool' ? `${s.block.id}:${s.status}` : s.kind)) : []));

describe('turn_product blocks in the turn view', () => {
  it.each([false, true])('streaming=%s: the same steps and statuses with and without products', (streaming) => {
    const base = [tu('a'), tr('a'), tu('b'), tr('b', true), tu('c'), tr('c')];
    const withP = [tu('a'), tr('a'), pr('a'), tu('b'), tr('b', true), tu('c'), tr('c'), pr('c')];
    expect(steps(deriveTurnView(withP, streaming))).toEqual(steps(deriveTurnView(base, streaming)));
  });

  it('a top-level product is a products unit after its work; consecutive ones join it', () => {
    const v = deriveTurnView([tu('a'), tr('a'), tu('b'), tr('b'), pr('a'), pr('b'), tu('c'), tr('c')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['process', 'products', 'process']);
    const p = v.units[1] as Extract<typeof v.units[number], { kind: 'products' }>;
    expect(p.blocks.map((b) => b.toolUseId)).toEqual(['a', 'b']);
    expect(v.products.length).toBe(2);
  });

  it('a sub-agent product is no unit but is in view.products', () => {
    const v = deriveTurnView([tu('a'), tr('a'), pr('s', 'a')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['process']);
    expect(v.products.map((b) => b.toolUseId)).toEqual(['s']);
  });

  it('a product after the last call does not hide its unrecovered failure', () => {
    const v = deriveTurnView([tu('a'), tr('a'), pr('a'), tu('b'), tr('b', true), pr('b')], false);
    expect(v.units.some((u) => u.kind === 'process' && !!u.unrecoveredFailure)).toBe(true);
  });

  it('a product before the answer keeps the answer an outcome', () => {
    const v = deriveTurnView([tu('a'), tr('a'), pr('a'), tx('Done.')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['process', 'products', 'outcome']);
  });
});
