/**
 * URL path contract test — closes the double-/api-prefix bug CLASS at the source.
 *
 * THE CONTRACT: the shared axios instance (services/api.ts) has a request
 * interceptor that unconditionally sets `config.baseURL = \`${getApiBaseUrl()}/api\``
 * (api.ts). So EVERY path handed to that shared `api` instance MUST be BARE —
 * `api.get('/attention')`, NOT `api.get('/api/attention')`. A `/api`-prefixed path
 * resolves to `…/api/api/…` → 404 (community坏13h bug 733a6f5e; attention.ts:74).
 *
 * A per-caller fix (fixing one service at a time) is a RULE, not a structural
 * guarantee — the next caller re-introduces it. This test asserts, across ALL
 * service files at once, that NO `api.<verb>(...)` call passes a URL literal
 * beginning with `/api/`. One test → the whole class is RED-detectable.
 *
 * ── HONEST SCOPE (do NOT oversell this as "closes the whole class") ──
 *  - COVERS: `api.<verb>('<literal>' | \`<literal>…\`)` — the shared-instance
 *    calls with an inline string/template literal as the first arg. This is the
 *    exact form of the attention.ts / community.ts bug.
 *  - DOES NOT COVER (documented, out of this test's reach — Gate-1 finding):
 *    (a) `api.get(url)` where `url` is a VARIABLE built elsewhere (the scan sees
 *        an identifier, not a string). A future variable built with `/api/` slips.
 *    (b) The raw-`fetch(\`${getApiBaseUrl()}/api/…\`)` family (skills.ts, tasks.ts,
 *        voice.ts, pollinate.ts, chat.ts, logForwarder.ts). Those are CORRECT —
 *        getApiBaseUrl() returns the base WITHOUT /api, so the raw-fetch path must
 *        add /api itself. This test intentionally excludes them (they don't use the
 *        interceptor). Flagging them would be a false positive.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const SERVICES_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * Match a call on the shared `api` instance whose first argument is a string or
 * template literal beginning with `/api/` or `/api'` / `/api\``.
 *   api.get('/api/x')      api.post(`/api/x${q}`)      api.delete("/api/y")
 * The URL literal opens with a quote/backtick immediately followed by `/api`
 * then a boundary (`/`, quote, backtick, or `${`).
 */
const DOUBLE_PREFIX_RE =
  /\bapi\s*\.\s*(?:get|post|put|delete|patch|request)\s*(?:<[^>]*>)?\s*\(\s*[`'"]\/api(?:\/|['"`]|\$\{)/;

function listServiceFiles(): string[] {
  return readdirSync(SERVICES_DIR)
    .filter((f) => f.endsWith('.ts') && !f.endsWith('.d.ts'))
    .filter((f) => !f.endsWith('.test.ts') && !f.endsWith('.property.test.ts'));
}

describe('URL path contract — shared axios instance takes BARE paths', () => {
  it('no service passes a /api-prefixed literal to the shared api.<verb>() instance', () => {
    const offenders: string[] = [];
    for (const file of listServiceFiles()) {
      const src = readFileSync(join(SERVICES_DIR, file), 'utf-8');
      const lines = src.split('\n');
      lines.forEach((line, i) => {
        if (DOUBLE_PREFIX_RE.test(line)) {
          offenders.push(`${file}:${i + 1}  ${line.trim()}`);
        }
      });
    }
    expect(
      offenders,
      `These call the shared \`api\` instance (baseURL already ends in /api) with a ` +
        `/api-prefixed path → resolves to /api/api/… → 404. Drop the /api prefix:\n` +
        offenders.join('\n'),
    ).toEqual([]);
  });

  it('the regex actually catches the known-bad form (guard against a vacuous test)', () => {
    // If this ever fails, the DOUBLE_PREFIX_RE was loosened into a no-op.
    expect(DOUBLE_PREFIX_RE.test("const r = await api.get<Raw>(`/api/attention${q}`);")).toBe(true);
    expect(DOUBLE_PREFIX_RE.test("await api.post('/api/community/feeds', feed);")).toBe(true);
    // And does NOT flag the correct bare form, nor the raw-fetch family.
    expect(DOUBLE_PREFIX_RE.test("const r = await api.get<Raw>(`/attention${q}`);")).toBe(false);
    expect(DOUBLE_PREFIX_RE.test("fetch(`${apiBase}/api/skills/generate-with-agent`, {")).toBe(false);
  });
});
