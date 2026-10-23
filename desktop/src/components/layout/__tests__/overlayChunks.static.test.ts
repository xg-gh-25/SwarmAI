/**
 * Static guard for overlayChunks (docs/design/left-nav-overlay-finish.md §2).
 *
 * An overlay's content is only out of the first bundle if NO module outside the
 * overlay chunks imports its file statically: one value import from, say,
 * ChatPage pulls the whole overlay back into the main chunk (ToDoOverlay was
 * held there by `parseWorkPacket`). This test reads every non-test source file
 * under src/, resolves each static value import, and fails when a module that
 * is not itself an overlay chunk imports one. `import type` is erased at build
 * and is allowed.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync, existsSync } from 'fs';
import { join, dirname, resolve, relative } from 'path';
import { fileURLToPath } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
const layoutDir = resolve(here, '..');
const srcDir = resolve(layoutDir, '../..');

function chunkFiles(): Set<string> {
  const text = readFileSync(join(layoutDir, 'overlayChunks.ts'), 'utf-8');
  const out = new Set<string>();
  for (const m of text.matchAll(/import\('([^']+)'\)/g)) {
    const f = resolveModule(layoutDir, m[1]);
    if (f) out.add(f);
  }
  return out;
}

function resolveModule(fromDir: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null;
  const base = resolve(fromDir, spec);
  for (const c of [base, `${base}.tsx`, `${base}.ts`, join(base, 'index.tsx'), join(base, 'index.ts')]) {
    if (existsSync(c) && statSync(c).isFile()) return c;
  }
  return null;
}

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue;
      sourceFiles(p, acc);
    } else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) {
      acc.push(p);
    }
  }
  return acc;
}

/** Static value imports/re-exports of a file (type-only ones are skipped). */
function valueImports(text: string): string[] {
  const out: string[] = [];
  const re = /^\s*(import|export)\s+(type\s+)?([^'";]*?)\s*from\s*'([^']+)'/gm;
  for (const m of text.matchAll(re)) {
    if (m[2]) continue; // import type / export type
    out.push(m[4]);
  }
  for (const m of text.matchAll(/^\s*import\s+'([^']+)'/gm)) out.push(m[1]);
  return out;
}

describe('overlay chunks stay out of the first bundle', () => {
  it('no module outside the overlay chunks statically imports an overlay file', () => {
    const chunks = chunkFiles();
    expect(chunks.size).toBeGreaterThan(10);
    const offenders: string[] = [];
    for (const file of sourceFiles(srcDir)) {
      if (chunks.has(file)) continue;
      const text = readFileSync(file, 'utf-8');
      for (const spec of valueImports(text)) {
        const target = resolveModule(dirname(file), spec);
        if (target && chunks.has(target)) {
          offenders.push(`${relative(srcDir, file)} -> ${relative(srcDir, target)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
