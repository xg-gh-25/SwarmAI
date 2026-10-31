/**
 * The webview's Tauri capabilities grant no shell permission.
 *
 * Question answered: can a script running in the webview start a process through the
 * shell plugin? It must not: nothing in the frontend uses the plugin (links open
 * through plugin-opener, the backend is started by Rust with std::process::Command,
 * the terminal uses the App's own pty_* commands), and a grant such as
 * `shell:allow-execute` (python / node, any args) or `shell:allow-spawn` (the
 * python-backend sidecar, whose `py -c <code>` runs any Python) would let one script
 * injection run code outside the agent sandbox, as the App
 * (docs/design/2026-10-31-human-proof-verification.md §8).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

type Permission = string | { identifier: string };

function permissionIds(): string[] {
  const file = resolve(__dirname, '../../src-tauri/capabilities/default.json');
  const cap = JSON.parse(readFileSync(file, 'utf8')) as { permissions: Permission[] };
  return cap.permissions.map((p) => (typeof p === 'string' ? p : p.identifier));
}

describe('Tauri capabilities', () => {
  it('grant the webview no shell permission', () => {
    const ids = permissionIds();
    expect(ids.length).toBeGreaterThan(0);
    expect(ids.filter((id) => id.startsWith('shell:'))).toEqual([]);
  });

  it('still let the webview open links and files through the opener plugin', () => {
    expect(permissionIds()).toEqual(expect.arrayContaining(['opener:default', 'opener:allow-open-path']));
  });
});
