/**
 * Structural invariant (run_7263ff67): EVERY `chatService.streamChat(...)` request
 * builder in ChatPage.tsx must carry a `clientId`.
 *
 * WHY a source-invariant test (not a render test): the reconcile-tail duplicate
 * bug had FOUR entrances; the 4th was two in-tab send paths (queued-drain, error-
 * retry) that built their streamChat request WITHOUT clientId → the whole turn
 * persisted keyless → a later reconcile-tail cut landing mid-group produced a
 * duplicate bubble that no correlation guard could catch (id-miss, no client_id,
 * UUID so H2's numeric-drop is unreachable). The class only closes if EVERY send
 * is keyed at the source. A render test for each path is heavy + fragile (mocks 6
 * deps) and risks a vacuous green; this static check directly guards the invariant
 * the fix establishes — "no keyless streamChat send" — and fails loudly if a
 * future send path forgets the key.
 *
 * Backend + correlation are covered elsewhere (test_assistant_client_id_correlation,
 * MessageStore.reconcileChurn) — those prove a KEYED row correlates; this proves
 * every send PRODUCES a keyed row.
 *
 * NOTE: streamAnswerQuestion / streamCmdPermissionContinue are continuation calls
 * that carry no wire clientId BY DESIGN — the backend reuses the turn's stashed
 * `unit._turn_client_id` (run_9bbf1761). They are intentionally excluded here.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CHATPAGE = resolve(__dirname, '../ChatPage.tsx');

/**
 * For each `chatService.streamChat(` call, capture the request-object argument
 * (everything up to the first handler arg / `createStreamHandler`) and report
 * whether it references `clientId` — either as a direct key or via a spread of an
 * object that itself carries it (`...streamRequest`, where streamRequest is built
 * with clientId in the same function).
 */
function streamChatRequestsCarryClientId(src: string): { line: number; ok: boolean }[] {
  const lines = src.split('\n');
  // Names of request-objects built WITH a clientId (so a spread of them is keyed).
  const keyedSpreadSources = new Set<string>();
  for (let i = 0; i < lines.length; i++) {
    // crude: `const <name> = {` ... a following line has `clientId` before the closing
    const m = lines[i].match(/const\s+(\w+)\s*=\s*\{/);
    if (m) {
      let block = '';
      for (let j = i + 1; j < Math.min(i + 20, lines.length); j++) {
        block += lines[j] + '\n';
        if (/^\s*\};?\s*$/.test(lines[j])) break;
      }
      if (/\bclientId\b/.test(block)) keyedSpreadSources.add(m[1]);
    }
  }

  const results: { line: number; ok: boolean }[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!/chatService\.streamChat\(/.test(lines[i])) continue;
    let block = '';
    for (let j = i + 1; j < Math.min(i + 18, lines.length); j++) {
      block += lines[j] + '\n';
      if (/createStreamHandler|wrappedCreateStreamHandler/.test(lines[j])) break;
    }
    const direct = /\bclientId\b/.test(block);
    const viaSpread = [...keyedSpreadSources].some((name) =>
      new RegExp(`\\.\\.\\.${name}\\b`).test(block),
    );
    results.push({ line: i + 1, ok: direct || viaSpread });
  }
  return results;
}

describe('ChatPage — every streamChat send is keyed (run_7263ff67)', () => {
  it('no chatService.streamChat request builder omits clientId', () => {
    const src = readFileSync(CHATPAGE, 'utf8');
    const calls = streamChatRequestsCarryClientId(src);
    // sanity: we actually found the send sites (main + main-retry + drain + retry-timeout)
    expect(calls.length).toBeGreaterThanOrEqual(4);
    const keyless = calls.filter((c) => !c.ok).map((c) => c.line);
    expect(
      keyless,
      `streamChat request(s) at ChatPage.tsx line(s) ${keyless.join(', ')} omit clientId — ` +
        `a keyless send persists keyless rows → reconcile-tail duplicate (run_7263ff67). ` +
        `Add clientId (+ a local-\${clientId}-asst placeholder), symmetric with the main send.`,
    ).toEqual([]);
  });
});
