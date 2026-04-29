/**
 * fileViewTypes — File type classification for the unified FileViewer.
 *
 * Replaces the old 3-way classifyFileForPreview() (text/image/unsupported)
 * with fine-grained type routing to per-type renderers.
 *
 * Key exports:
 * - FileViewType          — Union type for all supported viewer modes
 * - classifyFileForViewer  — Extension-based file → renderer routing
 * - getFileTypeInfo        — Human-friendly label + icon for any type
 */

export type FileViewType =
  | 'text'
  | 'markdown'
  | 'svg'
  | 'image'
  | 'pdf'
  | 'html-preview'
  | 'csv'
  | 'video'
  | 'audio'
  | 'unsupported';

const IMAGE_EXTENSIONS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif',
]);

const VIDEO_EXTENSIONS = new Set([
  'mp4', 'webm', 'mov', 'm4v', 'ogv',
]);

const AUDIO_EXTENSIONS = new Set([
  'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'opus',
]);

const NON_RENDERABLE_BINARY = new Set([
  // Office documents
  'docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt', 'odt', 'ods', 'odp',
  // Non-browser images
  'tiff', 'tif', 'heic', 'heif', 'raw', 'cr2', 'nef',
  // Archives
  'zip', 'tar', 'gz', 'bz2', 'xz', 'rar', '7z', 'dmg', 'iso', 'jar', 'war',
  // Executables & compiled
  'exe', 'dll', 'so', 'dylib', 'wasm', 'pyc', 'class', 'o', 'a',
  // Databases
  'sqlite', 'db', 'mdb',
  // Fonts
  'ttf', 'otf', 'woff', 'woff2',
  // Generic binary
  'bin', 'dat', 'pak',
]);

/**
 * Classify a file by name into a FileViewType for renderer routing.
 * More granular than the old classifyFileForPreview — routes to per-type renderers.
 */
export function classifyFileForViewer(fileName: string): FileViewType {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';

  // Exact mappings first
  if (ext === 'md' || ext === 'markdown' || ext === 'mdx') return 'markdown';
  if (ext === 'svg') return 'svg';
  if (ext === 'pdf') return 'pdf';
  if (ext === 'html' || ext === 'htm') return 'html-preview';
  if (ext === 'csv' || ext === 'tsv') return 'csv';

  // Category sets
  if (IMAGE_EXTENSIONS.has(ext)) return 'image';
  if (VIDEO_EXTENSIONS.has(ext)) return 'video';
  if (AUDIO_EXTENSIONS.has(ext)) return 'audio';
  if (NON_RENDERABLE_BINARY.has(ext)) return 'unsupported';

  // Default: try to open as text (code, config, scripts, etc.)
  return 'text';
}

/** Whether a file type supports text editing (save, diff, search). */
export function isEditableType(type: FileViewType): boolean {
  return type === 'text' || type === 'markdown' || type === 'svg' || type === 'csv';
}

/** Whether a file type needs content as base64 instead of UTF-8 text. */
export function isBinaryType(type: FileViewType): boolean {
  return type === 'image' || type === 'pdf' || type === 'video' || type === 'audio' || type === 'unsupported';
}

/** Human-friendly info for each file type. */
export interface FileTypeInfo {
  label: string;
  icon: string;
  category: 'text' | 'media' | 'document' | 'data' | 'binary';
}

export function getFileTypeInfo(fileName: string): FileTypeInfo {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
  const type = classifyFileForViewer(fileName);

  switch (type) {
    case 'text':
      return { label: detectTextLabel(ext), icon: 'code', category: 'text' };
    case 'markdown':
      return { label: 'Markdown', icon: 'description', category: 'text' };
    case 'svg':
      return { label: 'SVG Image', icon: 'image', category: 'text' };
    case 'image':
      return { label: `${ext.toUpperCase()} Image`, icon: 'image', category: 'media' };
    case 'pdf':
      return { label: 'PDF Document', icon: 'picture_as_pdf', category: 'document' };
    case 'html-preview':
      return { label: 'HTML Document', icon: 'language', category: 'text' };
    case 'csv':
      return { label: ext === 'tsv' ? 'TSV Spreadsheet' : 'CSV Spreadsheet', icon: 'table_chart', category: 'data' };
    case 'video':
      return { label: `${ext.toUpperCase()} Video`, icon: 'movie', category: 'media' };
    case 'audio':
      return { label: `${ext.toUpperCase()} Audio`, icon: 'audiotrack', category: 'media' };
    case 'unsupported':
      return { label: detectUnsupportedLabel(ext), icon: detectUnsupportedIcon(ext), category: 'binary' };
  }
}

function detectTextLabel(ext: string): string {
  const labels: Record<string, string> = {
    js: 'JavaScript', jsx: 'JavaScript (JSX)', ts: 'TypeScript', tsx: 'TypeScript (TSX)',
    py: 'Python', rs: 'Rust', go: 'Go', java: 'Java', rb: 'Ruby', php: 'PHP',
    c: 'C', cpp: 'C++', h: 'C Header', cs: 'C#', kt: 'Kotlin', swift: 'Swift',
    sql: 'SQL', sh: 'Shell', bash: 'Bash', zsh: 'Zsh', fish: 'Fish',
    json: 'JSON', yaml: 'YAML', yml: 'YAML', xml: 'XML', toml: 'TOML', ini: 'INI',
    css: 'CSS', scss: 'SCSS', less: 'LESS',
    txt: 'Plain Text', log: 'Log File', env: 'Environment',
  };
  return labels[ext] ?? 'Text File';
}

function detectUnsupportedLabel(ext: string): string {
  const labels: Record<string, string> = {
    docx: 'Word Document', xlsx: 'Excel Spreadsheet', pptx: 'PowerPoint Presentation',
    doc: 'Word Document (Legacy)', xls: 'Excel Spreadsheet (Legacy)', ppt: 'PowerPoint (Legacy)',
    zip: 'ZIP Archive', tar: 'TAR Archive', gz: 'GZip Archive', rar: 'RAR Archive', '7z': '7-Zip Archive',
    dmg: 'Disk Image', iso: 'Disk Image',
    exe: 'Executable', dll: 'Dynamic Library', so: 'Shared Library', dylib: 'Dynamic Library',
    sqlite: 'SQLite Database', db: 'Database',
    ttf: 'TrueType Font', otf: 'OpenType Font', woff: 'Web Font', woff2: 'Web Font',
    tiff: 'TIFF Image', tif: 'TIFF Image', heic: 'HEIC Image', heif: 'HEIF Image',
  };
  return labels[ext] ?? 'Binary File';
}

function detectUnsupportedIcon(ext: string): string {
  const docExts = new Set(['docx', 'doc', 'odt', 'xlsx', 'xls', 'ods', 'pptx', 'ppt', 'odp']);
  const archiveExts = new Set(['zip', 'tar', 'gz', 'bz2', 'xz', 'rar', '7z', 'dmg', 'iso']);
  const fontExts = new Set(['ttf', 'otf', 'woff', 'woff2']);
  const dbExts = new Set(['sqlite', 'db', 'mdb']);
  const imgExts = new Set(['tiff', 'tif', 'heic', 'heif', 'raw', 'cr2', 'nef']);

  if (docExts.has(ext)) return 'article';
  if (archiveExts.has(ext)) return 'folder_zip';
  if (fontExts.has(ext)) return 'font_download';
  if (dbExts.has(ext)) return 'storage';
  if (imgExts.has(ext)) return 'image_not_supported';
  return 'insert_drive_file';
}
