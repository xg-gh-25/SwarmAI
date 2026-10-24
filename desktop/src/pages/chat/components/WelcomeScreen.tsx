/**
 * WelcomeScreen — the new-chat landing (design docs/design/welcome-screen-v2.md).
 *
 * Shows the SwarmAI brand (logo, wordmark, slogan over a faint honeycomb) and one
 * "Needs you" card, then gets out of the way of the composer.
 *
 * - One queue: the card reads `overlayQuery.needsYou()` — the same react-query key
 *   as the Needs You overlay and the left-nav pill (useAttentionQueue), so all
 *   three show the same number (counts.blocking + counts.review).
 * - No send: a row or "Open all" opens the Needs You overlay; nothing is sent.
 * - Durable: an empty queue is one calm line; a failed read hides the card.
 * - Top-aligned (no vertical centring), so a late read never moves the hero.
 *
 * @exports WelcomeScreen
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { overlayQuery } from '../../../components/layout/overlayQueries';
import { ATTENTION_SOURCE_LABEL, type AttentionEntry } from '../../../services/attention';
import { dispatchUiCommand } from '../../../utils/uiCommands';
import { tf } from '../tf';

const MAX_ROWS = 3;

// ---------------------------------------------------------------------------
// Honeycomb — built once per app session (pure geometry, no theme colour: the
// stroke is currentColor and .welcome-comb sets it from --color-brand).
// ---------------------------------------------------------------------------

const LIT = new Map([['3,8', 0.15], ['4,10', 0.3], ['2,11', 0.45], ['5,7', 0.25], ['3,13', 0.55], ['6,9', 0.4]]);

function hexPoints(cx: number, cy: number, r: number): string {
  const pts: string[] = [];
  for (let k = 0; k < 6; k++) {
    const a = (Math.PI / 180) * (60 * k - 30);
    pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`);
  }
  return pts.join(' ');
}

const COMB = (() => {
  const r = 15;
  const w = Math.sqrt(3) * r;
  const h = 1.5 * r;
  const cells: string[] = [];
  const lit: Array<{ points: string; delay: number }> = [];
  for (let y = 0; y < 14; y++) {
    for (let x = 0; x < 22; x++) {
      const points = hexPoints(x * w + (y % 2 ? w / 2 : 0), y * h, r);
      cells.push(points);
      const delay = LIT.get(`${y},${x}`);
      if (delay !== undefined) lit.push({ points, delay });
    }
  }
  return { cells, lit };
})();

function Honeycomb() {
  return (
    <svg className="welcome-comb" width="100%" viewBox="0 0 640 300" preserveAspectRatio="xMidYMin slice" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth={1}>
        {COMB.cells.map((p) => <polygon key={p} points={p} />)}
      </g>
      {COMB.lit.map((c) => <polygon key={c.points} className="lit" points={c.points} style={{ animationDelay: `${c.delay}s` }} />)}
    </svg>
  );
}

function HexDot({ tone }: { tone: 'brand' | 'calm' }) {
  return (
    <svg className="w-3 h-3 shrink-0" viewBox="0 0 12 12" aria-hidden="true">
      <polygon
        points="6,0.8 10.6,3.4 10.6,8.6 6,11.2 1.4,8.6 1.4,3.4"
        fill={tone === 'brand' ? 'var(--color-brand)' : 'var(--color-status-success)'}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Needs you card
// ---------------------------------------------------------------------------

const openNeedsYou = () => { dispatchUiCommand('show-needs-you'); };

function rowLabel(it: AttentionEntry): string {
  return it.brain ?? ATTENTION_SOURCE_LABEL[it.source];
}

function NeedsYouCard() {
  const { t } = useTranslation();
  const { data, isError } = useQuery(overlayQuery.needsYou());
  if (isError && !data) return null;          // a failed read hides the card
  const label = tf(t, 'chat.welcome.needsYou', 'Needs you');

  if (!data) {
    // Loading: hold the card's height so nothing below shifts when it lands.
    return <div data-testid="welcome-needs" className="h-[120px]" aria-hidden="true" />;
  }

  const count = data.counts.blocking + data.counts.review;
  const rows = data.items.slice(0, MAX_ROWS);
  // "N more" agrees with the badge: everything counted that is not shown.
  const more = Math.max(0, Math.max(count, data.items.length) - rows.length);

  return (
    <section data-testid="welcome-needs" className="welcome-rise [animation-delay:0.12s]">
      <div className="flex items-center justify-between mb-[7px] px-0.5 text-meta font-semibold uppercase tracking-[0.05em] text-[var(--color-text-muted)]">
        <span className="flex items-center gap-1.5">
          {label}
          {count > 0 && (
            <span
              data-testid="welcome-needs-count"
              className="min-w-[18px] h-4 px-[5px] rounded-lg grid place-items-center tracking-normal tabular-nums bg-[color-mix(in_srgb,var(--color-brand)_18%,transparent)] text-[var(--color-brand-strong)]"
            >
              {count}
            </span>
          )}
        </span>
        {count > 0 && (
          <button
            type="button"
            data-testid="welcome-needs-open"
            onClick={openNeedsYou}
            className="normal-case tracking-normal text-[var(--color-brand-strong)] hover:underline cursor-pointer"
          >
            {tf(t, 'chat.welcome.openAll', 'Open all')} ›
          </button>
        )}
      </div>

      <div className="relative overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-[0_1px_2px_var(--color-scrim),0_8px_24px_-12px_var(--color-scrim-medium)]">
        <span
          aria-hidden="true"
          className={`absolute left-0 inset-y-0 w-[3px] ${count > 0 ? 'welcome-card-bar' : 'bg-[var(--color-status-success)]'}`}
        />
        {count === 0 ? (
          <div data-testid="welcome-needs-empty" className="flex items-center gap-2.5 pl-4 pr-3.5 py-3 text-body text-[var(--color-text-secondary)]">
            <HexDot tone="calm" />
            {tf(t, 'chat.welcome.nothing', 'Nothing needs your decision right now')}
          </div>
        ) : (
          <>
            {rows.map((it, i) => (
              <button
                key={it.id}
                type="button"
                data-testid="welcome-needs-row"
                onClick={openNeedsYou}
                title={it.title}
                className={`group w-full text-left flex items-center gap-2.5 pl-4 pr-3.5 py-2.5 text-body text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors cursor-pointer ${i > 0 ? 'border-t border-[var(--color-border)]' : ''}`}
              >
                <HexDot tone="brand" />
                <span className="flex-1 min-w-0 truncate">{it.title}</span>
                <span className="shrink-0 text-meta text-[var(--color-text-muted)] px-[7px] py-px rounded-md bg-[var(--color-hover)]">
                  {rowLabel(it)}
                </span>
                <span className="material-symbols-outlined icon-sm shrink-0 text-[var(--color-text-muted)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--color-brand)]">
                  chevron_right
                </span>
              </button>
            ))}
            {more > 0 && (
              <div data-testid="welcome-needs-more" className="pl-4 pr-3.5 py-2 text-small text-[var(--color-text-muted)] border-t border-[var(--color-border)]">
                {tf(t, 'chat.welcome.more', '{{n}} more in Needs You', { n: more })}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export const WelcomeScreen: React.FC = () => (
  // `chat-scroll-gutter` (index.css): the same overflow + side gutters as the
  // transcript scroller, so this column lines up with the composer below it.
  <div className="relative flex flex-col items-center h-full chat-scroll-gutter">
    <Honeycomb />
    {/* The app-wide `chat-column`: the same width as the transcript and composer,
        so nothing changes width when the first message replaces this screen. */}
    <div className="relative chat-column flex flex-col pt-9 pb-4">
      <div className="welcome-rise flex flex-col items-center text-center mb-[26px] select-none">
        <div className="relative w-[60px] h-[60px] mb-3.5">
          <span className="welcome-glow" aria-hidden="true" />
          <img
            src="/swarm-avatar.svg"
            alt="SwarmAI"
            draggable={false}
            className="welcome-logo relative w-[60px] h-[60px] rounded-2xl"
          />
        </div>
        <h1 data-testid="welcome-wordmark" className="text-display font-semibold tracking-[-0.01em] text-[var(--color-text)]">
          SwarmAI
        </h1>
        <p data-testid="welcome-slogan" className="mt-1 text-heading text-[var(--color-text-secondary)]">
          Grow your <span className="welcome-brand-text font-semibold">Brain</span>. Deliver{' '}
          <span className="welcome-brand-text font-semibold">Anything</span>.
        </p>
      </div>
      <NeedsYouCard />
    </div>
  </div>
);

export default WelcomeScreen;
