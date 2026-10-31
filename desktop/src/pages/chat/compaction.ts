/**
 * Context-compaction divider model — ONE mapper for both delivery routes.
 *
 * A compaction reaches the chat two ways: live, as the `context_compacting` /
 * `context_compacted` SSE events, and on history load, as a synthetic
 * `compact-<id>` system message from the backend. Both carry the same
 * snake_case record (backend core/compact_summary.wire) in
 * `metadata.compaction`; the live "compacting" phase lives in
 * `metadata.compactPhase` until the record replaces it IN PLACE (one divider
 * per compaction, never a second row). The live and the history message share
 * the id `compact-<record id>`, so the store merge keeps one copy.
 *
 * Key exports:
 * - `CompactionInfo` / `toCompactionInfo(msg)` — the divider's state
 * - `applyCompactionEvent(store, event)` — the stream handler's store write
 * - `formatTokensK(n)` — "750K"
 */
import type { Message, StreamEvent } from '../../types';

export type CompactionPhase = 'running' | 'done' | 'failed';

export interface CompactionInfo {
  phase: CompactionPhase;
  trigger: 'auto' | 'manual';
  preTokens?: number;
  postTokens?: number;
  durationMs?: number;
  /** Cleaned summary markdown; '' when the CLI produced none. */
  summary: string;
}

/** Live (pre-record) divider ids: replaced by `compact-<record id>` on completion. */
export const COMPACT_LIVE_PREFIX = 'compact-live-';

const num = (v: unknown): number | undefined =>
  typeof v === 'number' && Number.isFinite(v) ? v : undefined;

/** The stored record (snake_case wire) → info, or null if it is not one. */
function fromWire(raw: unknown): CompactionInfo | null {
  if (!raw || typeof raw !== 'object') return null;
  const w = raw as Record<string, unknown>;
  if (typeof w.id !== 'string' || !w.id) return null;
  return {
    phase: 'done',
    trigger: w.trigger === 'manual' ? 'manual' : 'auto',
    preTokens: num(w.pre_tokens),
    postTokens: num(w.post_tokens),
    durationMs: num(w.duration_ms),
    summary: typeof w.summary === 'string' ? w.summary : '',
  };
}

/** State of a `compact-` system message; null for a legacy text-only notice. */
export function toCompactionInfo(msg: Message): CompactionInfo | null {
  const md = msg.metadata ?? {};
  const done = fromWire(md.compaction);
  if (done) return done;
  const phase = md.compactPhase;
  if (phase === 'running' || phase === 'failed') return { phase, trigger: 'auto', summary: '' };
  return null;
}

export function formatTokensK(n: number): string {
  return `${Math.round(n / 1000)}K`;
}

/** The part of MessageStore the compaction write needs (keeps this testable). */
export interface CompactionStore {
  readonly messages: Message[];
  append(msg: Message): void;
  insertBefore(anchorId: string, msg: Message): void;
  updateById(id: string, updater: (msg: Message) => Message): void;
}

const rand = () => Math.random().toString(36).slice(2, 7);
const textOf = (m: Message | undefined) =>
  m?.content?.[0]?.type === 'text' ? (m.content[0] as { text?: string }).text : undefined;
const pad = (n: number, w = 2) => String(n).padStart(w, '0');

/** Now in the DB's timestamp format (naive LOCAL ISO, backend
 *  `datetime.now().isoformat()`). A divider with no DB row is positioned by a
 *  string compare against DB timestamps on reconcile; a UTC `…Z` stamp would
 *  sort hours off (to index 0 east of UTC, where it then became the
 *  "load older" cursor). */
export function localTimestamp(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T`
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

function systemMessage(id: string, text: string, metadata: Message['metadata']): Message {
  return { id, role: 'system', content: [{ type: 'text', text }], timestamp: localTimestamp(), metadata };
}

/** The ``turnOf`` mark of a live compaction, kept when the message is replaced. */
const turnOfOf = (m: Message) => (typeof m.metadata?.turnOf === 'string' ? { turnOf: m.metadata.turnOf } : {});

/** Index of the current turn's start (the last user message), or -1. */
function turnStart(msgs: Message[]): number {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i].role === 'user') return i;
  return -1;
}

/**
 * Write one compaction event into the ORIGINATING tab's store.
 * - started: one live divider (a repeated start is ignored). Before a still
 *   EMPTY streaming placeholder (a turn-start compaction) it goes in front of
 *   it — the DB order is user → compaction → answer.
 * - failed: the live divider turns into the failure notice (or one is added).
 * - compacted: the live divider is replaced IN PLACE by the stored record
 *   (id `compact-<record id>`); an id already present, or an adjacent identical
 *   legacy notice, is not added again.
 * Only the CURRENT turn's live divider (after the last user message) is ever
 * updated: one abandoned by an interrupted turn stays where it was.
 */
export function applyCompactionEvent(store: CompactionStore, event: StreamEvent): void {
  const text = event.message;
  if (!text) return;
  const msgs = store.messages;
  const start = turnStart(msgs);
  let live: Message | undefined;
  for (let i = msgs.length - 1; i > start; i--) {
    const m = msgs[i];
    if (m.id.startsWith(COMPACT_LIVE_PREFIX) && m.metadata?.compactPhase === 'running') { live = m; break; }
  }
  const add = (msg: Message) => {
    const last = msgs[msgs.length - 1];
    const emptyPlaceholder = last && msgs.length - 1 > start && last.role === 'assistant' && last.content.length === 0;
    // in front of the turn's empty placeholder, the compaction is that turn's: say so
    // by id (an injected CLI turn has no user message before it, so the order alone
    // cannot tell it from the previous turn's; attachCompactions reads turnOf)
    if (emptyPlaceholder) store.insertBefore(last.id, { ...msg, metadata: { ...msg.metadata, turnOf: last.id } });
    else store.append(msg);
  };
  if (event.type === 'context_compacting') {
    if (event.phase === 'failed') {
      const failed = (m: Message) => ({ ...m, content: [{ type: 'text', text }],
        metadata: { compactPhase: 'failed', ...turnOfOf(m) } } as Message);
      if (live) store.updateById(live.id, failed);
      else add(systemMessage(`${COMPACT_LIVE_PREFIX}${Date.now()}-${rand()}`, text, { compactPhase: 'failed' }));
      return;
    }
    if (!live) add(systemMessage(`${COMPACT_LIVE_PREFIX}${Date.now()}-${rand()}`, text, { compactPhase: 'running' }));
    return;
  }
  const record = event.compaction;
  const recordId = record && typeof record.id === 'string' && record.id ? record.id : null;
  const id = recordId ? `compact-${recordId}` : `compact-${Date.now()}-${rand()}`;
  if (recordId && msgs.some((m) => m.id === id)) return;
  const done = systemMessage(id, text, recordId ? { compaction: record } : undefined);
  if (live) {
    store.updateById(live.id, (m) => ({ ...done, metadata: { ...done.metadata, ...turnOfOf(m) } }));
    return;
  }
  // A record-less (legacy / fallback) notice repeated in the same turn is one
  // compaction, not two — it may sit before the placeholder, not at the end.
  // A stored record is its own compaction: two in one turn are two dividers,
  // even with the same wording.
  if (!recordId) {
    for (let i = msgs.length - 1; i > start; i--) {
      const m = msgs[i];
      if (m.role === 'system' && m.id.startsWith('compact-') && textOf(m) === text) return;
    }
  }
  add(done);
}

/** Compactions placed on the turn they happened in: `byAssistant` maps the turn's last
 *  assistant message to its compaction messages (in order); `attached` is every one so
 *  placed. A compaction is a turn event, not conversation: shown inline it sat below
 *  the answer that kept streaming above it.
 *
 *  A turn starts at a user message that was sent (not a queued one, which is sent
 *  later, nor a steer the CLI read into the running turn) and at a turn the CLI started
 *  itself (`turn_source: injected`, no user message before it). A compaction marked
 *  `turnOf` (inserted in front of that turn's placeholder) belongs to that turn. A
 *  compaction is attached only to an answer TabView paints (`painted`); otherwise, as
 *  in a turn with no answer yet, it stays in the list and TabView renders it inline. */
export function attachCompactions(msgs: readonly Message[],
                                  painted: (m: Message) => boolean = () => true): {
  byAssistant: Map<string, Message[]>; attached: Set<string>;
} {
  const byAssistant = new Map<string, Message[]>();
  const attached = new Set<string>();
  let pending: Message[] = [];
  let last: Message | null = null;
  const flush = () => {
    if (last && pending.length && painted(last)) {
      byAssistant.set(last.id, [...(byAssistant.get(last.id) ?? []), ...pending]);
      for (const m of pending) attached.add(m.id);
    }
    pending = [];
    last = null;
  };
  for (const m of msgs) {
    if (m.role === 'user') {
      if (!m.isQueued && !m.metadata?.steered) flush();
    } else if (m.role === 'system' && m.id.startsWith('compact-')) {
      pending.push(m);
    } else if (m.role === 'assistant') {
      if (m.metadata?.turn_source === 'injected') {
        // the compactions marked as this turn's move with it; the rest end the turn before
        const mine = pending.filter((c) => c.metadata?.turnOf === m.id);
        pending = pending.filter((c) => c.metadata?.turnOf !== m.id);
        flush();
        pending = mine;
      }
      last = m;
    }
  }
  flush();
  return { byAssistant, attached };
}

/** ``next`` with every array that holds the same messages as in ``prev`` replaced by
 *  ``prev``'s: a memoized bubble whose turn's compactions did not change keeps its
 *  props while the streaming list is rebuilt on every token. */
export function reuseCompactions(prev: Map<string, Message[]> | undefined,
                                 next: Map<string, Message[]>): Map<string, Message[]> {
  if (!prev) return next;
  for (const [id, list] of next) {
    const old = prev.get(id);
    if (old && old.length === list.length && old.every((m, i) => m === list[i])) next.set(id, old);
  }
  return next;
}
