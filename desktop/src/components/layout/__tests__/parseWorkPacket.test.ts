/**
 * Tests for parseWorkPacket — the guarded linked_context JSON parser (A3).
 * Gate-1 A3 M1: null / empty / malformed / non-object MUST NOT throw; they
 * return null so the detail drawer renders "no work-packet context" instead of
 * crashing the whole overlay.
 */
import { describe, it, expect } from 'vitest';
import { parseWorkPacket } from '../ToDoOverlay';

describe('parseWorkPacket', () => {
  it('null / empty string → null (no throw)', () => {
    expect(parseWorkPacket(null)).toBeNull();
    expect(parseWorkPacket('')).toBeNull();
  });

  it('malformed JSON → null (no throw)', () => {
    expect(parseWorkPacket('{not json')).toBeNull();
    expect(parseWorkPacket('{"a":')).toBeNull();
  });

  it('non-object JSON (array / scalar) → null', () => {
    expect(parseWorkPacket('[]')).toBeNull();
    expect(parseWorkPacket('[1,2,3]')).toBeNull();
    expect(parseWorkPacket('42')).toBeNull();
    expect(parseWorkPacket('"just a string"')).toBeNull();
    expect(parseWorkPacket('true')).toBeNull();
    expect(parseWorkPacket('null')).toBeNull();
  });

  it('valid work-packet object → parsed', () => {
    const wp = parseWorkPacket(JSON.stringify({
      next_step: 'Read X',
      files: ['a.ts', 'b.ts'],
      design_docs: ['Knowledge/Designs/x.md'],
    }));
    expect(wp).not.toBeNull();
    expect(wp!.next_step).toBe('Read X');
    expect(wp!.files).toEqual(['a.ts', 'b.ts']);
    expect(wp!.design_docs).toEqual(['Knowledge/Designs/x.md']);
  });

  it('empty object → parsed (renders as no material, still not null)', () => {
    expect(parseWorkPacket('{}')).toEqual({});
  });
});
