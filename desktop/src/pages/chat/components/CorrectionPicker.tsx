/**
 * Correction picker: one line under a user message that stated a mistake, where the person
 * says which class the correction belongs to (design docs/design/2026-10-30-autonomy-tenets-gaps.md
 * §2). The classes are the ones the backend offered (an existing entry, a golden task, an
 * earlier correction), plus "a new kind" and "not a correction". One click records it and
 * the line goes away, here and in the CDF panel's list; ignoring it leaves the correction
 * unconfirmed (it stays in that list). The line never wraps: it adds at most one line under
 * the bubble, and each title is cut to fit, with the full title and its kind in the tooltip.
 *
 * Key exports: `CorrectionPicker` (under a chat message), `CorrectionChoices` (the choices
 * for one correction, also used by the CDF panel's waiting list).
 */
import { Fragment, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import {
  correctionsService, markAnswered, usePendingCorrection,
  type CorrectionKind, type PendingCorrection,
} from '../../../services/corrections';

const KIND_FALLBACK: Record<CorrectionKind, string> = {
  entry: 'Knowledge entry', golden: 'Golden task', ledger: 'Earlier correction',
};

/** The choices for one correction. One click records the person's choice; the row is
 *  marked answered everywhere, then `onChosen` runs. */
export function CorrectionChoices({ row, onChosen, showQuestion = true }: {
  row: PendingCorrection; onChosen?: () => void; showQuestion?: boolean;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const choose = async (choice: string) => {
    setBusy(true);
    setFailed(false);
    try {
      await correctionsService.confirm(row.id, choice);
      markAnswered(row.id);
      onChosen?.();
    } catch {
      setFailed(true);
      setBusy(false);
    }
  };

  const link = 'shrink-0 underline decoration-dotted hover:text-[var(--color-text)] transition-colors '
    + 'disabled:opacity-50 rounded-sm focus-visible:outline focus-visible:outline-1 '
    + 'focus-visible:outline-[var(--color-primary)] active:text-[var(--color-text)]';
  const dot = <span aria-hidden="true" className="shrink-0 text-[var(--color-text-faint)]">·</span>;
  return (
    <div className="flex flex-nowrap items-center gap-x-1.5 min-w-0 max-w-full overflow-hidden text-xs text-[var(--color-text-muted)]"
      role="group" aria-label={tf(t, 'chat.correction.label', 'Is this a correction?')}>
      {showQuestion && <span className="shrink-0">{tf(t, 'chat.correction.question', 'Correcting me? File it under:')}</span>}
      {row.candidates.map((c, i) => {
        const label = `${tf(t, `chat.correction.kind.${c.kind}`, KIND_FALLBACK[c.kind] ?? c.kind)}: ${c.title}`;
        return (
          <Fragment key={c.key}>
            {i > 0 && dot}
            <button type="button" className={`${link} min-w-0 shrink truncate max-w-[14em]`} disabled={busy}
              title={label} aria-label={label} onClick={() => { void choose(c.key); }}>
              {c.title}
            </button>
          </Fragment>
        );
      })}
      {row.candidates.length > 0 && dot}
      <button type="button" className={link} disabled={busy} onClick={() => { void choose('new'); }}>
        {tf(t, 'chat.correction.new', 'a new kind')}
      </button>
      {dot}
      <button type="button" className={link} disabled={busy} onClick={() => { void choose('not_correction'); }}>
        {tf(t, 'chat.correction.notCorrection', 'not a correction')}
      </button>
      {failed && <span className="shrink-0 text-[var(--color-status-error)]">{tf(t, 'chat.correction.failed', 'Not saved, click again')}</span>}
    </div>
  );
}

/** The picker under one chat message: the row the backend recorded for it, if any, while
 *  the message is recent (older ones wait in the CDF panel's list). */
export function CorrectionPicker({ sessionId, clientId, sentAt }: {
  sessionId?: string; clientId?: string; sentAt?: string;
}) {
  const row = usePendingCorrection(sessionId, clientId, sentAt);
  if (!row || !sessionId) return null;
  return (
    <div className="mt-1 flex justify-end">
      <CorrectionChoices row={row} />
    </div>
  );
}
