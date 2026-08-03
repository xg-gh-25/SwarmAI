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
 * filter + useChangeStatus git NEW/UPD badge) lives in hooks/ and is shared
 * with Radar. Only this ~presentation shell is Canvas-owned — it imports
 * NOTHING from pages/chat/components/RightSidebar/ (R29: the Radar sidebar is
 * owned by a parallel session; Canvas must not touch it).
 *
 * Click → dispatch swarm:open-file so the file opens in the same Canvas panel.
 * `autoDiff` is set for modified files (git 'upd') so they land on their diff;
 * new files open on source; renderable types default to preview in the viewer.
 *
 * Browsing row (run_5b330415): Canvas is also used to BROWSE a file opened from a
 * chat link that was NOT written this session. Such a file (the `selectedPath`
 * prop, when it matches no written output) is injected as a single "Browsing" row
 * below the outputs, and the bee empty-state is suppressed while it is shown — so
 * browsing never leaves a big empty band obscuring the file surface below.
 *
 * @exports CanvasOutputRail
 * @exports isBookkeepingPath — pure predicate (unit-tested)
 * @exports outputRowOpenDetail — pure builder for the open-file event detail (unit-tested)
 */
import { memo, useCallback, useMemo, useEffect, useRef } from 'react';
import { useReferencedFiles, type ReferencedFile } from '../../hooks/useReferencedFiles';
import { useChangeStatus, type ChangeStatus } from '../../hooks/useChangeStatus';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';
import { copyToClipboard } from '../../utils/clipboard';

/**
 * Known bookkeeping dot-directories. A DENYLIST, not "any dot-segment" — the
 * whole workspace lives under `~/.swarm-ai/`, so a blanket dot-segment rule
 * would drop EVERY absolute deliverable path (caught by test 2026-08-02).
 * `.swarm-ai`, `.aws`, etc. are NOT bookkeeping; these three are.
 */
const BOOKKEEPING_DIRS = new Set(['.artifacts', '.git', '.context']);

/**
 * True if a path is agent bookkeeping, not a user-facing deliverable.
 * Filtered OUT of the Canvas output list. Pure so it can be unit-tested and
 * reused by the auto-surface hook (same skip rule).
 *  - a `.artifacts` / `.git` / `.context` segment anywhere → bookkeeping
 *  - the FILE ITSELF is a dotfile (basename starts with `.`) → .DS_Store, .eslintrc
 *  - temp/scratch → /tmp, .tmp, ~ backups
 * Note: an ANCESTOR being a dot-dir (e.g. the `.swarm-ai` workspace root) does
 * NOT make a real deliverable bookkeeping — only the specific dirs above do.
 */
export function isBookkeepingPath(path: string): boolean {
  if (!path) return true;
  const segments = path.split('/');
  const base = segments[segments.length - 1] || '';
  // known bookkeeping dir anywhere in the path
  if (segments.some((s) => BOOKKEEPING_DIRS.has(s))) return true;
  // the file itself is a dotfile
  if (base.startsWith('.')) return true;
  // temp / scratch
  if (path.startsWith('/tmp/') || path.startsWith('/private/tmp/')) return true;
  if (base.endsWith('.tmp') || base.endsWith('~')) return true;
  return false;
}

const BADGE_STYLE: Record<ChangeStatus, { dotCls: string; tag: string; tagCls: string }> = {
  new: { dotCls: 'bg-green-400', tag: 'NEW', tagCls: 'text-green-400' },
  upd: { dotCls: 'bg-yellow-500', tag: 'UPD', tagCls: 'text-yellow-500' },
};

/** Sort: NEW first, then UPD, then unbadged; newest-first within a group. */
function badgeRank(s: ChangeStatus | undefined): number {
  return s === 'new' ? 0 : s === 'upd' ? 1 : 2;
}

/**
 * Build the swarm:open-file event detail for an output row. Pure — unit-tested.
 *
 * AC3: outputs open on SOURCE (single line-number gutter), NEVER auto-diff. The
 * diff view renders two before|after gutters which reads as "double line numbers";
 * a user who wants the diff toggles it via the editor's Show Changes button. The
 * `badge` arg is retained for signature stability / future per-status behavior but
 * no longer forces the diff view open.
 */
export function outputRowOpenDetail(
  path: string,
  _badge: ChangeStatus | undefined,
): { path: string; autoDiff: boolean } {
  return { path, autoDiff: false };
}

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
    // Always open on source (single gutter). Diff is reached via the editor's
    // Show Changes toggle — auto-diff-on-open showed a doubled old|new gutter.
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, { detail: outputRowOpenDetail(file.path, badge) }),
    );
  }, [file.path, badge]);

  const handleCopy = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      await copyToClipboard(file.absolutePath || file.path);
    },
    [file.absolutePath, file.path],
  );

  const style = badge ? BADGE_STYLE[badge] : null;
  const dir = dirOf(file.path);

  // Row = [status dot] [mono name] [NEW/UPD tag] [dir · right-aligned] [copy · hover].
  // Selected row carries a NEUTRAL left-bar (::before) + neutral fill — the ONE
  // signal linking the stream to the focused file below. Region A (the list) is
  // kept accent-FREE by design: ONLY the header carries the primary accent (the
  // color-primary bottom-border in FileViewerPanel). So selection uses neutral
  // greys — a --color-border FILL (stronger than the --color-hover hover shade, so
  // selected ≠ hovered) + a --color-text-muted left bar — never --color-primary.
  return (
    <div
      className={`group relative flex h-[30px] items-center gap-2 pl-3 pr-2 cursor-pointer rounded-md text-[12.5px] transition-colors ${
        fresh ? 'canvas-output-fresh ' : ''
      }${
        selected
          ? 'bg-[var(--color-border)]'
          : 'hover:bg-[var(--color-hover)]'
      }`}
      onClick={handleClick}
      title={file.path}
      data-selected={selected || undefined}
      data-testid="canvas-output-row"
    >
      {selected && (
        <span
          aria-hidden="true"
          className="absolute left-0.5 top-1.5 bottom-1.5 w-[2.5px] rounded-full bg-[var(--color-text-muted)]"
        />
      )}
      {style && <span className={`shrink-0 w-[7px] h-[7px] rounded-full ${style.dotCls}`} aria-hidden="true" />}
      <span
        className={`shrink-0 truncate font-mono ${
          selected ? 'text-[var(--color-text)] font-medium' : 'text-[var(--color-text-muted)]'
        }`}
      >
        {file.fileName}
      </span>
      {style && <span className={`shrink-0 text-[9px] font-bold tracking-wide ${style.tagCls}`}>{style.tag}</span>}
      {dir && (
        <span className="ml-auto truncate font-mono text-[10.5px] text-[var(--color-text-faint,var(--color-text-muted))]">
          {dir}
        </span>
      )}
      <button
        onClick={handleCopy}
        className={`${dir ? '' : 'ml-auto '}opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all shrink-0`}
        title="Copy path"
        aria-label={`Copy path of ${file.fileName}`}
      >
        <span className="material-symbols-outlined text-[12px]">content_copy</span>
      </button>
    </div>
  );
});

export interface OutputCounts {
  total: number;
  neu: number; // NEW-badged ('new' is a reserved word)
  upd: number;
}

export interface CanvasOutputRailProps {
  /** Active-tab session id (from useSessionMeta) — undefined before a session exists. */
  sessionId: string | undefined;
  /** Reports the current counts up to the header (for the summary line). */
  onCounts?: (counts: OutputCounts) => void;
  /** Path of the file currently shown in Region B — gets the accent left-bar. */
  selectedPath?: string;
}

export const CanvasOutputRail = memo(function CanvasOutputRail({ sessionId, onCounts, selectedPath }: CanvasOutputRailProps) {
  const { files: grouped } = useReferencedFiles(sessionId ?? '');

  // Mount timestamp — an output whose firstSeen is AFTER this arrived while the
  // user was watching → it gets one land-pulse (§v6 #4). Outputs already present
  // at mount (session restore, tab switch) are NOT "fresh" (no pulse-storm on
  // open). Ref, set once on mount; never triggers a re-render.
  const mountedAtRef = useRef<number>(Date.now());

  // Real deliverables only: written group minus bookkeeping noise.
  const outputs = useMemo(
    () => (grouped.written ?? []).filter((f) => !isBookkeepingPath(f.path)),
    [grouped.written],
  );

  const paths = useMemo(() => outputs.map((f) => f.path), [outputs]);
  const statusMap = useChangeStatus(paths);

  // NEW before UPD before unbadged; newest-first (higher firstSeen) within a rank.
  const ordered = useMemo(() => {
    return [...outputs].sort((a, b) => {
      const r = badgeRank(statusMap.get(a.path)) - badgeRank(statusMap.get(b.path));
      return r !== 0 ? r : b.firstSeen - a.firstSeen;
    });
  }, [outputs, statusMap]);

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
      const b = statusMap.get(f.path);
      if (b === 'new') neu++;
      else if (b === 'upd') upd++;
    }
    onCounts({ total: outputs.length, neu, upd });
  }, [outputs, statusMap, onCounts]);

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
          No outputs yet — keep chatting; whatever I create or edit lands here and
          flies out to you.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-px py-0.5" data-testid="canvas-output-rail">
      {ordered.map((file) => (
        <OutputRow
          key={file.path}
          file={file}
          badge={statusMap.get(file.path)}
          selected={selectedPath === file.path}
          fresh={file.firstSeen > mountedAtRef.current}
        />
      ))}
      {browsingFile && (
        <BrowsingRow
          path={browsingFile.path}
          fileName={browsingFile.fileName}
        />
      )}
    </div>
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
    <>
      {/* Always label the browsing row — a lone unlabeled row (browsing with no
          written outputs, the common case) reads as noise; the label gives it
          context (adversarial F2). */}
      <div
        className="px-3 pt-1.5 pb-0.5 text-[9px] font-bold uppercase tracking-[0.1em] text-[var(--color-text-faint,var(--color-text-muted))]"
        aria-hidden="true"
      >
        Browsing
      </div>
      <div
        className="group relative flex h-[30px] items-center gap-2 pl-3 pr-2 cursor-pointer rounded-md text-[12.5px] transition-colors bg-[var(--color-border)]"
        onClick={handleClick}
        title={path}
        data-selected
        data-testid="canvas-browsing-row"
      >
        <span
          aria-hidden="true"
          className="absolute left-0.5 top-1.5 bottom-1.5 w-[2.5px] rounded-full bg-[var(--color-text-muted)]"
        />
        <span className="material-symbols-outlined shrink-0 text-[14px] text-[var(--color-text-muted)]" aria-hidden="true">visibility</span>
        <span className="shrink-0 truncate font-mono text-[var(--color-text)] font-medium">{fileName}</span>
        {dir && (
          <span className="ml-auto truncate font-mono text-[10.5px] text-[var(--color-text-faint,var(--color-text-muted))]">
            {dir}
          </span>
        )}
        <button
          onClick={handleCopy}
          className={`${dir ? '' : 'ml-auto '}opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all shrink-0`}
          title="Copy path"
          aria-label={`Copy path of ${fileName}`}
        >
          <span className="material-symbols-outlined text-[12px]">content_copy</span>
        </button>
      </div>
    </>
  );
});
