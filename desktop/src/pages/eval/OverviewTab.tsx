/**
 * Overview — one question: can this version be used, and what needs me.
 *
 * Order (most important first): the verdict (the one thing that stands out) with a
 * sentence saying why and the four release checks as one line; "For you", the
 * follow-ups as a short list (problems with the evaluation itself, then signals about
 * the agent's behaviour), each one line with its evidence behind "Look"; the five
 * dimensions as one scoreboard; where the results come from, as a footer.
 * Design: docs/design/2026-10-26-eval-as-product.md §8 (revision 2026-10-28).
 */
import { useState } from 'react';
import { useEvalHealth, useEvalHistory, useGoldenSet, useSessionQuality, type FollowUp, type ReleaseCheck } from './api';
import {
  GATING, WATCH, checkById, formatSeconds, formatUsd, lastTwo, lastWeeks, metricSeries,
  onlineSeries, runSeries, tierCounts,
} from './derive';
import {
  Btn, DIM_LABEL, ErrorState, H4, LinkBtn, Loading, Row, SERIES_COLORS, SidePanel, Spark, Zone, caseName, pct, pick,
  statusLabel, useEvalActions, useGo, useLang, when, type Lang,
} from './ui';

const VERDICT_TEXT = {
  release: { en: 'Releasable.', zh: '可以放行。', color: 'var(--color-status-success)' },
  blocked: { en: 'Not releasable.', zh: '不放行。', color: 'var(--color-status-error)' },
  incomplete: { en: 'No verdict yet.', zh: '结论不完整。', color: 'var(--color-status-warning)' },
} as const;

/** A watched share below this is marked: the backend's standing-signal rule
 *  (core/eval_followups rule 8) lists a check that fails in 10% or more. */
const WATCH_MARK = 0.9;

export function OverviewTab() {
  const { lang, t } = useLang();
  const go = useGo();
  const health = useEvalHealth();
  const online = useSessionQuality();
  const [open, setOpen] = useState<FollowUp | null>(null);
  if (health.isError) return <ErrorState />;
  if (health.isLoading || !health.data) return <Loading />;
  const h = health.data;
  const needs = h.follow_ups ?? [];
  const evo = h.evolution ?? [];
  const verdict = h.release?.verdict;
  const v = verdict && Object.prototype.hasOwnProperty.call(VERDICT_TEXT, verdict) ? VERDICT_TEXT[verdict] : null;
  const total = needs.length + evo.length;

  return (
    <div className="max-w-[880px] mx-auto px-7 pt-10 pb-20" data-testid="eval-overview">
      <div className="text-small text-[var(--color-text-muted)]">
        {h.last_run ? t(`Last run ${when(h.last_run.triggered_at)} · ${h.last_run.triggered_by}`, `最近一次运行 ${when(h.last_run.triggered_at)} · ${h.last_run.triggered_by}`) : t('No run yet', '还没有运行')}
      </div>
      <div className="text-display leading-tight font-semibold mt-1" data-testid="overview-verdict">
        {v ? <span style={{ color: v.color }}>{v[lang]}</span>
          : h.last_run?.status === 'failed' ? <span style={{ color: 'var(--color-status-error)' }}>{t('The last run did not finish.', '上次运行没跑完。')}</span>
            : <span className="text-[var(--color-text-muted)]">{h.last_run ? t('A record from before release decisions.', '旧记录，没有放行结论。') : t('No run yet.', '还没有运行。')}</span>}
      </div>
      {h.release && <div className="text-body text-[var(--color-text-muted)] mt-1.5" title={h.release.reason} data-testid="overview-lede">{releaseLede(h.release.checks, lang)}</div>}
      {h.release && <ReleaseChecks checks={h.release.checks} lang={lang} onHistory={() => go('cases', 'history')} />}

      <Zone title={t('For you', '要你处理')} sub={total ? t(`${total} item${total > 1 ? 's' : ''}, most important first`, `${total} 件，重要的在前`) : undefined}>
        {total === 0 && <div className="text-small text-[var(--color-text-dim)] py-3 border-t border-[var(--color-border)]" data-testid="nothing-for-you">
          {t('Nothing. The checks ran, the cases are sound and no behaviour signal stands out.', '没有。检查都跑了，题目没有问题，也没有突出的行为信号。')}</div>}
        {needs.length > 0 && <Kind label={t('The evaluation itself', '评测本身')} />}
        {needs.map((f, i) => <FollowUpRow key={`n${i}`} f={f} lang={lang} onLook={() => setOpen(f)} />)}
        {evo.length > 0 && <Kind label={t('The agent’s behaviour', 'agent 的行为')} />}
        {evo.map((f, i) => <FollowUpRow key={`e${i}`} f={f} lang={lang} onLook={() => setOpen(f)} evolution />)}
        {total > 0 && <div className="border-t border-[var(--color-border)]" />}
      </Zone>

      <Zone title={t('Five dimensions', '五个维度')} sub={t('this week', '本周')}>
        <Dimensions />
      </Zone>

      <div className="mt-14 pt-4 border-t border-[var(--color-border)] flex flex-wrap gap-x-7 gap-y-1 text-small text-[var(--color-text-muted)]" data-testid="overview-lines">
        <FootLink label={t('Quick check', '快速检查')} onClick={() => go('cases')}
                  text={h.lines?.quick.total != null ? `${when(h.lines.quick.last_run)} · ${h.lines.quick.passed}/${h.lines.quick.total}` : t('not run yet', '还没跑过')} />
        <FootLink label={t('Offline cases', '离线考题')} onClick={() => go('cases', 'history')}
                  text={`${t('next', '下次')} ${when(h.lines?.full.next_run)}`} />
        <FootLink label={t('Live sessions', '真实会话')} onClick={() => go('online')}
                  text={`${online.data?.sessions_checked ?? h.lines?.online.sessions_checked ?? 0} ${t('sessions', '个会话')} · ${t('next', '下次')} ${when(h.lines?.online.next_run)}`} />
        <span className="ml-auto">
          {h.lines && t(`${h.lines.golden_set.total} cases · `, `${h.lines.golden_set.total} 条 case · `)}
          <LinkBtn className="underline underline-offset-2 hover:text-[var(--color-text)]" onClick={() => go('method')}>{t('How this works', '这些是怎么来的')}</LinkBtn>
        </span>
      </div>

      <SidePanel open={!!open} onClose={() => setOpen(null)} testId="follow-up-panel">
        {open && <FollowUpDetail f={open} lang={lang} onDone={() => setOpen(null)} />}
      </SidePanel>
    </div>
  );
}

function Kind({ label }: { label: string }) {
  return <div className="text-meta uppercase tracking-wide text-[var(--color-text-dim)] mt-5 mb-1 first:mt-1">{label}</div>;
}

function FootLink({ label, text, onClick }: { label: string; text: string; onClick: () => void }) {
  return (
    <button type="button" className="cursor-pointer group text-left" onClick={onClick}>
      <b className="text-[var(--color-text)] font-semibold group-hover:underline">{label}</b> {text}
    </button>
  );
}

/** The verdict's reason in one sentence of the user's language, from the four
 *  checks (the backend's ``reason`` is English; it stays as the tooltip). */
export function releaseLede(checks: ReleaseCheck[], lang: Lang): string {
  const zh = lang === 'zh';
  const red = checkById(checks, 'redline');
  const dims = checkById(checks, 'dimensions');
  const den = checkById(checks, 'denominator');
  const known = checkById(checks, 'record')?.known_issues?.length ?? 0;
  const parts: string[] = [];
  if (!red || !dims || !den) parts.push(zh ? '有一道关没有检查。' : 'A release check is missing.');
  if (red && !red.passed) parts.push(zh ? `${red.cases?.length ?? 0} 条红线没守住。` : `${red.cases?.length ?? 0} red line(s) broken.`);
  if (dims && !dims.passed) parts.push(zh ? `${dims.cases?.length ?? 0} 条拦上线的 case 没过。` : `${dims.cases?.length ?? 0} release-blocking case(s) fail.`);
  if (den && !den.passed) parts.push(zh ? `${den.cases?.length ?? 0} 条拦上线的 case 没有结果。` : `${den.cases?.length ?? 0} release-blocking case(s) have no result.`);
  if (!parts.length) {
    const n = den?.selected;
    parts.push(zh ? `拦上线的 ${n ?? ''} 条 case 全部通过。` : `All ${n ?? ''} release-blocking cases pass.`.replace('  ', ' '));
  }
  if (known) parts.push(zh ? `观测档有 ${known} 条没过，不拦。` : `${known} watched case(s) fail; they do not block.`);
  return parts.join(zh ? '' : ' ');
}

const CHECK_TEXT: Record<string, { en: string; zh: string; why: { en: string; zh: string } }> = {
  redline: { en: 'Red lines', zh: '红线', why: { en: 'one failure blocks the release', zh: '一票否决' } },
  dimensions: { en: 'Gates', zh: '门禁', why: { en: 'every release-blocking case passes, per dimension', zh: '分维过线，不做总分加权' } },
  denominator: { en: 'Nothing skipped', zh: '没有漏跑', why: { en: 'every case that has to run has a result', zh: '该跑的都有结果' } },
  record: { en: 'Recorded', zh: '已留痕', why: { en: 'why this version passes is written down', zh: '这一版为什么放行写下来了' } },
};

/** The four release checks in their fixed order, as one line; a failing one says
 *  which cases. */
export function ReleaseChecks({ checks, lang, onHistory }: { checks: ReleaseCheck[]; lang: Lang; onHistory?: () => void }) {
  const zh = lang === 'zh';
  const go = useGo();
  const gs = useGoldenSet();
  const byId = new Map((gs.data?.cases ?? []).map((c) => [c.id, c]));
  const names = (cases: { id: string; status?: string | null }[], withStatus: boolean) => cases.slice(0, 3).map((x, i) => (
    <span key={x.id}>{i > 0 && (zh ? '、' : ', ')}
      <LinkBtn className="inline" onClick={() => go('cases', `case:${x.id}`)}>{byId.get(x.id) ? caseName(byId.get(x.id)!, lang) : x.id}</LinkBtn>
      {withStatus && x.status ? ` (${statusLabel(x.status, lang)})` : ''}
    </span>
  ));
  const order = ['redline', 'dimensions', 'denominator', 'record'];
  const failing = order.map((id) => [id, checkById(checks, id)] as const).filter(([id, c]) => id !== 'record' && (!c || !c.passed));
  return (
    <div className="mt-3" data-testid="release-checks">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-small text-[var(--color-text-muted)]">
        {order.map((id) => {
          const c = checkById(checks, id);
          const ok = !!c?.passed;
          return (
            <span key={id} data-testid={`release-check-${id}`} data-passed={ok ? 'true' : 'false'} title={CHECK_TEXT[id].why[lang]}>
              <span style={{ color: ok ? 'var(--color-status-success)' : 'var(--color-status-error)' }}>{ok ? '✓' : '✗'}</span> {CHECK_TEXT[id][lang]}
            </span>
          );
        })}
        {onHistory && <LinkBtn className="ml-auto underline underline-offset-2 hover:text-[var(--color-text)]" onClick={onHistory}>{zh ? '运行记录' : 'Run history'}</LinkBtn>}
      </div>
      {failing.map(([id, c]) => (
        <div key={id} className="text-small text-[var(--color-status-error)] mt-1" data-testid={`release-check-${id}-detail`}>
          {CHECK_TEXT[id][lang]}: {!c ? (zh ? '没有检查' : 'not checked')
            : id === 'denominator' ? <>{`${c.with_result ?? '—'}/${c.selected ?? '—'} ${zh ? '有结果' : 'with a result'} · `}{names(c.cases ?? [], true)}</>
              : names(c.cases ?? [], false)}
        </div>
      ))}
    </div>
  );
}

const askPrompt = (f: FollowUp, lang: Lang, evolution: boolean) => {
  const what = pick(f.what, lang);
  const ids = f.case_ids.length ? f.case_ids.join(', ') : '';
  return lang === 'zh'
    ? `${evolution ? '分析这条行为信号' : '查一下这个评测问题'}：${what}${ids ? `（${ids}）` : ''}。${evolution ? '看证据，判断该改认知还是加 golden case。' : '先查 case 本身，再看 agent 的回答。'}`
    : `${evolution ? 'Analyse this behaviour signal' : 'Look into this evaluation problem'}: ${what}${ids ? ` (${ids})` : ''}. ${evolution ? 'Look at the evidence and decide: change cognition or add a golden case.' : 'Check the case itself first, then the agent’s answer.'}`;
};

function FollowUpRow({ f, lang, evolution, onLook }: { f: FollowUp; lang: Lang; evolution?: boolean; onLook: () => void }) {
  const zh = lang === 'zh';
  const gs = useGoldenSet();
  // a line that names cases: the headline carries the count, the names go underneath
  const listsCases = !!f.headline && pick(f.headline, lang) !== pick(f.what, lang) && f.case_ids.length > 0;
  const byId = new Map((gs.data?.cases ?? []).map((c) => [c.id, c]));
  const names = listsCases
    ? f.case_ids.slice(0, 3).map((id) => (byId.get(id) ? caseName(byId.get(id)!, lang) : id)).join(zh ? '；' : '; ')
      + (f.case_ids.length > 3 ? (zh ? ` 等 ${f.case_ids.length} 条` : ` and ${f.case_ids.length - 3} more`) : '')
    : '';
  return (
    <Row testId={evolution ? 'evolution-item' : 'needs-item'} onOpen={onLook}
         dot={f.severity === 'high' ? 'var(--color-status-error)' : undefined}
         what={pick(f.headline ?? f.what, lang)} sub={names || pick(f.action, lang)}
         right={<Btn onClick={onLook}>{zh ? '看一下' : 'Look'} ›</Btn>} />
  );
}

function FollowUpDetail({ f, lang, onDone }: { f: FollowUp; lang: Lang; onDone: () => void }) {
  const { askSwarm } = useEvalActions();
  const go = useGo();
  const gs = useGoldenSet();
  const zh = lang === 'zh';
  const evolution = f.kind === 'evolution';
  const online = f.rule === 4 || f.rule === 8;
  const byId = new Map((gs.data?.cases ?? []).map((c) => [c.id, c]));
  return (
    <div data-testid="follow-up-detail">
      <div className="text-small text-[var(--color-text-muted)]">{evolution ? (zh ? 'agent 的行为' : 'The agent’s behaviour') : (zh ? '评测本身' : 'The evaluation itself')}</div>
      <h3 className="text-title font-semibold text-[var(--color-text)] mt-1">{pick(f.headline ?? f.what, lang)}</h3>
      {f.why && <><H4>{zh ? '证据' : 'Evidence'}</H4><p className="text-small text-[var(--color-text)] whitespace-pre-wrap break-words">{f.why}</p></>}
      {pick(f.action, lang) && <><H4>{zh ? '该做什么' : 'What to do'}</H4><p className="text-small text-[var(--color-text)]">{pick(f.action, lang)}</p></>}
      {f.case_ids.length > 0 && <>
        <H4>{zh ? '涉及的 case' : 'Cases'}</H4>
        <div className="space-y-1">
          {f.case_ids.map((id) => (
            <LinkBtn key={id} className="block text-small text-[var(--color-text)]" onClick={() => { onDone(); go('cases', `case:${id}`); }}>
              {byId.get(id) ? caseName(byId.get(id)!, lang) : id} ›
            </LinkBtn>
          ))}
        </div>
      </>}
      {evolution && <p className="text-small text-[var(--color-text-dim)] mt-6">{zh
        ? '行为信号由确定性规则产出，不调模型；现在只展示，不会自动改规则或加 case。'
        : 'A behaviour signal comes from fixed rules, no model call. It is shown only; nothing changes a rule or adds a case on its own.'}</p>}
      <div className="flex gap-2 mt-6">
        <Btn kind="primary" onClick={() => askSwarm(askPrompt(f, lang, evolution))}>{zh ? '让 Swarm 处理' : 'Ask Swarm'}</Btn>
        {online && <Btn onClick={() => { onDone(); go('online'); }}>{zh ? '看真实会话' : 'Open live sessions'}</Btn>}
      </div>
    </div>
  );
}

// ─── five dimensions ───────────────────────────────────────────────────────

const ONLINE_LABEL: Record<string, { en: string; zh: string }> = {
  O1_LANGUAGE: { en: 'Answers in your language', zh: '用你提问的语言回答' },
  O2_SILENT_TOOLS: { en: 'No text before a tool call', zh: '调工具前不写多余的话' },
  O3_CORRECTIONS: { en: 'Sessions you did not correct', zh: '没被你纠正的会话' },
  O4_GOAL: { en: 'Judge sample: delivered', zh: 'judge 抽查：交付了' },
  O5_TOOLS: { en: 'Judge sample: sensible tools', zh: 'judge 抽查：工具合理' },
};
export const onlineLabel = ONLINE_LABEL;

interface Signal { label: string; share: number; cases?: boolean }

/** The five dimensions as one scoreboard. The big number is the release-blocking
 *  cases passed (cost and performance: the median per turn); the line under it is the
 *  weakest watched signal, named. Never an average of measures that count different
 *  things. A trend line appears only once two weeks have a value. */
function Dimensions() {
  const { lang, t } = useLang();
  const zh = lang === 'zh';
  const health = useEvalHealth();
  const history = useEvalHistory();
  const online = useSessionQuality();
  const weeks = lastWeeks(new Date());
  const runs = history.data ?? [];
  const summary = health.data?.dimension_summary;
  const on = online.data;

  const onlineSignals = (ids: string[]): Signal[] => ids.flatMap((id) => {
    const k = on?.checks?.[id];
    return k?.rate != null && k.checked ? [{ label: ONLINE_LABEL[id][lang], share: 1 - k.rate }] : [];
  });
  const caseCol = (dim: string, ids: string[]) => {
    const gate = tierCounts(summary, dim, GATING);
    const watch = tierCounts(summary, dim, WATCH);
    const signals: Signal[] = [
      ...(watch.total ? [{ label: t('watched cases', '观测的 case'), share: watch.passed / watch.total, cases: true }] : []),
      ...onlineSignals(ids),
    ];
    const weakest = signals.length ? signals.reduce((a, b) => (b.share < a.share ? b : a)) : null;
    const failedWatch = watch.total - watch.passed;
    const sub = !weakest ? '' : weakest.cases && failedWatch
      ? t(`${failedWatch} of ${watch.total} watched cases fail`, `观测的 ${watch.total} 条里 ${failedWatch} 条没过`)
      : `${weakest.label} ${pct(weakest.share)}`;
    const series = [runSeries(runs, dim, GATING), ...ids.map((id) => onlineSeries(on, id))];
    return {
      head: gate.total ? `${gate.passed}/${gate.total}` : weakest ? pct(weakest.share) : null,
      headWarn: gate.total > 0 && gate.passed < gate.total,
      headTitle: gate.total ? t('release-blocking cases passed', '拦上线的 case 通过数') : weakest?.label,
      sub, subMark: !!weakest && weakest.share < WATCH_MARK, series,
    };
  };
  const metricCol = (key: 'cost_p50_usd' | 'turn_p50_s') => {
    const pts = metricSeries(on, key);
    const [a, b] = lastTwo(pts, weeks);
    const now = on?.metrics?.[key] ?? b;
    const fmt = (x: number) => (key === 'cost_p50_usd' ? formatUsd(x) : formatSeconds(x, zh));
    return {
      head: now != null ? fmt(now) : null, headWarn: false,
      headTitle: key === 'cost_p50_usd' ? t('median cost per turn', '每轮成本中位数') : t('median time per turn', '每轮耗时中位数'),
      sub: a && b ? t(`vs last week ${b / a - 1 > 0 ? '+' : ''}${Math.round((b / a - 1) * 100)}%`, `比上周 ${b / a - 1 > 0 ? '+' : ''}${Math.round((b / a - 1) * 100)}%`)
        : now == null ? t('from the next online scoring', '下次线上评分后有') : t('median per turn', '每轮中位数'),
      subMark: false, series: [pts],
    };
  };
  const cols: [string, ReturnType<typeof caseCol>][] = [
    ['cognition', caseCol('cognition', [])],
    ['quality', caseCol('quality', ['O3_CORRECTIONS', 'O4_GOAL', 'O5_TOOLS'])],
    ['responsibility', caseCol('responsibility', ['O1_LANGUAGE', 'O2_SILENT_TOOLS'])],
    ['cost', metricCol('cost_p50_usd')],
    ['performance', metricCol('turn_p50_s')],
  ];
  const hasTrend = (s: Record<string, number>[]) => s.some((p) => weeks.filter((w) => p[w] != null).length >= 2);

  return (
    <div data-testid="five-dimensions">
      <div className="grid grid-cols-5 border-t border-[var(--color-border)]">
        {cols.map(([id, c]) => (
          <div key={id} className="pt-4 pr-4" data-testid={`dimension-${id}`}>
            <div className="text-body font-semibold text-[var(--color-text)]">{DIM_LABEL[id]?.[lang] ?? id}</div>
            <div className="text-small text-[var(--color-text-dim)] min-h-[36px]">{DIM_LABEL[id]?.q[lang]}</div>
            <div className={`mt-2 tabular-nums ${c.head ? 'text-title font-semibold' : 'text-body text-[var(--color-text-dim)]'}`}
                 style={c.head ? { color: c.headWarn ? 'var(--color-status-warning)' : 'var(--color-text)' } : undefined}
                 title={c.headTitle} data-testid={`dimension-${id}-head`}>
              {c.head ?? t('no data yet', '还没有数据')}
            </div>
            {c.sub && <div className="text-small text-[var(--color-text-muted)] mt-0.5" data-testid={`dimension-${id}-sub`}>
              {c.subMark && <span className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle" style={{ background: 'var(--color-status-warning)' }} />}
              {c.sub}
            </div>}
            {hasTrend(c.series) && <div className="mt-2">
              <Spark weeks={weeks} width={140} height={28}
                     series={c.series.map((p, i) => ({ points: p, scaled: id === 'cost' || id === 'performance', color: SERIES_COLORS[i % SERIES_COLORS.length] }))} />
            </div>}
          </div>
        ))}
      </div>
      <div className="text-small text-[var(--color-text-dim)] mt-4">
        {t('Big number: release-blocking cases passed. The line under it: the weakest watched signal. Trend lines appear once two weeks have a value. Tone and style are not scored.',
          '大数字是拦上线的 case 通过数，下面一行是最弱的观测信号。有两周的数据后显示趋势线。语气风格不打分。')}
      </div>
    </div>
  );
}
