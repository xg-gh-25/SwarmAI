/**
 * LiveDispatches — the real part of the Swarm Out dashboard (v0).
 *
 * Reads /api/swarm-out/runs: the tasks this Host dispatched to Hives, and the
 * questions those Hives are waiting on (mirrored to S3 by the Hive's bridge).
 * The decision this panel answers is "does a Hive need me?", so open questions come
 * first, each with its answer controls; dispatched runs follow as one line each.
 *
 * Everything a Hive wrote (question text, command, reason) is shown as plain text,
 * never rendered as markdown or HTML. Polls every 30 s while the dashboard is shown.
 * Design: docs/design/2026-10-27-swarm-out-v0-dispatch.md §3.4.
 */
import { useCallback, useEffect, useState } from 'react';
import { swarmOutService, type SwarmOutAsk, type SwarmOutQuestion, type SwarmOutRuns } from '../../services/swarmOut';

const POLL_MS = 30_000;
const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';

function when(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  return new Date(t).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function errText(e: unknown): string {
  const m = (e as { message?: string })?.message;
  return m && m.length < 200 ? m : '没能提交，稍后再试';
}

function AskCard({ ask, onDone }: { ask: SwarmOutAsk; onDone: () => void }) {
  // single-select / typed answers: the text; multi-select: the picked labels
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [multi, setMulti] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const send = async (body: { answers?: Record<string, string>; decision?: 'approve' | 'deny' }) => {
    setBusy(true);
    setError('');
    try {
      await swarmOutService.answer(ask, body);
      onDone();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const questions = ask.questions ?? [];
  const isMulti = (q: SwarmOutQuestion) => q.multiSelect && q.options.length > 0;
  // a multi-select answer is its picks joined with ", " in option order (as the chat's
  // AskUserQuestion); kept as a list until sent, so a label with a comma stays whole
  const answerOf = (q: SwarmOutQuestion) => (isMulti(q)
    ? q.options.map((o) => o.label).filter((l) => (multi[q.question] ?? []).includes(l)).join(', ')
    : (picks[q.question] ?? ''));
  const complete = questions.length > 0 && questions.every((q) => answerOf(q).trim());
  const toggle = (q: SwarmOutQuestion, label: string) => {
    if (!isMulti(q)) { setPicks((p) => ({ ...p, [q.question]: label })); return; }
    setMulti((m) => {
      const cur = m[q.question] ?? [];
      return { ...m, [q.question]: cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label] };
    });
  };
  const picked = (q: SwarmOutQuestion, label: string) =>
    isMulti(q) ? (multi[q.question] ?? []).includes(label) : picks[q.question] === label;
  const omitted = (ask.omitted?.questions ?? 0) + (ask.omitted?.options ?? 0);

  return (
    <div className={`${card} px-4 py-3`} data-testid="swarm-out-ask">
      <div className={`text-[12px] ${muted}`}>
        {ask.hive} · <span data-testid="swarm-out-ask-source">{ask.dispatch_title
          ?? (ask.possible_dispatch_title ? `可能属于：${ask.possible_dispatch_title}` : 'Hive 上的其他会话（不是这里派出的）')}</span> · {when(ask.asked_at)} · {ask.refused ? '没有送达' : '在等你'}
      </div>
      {ask.refused ? (
        <div className="mt-1 text-[12.5px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-refused">
          Hive 拒收了这次回答：它和正在等待的内容对不上（问题在显示之后被改过，或者这台 Hive 的版本更新）。回答没有送达，请到这台 Hive 自己的页面上处理。
        </div>
      ) : ask.kind === 'approval' ? (
        <>
          <div className="mt-1 font-medium">要执行一条需要批准的命令{ask.tool_name && <span className={`ml-1.5 font-mono text-[12px] ${muted}`} data-testid="swarm-out-ask-tool">{ask.tool_name}</span>}</div>
          {ask.reason && <div className={`mt-0.5 text-[12.5px] ${muted}`}>{ask.reason}</div>}
          <pre className="mt-2 mb-0 p-2 rounded-md bg-[var(--color-hover)] text-[12px] whitespace-pre-wrap break-all font-mono">{ask.command}</pre>
          {ask.truncated && (
            <div className="mt-1 text-[12px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-truncated">
              命令超过 2000 字，没有完整显示。不能在这里批准：去这台 Hive 自己的页面看全文再决定，或者在这里拒绝。
            </div>
          )}
          <div className="mt-2 flex gap-2">
            {!ask.truncated && <button type="button" className={btn} disabled={busy} onClick={() => send({ decision: 'approve' })}>批准</button>}
            <button type="button" className={btn} disabled={busy} onClick={() => send({ decision: 'deny' })}>拒绝</button>
          </div>
        </>
      ) : (
        <>
          {questions.map((q) => (
            <div key={q.question} className="mt-2">
              <div className="font-medium whitespace-pre-wrap">{q.question}</div>
              {q.options.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {q.options.map((o) => (
                    <button
                      key={o.label}
                      type="button"
                      title={o.description || undefined}
                      aria-pressed={picked(q, o.label)}
                      className={`${btn} ${picked(q, o.label) ? 'bg-[var(--color-hover)] font-medium' : ''}`}
                      onClick={() => toggle(q, o.label)}
                    >{o.label}</button>
                  ))}
                </div>
              )}
              {/* a multi-select question with options is answered by its picks only */}
              {!(q.multiSelect && q.options.length > 0) && (
                <input
                  aria-label={`回答：${q.question}`}
                  className="mt-1.5 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-[12.5px]"
                  placeholder={q.options.length ? '或者写你自己的回答' : '你的回答'}
                  value={q.options.some((o) => o.label === picks[q.question]) ? '' : (picks[q.question] ?? '')}
                  onChange={(e) => setPicks((p) => ({ ...p, [q.question]: e.target.value }))}
                />
              )}
            </div>
          ))}
          {omitted > 0 && (
            <div className={`mt-1.5 text-[12px] ${muted}`} data-testid="swarm-out-ask-omitted">
              还有 {omitted} 个问题或选项没有显示（太多或为空），完整内容在这台 Hive 自己的页面上。
            </div>
          )}
          <div className="mt-2">
            <button type="button" className={btn} disabled={busy || !complete}
              onClick={() => send({ answers: Object.fromEntries(questions.map((q) => [q.question, answerOf(q)])) })}>提交回答</button>
          </div>
        </>
      )}
      {error && <div className="mt-1.5 text-[12px] text-[var(--color-status-error)]" role="alert">{error}</div>}
    </div>
  );
}

export function LiveDispatches({ hidden = false }: { hidden?: boolean }) {
  const [data, setData] = useState<SwarmOutRuns | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await swarmOutService.runs());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    if (hidden) return undefined;
    void load();
    const id = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [hidden, load]);

  const asks = data?.asks ?? [];
  const runs = data?.runs ?? [];
  return (
    <section className="mb-4" data-testid="swarm-out-live">
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="m-0 text-[13px] font-semibold">真实派发</h3>
        <span className={`text-[12px] ${muted}`}>
          {asks.length ? `${asks.length} 个在等你` : '没有在等你的问题'} · 共 {runs.length} 个任务
        </span>
        <button type="button" className={`ml-auto ${btn}`} onClick={() => { void load(); }}>刷新</button>
      </div>
      {failed && <div className={`text-[12px] ${muted}`}>读不到派发记录，稍后自动重试</div>}
      {(data?.errors ?? []).map((e) => <div key={e} className="text-[12px] text-[var(--color-status-warning)]">{e}</div>)}
      <div className="flex flex-col gap-2">
        {asks.map((a) => <AskCard key={`${a.hive_id}/${a.request_id}`} ask={a} onDone={() => { void load(); }} />)}
      </div>
      {runs.length > 0 && (
        <div className={`${card} mt-2 divide-y divide-[var(--color-border)]`}>
          {runs.slice(0, 20).map((r) => (
            <div key={r.dispatch_id} className="px-4 py-2 flex items-baseline gap-2 min-w-0" data-testid="swarm-out-run">
              <span className="font-medium truncate min-w-0" title={r.title}>{r.title}</span>
              {r.waiting && <span className="text-[12px] text-[var(--color-status-warning)] flex-shrink-0">在等你</span>}
              {r.session_unknown && <span className={`text-[12px] flex-shrink-0 ${muted}`} data-testid="swarm-out-run-unknown">会话未确认，可能在跑</span>}
              <span className={`ml-auto text-[12px] flex-shrink-0 ${muted}`}>{r.hive} · {when(r.sent_at)}</span>
            </div>
          ))}
        </div>
      )}
      {data && runs.length === 0 && (
        <div className={`text-[12px] ${muted}`}>还没有派发过任务。在右边的对话里说要做什么，Swarm 会帮你派出去。</div>
      )}
    </section>
  );
}
