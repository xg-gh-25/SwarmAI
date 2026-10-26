/**
 * The run-trace tags on a Swarm Out run row — the story of one run, in build order:
 *
 *   Brain (DDD) → Project Agent → Context → Hive → Report
 *
 * The first four were PREPARED before the run started; Report is what it DELIVERED,
 * including what flowed back into the brain (the loop closing). Each tag is quiet
 * (muted text, no state colour — the Autonomy Judge tag is the only coloured one on
 * the row); a click opens its detail. A tag whose part does not exist says so rather
 * than vanish: "无 Brain" for a general task, "Report 生成中" while the run is going.
 */
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { RunTrace } from './mockData';
import { POPOVER_CLASS, usePopover } from './usePopover';

const muted = 'text-[var(--color-text-muted)]';

function TraceTag({ icon, label, testId, title, children }: {
  icon: string; label: string; testId: string; title: string; children?: ReactNode;
}) {
  const { open, triggerProps, popoverProps } = usePopover();
  const base = 'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-md text-[11.5px] whitespace-nowrap max-w-[180px]';
  // nothing to show (no brain, report not written yet) → plain text, not a button
  if (!children) {
    return (
      <span className={`${base} text-[var(--color-text-dim)]`} data-testid={testId}>
        <span className="material-symbols-outlined icon-xs">{icon}</span><span className="truncate">{label}</span>
      </span>
    );
  }
  return (
    <>
      <button
        {...triggerProps}
        aria-label={`${title}：${label}`}
        data-testid={testId}
        className={`${base} ${muted} hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]`}
      >
        <span className="material-symbols-outlined icon-xs">{icon}</span><span className="truncate">{label}</span>
      </button>
      {open && createPortal(
        <div {...popoverProps} aria-label={title} data-testid={`${testId}-popover`} className={POPOVER_CLASS}>
          <div className="text-[13.5px] font-semibold mb-2">{title}</div>
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}

function Field({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[64px_1fr] gap-2 py-1 border-t border-[var(--color-border)] first:border-t-0 text-[12.5px]">
      <div className={muted}>{k}</div>
      <div className="text-[var(--color-text-secondary)] min-w-0 break-words">{children}</div>
    </div>
  );
}

export function TraceTags({ trace }: { trace: RunTrace }) {
  const { brain, agent, context, hive, report } = trace;
  return (
    <div className="flex items-center gap-0.5 flex-wrap" data-testid="trace-tags">
      <TraceTag icon="psychology" label={brain ? brain.name : '无 Brain'} testId="trace-brain" title="Brain（DDD）">
        {brain && <>
          <Field k="Brain">{brain.name}</Field>
          <Field k="版本">{brain.version}</Field>
          <Field k="文档">{brain.docs.join(' · ')}</Field>
          <div className={`mt-2 text-[11.5px] text-[var(--color-text-dim)]`}>这个 run 的上下文从这个 Brain 打包，结果也只回流到它。</div>
        </>}
      </TraceTag>

      <TraceTag icon="smart_toy" label={agent ? `${agent.profile}` : '通用 Agent'} testId="trace-agent" title="Project Agent">
        {agent && <>
          <Field k="Agent">{agent.name} <span className="text-[var(--color-text-dim)]">· {agent.status === 'reviewed' ? '已审过' : '草稿，未审'}</span></Field>
          <Field k="走法">pipeline · {agent.profile}</Field>
          <Field k="命中规则">{agent.rule}</Field>
          <Field k="验证">{agent.validations.join(' · ')}</Field>
          <Field k="执行在">{agent.placement}</Field>
          <Field k="落地">{agent.landing}</Field>
          <div className={`mt-2 text-[11.5px] text-[var(--color-text-dim)]`}>来自这个 Brain 的 project-agent.yaml。</div>
        </>}
      </TraceTag>

      <TraceTag icon="inventory_2" label={`Context · ${context.tokens}`} testId="trace-context" title="Context（派出前打包）">
        <Field k="目标">{context.goal}</Field>
        <Field k="验收">{context.acceptance}</Field>
        {context.scope && <Field k="范围">{context.scope}</Field>}
        <Field k="知识">
          {context.slices.map((s) => (
            <div key={s.source} className="flex gap-2"><span className="flex-1 min-w-0 truncate">{s.source}</span><span className="tabular-nums text-[var(--color-text-dim)]">{s.score.toFixed(2)}</span></div>
          ))}
        </Field>
        <Field k="记忆">{context.memory} 条</Field>
        <Field k="大小">{context.tokens} tokens · {context.packedAt} 打包</Field>
      </TraceTag>

      <TraceTag icon="dns" label={`${hive.instance} #${hive.slot}`} testId="trace-hive" title="Hive">
        <Field k="实例">{hive.instance} · 槽位 #{hive.slot}</Field>
        <Field k="Session">{hive.session}</Field>
        {hive.run && <Field k="Run">{hive.run}</Field>}
      </TraceTag>

      <TraceTag icon="description" label={report ? 'Report' : 'Report 生成中'} testId="trace-report" title="Report">
        {report && <>
          <Field k="结果"><span className="font-mono text-[11.5px]">{report.outcome}</span></Field>
          <Field k="交付">{report.deliverables.map((d) => <div key={d}>{d}</div>)}</Field>
          <Field k="Session">{report.turns} 轮对话</Field>
          <Field k="回流">{report.flowedBack.length ? report.flowedBack.map((d) => <div key={d}>{d}</div>) : <span className="text-[var(--color-text-dim)]">没有回流到 Brain</span>}</Field>
          <Field k="报告">{report.path}</Field>
        </>}
      </TraceTag>
    </div>
  );
}
