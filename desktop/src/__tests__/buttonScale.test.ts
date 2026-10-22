/**
 * Button scale guard — labelled buttons use the ONE scale in index.css.
 *
 * Invariants, for every <button> in the scoped folders:
 * - a button that looks like a button (a surface: a border or a resting background)
 *   and sets its own padding or type size uses btn-sm / btn-md / btn-lg instead;
 * - an icon inside a btn-* button has that tier's glyph: icon-xs in btn-sm, icon-sm in
 *   btn-md, icon-md in btn-lg.
 * Rows, tabs and links that are buttons only for semantics (no surface), and
 * clickable cards / list rows (`text-left`: a control centres its label, a card or a
 * row aligns its content left), are not buttons in the visual sense and are not
 * checked here.
 *
 * Scope: all of desktop/src.
 *
 * Methodology: a static scan; a failure names file:line.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');
const SCOPE = ['.'];   // the whole app

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== '__tests__') out.push(...files(p));
    } else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

const SIZE = /(?<![\w-])btn-(sm|md|lg)(?![\w-])/;
const OWN_GEOMETRY = /(?<![\w:-])(px-|py-|text-(xs|sm|base|\[\d+(?:\.\d+)?px\]))/;
// a resting surface: an all-sides border (`border`, `border-2`), or a background.
// A one-sided border (border-t, a section divider) or a border colour alone is not.
const SURFACE = /(?<![\w:-])(border(?=[\s'"`]|$)|border-[0-9](?![\w-])|bg-)/;

function buttons(): { where: string; cls: string; body: string }[] {
  const out: { where: string; cls: string; body: string }[] = [];
  for (const dir of SCOPE) {
    for (const f of files(join(SRC, dir))) {
      const s = readFileSync(f, 'utf8');
      for (const m of s.matchAll(/<button\b/g)) {
        const close = s.indexOf('</button>', m.index!);
        if (close < 0) continue;
        const open = s.slice(m.index!, close);
        const cm = open.match(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{clsx\(([\s\S]*?)\)\}|\{(\w+)\})/);
        let cls = cm ? (cm[1] ?? cm[2] ?? cm[3] ?? '') : '';
        if (cm?.[4]) {   // className={name}: the const it names, in the same file
          const d = s.match(new RegExp(`const ${cm[4]} = '([^']*)'`));
          cls = d ? d[1] : '';
        }
        out.push({ where: `${relative(SRC, f)}:${s.slice(0, m.index!).split('\n').length}`, cls, body: open });
      }
    }
  }
  return out;
}

/** The classes a button always has: hover / focus / active variants and the branches
 *  of a conditional (a selected state of a row, tab or toggle) removed. */
function resting(cls: string): string {
  return cls
    .replace(/\$\{[^}]*\}/g, ' ')                     // template-literal branches
    .replace(/\?\s*'[^']*'\s*:\s*'[^']*'/g, ' ')        // a ? 'x' : 'y'
    .replace(/&&\s*'[^']*'/g, ' ')                       // cond && 'x'
    .replace(/(hover|focus|active|group-hover|focus-visible|disabled)[\w/-]*:[^\s'"`]+/g, ' ');
}

describe('button scale', () => {
  it('a button with a surface takes its size from btn-*, not its own padding / type', () => {
    const bad = buttons()
      .filter((b) => !SIZE.test(b.cls) && !/icon-btn/.test(b.cls) && !/(?<![\w-])text-left(?![\w-])/.test(b.cls))
      .filter((b) => SURFACE.test(resting(b.cls)) && OWN_GEOMETRY.test(b.cls))
      .map((b) => `${b.where}: ${b.cls.replace(/\s+/g, ' ').trim().slice(0, 120)}`);
    expect(bad).toEqual([]);
  });

  it('an icon in a btn-* button has the tier glyph', () => {
    const want = { sm: 'icon-xs', md: 'icon-sm', lg: 'icon-md' } as const;
    const bad: string[] = [];
    for (const b of buttons()) {
      const size = b.cls.match(SIZE)?.[1] as keyof typeof want | undefined;
      if (!size) continue;
      for (const ic of b.body.matchAll(/material-symbols-outlined[^"'`]*/g)) {
        const tier = ic[0].match(/icon-(xs|sm|md|lg|xl)/)?.[0];
        if (tier && tier !== want[size]) bad.push(`${b.where}: btn-${size} holds ${tier}, wants ${want[size]}`);
      }
    }
    expect(bad).toEqual([]);
  });
});
