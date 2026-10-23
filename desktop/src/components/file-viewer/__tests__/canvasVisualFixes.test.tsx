/**
 * Canvas small visual fixes (super-canvas-fixing.md C6, F13).
 *
 * - One icon slot (`.file-icon-slot`, the sm badge width) holds a file's badge AND a
 *   folder's icon in the file tree, so names at one depth line up (F9).
 * - The Artifacts list shows a focus-visible ring for ↑/↓ (F10).
 * - Both Canvas lists cap at the same ~5-row height (F11).
 * - The shared selected-row style is a primary tint, not the plain card colour (F13).
 * - The editor's floating group is inverted: ink fill, theme tokens re-pointed so
 *   every control inside inverts; the selected view is a paper chip (C4, variant B).
 *
 * The CSS checks read index.css as text (jsdom does not compute colours); the DOM
 * checks render the real components.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { CanvasOutputRail } from '../CanvasOutputRail';

const css = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8');
const rule = (sel: string) => {
  const i = css.indexOf(`${sel} {`);
  expect(i).toBeGreaterThan(-1);
  return css.slice(i, css.indexOf('}', i));
};
const panelSrc = readFileSync(resolve(__dirname, '../FileViewerPanel.tsx'), 'utf8');

describe('C6 / F13', () => {
  it('the icon slot is exactly the sm badge width', () => {
    const slot = rule('.file-icon-slot').match(/width:\s*(\d+)px/)![1];
    const badge = rule('.file-badge-sm').match(/width:\s*(\d+)px/)![1];
    expect(slot).toBe(badge);
  });

  it('the Artifacts list has a visible keyboard focus ring', () => {
    render(<CanvasOutputRail files={{ written: [{ path: 'a.md', absolutePath: '/a.md', fileName: 'a.md', operation: 'written', firstSeen: 1, count: 1 }] }} />);
    expect(screen.getByTestId('canvas-output-rail').className).toContain('focus-visible:ring-1');
  });

  it('both lists cap at the same height', () => {
    const caps = [...panelSrc.matchAll(/max-h-\[(\d+)px\] overflow-y-auto px-2 pb-1\.5/g)].map((m) => m[1]);
    expect(caps).toEqual(['140', '140']);
  });

  it('the selected row is primary-tinted with a primary ring, not the plain card colour', () => {
    const sel = rule('.canvas-row-sel');
    expect(sel).toMatch(/background:\s*color-mix\(in srgb, var\(--color-primary\) 16%, var\(--color-card\)\)/);
    expect(sel).toMatch(/box-shadow:[^;]*--color-primary/);
  });

  it('the floating group is the inverted solid (C4 variant B)', () => {
    const ink = rule('.editor-float-ink');
    expect(ink).toMatch(/--fg-ink:\s*var\(--color-text\)/);
    expect(ink).toMatch(/--fg-paper:\s*var\(--color-bg\)/);
    const g = rule('.editor-float-group');
    expect(g).toMatch(/background:\s*var\(--fg-ink\)/);
    for (const tok of ['--color-text:', '--color-text-muted:', '--color-hover:', '--color-border:']) expect(g).toContain(tok);
    const on = rule('.editor-view-on');
    expect(on).toMatch(/background:\s*var\(--fg-paper\)/);
  });
});
