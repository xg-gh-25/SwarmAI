/**
 * WakeLine — a Swarm Out wake in the chat: the system message that woke this session
 * because runs it dispatched need it (backend core/swarm_out_wake; design "the runs this
 * session dispatched, visible in the dispatching tab" r2 §3 ③).
 *
 * A wake is told by its persisted row's ``metadata.origin === "wake"``, set by the
 * backend for system turns only (session_router.user_row_metadata), never by the text:
 * a message the person typed is a bubble whatever it says, and a wake persisted before
 * the metadata existed keeps showing as it did. The line names the runs (their titles,
 * this Host's own record) and that Swarm takes over: centred, between two thin rules in
 * the agent's colour. The message text the agent read is on hover.
 *
 * Text a run's dispatcher wrote (a title) is plain text, never translated.
 *
 * @exports WakeLine, wakeOf
 */
import { useTranslation } from 'react-i18next';
import type { Message } from '../../../types';
import { tf } from '../tf';

/** the agent's colour (Swarm Out's "agent holds it" pill) */
const AGENT = 'var(--line-judge)';

export interface WakeInfo {
  kind: string;
  dispatches: { id: string; title: string | null; stage: string | null }[];
}

/** The wake a user message is, or null when it is not one (no ``origin: wake``). */
export function wakeOf(message: Message): WakeInfo | null {
  if (message.role !== 'user') return null;
  const md = message.metadata as Record<string, unknown> | undefined;
  if (!md || md.origin !== 'wake') return null;
  const w = (md.wake ?? {}) as { kind?: unknown; dispatches?: unknown };
  const list = Array.isArray(w.dispatches) ? w.dispatches : [];
  return {
    kind: typeof w.kind === 'string' ? w.kind : 'runs',
    dispatches: list.filter((d): d is Record<string, unknown> => !!d && typeof d === 'object')
      .map((d) => ({
        id: String(d.id ?? ''),
        title: typeof d.title === 'string' && d.title ? d.title : null,
        stage: typeof d.stage === 'string' ? d.stage : null,
      })),
  };
}

const textOf = (m: Message) => (m.content ?? [])
  .map((b) => (b as { type?: string; text?: string }).type === 'text' ? (b as { text?: string }).text ?? '' : '').join('');

export function WakeLine({ message, wake }: { message: Message; wake: WakeInfo }) {
  const { t } = useTranslation();
  const titles = wake.dispatches.map((d) => d.title ?? d.id).filter(Boolean);
  const names = titles.length ? titles.map((x) => `「${x}」`).join(tf(t, 'sessionRuns.wakeSep', '、')) : null;
  // every run came back (awaiting): it finished, Swarm reviews, lands and judges it
  const back = wake.dispatches.length > 0 && wake.dispatches.every((d) => d.stage === 'awaiting');
  const text = !names ? tf(t, 'sessionRuns.wakeNone', 'A run this session dispatched needs Swarm · Swarm takes over')
    : back ? tf(t, 'sessionRuns.wakeDone', '{{names}} came back · Swarm takes over: review the diff → land → judge', { names })
      : tf(t, 'sessionRuns.wakeChanged', '{{names}} changed · Swarm takes over', { names });
  return (
    <div className="flex items-center gap-3 py-2 px-4 select-none" role="note" data-testid="wake-line"
      data-kind={wake.kind} title={textOf(message)}>
      <div className="flex-1 h-px" style={{ background: `color-mix(in srgb, ${AGENT} 45%, transparent)` }} />
      <span className="text-xs whitespace-nowrap overflow-hidden text-ellipsis max-w-[80%] inline-flex items-center gap-1.5"
        style={{ color: AGENT }}>
        <span aria-hidden="true">⬡</span>
        <span className="truncate">{text}</span>
      </span>
      <div className="flex-1 h-px" style={{ background: `color-mix(in srgb, ${AGENT} 45%, transparent)` }} />
    </div>
  );
}
