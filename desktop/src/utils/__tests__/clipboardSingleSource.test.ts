/**
 * Guard: every copy in the app goes through utils/clipboard `copyToClipboard`.
 *
 * `navigator.clipboard.writeText` is denied in the Tauri webview, so a component
 * that calls it directly copies nothing in the desktop app (BrainMapView,
 * BrainHub and PollinateOverlay did, 2026-10-27). Method: scan every non-test
 * source file under desktop/src for a direct `navigator.clipboard` or
 * `execCommand('copy')` call; only utils/clipboard.ts may make one.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', '..');
const ALLOWED = new Set(['utils/clipboard.ts']);
const DIRECT = /navigator\.clipboard\s*\??\.\s*writeText|execCommand\(\s*['"]copy/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === '__tests__' || name === 'node_modules' ? [] : sources(p);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [p] : [];
  });
}

describe('clipboard single source', () => {
  it('no file outside utils/clipboard.ts writes the clipboard directly', () => {
    const offenders = sources(SRC)
      .map((p) => relative(SRC, p).split('\\').join('/'))
      .filter((rel) => !ALLOWED.has(rel))
      .filter((rel) => DIRECT.test(readFileSync(join(SRC, rel), 'utf8')));
    expect(offenders).toEqual([]);
  });
});
