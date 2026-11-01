/**
 * ProjectAgentForm — a Brain's Project Agent as one standard form (design
 * docs/design/2026-10-29-project-agent-standard-template.md §3, §7).
 *
 * The decision the page answers: "is this the agent I want for this Brain?". Every
 * Brain shows the same eight sections in the same order — what the agent is and has,
 * its limits, how work comes in and when it brings you in, then the reference — and
 * every field says what it is for and who may change it (Editable, From the Brain,
 * Your choice, Fixed). The fields come from the backend (`agent.form`, built from the
 * one template in core/project_agent_standard): this file holds no field list, only
 * how each shape is drawn and a few read-only views (repos, skills, tools, guards,
 * profiles).
 *
 * Changes are kept on the page until Save (one PATCH, the revision shown); the saved
 * definition is a draft and Confirm confirms it. A definition that changed meanwhile
 * is refused (409) and the current one loads. On a Hive the page is read-only (the
 * backend refuses writes there). `brain === null` is the Runner Agent.
 *
 * Exports: ProjectAgentForm
 */
import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  confirmProjectAgent, editProjectAgent, type AgentChange, type AgentChoice, type AgentFormField, type ProjectAgent,
} from '../../services/ddd';
import { StatusBadge } from '../overlay';

const KIND_STYLE: Record<string, string> = {
  edit: 'bg-[color-mix(in_srgb,var(--color-primary)_14%,transparent)] text-[var(--color-primary)]',
  brain: 'bg-[var(--color-hover)] text-[var(--color-text-muted)]',
  you: 'bg-[color-mix(in_srgb,var(--color-status-success)_16%,transparent)] text-[var(--color-status-success)]',
  fixed: 'border border-[var(--color-border)] text-[var(--color-text-muted)]',
};
const KIND_LEGEND: [string, string, string][] = [
  ['edit', 'Editable', "drafted from the Brain's documents; change it here"],
  ['brain', 'From the Brain', "read from the Brain's files; change it there"],
  ['you', 'Your choice', 'only you set it'],
  ['fixed', 'Fixed', 'the same for every Brain'],
];

function openPath(path: string | undefined) {
  if (path) document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
}

function choiceOf(c: AgentChoice) {
  return typeof c === 'string' ? { value: c, label: c } : c;
}

type Rows = Record<string, string>[];

/** The page's unsaved changes: a new value per path, or rows to drop per path. */
interface Pending { set: Map<string, unknown>; drop: Map<string, Set<number>> }

function KindTag({ kind, label }: { kind: string; label: string }) {
  return <span className={`text-[10.5px] px-1.5 py-px rounded ${KIND_STYLE[kind] ?? ''}`}>{label}</span>;
}

function SourceLink({ src, agent }: { src?: string; agent: ProjectAgent }) {
  if (!src) return null;
  const path = agent.sourcePaths?.[src];
  const label = src.replace(/^[^:]+:/, '').split('/').pop();
  return path
    ? <button className="text-meta text-[var(--color-text-muted)] underline decoration-dotted hover:text-[var(--color-text)]" onClick={() => openPath(path)}>{label}</button>
    : <span className="text-meta text-[var(--color-text-muted)]">{label}</span>;
}

const INPUT = 'w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2.5 py-1.5 text-small';
const BOX = 'rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)]';

/** A label column (name, purpose, kind) and a value column, one per field. */
function FieldRow({ f, children, agent, source }: { f: AgentFormField; children: React.ReactNode; agent: ProjectAgent; source?: string }) {
  return (
    <div className="grid grid-cols-[minmax(180px,240px)_1fr] gap-6 py-4 border-b border-[var(--color-border)] last:border-b-0"
      data-testid={`pa-field-${f.key}`}>
      <div>
        <div className="text-small font-semibold flex items-center gap-2">
          {f.label}
          {f.required && <span className="text-[10.5px] font-normal text-[var(--color-text-muted)] border border-[var(--color-border)] rounded px-1">Required</span>}
        </div>
        <div className="text-meta text-[var(--color-text-muted)] mt-0.5">{f.help}</div>
        <div className="mt-2 flex items-center gap-2"><KindTag kind={f.kind} label={f.kindLabel} /><SourceLink src={source} agent={agent} /></div>
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-[var(--color-border)] px-3 py-2">
      <div className="text-small font-semibold">{title}</div>
      {children && <div className="text-meta text-[var(--color-text-muted)]">{children}</div>}
    </div>
  );
}

function Table({ head, rows, testid }: { head: string[]; rows: React.ReactNode[][]; testid?: string }) {
  return (
    <table className="w-full text-small" data-testid={testid}>
      <thead><tr>{head.map((h) => <th key={h} className="text-left font-normal text-meta text-[var(--color-text-muted)] pb-1.5 pr-3 border-b border-[var(--color-border)]">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="py-1.5 pr-3 align-top border-b border-[var(--color-border)]">{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

/** Read-only views of the fields that come from the Brain or the product. */
function View({ f }: { f: AgentFormField }) {
  const v = f.value as unknown;
  switch (f.key) {
    case 'repos': {
      const rows = (v as { name: string; local: string | null; remote: string | null }[]) ?? [];
      return rows.length === 0
        ? <Empty title="No repository">A Brain without code: its knowledge is its documents. Bind a repo in the Brain to add one.</Empty>
        : <Table head={['Repository', 'On this machine', 'Remote']} testid="pa-repos"
            rows={rows.map((r) => [<b key="n">{r.name}</b>, <code key="l" className="text-meta">{r.local ?? 'not checked out here'}</code>,
              <span key="r" className="text-meta text-[var(--color-text-muted)]">{r.remote ?? '—'}</span>])} />;
    }
    case 'skills': {
      const rows = (v as { name: string; description: string; modes: string[]; builtin: boolean; hive: string }[]) ?? [];
      return rows.length === 0 ? <Empty title="No skills of its own" />
        : <Table head={['Skill', 'What it does', 'Used in', 'On a Hive']} testid="pa-skills"
            rows={rows.map((s) => [<span key="n"><code className="text-meta">{s.name}</code>{s.builtin && <span className="ml-1.5 text-[10.5px] text-[var(--color-text-muted)]">built-in</span>}</span>,
              <span key="d" className="text-[var(--color-text-muted)]">{s.description}</span>, s.modes.join(', ') || '—',
              <span key="h" className={s.hive === 'yes' ? 'text-[var(--color-status-success)]' : 'text-[var(--color-text-muted)]'}>{s.hive}</span>])} />;
    }
    case 'tools': {
      const rows = (v as { name: string; kind: string; hive: string }[]) ?? [];
      return rows.length === 0
        ? <Empty title="None declared">A data Brain declares its client and checks in aim.json. MCP servers are set up on each machine; they do not travel with the Brain.</Empty>
        : <Table head={['Tool', 'Kind', 'On a Hive']} rows={rows.map((t) => [<code key="n" className="text-meta">{t.name}</code>, t.kind, t.hive])} />;
    }
    case 'jobs': {
      const rows = (v as { name: string }[]) ?? [];
      return rows.length === 0 ? <Empty title="None">Scheduled work this Brain owns appears here.</Empty>
        : <div className="text-small">{rows.map((j) => j.name).join(' · ')}</div>;
    }
    case 'product_guards':
      return <div>{((v as { name: string; what: string }[]) ?? []).map((g) => (
        <div key={g.name} className="grid grid-cols-[200px_1fr] gap-3 py-1 text-small"><b>{g.name}</b><span className="text-[var(--color-text-muted)]">{g.what}</span></div>))}</div>;
    case 'profiles':
      // no mode picks a profile (the profile is chosen per dispatch, design 2026-10-30-run-spec)
      return <Table head={['Profile', 'For', 'Stages']} testid="pa-profiles"
        rows={((v as { name: string; for: string; stages: string[] }[]) ?? []).map((p) => [
          <b key="n">{p.name}</b>, p.for, <span key="s" className="text-meta text-[var(--color-text-muted)]">{p.stages.join(' → ')}</span>])} />;
    default:
      return <div className="text-small py-1">{typeof v === 'string' ? v : JSON.stringify(v)}</div>;
  }
}

/** One editable value, drawn by its shape. `onSet` records a new value; `onDrop` a row. */
function Editor({ f, value, path, canEdit, pending, onSet, onDrop }: {
  f: AgentFormField; value: unknown; path: string; canEdit: boolean;
  pending: Pending; onSet: (path: string, v: unknown) => void; onDrop: (path: string, i: number) => void;
}) {
  const current = pending.set.has(path) ? pending.set.get(path) : value;
  const writable = canEdit && f.editable && (f.kind !== 'brain' || f.shape === 'names');
  const [draft, setDraft] = useState('');
  if (f.shape === 'text') {
    return writable
      ? <input className={INPUT} value={(current as string) ?? ''} placeholder={f.example ? `e.g. ${f.example}` : ''}
          onChange={(e) => onSet(path, e.target.value)} data-testid={`pa-input-${path}`} />
      : <div className="text-small py-1">{(current as string) || <span className="text-[var(--color-text-muted)]">—</span>}</div>;
  }
  if (f.shape === 'list') {
    const items = (current as string[]) ?? [];
    return (
      <div className={BOX} data-testid={`pa-list-${path}`}>
        {items.length === 0 && <div className="px-2.5 py-1.5 text-small text-[var(--color-text-muted)]">{f.example ? `e.g. ${f.example}` : 'None'}</div>}
        {items.map((x, i) => (
          <div key={i} className="flex justify-between gap-3 px-2.5 py-1.5 border-b border-[var(--color-border)] text-small">
            <span>{x}</span>
            {writable && <button className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title="Remove" aria-label={`Remove "${x}"`}
              onClick={() => onSet(path, items.filter((_, j) => j !== i))}>×</button>}
          </div>))}
        {writable && (
          <div className="flex gap-2 px-2.5 py-1.5">
            <input className="flex-1 bg-transparent text-small outline-none" value={draft} placeholder="+ Add a line"
              aria-label={`Add to ${f.label}`}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => { if (draft.trim()) { onSet(path, [...items, draft.trim()]); setDraft(''); } }}
              onKeyDown={(e) => { if (e.key === 'Enter' && draft.trim()) { onSet(path, [...items, draft.trim()]); setDraft(''); } }} />
            <button className="text-meta text-[var(--color-text-muted)] disabled:opacity-40" disabled={!draft.trim()}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { onSet(path, [...items, draft.trim()]); setDraft(''); }}>Add</button>
          </div>)}
      </div>
    );
  }
  if (f.shape === 'choice') {
    const opts = (f.choices ?? []).map(choiceOf);
    return (
      <div className="inline-flex rounded-md border border-[var(--color-border)] overflow-hidden" data-testid={`pa-choice-${path}`}>
        {opts.map((o) => (
          <button key={o.value} disabled={!writable} aria-pressed={o.value === current}
            className={`px-3 py-1 text-meta border-r border-[var(--color-border)] last:border-r-0 ${o.value === current ? 'bg-[var(--color-hover)] font-semibold text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}`}
            onClick={() => onSet(path, o.value)}>{o.label}</button>))}
      </div>
    );
  }
  if (f.shape === 'names') {
    const chosen = new Set((current as string[]) ?? []);
    const original = new Set((value as string[]) ?? []);
    const opts = (f.choices ?? []).map(choiceOf);
    if (opts.length === 0) return <Empty title={f.key === 'gates' ? 'No gates yet' : 'None'}>{f.key === 'gates' ? "A gate starts as a pitfall in the Brain's knowledge and becomes a script in 3-gates/ once it is proven." : null}</Empty>;
    return (
      <div className="flex flex-col gap-2" data-testid={`pa-names-${path}`}>
        {opts.map((o) => {
          const on = chosen.has(o.value);
          // a gate may only be unticked here; a fixed or unavailable place cannot change
          const locked = !writable || o.fixed || o.available === false || (f.removable && f.kind === 'brain' && !original.has(o.value));
          return (
            <label key={o.value} className={`flex items-start gap-2.5 text-small ${o.available === false ? 'opacity-60' : ''}`}>
              <input type="checkbox" className="mt-0.5" checked={on} disabled={locked} data-testid={`pa-check-${path}-${o.value}`}
                onChange={() => onSet(path, on ? [...chosen].filter((x) => x !== o.value) : [...chosen, o.value])} />
              <span><b className="font-semibold">{o.label}</b>{o.where && <span className="ml-2 text-[10.5px] border border-[var(--color-border)] rounded px-1 text-[var(--color-text-muted)]">{o.where}</span>}
                {o.help && <span className="block text-meta text-[var(--color-text-muted)]">{o.help}</span>}
                {o.available === false && <span className="block text-meta text-[var(--color-status-warning)]">not available yet</span>}
                {f.kind === 'brain' && f.removable && !original.has(o.value) && <span className="block text-meta text-[var(--color-text-muted)]">not enforced; redraft from the Brain to enforce it again</span>}
              </span>
            </label>);
        })}
      </div>
    );
  }
  if (f.shape === 'rows') {
    const rows = ((current as Rows) ?? []);
    const dropped = pending.drop.get(path) ?? new Set<number>();
    const cols = f.columns.filter((c) => c !== 'source');
    const removeOnly = f.kind === 'brain';
    return (
      <div className={BOX} data-testid={`pa-rows-${path}`}>
        {rows.length === 0 && <div className="px-2.5 py-1.5 text-small text-[var(--color-text-muted)]">None</div>}
        {rows.map((r, i) => (
          <div key={i} className={`flex gap-3 px-2.5 py-1.5 border-b border-[var(--color-border)] text-small ${dropped.has(i) ? 'line-through opacity-50' : ''}`}>
            <div className="flex-1 min-w-0 flex flex-wrap gap-x-3">
              {cols.map((c, j) => c === 'command'
                ? <code key={c} className="text-meta whitespace-pre-wrap break-all">{(r[c] ?? '').split('\n')[0]}{(r[c] ?? '').includes('\n') ? ' …' : ''}</code>
                : <span key={c} className={j === 0 ? 'font-semibold' : 'text-[var(--color-text-muted)]'}>{c === 'cwd' ? (r[c] || 'repo root') : r[c]}</span>)}
            </div>
            {canEdit && (removeOnly ? f.removable : writable) && (
              <button className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title={dropped.has(i) ? 'Keep' : 'Remove'}
                aria-label={dropped.has(i) ? 'Keep this row' : 'Remove this row'}
                onClick={() => removeOnly ? onDrop(path, i) : onSet(path, rows.filter((_, j) => j !== i))}>{dropped.has(i) ? 'undo' : '×'}</button>)}
          </div>))}
        {writable && !removeOnly && cols.length > 0 && <RowAdder cols={cols}
          onAdd={(row) => onSet(path, [...rows, row])} />}
      </div>
    );
  }
  return null;
}

function RowAdder({ cols, onAdd }: { cols: string[]; onAdd: (row: Record<string, string>) => void }) {
  const [row, setRow] = useState<Record<string, string>>({});
  const full = cols.every((c) => (row[c] ?? '').trim());
  return (
    <div className="flex gap-2 px-2.5 py-1.5">
      {cols.map((c) => <input key={c} className="flex-1 min-w-0 bg-transparent text-small outline-none" placeholder={`+ ${c}`} aria-label={c}
            value={row[c] ?? ''} onChange={(e) => setRow({ ...row, [c]: e.target.value })} />)}
      <button className="text-meta text-[var(--color-text-muted)] disabled:opacity-40" disabled={!full}
        onClick={() => { onAdd(Object.fromEntries(cols.map((c) => [c, row[c].trim()]))); setRow({}); }}>Add</button>
    </div>
  );
}

export function ProjectAgentForm({ brain, agent, onRevise }: {
  /** the Brain's name; null = the Runner Agent */
  brain: string | null;
  agent: ProjectAgent;
  onRevise: (note: string) => void;
}) {
  const qc = useQueryClient();
  const form = agent.form;
  const [pending, setPending] = useState<Pending>(() => ({ set: new Map(), drop: new Map() }));
  const [modeId, setModeId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [seenRevision, setSeenRevision] = useState(agent.revision);
  const problems = agent.problems ?? [];
  // the confirmation holds (core/project_agent.confirmation); an older daemon sends no
  // confirmation: its status alone
  const confirmation = agent.confirmation ?? (agent.status === 'reviewed' ? 'reviewed' : 'draft');
  const reviewed = confirmation === 'reviewed';
  const changed = confirmation === 'changed';
  const widened = agent.widened ?? [];
  const agentConfirms = agent.agentConfirms ?? [];
  const readOnly = form?.readOnly === true;
  const canEdit = !busy && !readOnly;
  const count = pending.set.size + [...pending.drop.values()].reduce((n, s) => n + s.size, 0);
  const selectedMode = (modeId && form?.modes.some((m) => m.id === modeId) ? modeId : null)
    ?? form?.modes.find((m) => m.isDefault)?.id ?? form?.modes[0]?.id ?? null;

  const onSet = (path: string, v: unknown) => setPending((p) => ({ ...p, set: new Map(p.set).set(path, v) }));
  const onDrop = (path: string, i: number) => setPending((p) => {
    const drop = new Map(p.drop);
    const s = new Set(drop.get(path) ?? []);
    if (s.has(i)) s.delete(i); else s.add(i);
    if (s.size) drop.set(path, s); else drop.delete(path);
    return { ...p, drop };
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: brain === null ? ['runner-agent'] : ['brain-detail', brain] });

  // The definition changed under the page (a save, chat, the agent): unsaved changes
  // are kept, never dropped, and the person is told to check them before saving.
  useEffect(() => {
    if (agent.revision === seenRevision) return;
    setSeenRevision(agent.revision);
    if (count > 0) setNotice(`The definition changed since you started. Your ${count} unsaved ${count === 1 ? 'change is' : 'changes are'} kept: check ${count === 1 ? 'it' : 'them'}, then save.`);
  }, [agent.revision, seenRevision, count]);

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      await work();
      setPending({ set: new Map(), drop: new Map() });
      setSeenRevision(undefined);          // the next revision is ours, not a change under the page
      refresh();
      void qc.invalidateQueries({ queryKey: ['brains-with-pinned'] });
    } catch (e: unknown) {
      const resp = (e as { response?: { status?: number; data?: { detail?: unknown } } }).response;
      const d = resp?.data?.detail;
      const msg = typeof d === 'string' ? d
        : d && typeof d === 'object' && 'message' in d
          ? `${(d as { message: string }).message}: ${((d as { problems?: string[] }).problems ?? []).join(' · ')}`
          : 'Could not save. Try again.';
      if (resp?.status === 409) refresh();
      setError(resp?.status === 409 ? 'The definition changed meanwhile. Your changes are kept; check them against the current version and save again.' : msg);
    } finally {
      setBusy(false);
    }
  };
  // why Confirm is not offered, or true when it is
  const confirmBlock: true | string = !agent.revision ? 'The definition could not be read; open the YAML.'
    : problems.length > 0 ? 'Fix the problems listed at the top first.'
    : (form?.missing.length ?? 0) > 0 ? `Fill in first: ${form!.missing.join(', ')}.`
    : true;
  const save = () => {
    const changes: AgentChange[] = [...pending.set.entries()].map(([path, value]) => ({ path, value }));
    for (const [path, idx] of pending.drop) {
      // drop from the highest row down: each removal shifts the rows after it
      for (const i of [...idx].sort((a, b) => b - a)) changes.push({ path, remove: i });
    }
    return run(() => editProjectAgent(brain, agent.revision ?? '', changes));
  };
  const confirm = () => run(() => confirmProjectAgent(brain, agent.revision ?? ''));

  const sectionState = useMemo(() => {
    const out: Record<string, boolean> = {};
    for (const s of form?.sections ?? []) out[s.id] = !s.fields.some((f) => form?.missing.some((m) => m === f.label || m.startsWith(`${f.label} (`)));
    return out;
  }, [form]);

  if (!form) {
    return <div className="text-small text-[var(--color-status-error)]" data-testid="pa-form-missing">
      The form could not be built from this definition{problems.length ? `: ${problems.join(' · ')}` : '.'} Open the file to see it.
    </div>;
  }

  const sourceOf = (f: AgentFormField): string | undefined =>
    f.key === 'summary' || f.key === 'in_scope' || f.key === 'out_of_scope' ? agent.identity?.source : undefined;

  const renderField = (f: AgentFormField) => {
    if (f.shape === 'view') return <FieldRow key={f.key} f={f} agent={agent}><View f={f} /></FieldRow>;
    if (f.scope === 'mode') {
      // a mode's own field outside the Modes section (profile rules in Judgment): one block per mode
      const ids = Object.keys(f.paths ?? {});
      return (
        <FieldRow key={f.key} f={f} agent={agent}>
          <div className="flex flex-col gap-3">{ids.map((mid) => (
            <div key={mid}><div className="text-meta text-[var(--color-text-muted)] mb-1">{mid}</div>
              <Editor f={f} value={f.values?.[mid]} path={f.paths![mid]} canEdit={canEdit} pending={pending} onSet={onSet} onDrop={onDrop} />
            </div>))}</div>
        </FieldRow>);
    }
    return (
      <FieldRow key={f.key} f={f} agent={agent} source={sourceOf(f)}>
        <Editor f={f} value={f.value} path={f.path!} canEdit={canEdit} pending={pending} onSet={onSet} onDrop={onDrop} />
        {f.hive && f.hive.local_modes.length > 0 && (
          <div className="mt-3 text-meta text-[var(--color-text-muted)]" data-testid="pa-hive-local">
            {f.hive.local_modes.map((m) => <div key={m.mode}><b className="text-[var(--color-text)]">{m.mode}</b>: {m.why}</div>)}
          </div>)}
      </FieldRow>);
  };

  const modeSection = (s: { fields: AgentFormField[] }) => {
    const modeFields = s.fields.filter((f) => f.scope === 'mode');
    const shared = s.fields.filter((f) => f.scope !== 'mode');
    const when = modeFields.find((f) => f.key === 'when');
    const checks = modeFields.find((f) => f.key === 'validations');
    const repos = modeFields.find((f) => f.key === 'mode_repos');
    return (
      <>
        <table className="w-full text-small mt-1" data-testid="pa-modes">
          <thead><tr>{['Mode', 'Used when', 'Checks'].map((h) => <th key={h} className="text-left font-normal text-meta text-[var(--color-text-muted)] pb-1.5 pr-3 border-b border-[var(--color-border)]">{h}</th>)}</tr></thead>
          <tbody>{form.modes.map((m) => {
            // what the table shows is what Save would write: unsaved values included
            const live = (f: AgentFormField | undefined) => {
              const path = f?.paths?.[m.id];
              return path && pending.set.has(path) ? pending.set.get(path) : f?.values?.[m.id];
            };
            const edited = modeFields.some((f) => {
              const path = f.paths?.[m.id];
              return !!path && (pending.set.has(path) || pending.drop.has(path));
            });
            const dropped = checks?.paths?.[m.id] ? (pending.drop.get(checks.paths[m.id])?.size ?? 0) : 0;
            return (
            <tr key={m.id} className={`cursor-pointer ${m.id === selectedMode ? 'bg-[var(--color-hover)]' : ''}`} onClick={() => setModeId(m.id)}
              tabIndex={0} role="button" aria-pressed={m.id === selectedMode}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setModeId(m.id); } }}
              data-testid={`pa-mode-${m.id}`}>
              <td className="py-2 pr-3 align-top border-b border-[var(--color-border)]"><b>{m.id}</b>
                {edited && <span className="ml-1.5 text-[var(--color-status-warning)]" title="unsaved changes" data-testid={`pa-mode-edited-${m.id}`}>●</span>}
                {m.isDefault && <span className="ml-1.5 text-[10.5px] text-[var(--color-text-muted)] border border-[var(--color-border)] rounded px-1">default</span>}
                {m.standard && <span className="ml-1.5 text-[10.5px] text-[var(--color-text-muted)] border border-[var(--color-border)] rounded px-1">standard</span>}</td>
              <td className="py-2 pr-3 align-top border-b border-[var(--color-border)] text-[var(--color-text-muted)]">{String(live(when) ?? '')}</td>
              <td className="py-2 pr-3 align-top border-b border-[var(--color-border)] text-[var(--color-text-muted)]">{((checks?.values?.[m.id] as Rows) ?? []).length - dropped}</td>
            </tr>);
          })}</tbody>
        </table>
        {selectedMode && (
          <div className="mt-3 rounded-lg border border-[color-mix(in_srgb,var(--color-primary)_40%,transparent)] px-4" data-testid="pa-mode-editor">
            <div className="py-2.5 text-meta text-[var(--color-text-muted)] border-b border-[var(--color-border)]">Editing <b className="text-[var(--color-text)]">{selectedMode}</b></div>
            {modeFields.map((f) => {
              if (!f.paths?.[selectedMode]) return null;
              const v = f.values?.[selectedMode];
              if ((f.key === 'setup' || f.key === 'validations' || f.key === 'delivery') && ((repos?.values?.[selectedMode] as string[]) ?? []).length === 0) return null;
              if (f.key === 'mode_repos' && ((v as string[]) ?? []).length === 0) return null;
              return (
                <FieldRow key={`${f.key}:${selectedMode}`} f={f} agent={agent}>
                  {f.shape === 'names' && f.kind === 'brain'
                    ? <div className="text-small py-1">{((v as string[]) ?? []).join(' · ')}</div>
                    : <Editor f={f} value={v} path={f.paths[selectedMode]} canEdit={canEdit} pending={pending} onSet={onSet} onDrop={onDrop} />}
                </FieldRow>);
            })}
          </div>)}
        {shared.map(renderField)}
      </>
    );
  };

  return (
    <div data-testid="bm-agent-def" className="pb-20">
      <div className="flex items-center gap-3 flex-wrap">
        {problems.length > 0
          ? <StatusBadge tone="error" testid="bm-agent-status">has problems to fix</StatusBadge>
          : form.skeleton
            ? <StatusBadge tone="warning" testid="bm-agent-status">not drafted yet</StatusBadge>
            : reviewed
              ? <StatusBadge tone="success" testid="bm-agent-status">confirmed{agent.reviewedAt ? ` · ${agent.reviewedAt.slice(0, 10)}` : ''}{agent.reviewedBy ? (agent.reviewedBy === 'agent' ? ' by the agent' : ` by ${agent.reviewedBy}`) : ''}</StatusBadge>
              : widened.length > 0
                ? <StatusBadge tone="warning" testid="bm-agent-status">waits for you · it gives the agent more than you allowed</StatusBadge>
                : <StatusBadge tone="neutral" testid="bm-agent-status">{changed ? 'changed' : 'draft'} · the agent confirms it before its next dispatch</StatusBadge>}
        {agent.revision && <span className="text-meta text-[var(--color-text-muted)]" data-testid="pa-revision">version {agent.revision.slice(0, 7)}</span>}
        <span className="flex-1" />
        <button className="btn-sm btn-ghost" data-testid="pa-open-file"
          onClick={() => openPath(brain === null ? 'Knowledge/Library/project-agent.yaml' : `Projects/${brain}/project-agent.yaml`)}>Open YAML</button>
      </div>
      <p className="mt-2 text-small text-[var(--color-text-muted)]" data-testid="pa-intro">
        The agent that takes this Brain&apos;s work: <b className="text-[var(--color-text)]">what it accepts, how it does each kind of work, and what it never does without you.</b> Every Brain has this same form. The agent drafts it from the Brain&apos;s documents, keeps it current and confirms it itself. You confirm only what gives the agent more than you last allowed: an action kept for you, a gate, or how it delivers. You can change or tighten any field here at any time.
      </p>
      <div className="mt-2 flex flex-wrap gap-4">{KIND_LEGEND.map(([k, l, d]) => (
        <span key={k} className="flex items-center gap-1.5 text-meta text-[var(--color-text-muted)]"><KindTag kind={k} label={l} />{d}</span>))}</div>
      {readOnly && <div className="mt-2 text-meta text-[var(--color-status-warning)]" data-testid="pa-read-only">This Hive runs the definition your desktop sends it: change and confirm it on the desktop.</div>}
      {notice && <div className="mt-2 text-meta text-[var(--color-status-warning)]" data-testid="pa-notice">{notice}</div>}
      {problems.length > 0 && <div className="mt-2 text-meta text-[var(--color-status-error)]" data-testid="bm-agent-problems">{problems.join(' · ')}</div>}
      {!reviewed && widened.length > 0 && <div className="mt-2 text-meta text-[var(--color-status-warning)]" data-testid="pa-widened">Only you can confirm these: {widened.join(' · ')}</div>}
      {agentConfirms.length > 0 && (
        <details className="mt-2 text-meta text-[var(--color-text-muted)]" data-testid="pa-agent-confirms">
          <summary>{agentConfirms.length === 1 ? 'The agent confirmed it once' : `The agent confirmed it ${agentConfirms.length} times`} since {agent.floorSetBy ? `you (${agent.floorSetBy})` : 'it was created'}</summary>
          {agentConfirms.slice().reverse().map((e) => (
            <div key={`${e.at}-${e.revision}`}>{e.at.slice(0, 16).replace('T', ' ')} · {e.why} · changed: {e.changed.join(', ')}</div>))}
        </details>)}
      {(agent.notes ?? []).length > 0 && <div className="mt-2 text-meta text-[var(--color-status-warning)]" data-testid="pa-notes">{(agent.notes ?? []).join(' · ')}</div>}
      {form.missing.length > 0 && <div className="mt-2 text-meta text-[var(--color-status-warning)]" data-testid="pa-missing">Still empty: {form.missing.join(' · ')}</div>}

      <div className="mt-4 grid grid-cols-[170px_1fr] gap-8">
        <nav className="sticky top-2 self-start flex flex-col gap-0.5" aria-label="Sections" data-testid="pa-nav">
          {form.sections.map((s, i) => (
            <a key={s.id} href={`#pa-${s.id}`} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-small text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
              onClick={(e) => { e.preventDefault(); document.getElementById(`pa-${s.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>
              <span className={`w-4 h-4 rounded-full text-[10px] flex items-center justify-center ${sectionState[s.id] ? 'bg-[color-mix(in_srgb,var(--color-status-success)_20%,transparent)] text-[var(--color-status-success)]' : 'bg-[color-mix(in_srgb,var(--color-status-warning)_20%,transparent)] text-[var(--color-status-warning)]'}`}>{sectionState[s.id] ? '✓' : i + 1}</span>
              {s.title}
            </a>))}
        </nav>
        <div className="min-w-0 flex flex-col gap-5">
          {form.sections.map((s, i) => (
            <section key={s.id} id={`pa-${s.id}`} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-5" data-testid={`pa-section-${s.id}`}>
              <div className="flex gap-3 py-4 border-b border-[var(--color-border)]">
                <span className="w-6 h-6 rounded-md border border-[var(--color-border)] text-meta text-[var(--color-text-muted)] flex items-center justify-center shrink-0">{i + 1}</span>
                <div><h3 className="text-heading font-semibold">{s.title}</h3><p className="text-meta text-[var(--color-text-muted)] mt-0.5">{s.purpose}</p></div>
              </div>
              {s.id === 'modes' ? modeSection(s) : s.fields.map(renderField)}
              {s.id === 'guardrails' && (
                <div className="my-3 pl-3 border-l-2 border-[var(--color-status-warning)] text-meta text-[var(--color-text-muted)]" data-testid="pa-enforced">{form.enforced}</div>)}
            </section>))}
        </div>
      </div>

      <div className="sticky bottom-0 mt-6 -mx-3 px-3 py-2.5 border-t border-[var(--color-border)] bg-[var(--color-card)] flex items-center gap-3" data-testid="pa-bar">
        {count > 0 && <span className="text-meta text-[var(--color-status-warning)]" data-testid="pa-count">{count} {count === 1 ? 'change' : 'changes'}</span>}
        {error && <span className="text-meta text-[var(--color-status-error)]" data-testid="pa-error">{error}</span>}
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Or ask in chat for a bigger change"
          className="flex-1 min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1 text-small" data-testid="bm-agent-note" />
        <button className="btn-sm btn-secondary" disabled={!note.trim()} onClick={() => { onRevise(note.trim()); setNote(''); }} data-testid="bm-agent-revise">Ask in chat</button>
        {readOnly
          ? null
          : count > 0
          ? <>
              <button className="btn-sm btn-ghost" disabled={busy} onClick={() => { setPending({ set: new Map(), drop: new Map() }); setError(null); setNotice(null); }} data-testid="pa-discard">Discard</button>
              <button className="btn-sm btn-primary" disabled={busy || !agent.revision} onClick={() => void save()} data-testid="pa-save">
                {reviewed ? 'Save as draft' : `Save ${count === 1 ? 'change' : 'changes'}`}</button>
            </>
          : !reviewed && <button className="btn-sm btn-primary" disabled={busy || confirmBlock !== true} onClick={() => void confirm()}
              title={confirmBlock === true ? undefined : confirmBlock} data-testid="bm-agent-confirm">Confirm</button>}
      </div>
      {!readOnly && (count > 0 && reviewed || (count === 0 && !reviewed && confirmBlock !== true)) && (
        <div className="mt-1 text-meta text-[var(--color-text-muted)]" data-testid="pa-bar-note">
          {count > 0 ? 'Saving makes this a draft again: it does not run on a Hive until you confirm it.' : confirmBlock}
        </div>)}
    </div>
  );
}
