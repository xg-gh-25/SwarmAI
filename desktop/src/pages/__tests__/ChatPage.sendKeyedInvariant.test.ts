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
// the send, its reconnect resend and the busy send (design 2026-10-24-chat-stream-structure.md A1)
const TURN_CONTROLS = resolve(__dirname, '../chat/turnControls.ts');

/**
 * For each `chatService.streamChat(` call, capture the request-object argument
 * (everything up to the first handler arg / `createStreamHandler`) and report
 * whether it references `clientId` — either as a direct key or via a spread of an
 * object that itself carries it (`...streamRequest`, where streamRequest is built
 * with clientId in the same function).
 */
function streamChatRequestsCarryClientId(src: string, topLevel = /^ {2}(?:const|function|let)\s+\w+|^ {2}\w+(?:Ref)?\.current\s*=/): { line: number; ok: boolean }[] {
  const lines = src.split('\n');
  // The function a line is in: a line opening a function at ChatPage's top level
  // (two-space indent: `  const name = useCallback(` / `  const name = (` /
  // `  name.current = (`) starts a new scope. A keyed request name only counts
  // inside the scope that declares it (a keyless namesake elsewhere is caught).
  const scopeOf: number[] = [];
  let scope = 0;
  for (let i = 0; i < lines.length; i++) {
    if (topLevel.test(lines[i])) scope = i;
    scopeOf.push(scope);
  }
  // Names of request-objects built WITH a clientId (so a spread of them is keyed),
  // per scope: `${scope}:${name}`.
  const keyedSpreadSources = new Set<string>();
  for (let i = 0; i < lines.length; i++) {
    // crude: `const <name> = {` (or `const <name>: T = {`, or `const <name> =
    // noteSend(tab, {` — the P2b registration of a send) ... a following line has
    // `clientId` before the closing
    const m = lines[i].match(/const\s+(\w+)(?:\s*:\s*\w+)?\s*=\s*(?:noteSend\([^,]+,\s*)?\{/);
    if (m) {
      let block = '';
      for (let j = i + 1; j < Math.min(i + 20, lines.length); j++) {
        block += lines[j] + '\n';
        if (/^\s*\}\)?;?\s*$/.test(lines[j])) break;
      }
      const spreadOf = /^\s*const\s+\w+(?:\s*:\s*\w+)?\s*=\s*\{\s*\.\.\.(\w+)\s*\};/.exec(lines[i]);
      if (/\bclientId\b/.test(block) && !spreadOf) keyedSpreadSources.add(`${scopeOf[i]}:${m[1]}`);
      else if (spreadOf && keyedSpreadSources.has(`${scopeOf[i]}:${spreadOf[1]}`)) keyedSpreadSources.add(`${scopeOf[i]}:${m[1]}`);
    }
  }

  const results: { line: number; ok: boolean }[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!/(?:chatService|\bd\.chat)\.streamChat\(/.test(lines[i])) continue;
    let block = '';
    for (let j = i + 1; j < Math.min(i + 18, lines.length); j++) {
      block += lines[j] + '\n';
      if (/createStreamHandler|wrappedCreateStreamHandler|d\.stream\.handler|^\s*\(ev\)\s*=>/.test(lines[j])) break;
    }
    const direct = /\bclientId\b/.test(block);
    const here = `${scopeOf[i]}:`;
    const viaSpread = [...keyedSpreadSources].filter((k) => k.startsWith(here)).map((k) => k.slice(here.length))
      .some((name) =>
        new RegExp(`\\.\\.\\.${name}\\b`).test(block)
        // or the keyed request object passed as the argument itself
        || new RegExp(`^\\s*${name}\\s*,`, 'm').test(block),
      );
    results.push({ line: i + 1, ok: direct || viaSpread });
  }
  return results;
}

describe('ChatPage — every streamChat send is keyed (run_7263ff67)', () => {
  it('the guard scopes a keyed request name to its own function', () => {
    const fixture = [
      '  const sendA = useCallback(() => {',
      '    const req = {',
      '      clientId,',
      '    };',
      '    chatService.streamChat(',
      '      req,',
      '      createStreamHandler(a, t),',
      '  });',
      '  const sendB = useCallback(() => {',
      '    const req = {',
      '      message,',
      '    };',
      '    chatService.streamChat(',
      '      req,',
      '      createStreamHandler(a, t),',
      '  });',
    ].join('\n');
    expect(streamChatRequestsCarryClientId(fixture).map((c) => c.ok)).toEqual([true, false]);
  });

  it('no chatService.streamChat request builder omits clientId', () => {
    const src = readFileSync(CHATPAGE, 'utf8');
    const calls = streamChatRequestsCarryClientId(src);
    // sanity: the send sites left in ChatPage (Not-sent retry, QUEUE_TIMEOUT retry)
    expect(calls.length).toBeGreaterThanOrEqual(2);
    // ... and the send, its reconnect resend and the busy send in turnControls
    const moved = streamChatRequestsCarryClientId(readFileSync(TURN_CONTROLS, 'utf8'), /^(?:export\s+)?(?:async\s+)?function\s+\w+/);
    expect(moved.length).toBeGreaterThanOrEqual(3);
    expect(moved.filter((c) => !c.ok).map((c) => `turnControls.ts:${c.line}`)).toEqual([]);
    const keyless = calls.filter((c) => !c.ok).map((c) => c.line);
    expect(
      keyless,
      `streamChat request(s) at ChatPage.tsx line(s) ${keyless.join(', ')} omit clientId — ` +
        `a keyless send persists keyless rows → reconcile-tail duplicate (run_7263ff67). ` +
        `Add clientId (+ a local-\${clientId}-asst placeholder), symmetric with the main send.`,
    ).toEqual([]);
  });
});
