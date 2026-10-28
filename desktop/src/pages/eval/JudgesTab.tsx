/**
 * Judges (the last part of Method) — where the L2 scores come from: the two judges as they run (model,
 * prompt, settings, what each sees and returns), how much of the release depends
 * on a judge, the methodology's requirements for a judge against what we do, and
 * how often the online judge agrees with the owner.
 */
import { errorDetail, useJudgeModelSetting, useJudges, type JudgeInfo } from './api';
import { Btn, ErrorState, KV, LinkBtn, Loading, Zone, pct, useGo, useLang } from './ui';

export function JudgesTab() {
  const { t } = useLang();
  const go = useGo();
  const q = useJudges();
  const { query: settings, save } = useJudgeModelSetting();
  if (q.isError) return <ErrorState />;
  if (q.isLoading || !q.data) return <Loading />;
  const J = q.data;
  const offline = J.judges.find((j) => j.id === 'offline_decision');
  const online = J.judges.find((j) => j.id === 'online_session');
  const models = Array.from(new Set([J.judge_model, ...(settings.data?.available_models ?? [])].filter(Boolean)));
  const agreement = online?.calibration?.agreement;
  const temps = Array.from(new Set(J.judges.map((j) => j.temperature))).join(' / ') || '—';
  const fixedTemp = J.judges.length > 0 && J.judges.every((j) => j.temperature === 0);
  const sameFamily = /claude/i.test(J.judge_model) && /claude/i.test(J.agent_model ?? '');

  const reqs: [string, string, 'ok' | 'gap' | 'no', string][] = [
    [t('Fixed scorer: model, prompt and temperature fixed', '固定评分器：模型、prompt、温度都固定'),
      t(`every judge fixes model and prompt; temperature ${temps}`, `每个 judge 都固定了模型和 prompt；temperature ${temps}`),
      fixedTemp ? 'ok' : 'gap', fixedTemp ? t('done', '做到') : t('temperature not 0', '温度不是 0')],
    [t('What code can check never goes to a judge', '能用代码判的绝不交给 judge'),
      t('offline cases check the trajectory first, the judge only after; online O1–O3 are code checks', '离线考题先做轨迹检查，过了才调 judge；线上 O1–O3 全是代码检查'), 'ok', t('done', '做到')],
    [t('Uncalibrated scores stay in the watch tier', '未校准的分只进观测档，不做放行依据'),
      t('judged offline answers and the online sample are all watch', 'judge 判的离线回答和线上抽查都在观测档'), J.gating_judged === 0 ? 'ok' : 'gap', J.gating_judged === 0 ? t('done', '做到') : t('not yet', '没做到')],
    [t('Subjective items are not scored', '主观项默认不打分'), t('tone and style get no number', '语气风格不给数字'), 'ok', t('done', '做到')],
    [t('A judge error is not a score', 'judge 出错不算分'), t('a failed judge call counts as not measured, never pass or fail', 'judge 调用失败记为"没测成"，不当作通过或不通过'), 'ok', t('done', '做到')],
    [t('Judge and agent from different model families', 'judge 和被测 agent 用不同的模型家族'),
      `judge ${J.judge_model}, agent ${J.agent_model ?? '—'}`, sameFamily ? 'gap' : 'ok', sameFamily ? t('same family', '同一家族') : t('done', '做到')],
    [t('Agree with human labels before trusting it', '先和人工判定对齐再信它'),
      online?.calibration?.labels ? t(`${online.calibration.labels} labels, agreement ${pct(agreement)}`, `${online.calibration.labels} 条标注，一致率 ${pct(agreement)}`) : t('no human labels yet: the agree / disagree buttons in Live sessions collect them', '还没有人工判定；真实会话里低分会话的"同意 / 不同意"就是为这个准备的'),
      online?.calibration?.labels ? 'gap' : 'no', online?.calibration?.labels ? t('collecting', '收集中') : t('no data', '没有数据')],
    [t('Reduce judge bias', '减少 judge 的偏差'), t('one judge, no swapped-order scoring, no panel of models; longer answers may score higher', '只有一个 judge，没有双向打分，也没有多模型评审团；偏长的回答可能被打高分'), 'no', t('not done', '没做')],
  ];
  const stateColor = { ok: 'var(--color-status-success)', gap: 'var(--color-status-warning)', no: 'var(--color-status-error)' };

  return (
    <section id="judges" className="mt-16 pt-8 border-t border-[var(--color-border)]" data-testid="eval-judges">
      <div className="text-small text-[var(--color-text-muted)]">{t('Where the L2 scores come from', 'L2 的分数从这里来')}</div>
      <div className="text-display font-semibold text-[var(--color-text)] mt-1">{t(`${J.judges.length} judges, both on ${J.judge_model}`, `${J.judges.length} 个 judge，都用 ${J.judge_model}`)}</div>
      <p className="text-body text-[var(--color-text-muted)] mt-2">
        {t('A judge can be wrong too: with an outdated standard it scores a correct answer zero. So what a judge scores stays in the watch tier until it agrees with your own judgement; only then may it block a release.',
          'judge 自己也可能判错：判官用了过期的标准，agent 做对了也会被判零分。所以 judge 判的项目一律先在观测档，等和你的判断对齐了，才有资格拿去拦上线。')}
      </p>
      <div className="grid grid-cols-2 gap-8 mt-7" data-testid="judge-ratio">
        <div><b className="text-display font-semibold tabular-nums text-[var(--color-text)]">{J.gating_judged}/{J.gating_total}</b>
          <div className="text-small text-[var(--color-text-muted)]">{t('release-blocking criteria (red line + gate) that use a judge. The lower, the more solid the criteria.', '拦上线的判据（红线 + 门禁）里用 judge 的。这个比例越低，判据定义得越扎实。')}</div></div>
        <div><b className="text-display font-semibold tabular-nums text-[var(--color-text)]">{J.cases_judged}/{J.cases_total}</b>
          <div className="text-small text-[var(--color-text-muted)]">{t('all cases that use a judge: the offline answers, in the watch tier.', '全部 case 里用 judge 的：离线考题的回答判定，都在观测档。')}</div></div>
      </div>

      <div className="flex items-center gap-3 mt-7 text-small">
        <span className="text-[var(--color-text-muted)]">{t('Judge model', 'judge 模型')}</span>
        <select data-testid="judge-model" className="px-2 py-1 rounded-md border border-[var(--color-border)] bg-transparent text-[var(--color-text)]"
                value={settings.data?.eval_judge_model ?? J.judge_model} disabled={save.isPending}
                onChange={(e) => save.mutate(e.target.value)}>
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <span className="text-[var(--color-text-dim)]">{t('takes effect on the next run; scores before and after a change are not comparable', '下次运行开始生效；换前换后的分数不能直接比')}</span>
      </div>
      {save.isError && <div className="text-small text-[var(--color-status-error)] mt-1">{errorDetail(save.error)}</div>}

      {offline && <JudgeCard j={offline} model={J.judge_model} title={t('Offline decision judge', '离线判决 judge')} where={t('step two of an offline case', '离线考题的第二步')} rows={[
        [t('Judges', '判什么'), t('whether the agent’s final answer in the sample workspace meets the case’s rubric', 'agent 在样例工作区里的最终回答，是否满足这条 case 的判定标准')],
        [t('Sees', '拿到什么'), t('the rubric and the final answer (first 12000 characters); no tool record, no rule files', '判定标准 + agent 的最终回答（前 12000 字）；不给工具记录，不给规则文件')],
        [t('Returns', '给出什么'), t('pass / fail, a confidence, one line on what it actually recommended', '通过 / 不通过 + 置信度 + 一句话说明它实际推荐了什么')],
        [t('Called', '什么时候调'), t('only after the trajectory check passes', '只在轨迹检查通过之后；轨迹没过直接判不通过')],
        [t('Used by', '用在'), (offline.case_ids ?? []).join(', ') || '—'],
      ]} />}
      {online && <JudgeCard j={online} model={J.judge_model} title={t('Online session judge', '线上会话 judge')} where={t('the online sample', '线上评分的抽查')} rows={[
        [t('Judges', '判什么'), t('one exchange of a real session: delivered (goal) and sensible tools (tool), 0–1 each', '一个真实会话里的一问一答：交付了没有（goal）、工具选得对不对（tool），各 0–1 分')],
        [t('Sees', '拿到什么'), t('your question, that turn’s final answer and the tools it called; no expected answer', '你的问题 + 这一轮的最终回答 + 这一轮调用的工具名；没有标准答案')],
        [t('Returns', '给出什么'), t(`two scores, the dimension of the main weakness, a reason in your language; below ${online.low_threshold ?? 0.6} counts as done badly`, `两个分数 + 主要问题归到哪一维 + 一句用你的语言写的理由；低于 ${online.low_threshold ?? 0.6} 记为没做好`)],
        [t('Called', '什么时候调'), t(`at each online scoring run, on up to ${online.sample_cap ?? 10} sessions, corrected or unusually long ones first`, `每次线上评分时，最多 ${online.sample_cap ?? 10} 个会话，优先你纠正过的或特别长的`)],
        [t('Prompt version', 'prompt 版本'), online.prompt_sha ?? '—'],
      ]} />}

      <Zone title={t('Against the methodology’s requirements for a judge', '对照方法论对 judge 的要求')}>
        <table className="w-full text-small" data-testid="judge-requirements">
          <thead><tr className="text-left text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
            <th className="py-2 font-normal">{t('Requirement', '要求')}</th><th className="font-normal">{t('Now', '现在')}</th><th className="font-normal text-right">{t('State', '状态')}</th></tr></thead>
          <tbody>{reqs.map(([a, b, s, label]) => (
            <tr key={a} className="border-b border-[var(--color-border)] align-top">
              <td className="py-2 pr-4 text-[var(--color-text)]">{a}</td><td className="py-2 pr-4 text-[var(--color-text-muted)]">{b}</td>
              <td className="py-2 text-right whitespace-nowrap" style={{ color: stateColor[s] }}>{label}</td></tr>
          ))}</tbody>
        </table>
        <div className="text-small text-[var(--color-text-dim)] mt-2"><LinkBtn className="inline" onClick={() => go('method', 'g-judge')}>{t('Why these? Guide · three ways to score', '为什么要这些？Guide · 三种打分方式')}</LinkBtn></div>
      </Zone>

      <Zone title={t('Calibration · how often the judge agrees with you', '校准 · judge 和你的一致率')}>
        {online?.calibration?.labels ? (
          <div className="text-body text-[var(--color-text)]" data-testid="judge-agreement">
            <b className="text-display tabular-nums mr-2">{pct(agreement)}</b>
            {t(`${online.calibration.agree} of ${online.calibration.labels} low scores you agreed with (this prompt version)`, `${online.calibration.labels} 个低分里你同意 ${online.calibration.agree} 个（当前 prompt 版本）`)}
          </div>
        ) : (
          <div className="flex items-center gap-4 text-small text-[var(--color-text-muted)]">
            <span className="flex-1">{t('No data yet. Mark the low-scored sessions in Live sessions with agree / disagree; the agreement appears here. A judge whose agreement holds up may move its items from watch to gate.',
              '还没有数据。你在真实会话里对低分会话点"同意 / 不同意"，攒够后这里显示一致率。一致率达标的 judge，判的项目才能从观测升门禁。')}</span>
            <Btn onClick={() => go('online', 'low')}>{t('Go label', '去标注')}</Btn>
          </div>
        )}
      </Zone>
    </section>
  );
}

function JudgeCard({ j, model, title, where, rows }: { j: JudgeInfo; model: string; title: string; where: string; rows: [string, string][] }) {
  const { t } = useLang();
  return (
    <div className="mt-10 pt-5 border-t border-[var(--color-border)]" data-testid={`judge-${j.id}`}>
      <div className="flex items-baseline gap-3 mb-3"><b className="text-heading text-[var(--color-text)]">{title}</b><span className="text-small text-[var(--color-text-dim)]">{where}</span></div>
      <KV rows={[...rows, [t('Settings', '设置'), `${model} · temperature ${j.temperature} · ${t('max output', '最多输出')} ${j.max_tokens} tokens`]]} />
      <details className="mt-3">
        <summary className="cursor-pointer text-small text-[var(--color-text-muted)]">{t('The full prompt (every call uses this one; changing it changes the scoring)', '看完整 prompt（每次调用都用这一份，改它就是改评分标准）')}</summary>
        <pre className="mt-2 text-meta whitespace-pre-wrap bg-[var(--color-hover)] rounded p-3 text-[var(--color-text)]">{j.system}{'\n\n'}{j.prompt}</pre>
      </details>
    </div>
  );
}
