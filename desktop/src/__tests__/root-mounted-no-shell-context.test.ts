/**
 * CLASS-DEFENSE (loud, primary): App-root-mounted components must NOT depend on
 * shell-only context.
 *
 * Why this exists (the bug this locks out): components rendered at the App ROOT
 * (siblings of AppRoutes in App.tsx) live OUTSIDE LayoutProvider — that provider
 * only exists inside ThreeColumnLayout (the app shell). So a root-mounted component
 * that calls useLayout() throws "useLayout must be used within a LayoutProvider"
 * at render, the single app-level ErrorBoundary catches it, and the WHOLE APP boot-
 * crashes to "Something went wrong" — every launch. This actually shipped: the
 * method-aware CredentialBanner rework added a useLayout() "Open Settings" deep-link
 * at the App root (commit 1c0e5767) → deterministic boot crash. It is COMPILE-TIME
 * INVISIBLE (useLayout is a valid import; the provider-boundary violation is runtime).
 *
 * This is a SOURCE-SCAN (not a render test) on purpose: rendering these components
 * bare throws for unrelated reasons (missing HealthProvider, Tauri module-top imports)
 * → a render test would give a false signal about mocking, not about the shell-context
 * class. Reading the source text catches the exact class — a forbidden import — with
 * zero mocks. ENFORCEMENT: this runs in the CI frontend job (`.github/workflows/ci.yml`
 * → `npx vitest run` on push + PR to main), NOT a local pre-push hook (there is none;
 * the only local hook is pre-commit doc/SDK-sync). So a violation is caught at push/PR
 * CI, not commit time — and merge-blocking requires the frontend job be a required
 * status check in branch protection. An eslint rule would be weaker still (eslint is
 * not in the CI test job).
 *
 * If you add a NEW component to the App root (a new sibling of AppRoutes in App.tsx),
 * add its source path to ROOT_MOUNTED_COMPONENTS below. The list is the invariant's
 * subject — keep it in sync with App.tsx:126-148.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const COMMON = resolve(__dirname, '../components/common');

// Every component rendered at the App ROOT (sibling of AppRoutes), from
// App.tsx:126-148. These render OUTSIDE LayoutProvider — none may consume
// shell-only context. Keep in sync with App.tsx's root render block.
const ROOT_MOUNTED_COMPONENTS: Record<string, string> = {
  CredentialBanner: `${COMMON}/CredentialBanner.tsx`,
  BackendUpgradeBanner: `${COMMON}/BackendUpgradeBanner.tsx`,
  UpdateNotification: `${COMMON}/UpdateNotification.tsx`,
  ShutdownOverlay: `${COMMON}/ShutdownOverlay.tsx`,
  BackendStartupOverlay: `${COMMON}/BackendStartupOverlay.tsx`,
  ToastStack: `${COMMON}/ToastStack.tsx`,
  AudioKeepAlive: resolve(__dirname, '../components/AudioKeepAlive.tsx'),
  // PostUpdateToast is defined inline in App.tsx (not a separate module) — it is
  // covered by the App.tsx self-scan below, not this per-file loop.
};

// Shell-only contexts: their providers live ONLY inside ThreeColumnLayout, so a
// root-mounted component (above the shell) that imports the context or calls one of
// its hooks reproduces the boot-crash class. Verified against ThreeColumnLayout.tsx:
//   LayoutProvider (:959)  → useLayout, useSessionMeta   (LayoutContext.tsx:31,286)
//   TerminalProvider (:960) → useTerminal                (TerminalContext.tsx:132)
//   ExplorerProvider (:878) → useTreeData/useSelection/useSearch (ExplorerContext.tsx:631/638/645)
// App-root-SAFE (do NOT forbid — their providers wrap the banners in App.tsx:122-125):
//   ThemeProvider→useTheme, ToastProvider→useToast, HealthProvider→useHealth.
// We match CODE, not any substring: an import from a shell-only *Context module, OR a
// call to a shell-only hook. The `\s*\(` on calls structurally excludes React's
// useLayoutEffect( (unrelated built-in). Keep this set in sync with the providers
// nested inside ThreeColumnLayout.
const SHELL_HOOKS = ['useLayout', 'useSessionMeta', 'useTerminal', 'useTreeData', 'useSelection', 'useSearch'];
const SHELL_CONTEXT_MODULES = ['LayoutContext', 'TerminalContext', 'ExplorerContext'];
const FORBIDDEN_IMPORT = new RegExp(
  `\\bfrom\\s+['"][^'"]*(?:${SHELL_CONTEXT_MODULES.join('|')})['"]` +
    `|import\\b[^;\\n]*\\b(?:${SHELL_HOOKS.join('|')})\\b`,
);
const FORBIDDEN_CALL = new RegExp(`\\b(?:${SHELL_HOOKS.join('|')})\\s*\\(`);

/**
 * Scan source for shell-only-context usage, robustly ignoring comments.
 * Strips block comments (`/* ... *​/`) and line comments (`// ...`) BEFORE
 * matching, so a JSDoc/inline mention of useLayout (like this file's own header,
 * or CredentialBanner's docstring explaining why it must NOT use useLayout) never
 * false-flags — and code after a `*​/` on the same line is never missed. Returns
 * the offending {line, n} entries (empty = clean).
 */
function scanForShellContext(src: string): Array<{ line: string; n: number }> {
  // Remove block comments across the whole file (keeps newlines so line numbers stay true).
  const noBlock = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
  return noBlock
    .split('\n')
    .map((raw, i) => ({ line: raw.replace(/\/\/.*$/, '').trim(), n: i + 1 }))
    .filter(({ line }) => FORBIDDEN_IMPORT.test(line) || FORBIDDEN_CALL.test(line));
}

describe('App-root components must not depend on shell-only context (boot-crash class)', () => {
  for (const [name, path] of Object.entries(ROOT_MOUNTED_COMPONENTS)) {
    it(`${name} does not import/use useLayout or LayoutContext`, () => {
      const offending = scanForShellContext(readFileSync(path, 'utf8'));
      expect(
        offending,
        `${name} (root-mounted, outside LayoutProvider) references shell-only ` +
          `context — this boot-crashes the whole app. Use a window event (see ` +
          `CredentialBanner's swarm:open-settings pattern), not useLayout.\n` +
          offending.map((o) => `  L${o.n}: ${o.line}`).join('\n'),
      ).toEqual([]);
    });
  }

  it('App.tsx root render block does not use useLayout (covers inline PostUpdateToast)', () => {
    // App.tsx itself must not consume useLayout — LayoutProvider is mounted
    // deeper (inside ThreeColumnLayout), so the App component + its inline
    // children (PostUpdateToast) are above it.
    const offending = scanForShellContext(readFileSync(resolve(__dirname, '../App.tsx'), 'utf8'));
    expect(offending, `App.tsx references shell-only context above LayoutProvider`).toEqual([]);
  });

  it('ROOT_MOUNTED_COMPONENTS covers every imported component rendered at App root (list-drift guard)', () => {
    // Prevents the known gap: a dev adds a new banner to App.tsx's root block but
    // forgets to add it to ROOT_MOUNTED_COMPONENTS → it is never scanned → the class
    // recurs undetected. Marker-bounded (not full AST — proportionate for one block):
    // extract JSX component tags between the app-level ErrorBoundary open and its close,
    // then assert each imported component is either scanned (in the list) or explicitly
    // exempt (a wrapper, or defined inline in App.tsx and covered by the self-scan above).
    const appSrc = readFileSync(resolve(__dirname, '../App.tsx'), 'utf8');
    const start = appSrc.indexOf('<ErrorBoundary variant="app">');
    // The app-level boundary is the OUTERMOST — its close is the LAST </ErrorBoundary>
    // in the file. indexOf (first close) would stop at an INNER banner-isolation
    // boundary (<ErrorBoundary fallback={null}>…</ErrorBoundary>) and truncate the
    // block, silently missing every component after the first wrapped banner.
    const end = appSrc.lastIndexOf('</ErrorBoundary>');
    expect(start, 'app-level ErrorBoundary open marker must exist').toBeGreaterThan(-1);
    expect(end, 'app-level ErrorBoundary close marker must exist').toBeGreaterThan(start);
    const block = appSrc.slice(start, end);
    const tags = new Set([...block.matchAll(/<([A-Z][A-Za-z]+)/g)].map((m) => m[1]));

    // Exempt: NOT App-root components that need the shell-context scan.
    const EXEMPT = new Set([
      'ErrorBoundary', // the boundary wrapper itself (not a scanned banner)
      'AppRoutes', // defined inline in App.tsx — the mount gate, covered by the App.tsx self-scan
      'PostUpdateToast', // defined inline in App.tsx — covered by the App.tsx self-scan
      // SwarmToastBridge — also defined INLINE in App.tsx (:229, the one
      // document `swarm:toast` → ToastContext bridge), so there is no separate module
      // for the per-file loop to scan. Exempt for the same reason as PostUpdateToast:
      // the App.tsx self-scan test above runs scanForShellContext over the WHOLE file,
      // which covers every inline component. This is not a blind spot — verified.
      // (It was mounted at root without touching this list, which is precisely the
      // drift this guard exists to catch. The guard worked; the list was stale.)
      'SwarmToastBridge',
    ]);
    const scanned = new Set(Object.keys(ROOT_MOUNTED_COMPONENTS));
    const uncovered = [...tags].filter((t) => !scanned.has(t) && !EXEMPT.has(t));
    expect(
      uncovered,
      `App root renders component(s) not in ROOT_MOUNTED_COMPONENTS and not exempt: ` +
        `${uncovered.join(', ')}. Add each to ROOT_MOUNTED_COMPONENTS (so its source is ` +
        `scanned for shell-only context) or to EXEMPT (if inline/wrapper).`,
    ).toEqual([]);
  });
});
