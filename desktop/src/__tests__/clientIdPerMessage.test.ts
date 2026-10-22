/**
 * One client_id per message (services/clientId; backend core/send_requests).
 *
 * Invariants:
 * - only services/clientId mints a `local-…` client_id (a static scan of src/);
 * - a message re-queued from a handed-back send keeps that send's client_id;
 * - every client_id ChatPage sends is the message's own: a new message mints one,
 *   a queued drain uses the queued message's, a QUEUE_TIMEOUT re-send uses the
 *   handed-back one (scan of the assignments in pages/ChatPage.tsx).
 *
 * Methodology: static scans name file:line; the re-queue is the real helper.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { queuedMessageFromRetryPayload } from '../hooks/streaming-guards';

const SRC = join(__dirname, '..');

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== '__tests__' && name !== 'node_modules') out.push(...files(p));
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

describe('one client_id per message', () => {
  it('only services/clientId mints one', () => {
    const minted = files(SRC).flatMap((f) => {
      const s = readFileSync(f, 'utf8');
      return [...s.matchAll(/`local-\$\{/g)].map((m) => `${relative(SRC, f)}:${s.slice(0, m.index!).split('\n').length}`);
    });
    expect(minted).toEqual(['services/clientId.ts:13']);
  });

  it('ChatPage sends only the message\'s own client_id', () => {
    const s = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8');
    const assigned = [...s.matchAll(/const clientId = ([^;]+);/g)].map((m) => m[1].trim());
    expect(assigned.sort()).toEqual([
      'newClientId()',                          // a new message
      'queued.clientId ?? newClientId()',       // a queued message's drain
      'retry.clientId ?? newClientId()',        // a handed-back send, re-sent
    ].sort());
  });

  it('a re-queued handed-back send keeps its client_id', () => {
    const q = queuedMessageFromRetryPayload(
      { sessionId: 'S', agentId: 'a', userMessage: 'again', content: null, clientId: 'local-1-x' }, 'queued-1');
    expect(q?.clientId).toBe('local-1-x');
  });
});
