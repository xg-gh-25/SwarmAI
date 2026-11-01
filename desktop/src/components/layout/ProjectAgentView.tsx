/**
 * ProjectAgentView — a Brain's Project Agent, read-only (design
 * docs/design/2026-11-01-project-agent-simple.md).
 *
 * The decision the page answers: "can a Hive run do this Brain's work, and what will it
 * do?". It shows only what a Hive run reads: the summary, each mode (when it is used,
 * its repos, setup, checks and skills), the Brain gates, what every review watches, and
 * the actions a run always asks you for (the product's, the same for every Brain).
 * Nothing here is confirmed or edited: the agent keeps the definition current, and a
 * change you want goes to the agent in chat. `brain === null` is the Runner Agent.
 *
 * Exports: ProjectAgentView
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AgentCommand, ProjectAgent } from '../../services/ddd';
import { StatusBadge } from '../overlay';

function openPath(path: string | undefined) {
  if (path) document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
}

/** i18n keys for the actions a run always asks you for. */
const RESERVED_LABEL: Record<string, string> = {
  push: 'ui.ProjectAgentView.reservedPush', open_pr: 'ui.ProjectAgentView.reservedOpenPr', publish_release: 'ui.ProjectAgentView.reservedPublishRelease',
  send_external_message: 'ui.ProjectAgentView.reservedSendExternal',
};

function Commands({ rows }: { rows: AgentCommand[] }) {
  const { t } = useTranslation();
  if (!rows.length) return <span className="text-[var(--color-text-muted)]">{t('ui.ProjectAgentView.none')}</span>;
  return (
    <ul className="space-y-0.5">
      {rows.map((r, i) => (
        <li key={`${r.id ?? ''}-${i}`} className="font-mono text-meta break-all">
          {r.id ? <span className="text-[var(--color-text-muted)]">{r.id}: </span> : null}{r.command}
          {r.repo ? <span className="text-[var(--color-text-muted)]"> · {r.repo}{r.cwd ? `/${r.cwd}` : ''}</span> : null}
        </li>))}
    </ul>
  );
}

export function ProjectAgentView({ brain, agent, onRevise }: {
  brain: string | null;
  agent: ProjectAgent;
  /** send a change request to the agent in chat */
  onRevise: (note: string) => void;
}) {
  const { t } = useTranslation();
  const [note, setNote] = useState('');
  const problems = agent.problems ?? [];
  const modes = agent.modes ?? [];
  const reserved = agent.reservedForHuman ?? [];
  const file = brain === null ? 'Knowledge/Library/project-agent.yaml' : `Projects/${brain}/project-agent.yaml`;
  return (
    <div data-testid="bm-agent-def" className="pb-6">
      <div className="flex items-center gap-3 flex-wrap">
        {problems.length > 0
          ? <StatusBadge tone="error" testid="bm-agent-status">{t('ui.ProjectAgentView.hasProblems')}</StatusBadge>
          : <StatusBadge tone="success" testid="bm-agent-status">{t('ui.ProjectAgentView.ready')}</StatusBadge>}
        {agent.revision && <span className="text-meta text-[var(--color-text-muted)]" data-testid="pa-revision">{t('ui.ProjectAgentView.version', { revision: agent.revision.slice(0, 7) })}</span>}
        <span className="flex-1" />
        <button className="btn-sm btn-ghost" data-testid="pa-open-file" onClick={() => openPath(file)}>{t('ui.ProjectAgentView.openYaml')}</button>
      </div>
      {problems.length > 0 && <div className="mt-2 text-meta text-[var(--color-status-error)]" data-testid="bm-agent-problems">{problems.join(' · ')}</div>}
      {agent.summary && <p className="mt-3 text-small" data-testid="pa-summary">{agent.summary}</p>}

      <div className="mt-4 space-y-4" data-testid="pa-modes">
        {modes.map((m) => (
          <section key={m.id} data-testid={`pa-mode-${m.id}`}>
            <div className="text-small">
              <b>{m.id}</b>{m.id === agent.defaultMode ? <span className="text-[var(--color-text-muted)]"> · {t('ui.ProjectAgentView.default')}</span> : null}
              <span className="text-[var(--color-text-muted)]"> — {m.when}</span>
            </div>
            <div className="mt-1 grid grid-cols-[110px_1fr] gap-x-4 gap-y-1 text-meta">
              {m.repos.length > 0 && <><span className="text-[var(--color-text-muted)]">{t('ui.ProjectAgentView.repositories')}</span><span>{m.repos.join(', ')}</span></>}
              {m.setup.length > 0 && <><span className="text-[var(--color-text-muted)]">{t('ui.ProjectAgentView.setup')}</span><Commands rows={m.setup} /></>}
              {(m.repos.length > 0 || m.validations.length > 0) && <><span className="text-[var(--color-text-muted)]">{t('ui.ProjectAgentView.checks')}</span><Commands rows={m.validations} /></>}
              {m.skills.length > 0 && <><span className="text-[var(--color-text-muted)]">{t('ui.ProjectAgentView.skills')}</span><span>{m.skills.join(', ')}</span></>}
            </div>
          </section>))}
      </div>

      <div className="mt-4 text-meta text-[var(--color-text-muted)] space-y-1" data-testid="pa-guards">
        <div>{t('ui.ProjectAgentView.alwaysAsks', { actions: reserved.map((a) => (RESERVED_LABEL[a] ? t(RESERVED_LABEL[a]) : a)).join(', ') })}</div>
        {(agent.gates ?? []).length > 0 && <div>{t('ui.ProjectAgentView.gates', { gates: (agent.gates ?? []).join(', ') })}</div>}
      </div>
      {(agent.reviewFocus ?? []).length > 0 && (
        <details className="mt-3 text-meta" data-testid="pa-watches">
          <summary className="text-[var(--color-text-muted)] cursor-pointer">{t('ui.ProjectAgentView.reviewChecks', { count: (agent.reviewFocus ?? []).length })}</summary>
          <ul className="mt-1 space-y-0.5">
            {(agent.reviewFocus ?? []).map((w, i) => <li key={i}><b>{w.area}</b>: {w.why}</li>)}
          </ul>
        </details>)}

      <div className="mt-5" data-testid="pa-ask">
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4}
          placeholder={t('ui.ProjectAgentView.notePlaceholder')}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && note.trim()) { onRevise(note.trim()); setNote(''); } }}
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1.5 text-small" data-testid="bm-agent-note" />
        <div className="mt-1 flex justify-end">
          <button className="btn-sm btn-secondary" disabled={!note.trim()} onClick={() => { onRevise(note.trim()); setNote(''); }} data-testid="bm-agent-revise">{t('ui.ProjectAgentView.askInChat')}</button>
        </div>
      </div>
    </div>
  );
}
