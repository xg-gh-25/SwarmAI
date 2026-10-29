/**
 * LiveDispatches — the run view of the Swarm Out panel, on real data only
 * (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * It answers three things, in this order: does a Hive need me (the questions waiting,
 * each with its answer controls — the one thing in the accent colour), can I send
 * more work (the fleet and its free slots), and how are the runs I sent (running,
 * stopped in the last 24 h, state unknown). What has no data source yet says TBD and
 * which source is missing; nothing on this view is a sample.
 *
 * A run that is not running any more shows what its Hive recorded (design
 * docs/design/2026-10-28-swarm-out-run-results.md): how it ended, what it did, its
 * numbers; the details (the reply, code per repository, files, steps, a pipeline's
 * five rows) open on demand.
 *
 * Everything a Hive wrote (question text, command, reason, the reply, paths) is shown
 * as plain text, never rendered as markdown or HTML. Polls /api/swarm-out/runs every 30 s while shown;
 * the fleet comes from the panel (one poll of /api/swarm-out/fleet).
 */
import { useCallback, useEffect, useState } from 'react';
import { swarmOutService, type SwarmOutAsk, type SwarmOutFleet, type SwarmOutQuestion, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { JudgeTag } from './JudgeTag';
import { ReturnPanel } from './ReturnPanel';
import { brainLabel, checksLine, endedLabel, errorLabel, gateLine, hiveStatusLabel, numberParts, panelNumbers, resultErrorLabel, runGroups, runJudge, runsByBrain, stateLabel, useRuns, workLines } from './live';

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

/** A part with no data source yet: its title and what is missing, demoted. */
export function Tbd({ title, missing, testId }: { title: string; missing: string; testId?: string }) {
  return (
    <section className="mt-5" data-testid={testId ?? 'swarm-out-tbd'}>
      <h3 className={`m-0 text-[13px] font-semibold ${muted}`}>{title}</h3>
      <div className="mt-1 text-[12px] text-[var(--color-text-dim)]">TBD：{missing}</div>
    </section>
  );
}

function Num({ value, label, accent, testId }: { value: React.ReactNode; label: string; accent?: boolean; testId: string }) {
  return (
    <div className={`${card} px-4 py-3`} data-testid={testId}>
      <div className={`text-[24px] font-semibold leading-none tabular-nums ${accent ? 'text-[var(--color-status-warning)]' : ''}`}>{value}</div>
      <div className={`text-[12px] mt-1.5 ${muted}`}>{label}</div>
    </div>
  );
}

function Fleet({ fleet, failed }: { fleet: SwarmOutFleet | null; failed?: boolean }) {
  const hives = fleet?.hives ?? [];
  return (
    <section className={`${card} mt-5`} data-testid="swarm-out-fleet">
      <div className="flex items-baseline gap-2 px-4 pt-3 pb-1">
        <h3 className="m-0 text-[13px] font-semibold">机群</h3>
        <span className={`ml-auto text-[12px] ${muted}`}>占用 = Hive 上所有在跑的会话，包括它自己的</span>
      </div>
      {failed && <div className={`px-4 text-[12px] ${muted}`} data-testid="swarm-out-fleet-failed">读不到机群，稍后自动重试</div>}
      {fleet === null ? (!failed && <div className={`px-4 pb-3 text-[12px] ${muted}`}>正在读…</div>) : (
        <div className="grid grid-cols-[minmax(80px,180px)_minmax(0,1fr)_minmax(0,max-content)] items-center gap-x-3 gap-y-2 px-4 pt-1.5 pb-3">
          {hives.map((h) => {
            const up = h.status === 'running' && h.reachable && h.max_concurrent != null;
            const status = h.status !== 'running' ? hiveStatusLabel(h.status)
              : !h.reachable ? '连不上' : h.saturated ? `忙满 · 占用 ${h.streaming}/${h.max_concurrent}` : `占用 ${h.streaming}/${h.max_concurrent}`;
            const filled = h.saturated ? (h.max_concurrent ?? 0) : (h.streaming ?? 0);
            return (
              <div key={h.name} className="contents" data-testid="swarm-out-fleet-hive">
                <div className="font-medium min-w-0 truncate" title={h.name}>{h.name}
                  <div className={`text-[11.5px] font-normal truncate ${muted}`}>{[h.version, h.instance_type].filter(Boolean).join(' · ')}</div>
                </div>
                <div className="flex gap-[4px] min-w-0" style={{ opacity: up ? 1 : 0.4 }}>
                  {up && Array.from({ length: Math.min(h.max_concurrent ?? 0, 32) }, (_, i) => (
                    <span key={i} className={`flex-1 min-w-[8px] max-w-[26px] h-[12px] rounded-[3px] border ${i < filled ? 'bg-[var(--color-status-success)] border-[var(--color-status-success)]' : 'bg-[var(--color-hover)] border-[var(--color-border)]'}`} />
                  ))}
                </div>
                <div className={`text-[12px] text-right truncate min-w-0 ${h.status === 'running' && !h.reachable ? 'text-[var(--color-status-warning)]' : muted}`} title={status} data-testid="swarm-out-fleet-status">{status}</div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

const TONE = { error: 'text-[var(--color-status-error)]', normal: '', muted: muted } as const;

function ResultDetail({ r, onRefreshed }: { r: SwarmOutRun; onRefreshed: () => void }) {
  const [full, setFull] = useState<SwarmOutResult | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('loading');
  const id = r.dispatch_id;
  const load = useCallback(async () => {
    setState('loading');
    try {
      const rec = await swarmOutService.result(id);
      setFull(rec.record?.result ?? null);
      setState('idle');
    } catch {
      setState('failed');
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);   // once, when the details open
  const refresh = async () => {
    setState('loading');
    try {
      await swarmOutService.refresh(r.dispatch_id);
      onRefreshed();
      await load();
    } catch {
      setState('failed');
    }
  };
  // the details come from the whole record only: /runs carries counts, not the
  // Hive-written text and paths shown here
  const res = full;
  const judge = res ? runJudge(res) : null;
  const tools = Object.entries(res?.work.steps.by_tool ?? {});
  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)] text-[12.5px]" data-testid="swarm-out-run-detail">
      {state === 'loading' && <div className={muted}>正在读…</div>}
      {state === 'failed' && <div className={muted}>读不到详情，稍后再试</div>}
      {judge && <div className="mb-2"><JudgeTag judge={judge} title={r.title} /></div>}
      {res?.error?.message && <div className="mb-2 text-[var(--color-status-error)] whitespace-pre-wrap">{res.error.message}</div>}
      {full?.report ? (
        <>
          <div className={`text-[11.5px] ${muted}`}>最后的回复</div>
          <pre className="mt-1 mb-0 p-2 rounded-md bg-[var(--color-hover)] text-[12px] whitespace-pre-wrap break-words font-sans max-h-[320px] overflow-auto" data-testid="swarm-out-run-report">{full.report}</pre>
          {full.report_chars > full.report.length && <div className={`mt-1 text-[11.5px] ${muted}`}>全文 {full.report_chars} 字，去 Hive 页面看</div>}
        </>
      ) : full && <div className={muted}>没有回复文字</div>}
      {(res?.work.repos ?? []).filter((x) => x.files.length || x.commits.length).map((x) => (
        <div key={x.dir ?? x.url ?? ''} className="mt-2" data-testid="swarm-out-run-repo">
          <div className={`text-[11.5px] ${muted}`}>{x.dir}{x.ref ? ` @ ${x.ref}` : ''}（只统计 commit，未提交的改动没读）</div>
          {x.files.map((f, i) => <div key={`${i}:${f.path}`} className="font-mono text-[11.5px] truncate" title={f.path}>{f.path} <span className={muted}>+{f.added ?? '?'} −{f.deleted ?? '?'}</span></div>)}
          {x.files_total > x.files.length && <div className={`text-[11.5px] ${muted}`}>还有 {x.files_total - x.files.length} 个文件</div>}
          {x.commits.length > 0 && <div className={`font-mono text-[11.5px] ${muted}`}>{x.commits.map((c) => c.slice(0, 9)).join(' ')}{x.commits_total > x.commits.length ? ` …共 ${x.commits_total} 个` : ''}</div>}
          {r.project_agent?.delivery === 'bundle' && x.url && x.bundle?.sha256 && <ReturnPanel dispatchId={r.dispatch_id} url={x.url} />}
          {x.bundle?.error && <div className={`text-[11.5px] ${muted}`} data-testid="swarm-out-return-unavailable">取不回这些 commit：{x.bundle.error}</div>}
        </div>
      ))}
      {(res?.work.files ?? []).length > 0 && (
        <div className="mt-2" data-testid="swarm-out-run-files">
          <div className={`text-[11.5px] ${muted}`}>写了的文件（Hive 上的路径）</div>
          {res!.work.files.map((f, i) => <div key={`${i}:${f.path ?? ''}`} className="font-mono text-[11.5px] truncate" title={f.path}>{f.path}</div>)}
        </div>
      )}
      {tools.length > 0 && (
        <div className={`mt-2 text-[11.5px] ${muted}`} data-testid="swarm-out-run-steps">
          {res!.work.steps.total} 次工具调用：{tools.map(([n, c]) => `${n} ${c}`).join(' · ')}
        </div>
      )}
      <div className="mt-2"><button type="button" className={btn} disabled={state === 'loading'} onClick={() => { void refresh(); }}>刷新结果</button></div>
    </div>
  );
}

function RunResult({ r }: { r: SwarmOutRun }) {
  const res = r.result;
  if (!res) {
    if (r.result_status === 'pending') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">正在从 Hive 取回结果</div>;
    if (r.result_status === 'failed') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">取不回结果：{resultErrorLabel(r.result_error)}</div>;
    return null;
  }
  const ended = endedLabel(res);
  const work = workLines(res);
  return (
    <div className="mt-1.5" data-testid="swarm-out-run-result">
      <div className={`text-[12.5px] font-medium ${TONE[ended.tone]}`} data-testid="swarm-out-run-ended">{ended.text}</div>
      {work.map((w) => <div key={w} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-work">{w}</div>)}
      {[checksLine(res), gateLine(res)].filter(Boolean).map((l) => (
        <div key={l!} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-checks">{l}</div>
      ))}
      <div className={`text-[11.5px] mt-0.5 ${muted}`} data-testid="swarm-out-run-numbers">{numberParts(res).join(' · ')}</div>
      {r.result_error && <div className={`text-[11.5px] mt-0.5 ${muted}`}>上次检查：{resultErrorLabel(r.result_error)}</div>}
    </div>
  );
}

function RunCard({ r, onReload }: { r: SwarmOutRun; onReload?: () => void }) {
  const [open, setOpen] = useState(false);
  const canOpen = !!r.result || r.result_status === 'failed';
  return (
    <div className={`${card} px-3.5 py-3 min-w-0`} data-testid="swarm-out-run" data-state={r.state ?? 'unknown'}>
      <div className="flex items-start gap-2 min-w-0">
        <span className="font-medium min-w-0 flex-1 line-clamp-2 leading-[18px]" title={r.title}>{r.title}</span>
        <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.sent_at)}</span>
      </div>
      <div className={`text-[11.5px] mt-0.5 truncate ${muted}`}>
        {r.hive} · <span data-testid="swarm-out-run-brain" title={r.project_agent?.acceptance ? `验收：${r.project_agent.acceptance}` : undefined}>{brainLabel(r)}{r.project_agent?.mode ? ` / ${r.project_agent.mode}` : ''}</span> · <span data-testid={r.session_unknown ? 'swarm-out-run-unknown' : 'swarm-out-run-state'}>{stateLabel(r)}</span>
      </div>
      <RunResult r={r} />
      {canOpen && (
        <button type="button" className={`mt-1.5 text-[12px] ${muted} hover:text-[var(--color-text)]`} aria-expanded={open}
          onClick={() => setOpen((o) => !o)} data-testid="swarm-out-run-toggle">{open ? '收起' : '详情'}</button>
      )}
      {open && <ResultDetail r={r} onRefreshed={onReload ?? (() => {})} />}
    </div>
  );
}

const runGrid = { gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 300px), 1fr))' };

function RunGroup({ title, rows, empty, onReload }: { title: string; rows: SwarmOutRun[]; empty: string; onReload?: () => void }) {
  return (
    <section className="mt-5" data-testid="swarm-out-run-group" data-group={title}>
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
        {rows.length > 0 && <span className="text-[12px] text-[var(--color-text-dim)] tabular-nums">{rows.length}</span>}
      </div>
      {rows.length === 0 ? <div className={`text-[12.5px] ${muted}`}>{empty}</div> : (
        <div className="grid gap-3" style={runGrid}>
          {rows.map((r) => <RunCard key={r.dispatch_id} r={r} onReload={onReload} />)}
        </div>
      )}
    </section>
  );
}

/** The last 7 days by the Brain each run went under: how its work goes there. */
function ByBrain({ runs, now }: { runs: SwarmOutRun[]; now: number }) {
  const rows = runsByBrain(runs, now);
  if (rows.length === 0) return null;
  return (
    <section className="mt-5" data-testid="swarm-out-by-brain">
      <h3 className="m-0 mb-2 text-[13px] font-semibold">按大脑（7 天）</h3>
      <div className="flex flex-col gap-1">
        {rows.map((b) => (
          <div key={b.brain || '-'} className="flex items-baseline gap-3 text-[12.5px]" data-testid="swarm-out-brain-row" data-brain={b.brain}>
            <span className="w-44 shrink-0 truncate font-medium" title={b.label}>{b.label}</span>
            <span className="tabular-nums">{b.runs} 个</span>
            <span className={`tabular-nums ${muted}`}>{b.completed} 做完{b.failed ? ` · ${b.failed} 出错` : ''}</span>
            {b.checksNotRun > 0 && <span className="text-[var(--color-status-warning)] tabular-nums">{b.checksNotRun} 个校验没跑或报错</span>}
          </div>
        ))}
      </div>
    </section>
  );
}

function OpenHive({ onOpenHive }: { onOpenHive?: () => void }) {
  return onOpenHive ? <button type="button" className={`ml-2 ${btn}`} onClick={onOpenHive} data-testid="swarm-out-open-hive-admin">打开 Hive 管理</button> : null;
}

export function LiveDispatches({ hidden = false, fleet = null, fleetFailed = false, onOpenHive, now }: {
  hidden?: boolean; fleet?: SwarmOutFleet | null; fleetFailed?: boolean; onOpenHive?: () => void; now?: number;
}) {
  const { data, failed, reload } = useRuns(hidden);
  const at = now ?? Date.now();
  const asks = data?.asks ?? [];
  const open = asks.filter((a) => !a.refused);
  const refused = asks.filter((a) => a.refused);
  const n = panelNumbers(data, fleet);
  const groups = runGroups(data?.runs ?? [], at, asks);
  const noRuns = !!data && data.runs.length === 0;
  const dash = '—';
  return (
    <div data-testid="swarm-out-live">
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>读不到派发记录，稍后自动重试</div>}
      {(data?.errors ?? []).map((e) => <div key={e} className="text-[12px] mb-1 text-[var(--color-status-warning)]">{errorLabel(e)}</div>)}
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }} data-testid="kpi-grid">
        <Num testId="kpi-waiting" label="等你回答" accent={!!n.waiting || !!n.onHivePage}
             value={<>{n.waiting ?? dash}{n.onHivePage ? <span className="text-[12.5px] font-medium ml-2" data-testid="kpi-on-hive-page">+{n.onHivePage} 个要去 Hive 页面处理</span> : null}</>} />
        <Num testId="kpi-free" label="空位，可以马上派"
             value={n.capacity ? <>{n.capacity.free}<span className={`text-[13px] font-medium ml-1 ${muted}`}>/ {n.capacity.total}</span></> : dash} />
        <Num testId="kpi-running" label="我派出的，在跑" value={n.running ?? dash} />
      </div>

      <section className="mt-5" data-testid="swarm-out-waiting">
        <div className="flex items-baseline gap-2 mb-2">
          <h3 className="m-0 text-[13px] font-semibold">等你</h3>
          <button type="button" className={`ml-auto ${btn}`} onClick={reload}>刷新</button>
        </div>
        {data && open.length === 0 && groups.waitingUnasked.length === 0 && <div className={`text-[12.5px] ${muted}`}>没有在等你的问题</div>}
        <div className="flex flex-col gap-2">
          {open.map((a) => <AskCard key={`${a.hive_id}/${a.request_id}`} ask={a} onDone={reload} />)}
        </div>
        {groups.waitingUnasked.length > 0 && (
          <div className="mt-2" data-testid="swarm-out-waiting-unasked">
            <div className={`text-[12px] mb-1.5 ${muted}`}>Hive 说这些在等人，但问题还没传到这里：去 Hive 页面处理<OpenHive onOpenHive={onOpenHive} /></div>
            <div className="grid gap-3" style={runGrid}>
              {groups.waitingUnasked.map((r) => <RunCard key={r.dispatch_id} r={r} />)}
            </div>
          </div>
        )}
        {refused.length > 0 && (
          <div className="mt-3" data-testid="swarm-out-refused-group">
            <div className={`text-[12px] mb-1.5 ${muted}`}>要去 Hive 页面处理（回答没有送达，这里不能再回答）<OpenHive onOpenHive={onOpenHive} /></div>
            <div className="flex flex-col gap-2">
              {refused.map((a) => <AskCard key={`${a.hive_id}/${a.request_id}`} ask={a} onDone={reload} />)}
            </div>
          </div>
        )}
      </section>

      <Fleet fleet={fleet} failed={fleetFailed} />

      {noRuns ? (
        <div className={`mt-5 text-[12px] ${muted}`}>还没有派发过任务。在右边的对话里说要做什么，Swarm 会帮你派出去。</div>
      ) : (
        <>
          <RunGroup title="在跑" rows={groups.running} empty="没有在跑的任务" />
          <RunGroup title="结束了（24 小时内派出的）" rows={groups.stopped} empty="24 小时内派出的都还在跑，或在等你" onReload={reload} />
          {groups.unknown.length > 0 && <RunGroup title="状态未知" rows={groups.unknown} empty="" />}
        </>
      )}
      {data && data.runs.length > 0 && <ByBrain runs={data.runs} now={at} />}
      <Tbd title="已安排" missing="还没有定时派发" />
    </div>
  );
}
