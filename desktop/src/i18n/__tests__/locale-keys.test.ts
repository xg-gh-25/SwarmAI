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
    // reasonCode: backend cause codes (pipeline_stage_events._PLAIN_REASONS); zone.verb:
    // TurnZones' `chat.turn.zone.verb.${kind}` (turnZones VERBS); run.status: RunLine's
    // `chat.turn.run.status.${status}` (pipeline_stage_events.RUN_END_STATUSES)
    const dynamic = /^chat\.turn\.(subAgentStatus|attention\.reasonCode|zone\.verb|run\.status)\./;
    const dead = [...new Set(Object.keys(EN).map((k) => k.replace(/_(one|other)$/, '')))]
      .filter((k) => /^chat\.(turn|file)\./.test(k) && !dynamic.test(k))
      .filter((k) => !all.includes(`'${k}'`) && !all.includes(`"${k}"`) && !all.includes(`\`${k}\``));
    expect(dead).toEqual([]);
  });

  it('dynamic families are complete', () => {
    for (const s of ['failed', 'stopped']) expect(EN).toHaveProperty([`chat.turn.subAgentStatus.${s}`]);
    for (const k of ['explore', 'edit', 'run']) expect(EN).toHaveProperty([`chat.turn.zone.verb.${k}`]);
    // a run's end status (backend pipeline_stage_events.RUN_END_STATUSES) and an
    // escalation's severity (EscalationBlock's severity union)
    for (const k of ['completed', 'abandoned', 'failed']) expect(EN).toHaveProperty([`chat.turn.run.status.${k}`]);
    for (const k of ['low', 'medium', 'high', 'critical']) expect(EN).toHaveProperty([`chat.escalation.severity.${k}`]);
  });

  it('no object in en.json or zh.json names a key twice (JSON keeps the last one silently)', () => {
    // A duplicate once gave two meanings one key: the stop button ("Stop this agent")
    // read the later "background agent {{name}} stopped" line. Importing the JSON hides
    // it, so read the raw text: a key token is a string followed by a colon.
    const dupes = (raw: string): string[] => {
      const out: string[] = [];
      const stack: Set<string>[] = [];
      const path: string[] = [];
      let pending: string | null = null;
      for (let i = 0; i < raw.length; i++) {
        const c = raw[i];
        if (c === '"') {
          let j = i + 1;
          while (raw[j] !== '"') j += raw[j] === '\\' ? 2 : 1;
          const str = raw.slice(i + 1, j);
          i = j;
          if (/^\s*:/.test(raw.slice(j + 1))) {
            const keys = stack[stack.length - 1];
            if (keys.has(str)) out.push([...path, str].join('.'));
            keys.add(str);
            pending = str;
          }
        } else if (c === '{') { stack.push(new Set()); if (pending !== null && stack.length > 1) path.push(pending); pending = null; }
        else if (c === '}') { stack.pop(); if (stack.length >= 1) path.pop(); }
        else if (c === ',') pending = null;
      }
      return out;
    };
    const dir = join(SRC, 'i18n', 'locales');
    const found = ['en.json', 'zh.json'].flatMap((f) => dupes(readFileSync(join(dir, f), 'utf-8')).map((k) => `${f}: ${k}`));
    expect(found).toEqual([]);
    expect(dupes('{ "a": { "b": 1, "b": 2 }, "c": 3 }')).toEqual(['a.b']);   // the scan sees one
  });

  it('a chat.* string in zh.json is not the English sentence copied over', () => {
    // New chat keys were once added "in English in both locales" and stayed so. A
    // single word (Agent) or a value with no English words may legitimately match.
    const copied = Object.keys(EN).filter((k) => /^chat\./.test(k) && ZH[k] === EN[k]
      && /[A-Za-z]+\s+[A-Za-z]+/.test(EN[k]));
    expect(copied).toEqual([]);
  });

  it('chat.turn never hardcodes the assistant name or calls it "AI"', () => {
    const bad = Object.entries({ ...EN, ...Object.fromEntries(Object.entries(ZH).map(([k, v]) => [`zh:${k}`, v])) })
      .filter(([k, v]) => /chat\.turn\./.test(k) && /\bSwarm\b|(^|[^A-Za-z])AI([^A-Za-z]|$)/.test(v));
    expect(bad).toEqual([]);
  });
});
