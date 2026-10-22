/**
 * The stop receipt joined to the message a user Stop ended (backend core/turn_stop).
 *
 * POST /chat/stop answers with the stopped turn's closing blocks (turn_changes /
 * turn_deliverables / turn_stop), already persisted. They arrive after the stream
 * was aborted, so ChatPage adds them to the stopped message by id.
 *
 * - `withStopReceipt(msg, blocks)`: the message with the blocks appended, or the
 *   same message when it already carries a turn_stop (a reconcile from the DB got
 *   there first) or there is nothing to add.
 */
import type { ContentBlock, Message } from '../../types';

export function withStopReceipt(msg: Message, blocks: ContentBlock[]): Message {
  if (!blocks.length || msg.content.some((b) => b.type === 'turn_stop')) return msg;
  return { ...msg, content: [...msg.content, ...blocks] };
}
