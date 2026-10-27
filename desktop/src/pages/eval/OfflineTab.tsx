/**
 * Offline Eval — the checks before a release: fixed input, an expected answer,
 * "did this change break something". The quick check runs after a session ends (code
 * only); the offline cases run on the full-run schedule, whose next time comes from
 * /api/eval/health (a real agent in the shipped sample workspace).
 * A failing offline case drills down: session → trajectory → step. The release
 * record lists every full run's four checks and its report.
 */
import { useState } from 'react';
import { getApiBaseUrl } from '../../services/tauri';
import { openExternal } from '../../utils/openExternal';
import {
  errorDetail, useEvalHealth, useEvalHistory, useGoldenSet, useRunDetail, useRunFull, useRunProgress, useRunQuick,
  type EvalRun, type GoldenCase, type RunCaseResult,
} from './api';
import { checkById } from './derive';
import {
  Btn, ErrorState, Loading, StatusDot, Tag, TierTag, Zone, categoryName, dimName, isQuick, pick, splitPurpose,
  useEvalActions, useLang, when,
} from './ui';

export function OfflineTab() {
  const { t } = useLang();
  const health = useEvalHealth();
  const history = useEvalHistory();
  const gs = useGoldenSet();
  const runQuick = useRunQuick();
  const runFull = useRunFull();
  const { running: fullRunning, failed: fullFailed } = useRunProgress(runFull.data?.run_id);
  if (health.isError || gs.isError) return <ErrorState />;
  if (health.isLoading || gs.isLoading) return <Loading />;
  const cases = (gs.data?.cases ?? []).filter((c) => c.tier !== 'archived');
  const quickCases = cases.filter(isQuick);
  const offCases = cases.filter((c) => !isQuick(c));
  const lines = health.data?.lines;
  const runs = history.data ?? [];
  const fullRun = runs.find((r) => r.line === 'full');

  return (
    <div className="max-w-[900px] mx-auto px-7 pt-10 pb-24" data-testid="eval-offline">
      <p className="text-body text-[var(--color-text-muted)]">
        {t('Checks before a release: the input is fixed and there is an expected answer, so they answer "did this change break something". The fast ones run after a session ends, the slow ones on a schedule (the next run is shown below).',
          '上线前的检查：输入固定、有标准答案，回答"这次改动弄坏了什么吗"。快的在会话结束后跑，慢的按计划跑（下次时间见下方）。')}
      </p>

      <Zone first id="quick" title={t('Quick check', '快速检查')} sub={t('L1 code checks · no model call · red line + gate', 'L1 确定性 · 不调模型 · 红线 + 门禁')}
            right={<Btn onClick={() => runQuick.mutate()} disabled={runQuick.isPending} testId="run-quick">{runQuick.isPending ? t('Running…', '运行中…') : t('Run again', '再跑一次')}</Btn>}>
        <div className="text-display font-semibold tabular-nums text-[var(--color-text)]">
          {lines?.quick.total != null ? `${lines.quick.passed}/${lines.quick.total}` : '—'}
          <small className="text-body font-normal text-[var(--color-text-muted)] ml-2">{t('passed', '通过')}</small>
        </div>
        <div className="text-small text-[var(--color-text-muted)]">
          {when(lines?.quick.last_run)} · {t('runs after a session ends, in a temporary workspace that never touches your data',
            '会话结束后自动跑，在临时工作区，不碰你的数据')}
        </div>
        {runQuick.isError && <div className="text-small text-[var(--color-status-error)] mt-1">{errorDetail(runQuick.error)}</div>}
        <Days days={lines?.quick.days ?? []} />
        <div className="mt-4 border-t border-[var(--color-border)]"><Groups cases={quickCases} /></div>
      </Zone>

      <Zone id="exam" title={t('Offline cases', '离线考题')} sub={t('trajectory L1 + answer L2 judge · watch tier', '轨迹 L1 + 回答 L2 judge · 观测档')}
            right={<Btn onClick={() => runFull.mutate()} disabled={runFull.isPending || fullRunning} testId="run-full">{fullRunning ? t('Running…', '运行中…') : t('Run the full set now', '现在跑全量')}</Btn>}>
        <div className="text-small text-[var(--color-text-muted)]">
          {fullRun ? `${when(fullRun.triggered_at)} · ${fullRun.triggered_by}` : t('no full run yet', '还没有全量运行')} · {t('next', '下次')} {when(lines?.full.next_run)}
          {' · '}{t('a real agent answers in the sample workspace that ships with SwarmAI; calls the model', '真实 agent 在随包发布的样例工作区里答题，会调用模型')}
        </div>
        {runFull.isError && <div className="text-small text-[var(--color-status-error)] mt-1">{errorDetail(runFull.error)}</div>}
        {fullFailed && <div className="text-small text-[var(--color-status-error)] mt-1" data-testid="run-full-failed">{t('The run did not finish: ', '这次运行没跑完：')}{fullFailed}</div>}
        <OfflineResults runId={fullRun?.run_id ?? null} cases={offCases} />
      </Zone>

      <Zone id="release-record" title={t('Release record', '放行记录')} sub={t('each full run’s four checks and the reason it passed or not', '每次全量的四道关结果和放行理由')}>
        <ReleaseRecord runs={runs.filter((r) => r.line === 'full')} />
      </Zone>
      
    </div>
  );
}

function Days({ days }: { days: { date: string; passed: number | null; total: number | null; not_run: number | null; failed: number | null; timeout: number | null; verdict: string | null }[] }) {
  const { t } = useLang();
  if (!days.length) return <div className="text-small text-[var(--color-text-dim)] mt-3">{t('The day-by-day row starts with the next quick check.', '按天的记录从下一次快速检查开始。')}</div>;
  return (
    <div className="flex gap-1 mt-4 flex-wrap" data-testid="quick-days">
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

function Groups({ cases }: { cases: GoldenCase[] }) {
  const { lang, t } = useLang();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const by: Record<string, GoldenCase[]> = {};
  cases.forEach((c) => (by[c.category] = by[c.category] || []).push(c));
  return (
    <>
      {Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([cat, cs]) => {
        const bad = cs.filter((c) => c.last_result && c.last_result.status !== 'passed');
        const isOpen = open[cat] ?? bad.length > 0;
        return (
          <div key={cat} className="border-b border-[var(--color-border)]">
            <button type="button" onClick={() => setOpen({ ...open, [cat]: !isOpen })} className="w-full flex items-center gap-3 py-2.5 text-body text-left">
              <span className={`text-[var(--color-text-dim)] transition-transform ${isOpen ? 'rotate-90' : ''}`}>›</span>
              <span className="text-[var(--color-text)]">{categoryName(cat, lang)}</span>
              <span className="text-small text-[var(--color-text-dim)]">{cs.length}</span>
              <span className="ml-auto text-small" style={{ color: bad.length ? 'var(--color-status-warning)' : 'var(--color-status-success)' }}>
                {bad.length ? t(`${bad.length} not passing`, `${bad.length} 条没通过`) : t('all passing', '全部通过')}
              </span>
            </button>
            {isOpen && cs.map((c) => (
              <div key={c.id} className="flex items-center gap-2.5 py-1 pl-6 text-small">
                <StatusDot status={c.last_result?.status} /><TierTag tier={c.release_tier} />
                <span className="text-[var(--color-text)] truncate flex-1">{splitPurpose(pick(c.purpose, lang) || c.id)[0]}</span>
                <span className="text-meta text-[var(--color-text-dim)] font-mono">{c.id}</span>
              </div>
            ))}
          </div>
        );
      })}
    </>
  );
}

function OfflineResults({ runId, cases }: { runId: string | null; cases: GoldenCase[] }) {
  const { t } = useLang();
  const run = useRunDetail(runId);
  const byId = new Map((run.data?.cases ?? []).map((r) => [r.id, r]));
  const failing = cases.filter((c) => {
    const r = byId.get(c.id);
    return r ? r.status !== 'passed' : c.last_result && c.last_result.status !== 'passed';
  });
  const passing = cases.filter((c) => !failing.includes(c));
  return (
    <>
      <div className="text-display font-semibold tabular-nums text-[var(--color-text)] mt-3">
        {passing.length}/{cases.length}<small className="text-body font-normal text-[var(--color-text-muted)] ml-2">{t('passed', '通过')}</small>
      </div>
      {failing.length > 0 && <h3 className="text-body font-semibold text-[var(--color-text)] mt-6">{t('Not passing', '没通过')}</h3>}
      {failing.map((c) => <FailingCase key={c.id} c={c} r={byId.get(c.id)} />)}
      <h3 className="text-body font-semibold text-[var(--color-text)] mt-6 mb-1">{t('Passing', '通过')}</h3>
      <div className="border-t border-[var(--color-border)]"><Groups cases={passing} /></div>

    </>
  );
}

function FailingCase({ c, r }: { c: GoldenCase; r?: RunCaseResult }) {
  const { lang, t } = useLang();
  const { askSwarm } = useEvalActions();
  const [head, rest] = splitPurpose(pick(c.purpose, lang) || c.id);
  const steps = r?.steps ?? [];
  const firstBad = steps.findIndex((s) => s.is_error);
  const missing = (r?.expected_check ?? []).filter((e) => !e.found).map((e) => e.step);
  return (
    <div className="py-4 border-t border-[var(--color-border)]" data-testid={`failing-${c.id}`}>
      <div className="flex gap-1.5 mb-2"><TierTag tier={c.release_tier} /><Tag>{dimName(c.dimension, lang)}</Tag></div>
      <div className="text-body font-semibold text-[var(--color-text)]">{head}</div>
      {rest && <div className="text-small text-[var(--color-text-muted)]">{rest}</div>}
      <div className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-2 mt-3 text-small" data-testid="drilldown">
        <div className="text-[var(--color-text-muted)]"><b className="text-[var(--color-text)] block">{t('Session', '会话级')}</b>{t('the verdict', '整体结论')}</div>
        <div className="text-[var(--color-text)] break-words">{r ? `${r.status} · ${r.notes ?? ''}` : t('No result in the last full run.', '最近一次全量里没有结果。')}</div>
        <div className="text-[var(--color-text-muted)]"><b className="text-[var(--color-text)] block">{t('Trajectory', '轨迹级')}</b>{t('which step', '哪一步出错')}</div>
        <div>
          {r?.observed_trajectory?.length ? (
            <>
              <div className="flex flex-wrap gap-1">{r.observed_trajectory.map((s, i) => <Tag key={i} color={i === firstBad ? 'var(--color-status-error)' : undefined}>{s.split(' ')[0]}</Tag>)}</div>
              {missing.length > 0 && <div className="text-[var(--color-status-warning)] mt-1">{t('Expected but not seen: ', '期望出现但没出现：')}{missing.join(', ')}</div>}
            </>
          ) : <span className="text-[var(--color-text-dim)]">{t('This run kept no trajectory (runs before the drill-down was recorded).', '这次运行没有保存工具轨迹（记录下钻之前的运行）。')}</span>}
        </div>
        <div className="text-[var(--color-text-muted)]"><b className="text-[var(--color-text)] block">{t('Step', '步骤级')}</b>{t('what it did', '那一步做了什么')}</div>
        <div>
          {steps.length ? steps.slice(0, 12).map((s, i) => (
            <div key={i} className={`font-mono text-meta break-all ${s.is_error ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}>
              {i + 1}. {s.tool} {s.input} {s.output != null && <>→ {s.output}</>}
            </div>
          )) : <span className="text-[var(--color-text-dim)]">{c._origin === 'product'
            ? t('No tool steps in this run’s record.', '这次运行记录里没有工具步骤。')
            : t('Your own cases keep only tool names: their run records are tracked in your workspace, so what the agent read stays out.', '你自己的 case 只记工具名：运行记录在你的工作区里被 git 跟踪，agent 读到的内容不写进去。')}</span>}
          {r?.final_text && <details className="mt-2"><summary className="cursor-pointer text-[var(--color-text-muted)]">{t('The agent’s final answer', 'agent 的最终回答')}</summary>
            <pre className="whitespace-pre-wrap text-small text-[var(--color-text)] mt-1">{r.final_text}</pre></details>}
        </div>
      </div>
      <div className="flex gap-2 mt-3">
        <Btn kind="ghost" onClick={() => askSwarm(t(`Offline case ${c.id} did not pass. Check the case itself first, then the agent's answer.`, `离线考题 ${c.id} 没通过，先查 case 本身，再看 agent 的回答`))}>{t('Ask Swarm', '让 Swarm 查')}</Btn>
      </div>
      <div className="text-small text-[var(--color-text-dim)] mt-2">{t('How to fix a miss: check the denominator → drill down three levels → change one thing → re-run this case, then the regression set.', '不过线的修法：先查分母 → 三层下钻定位 → 只改一处 → 重跑这条，再跑回归')}</div>
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
      <a className="cursor-pointer hover:underline" onClick={() => setShowOld((v) => !v)}>{showOld ? t('Hide', '收起') : t('Show', '展开')}</a>
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
          <th className="font-normal">{t('Gate', '门禁')}</th><th className="font-normal">{t('Denominator', '分母')}</th>
          <th className="font-normal">{t('Reason', '放行理由')}</th><th /></tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.run_id} className="border-b border-[var(--color-border)] align-top">
              <td className="py-2 whitespace-nowrap text-[var(--color-text)]">{when(r.triggered_at)} · {r.triggered_by}</td>
              {r.release ? <>{cell(r, 'redline')}{cell(r, 'dimensions')}{cell(r, 'denominator')}</>
                : r.status === 'failed' ? <td colSpan={3} className="py-2 text-[var(--color-status-error)]">{t('did not finish', '没跑完')}</td>
                  : <td colSpan={3} className="py-2 text-[var(--color-text-dim)]">{t('old golden set, not comparable', '旧 golden set，不能比')}</td>}
              <td className="py-2 text-[var(--color-text-muted)] break-words">{r.release_reason ?? r.error ?? '—'}</td>
              <td className="py-2 text-right">{r.report && <a className="cursor-pointer text-[var(--color-primary)] hover:underline" onClick={() => openReport(r.report!)}>{t('Report', '报告')}</a>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {toggle}
    </>
  );
}
