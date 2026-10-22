/**
 * CanvasChanges — the Canvas "Changes" tab: each bound code repo's local state
 * (services/canvasChanges → core/repo_changes), read-only.
 *
 * Two layers, like the Artifacts tab: `ChangesNav` is the navigation list in the
 * Canvas header (one card per repo: name, project, branch, ahead / uncommitted /
 * worktrees, then its unpushed commits), `ChangesDetail` is the content card below
 * it (the selected commit's files and one file's patch, or the uncommitted files,
 * or the worktrees). Selection lives in the parent (FileViewerPanel) so both halves
 * agree. No action buttons: pushing or removing a worktree is not done from here.
 *
 * @exports ChangesNav, ChangesDetail, ChangesCount, ChangesRefresh, useRepoChanges,
 *   refreshRepoChanges, resetRepoChanges, changesKey, ChangesSelection, diffLineClass
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { canvasChangesService, type ChangesRepo, type CommitFile } from '../../services/canvasChanges';

export type ChangesSelection =
  | { kind: 'commit'; root: string; sha: string }
  | { kind: 'dirty'; root: string }
  | { kind: 'worktrees'; root: string }
  | null;

// ── Repo state store ─────────────────────────────────────────────────────────
// Module-level and subscribed to only by the small Changes components (count,
// nav, detail): the FileViewerPanel never holds this state, so a fetch never
// re-renders the heavy file viewer (FileViewerPanel.memo.test).
interface ChangesState { repos: ChangesRepo[] | null; error: string | null; loading: boolean }
let state: ChangesState = { repos: null, error: null, loading: false };
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;
function set(next: Partial<ChangesState>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}
function subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }
const snapshot = () => state;

/** Refetch every repo's state; concurrent calls share one request. */
export function refreshRepoChanges(): Promise<void> {
  if (inflight) return inflight;
  set({ loading: true });
  inflight = canvasChangesService.repos()
    .then((repos) => set({ repos, error: null }))
    .catch((e) => set({ error: e instanceof Error ? e.message : String(e) }))
    .finally(() => { inflight = null; set({ loading: false }); });
  return inflight;
}

/** Test seam: back to the never-fetched state. */
export function resetRepoChanges() { state = { repos: null, error: null, loading: false }; inflight = null; }

export function useRepoChanges(): ChangesState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** The Changes tab's count badge (repos with local state); fetches once on mount. */
export function ChangesCount() {
  const { repos } = useRepoChanges();
  useEffect(() => { if (state.repos === null) void refreshRepoChanges(); }, []);
  const n = repos?.length ?? 0;
  return n > 0
    ? <span className="text-[10.5px] px-1.5 rounded-full tabular-nums bg-[color-mix(in_srgb,var(--color-text)_10%,transparent)]">{n}</span>
    : null;
}

/** The refresh button of the Changes tab. */
export function ChangesRefresh() {
  const { loading } = useRepoChanges();
  return (
    <button type="button" onClick={() => void refreshRepoChanges()} title="Refresh repo state"
      aria-label="Refresh repo state" data-testid="canvas-changes-refresh"
      className="ml-1 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]">
      <span className={`material-symbols-outlined text-[14px] block ${loading ? 'animate-spin' : ''}`}>refresh</span>
    </button>
  );
}

/** A stable key for a selection (row highlight). */
export function changesKey(s: ChangesSelection): string {
  if (!s) return '';
  return s.kind === 'commit' ? `${s.root}@${s.sha}` : `${s.root}#${s.kind}`;
}

const SEL = 'bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] text-[var(--color-text)]';
const HOVER = 'hover:bg-[color-mix(in_srgb,var(--color-text)_5%,transparent)]';
const SHOWN = 8;

function Delta({ ins, del }: { ins: number | null; del: number | null }) {
  return (
    <span className="shrink-0 text-[11px] tabular-nums whitespace-nowrap">
      {ins !== null && <span className="text-[var(--color-git-added)]">+{ins}</span>}
      {ins !== null && del !== null && ' '}
      {del !== null && <span className="text-[var(--color-git-deleted,#f85149)]">−{del}</span>}
      {ins === null && del === null && <span className="text-[var(--color-text-dim)]">bin</span>}
    </span>
  );
}

function Stat({ value, label, onClick, selected }: { value: string; label: string; onClick?: () => void; selected?: boolean }) {
  const body = (
    <>
      <span className="font-semibold text-[var(--color-text)] tabular-nums">{value}</span>
      <span>{label}</span>
    </>
  );
  return onClick
    ? <button type="button" onClick={onClick} className={`inline-flex gap-1 items-baseline rounded px-1 -mx-1 ${selected ? SEL : HOVER}`}>{body}</button>
    : <span className="inline-flex gap-1 items-baseline">{body}</span>;
}

function RepoCard({ repo, selected, onSelect }: {
  repo: ChangesRepo; selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const [more, setMore] = useState(false);
  const key = changesKey(selected);
  const commits = repo.commits ?? [];
  const shown = more ? commits : commits.slice(0, SHOWN);
  const stale = (repo.worktrees ?? []).filter((w) => w.prunable).length;
  const wt = repo.worktrees?.length ?? 0;
  return (
    <div data-testid="changes-repo" className="mb-1.5">
      <div className="rounded-lg px-2.5 py-2 bg-[color-mix(in_srgb,var(--color-text)_4%,transparent)]" title={repo.root}>
        <div className="flex items-center gap-2 min-w-0 text-[12.5px]">
          <span aria-hidden="true" className={`w-[7px] h-[7px] rounded-full shrink-0 ${repo.error ? 'bg-[var(--color-git-deleted,#f85149)]' : 'bg-[var(--color-git-modified)]'}`} />
          <span className="font-semibold truncate">{repo.name}</span>
          {repo.project && <span className="shrink-0 text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]">{repo.project}</span>}
          {repo.branch && <span className="shrink-0 font-mono text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]">{repo.branch}</span>}
        </div>
        {repo.error
          ? <div className="mt-1 text-[11px] text-[var(--color-git-deleted,#f85149)]">{repo.error}</div>
          : (
            <div className="mt-1 flex flex-wrap gap-x-3.5 text-[11px] text-[var(--color-text-muted)]">
              <Stat value={repo.ahead === null || repo.ahead === undefined ? '—' : String(repo.ahead)}
                label={repo.ahead === null ? 'no upstream' : 'ahead'} />
              <Stat value={String(repo.dirty_count ?? 0)} label="uncommitted"
                onClick={repo.dirty_count ? () => onSelect({ kind: 'dirty', root: repo.root }) : undefined}
                selected={key === `${repo.root}#dirty`} />
              <Stat value={String(wt)} label={stale ? `worktrees · ${stale} stale` : 'worktrees'}
                onClick={wt ? () => onSelect({ kind: 'worktrees', root: repo.root }) : undefined}
                selected={key === `${repo.root}#worktrees`} />
            </div>
          )}
      </div>
      {shown.map((c) => (
        <button key={c.sha} type="button" data-testid="changes-commit"
          onClick={() => onSelect({ kind: 'commit', root: repo.root, sha: c.sha })}
          title={`${c.short} · ${c.at}`}
          className={`w-full flex items-center gap-2 pl-5 pr-2 py-[5px] rounded-md text-left text-[12px] text-[var(--color-text-secondary)] ${key === `${repo.root}@${c.sha}` ? SEL : HOVER}`}>
          <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{c.short}</span>
          <span className="flex-1 min-w-0 truncate">{c.subject}</span>
          <Delta ins={c.insertions} del={c.deletions} />
        </button>
      ))}
      {commits.length > SHOWN && (
        <button type="button" onClick={() => setMore((m) => !m)}
          className={`pl-5 pr-2 py-1 rounded-md text-[11.5px] text-[var(--color-text-muted)] ${HOVER}`}>
          {more ? '▾ fewer' : `▸ ${commits.length - SHOWN} more`}
        </button>
      )}
      {(repo.ahead ?? 0) > commits.length && (more || commits.length <= SHOWN) && (
        <div className="pl-5 py-1 text-[11px] text-[var(--color-text-dim)]">
          +{(repo.ahead ?? 0) - commits.length} older not listed
        </div>
      )}
    </div>
  );
}

/** The Changes navigation list; refetches each time it is shown (the tab opened). */
export function ChangesNav({ selected, onSelect }: {
  selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const { repos, error, loading } = useRepoChanges();
  useEffect(() => { void refreshRepoChanges(); }, []);
  const onRefresh = () => void refreshRepoChanges();
  if (error) {
    return (
      <div className="px-2 py-2 text-[12px] text-[var(--color-text-muted)]">
        Could not read the repos ({error}).{' '}
        <button type="button" onClick={onRefresh} className="underline hover:text-[var(--color-text)]">Retry</button>
      </div>
    );
  }
  if (!repos) return <div className="px-2 py-2 text-[12px] text-[var(--color-text-muted)]">Loading…</div>;
  if (!repos.length) {
    return <div className="px-2 py-2 text-[12px] text-[var(--color-text-muted)]">Every repo is clean and pushed.</div>;
  }
  return (
    <div data-testid="changes-nav" aria-busy={loading}>
      {repos.map((r) => <RepoCard key={r.root} repo={r} selected={selected} onSelect={onSelect} />)}
    </div>
  );
}

/** Patch line → its color class ('' for context). */
export function diffLineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-[var(--color-text-muted)]';
  if (line.startsWith('+')) return 'bg-[color-mix(in_srgb,var(--color-git-added)_13%,transparent)]';
  if (line.startsWith('-')) return 'bg-[color-mix(in_srgb,var(--color-git-deleted,#f85149)_13%,transparent)]';
  if (line.startsWith('@@')) return 'text-[var(--color-text-muted)]';
  return '';
}

function Patch({ root, sha, path }: { root: string; sha: string; path: string }) {
  const [state, setState] = useState<{ diff: string; truncated: boolean } | { error: string } | null>(null);
  useEffect(() => {
    let live = true;
    setState(null);
    canvasChangesService.commitDiff(root, sha, path)
      .then((d) => { if (live) setState(d); })
      .catch((e) => { if (live) setState({ error: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, [root, sha, path]);
  if (!state) return <div className="py-2 text-[12px] text-[var(--color-text-muted)]">Loading diff…</div>;
  if ('error' in state) return <div className="py-2 text-[12px] text-[var(--color-text-muted)]">Diff unavailable ({state.error}).</div>;
  const lines = state.diff.split('\n').filter((l) => !l.startsWith('diff --git') && !l.startsWith('index '));
  return (
    <div data-testid="changes-patch" className="mt-2 rounded-lg border border-[var(--color-border)] overflow-auto font-mono text-[11.5px] leading-[1.55]">
      {lines.map((l, i) => <div key={i} className={`px-2.5 whitespace-pre ${diffLineClass(l)}`}>{l || ' '}</div>)}
      {state.truncated && <div className="px-2.5 py-1 text-[var(--color-text-dim)]">Diff truncated.</div>}
    </div>
  );
}

function CommitDetail({ repo, sha }: { repo: ChangesRepo; sha: string }) {
  const [files, setFiles] = useState<CommitFile[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setFiles(null); setFailed(null); setOpen(null);
    canvasChangesService.commitFiles(repo.root, sha)
      .then((f) => { if (!live) return; setFiles(f); if (f.length === 1) setOpen(f[0].path); })
      .catch((e) => { if (live) setFailed(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [repo.root, sha]);
  if (failed) return <div className="text-[12px] text-[var(--color-text-muted)]">Files unavailable ({failed}).</div>;
  if (!files) return <div className="text-[12px] text-[var(--color-text-muted)]">Loading files…</div>;
  return (
    <div>
      {files.map((f) => (
        <div key={f.path}>
          <button type="button" data-testid="changes-file" onClick={() => setOpen((o) => (o === f.path ? null : f.path))}
            className={`w-full flex items-center gap-2 px-2 py-[5px] rounded-md text-left text-[12px] ${open === f.path ? SEL : HOVER}`}>
            <span className="font-mono flex-1 min-w-0 truncate" title={f.path}>{f.path}</span>
            <Delta ins={f.insertions} del={f.deletions} />
          </button>
          {open === f.path && <Patch root={repo.root} sha={sha} path={f.path} />}
        </div>
      ))}
    </div>
  );
}

export function ChangesDetail({ selected }: { selected: ChangesSelection }) {
  const { repos } = useRepoChanges();
  const repo = selected ? repos?.find((r) => r.root === selected.root) : undefined;
  if (!selected || !repo) {
    return (
      <div className="h-full flex items-center justify-center text-[12px] text-[var(--color-text-muted)]">
        Select a commit, the uncommitted files or the worktrees.
      </div>
    );
  }
  const commit = selected.kind === 'commit' ? repo.commits?.find((c) => c.sha === selected.sha) : undefined;
  const crumb = selected.kind === 'commit' ? commit?.short ?? selected.sha.slice(0, 8)
    : selected.kind === 'dirty' ? 'uncommitted' : 'worktrees';
  const title = selected.kind === 'commit' ? commit?.subject ?? selected.sha
    : selected.kind === 'dirty' ? `${repo.dirty_count ?? 0} uncommitted files`
      : `${repo.worktrees?.length ?? 0} worktrees`;
  return (
    <div data-testid="changes-detail" className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-[var(--color-border)]">
        <div className="text-[11px] text-[var(--color-text-muted)] truncate">Changes › {repo.name} › {crumb}</div>
        <div className="text-[13px] font-semibold truncate" title={title}>{title}</div>
      </div>
      <div className="flex-1 overflow-auto px-2 py-2">
        {selected.kind === 'commit' && <CommitDetail repo={repo} sha={selected.sha} />}
        {selected.kind === 'dirty' && (repo.dirty ?? []).map((d) => (
          <div key={d.path} className="flex items-center gap-2 px-2 py-[5px] text-[12px]">
            <span className={`w-4 text-center text-[11px] font-bold ${d.status === '?' || d.status === 'A' ? 'text-[var(--color-git-added)]' : d.status === 'D' ? 'text-[var(--color-git-deleted,#f85149)]' : 'text-[var(--color-git-modified)]'}`}>{d.status === '?' ? 'U' : d.status}</span>
            <span className="font-mono flex-1 min-w-0 truncate" title={d.path}>{d.path}</span>
          </div>
        ))}
        {selected.kind === 'dirty' && (repo.dirty_count ?? 0) > (repo.dirty?.length ?? 0) && (
          <div className="px-2 py-1 text-[11px] text-[var(--color-text-dim)]">+{(repo.dirty_count ?? 0) - (repo.dirty?.length ?? 0)} more</div>
        )}
        {selected.kind === 'worktrees' && (repo.worktrees ?? []).map((w) => (
          <div key={w.path} className="px-2 py-[6px] text-[12px]" title={w.path}>
            <div className="flex items-center gap-2">
              <span className="font-mono">{w.branch ?? (w.detached ? 'detached' : '—')}</span>
              {w.prunable && <span className="text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]">stale</span>}
            </div>
            <div className="font-mono text-[11px] text-[var(--color-text-dim)] truncate">{w.path}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
