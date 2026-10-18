/**
 * Locale key consistency — en and zh say the same things, and code only asks for
 * keys that exist (chat naming standard, XG 2026-10-18).
 *
 * What is tested (the real locale JSON and the real source tree):
 * - en.json and zh.json have the SAME key set (a key missing in zh silently
 *   falls back to English — the "· back to latest" pill did exactly that)
 * - every plural `_one` has its `_other`
 * - every static key the source passes to t() / tf() / i18n.t() exists, in every
 *   namespace (as itself or as a plural pair); every template-literal key family
 *   has keys; dynamic families (sub-agent status, settings tabs,
 *   knowledge source types, privileged capability types) are complete
 * - chat.turn strings never hardcode the assistant's name or say "AI": the name
 *   comes from ASSISTANT_NAME via `{{name}}`, at most once per container
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import en from '../locales/en.json';
import zh from '../locales/zh.json';

type Tree = { [k: string]: string | Tree };
const flat = (t: Tree, pre = ''): Record<string, string> =>
  Object.entries(t).reduce<Record<string, string>>((acc, [k, v]) => (
    typeof v === 'string' ? { ...acc, [`${pre}${k}`]: v } : { ...acc, ...flat(v, `${pre}${k}.`) }
  ), {});
const EN = flat(en as Tree);
const ZH = flat(zh as Tree);
const has = (k: string) => k in EN || (`${k}_one` in EN && `${k}_other` in EN);

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
function sources(): string[] {
  return (readdirSync(SRC, { recursive: true }) as string[])
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/__tests__|\.test\./.test(p))
    .map((p) => readFileSync(join(SRC, p), 'utf-8'));
}

describe('locale keys', () => {
  it('en and zh have the same key set', () => {
    const enOnly = Object.keys(EN).filter((k) => !(k in ZH));
    const zhOnly = Object.keys(ZH).filter((k) => !(k in EN));
    expect({ enOnly, zhOnly }).toEqual({ enOnly: [], zhOnly: [] });
  });

  it('every plural _one has its _other', () => {
    const orphan = Object.keys(EN).filter((k) => k.endsWith('_one') && !(`${k.slice(0, -4)}_other` in EN));
    expect(orphan).toEqual([]);
  });

  it('every static chat.turn / chat.file key the source asks for exists', () => {
    const used = new Set<string>();
    for (const s of sources()) {
      for (const m of s.matchAll(/['"`](chat\.(?:turn|file)\.[A-Za-z0-9_.]+)['"`]/g)) used.add(m[1]);
    }
    expect(used.size).toBeGreaterThan(20); // the scan really sees the chat surfaces
    expect([...used].filter((k) => !has(k))).toEqual([]);
  });

  it('every static key passed to t() / tf() / i18n.t() anywhere exists', () => {
    // A missing key shows its fallback in English in every language — or, with no
    // fallback, the raw key itself (settings tabs once showed "settings.skills.empty").
    const used = new Set<string>();
    const call = /\b(?:i18n\.t|t|tf\(\s*t\s*,)\s*\(?\s*(['"])([a-zA-Z]\w*\.[\w.]+)\1/g;
    for (const s of sources()) for (const m of s.matchAll(call)) used.add(m[2]);
    expect(used.size).toBeGreaterThan(200);
    expect([...used].filter((k) => !has(k)).sort()).toEqual([]);
  });

  it('every template-literal key family has keys, and the known enumerations are complete', () => {
    const call = /\b(?:i18n\.t|t|tf\(\s*t\s*,)\s*\(?\s*`([a-zA-Z][\w.]*)\$\{[^}]+\}([\w.]*)`/g;
    const empty: string[] = [];
    for (const s of sources()) {
      for (const m of s.matchAll(call)) {
        const [, pre, suf] = m;
        if (!Object.keys(EN).some((k) => k.startsWith(pre) && k.endsWith(suf))) empty.push(`${pre}\${…}${suf}`);
      }
    }
    expect(empty).toEqual([]);
    // Values the code iterates over (WorkspaceSettingsModal TABS, KnowledgebasesTab
    // SOURCE_TYPES, PrivilegedCapabilityModal capabilityType).
    const enums: Record<string, string[]> = {
      'settings.tabs.': ['skills', 'mcps', 'knowledgebases'],
      'settings.knowledgebases.types.': ['local_file', 'url', 'indexed_document', 'context_file', 'vector_index'],
      'settings.privileged.type.': ['skill', 'mcp'],
    };
    const missing = Object.entries(enums).flatMap(([pre, vals]) => vals.map((v) => pre + v).filter((k) => !(k in EN)));
    expect(missing).toEqual([]);
  });

  it('no dead chat.turn / chat.file keys (dynamic families excepted)', () => {
    const all = sources().join('\n');
    const dynamic = /^chat\.turn\.(subAgentStatus)\./;
    const dead = [...new Set(Object.keys(EN).map((k) => k.replace(/_(one|other)$/, '')))]
      .filter((k) => /^chat\.(turn|file)\./.test(k) && !dynamic.test(k))
      .filter((k) => !all.includes(`'${k}'`) && !all.includes(`"${k}"`) && !all.includes(`\`${k}\``));
    expect(dead).toEqual([]);
  });

  it('dynamic families are complete', () => {
    for (const s of ['failed', 'stopped']) expect(EN).toHaveProperty([`chat.turn.subAgentStatus.${s}`]);
  });

  it('chat.turn never hardcodes the assistant name or calls it "AI"', () => {
    const bad = Object.entries({ ...EN, ...Object.fromEntries(Object.entries(ZH).map(([k, v]) => [`zh:${k}`, v])) })
      .filter(([k, v]) => /chat\.turn\./.test(k) && /\bSwarm\b|(^|[^A-Za-z])AI([^A-Za-z]|$)/.test(v));
    expect(bad).toEqual([]);
  });
});
