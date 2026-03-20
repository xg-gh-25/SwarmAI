/**
 * Unified attachment lifecycle hook for the chat input pipeline.
 *
 * Replaces both `useFileAttachment` and `LayoutContext.attachedFiles` with a
 * single hook that manages: classify → validate → encode → store → clear.
 *
 * Key exports:
 * - ``useUnifiedAttachments``       — Main hook (tab-isolated via tabMapRef)
 * - ``UseUnifiedAttachmentsReturn`` — Return type interface
 *
 * Tab isolation: Attachments are stored in
 * ``tabMapRef.current.get(tabId).attachments`` (authoritative source).
 * React ``useState`` is a display mirror only, synced when ``tabId`` changes.
 *
 * Workspace files store only the path at attach time — content is read at
 * send time inside ``buildContentArray`` to avoid stale data.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import type { UnifiedAttachment, AttachmentType } from '../types';
import { MAX_ATTACHMENTS } from '../types';
import type { UnifiedTab } from './useUnifiedTabState';
import type { FileTreeItem } from '../components/workspace-explorer/FileTreeNode';
import {
  classifyFile,
  determineDeliveryStrategy,
  validateFileSize,
  validateWorkspacePath,
} from '../utils/fileClassification';

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

export interface UseUnifiedAttachmentsReturn {
  /** Display mirror of active tab's attachments (for rendering only). */
  attachments: UnifiedAttachment[];
  /** Add native File objects (File Picker, OS drop, clipboard). */
  addFiles: (files: File[]) => Promise<void>;
  /** Add workspace files by path (Workspace Explorer drag). */
  addWorkspaceFiles: (files: FileTreeItem[]) => Promise<void>;
  /** Remove a single attachment by ID. */
  removeAttachment: (id: string) => void;
  /** Clear all attachments for the current tab. */
  clearAll: () => void;
  /** True while any file is being read/encoded. */
  isProcessing: boolean;
  /** Last error message (null if none). */
  error: string | null;
  /** Whether more attachments can be added (count < MAX_ATTACHMENTS). */
  canAddMore: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Generate a unique attachment ID. */
function generateId(): string {
  return `attachment_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
}

/** Read a File as a base64 string (strips the data-URL prefix). */
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip "data:<mime>;base64," prefix
      const commaIdx = result.indexOf(',');
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = () => reject(new Error('Failed to read file as base64'));
    reader.readAsDataURL(file);
  });
}

/** Read a File as a UTF-8 text string. */
function readTextContent(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('Failed to read file as text'));
    reader.readAsText(file, 'utf-8');
  });
}

/**
 * Generate a preview string for a file.
 * - Images: object URL for thumbnail display.
 * - Text/CSV: first 200 characters (with ellipsis if truncated).
 * - PDF: undefined (no preview).
 */
function generatePreview(
  file: File,
  type: AttachmentType,
  textContent?: string,
): string | undefined {
  if (type === 'image') {
    return URL.createObjectURL(file);
  }
  if ((type === 'text' || type === 'csv') && textContent) {
    if (textContent.length > 200) {
      return textContent.slice(0, 200) + '…';
    }
    return textContent;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useUnifiedAttachments(
  tabId: string | null,
  tabMapRef: React.MutableRefObject<Map<string, UnifiedTab>>,
): UseUnifiedAttachmentsReturn {
  // Display mirror — synced from tabMapRef when tabId changes
  const [displayAttachments, setDisplayAttachments] = useState<UnifiedAttachment[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ref mirror of tabId — read inside async callbacks to avoid stale
  // closure captures when the user switches tabs during file encoding.
  const tabIdRef = useRef(tabId);
  tabIdRef.current = tabId;

  // Sync display mirror when tabId changes
  useEffect(() => {
    if (!tabId) {
      setDisplayAttachments([]);
      return;
    }
    const tab = tabMapRef.current.get(tabId);
    setDisplayAttachments(tab?.attachments ?? []);
  }, [tabId, tabMapRef]);

  /**
   * Write to tabMapRef (authoritative) and sync the display mirror.
   * Only updates React state if the mutation targets the active tab.
   */
  const updateAttachments = useCallback(
    (tid: string, updater: (prev: UnifiedAttachment[]) => UnifiedAttachment[]) => {
      const tab = tabMapRef.current.get(tid);
      if (!tab) return;
      tab.attachments = updater(tab.attachments);
      // Sync display mirror for the active tab
      setDisplayAttachments([...tab.attachments]);
    },
    [tabMapRef],
  );

  // ---- addFiles: native File objects (File Picker, OS drop, clipboard) ----

  const addFiles = useCallback(
    async (files: File[]): Promise<void> => {
      // Read tabId from ref (not closure) to get the current value at call time
      const tid = tabIdRef.current;
      if (!tid) return;
      setError(null);

      const tab = tabMapRef.current.get(tid);
      if (!tab) return;

      const currentCount = tab.attachments.length;
      const available = MAX_ATTACHMENTS - currentCount;
      if (available <= 0) {
        setError(`Maximum ${MAX_ATTACHMENTS} attachments allowed`);
        return;
      }

      // Trim to available slots
      const toProcess = files.slice(0, available);
      if (files.length > available) {
        setError(
          `Only ${available} more attachment(s) allowed. ${files.length - available} file(s) skipped.`,
        );
      }

      setIsProcessing(true);

      try {
        const errors: string[] = [];
        for (const file of toProcess) {
          // 1. Classify
          const fileType = classifyFile({ name: file.name, type: file.type });
          if (!fileType) {
            errors.push(`Unsupported: ${file.name}`);
            continue;
          }

          // 2. Validate size
          const sizeErr = validateFileSize(fileType, file.size);
          if (sizeErr) {
            errors.push(sizeErr);
            continue;
          }

          // 3. Determine delivery strategy (pass MIME + name for image subtype routing)
          const strategy = determineDeliveryStrategy(fileType, file.size, file.type, file.name);

          // 4. Create placeholder attachment (loading state)
          const id = generateId();
          const attachment: UnifiedAttachment = {
            id,
            name: file.name,
            type: fileType,
            deliveryStrategy: strategy,
            size: file.size,
            mediaType: file.type || 'application/octet-stream',
            isLoading: true,
          };

          // Add placeholder immediately so UI shows loading chip
          updateAttachments(tid, (prev) => [...prev, attachment]);

          // 5. Encode content based on strategy
          try {
            let base64: string | undefined;
            let textContent: string | undefined;
            let preview: string | undefined;

            if (strategy === 'base64_image' || strategy === 'base64_document') {
              base64 = await readFileAsBase64(file);
              preview = generatePreview(file, fileType);
            } else if (strategy === 'inline_text') {
              textContent = await readTextContent(file);
              preview = generatePreview(file, fileType, textContent);
            } else {
              // path_hint for large text files from File Picker —
              // content will be read at send time via buildContentArray
              const text = await readTextContent(file);
              preview = generatePreview(file, fileType, text);
              // Store textContent so buildContentArray can use it
              textContent = text;
            }

            // 6. Update attachment with encoded data — re-read tabId from ref
            //    in case user switched tabs during the async encode above
            const currentTid = tabIdRef.current;
            if (currentTid !== tid) {
              // Tab switched during encoding — attachment was added to the
              // original tab's map entry (via placeholder), which is correct.
              // Continue using tid (the originating tab) for the update.
            }
            updateAttachments(tid, (prev) =>
              prev.map((a) =>
                a.id === id
                  ? { ...a, base64, textContent, preview, isLoading: false }
                  : a,
              ),
            );
          } catch (encodeErr) {
            // Mark attachment as errored
            console.error('Failed to encode attachment:', encodeErr);
            updateAttachments(tid, (prev) =>
              prev.map((a) =>
                a.id === id
                  ? { ...a, isLoading: false, error: 'Failed to process file' }
                  : a,
              ),
            );
          }
        }
        // Surface accumulated errors
        if (errors.length > 0) {
          setError(errors.length === 1 ? errors[0] : `${errors.length} file(s) skipped: ${errors[0]}`);
        }
      } finally {
        setIsProcessing(false);
      }
    },
    [tabIdRef, tabMapRef, updateAttachments],
  );

  // ---- addWorkspaceFiles: workspace explorer drag-drop --------------------

  const addWorkspaceFiles = useCallback(
    async (files: FileTreeItem[]): Promise<void> => {
      const tid = tabIdRef.current;
      if (!tid) return;
      setError(null);

      const tab = tabMapRef.current.get(tid);
      if (!tab) return;

      const currentCount = tab.attachments.length;
      const available = MAX_ATTACHMENTS - currentCount;
      if (available <= 0) {
        setError(`Maximum ${MAX_ATTACHMENTS} attachments allowed`);
        return;
      }

      const fileItems = files.filter((f) => f.type === 'file');
      const toProcess = fileItems.slice(0, available);
      if (fileItems.length > available) {
        setError(
          `Only ${available} more attachment(s) allowed. Some files skipped.`,
        );
      }

      setIsProcessing(true);

      try {
        for (const file of toProcess) {
          // 1. Validate workspace path
          const pathErr = validateWorkspacePath(file.path);
          if (pathErr) {
            setError(pathErr);
            continue;
          }

          // 2. Classify by extension (workspace files have no MIME type)
          const fileType = classifyFile({ name: file.name, type: '' });
          if (!fileType) {
            setError(`Unsupported file type: ${file.name}`);
            continue;
          }

          // 3. Determine delivery strategy
          // Workspace files don't have base64 data at attach time.
          // - text/csv → inline_text (content read at send time via workspaceService)
          // - image/pdf → path_hint (binary files can't be read as base64 from frontend;
          //   the backend will serve them to Claude via Read tool)
          let strategy = determineDeliveryStrategy(fileType, 0, '', file.name);
          if (strategy === 'base64_image' || strategy === 'base64_document') {
            strategy = 'path_hint';
          }

          // 4. Create attachment — content is read at send time, not here
          const id = generateId();
          const attachment: UnifiedAttachment = {
            id,
            name: file.name,
            type: fileType,
            deliveryStrategy: strategy,
            size: 0, // Unknown for workspace files at attach time
            mediaType: '',
            workspacePath: file.path,
            preview: file.name, // Just show filename for workspace files
            isLoading: false,
          };

          updateAttachments(tid, (prev) => [...prev, attachment]);
        }
      } finally {
        setIsProcessing(false);
      }
    },
    [tabIdRef, tabMapRef, updateAttachments],
  );

  // ---- removeAttachment ---------------------------------------------------

  const removeAttachment = useCallback(
    (id: string): void => {
      const tid = tabIdRef.current;
      if (!tid) return;
      updateAttachments(tid, (prev) => prev.filter((a) => a.id !== id));
      setError(null);
    },
    [tabIdRef, updateAttachments],
  );

  // ---- clearAll -----------------------------------------------------------

  const clearAll = useCallback((): void => {
    const tid = tabIdRef.current;
    if (!tid) return;
    updateAttachments(tid, () => []);
    setError(null);
  }, [tabIdRef, updateAttachments]);

  // ---- Computed values ----------------------------------------------------

  const canAddMore = displayAttachments.length < MAX_ATTACHMENTS;

  // ---- Return -------------------------------------------------------------

  return {
    attachments: displayAttachments,
    addFiles,
    addWorkspaceFiles,
    removeAttachment,
    clearAll,
    isProcessing,
    error,
    canAddMore,
  };
}
