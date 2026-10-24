/**
 * StreamingBee — the "this turn is still running" indicator at the end of the
 * streaming assistant message (replaces the old pulsing caret).
 *
 * The bee flies for as long as the turn works (`--still` under prefers-reduced-
 * motion: breathing only). It never lands: a quiet turn is still a working turn.
 *
 * After 45s with no new content a muted line beside it states what the turn is
 * waiting on and for how long (useStreamingWait): the running call
 * ("Bash · make test · 1m 12s"), background agents, the model thinking, or the
 * model's reply. Facts only, never a cause; real problems are reported elsewhere
 * (still_working stall notice, reconnect banner, health monitor).
 *
 * The bee is an inline SVG (not the 🐝 emoji, whose facing direction and art
 * differ per platform) and is aria-hidden; only the hint is announced (an always-
 * mounted role=status, polite region that the text enters). All motion is CSS
 * keyframes on transform/opacity in index.css (`.streaming-bee*`).
 *
 * @exports StreamingBee, StreamingBeeProps, BeeGlyph
 */
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { tf } from '../tf';
import type { ContentBlock } from '../../../types';
import { usePrefersReducedMotion } from '../../../hooks/usePrefersReducedMotion';
import { formatWait, useStreamingWait } from '../../../hooks/useStreamingWait';
import type { StreamingWait } from '../../../hooks/useStreamingWait';

export interface StreamingBeeProps {
  /** The streaming message's content: its identity changing is "new content". */
  content: ContentBlock[];
  sessionId?: string;
}

/** The hint; `time` false drops the clock (the screen-reader copy, announced once). */
function waitText(w: StreamingWait, t: ReturnType<typeof useTranslation>['t'], zh: boolean, withTime = true): string {
  const time = withTime ? formatWait(Date.now() - w.since, zh) : '';
  const out = (() => { switch (w.kind) {
    case 'tool': return [w.tool, w.object, time].filter(Boolean).join(' · ');
    case 'agents': return w.count === 1
      ? tf(t, 'chat.message.waitAgent', 'Waiting for 1 background agent · {{time}}', { time })
      : tf(t, 'chat.message.waitAgents', 'Waiting for {{count}} background agents · {{time}}', { count: w.count, time });
    case 'thinking': return tf(t, 'chat.message.waitThinking', 'Thinking · {{time}}', { time });
    default: return tf(t, 'chat.message.waitModel', 'Waiting for the model · {{time}}', { time });
  } })();
  return withTime ? out : out.replace(/\s*·\s*$/, '');
}

export function StreamingBee({ content, sessionId }: StreamingBeeProps) {
  const { t, i18n } = useTranslation();
  const reducedMotion = usePrefersReducedMotion();
  const wait = useStreamingWait(content, sessionId);
  const zh = (i18n?.language ?? '').startsWith('zh');

  return (
    <div
      data-testid="streaming-bee"
      className={clsx('streaming-bee', `streaming-bee--${reducedMotion ? 'still' : 'flying'}`)}
    >
      <div className="streaming-bee-track" aria-hidden="true">
        <div className="streaming-bee-trail" />
        <div className="streaming-bee-fly">
          <div className="streaming-bee-bob">
            <BeeGlyph />
          </div>
        </div>
      </div>
      {/* The live region stays mounted and only its TEXT appears — screen
          readers often skip a live region that is inserted already filled. */}
      {wait && <span className="streaming-bee-note-text" aria-hidden="true" data-wait={wait.kind}>{waitText(wait, t, zh)}</span>}
      <span className="streaming-bee-note sr-only" role="status" aria-live="polite">
        {wait ? waitText(wait, t, zh, false) : ''}
      </span>
    </div>
  );
}

/** The bee, facing right; `.streaming-bee-fly` mirrors it on the way back. Shared
 *  with the Welcome screen bee (its wings animate under `.welcome-bee--flying`). */
export function BeeGlyph() {
  return (
    <svg width="26" height="23" viewBox="0 0 22 20" aria-hidden="true" focusable="false">
      <g className="streaming-bee-wing">
        <ellipse cx="9" cy="5.5" rx="3.6" ry="4.6" fill="#cfe3ff" fillOpacity=".75" stroke="#ffffff" strokeOpacity=".7" strokeWidth=".6" />
      </g>
      <g className="streaming-bee-wing streaming-bee-wing--back">
        <ellipse cx="13" cy="6" rx="3" ry="4" fill="#cfe3ff" fillOpacity=".6" stroke="#ffffff" strokeOpacity=".6" strokeWidth=".6" />
      </g>
      <path d="M2.2 12.5 L0.4 13 L2.2 13.6 Z" fill="#3a2a10" />
      <ellipse cx="10" cy="13" rx="8" ry="5.2" fill="#e8b53e" />
      <path d="M6.2 8.3 Q5 13 6.2 17.7" stroke="#2b2010" strokeWidth="2" fill="none" />
      <path d="M10.4 7.8 Q9.2 13 10.4 18.2" stroke="#2b2010" strokeWidth="2" fill="none" />
      <circle cx="17.6" cy="12.2" r="3.4" fill="#2b2010" />
      <circle cx="18.7" cy="11.4" r=".9" fill="#ffffff" fillOpacity=".85" />
      <path d="M17.8 9 Q18.6 6.6 20.4 6.2" stroke="#2b2010" strokeWidth=".8" fill="none" />
    </svg>
  );
}
