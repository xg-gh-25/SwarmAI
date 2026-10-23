/**
 * draftToRestore — what of a draft goes back to its tab's composer when the send
 * it was taken for did not happen (docs/design/chat-turn-liveness.md P2c).
 *
 * The composer gives up its draft when Send is pressed. If the send then does not
 * happen (attachments could not be read, the tab was closed, reset or moved to
 * another session, Stop), the text and attachments go back, but never over what
 * the user has typed or attached there since.
 *
 * Key exports:
 * - ``draftToRestore`` — the parts to put back (absent = leave that part alone).
 */
import type { UnifiedAttachment } from '../../types';

export function draftToRestore(
  current: { text: string; attachments: readonly UnifiedAttachment[] },
  draft: { text: string; attachments: readonly UnifiedAttachment[] },
): { text?: string; attachments?: UnifiedAttachment[] } {
  const out: { text?: string; attachments?: UnifiedAttachment[] } = {};
  if (!current.text.trim() && draft.text) out.text = draft.text;
  if (current.attachments.length === 0 && draft.attachments.length > 0) out.attachments = [...draft.attachments];
  return out;
}
