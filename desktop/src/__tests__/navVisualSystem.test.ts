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
 *   rawButton  a <button> that draws its own chrome (bg / border / rounding / padding)
 *              without btn-* / icon-btn; text or glyph links, clickable list rows
 *              (text-left / w-full), tabs, segments and switches excepted
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
  'components/layout/NeedsYouRow.tsx',
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
  'components/workspace-explorer/WorkspaceOverlay.tsx',
  'components/workspace-explorer/RepoPane.tsx',
  'components/workspace-explorer/workspaceParts.tsx',
  'components/workspace-explorer/WorkspaceExplorer.tsx',
  'components/workspace-explorer/ExplorerHeader.tsx',
  'components/workspace-explorer/SectionedExplorer.tsx',
  'components/workspace-explorer/VirtualizedTree.tsx',
  'components/workspace-explorer/TreeNodeRow.tsx',
  'components/workspace-explorer/ZoneSeparator.tsx',
  'components/workspace-explorer/FileContextMenu.tsx',
  'components/workspace-explorer/ExplorerToolbar.tsx',
  'components/workspace-explorer/OpenFileButton.tsx',
  'pages/chat/components/WelcomeScreen.tsx',
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

/** The attribute text of each `<button …>` opening tag (brace-aware: an arrow
 *  function's `=>` inside `{…}` does not end the tag). */
function buttonTags(s: string): string[] {
  const out: string[] = [];
  let i = s.indexOf('<button');
  while (i >= 0) {
    let depth = 0;
    let j = i + 7;
    for (; j < s.length; j++) {
      const c = s[j];
      if (c === '{') depth++;
      else if (c === '}') depth--;
      else if (c === '>' && depth === 0) break;
    }
    out.push(s.slice(i + 7, j));
    i = s.indexOf('<button', j);
  }
  return out;
}

/** A button that draws its own chrome (a background, border, rounding or padding)
 *  instead of using the btn-* / icon-btn scale. A plain text or glyph link is not
 *  counted, nor are tabs and segments (role tab / radio), nor switches (role switch). */
function rawButtons(s: string): number {
  let n = 0;
  for (const attrs of buttonTags(s)) {
    if (/role=["{]['"]?(?:tab|radio|switch)\b/.test(attrs)) continue;
    if (/(?<![\w-])data-viz(?![\w-])/.test(attrs)) continue;   // a chart mark that is clickable
    const cls = /className=(?:"([^"]*)"|\{([\s\S]*)\})/.exec(attrs);
    const text = cls ? (cls[1] ?? cls[2] ?? '') : '';
    if (/(?<![\w-])(?:btn-(?:sm|md|lg)|icon-btn(?:-lg)?)(?![\w-])/.test(text)) continue;
    if (/(?<![\w:-])(?:text-left|w-full)(?![\w-])/.test(text)) continue;   // a clickable list row
    if (/(?<![\w:-])(?:bg-|border(?![\w-]*-(?:b|t|l|r)-0)|rounded|px-|py-|p-\d|shadow)/.test(text)) n += 1;
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

// The measured baseline (2026-10-23). Only shrinks: a migrated file drops out
// (pilot S6: CMBrainOverlay, ToDoOverlay, PipelineOverlay).
const KNOWN: Record<string, Partial<Record<Kind, number>>> = {};

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
