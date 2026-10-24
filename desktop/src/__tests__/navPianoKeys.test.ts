/**
 * Left Nav piano-key material guard (2026-10-24).
 *
 * Reads index.css as text and checks:
 * - the per-group right accent spines (.a10-group::after / .a10-zone::after) stay
 *   deleted: they stacked with the scrollbar and the sidebar border into one
 *   thick vertical line;
 * - the Nav scroll area has its own 3px bar that is transparent until hover,
 *   while the global --scrollbar-size stays 6px (the chat gutter maths uses it);
 * - every nav row kind (card, History/Artifacts row, footer button) shares the
 *   one key material, with the theme tokens defined in both light and dark;
 * - the chat column is a raised sheet (.app-sheet on <main>, --shadow-sheet in
 *   both themes) and the sidebar no longer carries a right border.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const CSS = readFileSync(join(__dirname, '..', 'index.css'), 'utf8');

describe('left nav piano keys', () => {
  it('has no right-edge accent spine on nav groups', () => {
    expect(CSS).not.toMatch(/\.a10-group::after/);
    expect(CSS).not.toMatch(/\.a10-zone::after/);
  });

  it('gives the nav a 3px hover-only scrollbar and keeps the global size', () => {
    expect(CSS).toMatch(/\.a10-nav::-webkit-scrollbar\s*\{[^}]*width:\s*3px/);
    expect(CSS).toMatch(/\.a10-nav::-webkit-scrollbar-thumb\s*\{[^}]*background:\s*transparent/);
    expect(CSS).toMatch(/\.a10-nav:hover::-webkit-scrollbar-thumb\s*\{/);
    expect(CSS).toMatch(/--scrollbar-size:\s*6px/);
  });

  it('uses one key material for cards, rows and footer buttons', () => {
    expect(CSS).toMatch(/\.a10-card,\s*\.a10-histrow,\s*\.a10-fcard\s*\{[^}]*var\(--nav-key-face\)/);
  });

  it('defines the key tokens for light and dark', () => {
    const count = (CSS.match(/--nav-key-face:/g) ?? []).length;
    expect(count).toBe(2);
  });

  it('lifts the chat column as a sheet instead of a sidebar border', () => {
    const tsx = readFileSync(join(__dirname, '..', 'components', 'layout', 'ThreeColumnLayout.tsx'), 'utf8');
    const aside = tsx.match(/<aside\s+className="([^"]*)"/);
    expect(aside?.[1]).not.toMatch(/border-r/);
    expect(tsx).toMatch(/<main\s+className="app-sheet /);
    expect(CSS).toMatch(/\.app-sheet\s*\{[^}]*margin:\s*6px 6px 6px 0[^}]*box-shadow:\s*var\(--shadow-sheet\)/);
    expect((CSS.match(/--shadow-sheet:/g) ?? []).length).toBe(2);
  });
});
