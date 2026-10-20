/**
 * Guard: chat-tab / chat-session events never go to the app-wide toast stack.
 *
 * The top-right toast stack is for system-level events only. Anything that
 * belongs to one tab or session goes to that tab's notices
 * (stores/tabNoticeStore). This test greps the tab-scoped sources for
 * `addToast(` so a regression fails CI instead of relying on a comment.
 *
 * The chat page's few system-level toasts (opening / landing in a tab failed,
 * so no tab owns the message yet) go through hooks/useSystemToasts.ts by name;
 * ChatPage.tsx itself never calls addToast( — no allowlist to maintain.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const SRC = resolve(__dirname, '..');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === '__tests__' ? [] : walk(p);
    return /\.(ts|tsx)$/.test(name) ? [p] : [];
  });
}

const TAB_SCOPED = [
  join(SRC, 'hooks/useChatStreamingLifecycle.ts'),
  join(SRC, 'pages/ChatPage.tsx'),
  ...walk(join(SRC, 'pages/chat')),
];

describe('tab-scoped code does not raise app-wide toasts', () => {
  it.each(TAB_SCOPED.map((p) => [p.slice(SRC.length + 1), p]))('%s has no addToast(', (_name, path) => {
    expect(readFileSync(path, 'utf8')).not.toMatch(/\baddToast\s*\(/);
  });
});
