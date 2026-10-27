/**
 * Golden Cases — the cases, by line (quick check / offline case) and category, with
 * their release tier; a case's detail; and the user's own cases (add, edit, delete,
 * try before saving). Product cases ship with SwarmAI and are read-only.
 *
 * Saving runs the backend's full admission (POST /golden-set/try is the same check
 * without saving), so the form lists the checks and shows each one's result.
 */
import { useMemo, useState, type ReactNode } from 'react';
import {
  CASE_DIMENSIONS, errorDetail, useCaseDetail, useDeleteCase, useGoldenSet, useRunCases, useRunProgress, useSaveCase,
  useTryCase, type AdmissionResult, type CaseDetail, type CaseInput, type GoldenCase, type ReleaseTier,
} from './api';
import {
  Btn, CATEGORY_LABEL, Code, ErrorState, H4, KV, LayerTag, Loading, SidePanel, StatusDot, Tag, TIER_LABEL,
  TierTag, categoryName, dimName, isQuick, pick, splitPurpose, useEvalActions, useGo, useLang, when,
} from './ui';

type Line = 'all' | 'quick' | 'offline';
type Panel = { kind: 'case'; id: string } | { kind: 'form'; type: 'quick' | 'offline'; from?: CaseDetail; editing?: boolean } | null;

const tierOf = (c: GoldenCase): ReleaseTier => c.release_tier ?? 'watch';
const failed = (c: GoldenCase) => c.last_result?.status === 'failed' || c.last_result?.status === 'error' || c.last_result?.status === 'timeout';

export function GoldenCasesTab() {
  const { lang, t } = useLang();
  const gs = useGoldenSet();
  const [line, setLine] = useState<Line>('all');
  const [tier, setTier] = useState<'all' | ReleaseTier>('all');
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [q, setQ] = useState('');
  const [panel, setPanel] = useState<Panel>(null);

  const live = useMemo(() => (gs.data?.cases ?? []).filter((c) => c.tier !== 'archived'), [gs.data]);
  if (gs.isError) return <ErrorState />;
  if (gs.isLoading) return <Loading />;
  const product = live.filter((c) => c._origin === 'product');
  const mine = live.filter((c) => c._origin !== 'product');
  const match = (c: GoldenCase) =>
    (line === 'all' || (line === 'quick') === isQuick(c)) && (tier === 'all' || tierOf(c) === tier)
    && (!onlyFailed || failed(c))
    && (!q || `${pick(c.purpose, lang)} ${c.title ?? ''} ${c.id}`.toLowerCase().includes(q.toLowerCase()));
  const count = (tr: ReleaseTier) => live.filter((c) => tierOf(c) === tr).length;
  const chip = (on: boolean, label: string, onClick: () => void) => (
    <button type="button" key={label} onClick={onClick} aria-pressed={on}
            className={`btn-sm ${on ? 'btn-soft' : 'btn-ghost'} transition-colors`}>
      {label}
    </button>
  );
  const open = (c: GoldenCase) => setPanel({ kind: 'case', id: c.id });

  return (
    <div className="max-w-[1080px] mx-auto px-7 pt-10 pb-24" data-testid="eval-cases">
      <div className="flex items-end gap-6">
        <div className="flex-1">
          <div className="text-display font-semibold text-[var(--color-text)] tabular-nums">
            {live.length}<small className="text-body font-normal text-[var(--color-text-muted)] ml-2">{t('golden cases', '条 golden case')}</small>
          </div>
          <div className="text-small text-[var(--color-text-muted)] mt-1">
            {t(`${product.length} product cases ship with SwarmAI: read-only, the same for every user. ${mine.length} of your own.`,
              `${product.length} 条产品 case：随 SwarmAI 发布、只读、每个用户一样。你自己的 ${mine.length} 条。`)}
          </div>
        </div>
        <Btn kind="primary" testId="add-case" onClick={() => setPanel({ kind: 'form', type: 'offline' })}>{t('Add a case', '新增 case')}</Btn>
      </div>

      <div className="grid grid-cols-3 gap-6 mt-7 text-small text-[var(--color-text-muted)]" data-testid="tier-summary">
        {(['red', 'gate', 'watch'] as ReleaseTier[]).map((tr) => (
          <div key={tr}><b className="text-title text-[var(--color-text)] mr-2 tabular-nums">{count(tr)}</b><TierTag tier={tr} /> {TIER_LABEL[tr].what[lang]}</div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 mt-6">
        {chip(line === 'all', t('All', '全部'), () => setLine('all'))}
        {chip(line === 'quick', t('Quick checks', '快速检查'), () => setLine('quick'))}
        {chip(line === 'offline', t('Offline cases', '离线考题'), () => setLine('offline'))}
        <span className="w-px h-4 bg-[var(--color-border)] mx-1" />
        {chip(tier === 'all', t('Every tier', '全部档位'), () => setTier('all'))}
        {(['red', 'gate', 'watch'] as ReleaseTier[]).map((tr) => chip(tier === tr, TIER_LABEL[tr][lang], () => setTier(tr)))}
        <span className="w-px h-4 bg-[var(--color-border)] mx-1" />
        {chip(onlyFailed, t('Failing only', '只看没通过'), () => setOnlyFailed((v) => !v))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('Search what it protects, or an id', '搜它守什么、或 case id')}
               className="ml-auto w-64 px-2.5 py-1 rounded-md border border-[var(--color-border)] bg-transparent text-small text-[var(--color-text)]" />
      </div>

      <CaseBlock title={t('Quick checks', '快速检查')} what={t('deterministic probes, after a session ends, no model call', '确定性探针，会话结束后跑，不调模型')}
                 cases={product.filter((c) => isQuick(c) && match(c))} onOpen={open} />
      <CaseBlock title={t('Offline cases', '离线考题')} what={t('a real agent answers in the sample workspace, on the full-run schedule', '真实 agent 在样例工作区答题，按全量运行的计划')}
                 cases={product.filter((c) => !isQuick(c) && match(c))} onOpen={open} />

      <div className="flex items-baseline gap-3 mt-12 mb-2 pb-2 border-b border-[var(--color-border)]">
        <b className="text-heading text-[var(--color-text)]">{t('My cases', '我的 case')}</b>
        <span className="text-small text-[var(--color-text-dim)]">{t('only what is yours and no product case covers', '只放你独有、产品 case 管不到的东西')}</span>
      </div>
      {mine.filter(match).length ? (
        <CaseRows cases={mine.filter(match)} onOpen={open} />
      ) : (
        <div className="flex items-center gap-4 text-small text-[var(--color-text-muted)] py-3" data-testid="my-cases-empty">
          <span className="flex-1">{t('None yet. One worth adding: a decision in your own project that once went wrong and must never go wrong again.',
            '还没有。值得加的例子：你自己项目里一个曾经出过错、又一定不能再错的决定。')}</span>
          <Btn onClick={() => setPanel({ kind: 'form', type: 'offline' })}>{t('Add', '新增')}</Btn>
        </div>
      )}

      <SidePanel open={!!panel} onClose={() => setPanel(null)} testId="case-panel">
        {panel?.kind === 'case' && <CaseDetailView id={panel.id} onForm={(p) => setPanel(p)} onClosed={() => setPanel(null)} />}
        {panel?.kind === 'form' && <CaseForm key={`${panel.type}-${panel.from?.id ?? ''}-${panel.editing}`} type={panel.type} from={panel.from}
                                             editing={!!panel.editing} total={live.length}
                                             onType={(type) => setPanel({ ...panel, type })} onDone={() => setPanel(null)} />}
      </SidePanel>
    </div>
  );
}

function CaseBlock({ title, what, cases, onOpen }: { title: string; what: string; cases: GoldenCase[]; onOpen: (c: GoldenCase) => void }) {
  const { t } = useLang();
  if (!cases.length) return null;
  return (
    <>
      <div className="flex items-baseline gap-3 mt-10 mb-2 pb-2 border-b border-[var(--color-border)]">
        <b className="text-heading text-[var(--color-text)]">{title}</b>
        <span className="text-small text-[var(--color-text-dim)]">{what}</span>
        <span className="ml-auto text-small text-[var(--color-text-muted)]">{t(`${cases.length}`, `${cases.length} 条`)}</span>
      </div>
      <CaseRows cases={cases} onOpen={onOpen} />
    </>
  );
}

function CaseRows({ cases, onOpen }: { cases: GoldenCase[]; onOpen: (c: GoldenCase) => void }) {
  const { lang, t } = useLang();
  const by: Record<string, GoldenCase[]> = {};
  cases.forEach((c) => (by[c.category] = by[c.category] || []).push(c));
  return (
    <>
      {Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([cat, cs]) => (
        <div key={cat} className="mt-3">
          <div className="text-meta uppercase tracking-wide text-[var(--color-text-dim)] mb-1">{categoryName(cat, lang)}</div>
          {cs.map((c) => (
            <button type="button" key={c.id} onClick={() => onOpen(c)} data-testid={`case-row-${c.id}`}
                    className="w-full grid grid-cols-[10px_1fr_auto_90px_70px] gap-3 items-center py-1.5 text-left text-body hover:bg-[var(--color-hover)] rounded px-1">
              <StatusDot status={c.last_result?.status} />
              <span className="truncate text-[var(--color-text)]">{splitPurpose(pick(c.purpose, lang) || c.title || c.id)[0]}</span>
              <TierTag tier={tierOf(c)} />
              <span className="text-small text-[var(--color-text-muted)]">{dimName(c.dimension, lang)}</span>
              <span className="text-small text-right" style={{ color: failed(c) ? 'var(--color-status-warning)' : 'var(--color-text-muted)' }}>
                {c.last_result ? (failed(c) ? t('failing', '没通过') : c.last_result.status === 'passed' ? t('passing', '通过') : c.last_result.status) : t('not run', '没跑过')}
              </span>
            </button>
          ))}
        </div>
      ))}
    </>
  );
}

// ─── detail ────────────────────────────────────────────────────────────────

function CaseDetailView({ id, onForm, onClosed }: { id: string; onForm: (p: Panel) => void; onClosed: () => void }) {
  const { lang, t } = useLang();
  const go = useGo();
  const { askSwarm } = useEvalActions();
  const detail = useCaseDetail(id);
  const rerun = useRunCases();
  const { running: rerunning, failed: rerunFailed } = useRunProgress(rerun.data?.run_id);
  const del = useDeleteCase();
  const [confirm, setConfirm] = useState(false);
  if (detail.isLoading) return <Loading />;
  if (detail.isError || !detail.data) return <ErrorState />;
  const c = detail.data;
  const quick = isQuick(c);
  const mineCase = c._origin !== 'product';
  const tr = tierOf(c);
  const [head, rest] = splitPurpose(pick(c.purpose, lang) || c.title || c.id);
  const last = c.history?.[0];
  const v = c.verification ?? {};
  const tierWhy = tr === 'red' ? t('If it fails, the release is blocked, whatever else passed.', '一旦不通过，整次评测判红，其它再好也不放行。')
    : tr === 'gate' ? t('Must pass to release. Code-checked, so the result is reproducible.', '要通过才放行。全是确定性检查，结果可复现。')
      : t('Shown, never blocks. A judge-scored or new case starts here; once it passes steadily and the case itself is confirmed sound, it can move up (your call).',
        '只看不拦。judge 判的、新加的 case 从这里开始；稳定通过、题目确认无误后可以升档（升档要你点头）。');

  return (
    <div data-testid="case-detail">
      <div className="flex flex-wrap gap-1.5 mb-3">
        <TierTag tier={tr} /><Tag>{dimName(c.dimension, lang)}</Tag><Tag>{quick ? t('Quick check', '快速检查') : t('Offline case', '离线考题')}</Tag>
        <Tag>{categoryName(c.category, lang)}</Tag><LayerTag layer="L1" />{!quick && c.decision_rubric && <LayerTag layer="L2" />}
        <Tag>{mineCase ? t('Mine', '我的') : t('Product', '产品')}</Tag>
      </div>
      <h3 className="text-title font-semibold text-[var(--color-text)]">{head}</h3>
      {rest && <p className="text-body text-[var(--color-text-muted)] mt-1">{rest}</p>}

      <H4>{t('Tier', '档位')}</H4>
      <div className="flex gap-2 items-start text-small text-[var(--color-text)]"><TierTag tier={tr} /><span>{tierWhy}</span></div>

      <H4>{t('Latest result', '最近结果')}</H4>
      {last ? (
        <div className="text-small" data-testid="case-latest">
          <b style={{ color: last.status === 'passed' ? 'var(--color-status-success)' : 'var(--color-status-warning)' }}>{last.status}</b>
          <span className="text-[var(--color-text-muted)]"> · {when(last.triggered_at)}</span>
          {last.notes && <div className="text-[var(--color-text-muted)] mt-1 break-words">{last.notes}</div>}
        </div>
      ) : <div className="text-small text-[var(--color-text-dim)]">{t('Not run yet.', '还没跑过。')}</div>}

      {quick ? (
        <>
          <H4>{t('How it is checked · L1, code, no model call', '怎么检查 · L1 确定性，不调模型')}</H4>
          <KV rows={[
            [t('Command', '检查命令'), <Code>{v.command ?? '—'}</Code>],
            [t('Passes when', '通过的条件'), <>{t('output contains', '输出里有')} <Code>{v.expected_contains ?? '—'}</Code></>],
            [t('Negative command', '反例命令'), <Code>{v.negative_command ?? '—'}</Code>],
            [t('Negative must', '反例必须'), <>{t('print', '输出')} <Code>{v.negative_expected_contains ?? '—'}</Code>. {t('Break the thing on purpose and the check must catch it; if it cannot, it tests nothing.', '故意把功能弄坏，这条检查要能抓到；抓不到就是什么都没测。')}</>],
          ]} />
        </>
      ) : (
        <>
          <H4>{t('The prompt · what the agent receives', '题面 · agent 收到的消息')}</H4>
          <pre className="text-small whitespace-pre-wrap bg-[var(--color-hover)] rounded p-3 text-[var(--color-text)]">{c.scenario?.prompt ?? '—'}</pre>
          <H4>{t('Step one · trajectory check (L1)', '第一步 · 轨迹检查（L1）')}</H4>
          <KV rows={[
            [t('Tools that must appear', '必须出现的工具'), <>{(c.expected_trajectory ?? []).map((x) => <Code key={x}>{x}</Code>)} <span className="text-[var(--color-text-dim)]">{c.trajectory_match === 'any_order' ? t('any order; A|B = either', '顺序不限，A|B 表示任一') : t('in order; A|B = either', '按顺序，A|B 表示任一')}</span></>],
            ...(c.allowed_tools?.length ? [[t('Tools allowed', '允许用的工具'), <>{c.allowed_tools.map((x) => <Code key={x}>{x}</Code>)}</>] as [string, ReactNode]] : []),
          ]} />
          {c.decision_rubric && (
            <>
              <H4>{t('Step two · answer judged (L2), only after the trajectory passes', '第二步 · 回答判定（L2），轨迹通过后才调 judge')}</H4>
              <pre className="text-small whitespace-pre-wrap bg-[var(--color-hover)] rounded p-3 text-[var(--color-text)]">{c.decision_rubric}</pre>
              <div className="text-small text-[var(--color-text-dim)] mt-1">
                <a className="cursor-pointer hover:underline" onClick={() => { onClosed(); go('judges'); }}>{t('See the judge and its prompt', '看 judge 和它的 prompt')}</a>
              </div>
            </>
          )}
        </>
      )}

      <H4>{t('Changing these files marks the result stale until it re-runs', '改到这些文件时，这条的结果标成过期、等下次重跑')}</H4>
      <div className="text-small space-y-0.5">{(c.affected_by ?? []).map((f) => <div key={f}><Code>{f}</Code></div>)}</div>
      <H4>ID</H4><Code>{c.id}</Code>

      <div className="flex flex-wrap gap-2 mt-7">
        <Btn onClick={() => rerun.mutate({ ids: [c.id], behavior: !quick })} disabled={rerun.isPending || rerunning} testId="case-rerun">
          {rerunning ? t('Running…', '运行中…') : t('Re-run this case', '重跑这条')}
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
        <Btn kind="ghost" onClick={() => askSwarm(t(`Review golden case ${c.id}: is what it protects still right, and does the check have a hole?`,
          `看一下 golden case ${c.id}：它守住的东西现在还对吗，检查方式有没有漏洞`))}>{t('Ask Swarm to review it', '让 Swarm 审这条')}</Btn>
      </div>
      {rerun.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(rerun.error)}</div>}
      {rerunFailed && <div className="text-small text-[var(--color-status-error)] mt-2">{t('The run did not finish: ', '这次运行没跑完：')}{rerunFailed}</div>}
      {del.isError && <div className="text-small text-[var(--color-status-error)] mt-2">{errorDetail(del.error)}</div>}
      <div className="text-small text-[var(--color-text-dim)] mt-4">
        {mineCase ? t('Delete removes it from the file; nothing is archived. Add it again if you need it.', '删除就是从文件里删掉，不归档；需要时再加。')
          : t('Product cases are the same for every user: they cannot be edited or deleted here. Changing one is a code change.', '产品 case 每个用户都一样，这里不能改、不能删，要改就是改代码。')}
      </div>
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
        {!quick && <div className="text-small text-[var(--color-text-dim)]">{t('An offline case is not trial-run when saved (it would spawn a real agent): run it from its detail once saved.', '离线考题保存时不试跑（会启动真实 agent）：保存后在详情里点"重跑这条"。')}</div>}
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
