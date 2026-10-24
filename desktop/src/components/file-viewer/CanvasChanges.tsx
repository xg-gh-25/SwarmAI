/**
 * CanvasChanges — the Canvas "Changes" tab: what THIS session's own transcript says it
 * changed (services/canvasSession → core/session_changes), read-only. How a change
 * was made (native, a pipeline run, a sub-agent) is not asked.
 *
 * Two layers, like the Artifacts tab: `ChangesNav` is the navigation list in the
 * Canvas header —
 *   - Files changed: each turn's "files changed" card, newest first, as recorded
 *     (what a card left off, older turns past the cap, and earlier messages that were
 *     not scanned are said, not guessed);
 *   - Commits mentioned: the commits the session's messages name, confirmed by git.
 * `ChangesDetail` is the content card below it (a turn file's diff, or a commit's
 * files and patches). Selection lives in FileViewerPanel so both halves agree. No
 * action buttons: committing, pushing or removing is not done from here.
 *
 * @exports ChangesNav, ChangesDetail, ChangesCount, ChangesRefresh, changesKey,
 *   ChangesSelection, diffLineClass
 */
import { useEffect, useState } from 'react';
import {
  canvasSessionService, refreshCanvasChanges, useCanvasSession,
  type ChangedFile, type MentionedCommit, type SessionChanges, type TurnChangeCard,
} from '../../services/canvasSession';
import { FileTypeIcon } from '../common/FileTypeIcon';
import { CopyPathButton } from '../common/CopyPathButton';

export type ChangesSelection =
  | { kind: 'file'; turn: string; path: string }
  | { kind: 'commit'; root: string; sha: string }
  | null;

export function changesKey(s: ChangesSelection): string {
  if (!s) return '';
  return s.kind === 'commit' ? `${s.root}@${s.sha}` : `${s.turn}:${s.path}`;
}

const SEL = 'canvas-row-sel';
const HOVER = 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]';
const CHIP = 'shrink-0 text-[10.5px] px-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)]';
const SECTION = 'sticky top-0 z-[1] flex items-center gap-2 h-6 px-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)] bg-[var(--color-bg)]';
const NOTE = 'pl-[18px] pr-2 py-1 text-[11.5px] text-[var(--color-text-muted)]';
const SHOWN = 10;

/** `src/a/b.ts` → ['b.ts', 'src/a']. */
function splitPath(p: string): [string, string] {
  const i = p.lastIndexOf('/');
  return i < 0 ? [p, ''] : [p.slice(i + 1), p.slice(0, i)];
}

/** "20:31" for today, "Oct 24 20:31" otherwise; the raw value if it is not a date. */
function when(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString()
    ? hm : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${hm}`;
}

function useChanges(): SessionChanges | null {
  const { data } = useCanvasSession();
  return data?.changes ?? null;
}

/** The Changes tab's count badge: distinct files across the listed turns. */
export function ChangesCount() {
  const c = useChanges();
  const n = new Set((c?.turns ?? []).flatMap((t) => t.files.map((f) => f.absolutePath || f.path))).size;
  return n > 0
    ? <span data-testid="changes-count" className="text-[10.5px] px-1.5 rounded-full tabular-nums bg-[color-mix(in_srgb,var(--color-text)_10%,transparent)]">{n}</span>
    : null;
}

export function ChangesRefresh() {
  const { loading, streaming } = useCanvasSession();
  return (
    <button type="button" onClick={() => void refreshCanvasChanges()}
      title={streaming ? 'Refreshes when this turn ends' : 'Refresh'} disabled={streaming}
      aria-label="Refresh changes" data-testid="canvas-changes-refresh"
      className="ml-1 icon-btn hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40 disabled:pointer-events-none">
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

function FileRow({ turn, f, multiRepo, on, onSelect }: {
  turn: string; f: ChangedFile; multiRepo: boolean; on: boolean; onSelect: (s: ChangesSelection) => void;
}) {
  const [name, dir] = splitPath(f.path);
  const pick = () => onSelect({ kind: 'file', turn, path: f.path });
  return (
    // A row, not a <button>: it holds the copy-path button (no nested buttons).
    <div role="button" tabIndex={0} data-testid="changes-file" onClick={pick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }}
      title={f.path} data-selected={on || undefined}
      className={`group w-full flex items-center gap-1.5 h-[26px] pl-[18px] pr-2 rounded-md text-left text-[12px] cursor-pointer ${on ? SEL : HOVER}`}>
      <span className={`w-3 shrink-0 text-center text-[10.5px] font-bold ${f.status === 'added' ? 'text-[var(--color-git-added)]' : f.status === 'deleted' ? 'text-[var(--color-git-deleted,#f85149)]' : 'text-[var(--color-git-modified)]'}`}>{STATUS_LETTER[f.status] ?? 'M'}</span>
      <FileTypeIcon name={name} />
      <span className={`shrink-0 truncate max-w-[60%] text-[var(--color-text)] ${on ? 'font-semibold' : ''}`}>{name}</span>
      <span className="flex-1 min-w-0 truncate text-[11px] text-[var(--color-text-faint,var(--color-text-muted))]">
        {multiRepo ? `${f.repo} · ${dir}` : dir}
      </span>
      <CopyPathButton
        path={f.absolutePath || f.path}
        testId="changes-row-copy"
        className={`${on ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100 '}p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] shrink-0 inline-flex`}
      />
      <Delta ins={f.added} del={f.deleted} />
    </div>
  );
}

function TurnBlock({ turn, multiRepo, selected, onSelect }: {
  turn: TurnChangeCard; multiRepo: boolean; selected: ChangesSelection; onSelect: (s: ChangesSelection) => void;
}) {
  const [more, setMore] = useState(false);
  const key = changesKey(selected);
  const files = more ? turn.files : turn.files.slice(0, SHOWN);
  const count = turn.files.length + turn.omitted;
  return (
    <div data-testid="changes-turn" className="mb-1">
      <div className="flex items-center gap-2 min-w-0 pl-2 pr-2 pt-1 text-[11.5px] text-[var(--color-text-muted)]" title={turn.at}>
        <span className="font-semibold text-[var(--color-text)]">{when(turn.at)}</span>
        <span>· {count === 1 ? '1 file' : `${count} files`}</span>
      </div>
      {files.map((f) => (
        <FileRow key={f.path} turn={turn.id} f={f} multiRepo={multiRepo}
          on={key === `${turn.id}:${f.path}`} onSelect={onSelect} />
      ))}
      {turn.files.length > SHOWN && (
        <button type="button" onClick={() => setMore((m) => !m)}
          className={`w-full text-left pl-[18px] pr-2 py-1 rounded-md text-[11.5px] ${HOVER}`}>
          {more ? '▾ fewer' : `▸ ${turn.files.length - SHOWN} more files`}
        </button>
      )}
      {turn.omitted > 0 && <div className={NOTE}>{turn.omitted} more files are not listed on this turn&apos;s card.</div>}
    </div>
  );
}

function CommitRow({ c, on, onSelect }: { c: MentionedCommit; on: boolean; onSelect: (s: ChangesSelection) => void }) {
  return (
    <button type="button" data-testid="changes-commit"
      onClick={() => onSelect({ kind: 'commit', root: c.root, sha: c.sha })}
      title={`${c.short} · ${c.repo} · ${c.at}`} data-selected={on || undefined}
      className={`w-full flex items-center gap-1.5 h-[26px] pl-[18px] pr-2 rounded-md text-left text-[12px] ${on ? SEL : HOVER}`}>
      <span className={`material-symbols-outlined icon-xs shrink-0 ${on ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-dim)]'}`} aria-hidden="true">commit</span>
      <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{c.short}</span>
      <span className={`flex-1 min-w-0 truncate text-[var(--color-text)] ${on ? 'font-semibold' : ''}`}>{c.subject}</span>
      {c.pushed !== null && (
        <span className={CHIP} title={c.pushed ? 'The upstream branch has this commit.' : 'A local commit, not pushed.'}>
          {c.pushed ? 'pushed' : 'committed'}
        </span>
      )}
    </button>
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
  const c = data?.changes;
  if (error && !c) {
    return (
      <div className={msg}>
        Could not load this session&apos;s changes ({error}).{' '}
        <button type="button" onClick={() => void refreshCanvasChanges()} className="underline hover:text-[var(--color-text)]">Retry</button>
      </div>
    );
  }
  if (!c) return <div className={msg}>Loading…</div>;
  if (!c.turns.length && !c.commits.length) {
    return <div className={msg}>No code changes in this session yet.{!c.scannedAll && ' (Only recent messages were scanned.)'}</div>;
  }
  const key = changesKey(selected);
  const multiRepo = new Set(c.turns.flatMap((t) => t.files.map((f) => f.gitRoot))).size > 1;
  return (
    <div data-testid="changes-nav" aria-busy={loading.changes}>
      {c.turns.length > 0 && <div className={SECTION}>Files changed</div>}
      {c.turns.map((t) => <TurnBlock key={t.id} turn={t} multiRepo={multiRepo} selected={selected} onSelect={onSelect} />)}
      {c.olderTurns > 0 && <div data-testid="changes-older" className={NOTE}>{c.olderTurns} older turns are not listed.</div>}
      {c.commits.length > 0 && <div className={`${SECTION} mt-1`}>Commits mentioned</div>}
      {c.commits.map((m) => <CommitRow key={`${m.root}@${m.sha}`} c={m} on={key === `${m.root}@${m.sha}`} onSelect={onSelect} />)}
      {!c.scannedAll && (
        <div data-testid="changes-not-scanned" className={NOTE}>Some earlier messages of this session were not scanned.</div>
      )}
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

/** A turn file's diff: its card row's two trees, read in the row's repo. A binary or
 *  "file only" row (the card's own `binary` / `diffable`) is not requested. */
function FileDiff({ f }: { f: ChangedFile }) {
  const noDiff = f.binary === true || f.diffable === false;
  const state = usePatch(async () => {
    if (noDiff) return null;
    const diff = await canvasSessionService.treeDiff(f.gitRoot, f.start, f.end, f.path);
    return diff === null ? null : { diff, truncated: false };
  }, [f.gitRoot, f.start, f.end, f.path, noDiff]);
  if (noDiff) {
    return (
      <div data-testid="changes-no-diff" className="py-2 text-[12px] text-[var(--color-text-muted)]">
        {f.binary ? 'A binary file: no text diff.' : 'Too large to show as a diff.'}
      </div>
    );
  }
  return <PatchView state={state} gone="The diff is no longer available." />;
}

function CommitPatch({ root, sha, path }: { root: string; sha: string; path: string }) {
  const state = usePatch(() => canvasSessionService.commitDiff(root, sha, path), [root, sha, path]);
  return <PatchView state={state} gone="Diff unavailable." />;
}

function CommitDetail({ commit }: { commit: MentionedCommit }) {
  const [open, setOpen] = useState<string | null>(commit.files.length === 1 ? commit.files[0].path : null);
  useEffect(() => { setOpen(commit.files.length === 1 ? commit.files[0].path : null); }, [commit]);
  return (
    <div>
      {commit.files.map((f) => (
        <div key={f.path}>
          <button type="button" data-testid="changes-commit-file" onClick={() => setOpen((o) => (o === f.path ? null : f.path))}
            className={`w-full flex items-center gap-2 px-2 py-[5px] rounded-md text-left text-[12px] ${open === f.path ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : HOVER}`}>
            <FileTypeIcon name={splitPath(f.path)[0]} />
            <span className="font-mono flex-1 min-w-0 truncate" title={f.path}>{f.path}</span>
            <Delta ins={f.added} del={f.deleted} />
          </button>
          {open === f.path && <CommitPatch root={commit.root} sha={commit.sha} path={f.path} />}
        </div>
      ))}
      {commit.omittedFiles > 0 && (
        <div className="px-2 py-1 text-[11.5px] text-[var(--color-text-muted)]">{commit.omittedFiles} more files are not listed.</div>
      )}
    </div>
  );
}

export function ChangesDetail({ selected }: { selected: ChangesSelection }) {
  const c = useChanges();
  const turn = selected?.kind === 'file' ? c?.turns.find((t) => t.id === selected.turn) : undefined;
  const file = selected?.kind === 'file' ? turn?.files.find((f) => f.path === selected.path) : undefined;
  const commit = selected?.kind === 'commit'
    ? c?.commits.find((m) => m.root === selected.root && m.sha === selected.sha) : undefined;
  if (!file && !commit) {
    return (
      <div className="h-full flex items-center justify-center text-[12px] text-[var(--color-text-muted)]">
        Select a file or a commit.
      </div>
    );
  }
  // One title line: what (icon + name, or sha + subject) · when / repo · +/−.
  const name = file ? splitPath(file.path)[0] : '';
  const state = file
    ? `${file.repo} · turn at ${when(turn!.at)}`
    : `${commit!.repo}${commit!.pushed === null ? '' : commit!.pushed ? ' · pushed' : ' · committed'} · ${when(commit!.at)}`;
  return (
    <div data-testid="changes-detail" className="h-full flex flex-col">
      <div className="flex items-center gap-2 min-w-0 h-9 px-3 border-b border-[var(--color-border)] text-[11.5px] text-[var(--color-text-muted)]"
        title={file ? file.absolutePath || file.path : `${commit!.sha} · ${commit!.subject}`}
        data-testid="changes-detail-title">
        {file ? <FileTypeIcon name={name} /> : <span className="font-mono text-[11.5px] text-[var(--color-primary)] shrink-0">{commit!.short}</span>}
        <span className="min-w-0 truncate text-[12.5px] font-semibold text-[var(--color-text)]">{file ? name : commit!.subject}</span>
        <span data-testid="changes-state" className="shrink-0 whitespace-nowrap">· {state}</span>
        <span className="flex-1" />
        {file && <Delta ins={file.added} del={file.deleted} />}
      </div>
      <div className="flex-1 overflow-auto px-2 py-2">
        {file && <FileDiff f={file} />}
        {commit && <CommitDetail commit={commit} />}
      </div>
    </div>
  );
}
