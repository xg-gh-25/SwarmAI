/**
 * ReturnPanel — a Hive run's commits back on this machine, reviewed before they land
 * (design docs/design/2026-10-28-project-agent-flow.md §7 ⑨).
 *
 * The decision it answers: do these changes go onto this machine's main branch?
 * "取回并审阅" takes the bundle back and checks it (backend core/hive_return.prepare;
 * nothing lands). The review shows the files with their line counts, the commits (their
 * subjects are the Hive's text, shown as data), and the files under the repository's
 * protected paths, which need their own tick. "批准落地" lands exactly the tip shown;
 * "不要" keeps it at its refs/hive/ ref. Building and testing the change is the
 * repository's own step, after this.
 *
 * @exports ReturnPanel
 */
import { useState } from 'react';
import { swarmOutService, type SwarmOutReturn } from '../../services/swarmOut';

const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';

function errText(e: unknown): string {
  const d = (e as { detail?: unknown })?.detail;
  return typeof d === 'string' ? d : e instanceof Error ? e.message : String(e);
}

export function ReturnPanel({ dispatchId, url }: { dispatchId: string; url: string }) {
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
    setDone(out.landed ? `已落到 ${out.into ?? 'main'}（${out.head ?? ''}）。接下来按这个仓库自己的规则 build、测试。`
      : '没有可落地的改动');
  });
  const reject = () => run(async () => {
    await swarmOutService.returnReject(dispatchId, rev!.key, rev!.tip);
    setDone(`没有落地，改动留在 ${rev!.ref}`);
  });

  if (done) return <div className="mt-1 text-[12px]" data-testid="swarm-out-return-done">{done}</div>;
  const protectedN = rev?.protected_files.length ?? 0;
  return (
    <div className="mt-1.5" data-testid="swarm-out-return">
      {!rev ? (
        <button type="button" className={btn} disabled={busy} onClick={() => { void prepare(); }}
          data-testid="swarm-out-return-prepare">{busy ? '正在取回…' : '取回改动并审阅'}</button>
      ) : (
        <div className="rounded-md border border-[var(--color-border)] p-2" data-testid="swarm-out-return-review">
          <div className="text-[12.5px] font-medium">
            {rev.commits.length} 个 commit，{rev.files_total} 个文件，落到 {rev.branch.replace('refs/heads/', '')}
          </div>
          {rev.checkout_dirty && (
            <div className={`mt-1 text-[12px] text-[var(--color-status-warning)]`} data-testid="swarm-out-return-dirty">
              本机这个仓库有未提交的改动；改到同样的文件时，落地会被拒绝
            </div>
          )}
          {protectedN > 0 && (
            <div className="mt-1.5 text-[12px] text-[var(--color-status-error)]" data-testid="swarm-out-return-protected">
              改到了 {protectedN} 个受保护的路径（会在本机构建、执行的代码，或 agent 自己的规则），要单独确认：
              {rev.protected_files.map((p) => <div key={p} className="font-mono text-[11.5px]">{p}</div>)}
              <label className="mt-1 flex items-center gap-1.5 text-[12px] text-[var(--color-text)]">
                <input type="checkbox" checked={ticked} onChange={(e) => setTicked(e.target.checked)}
                  data-testid="swarm-out-return-tick" />
                我看过这些路径的改动
              </label>
            </div>
          )}
          <div className="mt-1.5 max-h-[200px] overflow-auto">
            {rev.files.map((f) => (
              <div key={f.path} className={`font-mono text-[11.5px] truncate ${f.protected ? 'text-[var(--color-status-error)]' : ''}`} title={f.path}>
                {f.path} <span className={muted}>+{f.added ?? '?'} −{f.deleted ?? '?'}</span>
              </div>
            ))}
            {rev.files_total > rev.files.length && <div className={`text-[11.5px] ${muted}`}>还有 {rev.files_total - rev.files.length} 个文件</div>}
          </div>
          <div className={`mt-1.5 text-[11.5px] ${muted}`}>commit（Hive 写的说明，只作参考）</div>
          {rev.commits.map((c) => (
            <div key={c.sha} className="text-[11.5px] truncate" title={c.subject}>
              <span className="font-mono">{c.sha.slice(0, 9)}</span> {c.subject}
            </div>
          ))}
          <div className={`mt-1.5 text-[11.5px] ${muted}`}>
            看完整 diff：<span className="font-mono select-all">git diff {rev.base.slice(0, 12)} {rev.ref}</span>
          </div>
          <div className="mt-2 flex gap-2">
            <button type="button" className={btn} disabled={busy || (protectedN > 0 && !ticked)} onClick={() => { void land(); }}
              data-testid="swarm-out-return-land">批准落地</button>
            <button type="button" className={btn} disabled={busy} onClick={() => { void reject(); }}
              data-testid="swarm-out-return-reject">不要</button>
          </div>
        </div>
      )}
      {error && (
        <div className="mt-1 text-[12px] text-[var(--color-status-error)]" data-testid="swarm-out-return-error">
          {error}
          {rev && (
            <button type="button" className={`ml-2 ${btn}`} disabled={busy} onClick={() => { setRev(null); void prepare(); }}
              data-testid="swarm-out-return-retake">重新取回</button>
          )}
        </div>
      )}
    </div>
  );
}
