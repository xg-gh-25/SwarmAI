/**
 * ChangesCard — "N files changed": what a turn changed in the code repos
 * (`turn_changes`, written by the backend from a change-tree snapshot taken when
 * the turn started — core/turn_changes.py). KiroCrew-style (XG 2026-10-19).
 *
 * One header line — the file count, then the turn's total +/- (the one figure to
 * read first). One row per file, biggest change first: a chevron when the change
 * can be shown as a diff, the file name (opens it in Canvas; a deleted file is plain text) with its
 * folder dimmed, `new` / `deleted` when that is what happened, then +a −d and a
 * five-square bar of the add/delete share. A file whose change is too large to show
 * says "File only"; a binary file says "binary". Rows separate with a hairline,
 * never boxes. Opening a row fetches its unified diff (the block holds the two
 * change trees, not the text — GET /api/chat/turn-diff) and shows it, lines
 * tinted by sign; once git has pruned the trees the row says so, and a failed
 * fetch (network) can be retried by opening the row again.
 *
 * The turn's landings (design docs/design/2026-10-25-turn-result-card.md §5.2) come
 * first: per landing `<branch> → <dest>`, its commits, whether it is still on
 * `dest`, and its rows (the same rows, diffing `onto..new`). The title counts both
 * ("N files changed · M commits landed"). What could not be read about landings
 * (unknown repos, a truncated read, unverified records) is said at the end.
 *
 * Inert in a read-only preview (ReadOnlyContext): names do not open, diffs still
 * expand (reading is not acting).
 *
 * @exports ChangesCard, diffBar, totals
 */
import { useContext, useRef, useState } from 'react';
import { chatService } from '../../../services/chat';
import { useTranslation } from 'react-i18next';
import type { TurnChangeFile, TurnChangesContent, TurnLanding } from '../../../types';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { FileTypeIcon } from '../../../components/common/FileTypeIcon';
import { tf } from '../tf';
import { ReadOnlyContext } from '../readOnlyContext';

const ADD = 'text-green-600 dark:text-green-400';
const DEL = 'text-red-600 dark:text-red-400';

/** The five squares: green / red share of the lines changed, grey for the rest when
 *  fewer than five lines changed. */
export function diffBar(added: number, deleted: number): Array<'add' | 'del' | 'none'> {
  const total = added + deleted;
  const filled = Math.min(5, total);
  const green = total ? Math.round((filled * added) / total) : 0;
  return Array.from({ length: 5 }, (_, i) => (i < green ? 'add' : i < filled ? 'del' : 'none'));
}

export function totals(files: TurnChangeFile[]): { added: number; deleted: number } {
  return files.reduce(
    (acc, f) => ({ added: acc.added + (f.added ?? 0), deleted: acc.deleted + (f.deleted ?? 0) }),
    { added: 0, deleted: 0 },
  );
}

function splitPath(p: string): { dir: string; name: string } {
  const i = p.lastIndexOf('/');
  return i < 0 ? { dir: '', name: p } : { dir: p.slice(0, i + 1), name: p.slice(i + 1) };
}

function DiffLines({ diff }: { diff: string }) {
  // The file header (diff --git / index / --- / +++) repeats the row; drop it.
  const lines = diff.split('\n');
  const start = lines.findIndex((l) => l.startsWith('@@'));
  const body = (start < 0 ? lines : lines.slice(start)).filter((l, i, a) => !(i === a.length - 1 && l === ''));
  return (
    <pre data-testid="change-diff" className="max-h-[480px] overflow-auto py-1 text-[12px] leading-[1.55] font-mono">
      {body.map((l, i) => {
        const cls = l.startsWith('@@')
          ? 'text-[var(--color-text-muted)]'
          : l.startsWith('+') ? `${ADD} bg-green-500/10`
            : l.startsWith('-') ? `${DEL} bg-red-500/10`
              : 'text-[var(--color-text-secondary)]';
        return <div key={i} className={`px-3 whitespace-pre ${cls}`}>{l || ' '}</div>;
      })}
    </pre>
  );
}

function ChangeRow({ file }: { file: TurnChangeFile }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const [open, setOpen] = useState(false);
  // undefined: not fetched yet; null: gone for good; 'error': failed, retry on reopen.
  const [diff, setDiff] = useState<string | null | undefined | { error: true }>(undefined);
  // Only the latest request may set the state (a stale one settling late must not
  // overwrite it); one in flight is not sent again.
  const req = useRef(0);
  const loading = useRef(false);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next || loading.current) return;
    if (diff !== undefined && !(typeof diff === 'object' && diff !== null)) return;
    const id = ++req.current;
    loading.current = true;
    setDiff(undefined);
    chatService.getTurnDiff({ gitRoot: file.gitRoot, start: file.start, end: file.end, path: file.path })
      .then((d) => { if (id === req.current) setDiff(d); })
      .catch(() => { if (id === req.current) setDiff({ error: true }); })
      .finally(() => { if (id === req.current) loading.current = false; });
  };
  const { dir, name } = splitPath(file.path);
  const canOpenFile = !readOnly && file.status !== 'deleted';
  const openFile = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const hasDiff = file.diffable && !file.binary;
  const tag = file.status === 'added'
    ? tf(t, 'chat.turn.changes.new', 'new')
    : file.status === 'deleted' ? tf(t, 'chat.turn.changes.deleted', 'deleted') : null;
  const nameEl = (
    <span className="min-w-0 truncate font-mono text-[12.5px]">
      {dir && <span className="text-[var(--color-text-muted)]">{dir}</span>}
      <span className="text-[var(--color-text)]">{name}</span>
    </span>
  );
  return (
    <li data-testid="change-row" data-path={file.path} className="border-t border-[var(--color-border)] first:border-t-0">
      <div className="flex items-center gap-2 px-3 py-1.5 min-w-0">
        {hasDiff ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={tf(t, 'chat.turn.changes.toggle', 'Show diff')}
            onClick={toggle}
            className="shrink-0 w-4 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <span aria-hidden="true" className={`material-symbols-outlined icon-sm leading-none transition-transform ${open ? 'rotate-90' : ''}`}>chevron_right</span>
          </button>
        ) : <span className="shrink-0 w-4" />}
        <FileTypeIcon name={name} muted />
        {canOpenFile ? (
          <button type="button" onClick={openFile} title={file.absolutePath} className="min-w-0 flex text-left hover:underline">
            {nameEl}
          </button>
        ) : <span title={file.absolutePath} className="min-w-0 flex">{nameEl}</span>}
        {tag && <span className="shrink-0 text-[11px] text-[var(--color-text-muted)]">{tag}</span>}
        <span className="ml-auto pl-3 shrink-0 flex items-center gap-2 tabular-nums text-[12px]">
          {file.binary ? (
            <span className="text-[var(--color-text-muted)]">{tf(t, 'chat.turn.changes.binary', 'binary')}</span>
          ) : (
            <>
              {!hasDiff && (
                <span data-testid="file-only" className="px-1.5 rounded-full border border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)]">
                  {tf(t, 'chat.turn.changes.fileOnly', 'File only')}
                </span>
              )}
              {!!file.added && <span className={ADD}>+{file.added}</span>}
              {!!file.deleted && <span className={DEL}>−{file.deleted}</span>}
              <span aria-hidden="true" className="flex gap-[2px]">
                {diffBar(file.added ?? 0, file.deleted ?? 0).map((k, i) => (
                  <span key={i} className={`w-[7px] h-[7px] rounded-[1.5px] ${k === 'add' ? 'bg-green-500' : k === 'del' ? 'bg-red-500' : 'bg-[var(--color-border)]'}`} />
                ))}
              </span>
            </>
          )}
        </span>
      </div>
      {open && (
        <div className="border-t border-[var(--color-border)]">
          {diff === undefined ? (
            <p className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">{tf(t, 'chat.turn.changes.loading', 'Loading diff…')}</p>
          ) : diff === null ? (
            <p data-testid="diff-gone" className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.changes.gone', 'This diff is no longer available. Open the file instead.')}
            </p>
          ) : typeof diff === 'object' ? (
            <p data-testid="diff-error" className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.changes.failed', "Couldn't load the diff. Close and open the row to retry.")}
            </p>
          ) : <DiffLines diff={diff} />}
        </div>
      )}
    </li>
  );
}

/** One landing: `<branch> → <dest>`, its commits, whether it is still on `dest`,
 *  then its file rows (they diff `onto..new` through the same turn diff). */
function LandingGroup({ landing }: { landing: TurnLanding }) {
  const { t } = useTranslation();
  const on = landing.state === 'on';
  return (
    <li data-testid="turn-landing" className="border-b border-[var(--color-border)]">
      <div className="flex items-center gap-2 px-3 py-1.5 min-w-0">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm leading-none text-[var(--color-text-muted)]">merge</span>
        <span className="min-w-0 truncate font-mono text-[12.5px] text-[var(--color-text)]">
          {landing.branch} <span className="text-[var(--color-text-muted)]">→ {landing.dest}</span>
        </span>
        <span data-testid="landing-state"
          className={`ml-auto shrink-0 px-1.5 rounded-full border border-[var(--color-border)] text-[11px] ${on ? ADD : 'text-[var(--color-text-muted)]'}`}>
          {on
            ? tf(t, 'chat.turn.changes.onBranch', 'on {{dest}}', { dest: landing.dest })
            : tf(t, 'chat.turn.changes.offBranch', 'not on {{dest}}', { dest: landing.dest })}
        </span>
      </div>
      <ul className="px-3 pb-1.5 text-[12px] text-[var(--color-text-secondary)]">
        {landing.subjects.map((s) => (
          <li key={s.sha} className="truncate"><span className="font-mono text-[var(--color-text-muted)]">{s.sha}</span> {s.subject}</li>
        ))}
        {landing.count > landing.subjects.length && (
          <li className="italic text-[var(--color-text-muted)]">
            {tf(t, 'chat.turn.changes.moreCommits', '{{n}} more commits', { n: landing.count - landing.subjects.length })}
          </li>
        )}
      </ul>
      {!!landing.files.length && <ul className="border-t border-[var(--color-border)]">{landing.files.map((f) => <ChangeRow key={f.absolutePath} file={f} />)}</ul>}
      {!!landing.omitted && (
        <p className="px-3 py-1.5 border-t border-[var(--color-border)] text-[12px] italic text-[var(--color-text-muted)]">
          {tf(t, 'chat.turn.changes.omitted', '{{n}} more files are not listed.', { n: landing.omitted })}
        </p>
      )}
    </li>
  );
}

function title(t: Parameters<typeof tf>[0], files: number, commits: number): string {
  const f = files === 1 ? tf(t, 'chat.turn.changes.titleOne', '1 file changed')
    : tf(t, 'chat.turn.changes.title', '{{count}} files changed', { count: files });
  const c = commits === 1 ? tf(t, 'chat.turn.changes.landedOne', '1 commit landed')
    : tf(t, 'chat.turn.changes.landed', '{{count}} commits landed', { count: commits });
  if (files && commits) return `${f} · ${c}`;
  if (commits) return c;
  if (files) return f;
  return tf(t, 'chat.turn.changes.titleNone', 'Changes');
}

/** `bare`: a section of the turn's output card (TurnCards OutputCard), no frame of its own. */
export function ChangesCard({ block, bare }: { block: TurnChangesContent; bare?: boolean }) {
  const { t } = useTranslation();
  const files = block.files ?? [];
  const landed = block.landed ?? [];
  const sum = totals([...files, ...landed.flatMap((l) => l.files)]);
  const count = files.length + (block.omitted ?? 0);
  const commits = landed.reduce((n, l) => n + l.count, 0);
  const anyFileOnly = [...files, ...landed.flatMap((l) => l.files)].some((f) => !f.diffable && !f.binary);
  const notes: string[] = [
    ...(block.unknown ?? []).map((u) => tf(t, 'chat.turn.changes.unknown',
      'Could not read landings in {{repo}} ({{reason}}).', { repo: u.repo, reason: u.reason })),
    ...(block.landedTruncated ? [tf(t, 'chat.turn.changes.truncated', 'More landings may exist; see Canvas.')] : []),
    ...(block.unverified ? [tf(t, 'chat.turn.changes.unverified',
      '{{n}} landing records could not be verified.', { n: block.unverified })] : []),
  ];
  return (
    <section data-testid="turn-changes" className={bare ? '' : 'rounded-lg border border-[var(--color-border)] overflow-hidden'}>
      <header className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm leading-none text-[var(--color-text-muted)]">difference</span>
        <span className="font-mono text-[13px] text-[var(--color-text)]">{title(t, count, commits)}</span>
        <span className="ml-auto flex gap-2 tabular-nums text-[12.5px]">
          {!!sum.added && <span className={ADD}>+{sum.added}</span>}
          {!!sum.deleted && <span className={DEL}>−{sum.deleted}</span>}
        </span>
      </header>
      {!!landed.length && <ul>{landed.map((l) => <LandingGroup key={`${l.gitRoot}:${l.new}`} landing={l} />)}</ul>}
      {(anyFileOnly || !!block.omitted) && (
        <p className="px-3 py-1.5 border-b border-[var(--color-border)] text-[12px] italic text-[var(--color-text-muted)]">
          {block.omitted
            ? tf(t, 'chat.turn.changes.omitted', '{{n}} more files are not listed.', { n: block.omitted })
            : tf(t, 'chat.turn.changes.tooLarge', 'Some changes are too large to show as a diff. Click a file name to open it.')}
        </p>
      )}
      {!!files.length && <ul>{files.map((f) => <ChangeRow key={f.absolutePath} file={f} />)}</ul>}
      {notes.map((n) => (
        <p key={n} data-testid="changes-note" className="px-3 py-1.5 border-t border-[var(--color-border)] text-[12px] italic text-[var(--color-text-muted)]">{n}</p>
      ))}
    </section>
  );
}
