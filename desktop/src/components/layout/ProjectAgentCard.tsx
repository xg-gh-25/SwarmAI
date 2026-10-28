/**
 * ProjectAgentCard — the review card of a Brain's Project Agent (design
 * docs/design/2026-10-28-project-agent-flow.md §5, step 3).
 *
 * The decision it answers: "can I confirm this definition?". So the verdict comes
 * first (draft / reviewed / has problems), then what the agent takes on (identity,
 * typical intents), then one block per work mode, then the shared rules (actions
 * that wait for you, gates, what reviewers watch). Commands, sources and repos sit
 * behind each mode's Details.
 *
 * While a draft is shown the person can mark rows to delete (× → struck through),
 * accept proposed aliases, allow the Hive, and Confirm. Confirm posts those choices
 * with the revision shown (confirmProjectAgent); a definition that changed in the
 * meantime is refused (409) and the card asks to look again. Wording changes go to
 * chat ("Revise in chat"); the card never rewrites a row's text. A reviewed
 * definition is shown read-only. `brain === null` is the Runner Agent (the General
 * Brain, Knowledge/Library). Callers key the card by Brain and revision, so the
 * person's marks never carry over onto another version of the definition.
 *
 * Exports: ProjectAgentCard
 */
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  confirmProjectAgent, type AgentCommand, type AgentConfirmResult, type AgentMode, type ProjectAgent,
} from '../../services/ddd';
import { SectionLabel, StatusBadge } from '../overlay';

const PLACE_LABEL: Record<string, string> = { 'chat-tab': 'Chat tab', 'local-worker': 'Local Worker', hive: 'Hive' };
const ACTION_LABEL: Record<string, string> = {
  push: 'push', open_pr: 'open a PR', publish_release: 'publish a release', send_external_message: 'send an external message',
};

function openPath(path: string | undefined) {
  if (path) document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
}

/** A source as a link when it resolves to a file, plain text otherwise. */
function Source({ src, agent }: { src?: string; agent: ProjectAgent }) {
  if (!src) return null;
  const path = agent.sourcePaths?.[src];
  return path
    ? <button className="text-meta text-[var(--color-text-muted)] underline decoration-dotted hover:text-[var(--color-text)]" onClick={() => openPath(path)}>{src.replace(/^[^:]+:/, '')}</button>
    : <span className="text-meta text-[var(--color-text-muted)]">{src}</span>;
}

/** One deletable line: × marks it for removal (struck through), again restores it. */
function Line({ path, marked, toggle, editable, children }: {
  path: string; marked: Set<string>; toggle: (p: string) => void; editable: boolean; children: React.ReactNode;
}) {
  const off = marked.has(path);
  return (
    <div className="group flex items-start gap-2 py-0.5" data-testid={`pa-line-${path}`}>
      <div className={`flex-1 min-w-0 ${off ? 'line-through opacity-50' : ''}`}>{children}</div>
      {editable && (
        <button className="btn-ghost text-meta px-1 opacity-0 group-hover:opacity-100 focus:opacity-100" title={off ? 'Keep' : 'Delete'}
          onClick={() => toggle(path)} data-testid={`pa-del-${path}`}>{off ? 'undo' : '×'}</button>
      )}
    </div>
  );
}

function Command({ c, agent }: { c: AgentCommand; agent: ProjectAgent }) {
  const key = c.repo ? `${c.repo}:${c.source}` : c.source;
  return (
    <span>
      <code className="text-meta whitespace-pre-wrap">{c.command}</code>
      <span className="text-meta text-[var(--color-text-muted)]">{c.cwd ? ` · in ${c.cwd}` : ''} · </span>
      <Source src={key} agent={agent} />
    </span>
  );
}

function ModeBlock({ mode, agent, isDefault, marked, toggle, editable }: {
  mode: AgentMode; agent: ProjectAgent; isDefault: boolean;
  marked: Set<string>; toggle: (p: string) => void; editable: boolean;
}) {
  const [open, setOpen] = useState(false);
  const base = `modes[${mode.id}]`;
  const hasDetail = mode.setup.length + mode.validations.length + mode.skills.length + mode.repos.length > 0;
  // what this mode adds on top of the shared lists: shown here, so nothing is confirmed unseen
  const extraReserved = mode.reservedForHuman.filter((a) => !(agent.reservedForHuman ?? []).includes(a));
  const extraGates = mode.gates.filter((g) => !(agent.gates ?? []).includes(g));
  return (
    <div className="py-2 border-t border-[var(--color-border)]" data-testid={`pa-mode-${mode.id}`}>
      <Line path={base} marked={marked} toggle={toggle} editable={editable && !isDefault}>
        <div className="text-small">
          <span className="font-semibold">{mode.id}</span>
          {isDefault && <span className="text-meta text-[var(--color-text-muted)]"> · default</span>}
          <span className="text-[var(--color-text-muted)]"> — {mode.when}</span>
        </div>
        <div className="text-meta text-[var(--color-text-muted)]">
          profile <span className="font-semibold text-[var(--color-text)]">{mode.profile}</span>
          {' · '}{mode.placements.map((p) => PLACE_LABEL[p] ?? p).join(', ')}
          {mode.repos.length > 0 && <> · delivers a {mode.delivery === 'branch-pr' ? 'branch and PR' : 'bundle you review'}</>}
        </div>
      </Line>
      {extraReserved.map((a) => (
        <Line key={`r${a}`} path={`${base}.reserved_for_human[${a}]`} marked={marked} toggle={toggle} editable={editable}>
          <div className="text-meta pl-3">also waits for you before it can <span className="font-semibold">{ACTION_LABEL[a] ?? a}</span></div>
        </Line>
      ))}
      {extraGates.map((g) => (
        <Line key={`g${g}`} path={`${base}.gates[${g}]`} marked={marked} toggle={toggle} editable={editable}>
          <div className="text-meta pl-3">also runs gate <code>{g}</code></div>
        </Line>
      ))}
      {mode.profileRules.map((r, i) => (
        <Line key={i} path={`${base}.profile_rules[${i}]`} marked={marked} toggle={toggle} editable={editable}>
          <div className="text-meta pl-3"><span className="font-semibold">{r.profile}</span> when {r.when}</div>
        </Line>
      ))}
      {hasDetail && (
        <button className="btn-ghost text-meta mt-0.5" onClick={() => setOpen(!open)} data-testid={`pa-mode-more-${mode.id}`}>
          {open ? 'Hide details' : `Details · ${mode.validations.length} checks, ${mode.skills.length} skills`}
        </button>
      )}
      {open && (
        <div className="pl-3 mt-1 flex flex-col gap-1" data-testid={`pa-mode-detail-${mode.id}`}>
          {mode.repos.length > 0 && <div className="text-meta text-[var(--color-text-muted)]">Repos: {mode.repos.join(', ')}</div>}
          {mode.setup.length > 0 && <div className="text-meta text-[var(--color-text-muted)]">Setup</div>}
          {mode.setup.map((c, i) => (
            <Line key={`s${i}`} path={`${base}.setup[${i}]`} marked={marked} toggle={toggle} editable={editable}>
              <Command c={c} agent={agent} />
            </Line>
          ))}
          {mode.validations.length > 0 && <div className="text-meta text-[var(--color-text-muted)]">Checks a Run must pass</div>}
          {mode.validations.map((c, i) => (
            <Line key={`v${i}`} path={`${base}.validations[${i}]`} marked={marked} toggle={toggle} editable={editable}>
              <Command c={c} agent={agent} />
            </Line>
          ))}
          {mode.skills.length > 0 && <div className="text-meta text-[var(--color-text-muted)]">Skills</div>}
          {mode.skills.map((s) => (
            <Line key={s} path={`${base}.skills[${s}]`} marked={marked} toggle={toggle} editable={editable}>
              <span className="text-meta">{s}</span>
            </Line>
          ))}
        </div>
      )}
    </div>
  );
}

export function ProjectAgentCard({ brain, agent, onRevise, onConfirmed }: {
  /** the Brain's name; null = the Runner Agent */
  brain: string | null;
  agent: ProjectAgent;
  onRevise: (note: string) => void;
  onConfirmed?: (r: AgentConfirmResult) => void;
}) {
  const qc = useQueryClient();
  const [marked, setMarked] = useState<Set<string>>(() => new Set());
  const [aliases, setAliases] = useState<Set<string>>(() => new Set());
  const [hive, setHive] = useState<boolean>((agent.placements ?? []).includes('hive'));
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AgentConfirmResult | null>(null);

  const reviewed = agent.status === 'reviewed';
  const editable = !reviewed && !busy;
  const problems = agent.problems ?? [];
  const toggle = (p: string) => setMarked((m) => { const n = new Set(m); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const modes = agent.modes ?? [];
  const proposed = agent.intake?.proposedAliases ?? [];

  const confirm = async () => {
    if (!agent.revision) return;
    setBusy(true); setError(null);
    try {
      const r = await confirmProjectAgent(brain, {
        revision: agent.revision, remove: [...marked],
        allow_hive: hive, aliases: [...aliases],
      });
      setResult(r);
      setMarked(new Set());
      onConfirmed?.(r);
      void qc.invalidateQueries({ queryKey: brain === null ? ['runner-agent'] : ['brain-detail', brain] });
      void qc.invalidateQueries({ queryKey: ['brains-with-pinned'] });
    } catch (e: unknown) {
      const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } }).response;
      const d = resp?.data?.detail;
      const msg = typeof d === 'string' ? d
        : d && typeof d === 'object' && 'message' in d
          ? `${(d as { message: string }).message}: ${((d as { problems?: string[] }).problems ?? []).join(' · ')}`
          : 'Could not confirm. Try again.';
      if (resp?.status === 409) {
        // the shown revision is stale: fetch the current one, which remounts the card
        void qc.invalidateQueries({ queryKey: brain === null ? ['runner-agent'] : ['brain-detail', brain] });
      }
      setError(resp?.status === 409 ? 'The definition changed since it was shown. The current version is loading; review it again.' : msg);
    } finally {
      setBusy(false);
    }
  };

  const verdict = problems.length > 0
    ? <StatusBadge tone="error" testid="bm-agent-status">has problems to fix</StatusBadge>
    : reviewed
      ? <StatusBadge tone="success" testid="bm-agent-status">reviewed{agent.reviewedAt ? ` · ${agent.reviewedAt}` : ''}{agent.reviewedBy ? ` by ${agent.reviewedBy}` : ''}</StatusBadge>
      : <StatusBadge tone="warning" testid="bm-agent-status">draft · needs your review</StatusBadge>;

  return (
    <div data-testid="bm-agent-def">
      <SectionLabel right={<button className="btn-sm btn-ghost" data-testid="pa-open-file"
        onClick={() => openPath(brain === null ? 'Knowledge/Library/project-agent.yaml' : `Projects/${brain}/project-agent.yaml`)}>Open file</button>}>
        {brain === null ? 'Runner Agent' : 'Project Agent'}
      </SectionLabel>
      <div className="flex items-center gap-2 mt-1">
        {verdict}
        {agent.revision && <span className="text-meta text-[var(--color-text-muted)]" data-testid="pa-revision">version {agent.revision.slice(0, 7)}</span>}
      </div>
      {problems.length > 0 && (
        <div className="mt-1 text-meta text-[var(--color-status-error)]" data-testid="bm-agent-problems">{problems.join(' · ')}</div>
      )}
      {(agent.notes ?? []).length > 0 && (
        <div className="mt-1 text-meta text-[var(--color-status-warning)]" data-testid="pa-notes">{(agent.notes ?? []).join(' · ')}</div>
      )}
      {agent.sourceCheck && !agent.sourceCheck.ok && (
        <div className="mt-1 text-meta text-[var(--color-status-warning)]" data-testid="pa-source-check">
          Not sent to a Hive until fixed: {agent.sourceCheck.missing.join(' · ')}
        </div>
      )}

      {agent.identity?.summary && (
        <Line path="identity" marked={marked} toggle={toggle} editable={editable}>
          <div className="text-small mt-2">{agent.identity.summary} <Source src={agent.identity.source} agent={agent} /></div>
          {agent.identity.outOfScope.length > 0 && (
            <div className="text-meta text-[var(--color-text-muted)]">Not for: {agent.identity.outOfScope.join('; ')}</div>
          )}
        </Line>
      )}
      {(agent.intake?.handles ?? []).length > 0 && (
        <div className="mt-2" data-testid="pa-handles">
          <div className="text-meta text-[var(--color-text-muted)]">Takes on</div>
          {(agent.intake?.handles ?? []).map((h, i) => (
            <Line key={i} path={`intake.handles[${i}]`} marked={marked} toggle={toggle} editable={editable}>
              <div className="text-small">{h}</div>
            </Line>
          ))}
        </div>
      )}

      <div className="mt-3">
        <div className="text-meta text-[var(--color-text-muted)]">Work modes</div>
        {modes.map((m) => (
          <ModeBlock key={m.id} mode={m} agent={agent} isDefault={m.id === agent.defaultMode}
            marked={marked} toggle={toggle} editable={editable} />
        ))}
      </div>

      <div className="mt-3 flex flex-col gap-1">
        <div className="text-meta text-[var(--color-text-muted)]">Always waits for you before it can</div>
        <div className="flex flex-wrap gap-x-3">
          {(agent.reservedForHuman ?? []).map((a) => (
            <Line key={a} path={`reserved_for_human[${a}]`} marked={marked} toggle={toggle} editable={editable}>
              <span className="text-small">{ACTION_LABEL[a] ?? a}</span>
            </Line>
          ))}
        </div>
        {(agent.gates ?? []).length > 0 && (
          <>
            <div className="text-meta text-[var(--color-text-muted)] mt-1">Gates it runs on every command</div>
            {(agent.gates ?? []).map((g) => (
              <Line key={g} path={`gates[${g}]`} marked={marked} toggle={toggle} editable={editable}>
                <code className="text-meta">{g}</code>
              </Line>
            ))}
          </>
        )}
        {(agent.reviewFocus ?? []).length > 0 && (
          <>
            <div className="text-meta text-[var(--color-text-muted)] mt-1">Reviewers watch</div>
            {(agent.reviewFocus ?? []).map((w, i) => (
              <Line key={i} path={`watches[${i}]`} marked={marked} toggle={toggle} editable={editable}>
                <span className="text-small font-semibold">{w.area}</span>
                <span className="text-meta text-[var(--color-text-muted)]"> · {w.why} · </span><Source src={w.source} agent={agent} />
              </Line>
            ))}
          </>
        )}
      </div>

      {!reviewed && proposed.length > 0 && (
        <div className="mt-3" data-testid="pa-aliases">
          <div className="text-meta text-[var(--color-text-muted)]">Names that route a request here (tick to keep)</div>
          {proposed.map((a) => (
            <label key={a.name} className="flex items-center gap-2 text-small">
              <input type="checkbox" checked={aliases.has(a.name)} disabled={!editable} data-testid={`pa-alias-${a.name}`}
                onChange={() => setAliases((s) => { const n = new Set(s); if (n.has(a.name)) n.delete(a.name); else n.add(a.name); return n; })} />
              {a.name} <Source src={a.source} agent={agent} />
            </label>
          ))}
        </div>
      )}

      {result && (
        <div className="mt-2 text-meta" data-testid="pa-confirmed">
          Confirmed. {result.sourceCheck.ok ? 'Every command source was found.' : `Missing sources: ${result.sourceCheck.missing.join(' · ')}`}
        </div>
      )}
      {error && <div className="mt-2 text-meta text-[var(--color-status-error)]" data-testid="pa-error">{error}</div>}

      <div className="flex items-center gap-2 mt-3">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What to change, e.g. use full for API changes"
          className="flex-1 min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-note" />
        <button className="btn-sm btn-secondary" disabled={!note.trim()} onClick={() => { onRevise(note.trim()); setNote(''); }} data-testid="bm-agent-revise">Revise in chat</button>
        {!reviewed && (
          <>
            <label className="flex items-center gap-1 text-small" title="Allow this agent to run on your Hives">
              <input type="checkbox" checked={hive} disabled={!editable} onChange={() => setHive(!hive)} data-testid="pa-hive" /> Hive
            </label>
            <button className="btn-sm btn-primary" onClick={confirm} disabled={busy || !agent.revision}
              data-testid="bm-agent-confirm">{marked.size ? `Delete ${marked.size} and confirm` : 'Confirm'}</button>
          </>
        )}
      </div>
    </div>
  );
}
