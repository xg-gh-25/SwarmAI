/**
 * Guard: no window-wide overlay puts its content under the macOS title bar.
 *
 * On macOS the window uses `TitleBarStyle::Overlay` (src-tauri/src/lib.rs), so the
 * title bar and its traffic lights overlay the top 28px of the page. A `fixed inset-0`
 * overlay covers that strip too. 046cbe66a fixed the two run cards with one rule,
 * applied here to every such overlay:
 *
 * - the overlay keeps its content 44px down (`pt-11`: 28px bar + 16px gap), and
 * - the box inside is sized by the overlay's own box (`h-full`, `max-h-full`, `w-full`),
 *   never by `vh` / `vw`: a share of the viewport ignores that padding and the app's
 *   CSS zoom on <html> (hooks/useZoom.ts), so a 90vh card can still reach the top.
 *
 * Method: static scan of every non-test .tsx under src/ (jsdom computes no layout).
 * A code line holding `fixed inset-0` must also hold `pt-11`; a file with such an
 * overlay must hold no viewport unit on a code line. Per-file bypass counts must
 * EQUAL `KNOWN`, which is `{}`. `EXEMPT` lists the overlays that show nothing a title
 * bar can hide, with the reason and the exact count, so a new one still fails.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { join, relative, resolve } from 'path';

const SRC = resolve(__dirname, '..');

/** file (relative to src/) → number of `fixed inset-0` code lines allowed without pt-11 */
const EXEMPT: Record<string, { count: number; why: string }> = {
  'App.tsx': { count: 1, why: 'opaque full-window "backend unreachable" screen, not a dialog' },
  'components/common/BackendStartupOverlay.tsx': { count: 1, why: 'opaque full-window startup screen' },
  'components/common/ShutdownOverlay.tsx': { count: 1, why: 'spinner + one line, no controls' },
  'components/layout/BrainHub.tsx': { count: 2, why: 'transparent click-outside catchers, no content' },
  'components/code-intel/CodeGraph.tsx': { count: 1, why: 'fullscreen branch has no caller; BrainHub renders it inline' },
  'pages/eval/ui.tsx': {
    count: 1,
    why: 'drawer inside an OverlayHost card, whose transform makes the card its containing block (below the chat header)',
  },
};

/** Known bypasses per file. Must stay empty. */
const KNOWN: Record<string, { unpadded?: number; viewportUnits?: number }> = {};

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith('.tsx') && !/\.test\.tsx$/.test(p) && !p.includes('__tests__')) out.push(p);
  }
  return out;
}

/** Source lines that are code, not comments. */
function codeLines(src: string): string[] {
  return src.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l));
}

const VIEWPORT_UNIT = /\b\d+(?:\.\d+)?[ds]?v[hw]\b/g;

function scan() {
  const bypasses: Record<string, { unpadded?: number; viewportUnits?: number }> = {};
  const scrims: Record<string, number> = {};
  for (const file of walk(SRC)) {
    const rel = relative(SRC, file).split('\\').join('/');
    const lines = codeLines(readFileSync(file, 'utf-8'));
    const overlays = lines.filter((l) => l.includes('fixed inset-0'));
    if (overlays.length === 0) continue;
    scrims[rel] = overlays.length;
    if (EXEMPT[rel]) continue;
    const unpadded = overlays.filter((l) => !/\bpt-11\b/.test(l)).length;
    const viewportUnits = lines.reduce((n, l) => n + (l.match(VIEWPORT_UNIT)?.length ?? 0), 0);
    if (unpadded || viewportUnits) {
      bypasses[rel] = { ...(unpadded ? { unpadded } : {}), ...(viewportUnits ? { viewportUnits } : {}) };
    }
  }
  return { bypasses, scrims };
}

describe('window-wide overlays stay below the macOS title bar', () => {
  const { bypasses, scrims } = scan();

  it('every fixed inset-0 overlay keeps pt-11 and sizes its box by itself, not by vh/vw', () => {
    expect(bypasses).toEqual(KNOWN);
  });

  it('each exempt file still has exactly the exempt overlays it declares', () => {
    const found = Object.fromEntries(Object.keys(EXEMPT).map((f) => [f, scrims[f] ?? 0]));
    const declared = Object.fromEntries(Object.entries(EXEMPT).map(([f, e]) => [f, e.count]));
    expect(found).toEqual(declared);
  });

  it('the scan sees the overlays this guard was written for', () => {
    for (const f of [
      'components/common/Modal.tsx',
      'components/common/FileEditorModal.tsx',
      'pages/chat/components/TSCCModules.tsx',
      'components/swarm-out/RunOverlay.tsx',
      'components/layout/PipelineRunCard.tsx',
    ]) expect(scrims[f], f).toBeGreaterThan(0);
  });

  it('the line rule catches a 90vh card on a p-4 scrim (self-test)', () => {
    const lines = codeLines([
      '<div className="fixed inset-0 z-50 flex items-center justify-center p-4">',
      '  <div className="w-[95vw] h-[90vh]">',
      '// fixed inset-0 in a comment is not counted, nor is 90vh',
    ].join('\n'));
    const overlays = lines.filter((l) => l.includes('fixed inset-0'));
    expect(overlays.filter((l) => !/\bpt-11\b/.test(l))).toHaveLength(1);
    expect(lines.join('\n').match(VIEWPORT_UNIT)).toEqual(['95vw', '90vh']);
  });
});
