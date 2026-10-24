/**
 * welcomeBee.test.ts — the Welcome bee's flight geometry (pure, no DOM).
 *
 * Properties: the easing runs 0→1 without going backwards; an entrance starts
 * where it is told and ends exactly on the landing spot; a lap starts and ends
 * on the landing spot; keyframe offsets run 0→1 in order.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ease, flightKeyframes, landedTransform } from './welcomeBee';

const xy = (t: unknown) => {
  const m = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(String(t));
  return m ? { x: Number(m[1]), y: Number(m[2]) } : null;
};

describe('welcomeBee', () => {
  it('ease starts at 0, ends at 1 and never goes backwards', () => {
    expect(ease(0)).toBe(0);
    expect(ease(1)).toBe(1);
    let prev = 0;
    for (let i = 1; i <= 200; i++) {
      const v = ease(i / 200);
      expect(v).toBeGreaterThanOrEqual(prev - 1e-9);
      prev = v;
    }
  });

  it('the hover slows the bee just before touchdown', () => {
    const speed = (a: number, b: number) => (ease(b) - ease(a)) / (b - a);
    expect(speed(0.83, 0.89)).toBeLessThan(speed(0.3, 0.4) / 5);
  });

  it('an entrance starts at the given point and lands on the logo', () => {
    const f = flightKeyframes('in', { x: -320, y: 12 });
    expect(xy(f[0].transform)).toEqual({ x: -320, y: 12 });
    expect(f[f.length - 1].transform).toBe(landedTransform);
  });

  it('a lap starts and ends on the landing spot', () => {
    const f = flightKeyframes('lap');
    expect(xy(f[0].transform)).toEqual(xy(landedTransform));
    expect(f[f.length - 1].transform).toBe(landedTransform);
  });

  it('keyframe offsets run 0 → 1 in order', () => {
    const offs = flightKeyframes('in').map((k) => k.offset as number);
    expect(offs[0]).toBe(0);
    expect(offs[offs.length - 1]).toBe(1);
    for (let i = 1; i < offs.length; i++) expect(offs[i]).toBeGreaterThan(offs[i - 1]);
  });

  it('the CSS landed spot equals the last keyframe (no jump when the flight ends)', () => {
    const css = readFileSync(join(__dirname, '../../../index.css'), 'utf8');
    const rule = /\.welcome-bee--landed\s*\{\s*transform:\s*([^;]+);/.exec(css);
    expect(rule?.[1].trim()).toBe(landedTransform);
  });
});
