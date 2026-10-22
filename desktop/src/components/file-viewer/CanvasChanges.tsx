/**
 * CanvasChanges — the Canvas "Changes" tab: what THIS session changed in code repos
 * (services/canvasSession → core/canvas_session), read-only.
 *
 * Two layers, like the Artifacts tab: `ChangesNav` is the navigation list in the
 * Canvas header (one card per repo: name, project, branch; the session's files with
 * their net +/- and where that content is now; the commits holding them),
 * `ChangesDetail` is the content card below it (a file's net diff over the session,
 * or a commit's files and patches). Selection lives in FileViewerPanel so both halves
 * agree. No action buttons: committing, pushing or removing is not done from here.
 *
 * @exports ChangesNav, ChangesDetail, ChangesCount, ChangesRefresh, changesKey,
 *   ChangesSelection, diffLineClass, STATE_LABEL
 */
import { useEffect, useState } from 'react';
import { chatService } from '../../services/chat';
import {
  canvasSessionService, refreshCanvasSession, useCanvasSession,
  type CommitFile, type FileState, type SessionRepo,
} from '../../services/canvasSession';

export type ChangesSelection =
  | { kind: 'file'; root: string; path: string }
  | { kind: 'commit'; root: string; sha: string }
  | null;

export function changesKey(s: ChangesSelection): string {
  if (!s) return '';
  return s.kind === 'commit' ? `${s.root}@${s.sha}` : `${s.root}:${s.path}`;
}

export const STATE_LABEL: Record<FileState, string> = {
  uncommitted: 'uncommitted',
  committed: 'committed',
  pushed: 'pushed',
  changed: 'changed since',
};
const STATE_TITLE: Record<FileState, string> = {
  uncommitted: 'The file on disk is what this session left; not committed yet.',
  committed: 'A local commit (not on any remote) holds what this session left.',
  pushed: 'The upstream branch holds what this session left.',
  changed: 'The file was changed (or reverted) after this session.',
};

const SEL = 'bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] text-[var(--color-text)]';
const HOVER = 'hover:bg-[color-mix(in_srgb,var(--color-text)_5%,transparent)]';
const SHOWN = 10;

function useRepos(): SessionRepo[] | null {
  const { data } = useCanvasSession();
  return data?.changes ?? null;
}

/** The Changes tab's count badge: files this session changed. */
export function ChangesCount() {
  const repos = useRepos();
  const n = (repos ?? []).reduce((s, r) => s + r.files.length, 0);
  return n > 0
    ? <span className="text-[10.5px] px-1.5 rounded-full tabular-nums bg-[color-mix(in_srgb,var(--color-text)_10%,transparent)]">{n}</span>
    : null;
}

export function ChangesRefresh() {
  const { loading } = useCanvasSession();
  return (
    <button type="button" onClick={() => void refreshCanvasSession()} title="Refresh"
      aria-label="Refresh changes" data-testid="canvas-changes-refresh"
      className="ml-1 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]">
      <span className={`material-symbols-outlined text-[14px] block ${loading ? 'animate-spin' : ''}`}>refresh</span>
    </button>
  );
}

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

function StateChip({ state, short }: { state: FileState; short?: string }) {
  const tone = state === 'uncommitted' ? 'text-[var(--color-git-modified)]'
    : state === 'changed' ? 'text-[var(--color-text-dim)]' : 'text-[var(--color-text-muted)]';
  return (
    <span data-testid="changes-state" title={STATE_TITLE[state]}
      className={`shrink-0 text-[10.5px] px-1.5 rounded border border-[var(--color-border)] ${tone}`}>
      {STATE_LABEL[state]}{short ? ` ${short}` : ''}
    </span>
  );
}

const STATUS_LETTER = { added: 'A', modified: 'M', deleted: 'D' } as const;

function RepoCard({ repo, selected, onSelect }: {
  repo: SessionRepo; selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const [more, setMore] = useState(false);
  const key = changesKey(selected);
  const shortOf = new Map(repo.commits.map((c) => [c.sha, c.short]));
  const files = more ? repo.files : repo.files.slice(0, SHOWN);
  return (
    <div data-testid="changes-repo" className="mb-1.5">
      <div className="rounded-lg px-2.5 py-1.5 bg-[color-mix(in_srgb,var(--color-text)_4%,transparent)]" title={repo.root}>
        <div className="flex items-center gap-2 min-w-0 text-[12.5px]">
          <span aria-hidden="true" className={`w-[7px] h-[7px] rounded-full shrink-0 ${repo.error ? 'bg-[var(--color-git-deleted,#f85149)]' : 'bg-[var(--color-git-modified)]'}`} />
          <span className="font-semibold truncate">{repo.repo}</span>
          {repo.project && <span className="shrink-0 text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]">{repo.project}</span>}
          {repo.branch && <span className="shrink-0 font-mono text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]">{repo.branch}</span>}
        </div>
        {repo.error && <div className="mt-1 text-[11px] text-[var(--color-git-deleted,#f85149)]">{repo.error}</div>}
      </div>
      {files.map((f) => (
        <button key={f.path} type="button" data-testid="changes-file"
          onClick={() => onSelect({ kind: 'file', root: repo.root, path: f.path })}
          title={`${f.path} · ${f.turns} turn${f.turns === 1 ? '' : 's'}`}
          className={`w-full flex items-center gap-2 pl-5 pr-2 py-[4px] rounded-md text-left text-[12px] ${key === `${repo.root}:${f.path}` ? SEL : HOVER}`}>
          <span className={`w-3 shrink-0 text-center text-[10.5px] font-bold ${f.status === 'added' ? 'text-[var(--color-git-added)]' : f.status === 'deleted' ? 'text-[var(--color-git-deleted,#f85149)]' : 'text-[var(--color-git-modified)]'}`}>{STATUS_LETTER[f.status]}</span>
          <span className="flex-1 min-w-0 truncate font-mono text-[11.5px]">{f.path}</span>
          <Delta ins={f.added} del={f.deleted} />
          <StateChip state={f.state} short={f.commit ? shortOf.get(f.commit) : undefined} />
        </button>
      ))}
      {repo.files.length > SHOWN && (
        <button type="button" onClick={() => setMore((m) => !m)}
          className={`pl-5 pr-2 py-1 rounded-md text-[11.5px] text-[var(--color-text-muted)] ${HOVER}`}>
          {more ? '▾ fewer' : `▸ ${repo.files.length - SHOWN} more files`}
        </button>
      )}
      {repo.commits.map((c) => (
        <button key={c.sha} type="button" data-testid="changes-commit"
          onClick={() => onSelect({ kind: 'commit', root: repo.root, sha: c.sha })}
          title={`${c.short} · ${c.at}${c.run ? ` · ${c.run}` : ''}`}
          className={`w-full flex items-center gap-2 pl-5 pr-2 py-[4px] rounded-md text-left text-[12px] text-[var(--color-text-secondary)] ${key === `${repo.root}@${c.sha}` ? SEL : HOVER}`}>
          <span className="material-symbols-outlined text-[13px] text-[var(--color-text-dim)] shrink-0" aria-hidden="true">commit</span>
          <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{c.short}</span>
          <span className="flex-1 min-w-0 truncate">{c.subject}</span>
          {c.source === 'run' && <span className="shrink-0 text-[10.5px] text-[var(--color-text-dim)]">run</span>}
        </button>
      ))}
    </div>
  );
}

export function ChangesNav({ selected, onSelect }: {
  selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const { data, error, loading, sessionId } = useCanvasSession();
  // opening the tab re-reads (a commit or push made outside a turn shows up)
  useEffect(() => { void refreshCanvasSession(); }, []);
  const msg = 'px-2 py-2 text-[12px] text-[var(--color-text-muted)]';
  if (!sessionId) return <div className={msg}>No session yet.</div>;
  if (error && !data) {
    return (
      <div className={msg}>
        Could not load this session&apos;s changes ({error}).{' '}
        <button type="button" onClick={() => void refreshCanvasSession()} className="underline hover:text-[var(--color-text)]">Retry</button>
      </div>
    );
  }
  if (!data?.changes) return <div className={msg}>Loading…</div>;
  if (!data.changes.length) return <div className={msg}>This session has not changed any code.</div>;
  return (
    <div data-testid="changes-nav" aria-busy={loading}>
      {data.changes.map((r) => <RepoCard key={r.root} repo={r} selected={selected} onSelect={onSelect} />)}
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

type PatchState = { diff: string; truncated: boolean } | { error: string } | null;

function PatchView({ state, gone }: { state: PatchState; gone: string }) {
  if (!state) return <div className="py-2 text-[12px] text-[var(--color-text-muted)]">Loading diff…</div>;
  if ('error' in state) return <div className="py-2 text-[12px] text-[var(--color-text-muted)]">{state.error || gone}</div>;
  const lines = state.diff.split('\n').filter((l) => !l.startsWith('diff --git') && !l.startsWith('index '));
  return (
    <div data-testid="changes-patch" className="mt-2 rounded-lg border border-[var(--color-border)] overflow-auto font-mono text-[11.5px] leading-[1.55]">
      {lines.map((l, i) => <div key={i} className={`px-2.5 whitespace-pre ${diffLineClass(l)}`}>{l || ' '}</div>)}
      {state.truncated && <div className="px-2.5 py-1 text-[var(--color-text-dim)]">Diff truncated.</div>}
    </div>
  );
}

function usePatch(load: () => Promise<{ diff: string; truncated: boolean } | null>, deps: unknown[]): PatchState {
  const [state, setState] = useState<PatchState>(null);
  useEffect(() => {
    let live = true;
    setState(null);
    load()
      .then((d) => { if (live) setState(d ?? { error: '' }); })
      .catch((e) => { if (live) setState({ error: `Diff unavailable (${e instanceof Error ? e.message : String(e)}).` }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps are the caller's keys
  }, deps);
  return state;
}

/** The session's net diff of one file: first turn's start tree → last turn's end tree. */
function FileDiff({ root, start, end, path }: { root: string; start: string; end: string; path: string }) {
  const state = usePatch(async () => {
    const diff = await chatService.getTurnDiff({ gitRoot: root, start, end, path });
    return diff === null ? null : { diff, truncated: false };
  }, [root, start, end, path]);
  return <PatchView state={state} gone="The diff is no longer available (the repo pruned it)." />;
}

function CommitPatch({ root, sha, path }: { root: string; sha: string; path: string }) {
  const state = usePatch(() => canvasSessionService.commitDiff(root, sha, path), [root, sha, path]);
  return <PatchView state={state} gone="Diff unavailable." />;
}

function CommitDetail({ root, sha, mine }: { root: string; sha: string; mine: Set<string> }) {
  const [files, setFiles] = useState<CommitFile[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setFiles(null); setFailed(null); setOpen(null);
    canvasSessionService.commitFiles(root, sha)
      .then((f) => { if (!live) return; setFiles(f); if (f.length === 1) setOpen(f[0].path); })
      .catch((e) => { if (live) setFailed(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [root, sha]);
  if (failed) return <div className="text-[12px] text-[var(--color-text-muted)]">Files unavailable ({failed}).</div>;
  if (!files) return <div className="text-[12px] text-[var(--color-text-muted)]">Loading files…</div>;
  return (
    <div>
      {files.map((f) => (
        <div key={f.path}>
          <button type="button" data-testid="changes-commit-file" onClick={() => setOpen((o) => (o === f.path ? null : f.path))}
            className={`w-full flex items-center gap-2 px-2 py-[5px] rounded-md text-left text-[12px] ${open === f.path ? SEL : HOVER}`}>
            <span className="font-mono flex-1 min-w-0 truncate" title={f.path}>{f.path}</span>
            {mine.has(f.path) && <span className="shrink-0 text-[10.5px] text-[var(--color-primary)]" title="Changed by this session">this session</span>}
            <Delta ins={f.insertions} del={f.deletions} />
          </button>
          {open === f.path && <CommitPatch root={root} sha={sha} path={f.path} />}
        </div>
      ))}
    </div>
  );
}

export function ChangesDetail({ selected }: { selected: ChangesSelection }) {
  const repos = useRepos();
  const repo = selected ? repos?.find((r) => r.root === selected.root) : undefined;
  if (!selected || !repo) {
    return (
      <div className="h-full flex items-center justify-center text-[12px] text-[var(--color-text-muted)]">
        Select a file or a commit.
      </div>
    );
  }
  const file = selected.kind === 'file' ? repo.files.find((f) => f.path === selected.path) : undefined;
  const commit = selected.kind === 'commit' ? repo.commits.find((c) => c.sha === selected.sha) : undefined;
  const crumb = selected.kind === 'commit' ? commit?.short ?? selected.sha.slice(0, 8) : selected.path;
  const title = selected.kind === 'commit' ? commit?.subject ?? selected.sha : selected.path.split('/').pop();
  return (
    <div data-testid="changes-detail" className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-[var(--color-border)]">
        <div className="text-[11px] text-[var(--color-text-muted)] truncate" title={crumb}>Changes › {repo.repo} › {crumb}</div>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[13px] font-semibold truncate" title={title}>{title}</span>
          {file && <StateChip state={file.state} short={file.commit ? repo.commits.find((c) => c.sha === file.commit)?.short : undefined} />}
        </div>
        {file && (
          <div className="text-[11px] text-[var(--color-text-dim)]">
            This session&apos;s net change over {file.turns} turn{file.turns === 1 ? '' : 's'}
          </div>
        )}
      </div>
      <div className="flex-1 overflow-auto px-2 py-2">
        {file && <FileDiff root={repo.root} start={file.start} end={file.end} path={file.path} />}
        {selected.kind === 'commit' && (
          <CommitDetail root={repo.root} sha={selected.sha} mine={new Set(repo.files.map((f) => f.path))} />
        )}
      </div>
    </div>
  );
}
