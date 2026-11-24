/**
 * The shared pieces of the Needs You overlay's four tabs (design Knowledge/Mockups/
 * 2026-11-24-needs-you-redesign.html), the same visual language as the Pipeline and
 * Swarm Out panels: a card, a zone label with its count, one big number with what it is
 * compared to, a row of small numbers, and the grow-in that bars use when their data
 * changes (not on every refetch).
 *
 * Exports: CARD, MUTED, DIM, ZoneLabel, Big, Compare, Kpis, useGrown, agoText
 */
import { useEffect, useState, type ReactNode } from 'react';
import type { TFunction } from 'i18next';

export const CARD = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
export const MUTED = 'text-[var(--color-text-muted)]';
export const DIM = 'text-[var(--color-text-dim)]';

/** A zone's small caps label, its count and what sits on its right. */
export function ZoneLabel({ children, count, right, testid }: {
  children: ReactNode; count?: number; right?: ReactNode; testid?: string;
}) {
  return (
    <div className="mb-2.5 flex items-center gap-2 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]" data-testid={testid}>
      <span>{children}</span>
      {count != null && (
        <span className="inline-block min-w-[18px] rounded-full bg-[var(--color-bg-chrome)] px-1.5 text-center tabular-nums tracking-normal text-[var(--color-text-secondary)]">{count}</span>
      )}
      {right != null && <span className="ml-auto font-normal tracking-normal">{right}</span>}
    </div>
  );
}

/** The one number a tab leads with, its unit, what it counts and what it is compared to. */
export function Big({ value, unit, what, of, compare, testid }: {
  value: string; unit?: string; what: string; of?: string; compare?: ReactNode; testid?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1" data-testid={testid}>
      <span className="text-[44px] font-semibold leading-[48px] tracking-tight tabular-nums" data-testid={testid ? `${testid}-value` : undefined}>
        {value}{unit && <span className={`ml-0.5 text-[22px] font-medium ${MUTED}`}>{unit}</span>}
      </span>
      <span className="text-heading">{what}</span>
      {of && <span className={`text-body tabular-nums ${MUTED}`}>{of}</span>}
      {compare != null && <span className="ml-auto">{compare}</span>}
    </div>
  );
}

/** A chip: the change in its colour (green better, amber worse, plain when a change is
 *  neither: more items learned is not better by itself), then what it was. */
export function Compare({ change, good, was, testid }: { change?: string; good?: boolean; was: string; testid?: string }) {
  return (
    <span className="whitespace-nowrap rounded-full bg-[var(--color-bg-chrome)] px-2.5 py-0.5 text-small tabular-nums" data-testid={testid}>
      {change && (
        <b className="font-semibold" style={{ color: good == null ? 'var(--color-text)' : good ? 'var(--color-status-success)' : 'var(--color-status-warning)' }}>{change}</b>
      )}
      <span className={MUTED}>{change ? ' · ' : ''}{was}</span>
    </span>
  );
}

const COLS: Record<number, string> = { 2: 'md:grid-cols-2', 3: 'md:grid-cols-3', 4: 'md:grid-cols-4' };

/** A row of small numbers under a border, each with its label; amber when it is a problem.
 *  Stacked on a narrow window, side by side from md up. */
export function Kpis({ items, testid }: {
  items: Array<{ key: string; label: string; value: ReactNode; warn?: boolean }>; testid?: string;
}) {
  return (
    <div className={`mt-[18px] grid grid-cols-1 border-t border-[var(--color-border)] ${COLS[items.length] ?? ''}`} data-testid={testid}>
      {items.map((k, i) => (
        <div key={k.key} data-testid={testid ? `${testid}-${k.key}` : undefined}
          className={`min-w-0 pb-0.5 pt-3.5 ${i ? 'border-t border-[var(--color-border)] md:border-l md:border-t-0 md:px-4' : 'md:pr-4'}`}>
          <div className={`text-small ${MUTED}`}>{k.label}</div>
          <div className="mt-0.5 whitespace-nowrap text-[22px] font-semibold leading-7 tabular-nums"
            style={k.warn ? { color: 'var(--color-status-warning)' } : undefined}>{k.value}</div>
        </div>
      ))}
    </div>
  );
}

/** True one frame after `key` changes: what bars grow from. A refetch with the same data
 *  keeps them grown. */
export function useGrown(key: string): boolean {
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    setGrown(false);
    if (typeof requestAnimationFrame === 'undefined') { setGrown(true); return undefined; }
    let b = 0;
    const a = requestAnimationFrame(() => { b = requestAnimationFrame(() => setGrown(true)); });
    return () => { cancelAnimationFrame(a); cancelAnimationFrame(b); };
  }, [key]);
  return grown;
}

/** How long ago an ISO time was, in the user's language ("12 min", "5 h", "3 d"); null
 *  for an unknown or future time. */
export function agoText(t: TFunction, iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const at = new Date(iso).getTime();
  if (Number.isNaN(at) || at > now + 60_000) return null;
  const min = Math.max(1, Math.round((now - at) / 60_000));
  if (min < 60) return t('needYou.ago.min', { count: min });
  const h = Math.round(min / 60);
  if (h < 48) return t('needYou.ago.h', { count: h });
  return t('needYou.ago.d', { count: Math.round(h / 24) });
}
