/**
 * Online Eval — the owner's real sessions of the last week (backend
 * jobs/handlers/session_quality): code checks on every turn, a judge on a sample,
 * and the low-scored sessions with "agree / disagree" (the judge's calibration).
 * No expected answer; every online measure is in the watch tier.
 */
import { useCalibrate, useRunOnline, useSessionQuality, errorDetail, type OnlineCheck } from './api';
import { lastWeeks, onlineSeries } from './derive';
import { onlineLabel } from './OverviewTab';
import { Btn, ErrorState, Item, Loading, Spark, TierTag, Zone, pct, useEvalActions, useLang, when } from './ui';
import { useState } from 'react';

const DIM_OF: Record<string, { en: string; zh: string }> = {
  O1_LANGUAGE: { en: 'Responsibility', zh: '责任' }, O2_SILENT_TOOLS: { en: 'Responsibility', zh: '责任' },
  O3_CORRECTIONS: { en: 'Quality', zh: '质量' }, O4_GOAL: { en: 'Quality', zh: '质量' }, O5_TOOLS: { en: 'Quality', zh: '质量' },
};

export function OnlineTab() {
  const { lang, t } = useLang();
  const q = useSessionQuality();
  const run = useRunOnline();
  const { askSwarm, openSession } = useEvalActions();
  const calibrate = useCalibrate();
  const weeks = lastWeeks(new Date());
  if (q.isError) return <ErrorState />;
  if (q.isLoading || !q.data) return <Loading />;
  const on = q.data;
  const cal = on.calibration;

  const row = (id: string) => {
    const k: OnlineCheck | undefined = on.checks?.[id];
    if (!k) return null;
    const judged = id === 'O4_GOAL' || id === 'O5_TOOLS';
    const ok = k.checked != null && k.failed != null ? k.checked - k.failed : null;
    const head = judged ? (ok != null ? t(`${ok}/${k.checked} done well`, `${ok}/${k.checked} 个做好`) : '—') : pct(k.rate == null ? null : 1 - k.rate);
    const sub = id === 'O3_CORRECTIONS' ? t(`${ok}/${k.checked} sessions`, `${ok}/${k.checked} 个会话`) : judged ? t('sampled', '抽查') : t(`${ok}/${k.checked} turns`, `${ok}/${k.checked} 轮`);
    return <CheckRow key={id} id={id} head={head} sub={sub} check={k} weeks={weeks} points={onlineSeries(on, id)} dim={DIM_OF[id]?.[lang] ?? ''} />;
  };

  return (
    <div className="max-w-[900px] mx-auto px-7 pt-10 pb-24" data-testid="eval-online">
      <div className="flex items-end gap-6">
        <div className="flex-1">
          <div className="text-display font-semibold text-[var(--color-text)] tabular-nums">
            {on.sessions_checked}<small className="text-body font-normal text-[var(--color-text-muted)] ml-2">{t(`sessions · last ${on.window_days ?? 7} days`, `个会话 · 过去 ${on.window_days ?? 7} 天`)}</small>
          </div>
          <div className="text-small text-[var(--color-text-muted)] mt-1">
            {on.last_run ? when(on.last_run) : t('not run yet', '还没跑过')} · {t('weekly · no expected answer · only on your machine · every online measure is watch-only, never blocks a release',
              '每周一次 · 没有标准答案 · 只在你的机器上 · 线上项全部在观测档，从不拦上线')}
          </div>
        </div>
        <Btn onClick={() => run.mutate()} disabled={run.isPending} testId="online-run">{run.isPending ? t('Scoring…', '评分中…') : t('Score now', '现在评一次')}</Btn>
      </div>
      {run.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(run.error)}</div>}
      {on.sampler_error && <div className="text-small text-[var(--color-status-error)] mt-4">{t('The last run could not read the sessions: ', '上次没能读到会话：')}{on.sampler_error}</div>}

      <Zone first title={t('Checked on every turn', '每一轮都查')} sub={t('L1 code checks · the lines are the last 12 weeks', 'L1 确定性 · 线是过去 12 周')}>
        <div>{['O1_LANGUAGE', 'O2_SILENT_TOOLS', 'O3_CORRECTIONS'].map(row)}</div>
      </Zone>

      <Zone title={t('A judge on a sample of sessions', '抽查会话')} sub={t('L2 judge scores', 'L2 judge 打分')}>
        <p className="text-small text-[var(--color-text-muted)] -mt-1 mb-3">
          {t('Sessions you corrected and unusually long ones are sampled first, so the share done badly runs high and does not describe all use. The judge has no expected answer: it sees the question, the answer and the tools called.',
            '优先抽你纠正过的、或特别长的会话，所以没做好的比例偏高，不代表整体。judge 不知道标准答案，只看问题、回答和调了哪些工具。')}
        </p>
        {on.judge_error && <p className="text-small text-[var(--color-status-error)] mb-3">{t('The judge failed on every sampled session: ', 'judge 在所有抽查会话上都失败了：')}{on.judge_error}</p>}
        <div>{['O4_GOAL', 'O5_TOOLS'].map(row)}</div>
      </Zone>

      <Zone id="low" title={t('Low-scored sessions · do you agree with the judge?', '低分会话 · 你同意 judge 吗')} right={`${on.low_details.length}`}>
        <p className="text-small text-[var(--color-text-muted)] -mt-1 mb-3">
          {cal?.accepting
            ? t('Your answer is stored as calibration for the judge; the LLM Judges tab shows how often it agrees with you. Only the low ones need a look.',
              '你的判断会存下来，当作 judge 的校准数据；LLM Judges 页显示 judge 和你的一致率。只看低分的，不用看全部。')
            : t(`This batch does not take labels (${cal?.reason ?? 'no state yet'}); the next run will.`, `这一批不收校准（${cal?.reason === 'scored by an older judge prompt' ? '来自旧版 judge prompt' : cal?.reason ? '在校准记录之前打的分' : '还没有数据'}），下一次运行开始收。`)}
        </p>
        {on.low_details.length ? on.low_details.map((l) => {
          const sid = l.session_id ?? '';
          const label = cal?.labels?.[sid];
          return (
            <Item key={sid} tone="warning" testId="low-session" what={l.title || sid}
                  why={`${t('delivered', '交付')} ${l.goal_score ?? '—'} · ${t('tools', '工具')} ${l.tool_score ?? '—'} — ${l.reason ?? ''}`}
                  actions={<>
                    {(['agree', 'disagree'] as const).map((h) => (
                      <Btn key={h} testId={`calibrate-${h}`} disabled={!cal?.accepting || calibrate.isPending}
                           kind={label === h ? 'primary' : 'normal'}
                           title={cal?.accepting ? undefined : t('This batch does not take labels', '这一批不收校准')}
                           onClick={() => calibrate.mutate({ session_id: sid, human: h })}>
                        {h === 'agree' ? t('Agree', '同意') : t('Disagree', '不同意')}
                      </Btn>
                    ))}
                    {sid && <Btn kind="ghost" onClick={() => openSession(sid)}>{t('Open session', '打开会话')}</Btn>}
                    <Btn kind="ghost" onClick={() => askSwarm(t(`Review session ${sid}: where it went wrong, and whether it deserves a case.`, `复盘会话 ${sid} 哪里没做好，值不值得写成一条 case`))}>{t('Ask Swarm', '让 Swarm 复盘')}</Btn>
                  </>} />
          );
        }) : <div className="text-small text-[var(--color-text-dim)] py-3 border-t border-[var(--color-border)]">{t('None this week.', '本周没有。')}</div>}
        {calibrate.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(calibrate.error)}</div>}
      </Zone>
    </div>
  );
}

function CheckRow({ id, head, sub, check, weeks, points, dim }: {
  id: string; head: string; sub: string; check: OnlineCheck; weeks: string[]; points: Record<string, number>; dim: string;
}) {
  const { lang, t } = useLang();
  const { openSession } = useEvalActions();
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-[var(--color-border)] last:border-b" data-testid={`online-${id}`}>
      <button type="button" onClick={() => setOpen((o) => !o)} className="w-full grid grid-cols-[1fr_200px_130px_14px] gap-4 items-center py-3.5 text-left">
        <span className="text-body text-[var(--color-text)]">{onlineLabel[id]?.[lang] ?? id}
          <small className="flex gap-1.5 items-center text-small text-[var(--color-text-dim)] mt-0.5">{dim} · <TierTag tier="watch" /></small></span>
        <Spark weeks={weeks} series={[{ points }]} width={200} height={32} empty={t('weekly points start with the next runs', '每周一个点，从接下来的运行开始')} />
        <span className="text-right"><span className="text-body font-semibold tabular-nums text-[var(--color-text)]">{head}</span>
          <small className="block text-meta text-[var(--color-text-dim)]">{sub}</small></span>
        <span className={`text-[var(--color-text-dim)] transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="pb-3">
          {check.examples.length ? check.examples.map((e, i) => (
            <div key={i} className="grid grid-cols-[1fr_auto] gap-4 py-2 border-t border-[var(--color-border)] text-small">
              <div><div className="text-[var(--color-text)] font-semibold">{e.title || e.session_id}</div>
                <div className="text-[var(--color-text-muted)]">{e.reason}</div></div>
              {e.session_id && <Btn kind="ghost" onClick={() => openSession(e.session_id!)}>{t('Open session', '打开会话')}</Btn>}
            </div>
          )) : <div className="text-small text-[var(--color-text-dim)]">{t('No examples this week.', '本周没有例子。')}</div>}
        </div>
      )}
    </div>
  );
}
