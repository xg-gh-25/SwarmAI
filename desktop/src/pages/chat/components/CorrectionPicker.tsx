/**
 * Correction picker: one line under a user message that stated a mistake, where the person
 * says which class the correction belongs to (design docs/design/2026-10-30-autonomy-tenets-gaps.md
 * §2). The classes are the ones the backend offered (an existing entry, a golden task, an
 * earlier correction), plus "a new kind" and "not a correction". One click records it and
 * the line goes away; ignoring it leaves the correction unconfirmed.
 *
 * Key exports: `CorrectionPicker` (under a chat message), `CorrectionChoices` (the choices
 * for one correction, also used by the CDF panel's waiting list).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import {
  correctionsService, forgetCorrection, usePendingCorrection, type PendingCorrection,
} from '../../../services/corrections';

const MAX_TITLE = 48;

function short(title: string): string {
  return title.length > MAX_TITLE ? `${title.slice(0, MAX_TITLE - 1)}…` : title;
}

/** The choices for one correction: its offered classes, "a new kind", "not a correction".
 *  One click records the person's choice; `onChosen` runs once it is saved. */
export function CorrectionChoices({ row, onChosen, showQuestion = true }: {
  row: PendingCorrection; onChosen: () => void; showQuestion?: boolean;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const choose = async (choice: string) => {
    setBusy(true);
    setFailed(false);
    try {
      await correctionsService.confirm(row.id, choice);
      onChosen();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const link = 'underline decoration-dotted hover:text-[var(--color-text)] transition-colors disabled:opacity-50';
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[var(--color-text-muted)]"
      role="group" aria-label={tf(t, 'chat.correction.label', 'Is this a correction?')}>
      {showQuestion && <span>{tf(t, 'chat.correction.question', 'Correcting me? File it under:')}</span>}
      {row.candidates.map((c) => (
        <button key={c.key} type="button" className={link} disabled={busy} title={c.title}
          onClick={() => { void choose(c.key); }}>
          {short(c.title)}
        </button>
      ))}
      <button type="button" className={link} disabled={busy} onClick={() => { void choose('new'); }}>
        {tf(t, 'chat.correction.new', 'a new kind')}
      </button>
      <button type="button" className={link} disabled={busy} onClick={() => { void choose('not_correction'); }}>
        {tf(t, 'chat.correction.notCorrection', 'not a correction')}
      </button>
      {failed && <span className="text-[var(--color-status-error)]">{tf(t, 'chat.correction.failed', 'Not saved')}</span>}
    </div>
  );
}

/** The picker under one chat message: the row the backend recorded for it, if any. */
export function CorrectionPicker({ sessionId, clientId, sentAt }: {
  sessionId?: string; clientId?: string; sentAt?: string;
}) {
  const row = usePendingCorrection(sessionId, clientId, sentAt);
  if (!row || !sessionId) return null;
  return (
    <div className="mt-1 flex justify-end">
      <CorrectionChoices row={row} onChosen={() => forgetCorrection(sessionId, row.id)} />
    </div>
  );
}
