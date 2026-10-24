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

  it('joins the active key to the sheet with the sheet colour', () => {
    expect(CSS).toMatch(/\.a10-card--active,\s*\.a10-histrow\[aria-pressed="true"\]\s*\{[^}]*margin-right:\s*-10px[^}]*background:\s*var\(--color-sheet\)/);
    expect(CSS).toMatch(/\.a10-zone \.a10-card--active\s*\{[^}]*margin-right:\s*-20px/);
    expect(CSS).toMatch(/\.a10-nav\s*\{\s*direction:\s*rtl;\s*\}/);
  });

  it('lifts the dark surface ladder inside the sheet only', () => {
    const block = CSS.match(/:root\.dark \.app-sheet\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(block).toMatch(/--color-bg:\s*var\(--color-sheet\)/);
    expect(block).toMatch(/--color-card:\s*var\(--color-sheet-card\)/);
  });

  it('keeps the layout constants in step with the sheet inset', async () => {
    const { LAYOUT_CONSTANTS } = await import('../contexts/LayoutContext');
    expect(CSS).toMatch(new RegExp(`\\.app-sheet\\s*\\{[^}]*margin:\\s*${LAYOUT_CONSTANTS.SHEET_INSET}px`));
    // TopBar 28 + sheet inset + tab bar 40
    expect(LAYOUT_CONSTANTS.CHAT_CONTENT_TOP).toBe(28 + LAYOUT_CONSTANTS.SHEET_INSET + 40);
  });
});
