/**
 * Source invariant for terminal output in ChatPage (docs/design/terminal-locations.md
 * "Add to chat"). Terminal output is a per-tab attachment (useUnifiedAttachments),
 * and every send site derives `terminalContext` from THE MESSAGE'S OWN attachments:
 *   - no page-level holder of terminal output exists (the old one-shot ref leaked a
 *     selection into another tab's send / a background drain, and was cleared
 *     before the reconnect resend read it);
 *   - each streamChat request's terminalContext is a const computed by
 *     terminalContextOf(<that message's attachments>), so the initial send and its
 *     resend carry the same value.
 * Methodology: source scan (rendering ChatPage's whole send path needs the full
 * provider stack); each assertion names the regression it blocks.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, '../ChatPage.tsx'), 'utf-8');

describe('ChatPage terminal output rides its own message', () => {
  it('no page-level terminal-output holder (the leaking one-shot ref)', () => {
    expect(src).not.toMatch(/terminalContextRef/);
    expect(src).not.toMatch(/useRef<\{\s*bufferTail/);
  });

  it('every terminalContext in a request is that message\'s own', () => {
    const uses = [...src.matchAll(/terminalContext:\s*(\w+)/g)].map((m) => m[1]);
    expect(uses.length).toBeGreaterThanOrEqual(4);   // send, its resend, the queue drain, the hand-back retry
    for (const v of uses) {
      if (v === 'retryTerminal') {
        // the QUEUE_TIMEOUT retry re-sends the message the server handed back
        expect(src).toMatch(/const retryTerminal = retry\.terminalContext\?\.bufferTail/);
        continue;
      }
      const decl = new RegExp(`const ${v} = terminalContextOf\\((\\w+(?:\\.\\w+)?)\\)`).exec(src);
      expect(decl, `${v} must be const ${v} = terminalContextOf(...)`).not.toBeNull();
      expect(['currentAttachments', 'queued.attachments']).toContain(decl![1]);
    }
  });

  it('the queue drain uses the QUEUED message\'s attachments, the send its own', () => {
    expect(src).toMatch(/const drainTerminal = terminalContextOf\(queued\.attachments\)/);
    expect(src).toMatch(/const sendTerminal = terminalContextOf\(currentAttachments\)/);
  });

  it('terminal output is added through the per-tab attachment hook', () => {
    expect(src).toMatch(/addTerminalText\(d\.bufferTail/);
  });
});
