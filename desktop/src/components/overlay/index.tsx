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
import type { ReactNode } from 'react';
import clsx from 'clsx';

export const ROW_1 = 'min-h-[36px]';
export const ROW_2 = 'min-h-[48px]';

const TINT = (v: string, pct: number) => `color-mix(in srgb, var(${v}) ${pct}%, transparent)`;

export function Stat({ label, value, hint, testid }: {
  label: string; value: ReactNode; hint?: ReactNode; testid?: string;
}) {
  return (
    <div data-testid={testid}
      className="flex flex-col gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5 min-w-0">
      <span className="text-meta font-semibold uppercase tracking-[0.06em] text-[var(--color-text-muted)] truncate">{label}</span>
      <span className="text-display font-semibold text-[var(--color-text)] tabular-nums">{value}</span>
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

export interface TabItem<K extends string> { key: K; label: string; count?: number; testid?: string }

export function Tabs<K extends string>({ items, value, onChange }: {
  items: TabItem<K>[]; value: K; onChange: (k: K) => void;
}) {
  return (
    <div role="tablist" className="flex items-center gap-4 border-b border-[var(--color-border)]">
      {items.map((t) => {
        const on = t.key === value;
        return (
          <button key={t.key} type="button" role="tab" aria-selected={on} data-testid={t.testid}
            onClick={() => onChange(t.key)}
            className={clsx('h-9 -mb-px border-b-2 text-body transition-colors',
              on ? 'border-[var(--color-primary)] text-[var(--color-primary)] font-semibold'
                 : 'border-transparent text-[var(--color-text-muted)]')}>
            {t.label}
            {t.count != null && <span className="ml-1.5 text-meta text-[var(--color-text-muted)] tabular-nums">{t.count}</span>}
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
