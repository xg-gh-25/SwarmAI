/**
 * Cases — the golden set with each case's latest result, and the run history.
 *
 * The list answers "what is not passing": the cases whose latest result is not a
 * pass come first, each with the reason in one line; the passing ones are folded by
 * category. A case opens on the right: what it protects, its latest result, and for
 * an offline case what it was asked, what it did and its answer (the drill-down),
 * then how it is checked. Run history lists the quick checks by day and every full
 * run's release checks with its report. The user's own cases are added, edited,
 * deleted and tried here; product cases ship with SwarmAI and are read-only.
 *
 * Saving runs the backend's full admission (POST /golden-set/try is the same check
 * without saving), so the form lists the checks and shows each one's result.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { getApiBaseUrl } from '../../services/tauri';
import { openExternal } from '../../utils/openExternal';
import {
  CASE_DIMENSIONS, errorDetail, useCaseDetail, useDeleteCase, useEvalHealth, useEvalHistory, useGoldenSet, useRunCases,
  useRunDetail, useRunFull, useRunProgress, useRunQuick, useSaveCase, useTryCase,
  type AdmissionResult, type CaseDetail, type CaseInput, type EvalRun, type GoldenCase, type QuickDay, type ReleaseTier,
} from './api';
import { checkById, notPassing } from './derive';
import {
  Btn, CATEGORY_LABEL, Code, ErrorState, H4, KV, LinkBtn, Loading, Seg, SidePanel, TIER_LABEL, TierText,
  caseName, categoryName, dimName, isQuick, judgeReason, pick, splitPurpose, statusLabel, useEvalActions, useGo, useLang, when,
} from './ui';

type Line = 'all' | 'quick' | 'offline';
type View = 'cases' | 'history';
type Panel = { kind: 'case'; id: string } | { kind: 'form'; type: 'quick' | 'offline'; from?: CaseDetail; editing?: boolean } | null;

const tierOf = (c: GoldenCase): ReleaseTier => c.release_tier ?? 'watch';
const statusColor = (s?: string) => (s === 'passed' ? 'var(--color-status-success)'
  : s === 'error' || s === 'timeout' ? 'var(--color-status-error)' : 'var(--color-status-warning)');

export function CasesTab({ focus }: { focus?: string }) {
  const { lang, t } = useLang();
  const gs = useGoldenSet();
  const [view, setView] = useState<View>(focus === 'history' ? 'history' : 'cases');
  const [line, setLine] = useState<Line>('all');
  const [q, setQ] = useState('');
  const [panel, setPanel] = useState<Panel>(focus?.startsWith('case:') ? { kind: 'case', id: focus.slice(5) } : null);
  useEffect(() => {
    if (focus === 'history') setView('history');
    if (focus?.startsWith('case:')) { setView('cases'); setPanel({ kind: 'case', id: focus.slice(5) }); }
  }, [focus]);

  const live = useMemo(() => (gs.data?.cases ?? []).filter((c) => c.tier !== 'archived'), [gs.data]);
  if (gs.isError) return <ErrorState />;
  if (gs.isLoading) return <Loading />;
  const product = live.filter((c) => c._origin === 'product');
  const mine = live.filter((c) => c._origin !== 'product');
  const blocking = live.filter((c) => tierOf(c) !== 'watch').length;
  const bad = live.filter(notPassing);
  const neverRun = live.filter((c) => !c.last_result);
  const match = (c: GoldenCase) =>
    (line === 'all' || (line === 'quick') === isQuick(c))
    && (!q || `${caseName(c, lang)} ${pick(c.purpose, lang)} ${c.title ?? ''} ${c.id} ${TIER_LABEL[tierOf(c)][lang]}`.toLowerCase().includes(q.toLowerCase()));
  const open = (c: GoldenCase) => setPanel({ kind: 'case', id: c.id });

  return (
    <div className="max-w-[880px] mx-auto px-7 pt-10 pb-20" data-testid="eval-cases">
      <div className="flex items-end gap-4">
        <div className="flex-1">
          <div className="text-display font-semibold text-[var(--color-text)] tabular-nums" data-testid="cases-head">
            {t(`${live.length} cases`, `${live.length} 条 case`)}
            <span className="text-body font-normal ml-2" style={{ color: bad.length ? 'var(--color-status-warning)' : 'var(--color-text-muted)' }}>
              · {bad.length ? t(`${bad.length} not passing`, `${bad.length} 条没通过`)
                : live.length === 0 ? t('none yet', '还没有')
                  : neverRun.length ? t(`${neverRun.length} not run yet`, `${neverRun.length} 条还没跑`) : t('all passing', '全部通过')}
            </span>
          </div>
          <div className="text-small text-[var(--color-text-muted)] mt-1" data-testid="tier-summary">
            {t(`${blocking} block a release, ${live.length - blocking} are watched. ${product.length} ship with SwarmAI; ${mine.length} of your own.`,
              `${blocking} 条拦上线，${live.length - blocking} 条只观测。${product.length} 条随 SwarmAI 发布，你自己的 ${mine.length} 条。`)}
          </div>
        </div>
        <RunMenu />
        <Btn kind="primary" testId="add-case" onClick={() => setPanel({ kind: 'form', type: 'offline' })}>{t('Add a case', '新增 case')}</Btn>
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-6">
        <Seg value={view} onChange={setView} testId="cases-view"
             options={[['cases', t('Cases', '考题')], ['history', t('Run history', '运行记录')]]} />
        {view === 'cases' && <>
          <Seg value={line} onChange={setLine} testId="cases-line"
               options={[['all', t('All', '全部')], ['quick', t('Quick checks', '快速检查')], ['offline', t('Offline', '离线考题')]]} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('Search…', '搜索…')}
                 className="ml-auto w-56 px-2.5 py-1 rounded-md border border-[var(--color-border)] bg-transparent text-small text-[var(--color-text)]" />
        </>}
      </div>

      {view === 'cases' ? <>
        <NotPassing cases={bad.filter(match)} onOpen={open} />
        <Passing cases={live.filter((c) => !!c.last_result && !notPassing(c) && match(c))} onOpen={open} title={t('Passing', '通过')} testId="passing" />
        <Passing cases={neverRun.filter(match)} onOpen={open} title={t('Not run yet', '还没跑过')} testId="not-run" />
        {(q || line !== 'all') && !live.some(match) && (
          <div className="flex items-center gap-3 text-small text-[var(--color-text-muted)] mt-8 py-3 border-t border-[var(--color-border)]" data-testid="cases-no-match">
            <span className="flex-1">{q ? t(`No case matches “${q}”.`, `没有 case 匹配“${q}”。`) : t('No case in this line.', '这一类下没有 case。')}</span>
            <Btn onClick={() => { setQ(''); setLine('all'); }}>{t('Clear the filter', '清除筛选')}</Btn>
          </div>
        )}
        {mine.length === 0 && (
          <div className="flex items-center gap-4 text-small text-[var(--color-text-muted)] mt-10" data-testid="my-cases-empty">
            <span className="flex-1">{t('None of your own yet. One worth adding: a decision in your own project that once went wrong and must never go wrong again.',
              '你自己的 case 还没有。值得加的例子：你自己项目里一个曾经出过错、又一定不能再错的决定。')}</span>
            <Btn onClick={() => setPanel({ kind: 'form', type: 'offline' })}>{t('Add', '新增')}</Btn>
          </div>
        )}
      </> : <RunHistory />}

      <SidePanel open={!!panel} onClose={() => setPanel(null)} testId="case-panel">
        {panel?.kind === 'case' && <CaseDetailView key={panel.id} id={panel.id} onForm={(p) => setPanel(p)} onClosed={() => setPanel(null)} />}
        {panel?.kind === 'form' && <CaseForm key={`${panel.type}-${panel.from?.id ?? ''}-${panel.editing}`} type={panel.type} from={panel.from}
                                             editing={!!panel.editing} total={live.length}
                                             onType={(type) => setPanel({ ...panel, type })} onDone={() => setPanel(null)} />}
      </SidePanel>
    </div>
  );
}

/** "Run": the quick check (code only) or the full set (calls the model). The menu
 *  closes on Escape or a click outside; what came of the run is said under the header. */
function RunMenu() {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const runQuick = useRunQuick();
  const runFull = useRunFull();
  const { running: fullRunning, failed: fullFailed } = useRunProgress(runFull.data?.run_id);
  const busy = runQuick.isPending || fullRunning;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } };
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown);
    return () => { window.removeEventListener('keydown', onKey, true); document.removeEventListener('mousedown', onDown); };
  }, [open]);
  const start = (fn: () => void) => { setOpen(false); setDismissed(false); fn(); };
  const quick = runQuick.data as { cases_passed?: number; total_cases?: number } | undefined;
  const message = runQuick.isError || runFull.isError ? { bad: true, text: errorDetail(runQuick.error ?? runFull.error) }
    : fullFailed ? { bad: true, text: t('The run did not finish: ', '这次运行没跑完：') + fullFailed }
      : runQuick.isSuccess ? { bad: false, text: quick?.total_cases != null ? t(`Quick check done · ${quick.cases_passed}/${quick.total_cases}`, `快速检查跑完 · ${quick.cases_passed}/${quick.total_cases}`) : t('Quick check done.', '快速检查跑完了。') }
        : null;
  return (
    <div className="relative" ref={ref}>
      <Btn testId="run-menu" onClick={() => setOpen((o) => !o)}>{busy ? t('Running…', '运行中…') : t('Run', '运行')} ▾</Btn>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-10 w-72 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] shadow-lg p-2 space-y-1" role="menu">
          <button type="button" role="menuitem" data-testid="run-quick" disabled={runQuick.isPending} onClick={() => start(() => runQuick.mutate())}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-[var(--color-hover)] disabled:opacity-40">
            <div className="text-body text-[var(--color-text)]">{t('Quick check', '快速检查')}</div>
            <div className="text-small text-[var(--color-text-dim)]">{t('code checks only, in a temporary workspace', '只跑代码检查，在临时工作区')}</div>
          </button>
          <button type="button" role="menuitem" data-testid="run-full" disabled={runFull.isPending || fullRunning} onClick={() => start(() => runFull.mutate())}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-[var(--color-hover)] disabled:opacity-40">
            <div className="text-body text-[var(--color-text)]">{t('The full set', '全量')}</div>
            <div className="text-small text-[var(--color-text-dim)]">{t('includes the offline cases: a real agent answers, calls the model', '含离线考题：真实 agent 答题，会调用模型')}</div>
          </button>
        </div>
      )}
      {message && !dismissed && (
        <div className="absolute right-0 top-full mt-1 w-72 flex gap-2 items-start text-small rounded-md border border-[var(--color-border)] bg-[var(--color-card)] shadow p-2"
             style={{ color: message.bad ? 'var(--color-status-error)' : 'var(--color-text-muted)', visibility: open ? 'hidden' : 'visible' }}
             data-testid={fullFailed ? 'run-full-failed' : 'run-message'}>
          <span className="flex-1 break-words">{message.text}</span>
          <button type="button" aria-label={t('dismiss', '关闭')} onClick={() => setDismissed(true)} className="text-[var(--color-text-dim)] hover:text-[var(--color-text)]">✕</button>
        </div>
      )}
    </div>
  );
}

/** The latest result's reason, from the run that produced it (one detail fetch for
 *  the newest run among the cases; a case from an older run shows its status only). */
function NotPassing({ cases, onOpen }: { cases: GoldenCase[]; onOpen: (c: GoldenCase) => void }) {
  const { lang, t } = useLang();
  const runId = cases.map((c) => c.last_result!).sort((a, b) => (b.triggered_at ?? '').localeCompare(a.triggered_at ?? ''))[0]?.run_id ?? null;
  const run = useRunDetail(runId);
  const byId = new Map((run.data?.cases ?? []).map((r) => [r.id, r]));
  if (!cases.length) return null;
  return (
    <section className="mt-8" data-testid="not-passing">
      <h2 className="text-heading font-semibold text-[var(--color-text)] mb-2">{t('Not passing', '没通过')}</h2>
      {cases.map((c) => {
        const r = c.last_result?.run_id === runId ? byId.get(c.id) : undefined;
        const reason = judgeReason(r?.notes, lang);
        return (
          <button type="button" key={c.id} onClick={() => onOpen(c)} data-testid={`case-row-${c.id}`}
                  className="w-full grid grid-cols-[1fr_64px_70px_12px] gap-3 items-center py-3 border-t border-[var(--color-border)] text-left hover:bg-[var(--color-hover)]/40">
            <span className="min-w-0">
              <span className="block text-body text-[var(--color-text)] truncate">{caseName(c, lang)}</span>
              <span className="block text-small text-[var(--color-text-muted)] truncate">
                <span style={{ color: statusColor(c.last_result?.status) }}>{statusLabel(c.last_result?.status, lang)}</span>
                {` · ${when(c.last_result?.triggered_at)}`}{reason ? ` · ${reason}` : ''}
              </span>
            </span>
            <span className="text-small text-[var(--color-text-dim)]">{isQuick(c) ? t('quick', '快速') : t('offline', '离线')}</span>
            <span className="text-right"><TierText tier={tierOf(c)} /></span>
            <span className="text-[var(--color-text-dim)]">›</span>
          </button>
        );
      })}
      <div className="border-t border-[var(--color-border)]" />
    </section>
  );
}

/** Cases folded by category: the passing ones, or the ones that never ran. */
function Passing({ cases, onOpen, title, testId }: { cases: GoldenCase[]; onOpen: (c: GoldenCase) => void; title: string; testId: string }) {
  const { lang, t } = useLang();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!cases.length) return null;
  const by: Record<string, GoldenCase[]> = {};
  cases.forEach((c) => (by[c.category] = by[c.category] || []).push(c));
  const passing = testId === 'passing';
  return (
    <section className="mt-10" data-testid={testId}>
      <h2 className="text-heading font-semibold text-[var(--color-text)] mb-2">
        {title} <span className="text-small font-normal text-[var(--color-text-dim)] ml-1">{cases.length}</span>
      </h2>
      {Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([cat, cs]) => {
        const isOpen = !!open[cat];
        return (
          <div key={cat} className="border-t border-[var(--color-border)]" data-testid={`${passing ? 'group' : 'not-run-group'}-${cat}`}>
            <button type="button" onClick={() => setOpen({ ...open, [cat]: !isOpen })} aria-expanded={isOpen}
                    className="w-full flex items-center gap-3 py-2.5 text-left text-body">
              <span className={`text-[var(--color-text-dim)] transition-transform ${isOpen ? 'rotate-90' : ''}`}>›</span>
              <span className="text-[var(--color-text)]">{categoryName(cat, lang)}</span>
              <span className="text-small text-[var(--color-text-dim)]">{cs.length}</span>
              {passing && <span className="ml-auto text-small" style={{ color: 'var(--color-status-success)' }}>{t('all pass', '全部通过')}</span>}
            </button>
            {isOpen && cs.map((c) => (
              <button type="button" key={c.id} onClick={() => onOpen(c)} data-testid={`case-row-${c.id}`}
                      className="w-full grid grid-cols-[1fr_64px_70px_12px] gap-3 items-center py-2 pl-6 text-left text-body hover:bg-[var(--color-hover)]/40">
                <span className="truncate text-[var(--color-text)]">{caseName(c, lang)}</span>
                <span className="text-small text-[var(--color-text-dim)]">{isQuick(c) ? t('quick', '快速') : t('offline', '离线')}</span>
                <span className="text-right"><TierText tier={tierOf(c)} /></span>
                <span className="text-[var(--color-text-dim)]">›</span>
              </button>
            ))}
          </div>
        );
      })}
      <div className="border-t border-[var(--color-border)]" />
    </section>
  );
}

// ─── run history ───────────────────────────────────────────────────────────

function RunHistory() {
  const { t } = useLang();
  const health = useEvalHealth();
  const history = useEvalHistory();
  const lines = health.data?.lines;
  return (
    <div data-testid="run-history">
      <section className="mt-8">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('Quick check, by day', '快速检查，按天')}</h2>
        <div className="text-small text-[var(--color-text-muted)] mt-1">
          {lines?.quick.total != null ? `${when(lines.quick.last_run)} · ${lines.quick.passed}/${lines.quick.total}` : t('not run yet', '还没跑过')}
          {' · '}{t('runs after a session ends, in a temporary workspace that never touches your data', '会话结束后自动跑，在临时工作区，不碰你的数据')}
        </div>
        <Days days={lines?.quick.days ?? []} />
      </section>
      <section className="mt-10">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('Full runs', '全量运行')}</h2>
        <div className="text-small text-[var(--color-text-muted)] mt-1 mb-2">
          {t('next', '下次')} {when(lines?.full.next_run)} · {t('the four release checks of each run and why it passed or not', '每次的四道放行关和放行理由')}
        </div>
        <ReleaseRecord runs={(history.data ?? []).filter((r) => r.line === 'full')} />
      </section>
    </div>
  );
}

function Days({ days }: { days: QuickDay[] }) {
  const { t } = useLang();
  if (!days.length) return <div className="text-small text-[var(--color-text-dim)] mt-3">{t('The day-by-day row starts with the next quick check.', '按天的记录从下一次快速检查开始。')}</div>;
  return (
    <div className="flex gap-1 mt-3 flex-wrap" data-testid="quick-days">
      {days.map((d) => {
        const bad = (d.failed ?? 0) > 0 || (d.timeout ?? 0) > 0;
        const color = bad ? 'var(--color-status-warning)' : d.verdict === 'release' ? 'var(--color-status-success)' : 'var(--color-text-dim)';
        const title = `${d.date}: ${d.passed ?? 0}/${d.total ?? 0}${d.not_run ? t(`, ${d.not_run} not run`, `，${d.not_run} 条没跑完`) : ''}${d.timeout ? t(`, ${d.timeout} timed out`, `，${d.timeout} 条超时`) : ''}`;
        return (
          <div key={d.date} title={title} className="flex flex-col items-center gap-1 text-meta text-[var(--color-text-dim)] w-7">
            <i className="block w-5 h-5 rounded" style={{ background: `color-mix(in srgb, ${color} 55%, transparent)` }} />{d.date.slice(8)}
          </div>
        );
      })}
    </div>
  );
}

function ReleaseRecord({ runs }: { runs: EvalRun[] }) {
  const { t } = useLang();
  const [showOld, setShowOld] = useState(false);
  const decided = runs.filter((r) => r.release || r.status === 'failed');  // a crash is shown, not hidden as old
  const old = runs.length - decided.length;
  const openReport = (f: string) => void openExternal(`${getApiBaseUrl()}/api/eval/reports/${encodeURIComponent(f)}`);
  const shown = showOld ? runs : decided;
  const toggle = old > 0 && (
    <div className="text-small text-[var(--color-text-dim)] mt-2">
      {t(`${old} older runs used an earlier golden set and cannot be compared. `, `${old} 次更早的运行用的是旧 golden set，不能和现在比。`)}
      <LinkBtn className="inline" onClick={() => setShowOld((v) => !v)}>{showOld ? t('Hide', '收起') : t('Show', '展开')}</LinkBtn>
    </div>
  );
  if (!shown.length) return <><div className="text-small text-[var(--color-text-dim)]">{t('No full run with a release decision yet.', '还没有带放行结论的全量运行。')}</div>{toggle}</>;
  const cell = (r: EvalRun, id: string) => {
    const c = checkById(r.release_checks, id);
    if (!c) return <td className="py-2 text-[var(--color-text-dim)]">—</td>;
    const n = c.cases?.length ?? 0;
    const text = id === 'denominator' ? `${c.with_result ?? '—'}/${c.selected ?? '—'}` : id === 'redline' ? t(`${n} violations`, `${n} 违例`) : n ? t(`${n} failed`, `${n} 条没过`) : t('all passed', '全过');
    return <td className="py-2 tabular-nums" style={{ color: c.passed ? 'var(--color-status-success)' : 'var(--color-status-error)' }}>{text}</td>;
  };
  return (
    <>
      <table className="w-full text-small" data-testid="release-record">
        <thead><tr className="text-left text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
          <th className="py-2 font-normal">{t('When', '时间')}</th><th className="font-normal">{t('Red line', '红线')}</th>
          <th className="font-normal">{t('Gate', '门禁')}</th><th className="font-normal">{t('With a result', '有结果')}</th>
          <th className="font-normal">{t('Reason', '放行理由')}</th><th /></tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.run_id} className="border-b border-[var(--color-border)] align-top">
              <td className="py-2 whitespace-nowrap text-[var(--color-text)]">{when(r.triggered_at)} · {r.triggered_by}</td>
              {r.release ? <>{cell(r, 'redline')}{cell(r, 'dimensions')}{cell(r, 'denominator')}</>
                : r.status === 'failed' ? <td colSpan={3} className="py-2 text-[var(--color-status-error)]">{t('did not finish', '没跑完')}</td>
                  : <td colSpan={3} className="py-2 text-[var(--color-text-dim)]">{t('old golden set, not comparable', '旧 golden set，不能比')}</td>}
              <td className="py-2 text-[var(--color-text-muted)] break-words">{r.release_reason ?? r.error ?? '—'}</td>
              <td className="py-2 text-right">{r.report && <LinkBtn className="text-[var(--color-primary)]" onClick={() => openReport(r.report!)}>{t('Report', '报告')}</LinkBtn>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {toggle}
    </>
  );
}

// ─── detail ────────────────────────────────────────────────────────────────

/** A tool step's object without the temporary workspace it ran in. */
const shortInput = (input?: string | null) => (input ?? '')
  .replace(/"[^"]*\/workspace\//g, '"').replace(/^\{"(file_path|path|pattern|command)":\s*"([^"]*)".*$/s, '$2').slice(0, 120);

function CaseDetailView({ id, onForm, onClosed }: { id: string; onForm: (p: Panel) => void; onClosed: () => void }) {
  const { lang, t } = useLang();
  const go = useGo();
  const { askSwarm } = useEvalActions();
  const detail = useCaseDetail(id);
  const rerun = useRunCases();
  const { running: rerunning, failed: rerunFailed } = useRunProgress(rerun.data?.run_id);
  const del = useDeleteCase();
  const [confirm, setConfirm] = useState(false);
  const last = detail.data?.history?.[0];
  const run = useRunDetail(detail.data && !isQuick(detail.data) && last ? last.run_id : null);
  if (detail.isLoading) return <Loading />;
  if (detail.isError || !detail.data) return <ErrorState />;
  const c = detail.data;
  const quick = isQuick(c);
  const mineCase = c._origin !== 'product';
  const tr = tierOf(c);
  const [, rest] = splitPurpose(pick(c.purpose, lang));
  const prev = c.history?.[1];
  const v = c.verification ?? {};
  const r = (run.data?.cases ?? []).find((x) => x.id === c.id);
  const failing = !!last && last.status !== 'passed';
  const missing = (r?.expected_check ?? []).filter((e) => !e.found).map((e) => e.step);
  const tierWhy = tr === 'red' ? t('If it fails, the release is blocked, whatever else passed.', '一旦不通过，整次评测判红，其它再好也不放行。')
    : tr === 'gate' ? t('Must pass to release. Code-checked, so the result is reproducible.', '要通过才放行。全是确定性检查，结果可复现。')
      : t('Shown, never blocks. A judge-scored or new case starts here; once it passes steadily and the case itself is confirmed sound, it can move up (your call).',
        '只看不拦。judge 判的、新加的 case 从这里开始；稳定通过、题目确认无误后可以升档（升档要你点头）。');

  return (
    <div data-testid="case-detail">
      <div className="text-small text-[var(--color-text-muted)]">
        {quick ? t('Quick check', '快速检查') : t('Offline case', '离线考题')} · {dimName(c.dimension, lang)} · <TierText tier={tr} /> · {mineCase ? t('yours', '你的') : t('ships with SwarmAI', '随 SwarmAI 发布')}
      </div>
      <h3 className="text-title font-semibold text-[var(--color-text)] mt-1">{caseName(c, lang)}</h3>
      {rest && <p className="text-body text-[var(--color-text-muted)] mt-1">{rest}</p>}

      <H4>{last ? t(`Latest result · ${when(last.triggered_at)}`, `最近结果 · ${when(last.triggered_at)}`) : t('Latest result', '最近结果')}</H4>
      {last ? (
        <div className="text-small" data-testid="case-latest">
          <b style={{ color: statusColor(last.status) }}>{statusLabel(last.status, lang)}</b>
          {prev && prev.status !== last.status && <span className="text-[var(--color-text-muted)]"> · {t(`${statusLabel(prev.status, lang).toLowerCase()} on ${when(prev.triggered_at).slice(0, 5)}`, `${when(prev.triggered_at).slice(0, 5)} 是${statusLabel(prev.status, lang)}`)}</span>}
          {last.notes && <div className="text-[var(--color-text)] mt-1 break-words">{judgeReason(last.notes, lang)}</div>}
          {last.notes && judgeReason(last.notes, lang) !== last.notes.trim() && (
            <details className="mt-1"><summary className="cursor-pointer text-[var(--color-text-dim)]">{t('The full note', '完整记录')}</summary>
              <div className="text-[var(--color-text-muted)] mt-1 break-words whitespace-pre-wrap">{last.notes}</div></details>
          )}
        </div>
      ) : <div className="text-small text-[var(--color-text-dim)]">{t('Not run yet.', '还没跑过。')}</div>}

      {!quick && <>
        <H4>{t('What it was asked', '题面')}</H4>
        <p className="text-small text-[var(--color-text)] whitespace-pre-wrap">{c.scenario?.prompt ?? '—'}</p>
        <H4>{t('What it did', '它做了什么')}</H4>
        <div data-testid="drilldown">
          {r?.steps?.length ? (
            <div className="space-y-0.5">
              {r.steps.slice(0, 12).map((s, i) => (
                <div key={i} className={`font-mono text-meta break-all ${s.is_error ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}
                     data-testid={s.is_error ? 'step-error' : undefined}>
                  <span className="text-[var(--color-text-dim)]">{i + 1}.</span>{' '}
                  <span className={s.is_error ? '' : 'text-[var(--color-text)]'}>{s.tool}</span> {shortInput(s.input)}
                  {s.is_error && <> · {t('returned an error', '返回了错误')}{s.output ? `: ${String(s.output).slice(0, 240)}` : ''}</>}
                </div>
              ))}
              {r.steps.length > 12 && <div className="text-meta text-[var(--color-text-dim)]">{t(`… ${r.steps.length - 12} more steps`, `… 还有 ${r.steps.length - 12} 步`)}</div>}
            </div>
          ) : r?.observed_trajectory?.length ? (
            <div className="font-mono text-meta text-[var(--color-text-muted)]">{r.observed_trajectory.map((s) => s.split(' ')[0]).join(' → ')}</div>
          ) : <span className="text-small text-[var(--color-text-dim)]">{!last ? t('Not run yet.', '还没跑过。')
            : mineCase ? t('Your own cases keep only tool names: their run records are tracked in your workspace, so what the agent read stays out.', '你自己的 case 只记工具名：运行记录在你的工作区里被 git 跟踪，agent 读到的内容不写进去。')
              : t('This run kept no tool steps (runs before the drill-down was recorded).', '这次运行没有保存工具步骤（记录下钻之前的运行）。')}</span>}
          {missing.length > 0 && <div className="text-small text-[var(--color-status-warning)] mt-1">{t('Expected but not seen: ', '期望出现但没出现：')}{missing.join(', ')}</div>}
          {r?.final_text && <>
            <H4>{t('Its answer', '它的回答')}</H4>
            <details><summary className="cursor-pointer text-small text-[var(--color-text)] line-clamp-2">{r.final_text.replace(/[*`#]+/g, '').replace(/\s+/g, ' ').slice(0, 200)}</summary>
              <pre className="whitespace-pre-wrap text-small text-[var(--color-text)] mt-1">{r.final_text}</pre></details>
          </>}
        </div>
      </>}

      <div className="flex flex-wrap gap-2 mt-6">
        <Btn kind={failing ? 'primary' : 'normal'} onClick={() => askSwarm(failing
          ? t(`Golden case ${c.id} does not pass. Check the case itself first (can the right answer be reached?), then the agent's answer.`, `golden case ${c.id} 没通过。先查 case 本身（正确答案能不能做到），再看 agent 的回答。`)
          : t(`Review golden case ${c.id}: is what it protects still right, and does the check have a hole?`, `看一下 golden case ${c.id}：它守住的东西现在还对吗，检查方式有没有漏洞`))}>
          {failing ? t('Ask Swarm to look', '让 Swarm 查') : t('Ask Swarm to review it', '让 Swarm 审这条')}
        </Btn>
        <Btn onClick={() => rerun.mutate({ ids: [c.id], behavior: !quick })} disabled={rerun.isPending || rerunning} testId="case-rerun">
          {rerunning ? t('Running…', '运行中…') : t('Run this case', '跑这一条')}
        </Btn>
        {mineCase ? (
          <>
            <Btn testId="case-edit" onClick={() => onForm({ kind: 'form', type: quick ? 'quick' : 'offline', from: c, editing: true })}>{t('Edit', '编辑')}</Btn>
            {confirm ? (
              <Btn kind="danger" testId="case-delete-confirm" onClick={() => del.mutate(c.id, { onSuccess: onClosed })}>{t('Delete for good', '确认删除')}</Btn>
            ) : <Btn kind="danger" testId="case-delete" onClick={() => setConfirm(true)}>{t('Delete', '删除')}</Btn>}
          </>
        ) : (
          <Btn testId="case-copy" onClick={() => onForm({ kind: 'form', type: quick ? 'quick' : 'offline', from: c })}>{t('Copy as my case', '复制成我的 case')}</Btn>
        )}
      </div>
      {rerun.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(rerun.error)}</div>}
      {rerunFailed && <div className="text-small text-[var(--color-status-error)] mt-2">{t('The run did not finish: ', '这次运行没跑完：')}{rerunFailed}</div>}
      {del.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(del.error)}</div>}

      <details className="mt-8" data-testid="case-how">
        <summary className="cursor-pointer text-small font-semibold text-[var(--color-text-muted)]">{t('How it is checked', '怎么检查的')}</summary>
        {quick ? (
          <div className="mt-3"><KV rows={[
            [t('Command', '检查命令'), <Code>{v.command ?? '—'}</Code>],
            [t('Passes when', '通过的条件'), <>{t('output contains', '输出里有')} <Code>{v.expected_contains ?? '—'}</Code></>],
            [t('Negative command', '反例命令'), <Code>{v.negative_command ?? '—'}</Code>],
            [t('Negative must', '反例必须'), <>{t('print', '输出')} <Code>{v.negative_expected_contains ?? '—'}</Code>. {t('Break the thing on purpose and the check must catch it; if it cannot, it tests nothing.', '故意把功能弄坏，这条检查要能抓到；抓不到就是什么都没测。')}</>],
          ]} /></div>
        ) : (
          <>
            <H4>{t('Step one · the tools it must use', '第一步 · 必须用到的工具')}</H4>
            <KV rows={[
              [t('Must appear', '必须出现'), <>{(c.expected_trajectory ?? []).map((x) => <Code key={x}>{x}</Code>)} <span className="text-[var(--color-text-dim)]">{c.trajectory_match === 'any_order' ? t('any order; A|B = either', '顺序不限，A|B 表示任一') : t('in order; A|B = either', '按顺序，A|B 表示任一')}</span></>],
              ...(c.allowed_tools?.length ? [[t('Allowed', '允许用'), <>{c.allowed_tools.map((x) => <Code key={x}>{x}</Code>)}</>] as [string, ReactNode]] : []),
            ]} />
            {c.decision_rubric && <>
              <H4>{t('Step two · a judge reads the answer, only after step one passes', '第二步 · 第一步过了，judge 才看回答')}</H4>
              <pre className="text-small whitespace-pre-wrap bg-[var(--color-hover)] rounded p-3 text-[var(--color-text)]">{c.decision_rubric}</pre>
              <LinkBtn className="block mt-1 text-small text-[var(--color-text-dim)]" onClick={() => { onClosed(); go('method', 'judges'); }}>{t('See the judge and its prompt', '看 judge 和它的 prompt')}</LinkBtn>
            </>}
          </>
        )}
        <H4>{t('Tier', '档位')}</H4>
        <p className="text-small text-[var(--color-text)]">{tierWhy}</p>
        <H4>{t('Changing these files marks the result stale until it re-runs', '改到这些文件时，这条的结果标成过期、等下次重跑')}</H4>
        <div className="text-small space-y-0.5">{(c.affected_by ?? []).map((f) => <div key={f}><Code>{f}</Code></div>)}</div>
        <H4>ID</H4><Code>{c.id}</Code>
        <p className="text-small text-[var(--color-text-dim)] mt-4">
          {mineCase ? t('Delete removes it from the file; nothing is archived. Add it again if you need it.', '删除就是从文件里删掉，不归档；需要时再加。')
            : t('Product cases are the same for every user: they cannot be edited or deleted here. Changing one is a code change.', '产品 case 每个用户都一样，这里不能改、不能删，要改就是改代码。')}
        </p>
      </details>
    </div>
  );
}

// ─── form ──────────────────────────────────────────────────────────────────

const CHECK_LABEL: Record<string, { en: string; zh: string }> = {
  schema: { en: 'Fields complete', zh: '字段完整' },
  duplicate: { en: 'Not a duplicate of an existing case', zh: '不和已有 case 重复' },
  non_vacuous: { en: 'Not an empty check', zh: '不是空检查' },
  teeth: { en: 'Has a negative command', zh: '有反例命令' },
  redline: { en: 'Red-line marker valid', zh: '红线标记有效' },
  gating: { en: 'Tier allowed', zh: '档位合法' },
  refs: { en: 'Referenced rules and files exist', zh: '引用的规则和文件存在' },
  purpose: { en: 'Says what it protects', zh: '写清楚它守什么' },
  dimension: { en: 'Dimension is cognition, quality or responsibility', zh: '维度是认知、质量或责任' },
  offline: { en: 'Prompt, tool steps and a two-sided rubric', zh: '题面、工具步骤和两面的判定标准' },
  trial: { en: 'Trial run: passes, and the negative fails', zh: '试跑：正例通过，反例失败' },
};

function CaseForm({ type, from, editing, total, onType, onDone }: {
  type: 'quick' | 'offline'; from?: CaseDetail; editing: boolean; total: number;
  onType: (t: 'quick' | 'offline') => void; onDone: () => void;
}) {
  const { lang, t } = useLang();
  const { askSwarm } = useEvalActions();
  const quick = type === 'quick';
  const [head0, rest0] = splitPurpose(pick(from?.purpose, lang));
  const [f, setF] = useState({
    id: editing ? from!.id : from ? `${from.id}_MINE` : '',
    head: head0, rest: rest0,
    category: from?.category ?? (quick ? 'recall' : 'decision'),
    dimension: CASE_DIMENSIONS.includes(from?.dimension as never) ? from!.dimension : 'quality',
    affected: (from?.affected_by ?? []).join(' '),
    command: from?.verification?.command ?? '', expected: from?.verification?.expected_contains ?? '',
    negative: from?.verification?.negative_command ?? '', negExpected: from?.verification?.negative_expected_contains ?? '',
    prompt: from?.scenario?.prompt ?? '', steps: (from?.expected_trajectory ?? []).join(' '), rubric: from?.decision_rubric ?? '',
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setF({ ...f, [k]: e.target.value });
    setResult(null);  // an old trial says nothing about the edited case
    tryIt.reset();
    save.reset();
  };
  const tryIt = useTryCase();
  const save = useSaveCase();
  const [result, setResult] = useState<AdmissionResult | null>(null);

  // A save replaces purpose / verification / scenario whole (the backend merges one
  // level deep), so what the form does not show is carried over from the case it
  // started from: the other language of an unchanged purpose, scenario.system,
  // the trajectory order and the allowed tools.
  const payload = (): CaseInput => {
    const purpose = [f.head.trim(), f.rest.trim()].filter(Boolean).join(' ');
    const unchanged = !!from && purpose === [head0, rest0].filter(Boolean).join(' ');
    const base = {
      id: f.id.trim(), category: f.category, dimension: f.dimension, title: f.head.trim(),
      purpose: unchanged ? { ...(from!.purpose ?? {}) } : { [lang]: purpose },
      affected_by: f.affected.split(/\s+/).filter(Boolean),
    };
    return quick
      ? { ...base, eval_method: 'programmatic', evaluators: ['canary_pass'],
          verification: { ...(editing ? from?.verification ?? {} : from?.verification?.timeout != null ? { timeout: from.verification.timeout } : {}),
                          command: f.command, expected_contains: f.expected,
                          negative_command: f.negative, negative_expected_contains: f.negExpected } }
      : { ...base, eval_method: 'behavior', evaluators: ['trajectory_capture'], scenario: { ...(from?.scenario ?? {}), prompt: f.prompt },
          expected_trajectory: f.steps.split(/\s+/).filter(Boolean), trajectory_match: from?.trajectory_match ?? 'any_order',
          ...(from?.allowed_tools ? { allowed_tools: from.allowed_tools } : {}), decision_rubric: f.rubric };
  };
  const doTry = () => { save.reset(); tryIt.mutate(payload(), { onSuccess: setResult, onError: () => setResult(null) }); };
  const doSave = () => { tryIt.reset(); save.mutate({ c: payload(), editing }, { onSuccess: onDone }); };

  const field = (label: string, hint: string, input: ReactNode) => (
    <label className="block mt-4">
      <span className="text-small text-[var(--color-text)]">{label}{hint && <small className="text-[var(--color-text-dim)] ml-2">{hint}</small>}</span>
      <div className="mt-1">{input}</div>
    </label>
  );
  const inputCls = 'w-full px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-transparent text-small text-[var(--color-text)]';
  const checks = Object.keys(CHECK_LABEL).filter((k) => k !== (quick ? 'offline' : 'teeth') && (quick || k !== 'trial'));

  return (
    <div data-testid="case-form">
      <h3 className="text-title font-semibold text-[var(--color-text)]">
        {editing ? t('Edit my case', '编辑我的 case') : from ? t('Copy a product case as my case', '从产品 case 复制一条我的 case') : t('Add my case', '新增我的 case')}
      </h3>
      <p className="text-small text-[var(--color-text-muted)] mt-1">
        {t('Stored in your own workspace, runs only on your machine. A new case starts in the watch tier: first prove the case itself is sound.',
          '存在你自己的工作区里，只在你的机器上跑，别人看不到。新 case 一律从观测档开始：先证明题目本身没问题。')}
      </p>
      {!editing && (
        <div className="flex items-center gap-3 text-small text-[var(--color-text-muted)] mt-4 py-3 border-y border-[var(--color-border)]">
          <span className="flex-1">{t('Not sure how to write it? Say what you want to protect; Swarm writes the case and tries it first.', '不确定怎么写？说一句你想守住什么，Swarm 帮你写成 case，并先试跑一次。')}</span>
          <Btn onClick={() => askSwarm(t('Help me write a golden case: I want to protect …', '帮我写一条 golden case：我想守住……'))}>{t('Ask Swarm to write it', '让 Swarm 帮我写')}</Btn>
        </div>
      )}
      {!editing && (
        <div className="flex gap-1 mt-4">
          {(['offline', 'quick'] as const).map((ty) => (
            <button key={ty} type="button" role="radio" aria-checked={type === ty} onClick={() => onType(ty)}
                    className={`btn-sm ${type === ty ? 'btn-soft' : 'btn-ghost'}`}>
              {ty === 'offline' ? t('Offline case · the agent answers', '离线考题 · agent 答题') : t('Quick check · you write a check script', '快速检查 · 要会写检查脚本')}
            </button>
          ))}
        </div>
      )}
      {field(t('Case id', 'Case ID'), t('letters, digits, _', '字母、数字、下划线'), <input className={inputCls} value={f.id} onChange={set('id')} disabled={editing} placeholder="MY_ARCHIVE_LOOKUP" data-testid="form-id" />)}
      {field(t('What it protects', '它守住什么'), t('the agent is the subject, one sentence', '主语是 agent，一句话'), <input className={inputCls} value={f.head} onChange={set('head')} data-testid="form-head"
        placeholder={t('e.g. Asked about something only in an archive, the agent looks it up first.', '例：问到只记在归档里的事，agent 先去找。')} />)}
      {field(t('What breaks for you if it fails', '坏了对你意味着什么'), '', <input className={inputCls} value={f.rest} onChange={set('rest')}
        placeholder={t('e.g. Archived knowledge is as good as lost.', '例：归档的知识等于丢了。')} />)}
      <div className="grid grid-cols-2 gap-4">
        {field(t('Dimension', '维度'), '', <select className={inputCls} value={f.dimension} onChange={set('dimension')} data-testid="form-dimension">
          {CASE_DIMENSIONS.map((d) => <option key={d} value={d}>{dimName(d, lang)}</option>)}</select>)}
        {field(t('Category', '分类'), '', <select className={inputCls} value={f.category} onChange={set('category')}>
          {Object.keys(CATEGORY_LABEL).map((k) => <option key={k} value={k}>{categoryName(k, lang)}</option>)}</select>)}
      </div>
      {field(t('Files that make the result stale', '改到哪些文件会让结果过期'), t('space-separated', '空格分隔'), <input className={inputCls} value={f.affected} onChange={set('affected')} data-testid="form-affected" placeholder="backend/core/memory_index.py" />)}
      {quick ? (
        <>
          {field(t('Check command', '检查命令'), t('runs in a temporary directory', '在临时目录里跑'), <input className={inputCls} value={f.command} onChange={set('command')} data-testid="form-command" />)}
          {field(t('Output contains when it passes', '通过时输出包含'), '', <input className={inputCls} value={f.expected} onChange={set('expected')} />)}
          {field(t('Negative command', '反例命令'), t('the same check with the thing broken on purpose', '故意把功能弄坏后的同一个检查'), <input className={inputCls} value={f.negative} onChange={set('negative')} />)}
          {field(t('Negative output contains', '反例输出包含'), '', <input className={inputCls} value={f.negExpected} onChange={set('negExpected')} />)}
        </>
      ) : (
        <>
          {field(t('Prompt', '题面'), t('what you would say to the agent', '你会对 agent 说的原话'), <textarea className={`${inputCls} h-20`} value={f.prompt} onChange={set('prompt')} data-testid="form-prompt" />)}
          {field(t('Tools that must appear', '必须出现的工具'), t('space-separated, A|B = either', '空格分隔，A|B 表示任一'), <input className={inputCls} value={f.steps} onChange={set('steps')} placeholder="Grep|Read|Glob" data-testid="form-steps" />)}
          {field(t('Rubric for the judge', '判定标准（给 judge）'), t('"PASS only if …; FAIL if …"', '写成"PASS only if …；FAIL if …"'), <textarea className={`${inputCls} h-20`} value={f.rubric} onChange={set('rubric')} data-testid="form-rubric" />)}
        </>
      )}

      <H4>{t('Checked before it is saved', '保存前会自动检查')}</H4>
      <div className="space-y-1" data-testid="form-checks">
        {checks.map((k) => {
          const r = result?.checks.find((c) => c.name === k);
          return (
            <div key={k} className="flex gap-2 text-small" data-testid={`form-check-${k}`} data-state={r ? (r.ok ? 'ok' : 'fail') : 'pending'}>
              <span style={{ color: r ? (r.ok ? 'var(--color-status-success)' : 'var(--color-status-error)') : 'var(--color-text-dim)' }}>{r ? (r.ok ? '✓' : '✗') : '○'}</span>
              <span className="text-[var(--color-text)]">{CHECK_LABEL[k][lang]}{r && !r.ok && <span className="text-[var(--color-status-error)]"> — {r.errors.join('; ')}</span>}</span>
            </div>
          );
        })}
        {!quick && <div className="text-small text-[var(--color-text-dim)]">{t('An offline case is not trial-run when saved (it would spawn a real agent): run it from its detail once saved.', '离线考题保存时不试跑（会启动真实 agent）：保存后在详情里点"跑这一条"。')}</div>}
        <div className="text-small text-[var(--color-text-dim)]">{t(`Total now ${total}`, `现在共 ${total} 条`)}</div>
      </div>
      <div className="flex gap-2 mt-5">
        <Btn onClick={doTry} disabled={tryIt.isPending} testId="form-try">{tryIt.isPending ? t('Trying…', '试跑中…') : t('Try it first', '先试跑一次')}</Btn>
        <Btn kind="primary" onClick={doSave} disabled={save.isPending} testId="form-save">{t('Save', '保存')}</Btn>
      </div>
      {(tryIt.isError || save.isError) && (
        <div className="text-small text-[var(--color-status-error)] mt-3 break-words" data-testid="form-error">{errorDetail(tryIt.error ?? save.error)}</div>
      )}
      <p className="text-small text-[var(--color-text-dim)] mt-5">
        {t('A good case: two people who know the work reach the same pass / fail on the same result; one way of failing per case; judge what it achieved, not the exact path.',
          '好 case 的标准：两个懂行的人看同一个结果，会给出同样的通过 / 不通过；一种出错方式写一条；判它做成了什么，不死抠它走哪条路。')}
      </p>
    </div>
  );
}
