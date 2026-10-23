/**
 * FileTypeIcon — the one file-type mark every file row in the app draws: a small
 * coloured letter badge (MD, TS, PY, {} …) from `fileTypeBadge` (utils/fileUtils).
 * The file tree, chat file cards, inline file references and the Canvas lists all
 * use it, so a type looks the same everywhere (XG 2026-10-23, option A).
 *
 * Styling lives in index.css (`.file-badge`, sizes `xs` / `sm` / `md`); the label
 * is decorative (aria-hidden) because the file name next to it says the same.
 *
 * @exports FileTypeIcon
 */
import type { CSSProperties } from 'react';
import { fileTypeBadge } from '../../utils/fileUtils';

export function FileTypeIcon({
  name, size = 'sm', color, muted, className, testId,
}: {
  /** File name or path; only the extension (or a known bare name) matters. */
  name: string;
  size?: 'xs' | 'sm' | 'md';
  /** Overrides the type colour (the file tree tints by git status). */
  color?: string;
  /** Desaturated, for references inside prose where a type must not read as a status. */
  muted?: boolean;
  className?: string;
  testId?: string;
}) {
  const b = fileTypeBadge(name);
  return (
    <span
      aria-hidden="true"
      className={`file-badge file-badge-${size}${muted ? ' file-badge-muted' : ''}${className ? ` ${className}` : ''}`}
      style={{ '--fb': color ?? b.color } as CSSProperties}
      data-testid={testId}
      data-label={b.label}
    >
      {b.label}
    </span>
  );
}
