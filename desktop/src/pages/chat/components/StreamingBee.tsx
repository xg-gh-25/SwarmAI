/**
 * StreamingBee — the "this turn is still running" indicator at the end of the
 * streaming assistant message (replaces the old pulsing caret).
 *
 * Three variants, chosen by props + the OS motion setting:
 * - flying  — the bee flies left↔right along a short dashed track, flipping at
 *             each end, bobbing and flapping (content is arriving).
 * - resting — the bee sits at the start of the track with folded wings and
 *             breathes; a muted line "{name} is mulling it over…" appears. Set
 *             by TabView via useStreamingRest: no new content for 30s while the
 *             connection is alive. The copy deliberately never says "stuck",
 *             never shows seconds and never promises "soon" — a quiet turn is
 *             not a failure, and we do not know how long it will take.
 * - still   — prefers-reduced-motion: no flight, no flap, breathing only.
 *
 * The bee is an inline SVG (not the 🐝 emoji, whose facing direction and art
 * differ per platform) and is aria-hidden; only the resting line is announced
 * (an always-mounted role=status, polite region that the text enters). All
 * motion is CSS keyframes on transform/opacity in index.css (`.streaming-bee*`):
 * flight, bob and breathing run on HTML wrappers (compositor); the wing flap
 * scales SVG <g> children, which browsers repaint on the main thread — a
 * 26×23px area, accepted.
 *
 * @exports StreamingBee, StreamingBeeProps
 */
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { tf } from '../tf';
import { ASSISTANT_NAME } from '../assistantName';
import { usePrefersReducedMotion } from '../../../hooks/usePrefersReducedMotion';

export interface StreamingBeeProps {
  /** No new content for a while (see useStreamingRest) — land and say so. */
  resting: boolean;
}

export function StreamingBee({ resting }: StreamingBeeProps) {
  const { t } = useTranslation();
  const reducedMotion = usePrefersReducedMotion();
  const variant = reducedMotion ? 'still' : resting ? 'resting' : 'flying';

  return (
    <div
      data-testid="streaming-bee"
      className={clsx('streaming-bee', `streaming-bee--${variant}`)}
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
      <span className="streaming-bee-note" role="status" aria-live="polite">
        {resting && (
          <span className="streaming-bee-note-text">
            {tf(t, 'chat.message.mulling', '{{name}} is mulling it over…', { name: ASSISTANT_NAME })}
          </span>
        )}
      </span>
    </div>
  );
}

/** The bee, facing right; `.streaming-bee-fly` mirrors it on the way back. */
function BeeGlyph() {
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
