/**
 * Overview — one decision: can this version be released, and what needs me.
 *
 * Order (most important first): the verdict sentence, the four release checks in
 * their fixed order, "needs you" (problems with the evaluation itself), Evolution
 * Suggestion (signals about the agent's behaviour; nothing acts on them yet), the
 * five dimensions with their trends, and where the results come from.
 */
import { useState } from 'react';
import { useEvalHealth, useEvalHistory, useSessionQuality, type FollowUp, type ReleaseCheck } from './api';
import {
  GATING, WATCH, checkById, formatSeconds, formatUsd, lastTwo, lastWeeks, metricSeries,
  onlineSeries, runSeries, tierCounts,
} from './derive';
import {
  Btn, DIM_LABEL, ErrorState, SERIES_COLORS, Item, LayerTag, Loading, Spark, Tag, TierTag, Zone, pct, pick, useEvalActions,
  useGo, useLang, when, type Lang,
} from './ui';

const VERDICT_TEXT = {
  release: { en: 'Can be released.', zh: '可以放行。', color: 'var(--color-status-success)' },
  blocked: { en: 'Not releasable.', zh: '不放行。', color: 'var(--color-status-error)' },
  incomplete: { en: 'No verdict yet.', zh: '结论不完整。', color: 'var(--color-status-warning)' },
} as const;

export function OverviewTab() {
  const { lang, t } = useLang();
  const go = useGo();
  const health = useEvalHealth();
  const online = useSessionQuality();
  if (health.isError) return <ErrorState />;
  if (health.isLoading || !health.data) return <Loading />;
  const h = health.data;
  const needs = h.follow_ups ?? [];
  const evo = h.evolution ?? [];
  const verdict = h.release?.verdict;
  const v = verdict && Object.prototype.hasOwnProperty.call(VERDICT_TEXT, verdict) ? VERDICT_TEXT[verdict] : null;
  const counts = [
    needs.length ? t(`${needs.length} thing${needs.length > 1 ? 's need' : ' needs'} you`, `有 ${needs.length} 件事要你看`) : t('nothing needs you', '没有要你看的事'),
    evo.length ? t(`${evo.length} evolution suggestion${evo.length > 1 ? 's' : ''}`, `${evo.length} 条进化建议`) : null,
  ].filter(Boolean).join(t(', ', '，'));

  return (
    <div className="max-w-[900px] mx-auto px-7 pt-10 pb-24" data-testid="eval-overview">
      <div className="text-small text-[var(--color-text-muted)]">
        {h.last_run ? `${when(h.last_run.triggered_at)} · ${h.last_run.triggered_by}` : t('No run yet', '还没有运行')}
      </div>
      <div className="text-display leading-tight font-semibold mt-1.5 text-[var(--color-text)]" data-testid="overview-verdict">
        {v ? <span style={{ color: v.color }}>{v[lang]}</span>
          : h.last_run?.status === 'failed' ? <span style={{ color: 'var(--color-status-error)' }}>{t('The last run did not finish. ', '上次运行没跑完。')}</span>
            : <span className="text-[var(--color-text-muted)]">{h.last_run ? t('A record from before release decisions. ', '旧记录，没有放行结论。') : t('No run yet. ', '还没有运行。')}</span>}
        {counts}
      </div>
      {h.release && <div className="text-body text-[var(--color-text-muted)] mt-2" title={h.release.reason}>{releaseLede(h.release.checks, lang)}</div>}

      {h.release && <ReleaseChecks checks={h.release.checks} lang={lang} onRun={() => go('offline', 'release-record')} />}

      <Zone title={t('Needs you', '需要你看')} sub={t('problems with the evaluation itself: a case, a run, a judge', '评测本身的问题：题目、运行、judge')}>
        {needs.length ? needs.map((f, i) => <FollowUpRow key={i} f={f} lang={lang} />)
          : <div className="text-small text-[var(--color-text-dim)] py-3 border-t border-[var(--color-border)]">{t('Nothing. The checks ran and the cases are sound.', '没有。检查都跑了，题目也没有问题。')}</div>}
      </Zone>

      <Zone title="Evolution Suggestion" sub={t('behaviour problems the evaluation found: where improvement comes in', '评测发现的 agent 行为问题，回流的入口')}
            right={evo.length ? t(`${evo.length}`, `${evo.length} 条`) : undefined}>
        <p className="text-small text-[var(--color-text-muted)] -mt-1 mb-3 max-w-[760px]">
          {t('Each is a signal with its evidence, produced by fixed rules, no model call. Later, distill reads them and decides where each goes: a change to cognition (rules, memory) or a new golden case. That is still being designed; for now they are shown and nothing changes automatically.',
            '每条是一个信号和它的证据，由确定性规则产出，不调模型。之后由 distill 读取，决定去向：改认知（规则、记忆），还是加一条 golden case。怎么消费还在设计，现在只展示，不自动改任何东西。')}
        </p>
        {evo.length ? evo.map((f, i) => <FollowUpRow key={i} f={f} lang={lang} evolution />)
          : <div className="text-small text-[var(--color-text-dim)] py-3 border-t border-[var(--color-border)]">{t('None this week.', '本周没有。')}</div>}
      </Zone>

      <Zone title={t('Five dimensions', '五个维度')} sub={t('the last 12 weeks; this week is not over', '过去 12 周，本周还没过完')}>
        <Dimensions />
      </Zone>

      <Zone title={t('Where the results come from', '结果从哪来')}>
        <div className="grid grid-cols-3 gap-6 text-small">
          <LineLink label={t('Quick check', '快速检查')} onClick={() => go('offline', 'quick')}
                    text={h.lines?.quick.total != null ? `${when(h.lines.quick.last_run)} · ${h.lines.quick.passed}/${h.lines.quick.total}` : t('not run yet', '还没跑过')} />
          <LineLink label={t('Offline cases', '离线考题')} onClick={() => go('offline', 'exam')}
                    text={`${when(h.lines?.full.last_run)} · ${t('next', '下次')} ${when(h.lines?.full.next_run)}`} />
          <LineLink label={t('Online scoring', '线上评分')} onClick={() => go('online')}
                    text={`${online.data?.sessions_checked ?? h.lines?.online.sessions_checked ?? 0} ${t('sessions', '个会话')} · ${t('next', '下次')} ${when(h.lines?.online.next_run)}`} />
        </div>
        <div className="text-small text-[var(--color-text-dim)] mt-5">
          {h.lines && t(`${h.lines.golden_set.product} product cases, ${h.lines.golden_set.private} of your own · `,
            `${h.lines.golden_set.product} 条产品 case，${h.lines.golden_set.private} 条你自己的 · `)}
          <a className="cursor-pointer underline-offset-2 hover:underline text-[var(--color-text-muted)]" onClick={() => go('cases')}>{t('Manage cases', '管理 case')}</a>
          {' · '}
          <a className="cursor-pointer underline-offset-2 hover:underline text-[var(--color-text-muted)]" onClick={() => go('guide')}>{t('First time here? Read the Guide', '第一次来？读 Guide，讲清 Eval-First')}</a>
        </div>
      </Zone>

    </div>
  );
}

/** The verdict in one line of the user's language, from the four checks (the
 *  backend's ``reason`` is English; it stays as the tooltip). */
export function releaseLede(checks: ReleaseCheck[], lang: Lang): string {
  const zh = lang === 'zh';
  const red = checkById(checks, 'redline');
  const dims = checkById(checks, 'dimensions');
  const den = checkById(checks, 'denominator');
  const known = checkById(checks, 'record')?.known_issues?.length ?? 0;
  const parts: string[] = [];
  const absent = zh ? '没有检查' : 'not checked';
  parts.push(!red ? `${zh ? '红线' : 'red line'} ${absent}` : red.passed ? (zh ? '红线没破' : 'No red line broken')
    : (zh ? `${red?.cases?.length ?? 0} 条红线没守住` : `${red?.cases?.length ?? 0} red line(s) broken`));
  parts.push(!dims ? `${zh ? '门禁' : 'gates'} ${absent}` : dims.passed ? (zh ? '门禁全过' : 'every gate passed')
    : (zh ? `${dims?.cases?.length ?? 0} 条门禁没过` : `${dims?.cases?.length ?? 0} gate case(s) failed`));
  parts.push(!den ? `${zh ? '分母' : 'denominator'} ${absent}` : den.passed ? (zh ? '该跑的都跑了' : 'everything that had to run ran')
    : (zh ? `${den?.cases?.length ?? 0} 条没有结果` : `${den?.cases?.length ?? 0} without a result`));
  const tail = known ? (zh ? `观测档有 ${known} 条没过，不拦。` : `${known} watch case(s) not passing; they do not block.`) : '';
  return `${parts.join(zh ? '，' : ', ')}${zh ? '。' : '. '}${tail}`;
}

function LineLink({ label, text, onClick }: { label: string; text: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="text-left group">
      <b className="text-[var(--color-text)] font-semibold group-hover:underline">{label} →</b>
      <div className="text-[var(--color-text-muted)] mt-0.5">{text}</div>
    </button>
  );
}

const CHECK_TEXT: Record<string, { en: string; zh: string; sub: { en: string; zh: string } }> = {
  redline: { en: 'Red line', zh: '红线', sub: { en: 'one failure blocks', zh: '一票否决' } },
  dimensions: { en: 'Per dimension', zh: '分维过线', sub: { en: 'no weighted total', zh: '不做总分加权' } },
  denominator: { en: 'Denominator', zh: '分母核对', sub: { en: 'nothing quietly left out', zh: '没跑的不能悄悄排除' } },
  record: { en: 'Record', zh: '口径留痕', sub: { en: 'why this version passes', zh: '这一版为什么放行' } },
};

export function ReleaseChecks({ checks, lang, onRun }: { checks: ReleaseCheck[]; lang: Lang; onRun?: () => void }) {
  const zh = lang === 'zh';
  const order = ['redline', 'dimensions', 'denominator', 'record'];
  const value = (c: ReleaseCheck | undefined, id: string): [string, string] => {
    const n = c?.cases?.length ?? 0;
    if (id === 'redline') return [zh ? `${n} 违例` : `${n} violation${n === 1 ? '' : 's'}`,
      n ? (c?.cases ?? []).map((x) => x.id).slice(0, 3).join(', ') : (zh ? '红线全部守住' : 'every red line held')];
    if (id === 'dimensions') return [n ? (zh ? `${n} 条门禁没过` : `${n} gate case${n === 1 ? '' : 's'} failed`) : (zh ? '门禁全过' : 'every gate passed'),
      zh ? '观测项不拦' : 'watch cases do not block'];
    if (id === 'denominator') return [`${c?.with_result ?? '—'}/${c?.selected ?? '—'} ${zh ? '有结果' : 'with a result'}`,
      n ? (c?.cases ?? []).map((x) => `${x.id} ${x.status ?? ''}`).slice(0, 3).join(', ') : (zh ? '红线和门禁项都跑了' : 'every red-line and gate case ran')];
    const known = c?.known_issues?.length ?? 0;
    return [zh ? '已记录' : 'Recorded', known ? (zh ? `带着 ${known} 条观测项的已知问题` : `carries ${known} watch case(s) as known issues`) : (zh ? '没有已知问题' : 'no known issues')];
  };
  return (
    <div className="mt-7" data-testid="release-checks">
      <div className="grid grid-cols-4 gap-4">
        {order.map((id, i) => {
          const c = checkById(checks, id);
          const ok = c ? c.passed && !(id === 'record' && (c.known_issues?.length ?? 0) > 0) : false;
          const [v, d] = value(c, id);
          return (
            <div key={id} data-testid={`release-check-${id}`} data-passed={c?.passed ? 'true' : 'false'}>
              <div className="h-[3px] rounded mb-3" style={{ background: c?.passed ? (ok ? 'var(--color-status-success)' : 'var(--color-status-warning)') : 'var(--color-status-error)' }} />
              <div className="text-small text-[var(--color-text-muted)]">
                <b className="text-[var(--color-text-dim)] font-semibold mr-1.5">{i + 1}</b>{CHECK_TEXT[id][lang]} · {CHECK_TEXT[id].sub[lang]}
              </div>
              <div className="text-title font-semibold text-[var(--color-text)] mt-0.5 tabular-nums">{v}</div>
              <div className="text-small text-[var(--color-text-dim)] mt-0.5">{d}</div>
            </div>
          );
        })}
      </div>
      <div className="text-small text-[var(--color-text-dim)] mt-3">
        {zh ? '四道关按顺序判，第一道没过的关决定结论' : 'The four checks run in this order; the first that fails decides'}
        {onRun && <> · <a className="cursor-pointer hover:underline" onClick={onRun}>{zh ? '看放行记录' : 'release record'}</a></>}
      </div>
    </div>
  );
}

function FollowUpRow({ f, lang, evolution }: { f: FollowUp; lang: Lang; evolution?: boolean }) {
  const { askSwarm } = useEvalActions();
  const go = useGo();
  const zh = lang === 'zh';
  const what = pick(f.what, lang);
  const action = pick(f.action, lang);
  const online = f.rule === 4;
  return (
    <Item tone={evolution ? 'evolution' : f.severity === 'high' ? 'error' : 'warning'} testId={evolution ? 'evolution-item' : 'needs-item'}
          what={what} why={[f.why, action].filter(Boolean).join(' — ')}
          tags={evolution ? <Tag>{zh ? '去向待定：改认知 / 新 golden case' : 'Destination open: cognition / new golden case'}</Tag> : undefined}
          actions={<>
            <Btn onClick={() => go(online ? 'online' : 'offline', online ? undefined : 'exam')}>
              {evolution ? (zh ? '看证据' : 'Evidence') : f.case_ids.length ? (zh ? `看这 ${f.case_ids.length} 条` : `See ${f.case_ids.length}`) : (zh ? '去看' : 'Open')}
            </Btn>
            <Btn kind="ghost" onClick={() => askSwarm(zh
              ? `${evolution ? '分析这条进化建议' : '查一下这个评测问题'}：${what}${f.case_ids.length ? `（${f.case_ids.join(', ')}）` : ''}。${evolution ? '看证据，判断该改认知还是加 golden case。' : '先查 case 本身，再看 agent 的回答。'}`
              : `${evolution ? 'Analyse this evolution suggestion' : 'Look into this evaluation problem'}: ${what}${f.case_ids.length ? ` (${f.case_ids.join(', ')})` : ''}. ${evolution ? 'Look at the evidence and decide: change cognition or add a golden case.' : 'Check the case itself first, then the agent’s answer.'}`)}>
              {zh ? (evolution ? '让 Swarm 分析' : '让 Swarm 查') : (evolution ? 'Ask Swarm' : 'Ask Swarm')}
            </Btn>
          </>} />
  );
}

// ─── five dimensions ───────────────────────────────────────────────────────

const ONLINE_LABEL: Record<string, { en: string; zh: string; per: { en: string; zh: string } }> = {
  O1_LANGUAGE: { en: 'Answers in the language you asked in', zh: '用你提问的语言回答', per: { en: 'online, every turn', zh: '线上，每一轮' } },
  O2_SILENT_TOOLS: { en: 'No extra text before tool calls', zh: '调工具前不写多余的话', per: { en: 'online, every turn', zh: '线上，每一轮' } },
  O3_CORRECTIONS: { en: 'Sessions you did not correct', zh: '没被你纠正的会话', per: { en: 'online, per session', zh: '线上，按会话' } },
  O4_GOAL: { en: 'Judge sample: delivered', zh: 'judge 抽查：交付了没有', per: { en: 'online, a sample per run', zh: '线上，每次运行抽查' } },
  O5_TOOLS: { en: 'Judge sample: sensible tools', zh: 'judge 抽查：工具选得合理', per: { en: 'online, a sample per run', zh: '线上，每次运行抽查' } },
};
export const onlineLabel = ONLINE_LABEL;

interface Measure {
  label: string; sub: string; points: Record<string, number>; tier: 'gating' | 'watch';
  layer: 'L1' | 'L2'; value: string; latest?: number; unit?: 'usd' | 's';
}

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

  const caseMeasures = (dim: string): Measure[] => {
    const out: Measure[] = [];
    for (const [tiers, tier] of [[GATING, 'gating'], [WATCH, 'watch']] as const) {
      const c = tierCounts(summary, dim, tiers);
      if (!c.total) continue;
      out.push({
        label: tier === 'gating' ? t(`Release-blocking cases (${c.total})`, `拦上线的 case ${c.total} 条`) : t(`Watched cases (${c.total})`, `观测的 case ${c.total} 条`),
        sub: tier === 'gating' ? t('red line + gate, code-checked', '红线 + 门禁，代码判') : t('includes the offline cases a judge scores', '含 judge 判的离线考题'),
        points: runSeries(runs, dim, tiers), tier, layer: tier === 'gating' ? 'L1' : 'L2',
        value: `${c.passed}/${c.total}`, latest: c.passed / c.total,
      });
    }
    return out;
  };
  const onlineMeasure = (check: string): Measure | null => {
    const pts = onlineSeries(on, check);
    const k = on?.checks?.[check];
    if (!k && !Object.keys(pts).length) return null;
    const [, b] = lastTwo(pts, weeks);
    return {
      label: ONLINE_LABEL[check][lang], sub: ONLINE_LABEL[check].per[lang], points: pts, tier: 'watch',
      layer: check === 'O4_GOAL' || check === 'O5_TOOLS' ? 'L2' : 'L1',
      value: k?.rate != null ? pct(1 - k.rate) : pct(b), latest: k?.rate != null ? 1 - k.rate : b,
    };
  };
  const dims: { id: string; measures: Measure[]; lower?: boolean }[] = [
    { id: 'cognition', measures: caseMeasures('cognition') },
    { id: 'quality', measures: [...caseMeasures('quality'), ...['O3_CORRECTIONS', 'O4_GOAL', 'O5_TOOLS'].map(onlineMeasure).filter((m): m is Measure => !!m)] },
    { id: 'responsibility', measures: [...caseMeasures('responsibility'), ...['O1_LANGUAGE', 'O2_SILENT_TOOLS'].map(onlineMeasure).filter((m): m is Measure => !!m)] },
    { id: 'cost', lower: true, measures: [{ label: t('Cost per turn (median)', '每轮成本（中位）'), sub: t('online, read from usage records', '线上，用量记录的读数'), points: metricSeries(on, 'cost_p50_usd'), tier: 'watch', layer: 'L1', value: formatUsd(on?.metrics?.cost_p50_usd), unit: 'usd' }] },
    { id: 'performance', lower: true, measures: [{ label: t('Time per turn (median)', '每轮耗时（中位）'), sub: t('online, read from message times', '线上，消息时间的读数'), points: metricSeries(on, 'turn_p50_s'), tier: 'watch', layer: 'L1', value: formatSeconds(on?.metrics?.turn_p50_s, zh), unit: 's' }] },
  ];

  return (
    <div data-testid="five-dimensions">
      {dims.map((d) => <DimensionRow key={d.id} id={d.id} measures={d.measures} lower={d.lower} weeks={weeks} />)}
      <div className="text-small text-[var(--color-text-dim)] mt-3">
        {t('Tone and style are not scored: there is no stable way to score them, so no number is given.', '语气风格不打分：没有稳定的评分方法，属于主观项，默认不给数字。')}
      </div>
    </div>
  );
}

function DimensionRow({ id, measures, lower, weeks }: { id: string; measures: Measure[]; lower?: boolean; weeks: string[] }) {
  const { lang, t } = useLang();
  const [open, setOpen] = useState(false);
  const label = DIM_LABEL[id];
  let head = t('no data yet', '还没有数据');
  let small = '';
  let warn = false;
  let smallTitle = '';
  if (lower) {
    const m = measures[0];
    const [a, b] = lastTwo(m.points, weeks);
    head = m.value !== '—' ? m.value : b != null ? (m.unit === 'usd' ? formatUsd(b) : formatSeconds(b, lang === 'zh')) : head;
    small = a && b ? t(`vs last week ${b / a - 1 > 0 ? '+' : ''}${Math.round((b / a - 1) * 100)}%`, `比上周 ${b / a - 1 > 0 ? '+' : ''}${Math.round((b / a - 1) * 100)}%`) : '';
  } else {
    const gating = measures.filter((m) => m.tier === 'gating');
    const watch = measures.filter((m) => m.tier === 'watch' && m.latest != null);
    const weakest = watch.length ? watch.reduce((x, y) => ((y.latest ?? 1) < (x.latest ?? 1) ? y : x)) : null;
    if (gating.length) {
      head = t(`Blocking ${gating[0].value}`, `拦上线 ${gating[0].value}`);
      const [p, tot] = gating[0].value.split('/').map(Number);
      warn = p < tot;
      small = weakest ? t(`weakest watched ${pct(weakest.latest)}`, `观测最低 ${pct(weakest.latest)}`) : '';
      smallTitle = weakest?.label ?? '';
    } else if (weakest) {
      head = t(`Weakest watched ${pct(weakest.latest)}`, `观测最低 ${pct(weakest.latest)}`);
      small = t('watch only, never blocks', '只看不拦');
      smallTitle = weakest.label;
    }
  }
  const lines = measures.filter((m) => Object.keys(m.points).length);
  return (
    <div className="border-t border-[var(--color-border)] last:border-b" data-testid={`dimension-${id}`}>
      <button type="button" onClick={() => setOpen((o) => !o)}
              className="w-full grid grid-cols-[72px_1fr_220px_150px_14px] gap-4 items-center py-3.5 text-left hover:bg-[var(--color-hover)]/40">
        <span className="text-title font-semibold text-[var(--color-text)]">{label?.[lang] ?? id}</span>
        <span className="text-body text-[var(--color-text)]">{label?.q[lang]}<small className="block text-small text-[var(--color-text-dim)]">{label?.small[lang]}</small></span>
        <Spark weeks={weeks} series={lines.map((m, i) => ({ points: m.points, scaled: lower, color: lower ? 'var(--color-primary)' : SERIES_COLORS[i % SERIES_COLORS.length] }))}
               empty={t('history starts with the next runs', '历史从接下来的运行开始累积')} />
        <span className="text-right">
          <span className={`text-body font-semibold tabular-nums ${warn ? 'text-[var(--color-status-warning)]' : 'text-[var(--color-text)]'}`} data-testid={`dimension-${id}-head`}>{head}</span>
          {small && <small className="block text-meta text-[var(--color-text-dim)] truncate" title={smallTitle || undefined}>{small}</small>}
        </span>
        <span className={`text-[var(--color-text-dim)] transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="pb-4 pl-[88px] space-y-3">
          {measures.map((m, i) => (
            <div key={i} className="grid grid-cols-[1fr_420px_90px] gap-4 items-center">
              <div className="text-small text-[var(--color-text)]">
                {m.label}
                <div className="flex gap-1.5 mt-1">{m.tier === 'gating' ? <TierTag tier="gate" /> : <TierTag tier="watch" />}<LayerTag layer={m.layer} /></div>
                <small className="block text-meta text-[var(--color-text-dim)] mt-0.5">{m.sub}</small>
              </div>
              <Spark weeks={weeks} series={[{ points: m.points, scaled: !!m.unit }]} width={420} height={56} big
                     empty={t('no weekly history yet', '还没有按周的历史')} />
              <div className="text-right text-body font-semibold tabular-nums text-[var(--color-text)]">{m.value}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
