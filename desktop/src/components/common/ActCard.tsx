/**
 * ActCard — the ONE look of every card that waits on the user (design
 * docs/design/2026-10-26-turn-cards-unified.md §1 D): AskUserQuestion, the permission
 * request and the escalation.
 *
 * - `ActFrame`: the chat column's width (no fixed width: it follows the window),
 *   border, radius, a primary left bar; a header line (icon, title that
 *   names Swarm, grey meta on the right: a question's tag, a timeout, a severity),
 *   the body, and a footer whose buttons sit on the right.
 * - `ActOption`: the one selectable option box (recommended ones say so).
 * - `ActDone`: once answered, the card becomes ONE grey line that still carries the
 *   decision (what was asked → what was chosen / approved / denied).
 *
 * @exports ActFrame, ActOption, ActDone
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export function ActFrame({ icon, title, meta, children, footer, testId }: {
  icon: string; title: ReactNode; meta?: ReactNode; children: ReactNode; footer?: ReactNode; testId?: string;
}) {
  return (
    <section data-testid={testId} data-act-frame=""
      className="my-2 w-full min-w-0 overflow-hidden rounded-[10px] border border-[var(--color-border)] border-l-[3px] border-l-primary bg-[var(--color-card)]">
      <header data-testid="act-title" className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)] text-[12.5px] text-[var(--color-text-secondary)]">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">{icon}</span>
        <span className="font-semibold text-[var(--color-text)]">{title}</span>
        {meta && <span data-testid="act-meta" className="ml-auto pl-3 text-[11.5px] text-[var(--color-text-dim)] tabular-nums">{meta}</span>}
      </header>
      <div className="px-3 py-2.5 space-y-2 text-[13px] text-[var(--color-text)]">{children}</div>
      {footer && (
        <footer data-testid="act-footer" className="flex items-center justify-end gap-2 px-3 py-2 border-t border-[var(--color-border)]">
          {footer}
        </footer>
      )}
    </section>
  );
}

export function ActOption({ label, description, selected, recommended, recommendedLabel, onClick, disabled, round = true }: {
  label: ReactNode; description?: ReactNode; selected?: boolean; recommended?: boolean; recommendedLabel?: string;
  onClick?: () => void; disabled?: boolean; round?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onClick} disabled={disabled} data-selected={selected ? 'true' : 'false'}
      className={`w-full text-left flex items-start gap-2.5 px-2.5 py-2 rounded-[7px] border transition-colors ${
        selected ? 'border-primary bg-primary/10' : 'border-[var(--color-border)] hover:border-primary/60'
      } ${disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}>
      <span aria-hidden="true" className={`mt-[3px] w-3.5 h-3.5 shrink-0 border-2 ${round ? 'rounded-full' : 'rounded-sm'} ${
        selected ? 'border-primary bg-primary' : 'border-[var(--color-text-muted)]'}`} />
      <span className="min-w-0 flex-1">
        <span className="text-[13px] text-[var(--color-text)]">{label}</span>
        {description && <span className="block text-[12px] text-[var(--color-text-dim)]">{description}</span>}
      </span>
      {recommended && <span className="shrink-0 text-[11px] text-primary">{recommendedLabel ?? t('chat.escalation.recommended')}</span>}
    </button>
  );
}

export function ActDone({ icon = 'check', label, children, testId }: {
  icon?: string; label: ReactNode; children?: ReactNode; testId?: string;
}) {
  return (
    <div data-testid={testId} data-act-done=""
      className="my-1 flex items-baseline gap-1.5 text-[12.5px] text-[var(--color-text-secondary)] min-w-0">
      <span aria-hidden="true" className="material-symbols-outlined icon-sm self-center text-[var(--color-text-dim)]">{icon}</span>
      <span className="shrink-0">{label}</span>
      {children && <span className="min-w-0 break-words">· {children}</span>}
    </div>
  );
}
