/**
 * LessonsAccept — the person takes a Hive run's lessons (design
 * docs/design/2026-10-29-hive-run-record-and-worker-role.md §7 D3).
 *
 * A Hive's lessons are kept on this machine and reach nothing until the person takes
 * them: "收下教训" sends them to this machine's judge (backend core/hive_lessons.accept),
 * which decides, unit by unit, what goes into the run's Brain. Shown only while some
 * are waiting. The accept names the units shown (their digests), so one that arrived
 * after the panel rendered is not taken with them; an accept that came back with an
 * error or still-waiting units keeps the button.
 *
 * @exports LessonsAccept
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { swarmOutService, type SwarmOutLessons } from '../../services/swarmOut';

const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';

export function LessonsAccept({ dispatchId, lessons, onDone }:
  { dispatchId: string; lessons: SwarmOutLessons | null | undefined; onDone?: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const waiting = lessons?.pending ?? 0;
  // more lessons arrived after the person took the last ones: the button comes back
  useEffect(() => { if (waiting > 0) setDone(false); }, [waiting]);
  if (!lessons || (!lessons.pending && !done)) return null;
  if (done) return <div className="mt-1 text-[12px]" data-testid="swarm-out-lessons-accepted">{t('swarmOut.lessons.accepted')}</div>;
  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await swarmOutService.acceptLessons(dispatchId, lessons?.digests ?? []);
      onDone?.();
      if (res?.error) setError(t('swarmOut.lessons.notQueued'));
      // done only when nothing the person was shown is still waiting (a failed queue or
      // a judge budget leaves some: the button stays)
      else if (!res || !res.pending) setDone(true);
    } catch (e) {
      const d = (e as { detail?: unknown })?.detail;
      setError(typeof d === 'string' ? d : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-1.5" data-testid="swarm-out-lessons">
      <button type="button" className={btn} disabled={busy} onClick={() => { void accept(); }}
        data-testid="swarm-out-lessons-accept">
        {busy ? t('swarmOut.lessons.accepting') : t('swarmOut.lessons.accept')}
      </button>
      {error && <div className="mt-1 text-[12px] text-[var(--color-status-error)]" data-testid="swarm-out-lessons-error">{error}</div>}
    </div>
  );
}
