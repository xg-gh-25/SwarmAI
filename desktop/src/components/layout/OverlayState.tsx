/**
 * OverlayState — the shared first-load states of an overlay: loading, error, empty
 * (docs/design/left-nav-overlay-finish.md §1.2).
 *
 * Used only for the moment an overlay has NO data yet. A refresh over data already
 * shown keeps the toolbar's small hint instead. Each overlay keeps its own wording
 * and testids; these components only give the three states one look:
 * - `OverlayLoading` — a centred "Loading…" line.
 * - `OverlayError`   — a dashed red box with the message and a Retry button. Every
 *                      first-load failure has one, so a failed fetch never reads as
 *                      "nothing here".
 *                      `compact` is the small left-aligned form for a box inside a
 *                      panel or above a table (C&M's queue, ToDo's banner).
 * - `OverlayEmpty`   — a centred, muted message with an optional Material icon.
 *
 * Key exports: `OverlayLoading`, `OverlayError`, `OverlayEmpty`.
 */
import type { ReactNode } from 'react';

export function OverlayLoading({ label = 'Loading…', testid }: { label?: string; testid?: string }) {
  return (
    <div data-testid={testid} className="py-8 text-center text-[12px] text-[var(--color-text-muted)]">
      {label}
    </div>
  );
}

export function OverlayError({ message, onRetry, testid, retryTestid, compact = false, className }: {
  message: ReactNode;
  onRetry: () => void;
  testid?: string;
  retryTestid?: string;
  /** Small left-aligned form, for a box inside a panel. */
  compact?: boolean;
  /** Outer spacing; the box itself is fixed. */
  className?: string;
}) {
  const outer = className ?? (compact ? '' : 'mx-auto my-6 max-w-sm');
  const box = compact ? 'rounded-md px-2.5 py-2 text-[11px]' : 'rounded-lg px-4 py-4 text-center text-sm';
  return (
    <div
      data-testid={testid}
      role="alert"
      className={`${outer} ${box} border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] text-[var(--color-text)]`}
    >
      <div>{message}</div>
      <button
        type="button"
        data-testid={retryTestid}
        onClick={onRetry}
        className={compact ? 'mt-1.5 rounded px-2 py-0.5 text-[11px] font-medium text-white' : 'mt-2 rounded-md px-3 py-1 text-xs font-medium text-white'}
        style={{ background: '#d0524a' }}
      >
        Retry
      </button>
    </div>
  );
}

export function OverlayEmpty({ children, icon, testid }: { children: ReactNode; icon?: string; testid?: string }) {
  return (
    <div data-testid={testid} className="py-10 text-center text-[12px] text-[var(--color-text-faint)]">
      {icon && <div className="material-symbols-outlined icon-xl opacity-40">{icon}</div>}
      <div className={icon ? 'mt-1' : undefined}>{children}</div>
    </div>
  );
}
