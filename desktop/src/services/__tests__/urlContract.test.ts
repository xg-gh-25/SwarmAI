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

/**
 * DRIFT GUARD (run_a1f4c2d8) — the AST tier of this contract lives in TWO eslint
 * configs and they must not diverge:
 *   - `eslint.config.js`         — the full dev ruleset (`npm run lint`), currently RED
 *                                   on 30 pre-existing unrelated errors, so it gates nothing.
 *   - `eslint.contract.config.js` — a SCOPED config carrying only this contract; green,
 *                                   and wired into CI as `npm run lint:contract`.
 * The rule is duplicated because a flat-config array cannot be sliced without dragging
 * its `extends` presets along. Duplication is only safe with a guard, so: assert both
 * files carry the SAME selectors. If someone tightens one and forgets the other, this
 * goes RED — the developer-facing rule and the CI-facing rule can never silently drift.
 */
describe('URL path contract — eslint AST tier', () => {
  const DESKTOP_DIR = join(SERVICES_DIR, '..', '..');

  it('the scoped contract config is wired into CI (not just written)', () => {
    const ci = readFileSync(join(DESKTOP_DIR, '..', '.github', 'workflows', 'ci.yml'), 'utf-8');
    // Match an EXECUTED step (`run: npm run lint:contract`), not any occurrence of the
    // string. A bare `ci.includes('lint:contract')` passes on a mere COMMENT mentioning
    // it — caught by mutation-testing this very assertion (replacing the run: line with
    // `echo skipped` left the explanatory comment behind and the check stayed green).
    // That is the "guard that never executes" class, one level up: a guard that cannot
    // see its own subject.
    expect(
      /run:\s*npm run lint:contract/.test(ci),
      'ci.yml no longer RUNS `npm run lint:contract` — the AST gate is dormant again, ' +
        'which is exactly the state this run fixed (a rule nothing executes is prose).',
    ).toBe(true);

    const pkg = JSON.parse(readFileSync(join(DESKTOP_DIR, 'package.json'), 'utf-8'));
    expect(pkg.scripts['lint:contract']).toBeTruthy();
    expect(pkg.scripts['lint:contract']).toContain('eslint.contract.config.js');
  });

  it('both eslint configs carry the same contract selectors (no drift)', () => {
    const full = readFileSync(join(DESKTOP_DIR, 'eslint.config.js'), 'utf-8');
    const scoped = readFileSync(join(DESKTOP_DIR, 'eslint.contract.config.js'), 'utf-8');

    // Extract every `selector:` string literal from a config source.
    const selectorsOf = (src: string): string[] =>
      [...src.matchAll(/selector:\s*\n?\s*"([^"]+)"/g)].map((m) => m[1]).sort();

    const fullSelectors = selectorsOf(full);
    const scopedSelectors = selectorsOf(scoped);

    expect(
      fullSelectors.length,
      'eslint.config.js has no `selector:` entries — the URL contract rule was removed there',
    ).toBeGreaterThan(0);
    expect(
      scopedSelectors,
      'eslint.contract.config.js (the CI gate) and eslint.config.js (the dev ruleset) ' +
        'carry DIFFERENT selectors — one was edited without the other:\n' +
        `  full:   ${JSON.stringify(fullSelectors, null, 2)}\n` +
        `  scoped: ${JSON.stringify(scopedSelectors, null, 2)}`,
    ).toEqual(fullSelectors);
  });
});
