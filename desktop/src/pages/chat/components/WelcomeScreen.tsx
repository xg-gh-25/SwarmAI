/**
 * WelcomeScreen — the new-chat landing (design docs/design/welcome-screen-v2.md).
 *
 * Shows the SwarmAI brand (logo, wordmark, slogan over a faint honeycomb) and one
 * "Needs you" card, then gets out of the way of the composer.
 *
 * - One queue: the card reads `overlayQuery.needsYou()` — the same react-query key
 *   as the Needs You overlay and the left-nav pill (useAttentionQueue), so all
 *   three show the same number (counts.blocking + counts.review).
 * - Rows (NeedsYouRow, shared with the overlay): the backend's plain title and
 *   summary; click the text = the item's prompt goes into the input (not sent),
 *   ↑ = send it now and start the session. "N more" expands the list in place,
 *   capped in height so it scrolls inside the card; "Open all" opens the overlay.
 * - Durable: an empty queue is one calm line; a failed read hides the card.
 * - Top-aligned (no vertical centring), so a late read never moves the hero.
 * - The streaming bee (same drawing) flies in once (3.6s) and lands on the logo;
 *   hovering the landed logo sends it on one lap. Geometry in welcomeBee.ts.
 *
 * @exports WelcomeScreen
 */
import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { overlayQuery } from '../../../components/layout/overlayQueries';
import { NeedsYouRow } from '../../../components/layout/NeedsYouRow';
import { dispatchInjectChatInput } from '../injectChatInput';
import { dispatchUiCommand } from '../../../utils/uiCommands';
import { usePrefersReducedMotion } from '../../../hooks/usePrefersReducedMotion';
import { tf } from '../tf';
import { BeeGlyph } from './StreamingBee';
import { FLIGHT_MS, LAP_MS, flightKeyframes } from './welcomeBee';

const MAX_ROWS = 3;

// ---------------------------------------------------------------------------
// Honeycomb — pure geometry, no theme colour: the stroke is currentColor and
// .welcome-comb sets it from --color-brand. The grid is one SVG <pattern> in
// pixel units (cells stay the same size at any column width), drawn on a
// 2400px-wide canvas centred on the column, so the comb is centred on the logo.
// ---------------------------------------------------------------------------

// About the size the old viewBox-scaled cells had in a typical chat column.
const R = 24;
const W = Math.sqrt(3) * R;
const H = 1.5 * R;
const CANVAS_W = 2400;
const CANVAS_H = 560;
/** Column offset that puts the old 640-wide grid's centre on the canvas centre. */
const COL_SHIFT = 17;

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
  // One pattern tile = two rows: the cells whose centres sit on its corners
  // and the offset cell in its middle (the tile clips each to its share).
  const tile = [[0, 0], [W, 0], [W / 2, H], [0, 2 * H], [W, 2 * H]].map(([x, y]) => hexPoints(x, y, R));
  const lit = [...LIT].map(([key, delay]) => {
    const [y, x] = key.split(',').map(Number);
    const col = x + COL_SHIFT;
    return { points: hexPoints(col * W + (y % 2 ? W / 2 : 0), y * H, R), delay };
  });
  return { tile, lit };
})();

function Honeycomb() {
  // One id per mounted screen: every open tab renders its own Welcome, and a
  // url(#id) that resolves to a hidden tab's <pattern> would paint nothing.
  const patternId = `welcome-comb-${useId().replace(/:/g, '')}`;
  return (
    <div className="welcome-comb" aria-hidden="true">
      <svg width={CANVAS_W} height={CANVAS_H}>
        <defs>
          <pattern id={patternId} patternUnits="userSpaceOnUse" width={W} height={2 * H}>
            <g fill="none" stroke="currentColor" strokeWidth={1}>
              {COMB.tile.map((p) => <polygon key={p} points={p} />)}
            </g>
          </pattern>
        </defs>
        <rect width={CANVAS_W} height={CANVAS_H} fill={`url(#${patternId})`} />
        {COMB.lit.map((c) => <polygon key={c.points} className="lit" points={c.points} style={{ animationDelay: `${c.delay}s` }} />)}
      </svg>
    </div>
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
// Fill / send land in THIS tab's input: the Welcome screen only shows in the
// active, empty tab, so the shared ChatInput is this tab's input.
const fillInput = (text: string) => { dispatchInjectChatInput({ text, focus: true, autoSend: false }); };
const sendNow = (text: string) => { dispatchInjectChatInput({ text, focus: true, autoSend: true }); };

function NeedsYouCard() {
  const { t } = useTranslation();
  const { data, isError } = useQuery(overlayQuery.needsYou());
  const [open, setOpen] = useState(false);
  if (isError && !data) return null;          // a failed read hides the card
  const label = tf(t, 'chat.welcome.needsYou', 'Needs you');

  if (!data) {
    // Loading: hold the card's height so nothing below shifts when it lands.
    return <div data-testid="welcome-needs" className="h-[120px]" aria-hidden="true" />;
  }

  const count = data.counts.blocking + data.counts.review;
  const rows = open ? data.items : data.items.slice(0, MAX_ROWS);
  // "N more" agrees with the badge: everything counted that is not shown.
  const more = Math.max(0, Math.max(count, data.items.length) - Math.min(MAX_ROWS, data.items.length));

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
            {/* Expanded: every item, in a list capped at ~5 rows that scrolls
                inside the card, so the page never grows past the composer. */}
            <div
              data-testid="welcome-needs-list"
              className={open ? 'max-h-[290px] overflow-y-auto' : undefined}
            >
              {rows.map((it, i) => (
                <NeedsYouRow
                  key={it.id}
                  item={it}
                  testid="welcome-needs-row"
                  divider={i > 0}
                  lead={<HexDot tone="brand" />}
                  onFill={fillInput}
                  onSend={sendNow}
                />
              ))}
            </div>
            {more > 0 && (
              <button
                type="button"
                data-testid="welcome-needs-more"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
                className="w-full text-left flex items-center justify-between pl-4 pr-3.5 py-2 text-small text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-t border-[var(--color-border)] cursor-pointer"
              >
                <span>{open ? tf(t, 'chat.welcome.less', 'Show less') : tf(t, 'chat.welcome.more', '{{n}} more', { n: more })}</span>
                <span className="material-symbols-outlined icon-sm">{open ? 'expand_less' : 'expand_more'}</span>
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Bee — the same drawing as the streaming bee, but it LANDS: the streaming bee
// never lands (a running turn), this one flies in once and settles on the logo
// (ready). Hovering the landed logo sends it on one lap. Reduced motion, a hidden
// tab or no Web Animations: it just sits on the logo. Nothing runs once landed.
// ---------------------------------------------------------------------------

function useWelcomeBee(rootRef: React.RefObject<HTMLDivElement | null>) {
  const reduced = usePrefersReducedMotion();
  const beeRef = useRef<HTMLSpanElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const anim = useRef<Animation | null>(null);
  const [flying, setFlying] = useState(false);

  const fly = useCallback((kind: 'in' | 'lap') => {
    const bee = beeRef.current;
    const root = rootRef.current?.getBoundingClientRect();
    const logo = logoRef.current?.getBoundingClientRect();
    if (reduced || !bee || typeof bee.animate !== 'function' || !root || !logo || root.width === 0) return;
    // Enter from just past the screen's left edge, 40px below its top.
    const start = { x: root.left - logo.left - 40, y: root.top + 40 - logo.top };
    anim.current?.cancel();
    const a = bee.animate(flightKeyframes(kind, start), {
      duration: kind === 'in' ? FLIGHT_MS : LAP_MS, easing: 'linear', fill: 'forwards',
    });
    anim.current = a;
    setFlying(true);
    a.onfinish = () => setFlying(false);
  }, [reduced, rootRef]);

  // Layout effect: the first keyframe applies before paint, so the bee never
  // flashes on the landing spot before taking off.
  useLayoutEffect(() => {
    fly('in');
    return () => { anim.current?.cancel(); anim.current = null; };
    // once per mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (reduced && anim.current) { anim.current.cancel(); anim.current = null; setFlying(false); }
  }, [reduced]);

  const onLogoEnter = reduced ? undefined : () => { if (!flying) fly('lap'); };
  return { beeRef, logoRef, flying, onLogoEnter };
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export const WelcomeScreen: React.FC = () => {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const { beeRef, logoRef, flying, onLogoEnter } = useWelcomeBee(rootRef);
  return (
  // `chat-scroll-gutter` (index.css): the same overflow + side gutters as the
  // transcript scroller, so this column lines up with the composer below it.
  <div ref={rootRef} className="relative flex flex-col items-center h-full chat-scroll-gutter">
    <Honeycomb />
    {/* The app-wide `chat-column`: the same width as the transcript and composer,
        so nothing changes width when the first message replaces this screen. */}
    <div className="relative chat-column flex flex-col pt-9 pb-4">
      <div className="welcome-rise flex flex-col items-center text-center mb-[26px] select-none">
        <div ref={logoRef} data-testid="welcome-logo" onMouseEnter={onLogoEnter} className="relative w-[60px] h-[60px] mb-3.5">
          <span className="welcome-glow" aria-hidden="true" />
          <img
            src="/swarm-avatar.svg"
            alt="SwarmAI"
            draggable={false}
            className="welcome-logo relative w-[60px] h-[60px] rounded-2xl"
          />
          <span
            ref={beeRef}
            data-testid="welcome-bee"
            aria-hidden="true"
            className={`welcome-bee ${flying ? 'welcome-bee--flying' : 'welcome-bee--landed'}`}
          >
            <span className="welcome-bee-bob"><BeeGlyph /></span>
          </span>
        </div>
        <h1 data-testid="welcome-wordmark" className="text-display font-semibold tracking-[-0.01em] text-[var(--color-text)]">
          Swarm
        </h1>
        <p data-testid="welcome-slogan" className="mt-1 text-heading text-[var(--color-text-secondary)]">
          {t('ui.WelcomeScreen.sloganGrow')}<span className="welcome-brand-text font-semibold">{t('ui.WelcomeScreen.sloganBrain')}</span>{t('ui.WelcomeScreen.sloganDeliver')}
          <span className="welcome-brand-text font-semibold">{t('ui.WelcomeScreen.sloganAnything')}</span>{t('ui.WelcomeScreen.sloganEnd')}
        </p>
      </div>
      <NeedsYouCard />
    </div>
  </div>
  );
};

export default WelcomeScreen;
