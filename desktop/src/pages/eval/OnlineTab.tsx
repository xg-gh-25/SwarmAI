/**
 * Live sessions — the owner's real sessions of the last week (backend
 * jobs/handlers/session_quality). It leads with the one number a person feels (how
 * many sessions they had to correct), then the two code checks on every turn, the
 * judge's sample in one sentence, and the sessions it scored low: one line each,
 * opened in place for the full reason, "agree / disagree" (the judge's calibration,
 * only when this batch takes labels) and hand-offs. No expected answer; every
 * measure here is in the watch tier and never blocks a release.
 */
import { useState } from 'react';
import { useCalibrate, useEvalHealth, useRunOnline, useSessionQuality, errorDetail, type OnlineCheck, type OnlineLow } from './api';
import { onlineLabel } from './OverviewTab';
import { Btn, ErrorState, LinkBtn, Loading, Zone, pct, useEvalActions, useLang, when } from './ui';

/** Low-scored sessions shown before "N more". */
const LOW_SHOWN = 4;

/** Below this share a meter is marked (the backend's rule 8 lists a check failing in 10% or more). */
const MARK = 0.9;

export function OnlineTab() {
  const { lang, t } = useLang();
  const q = useSessionQuality();
  const health = useEvalHealth();
  const run = useRunOnline();
  const [allLow, setAllLow] = useState(false);
  if (q.isError) return <ErrorState />;
  if (q.isLoading || !q.data) return <Loading />;
  const on = q.data;
  const cal = on.calibration;
  const o3 = on.checks?.O3_CORRECTIONS;
  const corrected = o3?.checked ? o3.failed ?? null : null;
  const repeated = on.repeat_corrections?.repeats ?? 0;
  const next = health.data?.lines?.online.next_run;
  const goal = on.checks?.O4_GOAL;
  const tool = on.checks?.O5_TOOLS;
  const ok = (k?: OnlineCheck) => (k?.checked != null && k.failed != null ? k.checked - k.failed : null);
  const low = [...on.low_details].sort((a, b) => (a.goal_score ?? 1) + (a.tool_score ?? 1) - ((b.goal_score ?? 1) + (b.tool_score ?? 1)));

  return (
    <div className="max-w-[880px] mx-auto px-7 pt-10 pb-20" data-testid="eval-online">
      <div className="flex items-end gap-6">
        <div className="flex-1">
          <div className="text-small text-[var(--color-text-muted)]">
            {t(`Your ${on.sessions_checked} sessions of the last ${on.window_days ?? 7} days`, `你过去 ${on.window_days ?? 7} 天的 ${on.sessions_checked} 个会话`)}
            {' · '}{on.last_run ? t(`scored ${when(on.last_run)}`, `${when(on.last_run)} 评分`) : t('not scored yet', '还没评过')}
            {next && <> · {t('next', '下次')} {when(next)}</>}
          </div>
          <div className="text-display font-semibold text-[var(--color-text)] mt-1 leading-tight" data-testid="online-hero">
            {corrected != null
              ? <>{t('You corrected the agent in ', '你纠正了 agent ')}<span style={{ color: corrected ? 'var(--color-status-warning)' : 'var(--color-status-success)' }}>{t(`${corrected} of ${o3!.checked}`, `${o3!.checked} 个里的 ${corrected} 个`)}</span>{t(' sessions.', ' 会话。')}{repeated > 0 && <span className="text-[var(--color-status-warning)]" data-testid="online-repeats">{t(` ${repeated} repeated an earlier mistake.`, ` 其中 ${repeated} 次是同一个错误又犯了。`)}</span>}</>
              : t(`${on.sessions_checked} sessions checked.`, `查了 ${on.sessions_checked} 个会话。`)}
          </div>
        </div>
        <Btn onClick={() => run.mutate()} disabled={run.isPending} testId="online-run">{run.isPending ? t('Scoring…', '评分中…') : t('Score now', '现在评一次')}</Btn>
      </div>
      <p className="text-body text-[var(--color-text-muted)] mt-2">
        {t('No expected answer: the agent’s real work, checked by code on every turn and by a judge on a sample. Only on your machine; nothing here blocks a release.',
          '没有标准答案：看 agent 的真实工作，每一轮由代码检查，再抽一部分给 judge 看。只在你的机器上；这里的结果都不拦上线。')}
      </p>
      <Examples check={o3} />
      {run.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(run.error)}</div>}
      {on.sampler_error && <div className="text-small text-[var(--color-status-error)] mt-3">{t('The last run could not read the sessions: ', '上次没能读到会话：')}{on.sampler_error}</div>}

      <Zone title={t('Checked on every turn', '每一轮都查')} sub={t('by code', '代码检查')}>
        <div className="grid grid-cols-2 gap-8 border-t border-[var(--color-border)] pt-4">
          {['O1_LANGUAGE', 'O2_SILENT_TOOLS'].filter((id) => on.checks?.[id]).map((id) => <Meter key={id} id={id} check={on.checks?.[id]} />)}
        </div>
      </Zone>

      <Zone title={t(`A judge read ${goal?.checked ?? 0} sessions`, `judge 看了 ${goal?.checked ?? 0} 个会话`)}
            sub={t('corrected and long ones first, so it skews low', '先挑被纠正的和特别长的，所以偏低')}>
        {on.judge_error ? <p className="text-small text-[var(--color-status-error)]">{t('The judge failed on every sampled session: ', 'judge 在所有抽查会话上都失败了：')}{on.judge_error}</p>
          : !goal?.checked ? <p className="text-body text-[var(--color-text-muted)]" data-testid="online-judge-empty">{on.judge_failed ? t('The judge failed; nothing was scored.', 'judge 失败了，没有打出分。') : t('Nothing to score this time.', '这次没有可评的会话。')}</p>
            : <p className="text-body text-[var(--color-text-muted)]" data-testid="online-judge">
              <b className="text-[var(--color-text)] font-semibold">{t(`${ok(goal)} of ${goal.checked}`, `${goal.checked} 个里 ${ok(goal)} 个`)}</b>{t(' delivered what was asked', '交付了要的东西')}
              {tool?.checked ? <> · <b className="text-[var(--color-text)] font-semibold">{t(`${ok(tool)} of ${tool.checked}`, `${tool.checked} 个里 ${ok(tool)} 个`)}</b>{t(' used sensible tools.', '工具用得合理。')}</> : '.'}
            </p>}
        {!on.judge_error && !!on.judge_failed && !!goal?.checked && (
          <p className="text-small text-[var(--color-status-warning)] mt-1" data-testid="online-judge-partial">
            {t(`The judge failed on ${on.judge_failed} of ${on.judge_sampled ?? '?'} sampled sessions; they are not counted.`,
              `judge 在 ${on.judge_sampled ?? '?'} 个抽查会话里有 ${on.judge_failed} 个失败，没算进去。`)}
          </p>
        )}
      </Zone>

      <Zone id="low" title={t(`The ${low.length} it scored low`, `打了低分的 ${low.length} 个`)} sub={low.length ? t('open one to see where it went wrong', '点开看哪里没做好') : undefined}>
        {low.length > 0 && !cal?.accepting && (
          <p className="text-small text-[var(--color-text-dim)] -mt-1 mb-2" data-testid="calibration-closed">
            {t('Agree / disagree opens with the next run: ', '同意 / 不同意从下一次运行开始：')}
            {cal?.reason === 'scored by an older judge prompt' ? t('this batch was scored by an older judge prompt.', '这一批来自旧版 judge prompt。')
              : cal?.reason ? t('this batch was scored before labels were recorded.', '这一批在记录校准之前打的分。') : t('no state yet.', '还没有数据。')}
          </p>
        )}
        {low.length ? (allLow ? low : low.slice(0, LOW_SHOWN)).map((l) => <LowRow key={l.session_id ?? l.title} l={l} accepting={!!cal?.accepting} label={cal?.labels?.[l.session_id ?? '']} lang={lang} />)
          : <div className="text-small text-[var(--color-text-dim)] py-3 border-t border-[var(--color-border)]">{t('None this week.', '本周没有。')}</div>}
        {low.length > 0 && <div className="border-t border-[var(--color-border)]" />}
        {low.length > LOW_SHOWN && (
          <LinkBtn className="text-small text-[var(--color-text-muted)] mt-2" onClick={() => setAllLow((v) => !v)}>
            {allLow ? t('Show fewer', '收起') : t(`${low.length - LOW_SHOWN} more`, `还有 ${low.length - LOW_SHOWN} 个`)}
          </LinkBtn>
        )}
      </Zone>
    </div>
  );
}

/** A check's example sessions, behind one link. */
function Examples({ check }: { check?: OnlineCheck }) {
  const { t } = useLang();
  const { openSession } = useEvalActions();
  const [open, setOpen] = useState(false);
  if (!check?.examples?.length) return null;
  return (
    <>
      <LinkBtn className="text-small text-[var(--color-text-muted)] underline underline-offset-2 mt-1 inline-block" onClick={() => setOpen((o) => !o)}>
        {open ? t('Hide examples', '收起例子') : t(`${check.examples.length} examples`, `${check.examples.length} 个例子`)}
      </LinkBtn>
      {open && <div className="mt-2 space-y-2">
        {check.examples.map((e, i) => (
          <div key={i} className="text-small">
            {e.session_id ? <LinkBtn className="text-[var(--color-text)]" onClick={() => openSession(e.session_id!)}>{e.title || e.session_id}</LinkBtn>
              : <span className="text-[var(--color-text)]">{e.title}</span>}
            {e.reason && <div className="text-[var(--color-text-muted)]">{e.reason}</div>}
          </div>
        ))}
      </div>}
    </>
  );
}

function Meter({ id, check }: { id: string; check?: OnlineCheck }) {
  const { lang, t } = useLang();
  const share = check?.rate != null && check.checked ? 1 - check.rate : null;
  const ok = check?.checked != null && check.failed != null ? check.checked - check.failed : null;
  return (
    <div data-testid={`online-${id}`}>
      <div className="text-body text-[var(--color-text)]">{onlineLabel[id]?.[lang] ?? id}</div>
      <div className="h-1.5 rounded-full bg-[var(--color-hover)] mt-2 overflow-hidden">
        {share != null && <div className="h-full rounded-full" style={{ width: `${Math.round(share * 100)}%`, background: share < MARK ? 'var(--color-status-warning)' : 'var(--color-text-muted)' }} />}
      </div>
      <div className="text-title font-semibold tabular-nums text-[var(--color-text)] mt-1.5">{share != null ? pct(share) : <span className="text-body font-normal text-[var(--color-text-dim)]">{t('nothing to score', '没有可评的')}</span>}</div>
      {ok != null && !!check?.checked && <div className="text-small text-[var(--color-text-dim)]">{t(`${ok} of ${check.checked} turns`, `${check.checked} 轮里 ${ok} 轮`)}</div>}
      <Examples check={check} />
    </div>
  );
}

const firstSentence = (s?: string) => (s ?? '').match(/^(.+?[.。!！?？])(\s|$)/s)?.[1] ?? (s ?? '');

function LowRow({ l, accepting, label, lang }: { l: OnlineLow; accepting: boolean; label?: 'agree' | 'disagree'; lang: string }) {
  const { t } = useLang();
  const { askSwarm, openSession } = useEvalActions();
  const calibrate = useCalibrate();
  const [open, setOpen] = useState(false);
  const sid = l.session_id ?? '';
  return (
    <div className="border-t border-[var(--color-border)]" data-testid="low-session">
      <div className="flex gap-4 items-center py-3 hover:bg-[var(--color-hover)]/40">
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
                className="grid grid-cols-[1fr_auto] gap-4 items-center min-w-0 flex-1 text-left cursor-pointer">
          <span className="block min-w-0">
            <span className="block text-body font-semibold text-[var(--color-text)] truncate">{l.title || sid}</span>
            <span className={`block text-small text-[var(--color-text-muted)] ${open ? '' : 'truncate'}`}>{open ? l.reason : firstSentence(l.reason)}</span>
          </span>
          <span className="text-small text-[var(--color-text-dim)] tabular-nums whitespace-nowrap">
            {t('delivered', '交付')} {l.goal_score ?? '—'} · {t('tools', '工具')} {l.tool_score ?? '—'}
          </span>
        </button>
        {sid && <Btn onClick={() => openSession(sid)}>{t('Open session', '打开会话')}</Btn>}
      </div>
      {open && (
        <div className="flex flex-wrap gap-2 pb-3">
          {!accepting && label && (
            <span className="text-small text-[var(--color-text-muted)] self-center" data-testid="calibrate-label">
              {label === 'agree' ? t('You agreed with the judge.', '你同意了 judge。') : t('You disagreed with the judge.', '你不同意 judge。')}
            </span>
          )}
          {accepting && !!sid && (['agree', 'disagree'] as const).map((h) => (
            <Btn key={h} testId={`calibrate-${h}`} disabled={calibrate.isPending} kind={label === h ? 'primary' : 'normal'}
                 onClick={() => calibrate.mutate({ session_id: sid, human: h })}>
              {h === 'agree' ? t('Agree with the judge', '同意 judge') : t('Disagree', '不同意')}
            </Btn>
          ))}
          <Btn kind="ghost" onClick={() => askSwarm(lang === 'zh' ? `复盘会话 ${sid} 哪里没做好，值不值得写成一条 case` : `Review session ${sid}: where it went wrong, and whether it deserves a case.`)}>
            {t('Ask Swarm', '让 Swarm 复盘')}
          </Btn>
          {calibrate.isError && <div className="text-small text-[var(--color-status-error)]">{errorDetail(calibrate.error)}</div>}
        </div>
      )}
    </div>
  );
}
