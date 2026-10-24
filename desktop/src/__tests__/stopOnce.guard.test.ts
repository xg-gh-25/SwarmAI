/**
 * One Stop at a time (docs/design/2026-10-24-bg-agent-stop-livereload.md D3).
 *
 * What is tested: 2026-10-24 a slow Stop and the user's repeated clicks sent 6 stop
 * requests (5 identical receipts). ChatPage has no harness that drives handleStop, so
 * this guards the source: in handleStop the tab's `_stopInFlight` test-and-set comes
 * BEFORE the stream abort, the `*Stopped*` marker and the request, and is cleared when
 * the request settles; handleStopBackground and SkillsPage's stop have the same guard.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';

const SRC = resolve(__dirname, '..');
const chat = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8');
const skills = readFileSync(join(SRC, 'pages/SkillsPage.tsx'), 'utf8');
const body = (src: string, start: RegExp, end: RegExp) => {
  const i = src.search(start);
  expect(i, String(start)).toBeGreaterThanOrEqual(0);
  const rest = src.slice(i);
  return rest.slice(0, rest.search(end));
};

describe('one Stop at a time', () => {
  it('handleStop tests the flag before any side effect and clears it when the request settles', () => {
    const fn = body(chat, /const handleStop = \(\) => \{/, /\n {2}\};\n/);
    const guard = fn.search(/if \(stopTab\?\._stopInFlight\) return;/);
    expect(guard).toBeGreaterThan(0);
    for (const effect of [/abortController\.abort\(\)/, /\*Stopped\*/, /chatService\.stopSession\(/, /incrementStreamGen\(\)/]) {
      const all = [...fn.matchAll(new RegExp(effect, 'g'))].map((m) => m.index ?? 0);
      // the no-session branch (before the guard) only cancels the local request
      const afterNoSession = all.filter((i) => i > fn.indexOf('return;\n    }'));
      expect(afterNoSession.every((i) => i > guard), String(effect)).toBe(true);
    }
    expect(fn).toMatch(/\.finally\(\(\) => \{ if \(stopTab\) stopTab\._stopInFlight = false; \}\)/);
    // a synchronous throw before the request goes out still clears it
    expect(fn).toMatch(/\} finally \{\n\s+if \(!requested && stopTab\) stopTab\._stopInFlight = false;/);
    expect(fn.indexOf('let requested = false;\n    try {')).toBeGreaterThan(guard);
  });

  it('the background stop and the Skills page stop are guarded too', () => {
    const bg = body(chat, /handleStopBackgroundRef\.current = \(tabId: string\) => \{/, /\n {2}\};\n/);
    expect(bg).toMatch(/tab\._stopInFlight\) return;/);
    expect(bg).toMatch(/\.finally\(\(\) => \{ tab\._stopInFlight = false; \}\)/);
    const sk = body(skills, /const handleStop = async \(\) => \{/, /\n {2}\};\n/);
    expect(sk).toMatch(/if \(!sessionId \|\| stopInFlightRef\.current\) return;/);
    expect(sk).toMatch(/stopInFlightRef\.current = false;/);
  });
});
