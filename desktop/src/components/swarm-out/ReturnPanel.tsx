/**
 * ReturnPanel — a Hive run's commits back on this machine, reviewed before they land
 * (design docs/design/2026-10-28-project-agent-flow.md §7 ⑨).
 *
 * The decision it answers: do these changes go onto this machine's main branch?
 * "Fetch and review" takes the bundle back and checks it (backend core/hive_return.prepare;
 * nothing lands). The review shows the files with their line counts, the commits (their
 * subjects are the Hive's text, shown as data), and the files under the repository's
 * protected paths, which need their own tick. "Approve landing" lands exactly the tip
 * shown; "Reject" keeps it at its refs/hive/ ref. Building and testing the change is the
 * repository's own step, after this.
 *
 * @exports ReturnPanel
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { swarmOutService, type SwarmOutReturn } from '../../services/swarmOut';

const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';

function errText(e: unknown): string {
  const d = (e as { detail?: unknown })?.detail;
  return typeof d === 'string' ? d : e instanceof Error ? e.message : String(e);
}

export function ReturnPanel({ dispatchId, url }: { dispatchId: string; url: string }) {
  const { t } = useTranslation();
  const [rev, setRev] = useState<SwarmOutReturn | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  const prepare = () => run(async () => { setRev(await swarmOutService.returnPrepare(dispatchId, url)); setTicked(false); });
  const land = () => run(async () => {
    const out = await swarmOutService.returnLand(dispatchId, rev!.key, rev!.tip, ticked);
    setDone(out.landed ? t('swarmOut.return.landed', { into: out.into ?? 'main', head: out.head ?? '' }) : t('swarmOut.return.nothing'));
  });
  const reject = () => run(async () => {
    await swarmOutService.returnReject(dispatchId, rev!.key, rev!.tip);
    setDone(t('swarmOut.return.rejected', { ref: rev!.ref }));
  });

  if (done) return <div className="mt-1 text-[12px]" data-testid="swarm-out-return-done">{done}</div>;
  const protectedN = rev?.protected_files.length ?? 0;
  return (
    <div className="mt-1.5" data-testid="swarm-out-return">
      {!rev ? (
        <button type="button" className={btn} disabled={busy} onClick={() => { void prepare(); }}
          data-testid="swarm-out-return-prepare">{busy ? t('swarmOut.return.fetching') : t('swarmOut.return.prepare')}</button>
      ) : (
        <div className="rounded-md border border-[var(--color-border)] p-2" data-testid="swarm-out-return-review">
          <div className="text-[12.5px] font-medium">
            {t('swarmOut.return.summary', { commits: t('swarmOut.work.commits', { count: rev.commits.length }),
              files: t('swarmOut.work.files', { count: rev.files_total }), branch: rev.branch.replace('refs/heads/', '') })}
          </div>
          {rev.checkout_dirty && (
            <div className={`mt-1 text-[12px] text-[var(--color-status-warning)]`} data-testid="swarm-out-return-dirty">
              {t('swarmOut.return.dirty')}
            </div>
          )}
          {protectedN > 0 && (
            <div className="mt-1.5 text-[12px] text-[var(--color-status-error)]" data-testid="swarm-out-return-protected">
              {t('swarmOut.return.protected', { count: protectedN })}
              {rev.protected_files.map((p) => <div key={p} className="font-mono text-[11.5px]">{p}</div>)}
              <label className="mt-1 flex items-center gap-1.5 text-[12px] text-[var(--color-text)]">
                <input type="checkbox" checked={ticked} onChange={(e) => setTicked(e.target.checked)}
                  data-testid="swarm-out-return-tick" />
                {t('swarmOut.return.tick')}
              </label>
            </div>
          )}
          <div className="mt-1.5 max-h-[200px] overflow-auto">
            {rev.files.map((f) => (
              <div key={f.path} className={`font-mono text-[11.5px] truncate ${f.protected ? 'text-[var(--color-status-error)]' : ''}`} title={f.path}>
                {f.path} <span className={muted}>+{f.added ?? '?'} −{f.deleted ?? '?'}</span>
              </div>
            ))}
            {rev.files_total > rev.files.length && <div className={`text-[11.5px] ${muted}`}>{t('swarmOut.detail.moreFiles', { count: rev.files_total - rev.files.length })}</div>}
          </div>
          <div className={`mt-1.5 text-[11.5px] ${muted}`}>{t('swarmOut.return.commitsNote')}</div>
          {rev.commits.map((c) => (
            <div key={c.sha} className="text-[11.5px] truncate" title={c.subject}>
              <span className="font-mono">{c.sha.slice(0, 9)}</span> {c.subject}
            </div>
          ))}
          <div className={`mt-1.5 text-[11.5px] ${muted}`}>
            {t('swarmOut.return.fullDiff')}<span className="font-mono select-all">git diff {rev.base.slice(0, 12)} {rev.ref}</span>
          </div>
          <div className="mt-2 flex gap-2">
            <button type="button" className={btn} disabled={busy || (protectedN > 0 && !ticked)} onClick={() => { void land(); }}
              data-testid="swarm-out-return-land">{t('swarmOut.return.land')}</button>
            <button type="button" className={btn} disabled={busy} onClick={() => { void reject(); }}
              data-testid="swarm-out-return-reject">{t('swarmOut.return.reject')}</button>
          </div>
        </div>
      )}
      {error && (
        <div className="mt-1 text-[12px] text-[var(--color-status-error)]" data-testid="swarm-out-return-error">
          {error}
          {rev && (
            <button type="button" className={`ml-2 ${btn}`} disabled={busy} onClick={() => { setRev(null); void prepare(); }}
              data-testid="swarm-out-return-retake">{t('swarmOut.return.retake')}</button>
          )}
        </div>
      )}
    </div>
  );
}
