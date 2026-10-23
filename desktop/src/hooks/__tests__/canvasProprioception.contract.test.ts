/**
 * Canvas proprioception contracts (canvas-proprioception.md §7 T2, T5).
 *
 * T2 — the Canvas modules never touch the message store: Canvas state must not
 *      flow into MessageStore / reconcile (B1: Canvas cannot disturb chat).
 * T5 — every per-tab Canvas field is classified: reported in the agent's
 *      snapshot (and how) or deliberately not. A new / removed field turns this
 *      red until it is classified — the guard against another `collapsed: false`.
 *
 * Methodology: reads the source files (no rendering).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';

const SRC = join(__dirname, '..', '..');
const read = (p: string) => readFileSync(join(SRC, p), 'utf8');

describe('T2 — Canvas modules never touch the message store', () => {
  const viewer = readdirSync(join(SRC, 'components/file-viewer'))
    .filter((f) => /\.tsx?$/.test(f) && !f.includes('.test.'))
    .map((f) => `components/file-viewer/${f}`);
  it.each(['hooks/useCanvasHost.ts', 'utils/uiContext.ts', 'utils/uiCommands.ts', ...viewer])('%s', (p) => {
    const src = read(p);
    // imports and calls only (comments may say "never enters MessageStore")
    expect(src).not.toMatch(/from ['"][^'"]*(MessageStore|messageStore)[^'"]*['"]|messageStoreRegistry\.|setMessages\(/);
  });
});

describe('T5 — every CanvasTabState field is classified', () => {
  // field → where it shows in the snapshot (§3.1), or why it is not reported.
  const CLASSIFIED: Record<string, string> = {
    file: 'file.path / name / openedBy (auto)',
    pinned: 'pinned',
    manuallyOpen: 'visible (hidden vs open)',
    railed: 'visible (rail)',
    outputsCollapsed: 'listFolded',
    canvasTab: 'tab',
    fileDirty: 'file.dirty',
    pendingOpen: 'pendingOpen',
    seen: 'outputs[].isNew',
    fileView: 'file.view / file.review',
    commands: 'commands',
  };
  it('the interface has exactly the classified fields', () => {
    const src = read('hooks/useCanvasHost.ts');
    const body = src.slice(src.indexOf('interface CanvasTabState {'), src.indexOf('\n}', src.indexOf('interface CanvasTabState {')));
    const fields = [...body.matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1]);
    expect(fields.sort()).toEqual(Object.keys(CLASSIFIED).sort());
  });
});
