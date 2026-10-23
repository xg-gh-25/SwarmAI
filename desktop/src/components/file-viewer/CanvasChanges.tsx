/**
 * CanvasChanges — the Canvas "Changes" tab: the code the pipeline runs of THIS session
 * changed (services/canvasSession → core/canvas_session), read-only.
 *
 * Two layers, like the Artifacts tab: `ChangesNav` is the navigation list in the
 * Canvas header (one card per run: its title and status; per repo the files still
 * uncommitted in the run's worktree, the commits it landed, or that it was released
 * without landing), `ChangesDetail` is the content card below it (a file's diff in the
 * worktree, or a commit's files and patches). Selection lives in FileViewerPanel so
 * both halves agree. No action buttons: committing, pushing or removing is not done
 * from here.
 *
 * @exports ChangesNav, ChangesDetail, ChangesCount, ChangesRefresh, changesKey,
 *   ChangesSelection, diffLineClass
 */
import { useEffect, useState } from 'react';
import { chatService } from '../../services/chat';
import {
  canvasSessionService, refreshCanvasChanges, useCanvasSession,
  type RunCommit, type RunRepoChange, type SessionRunChange,
} from '../../services/canvasSession';
import { FileTypeIcon } from '../common/FileTypeIcon';

export type ChangesSelection =
  | { kind: 'file'; run: string; root: string; path: string }
  | { kind: 'commit'; run: string; root: string; sha: string }
  | null;

export function changesKey(s: ChangesSelection): string {
  if (!s) return '';
  return s.kind === 'commit' ? `${s.run}|${s.root}@${s.sha}` : `${s.run}|${s.root}:${s.path}`;
}

const SEL = 'canvas-row-sel';
const HOVER = 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]';
const CHIP = 'shrink-0 text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]';
const SHOWN = 10;

/** `src/a/b.ts` → ['b.ts', 'src/a']. */
function splitPath(p: string): [string, string] {
  const i = p.lastIndexOf('/');
  return i < 0 ? [p, ''] : [p.slice(i + 1), p.slice(0, i)];
}

function TypeIcon({ name }: { name: string }) {
  return <FileTypeIcon name={name} />;
}

function useRuns(): SessionRunChange[] | null {
  const { data } = useCanvasSession();
  return data?.changes ?? null;
}

/** The Changes tab's count badge: files uncommitted in the runs' worktrees plus the
 *  files of the commits they landed. */
export function ChangesCount() {
  const runs = useRuns();
  const n = (runs ?? []).reduce((s, r) => s + r.repos.reduce((t, p) =>
    t + p.files.length + p.commits.reduce((u, c) => u + c.files.length, 0), 0), 0);
  return n > 0
    ? <span className="text-[10.5px] px-1.5 rounded-full tabular-nums bg-[color-mix(in_srgb,var(--color-text)_10%,transparent)]">{n}</span>
    : null;
}

export function ChangesRefresh() {
  const { loading } = useCanvasSession();
  return (
    <button type="button" onClick={() => void refreshCanvasChanges()} title="Refresh"
      aria-label="Refresh changes" data-testid="canvas-changes-refresh"
      className="ml-1 icon-btn hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
      <span className={`material-symbols-outlined icon-sm block ${loading.changes ? 'animate-spin' : ''}`}>refresh</span>
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

const STATUS_LETTER = { added: 'A', modified: 'M', deleted: 'D' } as const;

function RepoBlock({ run, repo, multi, selected, onSelect }: {
  run: string; repo: RunRepoChange; multi: boolean; selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const [more, setMore] = useState(false);
  const key = changesKey(selected);
  const files = more ? repo.files : repo.files.slice(0, SHOWN);
  return (
    <div data-testid="changes-repo">
      {(multi || repo.branch) && (
        <div className="flex items-center gap-2 min-w-0 pl-5 pr-2 pt-1 text-[11.5px] text-[var(--color-text-muted)]" title={repo.worktree ?? repo.root}>
          {multi && <span className="font-semibold truncate">{repo.repo}</span>}
          {repo.branch && <span className={`${CHIP} font-mono`}>{repo.branch}</span>}
        </div>
      )}
      {repo.error && <div className="pl-5 pr-2 py-1 text-[11px] text-[var(--color-git-deleted,#f85149)]">{repo.error}</div>}
      {repo.released && (
        <div data-testid="changes-released" className="pl-5 pr-2 py-1 text-[11.5px] text-[var(--color-text-muted)]">
          Released without landing: no change is left to show.
        </div>
      )}
      {files.map((f) => {
        const on = key === `${run}|${repo.root}:${f.path}`;
        const [name, dir] = splitPath(f.path);
        return (
          <button key={f.path} type="button" data-testid="changes-file"
            onClick={() => onSelect({ kind: 'file', run, root: repo.root, path: f.path })}
            title={f.path} data-selected={on || undefined}
            className={`w-full flex items-center gap-1.5 h-[26px] pl-[18px] pr-2 rounded-md text-left text-[12px] ${on ? SEL : HOVER}`}>
            <span className={`w-3 shrink-0 text-center text-[10.5px] font-bold ${f.status === 'added' ? 'text-[var(--color-git-added)]' : f.status === 'deleted' ? 'text-[var(--color-git-deleted,#f85149)]' : 'text-[var(--color-git-modified)]'}`}>{STATUS_LETTER[f.status]}</span>
            <TypeIcon name={name} />
            <span className={`shrink-0 truncate max-w-[60%] text-[var(--color-text)] ${on ? 'font-semibold' : ''}`}>{name}</span>
            <span className="flex-1 min-w-0 truncate text-[11px] text-[var(--color-text-faint,var(--color-text-muted))]">{dir}</span>
            <Delta ins={f.added} del={f.deleted} />
          </button>
        );
      })}
      {repo.files.length > SHOWN && (
        <button type="button" onClick={() => setMore((m) => !m)}
          className={`w-full text-left pl-[18px] pr-2 py-1 rounded-md text-[11.5px] ${HOVER}`}>
          {more ? '▾ fewer' : `▸ ${repo.files.length - SHOWN} more files`}
        </button>
      )}
      {repo.commits.map((c) => {
        const on = key === `${run}|${repo.root}@${c.sha}`;
        return (
          <button key={c.sha} type="button" data-testid="changes-commit"
            onClick={() => onSelect({ kind: 'commit', run, root: repo.root, sha: c.sha })}
            title={`${c.short} · ${c.at}`} data-selected={on || undefined}
            className={`w-full flex items-center gap-1.5 h-[26px] pl-[18px] pr-2 rounded-md text-left text-[12px] ${on ? SEL : HOVER}`}>
            <span className={`material-symbols-outlined icon-xs shrink-0 ${on ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-dim)]'}`} aria-hidden="true">commit</span>
            <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{c.short}</span>
            <span className={`flex-1 min-w-0 truncate text-[var(--color-text)] ${on ? 'font-semibold' : ''}`}>{c.subject}</span>
            <span className={CHIP} title={c.pushed ? 'The upstream branch has this commit.' : 'A local commit, not pushed.'}>
              {c.pushed ? 'pushed' : 'committed'}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function RunCard({ run, selected, onSelect }: {
  run: SessionRunChange; selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  // The run title sticks while the list scrolls, so a row far down still says which
  // run it belongs to. Solid recessed surface (the list's own), not a tint.
  return (
    <div data-testid="changes-run" className="mb-1">
      <div className="sticky top-0 z-[1] flex items-center gap-2 min-w-0 h-6 px-2 text-[11.5px] text-[var(--color-text-muted)] bg-[var(--color-bg)]" title={run.run}
        data-testid="changes-run-title">
        <span className="font-semibold truncate text-[var(--color-text)]">{run.title || run.run}</span>
        {run.project && <span className={CHIP}>{run.project}</span>}
        {run.status && <span className={CHIP}>{run.status}</span>}
      </div>
      {run.repos.map((r) => (
        <RepoBlock key={r.root} run={run.run} repo={r} multi={run.repos.length > 1} selected={selected} onSelect={onSelect} />
      ))}
    </div>
  );
}

export function ChangesNav({ selected, onSelect }: {
  selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const { data, error, loading, sessionId } = useCanvasSession();
  // opening the tab re-reads (a commit or push made outside a turn shows up)
  useEffect(() => { void refreshCanvasChanges(); }, []);
  const msg = 'px-2 py-2 text-[12px] text-[var(--color-text-muted)]';
  if (!sessionId) return <div className={msg}>No session yet.</div>;
  if (error && !data?.changes) {
    return (
      <div className={msg}>
        Could not load this session&apos;s changes ({error}).{' '}
        <button type="button" onClick={() => void refreshCanvasChanges()} className="underline hover:text-[var(--color-text)]">Retry</button>
      </div>
    );
  }
  if (!data?.changes) return <div className={msg}>Loading…</div>;
  if (!data.changes.length) return <div className={msg}>No pipeline run of this session has changed code.</div>;
  return (
    <div data-testid="changes-nav" aria-busy={loading.changes}>
      {data.changes.map((r) => <RunCard key={r.run} run={r} selected={selected} onSelect={onSelect} />)}
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

/** A worktree file's diff: the worktree's HEAD tree → its change tree. */
function FileDiff({ root, start, end, path }: { root: string; start: string; end: string; path: string }) {
  const state = usePatch(async () => {
    const diff = await chatService.getTurnDiff({ gitRoot: root, start, end, path });
    return diff === null ? null : { diff, truncated: false };
  }, [root, start, end, path]);
  return <PatchView state={state} gone="The diff is no longer available (refresh the tab)." />;
}

function CommitPatch({ root, sha, path }: { root: string; sha: string; path: string }) {
  const state = usePatch(() => canvasSessionService.commitDiff(root, sha, path), [root, sha, path]);
  return <PatchView state={state} gone="Diff unavailable." />;
}

function CommitDetail({ root, commit }: { root: string; commit: RunCommit }) {
  const [open, setOpen] = useState<string | null>(commit.files.length === 1 ? commit.files[0].path : null);
  useEffect(() => { setOpen(commit.files.length === 1 ? commit.files[0].path : null); }, [commit]);
  return (
    <div>
      {commit.files.map((f) => (
        <div key={f.path}>
          <button type="button" data-testid="changes-commit-file" onClick={() => setOpen((o) => (o === f.path ? null : f.path))}
            className={`w-full flex items-center gap-2 px-2 py-[5px] rounded-md text-left text-[12px] ${open === f.path ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : HOVER}`}>
            <TypeIcon name={splitPath(f.path)[0]} />
            <span className="font-mono flex-1 min-w-0 truncate" title={f.path}>{f.path}</span>
            <Delta ins={f.added} del={f.deleted} />
          </button>
          {open === f.path && <CommitPatch root={root} sha={commit.sha} path={f.path} />}
        </div>
      ))}
    </div>
  );
}

export function ChangesDetail({ selected }: { selected: ChangesSelection }) {
  const runs = useRuns();
  const run = selected ? runs?.find((r) => r.run === selected.run) : undefined;
  const repo = selected ? run?.repos.find((r) => r.root === selected.root) : undefined;
  const file = selected?.kind === 'file' ? repo?.files.find((f) => f.path === selected.path) : undefined;
  const commit = selected?.kind === 'commit' ? repo?.commits.find((c) => c.sha === selected.sha) : undefined;
  if (!run || !repo || (!file && !commit)) {
    return (
      <div className="h-full flex items-center justify-center text-[12px] text-[var(--color-text-muted)]">
        Select a file or a commit.
      </div>
    );
  }
  // One title line: what (icon + name, or sha + subject) · state · +/− or time. The
  // run › repo › path crumb is gone: the selected row in the list above already says it.
  const name = file ? splitPath(file.path)[0] : '';
  const state = file
    ? `uncommitted · ${repo.branch ?? 'run worktree'}`
    : `${commit!.pushed ? 'pushed' : 'committed'} · ${commit!.at}`;
  return (
    <div data-testid="changes-detail" className="h-full flex flex-col">
      <div className="flex items-center gap-2 min-w-0 h-9 px-3 border-b border-[var(--color-border)] text-[11.5px] text-[var(--color-text-muted)]"
        title={`${run.title || run.run} › ${repo.repo} › ${file ? file.path : commit!.short}`}
        data-testid="changes-detail-title">
        {file ? <TypeIcon name={name} /> : <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{commit!.short}</span>}
        <span className="min-w-0 truncate text-[12.5px] font-semibold text-[var(--color-text)]">{file ? name : commit!.subject}</span>
        <span data-testid="changes-state" className="shrink-0 whitespace-nowrap"
          title={file ? "In the run's worktree, not committed yet." : undefined}>· {state}</span>
        <span className="flex-1" />
        {file && <Delta ins={file.added} del={file.deleted} />}
      </div>
      <div className="flex-1 overflow-auto px-2 py-2">
        {file && <FileDiff root={repo.root} start={file.start} end={file.end} path={file.path} />}
        {commit && <CommitDetail root={repo.root} commit={commit} />}
      </div>
    </div>
  );
}
