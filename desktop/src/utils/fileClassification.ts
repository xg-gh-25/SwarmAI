/**
 * Pure file classification and validation utilities.
 *
 * Exports:
 * - MIME_TYPE_MAP, EXTENSION_TYPE_MAP — lookup tables
 * - classifyFile — classify by MIME with extension fallback
 * - isGenericMimeType — check if MIME is generic/missing
 * - determineDeliveryStrategy — select strategy by type+size
 * - validateFileSize — check against per-type limits
 * - validateWorkspacePath — reject traversal/absolute paths
 */
import type { AttachmentType, DeliveryStrategy } from '../types';
import { SIZE_LIMITS, SIZE_THRESHOLD } from '../types';

/**
 * MIME types that Claude API accepts natively in image content blocks.
 * All other image types must be routed via path_hint so the agent reads them with tools.
 */
export const CLAUDE_NATIVE_IMAGE_MIMES = new Set([
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
]);

/**
 * Extensions whose images Claude API accepts natively.
 * Used by determineDeliveryStrategy when MIME type isn't available.
 */
export const CLAUDE_NATIVE_IMAGE_EXTENSIONS = new Set([
  '.jpg', '.jpeg', '.png', '.gif', '.webp',
]);

export const MIME_TYPE_MAP: Record<string, AttachmentType> = {
  // Images
  'image/png': 'image',
  'image/jpeg': 'image',
  'image/gif': 'image',
  'image/webp': 'image',
  'image/svg+xml': 'image',
  'image/bmp': 'image',
  'image/tiff': 'image',
  'image/x-icon': 'image',
  'image/heic': 'image',
  'image/heif': 'image',
  // PDF
  'application/pdf': 'pdf',
  // Office documents
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document',   // .docx
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'document',          // .xlsx
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'document',  // .pptx
  'application/msword': 'document',            // .doc
  'application/vnd.ms-excel': 'document',      // .xls
  'application/vnd.ms-powerpoint': 'document', // .ppt
  'application/rtf': 'document',               // .rtf
  'application/vnd.oasis.opendocument.text': 'document',         // .odt
  'application/vnd.oasis.opendocument.spreadsheet': 'document',  // .ods
  'application/vnd.oasis.opendocument.presentation': 'document', // .odp
  // Audio
  'audio/mpeg': 'audio',           // .mp3
  'audio/mp4': 'audio',            // .m4a
  'audio/wav': 'audio',            // .wav
  'audio/x-wav': 'audio',          // .wav (alt)
  'audio/ogg': 'audio',            // .ogg
  'audio/flac': 'audio',           // .flac
  'audio/aac': 'audio',            // .aac
  'audio/webm': 'audio',           // .weba
  'audio/x-m4a': 'audio',          // .m4a (alt)
  // Video
  'video/mp4': 'video',            // .mp4
  'video/quicktime': 'video',      // .mov
  'video/x-msvideo': 'video',      // .avi
  'video/x-matroska': 'video',     // .mkv
  'video/webm': 'video',           // .webm
  'video/mpeg': 'video',           // .mpeg
  'video/3gpp': 'video',           // .3gp
  // Text
  'text/plain': 'text',
  'text/html': 'text',
  'text/csv': 'csv',
  'application/csv': 'csv',
  'application/json': 'text',
  'application/xml': 'text',
  'text/xml': 'text',
  'text/markdown': 'text',
  'application/x-yaml': 'text',
};

export const EXTENSION_TYPE_MAP: Record<string, AttachmentType> = {
  // Code & config (text)
  '.py': 'text', '.ts': 'text', '.tsx': 'text',
  '.js': 'text', '.jsx': 'text', '.rs': 'text',
  '.go': 'text', '.java': 'text', '.c': 'text',
  '.cpp': 'text', '.h': 'text', '.rb': 'text',
  '.sh': 'text', '.md': 'text', '.txt': 'text',
  '.log': 'text', '.json': 'text',
  '.yaml': 'text', '.yml': 'text', '.toml': 'text',
  '.env': 'text', '.cfg': 'text',
  '.ini': 'text', '.conf': 'text',
  '.html': 'text', '.css': 'text',
  '.scss': 'text', '.xml': 'text', '.sql': 'text',
  '.kt': 'text', '.swift': 'text', '.r': 'text',
  '.lua': 'text', '.pl': 'text', '.php': 'text',
  '.dart': 'text', '.scala': 'text', '.zig': 'text',
  '.tf': 'text', '.hcl': 'text', '.proto': 'text',
  '.graphql': 'text', '.gql': 'text',
  '.dockerfile': 'text', '.makefile': 'text',
  // Images
  '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
  '.gif': 'image', '.webp': 'image', '.svg': 'image',
  '.bmp': 'image', '.tiff': 'image', '.tif': 'image',
  '.ico': 'image', '.heic': 'image', '.heif': 'image',
  // PDF
  '.pdf': 'pdf',
  // Office documents
  '.docx': 'document', '.xlsx': 'document', '.pptx': 'document',
  '.doc': 'document', '.xls': 'document', '.ppt': 'document',
  '.rtf': 'document', '.odt': 'document', '.ods': 'document', '.odp': 'document',
  // Audio
  '.mp3': 'audio', '.m4a': 'audio', '.wav': 'audio',
  '.ogg': 'audio', '.flac': 'audio', '.aac': 'audio',
  '.wma': 'audio', '.opus': 'audio', '.weba': 'audio',
  // Video
  '.mp4': 'video', '.mov': 'video', '.avi': 'video',
  '.mkv': 'video', '.webm': 'video', '.mpeg': 'video',
  '.mpg': 'video', '.wmv': 'video', '.flv': 'video',
  '.3gp': 'video', '.m4v': 'video',
  // Data
  '.csv': 'csv',
};

/** Returns true if the MIME type is missing, empty, or generic. */
export function isGenericMimeType(
  mimeType: string | undefined | null,
): boolean {
  if (!mimeType) return true;
  const t = mimeType.trim().toLowerCase();
  return t === '' || t === 'application/octet-stream';
}

/**
 * Classify a file by MIME type with extension fallback.
 * Returns null if the file type is not supported.
 */
export function classifyFile(
  file: { name: string; type: string },
): AttachmentType | null {
  // Try MIME type first (unless generic)
  if (!isGenericMimeType(file.type)) {
    const result = MIME_TYPE_MAP[file.type.trim().toLowerCase()];
    if (result) return result;
  }
  // Fallback to extension
  const dot = file.name.lastIndexOf('.');
  if (dot >= 0) {
    const ext = file.name.slice(dot).toLowerCase();
    const result = EXTENSION_TYPE_MAP[ext];
    if (result) return result;
  }
  return null;
}

/**
 * Determine how a file should be delivered to the backend.
 * - image (jpeg/png/gif/webp) -> base64_image  (Claude processes natively)
 * - image (other)             -> path_hint      (svg/bmp/tiff/heic etc. — not supported by Claude)
 * - pdf                       -> base64_document (Claude processes natively — ONLY format Claude accepts)
 * - document                  -> path_hint       (docx/xlsx/pptx — Claude API rejects non-PDF documents;
 *                                                  agent reads with tools or skills)
 * - audio/video               -> path_hint      (agent uses tools: whisper, ffmpeg, etc.)
 * - text/csv small            -> inline_text
 * - text/csv large            -> path_hint
 *
 * @param mimeType  Optional MIME type for fine-grained image routing.
 * @param fileName  Optional filename for extension-based fallback when MIME is generic.
 */
export function determineDeliveryStrategy(
  type: AttachmentType,
  size: number,
  mimeType?: string,
  fileName?: string,
): DeliveryStrategy {
  switch (type) {
    case 'image': {
      // Claude API image blocks only accept jpeg/png/gif/webp.
      // Route unsupported image types (svg, bmp, tiff, ico, heic, heif) via path_hint.
      if (mimeType && !isGenericMimeType(mimeType)) {
        if (!CLAUDE_NATIVE_IMAGE_MIMES.has(mimeType.trim().toLowerCase())) {
          return 'path_hint';
        }
        return 'base64_image';
      }
      // Fallback: check extension
      if (fileName) {
        const dot = fileName.lastIndexOf('.');
        if (dot >= 0) {
          const ext = fileName.slice(dot).toLowerCase();
          if (!CLAUDE_NATIVE_IMAGE_EXTENSIONS.has(ext)) {
            return 'path_hint';
          }
        }
      }
      return 'base64_image';
    }
    case 'pdf':
      return 'base64_document';
    case 'document':
      // Claude API document blocks only accept application/pdf.
      // Office docs (docx/xlsx/pptx) must go via path_hint so the agent
      // can read them with the Read tool or process with skills.
      return 'path_hint';
    case 'audio':
    case 'video':
      return 'path_hint';
    case 'text':
    case 'csv':
      return size <= SIZE_THRESHOLD ? 'inline_text' : 'path_hint';
  }
}

/**
 * Validate file size against per-type limits.
 * Returns null if OK, or an error string if too large.
 */
export function validateFileSize(
  type: AttachmentType,
  size: number,
): string | null {
  const limit = SIZE_LIMITS[type];
  if (size > limit) {
    const limitMB = (limit / (1024 * 1024)).toFixed(0);
    const actualMB = (size / (1024 * 1024)).toFixed(1);
    return `File too large. Max size for ${type}: ${limitMB}MB, actual: ${actualMB}MB`;
  }
  return null;
}

/**
 * Validate that a workspace file path is safe.
 * Returns null if safe, or an error string if rejected.
 */
export function validateWorkspacePath(path: string): string | null {
  if (!path || path.trim() === '') {
    return 'Empty file path';
  }
  // Reject absolute paths
  if (path.startsWith('/') || path.startsWith('~')) {
    return `Absolute path not allowed: ${path}`;
  }
  // Reject Windows drive letters
  if (/^[A-Za-z]:/.test(path)) {
    return `Absolute path not allowed: ${path}`;
  }
  // Reject path traversal
  if (path === '..' || path.includes('../') || path.includes('..\\')) {
    return `Path traversal not allowed: ${path}`;
  }
  return null;
}
