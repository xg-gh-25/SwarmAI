/**
 * Correction picker: one line under a user message that stated a mistake, where the person
 * says which class the correction belongs to (design docs/design/2026-10-30-autonomy-tenets-gaps.md
 * §2). The classes are the ones the backend offered (an existing entry, a golden task, an
 * earlier correction), plus "a new kind" and "not a correction". One click records it and
 * the line goes away; ignoring it leaves the correction unconfirmed.
 *
 * Key export: `CorrectionPicker`.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import {
  correctionsService, forgetCorrection, usePendingCorrection,
} from '../../../services/corrections';

const MAX_TITLE = 48;

function short(title: string): string {
  return title.length > MAX_TITLE ? `${title.slice(0, MAX_TITLE - 1)}…` : title;
}

export function CorrectionPicker({ sessionId, clientId, sentAt }: {
  sessionId?: string; clientId?: string; sentAt?: string;
}) {
  const { t } = useTranslation();
  const row = usePendingCorrection(sessionId, clientId, sentAt);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!row || !sessionId) return null;

  const choose = async (choice: string) => {
    setBusy(true);
    setFailed(false);
    try {
      await correctionsService.confirm(row.id, choice);
      forgetCorrection(sessionId, row.id);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const link = 'underline decoration-dotted hover:text-[var(--color-text)] transition-colors disabled:opacity-50';
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-xs text-[var(--color-text-muted)] justify-end"
      role="group" aria-label={tf(t, 'chat.correction.label', 'Is this a correction?')}>
      <span>{tf(t, 'chat.correction.question', 'Correcting me? File it under:')}</span>
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
