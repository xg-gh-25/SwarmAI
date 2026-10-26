/**
 * ChangesCard — "N files changed": what a turn changed in the code repos
 * (`turn_changes`, written by the backend — core/turn_changes.py), in the one
 * ItemBox / ItemRow every turn card uses (design
 * docs/design/2026-10-26-turn-cards-unified.md §2).
 *
 * One box: a title line ("N files changed" and the turn's +/-), a row per file
 * (landed files first, then in-place ones): ▸ opens its diff below the row (fetched
 * on demand: GET /api/chat/turn-diff), the name opens it in Canvas (a deleted file is
 * plain text), its folder in grey, then added / deleted / binary / File only and
 * +a −d. Then a COMMITS section: each landed commit's short sha, subject, and
 * "→ <dest>" or "not on <dest>"; "+N more" past the listed subjects. What could not be
 * read (unknown repos, a truncated read, unverified records) is said at the end.
 *
 * Inert in a read-only preview (ReadOnlyContext): names do not open, diffs still
 * expand (reading is not acting).
 *
 * @exports ChangesCard, totals
 */
import { useContext, useRef, useState } from 'react';
import { chatService } from '../../../services/chat';
import { useTranslation } from 'react-i18next';
import type { TurnChangeFile, TurnChangesContent, TurnLanding } from '../../../types';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { FileTypeIcon } from '../../../components/common/FileTypeIcon';
import { tf } from '../tf';
import { ReadOnlyContext } from '../readOnlyContext';
import { ItemBox, ItemRow, ItemSection, shortFolder } from './ItemRows';

const ADD = 'text-green-600 dark:text-green-400';
const DEL = 'text-red-600 dark:text-red-400';

export function totals(files: TurnChangeFile[]): { added: number; deleted: number } {
  return files.reduce(
    (acc, f) => ({ added: acc.added + (f.added ?? 0), deleted: acc.deleted + (f.deleted ?? 0) }),
    { added: 0, deleted: 0 },
  );
}

const baseName = (p: string) => p.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || p;

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
  const name = baseName(file.path);
  const canOpenFile = !readOnly && file.status !== 'deleted';
  const openFile = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const hasDiff = file.diffable && !file.binary;
  const tag = file.status === 'added'
    ? tf(t, 'chat.turn.changes.added', 'added')
    : file.status === 'deleted' ? tf(t, 'chat.turn.changes.deleted', 'deleted') : null;
  const lead = hasDiff ? (
    <button type="button" aria-expanded={open} aria-label={tf(t, 'chat.turn.changes.toggle', 'Show diff')} onClick={toggle}
      className="shrink-0 w-4 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
      <span aria-hidden="true" className={`material-symbols-outlined icon-sm leading-none transition-transform ${open ? 'rotate-90' : ''}`}>chevron_right</span>
    </button>
  ) : <span className="shrink-0 w-4" />;
  const tail = (
    <span className="ml-1 shrink-0 flex items-center gap-2 tabular-nums text-[12px] whitespace-nowrap">
      {tag && <span className="text-[11px] text-[var(--color-text-muted)]">{tag}</span>}
      {file.binary ? (
        <span className="text-[11px] text-[var(--color-text-muted)]">{tf(t, 'chat.turn.changes.binary', 'binary')}</span>
      ) : (
        <>
          {!hasDiff && (
            <span data-testid="file-only" className="text-[11px] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.changes.fileOnly', 'File only')}
            </span>
          )}
          {!!file.added && <span className={ADD}>+{file.added}</span>}
          {!!file.deleted && <span className={DEL}>−{file.deleted}</span>}
        </>
      )}
    </span>
  );
  const below = open ? (
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
  ) : null;
  return (
    <ItemRow testId="change-row" data={{ path: file.path }} lead={lead}
      badge={<FileTypeIcon name={name} size="sm" />} name={name} link={canOpenFile}
      folder={shortFolder(file.absolutePath.replace(/\/[^/]*$/, '') || '/')} folderTitle={file.absolutePath}
      openTitle={file.absolutePath} onOpen={canOpenFile ? openFile : undefined}
      tail={tail} below={below} />
  );
}

/** A landing's commits in the COMMITS section: short sha, subject (it may be cut:
 *  it is not a file name), and whether the commit is on the branch it landed into. */
function CommitRows({ landing }: { landing: TurnLanding }) {
  const { t } = useTranslation();
  const on = landing.state === 'on';
  const dest = on
    ? `→ ${landing.dest}`
    : tf(t, 'chat.turn.changes.offBranch', 'not on {{dest}}', { dest: landing.dest });
  const more = landing.count - landing.subjects.length;
  return (
    <>
      {landing.subjects.map((s) => (
        <ItemRow key={`${landing.new}:${s.sha}`} testId="commit-row" data={{ sha: s.sha }}
          badge={<span className="shrink-0 font-mono text-[11.5px] text-[var(--color-text-dim)]">{s.sha}</span>}
          name={s.subject} nameCanCut
          tail={<span data-testid="landing-state" className={`ml-2 shrink-0 whitespace-nowrap text-[11px] ${on ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}>{dest}</span>} />
      ))}
      {more > 0 && (
        <li className="px-3 py-1 text-[12px] text-[var(--color-text-muted)]">
          {tf(t, 'chat.turn.changes.moreCommits', '+{{n}} more', { n: more })}
        </li>
      )}
    </>
  );
}

function title(t: Parameters<typeof tf>[0], files: number): string {
  if (files === 1) return tf(t, 'chat.turn.changes.titleOne', '1 file changed');
  if (files) return tf(t, 'chat.turn.changes.title', '{{count}} files changed', { count: files });
  return tf(t, 'chat.turn.changes.titleNone', 'Changes');
}

/** The turn's code changes: ONE ItemBox (TurnCards' product box look). Title line
 *  with the turn's +/-, every changed file (landed ones first), then COMMITS. */
export function ChangesCard({ block }: { block: TurnChangesContent }) {
  const { t } = useTranslation();
  const files = block.files ?? [];
  const landed = block.landed ?? [];
  const all = [...landed.flatMap((l) => l.files), ...files];
  const sum = totals(all);
  const omitted = (block.omitted ?? 0) + landed.reduce((n, l) => n + (l.omitted ?? 0), 0);
  const count = all.length + omitted;
  const commits = landed.reduce((n, l) => n + l.count, 0);
  const anyFileOnly = all.some((f) => !f.diffable && !f.binary);
  const notes: string[] = [
    ...(omitted ? [tf(t, 'chat.turn.changes.omitted', '{{n}} more files are not listed.', { n: omitted })] : []),
    ...(anyFileOnly ? [tf(t, 'chat.turn.changes.tooLarge', 'Some changes are too large to show as a diff. Click a file name to open it.')] : []),
    ...(block.unknown ?? []).map((u) => tf(t, 'chat.turn.changes.unknown',
      'Could not read landings in {{repo}} ({{reason}}).', { repo: u.repo, reason: u.reason })),
    ...(block.landedTruncated ? [tf(t, 'chat.turn.changes.truncated', 'More landings may exist; see Canvas.')] : []),
    ...(block.unverified ? [tf(t, 'chat.turn.changes.unverified',
      '{{n}} landing records could not be verified.', { n: block.unverified })] : []),
  ];
  const totalsEl = (sum.added || sum.deleted) ? (
    <span className="flex gap-2 tabular-nums text-[12px]">
      {!!sum.added && <span className={ADD}>+{sum.added}</span>}
      {!!sum.deleted && <span className={DEL}>−{sum.deleted}</span>}
    </span>
  ) : null;
  const footer = notes.length ? (
    <div className="border-t border-[var(--color-border)]">
      {notes.map((n) => (
        <p key={n} data-testid="changes-note" className="px-3 py-1 text-[12px] text-[var(--color-text-muted)]">{n}</p>
      ))}
    </div>
  ) : null;
  return (
    <section data-testid="turn-changes">
      <ItemBox title={title(t, count)} tail={totalsEl} footer={footer}>
        {all.map((f) => <ChangeRow key={`${f.gitRoot}:${f.start}:${f.absolutePath}`} file={f} />)}
        {commits > 0 && (
          <ItemSection testId="commits-title" title={tf(t, 'chat.turn.changes.commits', 'Commits · {{count}}', { count: commits })} />
        )}
        {landed.map((l) => <CommitRows key={`${l.gitRoot}:${l.new}`} landing={l} />)}
      </ItemBox>
    </section>
  );
}
