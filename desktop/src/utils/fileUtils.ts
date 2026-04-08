/**
 * Shared file-display utilities for icon selection, coloring, and git status badges.
 *
 * These functions were extracted from `TreeNodeRow.tsx` so they can be reused
 * across the Workspace Explorer and the FileEditorModal without creating a
 * component-level dependency between the two.
 *
 * Key exports:
 * - `fileIcon(name)`         — Material Symbols icon name based on file extension
 * - `fileIconColor(name)`    — CSS variable for the file-type icon color
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

/** Return a Material Symbols icon name based on file extension. */
export function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'md':
      return 'description';
    case 'json':
      return 'data_object';
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
      return 'javascript';
    case 'py':
      return 'code';
    case 'css':
    case 'scss':
      return 'style';
    case 'html':
      return 'html';
    case 'svg':
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
      return 'image';
    case 'pdf':
      return 'picture_as_pdf';
    case 'yaml':
    case 'yml':
    case 'toml':
    case 'ini':
    case 'env':
      return 'settings';
    case 'sh':
    case 'bash':
    case 'zsh':
      return 'terminal';
    case 'lock':
      return 'lock';
    case 'log':
      return 'receipt_long';
    case 'txt':
      return 'article';
    case 'doc':
    case 'docx':
      return 'description';
    case 'xls':
    case 'xlsx':
    case 'csv':
      return 'table_chart';
    case 'ppt':
    case 'pptx':
      return 'slideshow';
    case 'zip':
    case 'tar':
    case 'gz':
    case 'rar':
    case '7z':
      return 'folder_zip';
    default:
      return 'draft';
  }
}

/** Return a CSS variable for the file-type icon color. */
export function fileIconColor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'ts':
    case 'tsx':
      return 'var(--color-icon-typescript)';
    case 'js':
    case 'jsx':
      return 'var(--color-icon-javascript)';
    case 'py':
      return 'var(--color-icon-python)';
    case 'css':
    case 'scss':
      return 'var(--color-icon-css)';
    case 'html':
      return 'var(--color-icon-html)';
    case 'json':
      return 'var(--color-icon-json)';
    case 'md':
      return 'var(--color-icon-markdown)';
    case 'svg':
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
      return 'var(--color-icon-image)';
    case 'pdf':
      return 'var(--color-status-error)';
    case 'doc':
    case 'docx':
      return 'var(--color-icon-typescript)';
    case 'xls':
    case 'xlsx':
    case 'csv':
      return 'var(--color-status-success)';
    case 'ppt':
    case 'pptx':
      return 'var(--color-status-warning)';
    default:
      return 'var(--color-icon-default)';
  }
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
