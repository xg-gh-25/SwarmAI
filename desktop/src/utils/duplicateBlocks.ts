/**
 * Duplicate-block collapse shared by the stream reconciler (updateMessages) and the
 * MessageStore DB merge.
 *
 * Key export: collapseAdjacentDuplicateBlocks.
 */
import type { ContentBlock, TextContent, ThinkingContent } from '../types';

/** Shortest body that counts as a duplicate; a short repeated line ("Done.") can be real. */
const DUPLICATE_MIN_CHARS = 20;

/**
 * Drop a text/thinking block that repeats the block right before it (same type, same
 * body, >= 20 chars). The model never writes one thought or paragraph twice back to
 * back; two such blocks side by side are a client-side double write, and they also
 * inflate the "more-complete wins" length MessageStore merges by, so the DB's single
 * copy could never replace them. Logs each collapse so the writing path can be found.
 */
export function collapseAdjacentDuplicateBlocks(content: ContentBlock[], where: string): ContentBlock[] {
  const body = (b: ContentBlock): string | null =>
    b.type === 'text' ? ((b as TextContent).text ?? '')
      : b.type === 'thinking' ? ((b as ThinkingContent).thinking ?? '') : null;
  let out: ContentBlock[] | null = null;
  for (let i = 1; i < content.length; i += 1) {
    const cur = content[i];
    const prev = content[i - 1];
    const b = body(cur);
    if (b === null || prev.type !== cur.type || b.trim().length < DUPLICATE_MIN_CHARS || body(prev) !== b) {
      out?.push(cur);
      continue;
    }
    if (!out) out = content.slice(0, i);
    console.warn('[stream-dup] collapsed a repeated block', {
      where, type: cur.type, chars: b.length,
      confirmed: [!!(prev as TextContent)._confirmed, !!(cur as TextContent)._confirmed],
    });
    // Keep the confirmed copy when only the second one is confirmed.
    if ((cur as TextContent)._confirmed && !(prev as TextContent)._confirmed) out[out.length - 1] = cur;
  }
  return out ?? content;
}
