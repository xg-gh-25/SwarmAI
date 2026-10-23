/**
 * Shared file-display utilities for icon selection, coloring, and git status badges.
 *
 * These functions were extracted from `TreeNodeRow.tsx` so they can be reused
 * across the Workspace Explorer and the FileEditorModal without creating a
 * component-level dependency between the two.
 *
 * Key exports:
 * - `fileTypeBadge(name)`    — the file-type badge (label + colour) `<FileTypeIcon>` draws
 * - `gitStatusColor(status)` — CSS variable for git-status text/icon color
 * - `gitStatusBadge(status)` — Short badge descriptor (label, color, bg) for git status
 */

import type { GitStatus } from '../types';

/* ------------------------------------------------------------------ */
/*  Git status helpers                                                 */
/* ------------------------------------------------------------------ */

/** Map git status to the CSS variable name for text/icon color. */
export function gitStatusColor(status?: GitStatus): string | undefined {
  if (!status) return undefined;
  const map: Record<GitStatus, string> = {
    added: 'var(--color-git-added)',
    modified: 'var(--color-git-modified)',
    deleted: 'var(--color-git-deleted)',
    renamed: 'var(--color-git-renamed)',
    untracked: 'var(--color-git-untracked)',
    conflicting: 'var(--color-git-conflicting)',
    ignored: 'var(--color-git-ignored)',
  };
  return map[status];
}

/** Map git status to a short badge label (like VS Code / Kiro). */
export function gitStatusBadge(status?: GitStatus): { label: string; color: string; bg: string } | null {
  if (!status) return null;
  const badges: Record<GitStatus, { label: string; color: string; bg: string }> = {
    added:       { label: 'A', color: 'var(--color-git-added)',       bg: 'var(--color-git-badge-added-bg)' },
    modified:    { label: 'M', color: 'var(--color-git-modified)',    bg: 'var(--color-git-badge-modified-bg)' },
    deleted:     { label: 'D', color: 'var(--color-git-deleted)',     bg: 'var(--color-git-badge-deleted-bg)' },
    renamed:     { label: 'R', color: 'var(--color-git-renamed)',     bg: 'var(--color-git-badge-renamed-bg)' },
    untracked:   { label: 'U', color: 'var(--color-git-untracked)',   bg: 'var(--color-git-badge-untracked-bg)' },
    conflicting: { label: 'C', color: 'var(--color-git-conflicting)', bg: 'var(--color-git-badge-conflicting-bg)' },
    ignored:     { label: 'I', color: 'var(--color-git-ignored)',     bg: 'var(--color-git-badge-ignored-bg, transparent)' },
  };
  return badges[status];
}

/* ------------------------------------------------------------------ */
/*  File icon helpers                                                  */
/* ------------------------------------------------------------------ */

/** A file's extension (lower case, no dot), or '' when it has none. A leading
 *  dot alone (`.env`) counts as the extension. */
function extOf(name: string): string {
  const base = name.replace(/\\/g, '/').split('/').pop() ?? '';
  const i = base.lastIndexOf('.');
  return i < 0 ? '' : base.slice(i + 1).toLowerCase();
}

const BADGE_LABEL: Record<string, string> = {
  md: 'MD', mdx: 'MD', markdown: 'MD',
  ts: 'TS', tsx: 'TS', mts: 'TS', cts: 'TS',
  js: 'JS', jsx: 'JS', mjs: 'JS', cjs: 'JS',
  py: 'PY', json: '{}', jsonl: '{}',
  yaml: 'YML', yml: 'YML', toml: 'TML', ini: 'INI', env: 'ENV',
  css: 'CSS', scss: 'CSS', html: 'HTM', htm: 'HTM', xml: 'XML',
  svg: 'SVG', png: 'IMG', jpg: 'IMG', jpeg: 'IMG', gif: 'IMG', webp: 'IMG', bmp: 'IMG', ico: 'IMG',
  pdf: 'PDF', sh: 'SH', bash: 'SH', zsh: 'SH',
  txt: 'TXT', log: 'LOG', lock: 'LCK', csv: 'CSV',
  doc: 'DOC', docx: 'DOC', xls: 'XLS', xlsx: 'XLS', ppt: 'PPT', pptx: 'PPT',
  zip: 'ZIP', tar: 'ZIP', gz: 'ZIP', rar: 'ZIP', '7z': 'ZIP',
  rs: 'RS', go: 'GO', java: 'JV', kt: 'KT', swift: 'SW', rb: 'RB', sql: 'SQL',
};

/** Extensionless names that still have a known type. */
const NAMED: Record<string, string> = { dockerfile: 'DK', makefile: 'MK' };

/** Colour per badge label; every value is an existing theme token. */
const BADGE_COLOR: Record<string, string> = {
  MD: 'var(--color-icon-markdown)', TS: 'var(--color-icon-typescript)',
  JS: 'var(--color-icon-javascript)', PY: 'var(--color-icon-python)',
  '{}': 'var(--color-icon-javascript)', CSS: 'var(--color-icon-css)',
  HTM: 'var(--color-icon-html)', XML: 'var(--color-icon-html)',
  SVG: 'var(--color-icon-image)', IMG: 'var(--color-icon-image)',
  YML: 'var(--color-icon-image)', TML: 'var(--color-icon-image)',
  PDF: 'var(--color-status-error)', PPT: 'var(--color-status-warning)',
  CSV: 'var(--color-status-success)', XLS: 'var(--color-status-success)',
  SH: 'var(--color-status-success)', DOC: 'var(--color-icon-typescript)',
  DK: 'var(--color-icon-typescript)', RS: 'var(--color-icon-html)',
  GO: 'var(--color-icon-typescript)',
};

/**
 * The file-type badge every file row in the app shows (XG 2026-10-23, option A):
 * a short label plus its colour. Known types have their own label (MD, TS, {} …);
 * an unknown extension shows its first 3 letters; an extensionless name its first
 * 3 letters (Dockerfile → DK, Makefile → MK). Rendered by `<FileTypeIcon>`.
 */
export function fileTypeBadge(name: string): { label: string; color: string } {
  const ext = extOf(name);
  const base = (name.replace(/\\/g, '/').split('/').pop() ?? '').toLowerCase();
  const label = ext
    ? (BADGE_LABEL[ext] ?? ext.slice(0, 3).toUpperCase())
    : (NAMED[base] ?? (base.slice(0, 3).toUpperCase() || '?'));
  return { label, color: BADGE_COLOR[label] ?? 'var(--color-icon-default)' };
}

// ---------------------------------------------------------------------------
// File preview classification
// ---------------------------------------------------------------------------

export type FilePreviewType = 'image' | 'text' | 'unsupported';

/** Image formats renderable inline via <img> tag.
 *  All listed formats are supported by modern browsers (Chrome, Safari, Firefox).
 *  BMP: supported but uncommon — kept here since all target browsers render it.
 *  TIFF/HEIC: NOT included — no browser <img> support, routed to unsupported.
 *  SVG: NOT included — it's editable XML text, routed to FileEditorCore with
 *  a Preview toggle for visual rendering (same UX pattern as markdown preview).
 */
const IMAGE_EXTENSIONS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico',
]);

/** Binary/document formats that can't be edited as text.
 *  Shown in BinaryPreviewModal with file info + "Open in Default App" + "Copy Path".
 */
const NON_TEXT_BINARY = new Set([
  // Documents
  'pdf', 'docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt',
  // Images not renderable in browser <img> — need system viewer
  'tiff', 'tif', 'heic', 'heif',
  // Media — Audio
  'mp3', 'wav', 'flac', 'ogg', 'aac', 'm4a',
  // Media — Video
  'mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v',
  // Archives
  'zip', 'tar', 'gz', 'rar', '7z', 'dmg', 'iso', 'jar', 'war',
  // Executables & libraries
  'exe', 'dll', 'so', 'dylib', 'wasm',
  // Compiled / bytecode
  'pyc', 'class', 'o',
  // Databases
  'sqlite', 'db',
  // Fonts
  'ttf', 'otf', 'woff', 'woff2',
  // Generic binary
  'bin', 'dat',
]);

/**
 * Classify a file for preview routing based on its extension.
 * - 'image': viewable inline via <img> (png, jpg, gif, webp, bmp, ico)
 * - 'unsupported': show info modal with file path, "Open in Default App", and "Copy Path"
 * - 'text': open in FileEditor (includes SVG — editable XML with visual preview toggle)
 */
export function classifyFileForPreview(fileName: string): FilePreviewType {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
  if (IMAGE_EXTENSIONS.has(ext)) return 'image';
  if (NON_TEXT_BINARY.has(ext)) return 'unsupported';
  return 'text';
}
