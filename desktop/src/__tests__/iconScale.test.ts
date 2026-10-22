/**
 * Icon scale guard — every Material icon is sized by the ONE scale in index.css.
 *
 * Invariant: each source literal that names `material-symbols-outlined` carries
 * exactly one tier class (icon-xs / -sm / -md / -lg / -xl), no Tailwind text size
 * (text-xs … text-6xl, text-[Npx]) and no inline fontSize on that element. Without
 * this, sizes drift back to one-off pixel values and the app stops looking like one
 * thing (2026-10-22: 16 different sizes across ~400 icons). And an icon button's
 * glyph matches its button: icon-sm inside icon-btn, icon-md inside icon-btn-lg.
 *
 * Methodology: a static scan of desktop/src (tests excluded) with the same literal
 * boundary rule the migration used; a failure names file:line.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');
const TIER = /(?<![\w-])icon-(xs|sm|md|lg|xl)(?![\w-])/g;
const TEXT_SIZE = /(?<![\w-])text-(xs|sm|base|lg|xl|[2-9]xl|\[\d+(?:\.\d+)?px\])(?![\w-])/;

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== '__tests__' && name !== 'node_modules') out.push(...files(p));
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

function offenders(): string[] {
  const bad: string[] = [];
  for (const f of files(SRC)) {
    const s = readFileSync(f, 'utf8');
    let i = s.indexOf('material-symbols-outlined');
    while (i >= 0) {
      const starts = ['"', "'", '`'].map((c) => [s.lastIndexOf(c, i), c] as const);
      const [start, ch] = starts.reduce((a, b) => (b[0] > a[0] ? b : a));
      const end = s.indexOf(ch, i);
      const lit = s.slice(start, end);
      const line = s.slice(0, i).split('\n').length;
      const where = `${relative(SRC, f)}:${line}`;
      const isImport = /^\s*import\b/.test(s.slice(s.lastIndexOf('\n', i) + 1, i));
      if (!isImport) {
        const tiers = lit.match(TIER) ?? [];
        if (tiers.length !== 1) bad.push(`${where} has ${tiers.length} icon-* classes`);
        if (TEXT_SIZE.test(lit)) bad.push(`${where} sizes an icon with ${lit.match(TEXT_SIZE)![0]}`);
        const tagEnd = s.indexOf('>', end);
        if (/fontSize/.test(s.slice(end, tagEnd))) bad.push(`${where} sizes an icon with an inline fontSize`);
      }
      i = s.indexOf('material-symbols-outlined', i + 1);
    }
  }
  return bad;
}

function buttonMismatches(): string[] {
  const bad: string[] = [];
  const BTN = /(?<![\w-])icon-btn(-lg)?(?![\w-])/g;
  for (const f of files(SRC)) {
    const s = readFileSync(f, 'utf8');
    for (const m of s.matchAll(BTN)) {
      const close = s.indexOf('</button>', m.index!);
      const nextBtn = s.indexOf('<button', m.index! + 1);
      if (close < 0 || (nextBtn >= 0 && nextBtn < close)) continue;   // not a button's class
      const body = s.slice(m.index!, close);
      const want = m[1] ? 'icon-md' : 'icon-sm';
      for (const ic of body.matchAll(/material-symbols-outlined[^"'`]*/g)) {
        const tier = ic[0].match(/icon-(xs|sm|md|lg|xl)/)?.[0];
        if (tier && tier !== want) {
          bad.push(`${relative(SRC, f)}:${s.slice(0, m.index!).split('\n').length} ${m[0]} holds ${tier}, wants ${want}`);
        }
      }
    }
  }
  return bad;
}

describe('icon scale', () => {
  it('an icon button holds the glyph size of its tier', () => {
    expect(buttonMismatches()).toEqual([]);
  });

  it('every Material icon uses exactly one icon-* tier and nothing else for its size', () => {
    expect(offenders()).toEqual([]);
  });
});
