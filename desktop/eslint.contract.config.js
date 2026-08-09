/**
 * SCOPED lint config: the URL path contract ONLY — nothing else.
 *
 * WHY THIS FILE EXISTS (run_a1f4c2d8)
 * ----------------------------------
 * run_72a39300 added a `no-restricted-syntax` rule to eslint.config.js to close the
 * double-`/api`-prefix bug CLASS structurally (the community-broken-13h bug 733a6f5e +
 * attention.ts:74). But that rule NEVER EXECUTED:
 *   - `.github/workflows/ci.yml` has NO eslint step (the frontend job runs only
 *     `tsc --noEmit`, `vitest run`, `npm run build`), and
 *   - `npx eslint .` is RED anyway — 68 problems / 30 errors of PRE-EXISTING
 *     `no-explicit-any` + `no-unused-vars` in unrelated files — so nobody can use it
 *     as a gate.
 * A rule that nothing runs is prose. This repo has now shipped SIX guards that never
 * executed (`_get_session_router` NameError, `self._pid`, the inert reconciliation
 * endpoint, ...), so "write the rule" is not the same as "close the class".
 *
 * THE FIX, WITHOUT A 30-ERROR CLEANUP FIRST
 * ----------------------------------------
 * This config inherits NOTHING (no `js.configs.recommended`, no
 * `tseslint.configs.recommended`) — it carries the single contract rule and the TS
 * parser needed to read `.ts`/`.tsx`. So `npm run lint:contract` is green TODAY and can
 * go into CI immediately, while the full `npm run lint` cleanup stays a separate,
 * unblocked task. The rule is duplicated here rather than imported from
 * eslint.config.js because that file's flat-config array cannot be sliced without
 * dragging its `extends` presets along — and a drift guard is cheaper than the
 * refactor: `urlContract.test.ts` asserts BOTH files carry the same contract.
 *
 * RELATIONSHIP TO THE VITEST TWIN
 * -------------------------------
 * `src/services/__tests__/urlContract.test.ts` regex-scans service files for the same
 * class and DOES run in CI. This config is the AST-level twin: it understands syntax
 * (so a reformatted / multi-line call still matches, where a line-based regex would
 * miss it) and covers `.tsx` components, not just `services/*.ts`. Belt and suspenders,
 * both executing.
 *
 * SCOPE — honestly bounded, identical to the rule in eslint.config.js: catches an
 * inline STRING or TEMPLATE first-arg literal starting with `/api`. Does NOT catch a
 * variable-built URL (`api.get(url)` — an Identifier, no literal to inspect) nor the
 * `api.request({url})` config-object form (zero usages today). Raw
 * `fetch(\`${getApiBaseUrl()}/api/…\`)` callers are CORRECT and deliberately unmatched:
 * getApiBaseUrl() returns the base WITHOUT `/api`, so those must add it themselves.
 */
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// The ONE contract. Kept as a named export so a drift test can assert that
// eslint.config.js carries the same selectors (see urlContract.test.ts).
export const URL_CONTRACT_RULES = [
  {
    selector:
      "CallExpression[callee.object.name='api'][callee.property.name=/^(get|post|put|delete|patch)$/] > Literal.arguments:first-child[value=/^[/]api([/]|$)/]",
    message:
      "Double `/api` prefix: the shared `api` axios instance already prepends /api (services/api.ts interceptor). Pass a BARE path — e.g. api.get('/attention'), not api.get('/api/attention').",
  },
  {
    selector:
      "CallExpression[callee.object.name='api'][callee.property.name=/^(get|post|put|delete|patch)$/] > TemplateLiteral.arguments:first-child > TemplateElement:first-child[value.raw=/^[/]api([/]|$)/]",
    message:
      'Double `/api` prefix: the shared `api` axios instance already prepends /api (services/api.ts interceptor). Pass a BARE path — e.g. api.get(`/attention${q}`), not api.get(`/api/attention${q}`).',
  },
]

export default defineConfig([
  globalIgnores(['dist', 'src-tauri', 'node_modules']),
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 2020,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    linterOptions: {
      // Ignore ALL inline eslint comments. Two reasons, both load-bearing:
      //  1. CORRECTNESS: the tree is full of `// eslint-disable-next-line
      //     @typescript-eslint/no-explicit-any` etc. Those rules are not registered in
      //     this minimal config, and eslint hard-errors with "Definition for rule ...
      //     was not found" on a disable comment naming an unknown rule — 63 such
      //     errors, none of them about the contract. Registering the plugins just to
      //     satisfy the comments would drag their presets back in, defeating the point.
      //  2. NO ESCAPE HATCH: it also means nobody can `// eslint-disable-next-line
      //     no-restricted-syntax` past this gate. Same stance as the Python sibling
      //     gates, which deliberately ship without a `# noqa` (a bypassable whitelist
      //     is the C041 trap). The sanctioned fix is to pass a bare path.
      noInlineConfig: true,
    },
    rules: {
      'no-restricted-syntax': ['error', ...URL_CONTRACT_RULES],
    },
  },
])
