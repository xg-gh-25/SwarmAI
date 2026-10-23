/**
 * CanvasOutputRail — the session's OUTPUT list inside the Canvas surface.
 *
 * Shows ONLY the real deliverables the agent produced this session — the
 * `written` group from useReferencedFiles, MINUS bookkeeping noise
 * (.artifacts/ pipeline records, dotfiles, temp/scratch). This is STRICTER
 * than Radar's ChangesSection (which shows all written files): Canvas is
 * "outputs shown to the user", not "every file the agent touched".
 *
 * Reuse, not duplication of logic: the EXTRACTION (useReferencedFiles written
 * filter; each row's NEW/UPD badge is the backend's inHead) lives in hooks/ and is shared
 * with Radar. Only this ~presentation shell is Canvas-owned — it imports
 * NOTHING from pages/chat/components/RightSidebar/ (R29: the Radar sidebar is
 * owned by a parallel session; Canvas must not touch it).
 *
 * Click → dispatch swarm:open-file so the file opens in the same Canvas panel.
 * Rows open on the file's CONTENT (autoDiff:false, run_d3cc1f2c); the diff is a
 * per-file "Show Changes" toggle in FileEditorCore, not the open default.
 *
 * Browsing row (run_5b330415): Canvas is also used to BROWSE a file opened from a
 * chat link that was NOT written this session. Such a file (the `selectedPath`
 * prop, when it matches no written output) is injected as a single "Browsing" row
 * below the outputs, and the bee empty-state is suppressed while it is shown — so
 * browsing never leaves a big empty band obscuring the file surface below.
 *
 * @exports CanvasOutputRail
 * @exports clockOf — a row's first-seen time as shown (HH:MM today, M/D before)
 * @exports onOutputListKey — ↑/↓ on the focused list opens the previous / next row
 * @exports outputRowOpenDetail, railOrder — re-exported from hooks/railSsot (the
 *   pure open-detail builder and the rail's display order live there, so a test
 *   that stubs this component still gets them)
 */
import { memo, useCallback, useMemo, useEffect, useRef, useState } from 'react';
import { type ChangeStatus, type ReferencedFile, type GroupedReferencedFiles } from '../../hooks/useReferencedFiles';
import { orderOutputs, outputRowOpenDetail } from '../../hooks/railSsot';

export { outputRowOpenDetail, railOrder } from '../../hooks/railSsot';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';
import { copyToClipboard } from '../../utils/clipboard';
import { fileIcon, fileIconColor } from '../../utils/fileUtils';

// run_4de279ca: isBookkeepingPath + BOOKKEEPING_DIRS REMOVED. The backend git
// verdict (`kind`, needs_human_review) is the SOLE surfacing authority — this
// frontend denylist was a byte-for-byte duplicate that drifted from the real
// git-based machine/human boundary. The rail now filters on `kind` and the
// auto-surface hook gates on `kind` alone.

/**
 * Tree-list status chip (mirrors the SwarmWS explorer's `gitStatusBadge`): a
 * COMPACT single-letter chip — `A` (added=green) / `M` (modified=yellow) — using
 * the git SEMANTIC color CSS vars, NEVER `--color-primary` (the accent is
 * header-only; the list rows carry only git-semantic color, XG 2026-08-04).
 */
const BADGE_STYLE: Record<ChangeStatus, { label: string; color: string }> = {
  new: { label: 'A', color: 'var(--color-git-added)' },
  upd: { label: 'M', color: 'var(--color-git-modified)' },
};

/** Directory portion of a path (everything before the basename), for the dim
 *  right-aligned locator. Empty string when the path has no directory.
 *  Paths here are always POSIX workspace paths (`/Users/.../file`); a Windows
 *  drive-root like `C:\x` would yield `C:` as the "dir", but that input class
 *  cannot occur in this macOS/Linux app — not defended (adversarial MED,
 *  run_09431085; cosmetic-only + non-triggering, so no dead guard added). */
function dirOf(path: string): string {
  const norm = path.replace(/\\/g, '/');
  const i = norm.lastIndexOf('/');
  return i > 0 ? norm.slice(0, i) : '';
}

const OutputRow = memo(function OutputRow({
  file,
  badge,
  selected,
  fresh,
}: {
  file: ReferencedFile;
  badge: ChangeStatus | undefined;
  /** True when this row's file is the one shown in Region B below → accent left-bar. */
  selected: boolean;
  /** True when this output ARRIVED after the rail mounted → one gentle accent
   *  land-pulse (announces the product without grabbing). Keyed rows mount once,
   *  so the CSS animation plays exactly once and never replays on re-render. */
  fresh: boolean;
}) {
  const handleClick = useCallback(() => {
    // A DELETED row (run_5d9178bf) has no file on disk → opening would 404. The row
    // persists to show WHAT was deleted, but it is not clickable-to-open.
    if (file.deleted) return;
    // Open the row on its CONTENT (run_d3cc1f2c — autoDiff:false; diff is a toggle).
    // Pass absolutePath as the resolve anchor so a source-final row (repo-relative
    // display path, repo ≠ workspace) still resolves + opens.
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, { detail: outputRowOpenDetail(file.path, badge, file.absolutePath, file.baseRef, file.sessionId) }),
    );
  }, [file.path, file.absolutePath, file.baseRef, file.sessionId, badge, file.deleted]);

  const handleCopy = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      await copyToClipboard(file.absolutePath || file.path);
    },
    [file.absolutePath, file.path],
  );

  const style = badge ? BADGE_STYLE[badge] : null;
  const dir = dirOf(file.path);

  // Row = [file icon] [name] [A/M chip] [dir · right-aligned] [copy · hover] [time]
  // — 12px, ~26px row. Selected = `.canvas-row-sel` (index.css, shared with the
  // Changes list): the content card's surface + a 3px primary bar + bold name, so the
  // row reads as the thing the card below shows. Other rows: muted text, name in text
  // colour. The A/M chip carries only the git SEMANTIC colour (green/yellow).
  const rowRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // The selected row stays in view inside the ~5-row list (↑/↓, auto-open).
    if (selected) rowRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [selected]);
  const time = clockOf(file.firstSeen);
  return (
    <li
      ref={rowRef}
      className={`group relative flex h-[26px] items-center gap-1.5 pl-2.5 pr-2 rounded-md text-[12px] transition-colors list-none ${
        file.deleted ? 'cursor-default opacity-60 ' : 'cursor-pointer '
      }${
        fresh ? 'canvas-output-fresh ' : ''
      }${
        selected
          ? 'canvas-row-sel'
          : file.deleted ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
      }`}
      onClick={handleClick}
      title={file.deleted ? `${file.path} (deleted)` : file.path}
      data-selected={selected || undefined}
      data-deleted={file.deleted || undefined}
      data-testid="canvas-output-row"
    >
      <span
        className="material-symbols-outlined icon-sm shrink-0 leading-none"
        style={{ color: fileIconColor(file.fileName) }}
        aria-hidden="true"
        data-testid="canvas-output-icon"
      >
        {fileIcon(file.fileName)}
      </span>
      <span
        className={`shrink-0 truncate ${
          file.deleted ? 'line-through text-[var(--color-text-muted)]' :
          selected ? 'text-[var(--color-text)] font-semibold' : 'text-[var(--color-text)]'
        }`}
      >
        {file.fileName}
      </span>
      {style && (
        <span
          className="shrink-0 text-[11px] font-semibold leading-none tabular-nums"
          style={{ color: style.color }}
          data-testid="canvas-output-badge"
          title={badge === 'new' ? 'added' : 'modified'}
        >
          {style.label}
        </span>
      )}
      {dir && (
        <span className="ml-auto min-w-0 truncate text-[11px] text-[var(--color-text-faint,var(--color-text-muted))]">
          {dir}
        </span>
      )}
      <button
        onClick={handleCopy}
        className={`${dir ? '' : 'ml-auto '}opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all shrink-0`}
        title="Copy absolute path"
        aria-label={`Copy path of ${file.fileName}`}
      >
        <span className="material-symbols-outlined icon-sm">content_copy</span>
      </button>
      {time && (
        <span
          className="shrink-0 text-[11px] tabular-nums text-[var(--color-text-dim)]"
          title={new Date(file.firstSeen).toLocaleString()}
          data-testid="canvas-output-time"
        >
          {time}
        </span>
      )}
    </li>
  );
});

/** A row's first-seen time as the list shows it: `HH:MM` today, `M/D` before.
 *  '' when there is no time (an older payload without firstSeen). */
export function clockOf(ms: number | undefined, now: Date = new Date()): string {
  if (!ms || !Number.isFinite(ms)) return '';
  const d = new Date(ms);
  if (d.toDateString() !== now.toDateString()) return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** ↑/↓ while the Artifacts list has focus: open the previous / next openable row
 *  (a deleted row is skipped; a folded Activity group is not rendered, so skipped
 *  too). Opening goes through the row's own click, so it is the same open a mouse
 *  click does — one path. */
export function onOutputListKey(e: React.KeyboardEvent<HTMLElement>): void {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const rows = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(
    '[data-testid="canvas-output-row"]:not([data-deleted]), [data-testid="canvas-browsing-row"]',
  ));
  if (rows.length === 0) return;
  e.preventDefault();
  const cur = rows.findIndex((r) => r.hasAttribute('data-selected'));
  const step = e.key === 'ArrowDown' ? 1 : -1;
  const next = cur < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, cur + step));
  if (next !== cur) rows[next].click();
}

export interface OutputCounts {
  total: number;
  neu: number; // NEW-badged ('new' is a reserved word)
  upd: number;
}

export interface CanvasOutputRailProps {
  /** The referenced-files (rail rows), supplied by the RESIDENT owner
   *  (useCanvasHost via FileViewerPanel — run_9e42c066). The rail is now PURE
   *  PRESENTATIONAL: it does NOT run its own swarm:file-changed listener (that
   *  would be a SECOND listener writing the same per-tab sessionStorage key —
   *  Gate-1 Defect 2). The single listener lives in useCanvasHost so a write is
   *  captured even when this panel is unmounted (Canvas closed). */
  files: GroupedReferencedFiles;
  /** Reports the current counts up to the header (for the summary line). */
  onCounts?: (counts: OutputCounts) => void;
  /** Path of the file currently shown in Region B — gets the accent left-bar. */
  selectedPath?: string;
}

export const CanvasOutputRail = memo(function CanvasOutputRail({ files: grouped, onCounts, selectedPath }: CanvasOutputRailProps) {

  // Mount timestamp — an output whose firstSeen is AFTER this arrived while the
  // user was watching → it gets one land-pulse (§v6 #4). Outputs already present
  // at mount (session restore, tab switch) are NOT "fresh" (no pulse-storm on
  // open). Ref, set once on mount; never triggers a re-render.
  const mountedAtRef = useRef<number>(Date.now());

  // Real deliverables only. run_4de279ca: the backend git verdict (`kind`) is the
  // SOLE authority — the sweep only ever emits content/knowledge (process/source are
  // dropped/aggregated server-side), so the old frontend isBookkeepingPath duplicate
  // is deleted. Filter defensively on kind: keep content/knowledge; drop process; an
  // undefined kind (older backend) falls through to keep (no regression — server
  // already dropped bookkeeping upstream).
  // `grouped?.written` is guarded inside orderOutputs (O023: a stubbed/legacy caller
  // could omit the files prop — fail to empty, never crash).
  const ordered = useMemo(() => orderOutputs(grouped), [grouped]);
  const outputs = ordered;

  // Activity rows (DailyActivity, signals, briefs — the session's bookkeeping, by
  // the product catalog's role) fold under one toggle, closed by default; a
  // selected one opens it so the row in view is never hidden.
  const [activityShown, setActivityShown] = useState(false);
  const main = useMemo(() => ordered.filter((f) => f.role !== 'Activity'), [ordered]);
  const activity = useMemo(() => ordered.filter((f) => f.role === 'Activity'), [ordered]);

  // ── Browsing row ────────────────────────────────────────────────────────────
  // Canvas is used two ways: (1) the agent WRITES a file → it lands in `outputs`
  // above; (2) the user clicks a file link in chat to BROWSE a file NOT written
  // this session. In case (2) `selectedPath` is set but matches no written row, so
  // the outputs list is empty and the bee empty-state used to render ABOVE the file
  // surface, obscuring it. We inject the browsed file as one "Browsing" row instead.
  //
  // Dedup (Gate-1 A): `selectedPath` is the RESOLVED path (useCanvasHost resolves via
  // /workspace/file/resolve), while a written row's identity is the DISPLAY `path`.
  // So compare against BOTH `f.path` (display) AND `f.absolutePath` (resolved) — a
  // browsed file that IS also a written output must NOT double-render. Uses the
  // existing fields; no fragile path normalizer.
  const browsingFile = useMemo((): { path: string; fileName: string } | null => {
    if (!selectedPath) return null;
    const isWritten = outputs.some(
      (f) => f.path === selectedPath || f.absolutePath === selectedPath,
    );
    if (isWritten) return null;
    const fileName = selectedPath.replace(/\\/g, '/').split('/').pop() || selectedPath;
    return { path: selectedPath, fileName };
  }, [selectedPath, outputs]);

  // Publish counts to the header. Effect (not render-time call) so we never
  // setState-in-render a parent. neu/upd from the git badge map.
  useEffect(() => {
    if (!onCounts) return;
    let neu = 0, upd = 0;
    for (const f of outputs) {
      const b = f.change;
      if (b === 'new') neu++;
      else if (b === 'upd') upd++;
    }
    onCounts({ total: outputs.length, neu, upd });
  }, [outputs, onCounts]);

  if (ordered.length === 0 && !browsingFile) {
    // Empty state: a friendly reminder + a pure-CSS bee (🐝 Swarm identity)
    // flying a gentle loop. When the first output lands it flies out (the rail
    // re-renders into the list). Zero-dependency — see index.css .canvas-bee*.
    // Guarded on `!browsingFile`: when the user is browsing a chat-opened file
    // (not a written output), we show a Browsing row instead of the big empty
    // band that would otherwise obscure the file surface below (run_5b330415).
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 px-3 py-6 text-center"
        data-testid="canvas-output-rail-empty"
      >
        <div className="canvas-bee-field" aria-hidden="true">
          <span className="canvas-bee">🐝</span>
        </div>
        <p className="text-[11px] text-[var(--color-text-muted)] leading-relaxed max-w-full">
          No artifacts yet — keep chatting; whatever I create or edit lands here and
          flies out to you.
        </p>
      </div>
    );
  }

  const renderRow = (file: ReferencedFile) => (
    <OutputRow
      key={file.path}
      file={file}
      badge={file.change}
      selected={selectedPath === file.path}
      fresh={file.firstSeen > mountedAtRef.current}
    />
  );
  const activityOpen = activityShown || activity.some((f) => f.path === selectedPath);

  return (
    <ul
      className="flex flex-col gap-px py-0.5 list-none m-0 p-0 outline-none"
      data-testid="canvas-output-rail"
      tabIndex={0}
      aria-label="Artifacts (↑/↓ to switch file)"
      onKeyDown={onOutputListKey}
    >
      {main.map(renderRow)}
      {activity.length > 0 && (
        <li className="list-none">
          <button
            type="button"
            data-testid="canvas-activity-toggle"
            aria-expanded={activityOpen}
            onClick={() => setActivityShown((v) => !v)}
            className="w-full flex items-center gap-1.5 h-[24px] pl-2.5 pr-2 rounded-md text-left text-[11.5px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]"
          >
            <span className="material-symbols-outlined icon-xs leading-none" aria-hidden="true">
              {activityOpen ? 'expand_more' : 'chevron_right'}
            </span>
            Activity
            <span className="tabular-nums">· {activity.length}</span>
          </button>
        </li>
      )}
      {activityOpen && activity.map(renderRow)}
      {browsingFile && (
        <BrowsingRow
          path={browsingFile.path}
          fileName={browsingFile.fileName}
        />
      )}
    </ul>
  );
});

/**
 * BrowsingRow — the file the user is currently VIEWING that is NOT one of this
 * session's written outputs (opened via a chat file-link). Visually distinct from
 * an output row (eye icon, no NEW/UPD badge, always shown selected). Click
 * re-dispatches open-file (idempotent — keeps it open); copy-path yields the path.
 */
const BrowsingRow = memo(function BrowsingRow({
  path,
  fileName,
}: {
  path: string;
  fileName: string;
}) {
  const handleClick = useCallback(() => {
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, { detail: outputRowOpenDetail(path, undefined) }),
    );
  }, [path]);

  const handleCopy = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      await copyToClipboard(path);
    },
    [path],
  );

  const dir = dirOf(path);

  return (
    <li className="list-none flex flex-col" data-testid="canvas-browsing-item">
      {/* Always label the browsing row — a lone unlabeled row (browsing with no
          written outputs, the common case) reads as noise; the label gives it
          context (adversarial F2). Nested inside the listitem so the <ul> stays
          valid (a bare <div> child of <ul> is invalid markup). */}
      <div
        className="px-3 pt-1.5 pb-0.5 text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--color-text-faint,var(--color-text-muted))]"
        aria-hidden="true"
      >
        Browsing
      </div>
      <div
        className="group canvas-row-sel flex h-[26px] items-center gap-1.5 pl-2.5 pr-2 cursor-pointer rounded-md text-[12px] transition-colors"
        onClick={handleClick}
        title={path}
        data-selected
        data-testid="canvas-browsing-row"
      >
        <span className="material-symbols-outlined icon-xs shrink-0 text-[var(--color-text-muted)]" aria-hidden="true">visibility</span>
        <span className="shrink-0 truncate text-[var(--color-text)] font-semibold">{fileName}</span>
        {dir && (
          <span className="ml-auto min-w-0 truncate text-[11px] text-[var(--color-text-faint,var(--color-text-muted))]">
            {dir}
          </span>
        )}
        <button
          onClick={handleCopy}
          className={`${dir ? '' : 'ml-auto '}opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all shrink-0`}
          title="Copy absolute path"
          aria-label={`Copy path of ${fileName}`}
        >
          <span className="material-symbols-outlined icon-sm">content_copy</span>
        </button>
      </div>
    </li>
  );
});
