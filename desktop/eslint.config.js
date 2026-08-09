import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'src-tauri']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Allow underscore-prefixed variables to be unused (common pattern for intentionally unused params)
      '@typescript-eslint/no-unused-vars': ['error', { 
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_'
      }],
      // Allow empty interfaces that extend other interfaces (useful for type aliases)
      '@typescript-eslint/no-empty-object-type': 'off',
      // Allow exporting constants/functions alongside components (common pattern)
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // URL path contract (run_72a39300): the shared axios `api` instance (services/api.ts)
      // has a request interceptor that ALREADY prepends `/api` to baseURL — so every path
      // handed to api.<verb>() MUST be BARE (`/attention`), never `/api/...` (that
      // double-prefixes → `/api/api/…` → 404; the community坏13h bug + attention.ts:74).
      // This is the AST-level structural twin of urlContract.test.ts (kept as belt-and-
      // suspenders). SCOPE — honestly bounded (Gate-1 verified): catches an inline STRING
      // or TEMPLATE first-arg literal starting with `/api/` (or bare `/api`). Does NOT
      // catch a variable-built URL (`api.get(url)` — Identifier, no literal to inspect) nor
      // the `api.request({url})` config-object form (excluded from the verb list below —
      // it has zero usages). Those residuals are the same class the vitest can't reach.
      'no-restricted-syntax': ['error',
        {
          selector: "CallExpression[callee.object.name='api'][callee.property.name=/^(get|post|put|delete|patch)$/] > Literal.arguments:first-child[value=/^[/]api([/]|$)/]",
          message: "Double `/api` prefix: the shared `api` axios instance already prepends /api (services/api.ts interceptor). Pass a BARE path — e.g. api.get('/attention'), not api.get('/api/attention').",
        },
        {
          selector: "CallExpression[callee.object.name='api'][callee.property.name=/^(get|post|put|delete|patch)$/] > TemplateLiteral.arguments:first-child > TemplateElement:first-child[value.raw=/^[/]api([/]|$)/]",
          message: "Double `/api` prefix: the shared `api` axios instance already prepends /api (services/api.ts interceptor). Pass a BARE path — e.g. api.get(`/attention${q}`), not api.get(`/api/attention${q}`).",
        },
      ],
    },
  },
])
