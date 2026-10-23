/**
 * terminalAttachment — terminal text as a chat-input attachment
 * (docs/design/terminal-locations.md "Add to chat").
 *
 * A terminal selection (or the last screen of output) becomes ONE attachment chip
 * per chat tab, stored with the tab's other attachments (useUnifiedAttachments,
 * tab-isolated), so it is visible, removable, and travels with its message through
 * the queue, the drain and a reconnect resend. At send it is NOT a content block:
 * it rides the existing `terminalContext` channel, which the backend renders as a
 * fenced UNTRUSTED-DATA block on the user turn (session_router
 * `_build_terminal_prefix_block`).
 *
 * The chip keeps its PIECES, each with the cwd of the shell it came from; the text
 * is rendered from them, a piece from a different folder than the one before it
 * headed `# cwd: …`, and the chip's cwd is its first piece's. Over the limit, whole
 * oldest pieces are dropped first (so a heading never outlives or loses its text),
 * then the oldest lines of what remains.
 *
 * Exports: TERMINAL_CONTEXT_MAX, isTerminalAttachment, addTerminalText,
 * mergeTerminalAttachments, terminalContextOf, terminalLabel.
 */
import type { UnifiedAttachment } from '../types';

/** backend/schemas/message.py TerminalContext.buffer_tail max_length — a longer
 *  value is rejected with 422 and the whole message fails, so it is cut here.
 *  terminalAttachment.test.ts checks the two numbers agree. */
export const TERMINAL_CONTEXT_MAX = 16000;

type Piece = { cwd: string; text: string };

export function isTerminalAttachment(a: UnifiedAttachment): boolean {
  return a.type === 'terminal';
}

function baseName(path: string): string {
  const parts = path.replace(/\/+$/, '').split('/');
  return parts[parts.length - 1] || path;
}

/** "Terminal · swarmai" (SwarmWS when the shell was opened without a cwd). */
export function terminalLabel(cwd: string): string {
  return `Terminal · ${cwd ? baseName(cwd) : 'SwarmWS'}`;
}

function render(pieces: Piece[]): string {
  return pieces
    .map((p, i) => (i > 0 && p.cwd !== pieces[i - 1].cwd ? `# cwd: ${p.cwd || 'SwarmWS'}\n` : '') + p.text)
    .join('\n\n');
}

/** Keep the LAST *max* characters of *text*, starting at a line boundary when near. */
function keepTail(text: string, max: number): string {
  if (text.length <= max) return text;
  let tail = text.slice(text.length - max);
  const nl = tail.indexOf('\n');
  if (nl >= 0 && nl < 200) tail = tail.slice(nl + 1);
  return tail;
}

/** *pieces* within the limit: drop whole oldest pieces, then cut the oldest lines
 *  of the one left. */
function fit(pieces: Piece[]): { pieces: Piece[]; cut: boolean } {
  const kept = [...pieces];
  let cut = false;
  while (kept.length > 1 && render(kept).length > TERMINAL_CONTEXT_MAX) {
    kept.shift();
    cut = true;
  }
  if (kept.length === 1 && kept[0].text.length > TERMINAL_CONTEXT_MAX) {
    kept[0] = { cwd: kept[0].cwd, text: keepTail(kept[0].text, TERMINAL_CONTEXT_MAX) };
    cut = true;
  }
  return { pieces: kept, cut };
}

function piecesOf(a: UnifiedAttachment): Piece[] {
  return a.terminal?.pieces ?? (a.textContent ? [{ cwd: a.terminal?.cwd ?? '', text: a.textContent }] : []);
}

function build(id: string, all: Piece[], wasCut: boolean): UnifiedAttachment {
  const { pieces, cut } = fit(all);
  const text = render(pieces);
  const cwd = pieces[0]?.cwd ?? '';
  return {
    id,
    name: terminalLabel(cwd),
    type: 'terminal',
    deliveryStrategy: 'terminal_context',
    size: new TextEncoder().encode(text).length,
    mediaType: 'text/plain',
    textContent: text,
    preview: text.split('\n').slice(-8).join('\n'),
    terminal: { cwd, lines: text ? text.split('\n').length : 0, truncated: wasCut || cut, pieces },
    isLoading: false,
  };
}

/** The attachments with *text* added: appended to the tab's terminal chip when it
 *  has one, else a new chip. */
export function addTerminalText(prev: UnifiedAttachment[], text: string, cwd: string,
                                newId: () => string): UnifiedAttachment[] {
  const piece = text.replace(/\s+$/, '');
  if (!piece) return prev;
  const at = prev.findIndex(isTerminalAttachment);
  if (at < 0) return [...prev, build(newId(), [{ cwd, text: piece }], false)];
  const old = prev[at];
  const next = [...prev];
  next[at] = build(old.id, [...piecesOf(old), { cwd, text: piece }], !!old.terminal?.truncated);
  return next;
}

/** *existing* followed by *incoming*, at most *max* items, with every terminal chip
 *  folded into ONE (the first one's place). The terminal chip always survives the
 *  limit — a file past it is dropped instead — so merging a queued message with a
 *  new send never loses terminal output. */
export function mergeTerminalAttachments(existing: UnifiedAttachment[],
                                         incoming: UnifiedAttachment[],
                                         max: number): UnifiedAttachment[] {
  const all = [...existing, ...incoming];
  const chips = all.filter(isTerminalAttachment);
  const merged = chips.length > 1
    ? build(chips[0].id, chips.flatMap(piecesOf), chips.some((c) => !!c.terminal?.truncated))
    : chips[0];
  const files = all.filter((a) => !isTerminalAttachment(a));
  if (!merged) return files.slice(0, max);
  const keptFiles = files.slice(0, max - 1);
  const filesBefore = all.slice(0, all.indexOf(chips[0])).filter((a) => !isTerminalAttachment(a)).length;
  const at = Math.min(filesBefore, keptFiles.length);
  return [...keptFiles.slice(0, at), merged, ...keptFiles.slice(at)];
}

/** The `terminalContext` of a message: its terminal chip's text (every chip folded,
 *  within the limit); undefined when it has none. */
export function terminalContextOf(attachments: UnifiedAttachment[] | undefined):
    { bufferTail: string; cwd: string } | undefined {
  const chips = (attachments ?? []).filter((a) => isTerminalAttachment(a) && !a.error && a.textContent);
  if (chips.length === 0) return undefined;
  const one = chips.length === 1 ? chips[0] : build(chips[0].id, chips.flatMap(piecesOf), false);
  return { bufferTail: one.textContent ?? '', cwd: one.terminal?.cwd ?? '' };
}
