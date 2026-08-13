/**
 * Regression lock (run_021e4a60): the SSE→CustomEvent bridge that builds the
 * `swarm:file-changed` detail (useChatStreamingLifecycle.ts) constructs `detail`
 * as a FIELD WHITELIST. A prior run (run_c014a4f3) threaded `sessionId` through
 * every OTHER hop (event → ReferencedFile → outputRowOpenDetail → useCanvasHost →
 * FileViewer GET session_id) but this bridge's whitelist OMITTED sessionId, so it
 * was silently dropped here → the render fetch never sent session_id → external
 * Canvas-surfaced files rendered "Invalid request" (400).
 *
 * This is the whitelist-drop bug class (GUI53/PIT44: a frontend event whitelist
 * silently drops any backend field not explicitly listed). A behavioral test would
 * need the full 280-caller streaming hook; a SOURCE assertion binds directly to the
 * whitelist and goes RED the instant `sessionId` is removed from it — the exact
 * regression we're locking. It reads `sessionId: (e.sessionId ...` inside the same
 * detail block that already carries tabId, so it cannot be satisfied by a comment.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(__dirname, '../useChatStreamingLifecycle.ts');

describe("swarm:file-changed bridge — sessionId is carried through the detail whitelist", () => {
  const src = readFileSync(SRC, 'utf-8');

  it('assigns sessionId from the event inside the file_changed detail', () => {
    // The detail whitelist must read sessionId off the backend event (e.sessionId),
    // exactly like it reads e.kind / e.baseRef. A bare mention in a comment is not
    // enough — require the assignment form.
    expect(src).toMatch(/sessionId:\s*\(e\.sessionId as string\)\s*\?\?\s*undefined/);
  });

  it('the sessionId assignment lives in the SAME detail block as tabId (the file_changed dispatch)', () => {
    // Anchor: the file_changed dispatch block carries tabId: _stampTab; sessionId must
    // be in the same CustomEvent('swarm:file-changed') detail (not some unrelated spot).
    const dispatchIdx = src.indexOf("new CustomEvent('swarm:file-changed'");
    expect(dispatchIdx).toBeGreaterThan(-1);
    const block = src.slice(dispatchIdx, dispatchIdx + 3000);
    expect(block).toContain('tabId: _stampTab');
    expect(block).toMatch(/sessionId:\s*\(e\.sessionId/);
  });
});
