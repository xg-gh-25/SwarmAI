/**
 * Shared overlay parts — one look for each kind of thing every Left Nav overlay
 * shows (design left-nav-backend-and-visual-system.md V6). Pure presentational:
 * no state, no fetching. Type scale text-meta … text-display, weights 400 / 600,
 * colours only from theme variables (index.css).
 *
 * Exports:
 * - `Stat`          a metric: uppercase label, display number, optional hint line
 * - `SectionLabel`  a block heading: meta, 600, uppercase, muted
 * - `Tabs`          underline tabs, optional count per tab
 * - `Segmented`     a filter: the selected option on a light primary tint
 * - `StatusBadge`   dot + text in one of five tones
 * - `Hint`          one line of muted explanatory text, no box
 * - `TableHead`     a table header row cell style (`tableHeadClass`)
 * - `ROW_1` / `ROW_2` the two row heights (36 / 48 px)
 */
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import clsx from 'clsx';

export const ROW_1 = 'min-h-[36px]';
export const ROW_2 = 'min-h-[48px]';

const TINT = (v: string, pct: number) => `color-mix(in srgb, var(${v}) ${pct}%, transparent)`;

export function Stat({ label, value, hint, tone, testid }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; testid?: string;
}) {
  return (
    <div data-testid={testid}
      className="flex flex-col gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5 min-w-0">
      <span className="text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)] truncate">{label}</span>
      <span className="text-display font-semibold tabular-nums"
        style={{ color: tone ? `var(${TONE_VAR[tone]})` : 'var(--color-text)' }}>{value}</span>
      {hint != null && <span className="text-meta text-[var(--color-text-muted)] truncate">{hint}</span>}
    </div>
  );
}

export function SectionLabel({ children, right, testid }: {
  children: ReactNode; right?: ReactNode; testid?: string;
}) {
  return (
    <div data-testid={testid} className="flex items-center gap-2">
      <span className="text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">{children}</span>
      {right != null && <span className="ml-auto text-meta text-[var(--color-text-muted)]">{right}</span>}
    </div>
  );
}

/** One tab. ``tone: 'alert'`` colours its count as something waiting on the person;
 *  ``icon`` is a Material Symbols name; ``hint`` is a short faint note after the label. */
export interface TabItem<K extends string> {
  key: K; label: string; count?: number; testid?: string; tone?: 'alert'; icon?: string; hint?: string;
  /** the button's id, for a panel that names its tab (aria-labelledby) */
  id?: string;
}

/** The overlay tab bar: a segmented track; the selected tab in the brand colour, the
 *  others lit on hover, so it reads as a set of switchable views. Arrow keys, Home and
 *  End move the choice; only the selected tab is in the Tab order. ``controls`` names
 *  the panel the tabs switch (aria-controls). */
export function Tabs<K extends string>({ items, value, onChange, label, testid, controls }: {
  items: TabItem<K>[]; value: K; onChange: (k: K) => void; label?: string; testid?: string;
  controls?: string;
}) {
  const keys = items.map((t) => t.key);
  function onKey(e: ReactKeyboardEvent<HTMLButtonElement>) {
    const i = Math.max(0, keys.indexOf(value));
    const n = keys.length;
    const next = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n
      : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    onChange(keys[next]);
    (e.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  }
  return (
    <div role="tablist" aria-label={label} data-testid={testid}
      className="inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-lg p-[3px]"
      style={{ background: TINT('--color-text', 6) }}>
      {items.map((t) => {
        const on = t.key === value;
        return (
          <button key={t.key} type="button" role="tab" aria-selected={on} data-testid={t.testid} id={t.id}
            aria-controls={controls} tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.key)} onKeyDown={onKey}
            className={clsx(
              'inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-body transition-colors',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary)]',
              on ? 'font-semibold text-[var(--color-primary)]'
                 : 'text-[var(--color-text-muted)] hover:bg-[color-mix(in_srgb,var(--color-text)_6%,transparent)] hover:text-[var(--color-text)]')}
            style={on ? { background: TINT('--color-primary', 16),
                          boxShadow: `inset 0 0 0 1px ${TINT('--color-primary', 40)}` } : undefined}>
            {t.icon && <span className="material-symbols-outlined icon-xs" aria-hidden>{t.icon}</span>}
            {t.label}
            {t.hint && <span className="text-meta font-normal text-[var(--color-text-faint)]">{t.hint}</span>}
            {t.count != null && (
              <span data-testid={t.testid ? `${t.testid}-count` : undefined}
                className="rounded-full px-1.5 text-[11px] font-semibold leading-[18px] tabular-nums"
                style={t.tone === 'alert'
                  ? { background: TINT('--color-status-error', 18), color: 'var(--color-status-error)' }
                  : { background: TINT('--color-text', 10) }}>
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export interface SegmentOption<K extends string> { key: K; label: string; testid?: string }

export function Segmented<K extends string>({ options, value, onChange, label }: {
  options: SegmentOption<K>[]; value: K; onChange: (k: K) => void; label?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      {label && <span className="text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)]">{label}</span>}
      <div role="radiogroup" className="flex items-center gap-0.5 rounded-md border border-[var(--color-border)] p-0.5">
        {options.map((o) => {
          const on = o.key === value;
          return (
            <button key={o.key} type="button" role="radio" aria-checked={on} data-testid={o.testid}
              onClick={() => onChange(o.key)}
              style={on ? { background: TINT('--color-primary', 15) } : undefined}
              className={clsx('h-6 px-2.5 rounded text-small transition-colors',
                on ? 'text-[var(--color-primary)] font-semibold' : 'text-[var(--color-text-muted)]')}>
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export type Tone = 'neutral' | 'success' | 'warning' | 'error' | 'primary';
const TONE_VAR: Record<Tone, string> = {
  neutral: '--color-text-muted',
  success: '--color-status-success',
  warning: '--color-status-warning',
  error: '--color-status-error',
  primary: '--color-primary',
};

export function StatusBadge({ tone = 'neutral', children, testid }: {
  tone?: Tone; children: ReactNode; testid?: string;
}) {
  const v = TONE_VAR[tone];
  return (
    <span data-testid={testid} data-tone={tone}
      className="inline-flex items-center gap-1.5 text-meta whitespace-nowrap"
      style={{ color: `var(${v})` }}>
      <span aria-hidden className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: `var(${v})` }} />
      {children}
    </span>
  );
}

export function Hint({ children, testid }: { children: ReactNode; testid?: string }) {
  return <p data-testid={testid} className="text-small text-[var(--color-text-muted)]">{children}</p>;
}

/** A table header cell: meta, 600, uppercase, muted. A sorted column adds an arrow only. */
export const tableHeadClass =
  'text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)] text-left';
