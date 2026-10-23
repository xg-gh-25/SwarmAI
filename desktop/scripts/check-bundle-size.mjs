#!/usr/bin/env node
/**
 * check-bundle-size — fail when the app's first JS chunk grows past its cap
 * (design docs/design/left-nav-overlay-single-source.md §5.4,
 * docs/design/left-nav-overlay-finish.md §3).
 *
 * Runs as the last step of `npm run build` (so `build:all` and CI's frontend build
 * check it too). Reads dist/assets/index-*.js and compares the largest one with
 * CAP_BYTES. The cap is the size measured once every overlay, ToDo included, was
 * its own chunk (2026-10-23: 3,737,362 bytes) plus 5%. Raise it deliberately, in
 * the same change that explains what grew.
 *
 * Usage: node scripts/check-bundle-size.mjs [distDir]   (default: dist)
 */
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const CAP_BYTES = Math.round(3_737_362 * 1.05);
const dist = process.argv[2] ?? 'dist';
const assets = join(dist, 'assets');

let files;
try {
  files = readdirSync(assets).filter((f) => /^index-.*\.js$/.test(f));
} catch {
  console.error(`check-bundle-size: no ${assets} — run vite build first`);
  process.exit(2);
}
if (files.length === 0) {
  console.error(`check-bundle-size: no index-*.js in ${assets}`);
  process.exit(2);
}
const [name, size] = files
  .map((f) => [f, statSync(join(assets, f)).size])
  .sort((a, b) => b[1] - a[1])[0];
const kb = (n) => `${Math.round(n / 1024).toLocaleString()} kB`;
if (size > CAP_BYTES) {
  console.error(`check-bundle-size: ${name} is ${kb(size)}, over the ${kb(CAP_BYTES)} cap.`);
  console.error('  Move the new code behind a dynamic import, or raise CAP_BYTES in');
  console.error('  desktop/scripts/check-bundle-size.mjs in the same change and say why.');
  process.exit(1);
}
console.log(`check-bundle-size: ${name} ${kb(size)} (cap ${kb(CAP_BYTES)})`);
