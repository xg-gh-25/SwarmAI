/**
 * Nav visual-system ratchet — the Left Nav, OverlayHost and every overlay move onto
 * ONE type scale, ONE palette and ONE button scale (design
 * left-nav-backend-and-visual-system.md V1–V6, V4).
 *
 * Methodology: a static scan of the files below. Per file it counts what bypasses
 * the system:
 *   fontPx     hand-written pixel font sizes (text-[Npx], inline fontSize)
 *   twText     Tailwind's own text sizes (text-xs … text-2xl); the nav uses
 *              text-meta / small / body / heading / title / display instead
 *   hex        literal colours (#rgb… / rgb()), a var(--x, #fallback) fallback excepted
 *   palette    Tailwind palette colours (bg-red-500, text-amber-400, bg-white …)
 *   weight     font-medium / font-bold (the system has 400 and 600 only)
 *   rawButton  <button> whose className uses no btn-* / icon-btn (role tab / radio excepted)
 *   emoji      colour emoji (Emoji_Presentation or a VS16 emoji); icons are Material Symbols
 * The counts must EQUAL `KNOWN`: a new bypass fails, and so does a fixed one still
 * listed. Each migrated file drops to zero; the system is done when KNOWN is empty.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..');

export const FILES = [
  'components/layout/ThreeColumnLayout.tsx',
  'components/layout/OverlayHost.tsx',
  'components/layout/overlayShell.tsx',
  'components/layout/OverlayState.tsx',
  'components/layout/HistoryOverlay.tsx',
  'components/layout/ArtifactsOverlay.tsx',
  'components/layout/NeedYouOverlay.tsx',
  'components/layout/CMBrainOverlay.tsx',
  'components/layout/LibraryOverlay.tsx',
  'components/layout/LibraryHealth.tsx',
  'components/layout/LibraryTree.tsx',
  'components/layout/BrainHub.tsx',
  'components/layout/DddCard.tsx',
  'components/layout/NewBrainOverlay.tsx',
  'components/layout/ToDoOverlay.tsx',
  'components/layout/PipelineOverlay.tsx',
  'components/layout/PollinateOverlay.tsx',
  'components/layout/CommunityOverlay.tsx',
  'components/layout/JobsRunsOverlay.tsx',
  'components/layout/CapabilitiesOverlay.tsx',
  'components/layout/HiveFleetOverlay.tsx',
  'pages/EvalDashboard.tsx',
  'components/overlay/index.tsx',
];

const B = '(?<![\\w-])';
const E = '(?![\\w-])';
const PATTERNS = {
  fontPx: new RegExp(`${B}text-\\[\\d+(?:\\.\\d+)?px\\]${E}|fontSize\\s*:`, 'g'),
  twText: new RegExp(`${B}text-(?:xs|sm|base|lg|xl|2xl)${E}`, 'g'),
  hex: /#[0-9a-fA-F]{3,8}\b(?![\w-])|rgba?\(/g,
  palette: new RegExp(`${B}(?:bg|text|border|ring|from|to|fill|stroke)-(?:white|black|(?:red|green|blue|gray|slate|zinc|neutral|amber|yellow|orange|emerald|teal|cyan|sky|indigo|violet|purple|pink|rose|lime|stone)-\\d{2,3})(?:/\\d+)?${E}`, 'g'),
  weight: new RegExp(`${B}font-(?:medium|bold)${E}`, 'g'),
  emoji: /\p{Emoji_Presentation}|\uFE0F/gu,
} as const;

type Kind = keyof typeof PATTERNS | 'rawButton';

function stripFallbacks(s: string): string {
  // var(--x, #abc) — the fallback only shows when the theme variable is missing
  return s.replace(/var\(--[\w-]+\s*,\s*#[0-9a-fA-F]{3,8}\s*\)/g, 'var()');
}

function rawButtons(s: string): number {
  let n = 0;
  for (const m of s.matchAll(/<button\b([^>]*)>/gs)) {
    if (/role=["{]['"]?(?:tab|radio)\b/.test(m[1])) continue;   // tabs / segments are not buttons
    const cls = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{([^}]*)\})/s.exec(m[1]);
    const text = cls ? (cls[1] ?? cls[2] ?? cls[3] ?? '') : '';
    if (!/(?<![\w-])(?:btn-(?:sm|md|lg)|icon-btn(?:-lg)?)(?![\w-])/.test(text)) n += 1;
  }
  return n;
}

export function measure(): Record<string, Partial<Record<Kind, number>>> {
  const out: Record<string, Partial<Record<Kind, number>>> = {};
  for (const rel of FILES) {
    let s: string;
    try { s = readFileSync(join(SRC, rel), 'utf8'); } catch { continue; }
    const clean = stripFallbacks(s);
    const row: Partial<Record<Kind, number>> = {};
    for (const [k, re] of Object.entries(PATTERNS)) {
      const n = (k === 'hex' ? clean : s).match(re)?.length ?? 0;
      if (n) row[k as Kind] = n;
    }
    const rb = rawButtons(s);
    if (rb) row.rawButton = rb;
    if (Object.keys(row).length) out[rel] = row;
  }
  return out;
}

// The measured baseline (2026-10-23, before migration). Only shrinks.
const KNOWN: Record<string, Partial<Record<Kind, number>>> = {
  'components/layout/ThreeColumnLayout.tsx': { fontPx: 10, hex: 7, palette: 1, weight: 2, rawButton: 6, emoji: 1 },
  'components/layout/OverlayHost.tsx': { fontPx: 4, twText: 1, hex: 3, rawButton: 1 },
  'components/layout/overlayShell.tsx': { fontPx: 1 },
  'components/layout/OverlayState.tsx': { fontPx: 4, twText: 2, hex: 1, palette: 2, weight: 2, rawButton: 1 },
  'components/layout/HistoryOverlay.tsx': { fontPx: 5, weight: 1 },
  'components/layout/ArtifactsOverlay.tsx': { fontPx: 17, hex: 4, palette: 1, weight: 4, rawButton: 7, emoji: 1 },
  'components/layout/NeedYouOverlay.tsx': { fontPx: 8, rawButton: 1, emoji: 4 },
  'components/layout/CMBrainOverlay.tsx': { fontPx: 63, twText: 25, hex: 52, palette: 1, weight: 13, rawButton: 11, emoji: 21 },
  'components/layout/LibraryOverlay.tsx': { fontPx: 22, twText: 20, hex: 15, palette: 1, weight: 3, rawButton: 5, emoji: 16 },
  'components/layout/LibraryHealth.tsx': { fontPx: 10, hex: 6, palette: 2, weight: 3, rawButton: 3, emoji: 1 },
  'components/layout/LibraryTree.tsx': { twText: 2 },
  'components/layout/BrainHub.tsx': { fontPx: 70, hex: 70, weight: 4, rawButton: 14, emoji: 3 },
  'components/layout/DddCard.tsx': { fontPx: 16, hex: 23, weight: 2, rawButton: 2 },
  'components/layout/NewBrainOverlay.tsx': { fontPx: 14, twText: 6, hex: 6, weight: 3, rawButton: 5, emoji: 13 },
  'components/layout/ToDoOverlay.tsx': { fontPx: 51, hex: 5, palette: 16, weight: 11, rawButton: 15 },
  'components/layout/PipelineOverlay.tsx': { fontPx: 32, twText: 13, palette: 50, weight: 7, rawButton: 10 },
  'components/layout/PollinateOverlay.tsx': { fontPx: 24, twText: 12, palette: 14, weight: 7, rawButton: 11, emoji: 5 },
  'components/layout/CommunityOverlay.tsx': { fontPx: 45, palette: 17, weight: 2, rawButton: 16, emoji: 10 },
  'components/layout/JobsRunsOverlay.tsx': { fontPx: 32, twText: 1, hex: 2, palette: 8, weight: 7, rawButton: 7 },
  'components/layout/CapabilitiesOverlay.tsx': { fontPx: 14, twText: 18, palette: 2, weight: 5, rawButton: 5, emoji: 24 },
  'components/layout/HiveFleetOverlay.tsx': { fontPx: 5, twText: 1, palette: 1, weight: 3, rawButton: 4 },
  'pages/EvalDashboard.tsx': { fontPx: 131, twText: 74, hex: 8, palette: 89, weight: 60, rawButton: 19, emoji: 14 },
};

describe('nav visual system', () => {
  it('bypass counts equal the known baseline (a ratchet: only shrink it)', () => {
    expect(measure()).toEqual(KNOWN);
  });

  it('the shared overlay components themselves use only the system', () => {
    expect(measure()['components/overlay/index.tsx']).toBeUndefined();
  });

  it('the type scale and zone colours are defined once in index.css', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    for (const t of ['meta', 'small', 'body', 'heading', 'title', 'display']) {
      expect(css).toMatch(new RegExp(`--text-${t}:\\s*\\d+px;`));
      expect(css).toMatch(new RegExp(`--text-${t}--line-height:\\s*\\d+px;`));
    }
    for (const z of ['cognitive', 'work', 'system']) {
      expect(css.match(new RegExp(`--color-zone-${z}:\\s*#[0-9a-fA-F]{6};`, 'g'))?.length).toBe(2);
    }
  });
});
