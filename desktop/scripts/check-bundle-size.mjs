#!/usr/bin/env node
/**
 * check-bundle-size — fail when the app's startup JS chunks grow past their caps
 * (design docs/design/left-nav-overlay-single-source.md §5.4,
 * docs/design/left-nav-overlay-finish.md §3).
 *
 * Runs as the last step of `npm run build` (so `build:all` and CI's frontend build
 * check it too). Two chunks load at startup, each with its own cap:
 *
 * - The main chunk, the largest dist/assets/index-*.js: code only. Its cap is the
 *   size measured once every overlay, ToDo included, was its own chunk (2026-10-23:
 *   3,737,362 bytes) plus 5%. The locale data that pushed it past that cap now has
 *   its own chunk (vite.config.ts manualChunks), so this cap measures code again.
 * - The locales chunk, dist/assets/locales-*.js: en.json + zh.json, which
 *   src/i18n/index.ts imports statically and index.html modulepreloads. Its cap is
 *   its size when it was split out (2026-11-23: 403,897 bytes) plus 25%: the locale
 *   files grow with every translated string, so the margin is wider than the code's,
 *   and a separate cap keeps that growth visible without eating the code budget.
 *
 * Raise a cap deliberately, in the same change that explains what grew.
 *
 * Usage: node scripts/check-bundle-size.mjs [distDir]   (default: dist)
 */
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const CHUNKS = [
  {
    label: 'main',
    pattern: /^index-.*\.js$/,
    cap: Math.round(3_737_362 * 1.05),
    advice: 'Move the new code behind a dynamic import, or raise its cap',
  },
  {
    label: 'locales',
    pattern: /^locales-.*\.js$/,
    cap: Math.round(403_897 * 1.25),
    advice: 'The locale files grew; raise the locales cap',
  },
];

const dist = process.argv[2] ?? 'dist';
const assets = join(dist, 'assets');

let files;
try {
  files = readdirSync(assets);
} catch {
  console.error(`check-bundle-size: no ${assets} — run vite build first`);
  process.exit(2);
}

const kb = (n) => `${Math.round(n / 1024).toLocaleString()} kB`;
let failed = false;
for (const { label, pattern, cap, advice } of CHUNKS) {
  const matches = files.filter((f) => pattern.test(f));
  if (matches.length === 0) {
    console.error(`check-bundle-size: no ${pattern} (${label} chunk) in ${assets}`);
    process.exit(2);
  }
  const [name, size] = matches
    .map((f) => [f, statSync(join(assets, f)).size])
    .sort((a, b) => b[1] - a[1])[0];
  if (size > cap) {
    console.error(`check-bundle-size: ${label} chunk ${name} is ${kb(size)}, over the ${kb(cap)} cap.`);
    console.error(`  ${advice} in desktop/scripts/check-bundle-size.mjs`);
    console.error('  in the same change and say why.');
    failed = true;
  } else {
    console.log(`check-bundle-size: ${label} chunk ${name} ${kb(size)} (cap ${kb(cap)})`);
  }
}
if (failed) process.exit(1);
