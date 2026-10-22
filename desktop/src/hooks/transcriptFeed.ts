/**
 * The transcript feed applied to a tab (backend core/transcript_feed).
 *
 * A turn's content reaches a tab as the turn's events. Every other change to the
 * session's record — a user message (any tab, any channel), a permission
 * decision, a question's answer, a Stop receipt and the stopped turn's products,
 * a background agent's final status, a compaction, a removed row — arrives as a
 * `transcript_row` / `transcript_delete` envelope on the session subscription,
 * already persisted. `planTranscriptChange` turns one into store operations that
 * make the tab show what a reload of the record shows:
 *
 * - a user row: added unless the tab has it (its id, or its client_id as the
 *   optimistic bubble's id or carried metadata);
 * - an assistant row (`op: insert`, or `update` of state): each block with an identity (a permission prompt by
 *   requestId, a question by toolUseId, a tool result by toolUseId) that the tab
 *   shows gets the row's state (decision / answers / subagent); the rest join
 *   the bubble of the row's turn (its `-asst` key), else the last assistant
 *   message when it is the last message (the reload merges consecutive
 *   assistant rows), else a new assistant message;
 * - a compaction row: the stored divider, through the one compaction mapper;
 * - a removal: the row's message goes.
 *
 * Idempotent: a row applied at a rev is not applied again at that rev or lower
 * (`applied`, per store), and an identity block is only ever patched.
 *
 * Exports: planTranscriptChange, FeedOp, TranscriptChange.
 */
import type { ContentBlock, Message, StreamEvent } from '../types';
import { toMessageCamelCase } from '../services/chat';
import { toDisplayMessage } from '../pages/chat/utils';

export interface TranscriptChange {
  type: 'transcript_row' | 'transcript_delete';
  row?: Record<string, unknown>;
  rowId?: string;
  rev?: number;
  pending?: boolean;
  /** insert: a new row; update: state changed on blocks a tab already shows */
  op?: 'insert' | 'update' | 'upsert';
}

export type FeedOp =
  | { kind: 'append'; msg: Message }
  | { kind: 'update'; id: string; update: (m: Message) => Message }
  | { kind: 'remove'; id: string }
  | { kind: 'compaction'; event: StreamEvent };

/** Blocks with an identity of their own, and the state a later write sets on them. */
const IDENTITY: Record<string, { key: string; state: string[] }> = {
  cmd_permission_request: { key: 'requestId', state: ['decision'] },
  ask_user_question: { key: 'toolUseId', state: ['answers'] },
  tool_result: { key: 'toolUseId', state: ['subagent'] },
};

type Raw = Record<string, unknown>;
const field = (b: unknown, k: string): unknown => (b as Raw | undefined)?.[k];

function identityOf(b: ContentBlock): string | null {
  const spec = IDENTITY[String(field(b, 'type'))];
  const key = spec ? field(b, spec.key) : undefined;
  return spec && typeof key === 'string' && key ? `${String(field(b, 'type'))}:${key}` : null;
}

/** The turn a row belongs to: its client_id without `-asst`. */
function turnKey(row: Raw): string | null {
  const cid = field(row.metadata, 'client_id');
  if (typeof cid !== 'string' || !cid) return null;
  return cid.endsWith('-asst') ? cid.slice(0, -5) : cid;
}

export function planTranscriptChange(
  messages: readonly Message[], change: TranscriptChange, applied: Map<string, number>,
): FeedOp[] {
  if (change.type === 'transcript_delete') {
    const id = change.rowId;
    return id && messages.some((m) => m.id === id) ? [{ kind: 'remove', id }] : [];
  }
  const row = change.row;
  const rowId = typeof row?.id === 'string' ? row.id : null;
  if (!row || !rowId) return [];
  const rev = typeof change.rev === 'number' ? change.rev : 0;
  if ((applied.get(rowId) ?? -1) >= rev) return [];
  applied.set(rowId, rev);
  const msg = toDisplayMessage(toMessageCamelCase(row) as never);

  if (msg.role === 'system' && rowId.startsWith('compact-')) {
    const text = (msg.content ?? []).map((b) => String(field(b, 'text') ?? '')).join('');
    return [{ kind: 'compaction', event: {
      type: 'context_compacted', message: text,
      compaction: field(row.metadata, 'compaction'),
    } as unknown as StreamEvent }];
  }

  if (msg.role === 'user') {
    const cid = field(row.metadata, 'client_id');
    const have = messages.some((m) => m.id === rowId
      || (typeof cid === 'string' && cid && (m.id === cid || m.metadata?.client_id === cid)));
    return have ? [] : [{ kind: 'append', msg }];
  }

  if (msg.role !== 'assistant') return [];
  const ops: FeedOp[] = [];
  const rest: ContentBlock[] = [];
  for (const blk of msg.content ?? []) {
    const ident = identityOf(blk);
    const holder = ident ? messages.find((m) => (m.content ?? []).some((b) => identityOf(b) === ident)) : undefined;
    if (!ident || !holder) { rest.push(blk); continue; }
    const spec = IDENTITY[String(field(blk, 'type'))];
    const patch: Raw = {};
    for (const k of spec.state) if (field(blk, k) !== undefined) patch[k] = field(blk, k);
    if (Object.keys(patch).length === 0) continue;
    ops.push({ kind: 'update', id: holder.id, update: (m) => ({
      ...m, content: m.content.map((b) => (identityOf(b) === ident ? { ...b, ...patch } as ContentBlock : b)),
    }) });
  }
  // an update only changes state on blocks the tab shows; a block it does not
  // show belongs to content the tab never had (a reconcile brings it)
  if (change.op === 'update' || !rest.length || messages.some((m) => m.id === rowId)) return ops;
  const key = turnKey(row);
  const own = key ? messages.find((m) => m.id === `${key}-asst` || m.metadata?.client_id === `${key}-asst`) : undefined;
  const last = messages[messages.length - 1];
  const target = own ?? (last && last.role === 'assistant' ? last : undefined);
  if (target) {
    ops.push({ kind: 'update', id: target.id, update: (m) => ({ ...m, content: [...m.content, ...rest] }) });
  } else {
    ops.push({ kind: 'append', msg: { ...msg, content: rest } });
  }
  return ops;
}
