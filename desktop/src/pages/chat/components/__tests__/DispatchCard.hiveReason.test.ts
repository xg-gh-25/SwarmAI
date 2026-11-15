/**
 * Why this Hive, in words (DispatchCard hiveReasonLabel): every code pick_hive and
 * dispatch_spec give (named / only_free / idle / least_busy:<n> / started) is worded in
 * both locales, never shown as the raw code.
 */
import { describe, it, expect } from 'vitest';
import en from '../../../../i18n/locales/en.json';
import zh from '../../../../i18n/locales/zh.json';
import { hiveReasonLabel } from '../DispatchCard';

type Tree = { [k: string]: string | Tree };

function lookup(tree: Tree) {
  return (key: string, o?: { defaultValue?: string } & Record<string, unknown>) => {
    let node: string | Tree | undefined = tree;
    for (const part of key.split('.')) node = typeof node === 'object' ? node[part] : undefined;
    const s = typeof node === 'string' ? node : String(o?.defaultValue ?? key);
    return s.replace(/\{\{(\w+)\}\}/g, (_m, v) => String(o?.[v] ?? ''));
  };
}

describe('hiveReasonLabel', () => {
  for (const [name, tree] of [['en', en], ['zh', zh]] as const) {
    it(`words every reason code in ${name}`, () => {
      const t = lookup(tree as unknown as Tree) as never;
      for (const code of ['named', 'only_free', 'idle', 'least_busy:2', 'started']) {
        const label = hiveReasonLabel(t, code);
        expect(label, code).toBeTruthy();
        expect(label, code).not.toBe(code);
      }
    });
  }

  it('says a stopped Hive was started for the task', () => {
    expect(hiveReasonLabel(lookup(en as unknown as Tree) as never, 'started')).toMatch(/started/);
  });
});
