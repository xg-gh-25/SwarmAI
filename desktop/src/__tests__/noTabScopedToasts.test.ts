/**
 * Guard: chat-tab / chat-session events never go to the app-wide toast stack.
 *
 * The top-right toast stack is for system-level events only. Anything that
 * belongs to one tab or session goes to that tab's notices
 * (stores/tabNoticeStore). This test greps the tab-scoped sources for
 * `addToast(` so a regression fails CI instead of relying on a comment.
 *
 * ChatPage.tsx still owns a few system-level toasts (tab limit / memory
 * pressure — the action of opening a tab failed, so no tab owns it yet); those
 * are listed by message so a NEW addToast there must be justified here.
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
  ...walk(join(SRC, 'pages/chat')),
];

describe('tab-scoped code does not raise app-wide toasts', () => {
  it.each(TAB_SCOPED.map((p) => [p.slice(SRC.length + 1), p]))('%s has no addToast(', (_name, path) => {
    expect(readFileSync(path, 'utf8')).not.toMatch(/\baddToast\s*\(/);
  });

  it('ChatPage.tsx only raises the listed system-level toasts', () => {
    const src = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8');
    const calls = src.match(/\baddToast\s*\(\s*\{[^}]*\}/g) ?? [];
    const allowed = [
      /Memory usage is high/,
      /Could not open a new tab/,
      /addToast\(\{\s*severity: 'warning', message, autoDismiss: true \}/, // tab-limit message built above
    ];
    const unexpected = calls.filter((c) => !allowed.some((re) => re.test(c)));
    expect(unexpected).toEqual([]);
  });
});
