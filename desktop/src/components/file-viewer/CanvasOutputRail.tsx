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
 * @exports CanvasOutputRail
 * @exports isBookkeepingPath — pure predicate (unit-tested)
 */
import { memo, useCallback, useMemo } from 'react';
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

const BADGE_STYLE: Record<ChangeStatus, { label: string; cls: string }> = {
  new: { label: 'NEW', cls: 'text-green-400 bg-green-400/10' },
  upd: { label: 'UPD', cls: 'text-yellow-500 bg-yellow-500/10' },
};

/** Sort: NEW first, then UPD, then unbadged; newest-first within a group. */
function badgeRank(s: ChangeStatus | undefined): number {
  return s === 'new' ? 0 : s === 'upd' ? 1 : 2;
}

const OutputRow = memo(function OutputRow({
  file,
  badge,
}: {
  file: ReferencedFile;
  badge: ChangeStatus | undefined;
}) {
  const handleClick = useCallback(() => {
    // Modified (upd) → open directly on diff; new/unbadged → plain open
    // (renderers default renderable types to preview; text lands on source).
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, {
        detail: { path: file.path, autoDiff: badge === 'upd' },
      }),
    );
  }, [file.path, badge]);

  const handleCopy = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      await copyToClipboard(file.absolutePath || file.path);
    },
    [file.absolutePath, file.path],
  );

  return (
    <div
      className="group flex h-6 items-center gap-1 px-2 cursor-pointer hover:bg-[var(--color-hover)] rounded text-[12px] transition-colors"
      onClick={handleClick}
      title={file.path}
    >
      {badge && (
        <span className={`shrink-0 rounded px-1 text-[9px] font-bold tracking-wide ${BADGE_STYLE[badge].cls}`}>
          {BADGE_STYLE[badge].label}
        </span>
      )}
      <span className="flex-1 truncate font-mono text-[var(--color-text)]">{file.fileName}</span>
      <button
        onClick={handleCopy}
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all"
        title="Copy path"
        aria-label={`Copy path of ${file.fileName}`}
      >
        <span className="material-symbols-outlined text-[12px]">content_copy</span>
      </button>
    </div>
  );
});

export interface CanvasOutputRailProps {
  /** Active-tab session id (from useSessionMeta) — undefined before a session exists. */
  sessionId: string | undefined;
}

export function CanvasOutputRail({ sessionId }: CanvasOutputRailProps) {
  const { files: grouped } = useReferencedFiles(sessionId ?? '');

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

  if (ordered.length === 0) {
    return (
      <div className="px-2 py-3 text-[11px] text-[var(--color-text-muted)]">
        No outputs yet — files the agent creates or edits will appear here.
      </div>
    );
  }

  return (
    <div className="py-0.5" data-testid="canvas-output-rail">
      {ordered.map((file) => (
        <OutputRow key={file.path} file={file} badge={statusMap.get(file.path)} />
      ))}
    </div>
  );
}
