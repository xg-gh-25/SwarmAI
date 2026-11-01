/**
 * Every `ui.<File>.<key>` string in the locales is read by some source file.
 *
 * The `ui.*` namespace holds the text moved out of components into i18n. A key that
 * no component reads any more is dead text that translators keep maintaining (a merge
 * can bring back keys for a feature that was removed). A key counts as read when its
 * full name, its name without a plural suffix (_one/_other), or a template family
 * prefix (`ui.X.name_${...}`) appears in a non-test source file.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import en from '../locales/en.json';

const SRC = resolve(__dirname, '../..');

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === '__tests__' || name === 'locales') continue;
      out.push(...sources(p));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.(ts|tsx)$/.test(name)) {
      out.push(readFileSync(p, 'utf8'));
    }
  }
  return out;
}

function leaves(obj: Record<string, unknown>, prefix: string): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' ? leaves(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`]);
}

describe('ui.* locale keys', () => {
  it('every ui.* key is read by a source file', () => {
    const code = sources(SRC).join('\n');
    const families = new Set(Array.from(code.matchAll(/(ui\.[A-Za-z0-9_]+\.[A-Za-z0-9_]*)\$\{/g), (m) => m[1]));
    const ui = (en as Record<string, unknown>).ui as Record<string, unknown>;
    const keys = leaves(ui, 'ui.');
    expect(keys.length).toBeGreaterThan(1000); // the namespace was read at all
    const unread = keys.filter((key) => {
      const base = key.replace(/_(one|other)$/, '');
      if (code.includes(`'${key}'`) || code.includes(`'${base}'`) || code.includes(`"${base}"`)
          || code.includes(`\`${base}\``) || code.includes(`${base}'`)) return false;
      return ![...families].some((f) => key.startsWith(f));
    });
    expect(unread).toEqual([]);
  });

  it('the removed Save to Memory button left no text behind', () => {
    const message = ((en as Record<string, any>).chat?.message ?? {}) as Record<string, unknown>;
    expect(Object.keys(message).filter((k) => k.startsWith('save'))).toEqual([]);
  });
});
