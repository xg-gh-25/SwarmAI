/**
 * Escape has one owner: the layer stack in hooks/useLayer.
 *
 * Every surface that closes on Escape (a dialog, the overlay host, a menu, a popover)
 * registers with ``useEscape`` / ``useDialogLayer``. A keydown listener of its own on
 * the document or the window that reacts to Escape races the stack and every other
 * such listener: the order they were added decides which surface closes, and before
 * the stack a dialog inside an overlay closed both. This test reads every source file
 * and fails on a ``addEventListener('keydown', …)`` whose handler looks at Escape,
 * outside useLayer itself.
 *
 * Allowed, and not caught here: a React ``onKeyDown`` on a field or a region that takes
 * Escape for itself (a rename input, a search box). It runs before the stack decides
 * and marks the key handled with ``preventDefault`` or ``stopPropagation``.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..', '..');
const OWNER = 'hooks/useLayer.ts';

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue;
      out.push(...sources(p));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

const stripComments = (code: string) =>
  code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');

/** The text from ``open`` to its matching close, or to the end. */
function balanced(code: string, from: number, open: string, close: string): string {
  let depth = 0;
  for (let i = from; i < code.length; i++) {
    if (code[i] === open) depth++;
    else if (code[i] === close && --depth === 0) return code.slice(from, i + 1);
  }
  return code.slice(from);
}

/** The body of every keydown handler added with addEventListener in ``code``. */
export function keydownHandlers(code: string): string[] {
  const out: string[] = [];
  const re = /addEventListener\(\s*['"]keydown['"]\s*,\s*/g;
  for (let m = re.exec(code); m; m = re.exec(code)) {
    const at = m.index + m[0].length;
    const name = /^[A-Za-z_$][\w$]*/.exec(code.slice(at))?.[0];
    const call = balanced(code, code.indexOf('(', m.index), '(', ')');
    if (name && !code.slice(at + name.length).trimStart().startsWith('(') && !/^(async|function)$/.test(name)) {
      // a handler by name: its definition (const name = … / function name(…) { … })
      const def = new RegExp(`(?:const|let|function)\\s+${name}\\b`).exec(code);
      out.push(def ? balanced(code, code.indexOf('{', def.index), '{', '}') : call);
    } else {
      out.push(call);
    }
  }
  return out;
}

describe('Escape belongs to the layer stack', () => {
  it('no keydown listener on the document or the window reacts to Escape, outside hooks/useLayer', () => {
    const offenders = sources(SRC)
      .map((p) => [relative(SRC, p), stripComments(readFileSync(p, 'utf8'))] as const)
      .filter(([rel]) => rel !== OWNER)
      .filter(([, code]) => keydownHandlers(code).some((h) => /Escape|['"]Esc['"]|keyCode\s*===?\s*27/.test(h)))
      .map(([rel]) => rel);
    expect(offenders, 'register with useEscape / useDialogLayer (hooks/useLayer) instead').toEqual([]);
  });

  it('the scan finds a handler by name and an inline one (so a clean result means something)', () => {
    const named = `const onKey = (e) => { if (e.key === 'Escape') close(); };\ndocument.addEventListener('keydown', onKey);`;
    const inline = `window.addEventListener("keydown", (e) => { if (e.key === 'Escape') close(); }, true);`;
    const other = `const onKey = (e) => { if (e.key === 'Tab') trap(); };\nwindow.addEventListener('keydown', onKey);`;
    expect(keydownHandlers(named).some((h) => h.includes('Escape'))).toBe(true);
    expect(keydownHandlers(inline).some((h) => h.includes('Escape'))).toBe(true);
    expect(keydownHandlers(other).some((h) => h.includes('Escape'))).toBe(false);
  });
});
