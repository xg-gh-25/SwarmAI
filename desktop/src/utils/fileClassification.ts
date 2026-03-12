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

export const MIME_TYPE_MAP: Record<string, AttachmentType> = {
  'image/png': 'image',
  'image/jpeg': 'image',
  'image/gif': 'image',
  'image/webp': 'image',
  'application/pdf': 'pdf',
  'text/plain': 'text',
  'text/html': 'text',
  'text/csv': 'csv',
  'application/csv': 'csv',
};

export const EXTENSION_TYPE_MAP: Record<string, AttachmentType> = {
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
  '.png': 'image', '.jpg': 'image',
  '.jpeg': 'image', '.gif': 'image',
  '.webp': 'image', '.pdf': 'pdf', '.csv': 'csv',
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
 * - image -> base64_image
 * - pdf   -> base64_document
 * - text/csv <= SIZE_THRESHOLD -> inline_text
 * - text/csv >  SIZE_THRESHOLD -> path_hint
 */
export function determineDeliveryStrategy(
  type: AttachmentType,
  size: number,
): DeliveryStrategy {
  switch (type) {
    case 'image':
      return 'base64_image';
    case 'pdf':
      return 'base64_document';
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
