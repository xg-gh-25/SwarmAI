/**
 * ChatPage — chat-area column layout containment (run_26172836).
 *
 * ROOT FIX (defense-in-depth half) for the recurring "Canvas 开着时 chat input
 * 输入卡死" lag: the chat-area column and the Canvas (FileViewerPanel) are SIBLING
 * flex children of one shared row. On WebKit without `field-sizing` support, the
 * chat textarea's JS autogrow still writes style.height + reads scrollHeight, forcing
 * a synchronous flush of that shared row that re-lays-out the large Canvas surface.
 * `contain:layout` on the chat-area column (the reflow SOURCE) isolates its internal
 * reflow so it can no longer propagate to the row / Canvas sibling.
 *
 * WHY a source assertion (not a render): ChatPage mounts ~a dozen providers + heavy
 * hooks and is never full-rendered in the suite (verified: no `render(<ChatPage/>)`
 * anywhere). A full-mount test for a single static className would be brittle theater.
 * This asserts the durable structural fact directly against source — mutation-sensitive:
 * remove `[contain:layout]` from the Main-Chat-Area column → this test goes RED.
 *
 * The PRIMARY half of the fix (eliminate the reflow entirely via field-sizing) is
 * covered behaviorally in ChatInput.reflow-skip.test.tsx (Layer 3).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CHATPAGE = resolve(__dirname, '../ChatPage.tsx');

describe('ChatPage chat-area column contains its layout (Canvas-lag coupling severed)', () => {
  const src = readFileSync(CHATPAGE, 'utf8');

  it('the Main Chat Area column div carries [contain:layout]', () => {
    // The column is the div immediately following the "Main Chat Area" comment,
    // with the flex-1/flex-col/min-w-0/overflow-hidden signature.
    const idx = src.indexOf('{/* Main Chat Area');
    expect(idx).toBeGreaterThan(-1);
    // Look at the div opened right after that comment (within the next ~1500 chars,
    // which spans the multi-line explanatory comment block before the div).
    const window = src.slice(idx, idx + 1500);
    const colMatch = window.match(/<div className="([^"]*flex-1 flex flex-col min-w-0 overflow-hidden[^"]*)"/);
    expect(colMatch, 'chat-area column div not found after "Main Chat Area"').toBeTruthy();
    expect(colMatch![1]).toContain('[contain:layout]');
  });
});
